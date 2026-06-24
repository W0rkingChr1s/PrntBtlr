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
_STATE_RE = re.compile(
    r"^printer\s+(?P<name>\S+)\s+is\s+(?P<state>\w+)\.?\s*(?P<rest>.*)$"
)
# ``device for NAME: usb://...``
_DEVICE_RE = re.compile(r"^device for (?P<name>\S+):\s*(?P<uri>.+)$")
# A queued job line from ``lpstat -o``: ``MX870-7  pi  4096  Wed ...``
_JOB_RE = re.compile(
    r"^(?P<id>\S+)\s+(?P<user>\S+)\s+(?P<size>\d+)\s+(?P<when>.+)$"
)


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
        shell.run(
            [settings.cups_lpadmin, "-p", name, "-o", "printer-error-policy=retry-job"]
        )
    return res


def delete_printer(name: str) -> shell.Result:
    return shell.run([settings.cups_lpadmin, "-x", name])


def set_enabled(name: str, enabled: bool) -> shell.Result:
    binary = settings.cups_enable if enabled else settings.cups_disable
    return shell.run([binary, name])


def set_error_policy(name: str, policy: str) -> shell.Result:
    if policy not in {"retry-job", "retry-current-job", "abort-job", "stop-printer"}:
        return shell.Result(False, 1, "", f"invalid error policy: {policy}")
    return shell.run(
        [settings.cups_lpadmin, "-p", name, "-o", f"printer-error-policy={policy}"]
    )


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
