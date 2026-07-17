"""CUPS management via the standard command-line tools.

We deliberately shell out to ``lpstat``/``lpadmin``/``lpinfo`` rather than link
against libcups: the CLI is always present on a CUPS box, needs no native build
on the Pi, and mirrors the manual workflow from the setup plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import settings
from . import shell

# ``printer NAME is idle.  enabled since ...`` / ``... is processing ...`` etc.
_STATE_RE = re.compile(r"^printer\s+(?P<name>\S+)\s+is\s+(?P<state>\w+)\.?\s*(?P<rest>.*)$")
# ``device for NAME: usb://...``
_DEVICE_RE = re.compile(r"^device for (?P<name>\S+):\s*(?P<uri>.+)$")
# A queued job line from ``lpstat -o``: ``MX870-7  pi  4096  Wed ...``
_JOB_RE = re.compile(r"^(?P<id>\S+)\s+(?P<user>\S+)\s+(?P<size>\d+)\s+(?P<when>.+)$")

# CUPS forbids spaces, '/', '#' and control chars in queue names. We go a little
# stricter: letters, digits, dot, underscore, hyphen — and never a leading hyphen
# (which lpadmin would otherwise mistake for a command-line flag).
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def is_valid_printer_name(name: str) -> bool:
    """True if *name* is a safe, CUPS-acceptable queue name."""
    return bool(name) and len(name) <= 127 and _NAME_RE.match(name) is not None


@dataclass
class Printer:
    name: str
    state: str  # idle | processing | disabled | stopped
    state_detail: str = ""
    uri: str = ""
    is_default: bool = False
    enabled: bool = True
    error_policy: str = ""

    @property
    def state_label(self) -> str:
        if not self.enabled:
            return "paused"
        return self.state

    @property
    def is_usb(self) -> bool:
        return self.uri.lower().startswith("usb://")

    @property
    def device_params(self) -> dict[str, str]:
        """Query parameters of the device URI (``serial``, ``interface``, ...).

        Keys are lower-cased; an empty dict when the URI carries no query. CUPS
        writes these as ``usb://Canon/MX870%20series?serial=10C5A0&interface=1``.
        """
        if "?" not in self.uri:
            return {}
        params: dict[str, str] = {}
        for part in self.uri.split("?", 1)[1].split("&"):
            if "=" in part:
                key, value = part.split("=", 1)
                params[key.strip().lower()] = value.strip()
        return params

    @property
    def serial(self) -> str:
        """The device serial from the URI — the stable identity of the physical
        printer, shared by every queue that points at the same hardware."""
        return self.device_params.get("serial", "")

    @property
    def usb_interface(self) -> str:
        """The USB interface number, if present. Multifunction devices expose
        several (e.g. print vs. fax), each as its own queue."""
        return self.device_params.get("interface", "")

    @property
    def is_fax(self) -> bool:
        """True when this queue is the device's fax endpoint rather than its
        printer — reported by the model name in the URI path (``...FAX``). Such
        a queue shows up alongside the real one but can't print documents.
        """
        return "fax" in self.uri.split("?", 1)[0].lower()


@dataclass
class Job:
    id: str
    user: str
    size: int
    when: str
    printer: str = ""


@dataclass
class Device:
    uri: str
    description: str = ""

    @property
    def is_usb(self) -> bool:
        return self.uri.lower().startswith("usb://")


@dataclass
class Driver:
    ppd: str
    description: str = ""


@dataclass
class CupsStatus:
    available: bool
    printers: list[Printer] = field(default_factory=list)
    jobs: list[Job] = field(default_factory=list)
    default_printer: str = ""
    message: str = ""


# --------------------------------------------------------------------------- #
# Read operations
# --------------------------------------------------------------------------- #
def available() -> bool:
    return shell.which(settings.cups_lpstat) is not None


def scheduler_running() -> bool:
    """True when the CUPS scheduler answers (``lpstat -r``).

    ``lpstat`` being installed doesn't mean ``cupsd`` is up — a stopped daemon
    still prints "scheduler is not running". Used by the health checks to tell a
    dead print system apart from a merely idle one.
    """
    res = shell.run([settings.cups_lpstat, "-r"])
    return res.ok and "not running" not in res.stdout.lower()


def sharing_enabled() -> bool | None:
    """Whether AirPrint/IPP sharing (``--share-printers``) is on.

    Returns ``None`` when it can't be determined (cupsctl missing/erroring) so
    callers can skip the check instead of reporting a false problem.
    """
    res = shell.run([settings.cups_cupsctl])
    if not res.ok:
        return None
    m = re.search(r"_share_printers=(\d)", res.stdout)
    return m.group(1) == "1" if m else None


def _default_printer() -> str:
    res = shell.run([settings.cups_lpstat, "-d"])
    if not res.ok:
        return ""
    # ``system default destination: MX870`` or ``no system default destination``
    m = re.search(r"destination:\s*(\S+)", res.stdout)
    return m.group(1) if m else ""


def _device_uris() -> dict[str, str]:
    res = shell.run([settings.cups_lpstat, "-v"])
    out: dict[str, str] = {}
    if not res.ok:
        return out
    for line in res.stdout.splitlines():
        m = _DEVICE_RE.match(line.strip())
        if m:
            out[m.group("name")] = m.group("uri").strip()
    return out


def _error_policy(name: str) -> str:
    res = shell.run([settings.cups_lpoptions, "-p", name])
    if not res.ok:
        return ""
    m = re.search(r"printer-error-policy=(\S+)", res.stdout)
    return m.group(1) if m else ""


def list_printers() -> list[Printer]:
    res = shell.run([settings.cups_lpstat, "-p"])
    printers: list[Printer] = []
    if not res.ok:
        return printers

    default = _default_printer()
    uris = _device_uris()

    for line in res.stdout.splitlines():
        m = _STATE_RE.match(line.strip())
        if not m:
            continue
        name = m.group("name")
        state = m.group("state").lower()
        rest = m.group("rest").strip()
        enabled = "disabled" not in line.lower() and state != "disabled"
        printers.append(
            Printer(
                name=name,
                state=state,
                state_detail=rest,
                uri=uris.get(name, ""),
                is_default=(name == default),
                enabled=enabled,
                error_policy=_error_policy(name),
            )
        )
    return printers


def duplicate_device_serials(printers: list[Printer]) -> set[str]:
    """Serials shared by more than one queue.

    A single physical printer can surface as several CUPS queues — most often a
    multifunction device whose print and fax USB interfaces each get their own
    auto-created queue, and manual queues that race with CUPS' USB hotplug
    discovery. They differ by URI (path and ``interface=``) but carry the *same*
    ``serial=``, which is the reliable "same hardware" key. Returned so the UI
    can flag such queues instead of leaving the user guessing why there are two.
    """
    counts: dict[str, int] = {}
    for p in printers:
        if p.serial:
            counts[p.serial] = counts.get(p.serial, 0) + 1
    return {serial for serial, n in counts.items() if n > 1}


def list_jobs() -> list[Job]:
    res = shell.run([settings.cups_lpstat, "-o"])
    jobs: list[Job] = []
    if not res.ok:
        return jobs
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _JOB_RE.match(line)
        if not m:
            continue
        job_id = m.group("id")
        printer = job_id.rsplit("-", 1)[0] if "-" in job_id else ""
        jobs.append(
            Job(
                id=job_id,
                user=m.group("user"),
                size=int(m.group("size")),
                when=m.group("when").strip(),
                printer=printer,
            )
        )
    return jobs


def status() -> CupsStatus:
    if not available():
        return CupsStatus(
            available=False,
            message="CUPS (lpstat) is not installed on this host.",
        )
    return CupsStatus(
        available=True,
        printers=list_printers(),
        jobs=list_jobs(),
        default_printer=_default_printer(),
    )


def list_devices() -> list[Device]:
    """Discoverable backends from ``lpinfo -v`` (USB connections, network, ...)."""
    res = shell.run([settings.cups_lpinfo, "-v"], timeout=settings.command_timeout)
    devices: list[Device] = []
    if not res.ok:
        return devices
    for line in res.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        scheme, uri = parts
        # ``direct``/``network``/``serial`` etc. precede the URI; skip bare schemes.
        if scheme in {"file", "direct", "network", "serial", "usb"} and "://" in uri:
            devices.append(Device(uri=uri))
    return devices


def list_drivers(query: str = "") -> list[Driver]:
    """Drivers/PPDs from ``lpinfo -m``, optionally filtered by *query*."""
    res = shell.run([settings.cups_lpinfo, "-m"], timeout=settings.command_timeout)
    drivers: list[Driver] = []
    if not res.ok:
        return drivers
    q = query.lower().strip()
    for line in res.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        ppd, desc = parts
        if q and q not in line.lower():
            continue
        drivers.append(Driver(ppd=ppd, description=desc))
    return drivers


# --------------------------------------------------------------------------- #
# Write operations
# --------------------------------------------------------------------------- #
def add_printer(
    name: str,
    uri: str,
    ppd: str,
    *,
    shared: bool = True,
    retry: bool = True,
) -> shell.Result:
    """Create (or reconfigure) a queue, mirroring the plan's Phase 2."""
    cmd = [
        settings.cups_lpadmin,
        "-p",
        name,
        "-v",
        uri,
        "-m",
        ppd,
        "-E",
        "-o",
        f"printer-is-shared={'true' if shared else 'false'}",
    ]
    res = shell.run(cmd)
    if res.ok and retry:
        # Crucial: don't stop the whole queue on a single error — retry instead.
        shell.run([settings.cups_lpadmin, "-p", name, "-o", "printer-error-policy=retry-job"])
    return res


def delete_printer(name: str) -> shell.Result:
    return shell.run([settings.cups_lpadmin, "-x", name])


def set_enabled(name: str, enabled: bool) -> shell.Result:
    binary = settings.cups_enable if enabled else settings.cups_disable
    return shell.run([binary, name])


def set_error_policy(name: str, policy: str) -> shell.Result:
    if policy not in {"retry-job", "retry-current-job", "abort-job", "stop-printer"}:
        return shell.Result(False, 1, "", f"invalid error policy: {policy}")
    return shell.run([settings.cups_lpadmin, "-p", name, "-o", f"printer-error-policy={policy}"])


def cancel_job(job_id: str) -> shell.Result:
    return shell.run([settings.cups_cancel, job_id])


def cancel_all(printer: str) -> shell.Result:
    return shell.run([settings.cups_cancel, "-a", printer])


def print_test_page(name: str) -> shell.Result:
    """Send a small text test job to *name* (uses lp's stdin)."""
    return shell.run(
        [settings.cups_lp, "-d", name, "-"],
        input_text="PrntBtlr test page — if you can read this, printing works.\n",
    )


def set_sharing(enabled: bool) -> shell.Result:
    """Toggle the global ``--share-printers`` flag (AirPrint advertising)."""
    flag = "--share-printers" if enabled else "--no-share-printers"
    return shell.run([settings.cups_cupsctl, flag])
