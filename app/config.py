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

    # --- Probe caching ----------------------------------------------------
    # Slow, rarely-changing discovery shell-outs are cached for a short window
    # so back-to-back page loads and the background polls don't re-run them
    # every time. Set either to 0 to always probe live.
    #
    # systemd service states (systemctl is-active/is-enabled). Kept short so a
    # service you just (re)started reflects quickly; service actions from the
    # panel bust the cache immediately regardless.
    service_cache_ttl: float = 5.0
    # SANE scanner discovery (``scanimage -L``) — the slowest probe of all, and
    # a scanner rarely comes or goes between refreshes. Set above the health
    # panel's poll interval so that poll mostly hits the cache.
    scan_devices_cache_ttl: float = 30.0

    # --- Feature toggles ---------------------------------------------------
    # Which top-level functions of the panel are switched on. Every function
    # ships enabled; the System page lets you turn ones you don't use off, which
    # hides them from the navigation and blocks their routes. The choices live in
    # a tiny JSON state file so they survive restarts and updates.
    feature_state_file: Path = Path("/etc/prntbtlr/features.json")

    # --- Webhooks ---------------------------------------------------------
    # Outbound webhooks: the panel POSTs a JSON payload to user-configured URLs
    # when events happen (scan finished, printer added, health changed, update
    # available/applied, ...). Endpoints are managed on the System page and live
    # in a small JSON state file, same pattern as the features/updater state.
    webhook_state_file: Path = Path("/etc/prntbtlr/webhooks.json")
    # Per-delivery HTTP timeout (seconds); a slow endpoint can't stall the app
    # since deliveries run off the request thread.
    webhook_timeout: int = 10
    # Seconds between background health sweeps that drive the health.degraded /
    # health.recovered events. Only runs while at least one enabled webhook is
    # subscribed to a health event, so idle installs do no extra work.
    webhook_health_interval: int = 60
    # Seconds between CUPS job polls that drive print.submitted / print.completed
    # for real jobs (AirPrint, lp, the panel's test page). Same gating: only
    # polls while an enabled webhook subscribes to a print event.
    webhook_jobs_interval: int = 15

    # --- Scanner initialization -------------------------------------------
    # Detected scanner capabilities (available modes/sources/resolutions) from
    # the "Initialize scanner" step on the Add-printer page, cached here so the
    # Scans page can offer exactly what the hardware supports.
    scan_caps_file: Path = Path("/etc/prntbtlr/scan_caps.json")

    # --- Health checks & self-repair --------------------------------------
    # The "control instances": the panel continuously verifies that the box is
    # actually working (network up, services running, printer connected & set
    # up, storage free) and can repair the common breakages on its own.
    #
    # Warn below this much free space in the scan folder (MB).
    health_min_free_mb: int = 200
    # Run the self-repair automatically in the background (restart dead
    # services, wake stopped printers, ...). Off by default — the "Run
    # self-repair" button on the System page always works regardless.
    self_repair_enabled: bool = False
    # Seconds between background self-repair sweeps (only when enabled above).
    self_repair_interval: int = 300

    # --- Updates ------------------------------------------------------------
    # The panel updates itself from this repo's GitHub Releases. Two channels:
    # "stable" (default) sees full releases only, "beta" also sees
    # pre-releases. Channel + auto-install vs. notify-only are toggled on the
    # System page and persisted in ``update_state_file``.
    update_repo: str = "w0rkingchr1s/prntbtlr"
    update_api_base: str = "https://api.github.com"
    update_state_file: Path = Path("/etc/prntbtlr/updater.json")
    # Self-update script, installed next to the app by scripts/install.sh.
    update_script: Path = Path("/opt/prntbtlr/update.sh")
    # Seconds between automatic update checks (0 disables the background
    # checker; "Check now" on the System page always works).
    update_check_interval: int = 6 * 3600


settings = Settings()
