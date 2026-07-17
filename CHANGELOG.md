# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Reliable button scanning for Canon PIXMA via the USB interrupt endpoint.**
  Several PIXMA MFPs (e.g. the MX870) don't report their scan button through
  SANE's pollable `button-1`/`button-2` options, so `scanbd` never fires. A new
  daemon (`scripts/scan-listen.py`, service `prntbtlr-scan-listen`) reads the
  press straight off the scanner's USB interrupt endpoint (decoded against the
  pixma backend) and runs the scan: **Color** button → colour PDF, **Black**
  button → grayscale PDF (`PRNTBTLR_BLACK_MODE=Lineart` for 1-bit B&W). The
  installer pulls in `python3-usb`, and when a Canon device is present it enables
  the listener and disables `scanbd` (they can't share the USB scanner).
- `scan2pdf.sh` scan mode is now configurable via `PRNTBTLR_SCAN_MODE`
  (`Color`/`Gray`/`Lineart`).
- **Paper size for scans** (`PRNTBTLR_SCAN_PAPER`, default `A4`; also a new
  "Paper size" select on the Scans page): `A4`, `Letter`, `Legal`, or `Max`
  (full scanner bed).

### Fixed
- **Scanned pages were not A4.** Neither the button handler nor the browser scan
  passed a scan window (`-x`/`-y`) to `scanimage`, so scanners scanned their
  maximum area — 216 × 356 mm on a PIXMA ADF. Scans now default to A4 (210 × 297)
  and the PDF page box is pinned to the exact standard size via
  `img2pdf --pagesize`.
- **The SMB share showed a 0-byte PDF while a scan was still being written.**
  Button scans are now assembled (and OCR'd) entirely in a private temp dir and
  published to `/srv/scans` with a copy-to-hidden-`.part`-then-rename, so the
  visible `scan_*.pdf` appears complete in one atomic step. Browser scans build
  under a hidden `.part` name and are renamed into place the same way; the scan
  library and download endpoints ignore in-progress dotfiles.
- **Scan button did nothing on Canon PIXMA (e.g. the MX870).** Pressing
  **SCAN → PC** left the device waiting ("Processing… / Verarbeitung…") while the
  Pi never picked up the scan. It turned out several PIXMAs don't expose the
  button through SANE's pollable options at all, so `scanbd` couldn't see it —
  the new USB-interrupt listener (see Added) handles it instead. The installer
  still ships a ready-to-use scanbd action config (`config/scanbd-pixma.conf`,
  wiring `button-1`/`button-2`, included from `scanbd.conf`) for PIXMAs whose
  buttons *are* pollable and as the path for non-Canon scanners.
- `scan2pdf.sh` now retries a briefly-busy scanner and only writes a PDF once a
  page actually converts, so a failed/partial scan can't leave a 0-byte PDF, and
  logs each firing (`journalctl -t prntbtlr`) to make button scans diagnosable.

### Security
- Validate printer queue names (`[A-Za-z0-9_][A-Za-z0-9_.-]*`, no leading hyphen)
  before they reach `lpadmin`, so a crafted name can't be mistaken for a flag.

### Added
- **Release automation**: a tag-driven `release.yml` workflow that builds a
  multi-arch (amd64 + arm64) container image and pushes it to
  `ghcr.io/w0rkingchr1s/prntbtlr` (`:<version>` + `:latest`), then creates a
  GitHub Release with notes extracted from this changelog. Docs in CONTRIBUTING.
- **OCR / searchable PDFs**: a "Searchable PDF (OCR)" option in the scan form
  (via ocrmypdf + tesseract), best-effort so a plain scan is still saved if OCR
  fails or isn't installed. Installer flag `ENABLE_OCR=1` (with optional
  `OCR_LANGS="eng deu"`) installs it. `PRNTBTLR_OCR_LANG` selects the language.
- **Second scan button**: `scan2pdf-ocr.sh` wrapper + a documented second
  scanbd action so the MX870's two task buttons can produce plain vs. searchable
  PDFs. `scan2pdf.sh` gained `PRNTBTLR_OCR` / `PRNTBTLR_OCR_LANG` support.

### Added
- **Opt-in panel authentication**: a session-cookie login (styled login page +
  sign-out) guarding every page except `/login`, `/healthz` and static assets.
  Off by default. Passwords are PBKDF2-hashed; `python -m app.auth hash|secret`
  generates a hash / session secret. Installer flag `ENABLE_AUTH=1` (with
  optional `AUTH_USER` / `AUTH_PASSWORD`) seeds the hash + session secret into a
  `600` env file and prints a generated password when none is supplied.
- Blank-system bootstrap: `scripts/bootstrap.sh` one-liner that installs git,
  clones the repo, and runs the installer (`curl … | sudo bash`).
- Expanded `install.sh` for a turnkey install on a fresh box:
  - pre-flight checks (root, OS, architecture, network, Python ≥ 3.9, port use);
  - installs all printer drivers + helper tools, not just Gutenprint;
  - adds the service user to `lpadmin`/`lp`/`scanner` groups;
  - USB auto-suspend disabled for all common printer brands (not just Canon);
  - firewall opening via `ufw` (80, 631, 5353/mDNS, Samba) when active;
  - enables every service on boot (incl. `nmbd`) and seeds `scanbd`;
  - error trap with line numbers, install log at `/var/log/prntbtlr-install.log`,
    and a post-start health check against `/healthz`;
  - configurable `PORT`, `NO_FIREWALL` flags; re-run doubles as the upgrade path.
- GitHub project scaffolding for public operation: CI (ruff lint + format,
  pytest matrix on Python 3.9–3.12, shellcheck, Docker build), Dependabot,
  issue/PR templates, `CONTRIBUTING`, `SECURITY`, `CODE_OF_CONDUCT`, `LICENSE`,
  `.editorconfig`, and a `Makefile`.

### Changed
- USB no-autosuspend rule renamed `50-canon-…` → `50-prntbtlr-noautosuspend.rules`
  (the installer removes the old file automatically).

## [0.1.0] - 2026-06-24

### Added
- Initial release of PrntBtlr — a web control panel for Raspberry Pi print &
  scan stations.
- FastAPI backend wrapping CUPS/SANE/systemd via safe argv subprocess calls.
- Server-rendered web UI: dashboard, printer management, scanning, and system
  pages with a dependency-free CSS theme and vanilla-JS live status polling.
- Printer management: add/remove, test page, pause/resume, clear queue, error
  policy, and AirPrint sharing toggle.
- Scanning: on-demand browser scan plus a saved-PDF library with a path-traversal
  guard; hardware scan-button flow via scanbd + `scan2pdf.sh`.
- Deployment: idempotent `install.sh`/`uninstall.sh`, a systemd unit (port 80 via
  `CAP_NET_BIND_SERVICE`), and a Dockerfile + compose file.
- Test suite covering CUPS/scan parsers, the path-traversal guard, and page
  rendering.

[Unreleased]: https://github.com/w0rkingchr1s/prntbtlr/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/w0rkingchr1s/prntbtlr/releases/tag/v0.1.0
