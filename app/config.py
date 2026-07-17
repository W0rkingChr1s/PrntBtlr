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

    # --- Authentication (opt-in) -----------------------------------------
    # Off by default so existing trusted-LAN installs are unaffected. Enable it
    # before exposing the panel beyond your LAN. When on, a password is required.
    auth_enabled: bool = False
    auth_username: str = "admin"
    # Either a plaintext password (simplest) or a PBKDF2 hash produced by
    # ``python -m app.auth hash`` (preferred — keeps the secret out of the env).
    auth_password: str = ""
    auth_password_hash: str = ""
    # Secret used to sign the session cookie. Auto-seeded by the installer;
    # if left empty while auth is on, a random per-process key is used (sessions
    # then reset on restart).
    session_secret: str = ""
    # Session lifetime in seconds (default 7 days).
    session_max_age: int = 7 * 24 * 3600

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

    # Default paper size for browser scans: A4, Letter, Legal, or Max (full
    # scanner bed). The same PRNTBTLR_SCAN_PAPER environment variable is read by
    # scan2pdf.sh for button scans.
    scan_paper: str = "A4"

    # OCR (searchable PDFs) via ocrmypdf. Language(s) for tesseract, e.g.
    # "eng", "deu", or "deu+eng". Only used when OCR is requested and installed.
    ocr_lang: str = "eng"

    # systemd units surfaced on the dashboard. On a Canon PIXMA the button is
    # handled by prntbtlr-scan-listen and scanbd is intentionally disabled; on
    # other scanners it's the reverse — so both are shown (whichever is idle
    # simply reads "inactive"/"not installed").
    services: tuple[str, ...] = (
        "cups",
        "scanbd",
        "prntbtlr-scan-listen",
        "smbd",
        "avahi-daemon",
    )

    # Timeout (seconds) for short discovery shell-outs.
    command_timeout: int = 30
    # Scanning can take a while (warm-up + ADF); give it room.
    scan_timeout: int = 300


settings = Settings()
