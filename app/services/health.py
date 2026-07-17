"""Health checks — the "control instances" (Kontrollinstanzen).

Each check answers one question about whether the print/scan station is
actually working end to end:

* is the box on the network?
* are the required services running (and set to start on boot)?
* is CUPS alive?
* is a printer connected *and* set up, and is its queue ready?
* does SANE see a scanner?
* is AirPrint sharing on?
* is there room to save scans?

Every check is independent and degrades gracefully: a tool that isn't
installed yields a ``skip`` result rather than a crash, so the exact same
checks run on the Raspberry Pi and on a laptop used for development.

Checks only describe state. Fixing it lives in :mod:`app.services.repair`,
which keys off the ``fix_hint`` each check exposes.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from ..config import settings
from . import cups, scan, system

# Status vocabulary shared with the templates and the /healthz payload.
OK = "ok"  # working
WARN = "warn"  # degraded, but not down
FAIL = "fail"  # broken
SKIP = "skip"  # not applicable here (tool/hardware absent)

# The scan button is served by exactly one of these — ``scanbd`` on most
# scanners, the ``prntbtlr-scan-listen`` USB listener on Canon PIXMA hardware
# (they can't share the USB scanner). So we check that *one* handler is up
# rather than flagging the intentionally-idle one as broken.
SCAN_BUTTON_SERVICES: tuple[str, ...] = ("scanbd", "prntbtlr-scan-listen")


@dataclass
class Check:
    """One control instance's verdict.

    ``key`` is a stable identifier the repair engine dispatches on
    (``network``, ``cups``, ``storage``, ``sharing``, ``scanbutton``,
    ``service:<name>`` or ``printer:<name>``). ``fix_hint`` describes what
    self-repair would attempt; an empty hint means nothing can be auto-fixed.
    """

    key: str
    title: str
    status: str
    detail: str = ""
    fix_hint: str = ""

    @property
    def repairable(self) -> bool:
        return bool(self.fix_hint) and self.status in (WARN, FAIL)


@dataclass
class HealthReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def overall(self) -> str:
        statuses = {c.status for c in self.checks}
        if FAIL in statuses:
            return FAIL
        if WARN in statuses:
            return WARN
        return OK

    @property
    def repairable(self) -> list[Check]:
        return [c for c in self.checks if c.repairable]

    def count(self, status: str) -> int:
        return sum(1 for c in self.checks if c.status == status)

    @property
    def summary(self) -> str:
        parts = []
        for status, word in ((FAIL, "failing"), (WARN, "warnings"), (OK, "ok")):
            n = self.count(status)
            if n:
                parts.append(f"{n} {word}")
        return ", ".join(parts) or "no checks"


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #
def check_network() -> Check:
    """A LAN address (not just loopback) means the station is reachable."""
    ip = system._primary_ip()
    if ip and ip != "127.0.0.1":
        return Check("network", "Network connection", OK, f"Reachable on the LAN at {ip}.")
    # Deliberately not auto-repaired: prodding the network stack on a headless
    # Pi risks locking out the very session doing the repair.
    return Check(
        "network",
        "Network connection",
        FAIL,
        "No LAN address — only loopback is up. Check the cable / Wi-Fi.",
    )


def check_cups() -> Check:
    if not cups.available():
        return Check("cups", "CUPS print system", SKIP, "CUPS (lpstat) is not installed.")
    if cups.scheduler_running():
        return Check("cups", "CUPS print system", OK, "The CUPS scheduler is running.")
    return Check(
        "cups",
        "CUPS print system",
        FAIL,
        "CUPS is installed but the scheduler isn't responding.",
        fix_hint="Restart the cups service.",
    )


def _core_service_checks() -> list[Check]:
    """One check per required service, minus the scan-button pair."""
    checks: list[Check] = []
    for name in settings.services:
        if name in SCAN_BUTTON_SERVICES:
            continue
        st = system.service_state(name)
        key = f"service:{name}"
        title = f"Service {name}"
        if st.status in ("not-installed", "unknown"):
            reason = (
                "Not installed on this host."
                if st.status == "not-installed"
                else "systemctl unavailable."
            )
            checks.append(Check(key, title, SKIP, reason))
            continue

        problems: list[str] = []
        actions: list[str] = []
        if not st.active:
            problems.append(f"not running ({st.status})")
            actions.append("restart it")
        if not st.enabled:
            problems.append("won't start on boot")
            actions.append("enable it on boot")

        if problems:
            checks.append(
                Check(
                    key,
                    title,
                    FAIL if not st.active else WARN,
                    f"{name} is " + " and ".join(problems) + ".",
                    fix_hint=f"{name.capitalize()}: " + " and ".join(actions) + ".",
                )
            )
        else:
            checks.append(Check(key, title, OK, "Running and enabled on boot."))
    return checks


def _scan_button_states() -> list[system.ServiceState]:
    return [system.service_state(n) for n in SCAN_BUTTON_SERVICES]


def scan_button_target(states: list[system.ServiceState] | None = None) -> str | None:
    """The handler self-repair should (re)start / enable, or ``None``.

    Prefers whichever handler is already active (so we only enable-on-boot),
    then the one enabled on boot (the installer's pick), then the sole installed
    one. Returns ``None`` when both are installed but neither is active or
    enabled — too ambiguous to pick safely, so it's left to the operator.
    """
    states = states or _scan_button_states()
    installed = [s for s in states if s.status not in ("not-installed", "unknown")]
    if not installed:
        return None
    for pool in ([s for s in installed if s.active], [s for s in installed if s.enabled]):
        if pool:
            return pool[0].name
    if len(installed) == 1:
        return installed[0].name
    return None


def check_scan_button() -> Check:
    """At least one scan-button handler up (browser scanning works regardless)."""
    key = "scanbutton"
    title = "Scan button"
    states = _scan_button_states()
    installed = [s for s in states if s.status not in ("not-installed", "unknown")]
    if not installed:
        return Check(key, title, SKIP, "No button handler installed — use the Scan page.")

    active = [s for s in installed if s.active]
    if active:
        a = active[0]
        if a.enabled:
            return Check(key, title, OK, f"{a.name} is handling the scan button.")
        return Check(
            key,
            title,
            WARN,
            f"{a.name} is running but won't start on boot.",
            fix_hint=f"Enable {a.name} on boot.",
        )

    target = scan_button_target(states)
    if target:
        return Check(
            key,
            title,
            FAIL,
            "No scan-button handler is running.",
            fix_hint=f"Start {target} and enable it on boot.",
        )
    return Check(
        key,
        title,
        WARN,
        "Two handlers are installed but neither is running — enable one manually.",
    )


def _usb_detected(printer: cups.Printer, usb_uris: list[str]) -> bool:
    """Is the printer's USB device currently on the bus (``lpinfo -v``)?

    Compared on the path before the query string so a differing ``?serial=``
    doesn't read as unplugged. Only meaningful when *some* USB device was
    discovered; with none we can't tell (e.g. permissions) and don't judge.
    """
    base = printer.uri.split("?", 1)[0].lower()
    return any(u.split("?", 1)[0].lower() == base for u in usb_uris)


def check_printers() -> list[Check]:
    if not cups.available():
        return [Check("printer", "Printer", SKIP, "CUPS is not installed.")]

    printers = cups.list_printers()
    if not printers:
        return [
            Check(
                "printer",
                "Printer configured",
                WARN,
                "No printer set up yet — add one under Printers → Add printer.",
            )
        ]

    usb_uris = [d.uri for d in cups.list_devices() if d.is_usb]
    checks: list[Check] = []
    for p in printers:
        key = f"printer:{p.name}"
        title = f"Printer {p.name}"
        problems: list[str] = []
        fixable = False

        if p.is_usb and usb_uris and not _usb_detected(p, usb_uris):
            # Hardware/connection issue — reported, but not something software
            # can fix, so it doesn't set fixable on its own.
            problems.append("USB device not detected (powered off or unplugged?)")

        if not p.enabled:
            problems.append("queue is paused")
            fixable = True
        elif p.state == "stopped":
            problems.append("queue stopped after an error")
            fixable = True

        if problems:
            checks.append(
                Check(
                    key,
                    title,
                    FAIL if fixable else WARN,
                    "; ".join(problems) + ".",
                    fix_hint=f"Resume {p.name} and set it to retry jobs." if fixable else "",
                )
            )
        else:
            checks.append(Check(key, title, OK, f"Ready ({p.state})."))
    return checks


def check_scanner() -> Check:
    if not scan.available():
        return Check("scanner", "Scanner", SKIP, "SANE (scanimage) is not installed.")
    devices = scan.list_devices()
    if devices:
        label = devices[0].description or devices[0].device
        n = len(devices)
        detail = f"{label}." if n == 1 else f"{n} scanners detected."
        return Check("scanner", "Scanner", OK, detail)
    return Check(
        "scanner",
        "Scanner",
        WARN,
        "scanimage found no scanner — check the USB connection and power.",
    )


def check_sharing() -> Check:
    if not cups.available():
        return Check("sharing", "AirPrint sharing", SKIP, "CUPS is not installed.")
    if not cups.list_printers():
        return Check("sharing", "AirPrint sharing", SKIP, "No printers to share yet.")
    state = cups.sharing_enabled()
    if state is None:
        return Check("sharing", "AirPrint sharing", SKIP, "Sharing state unknown.")
    if state:
        return Check("sharing", "AirPrint sharing", OK, "Printers are shared (AirPrint/IPP).")
    return Check(
        "sharing",
        "AirPrint sharing",
        WARN,
        "Printer sharing is off — Macs/iPhones won't see the printer.",
        fix_hint="Turn AirPrint/IPP sharing back on.",
    )


def check_storage() -> Check:
    directory = settings.scan_dir
    if not directory.exists():
        return Check(
            "storage",
            "Scan storage",
            WARN,
            f"Scan folder {directory} doesn't exist yet.",
            fix_hint=f"Create the scan folder {directory}.",
        )
    try:
        free = shutil.disk_usage(directory).free
    except OSError as exc:
        return Check("storage", "Scan storage", WARN, f"Could not read free space: {exc}")
    free_mb = free // (1024 * 1024)
    if free_mb < settings.health_min_free_mb:
        # Not auto-repairable: freeing space is the operator's call.
        return Check(
            "storage",
            "Scan storage",
            WARN,
            f"Low disk space: only {system._human(free)} free.",
        )
    return Check("storage", "Scan storage", OK, f"{system._human(free)} free for scans.")


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def run_checks() -> HealthReport:
    """Run every control instance and return the combined report."""
    checks: list[Check] = [check_network()]
    checks += _core_service_checks()
    checks.append(check_cups())
    checks += check_printers()
    checks.append(check_scan_button())
    checks.append(check_scanner())
    checks.append(check_sharing())
    checks.append(check_storage())
    return HealthReport(checks)
