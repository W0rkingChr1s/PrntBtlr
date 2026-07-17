"""Host & service introspection for the dashboard."""

from __future__ import annotations

import platform
import shutil
import socket
from dataclasses import dataclass

from ..config import settings
from . import shell


@dataclass
class ServiceState:
    name: str
    active: bool
    enabled: bool
    status: str  # active | inactive | failed | not-installed | unknown


@dataclass
class HostInfo:
    hostname: str
    ip: str
    os: str
    kernel: str
    scan_dir: str
    scan_dir_free: str
    scan_dir_total: str


def _systemctl(*args: str) -> shell.Result:
    return shell.run(["systemctl", *args])


def service_state(name: str) -> ServiceState:
    if shell.which("systemctl") is None:
        return ServiceState(name, False, False, "unknown")

    active = _systemctl("is-active", name)
    enabled = _systemctl("is-enabled", name)

    status = active.stdout.strip() or "unknown"
    # ``is-active`` returns "inactive"/"failed"/"unknown"; map missing units.
    if "could not be found" in (active.stderr + enabled.stderr).lower():
        status = "not-installed"

    return ServiceState(
        name=name,
        active=active.stdout.strip() == "active",
        enabled=enabled.stdout.strip() == "enabled",
        status=status,
    )


def services() -> list[ServiceState]:
    return [service_state(name) for name in settings.services]


def restart_service(name: str) -> shell.Result:
    if name not in settings.services:
        return shell.Result(False, 1, "", f"unknown service: {name}")
    return _systemctl("restart", name)


def enable_service(name: str) -> shell.Result:
    """Enable *name* on boot (used by self-repair to make a fix stick)."""
    if name not in settings.services:
        return shell.Result(False, 1, "", f"unknown service: {name}")
    return _systemctl("enable", name)


def _primary_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _disk(path) -> tuple[str, str]:
    try:
        usage = shutil.disk_usage(path)
        return _human(usage.free), _human(usage.total)
    except OSError:
        return "?", "?"


def _human(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def host_info() -> HostInfo:
    free, total = _disk(settings.scan_dir if settings.scan_dir.exists() else "/")
    return HostInfo(
        hostname=socket.gethostname(),
        ip=_primary_ip(),
        os=_os_pretty(),
        kernel=platform.release(),
        scan_dir=str(settings.scan_dir),
        scan_dir_free=free,
        scan_dir_total=total,
    )


def _os_pretty() -> str:
    try:
        with open("/etc/os-release") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()
