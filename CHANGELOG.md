# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
