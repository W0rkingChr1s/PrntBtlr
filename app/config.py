"""Central configuration for PrntBtlr.

Values can be overridden via environment variables (prefix ``PRNTBTLR_``) or a
``.env`` file in the working directory. Defaults match the layout produced by
``scripts/install.sh``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PRNTBTLR_",
        env_file=".env",
        extra="ignore",
    )

    # --- Web server -------------------------------------------------------
    host: str = "0.0.0.0"
    # Port 80 needs privileges; the systemd unit grants CAP_NET_BIND_SERVICE.
    port: int = 80
    debug: bool = False

    # --- Branding ---------------------------------------------------------
    app_name: str = "PrntBtlr"
    tagline: str = "Your Raspberry Pi print & scan butler"

    # --- Filesystem -------------------------------------------------------
    # Where finished scans (PDFs) are written and served from.
    scan_dir: Path = Path("/srv/scans")

    # --- External tooling -------------------------------------------------
    # Allow tests / non-standard installs to point at alternative binaries.
    cups_lpstat: str = "lpstat"
    cups_lpadmin: str = "lpadmin"
    cups_lpinfo: str = "lpinfo"
    cups_lpoptions: str = "lpoptions"
    cups_lp: str = "lp"
    cups_cancel: str = "cancel"
    cups_enable: str = "cupsenable"
    cups_disable: str = "cupsdisable"
    cups_cupsctl: str = "cupsctl"
    scanimage: str = "scanimage"

    # Default SANE device fallback when none is auto-detected.
    default_scan_device: str = "pixma"

    # systemd units surfaced on the dashboard.
    services: tuple[str, ...] = ("cups", "scanbd", "smbd", "avahi-daemon")

    # Timeout (seconds) for short discovery shell-outs.
    command_timeout: int = 30
    # Scanning can take a while (warm-up + ADF); give it room.
    scan_timeout: int = 300


settings = Settings()
