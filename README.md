# 🖨️ PrntBtlr

[![CI](https://github.com/w0rkingchr1s/prntbtlr/actions/workflows/ci.yml/badge.svg)](https://github.com/w0rkingchr1s/prntbtlr/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

**Your Raspberry Pi print & scan butler.** Plug an old USB printer/scanner into a
Raspberry Pi and PrntBtlr turns it into a modern, network-shared print + scan
station — with a clean web control panel on **port 80** instead of fiddling on
the command line.

It does what a hand-rolled CUPS + scanbd + Samba setup does, but wraps the daily
tasks in a friendly UI:

- **Print** — AirPrint/IPP sharing so Macs & iPhones can print to the old printer.
- **Scan** — press the scan button on the printer → PDF lands in a network folder
  (`/srv/scans`, shared over SMB). Or scan on demand from the browser.
- **Manage** — add/remove printers, watch & clear the queue, pause/resume,
  set the error policy, control services, all from one panel.

---

## Quick start

On a Raspberry Pi (Raspberry Pi OS / Debian) with the printer connected by USB:

```bash
git clone https://github.com/w0rkingchr1s/prntbtlr.git
cd prntbtlr
sudo ./scripts/install.sh
```

Then open **`http://<pi-ip>/`** and add your printer under **Printers → Add printer**.

> Already have stuck jobs from Canon's proprietary drivers? Run the installer with
> `sudo PURGE_CANON=1 ./scripts/install.sh` to remove them first.

### What the installer does

1. Installs CUPS + Gutenprint, Avahi (AirPrint), SANE + scanbd, Samba and Python.
2. Creates the shared scan folder `/srv/scans` and a Samba `[scans]` share.
3. Disables USB auto-suspend for the printer (a classic "jobs vanish" cause).
4. Installs the button-scan handler (`scan2pdf.sh`) for scanbd.
5. Enables printer sharing + Bonjour so AirPrint works.
6. Deploys the control panel to `/opt/prntbtlr` and runs it as a systemd service
   on port 80.

Every config file it touches is backed up as `<file>.bak.<timestamp>` first, and
the script is safe to re-run.

### One manual step: the scan button

The scan button's internal name is hardware-specific and can't be guessed.
Discover it once and wire it up — see [`config/scanbd-action.conf`](config/scanbd-action.conf):

```bash
sudo systemctl stop scanbd
sudo scanbd -f          # press the scan button; note the option name + 0→1 change
# Ctrl+C, then add the matching action block to /etc/scanbd/scanbd.conf
sudo systemctl enable --now scanbd
```

Scanning from the **web panel** works without this — the button just adds the
"press the hardware button" convenience.

---

## The control panel

| Page | What you can do |
|------|-----------------|
| **Dashboard** | Live status of printers, queue, services, storage and recent scans. |
| **Printers** | Add/remove printers, test page, pause/resume, clear queue, error policy, AirPrint sharing. |
| **Scans** | Scan on demand (source/mode/DPI), browse, view, download and delete saved PDFs. |
| **System** | Service status + restart, host info, links to CUPS admin. |

The UI is server-rendered (Jinja2) with a small bit of vanilla JS for live
status polling — **no build step, no CDN, works fully offline** on the Pi's LAN.

---

## Running it another way

### Docker

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

The container ships the panel + CUPS/SANE client tools and talks to a CUPS daemon
on the host (`CUPS_SERVER`). Button scanning (scanbd) stays on the host — see the
comments in [`deploy/docker-compose.yml`](deploy/docker-compose.yml).

### Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
PRNTBTLR_PORT=8080 PRNTBTLR_DEBUG=1 python -m app.main   # http://localhost:8080
pytest                                                   # run the tests
```

The panel degrades gracefully when CUPS/SANE aren't installed (handy for
developing on a laptop): pages render and show "not installed" instead of
crashing.

---

## Configuration

Settings come from environment variables (prefix `PRNTBTLR_`) or
`/etc/prntbtlr/prntbtlr.env`. Common ones:

| Variable | Default | Meaning |
|----------|---------|---------|
| `PRNTBTLR_PORT` | `80` | Web panel port. |
| `PRNTBTLR_SCAN_DIR` | `/srv/scans` | Where scans are saved & served from. |
| `PRNTBTLR_DEBUG` | `false` | Verbose logging + autoreload. |

Restart after changes: `sudo systemctl restart prntbtlr`.

---

## How it fits together

```
  Mac / iPhone ──AirPrint/IPP──┐
                               ▼
                   ┌──────────────────────┐      USB
                   │   Raspberry Pi        │ ───────────►  Old printer/scanner
                   │                       │ ◄───────────
                   │  CUPS + Gutenprint    │   scan button
                   │  scanbd → scan2pdf.sh │
                   │  Samba  → /srv/scans  │
                   │  PrntBtlr panel :80   │
                   └──────────────────────┘
                               ▲
        Browser ───────────────┘ (control panel)
        Finder ──SMB──► smb://<pi>/scans (your PDFs)
```

PrntBtlr drives the standard tools via their CLIs (`lpadmin`, `lpstat`,
`scanimage`, `systemctl`) — nothing proprietary, no daemon of its own beyond the
web app.

---

## Security

The systemd service runs as **root** because it manages CUPS, SANE and systemd,
which need privileges. That's a reasonable trade-off for a single-purpose home
print server on a trusted LAN. The panel has **no authentication** — don't expose
port 80 to the internet. To harden: put it behind a reverse proxy with auth, or
restrict access by firewall. The Samba share defaults to guest access (`guest ok`)
for convenience; switch to `guest ok = no` + `smbpasswd` for a locked-down share.

---

## Uninstall

```bash
sudo ./scripts/uninstall.sh                 # remove the panel + service
sudo PURGE_CONFIG=1 ./scripts/uninstall.sh  # also remove udev rule + scan script
```

Your scans in `/srv/scans` are left untouched.

---

## Contributing

Contributions are welcome! See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev
setup and workflow, and please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Found a security issue? See [`SECURITY.md`](SECURITY.md) — don't open a public
issue. Changes are tracked in [`CHANGELOG.md`](CHANGELOG.md).

## License

MIT © Christoph Zeitler — see [`LICENSE`](LICENSE).
