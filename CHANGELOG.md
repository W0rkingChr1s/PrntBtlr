# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- GitHub project scaffolding for public operation: CI (ruff lint + format,
  pytest matrix on Python 3.9–3.12, shellcheck, Docker build), Dependabot,
  issue/PR templates, `CONTRIBUTING`, `SECURITY`, `CODE_OF_CONDUCT`, `LICENSE`,
  `.editorconfig`, and a `Makefile`.

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
