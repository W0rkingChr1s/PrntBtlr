# 🖨️ PrntBtlr

[![CI](https://github.com/w0rkingchr1s/prntbtlr/actions/workflows/ci.yml/badge.svg)](https://github.com/w0rkingchr1s/prntbtlr/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![GHCR](https://img.shields.io/badge/ghcr.io-prntbtlr-2496ED?logo=docker&logoColor=white)](https://github.com/w0rkingchr1s/prntbtlr/pkgs/container/prntbtlr)
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

The installer assumes a **blank system** (a fresh Raspberry Pi OS / Debian box)
and brings everything it needs. Connect the printer by USB, then either:

**One-liner** (truly blank box — installs git, clones, installs):

```bash
curl -fsSL https://raw.githubusercontent.com/w0rkingchr1s/prntbtlr/main/scripts/bootstrap.sh | sudo bash
```

**Or from a clone:**

```bash
git clone https://github.com/w0rkingchr1s/prntbtlr.git
cd prntbtlr
sudo ./scripts/install.sh
```

Then open **`http://<pi-ip>/`** and add your printer under **Printers → Add printer**.

Useful installer flags:

| Command | Effect |
|---------|--------|
| `sudo PURGE_CANON=1 ./scripts/install.sh` | Also remove Canon proprietary drivers (a classic stuck-job cause). |
| `sudo ENABLE_AUTH=1 ./scripts/install.sh` | Turn on the panel login (prints a generated password unless you pass `AUTH_PASSWORD=…`). |
| `sudo ENABLE_OCR=1 ./scripts/install.sh` | Install OCR (ocrmypdf + tesseract) for searchable PDFs; `OCR_LANGS="eng deu"` adds languages. |
| `sudo PORT=8080 ./scripts/install.sh` | Serve the panel on a different port. |
| `sudo NO_FIREWALL=1 ./scripts/install.sh` | Don't touch ufw. |
| `sudo SKIP_APT=1 ./scripts/install.sh` | Re-deploy the app only (skip package install). |

### What the installer does

1. **Pre-flight checks** — root, supported OS, architecture, network, Python ≥ 3.9,
   and whether the target port is already taken.
2. Installs **everything**: CUPS + Gutenprint + all printer drivers, Avahi
   (AirPrint), SANE + scanbd, Samba, Python, and helper tools.
3. Adds the service user to the `lpadmin`/`lp`/`scanner` groups.
4. Creates the shared scan folder `/srv/scans` and a Samba `[scans]` share.
5. Disables USB auto-suspend for **all common printer brands** (Canon, Epson, HP,
   Brother, Samsung, …) — a classic "jobs vanish" cause.
6. Installs the button-scan handler (`scan2pdf.sh`) for scanbd.
7. Enables printer sharing + Bonjour so AirPrint works.
8. Opens the firewall (ports 80, 631, 5353, Samba) **if `ufw` is active**.
9. Enables every service on boot (cups, avahi, smbd, nmbd, scanbd, prntbtlr).
10. Deploys the control panel to `/opt/prntbtlr` in a venv, runs it as a systemd
    service, and **verifies the panel actually answers** before declaring success.

Every config file it touches is backed up as `<file>.bak.<timestamp>` first, the
script is **idempotent** (re-running is also the upgrade path), and the full
output is logged to `/var/log/prntbtlr-install.log`.

### The scan button

**Canon PIXMA** scanners (like the MX870) work out of the box — no manual
setup. Several PIXMA MFPs don't report their scan button through SANE's
pollable options (so `scanbd` never fires), so PrntBtlr instead reads the press
straight off the scanner's **USB interrupt endpoint** with a tiny daemon
([`scripts/scan-listen.py`](scripts/scan-listen.py), run as
`prntbtlr-scan-listen`). When the installer sees a Canon device it enables the
listener and steps `scanbd` aside (they can't share the USB scanner).

On the device, press **SCAN → PC**, then the start button:

- **Color** → a colour PDF in `/srv/scans`
- **Black** → a grayscale PDF (set `PRNTBTLR_BLACK_MODE=Lineart` for pure 1-bit B&W)

For **non-Canon scanners** the button is handled by `scanbd`, whose button name
is hardware-specific. Discover it once and wire it up — see
[`config/scanbd-action.conf`](config/scanbd-action.conf):

```bash
sudo systemctl stop scanbd
sudo scanbd -f          # press the scan button; note the option name + 0→1 change
# Ctrl+C, then add the matching action block to /etc/scanbd/scanbd.conf
sudo systemctl enable --now scanbd
```

Scanning from the **web panel** works without any of this.

**Pressed the button and nothing happened?** Check the handler is running and
watch the log — each scan logs there:

```bash
systemctl status prntbtlr-scan-listen    # Canon PIXMA (or: scanbd, others)
journalctl -t prntbtlr -n 20             # "button scan fired …" / "scan saved …"
sudo scanimage -L                        # confirm SANE sees the scanner
```

**Searchable PDFs (OCR).** Install OCR with `sudo ENABLE_OCR=1 ./scripts/install.sh`
(add languages with `OCR_LANGS="eng deu"`), then tick **Searchable PDF (OCR)** in
the panel's scan form. To OCR button scans too, add `PRNTBTLR_OCR=1` to
`/etc/prntbtlr/prntbtlr.env` and restart the handler.

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

## Updates

PrntBtlr updates itself from **GitHub Releases** — no `git pull` needed. Two
channels:

- **Stable** (default) — full releases only (`vX.Y.Z`).
- **Beta** — additionally receives pre-releases (`vX.Y.Z-beta.N`).

Configure it under **System → Updates** with two checkboxes:

| Checkbox | Effect |
|----------|--------|
| **Beta channel** | Also offer pre-releases (unticked: stable releases only). |
| **Install updates automatically** | Apply new releases as they appear. Unticked = **notify only**: a banner appears in the panel and you install with one click. |

Both settings persist across restarts and updates (in
`/etc/prntbtlr/updater.json`). The panel checks GitHub every 6 hours
(`PRNTBTLR_UPDATE_CHECK_INTERVAL` in seconds, `0` disables the background
check; **Check for updates now** always works) and picks the newest release on
your channel — a release marked `[failed]` (in its title or notes) is skipped.

**What installing does.** For the chosen tag the panel runs its self-updater
([`scripts/update.sh`](scripts/update.sh)): it downloads that release's
tarball, stamps the version into the app, and re-runs the bundled installer
(`SKIP_APT=1` — app only, no package churn), which redeploys `/opt/prntbtlr`,
restarts the service and health-checks the panel. It runs in a transient
systemd unit so the restart at the end can't kill the update mid-way, and every
step is logged to `/var/log/prntbtlr-update.log`.

Prefer the command line? Run the same updater by hand:

```bash
sudo /opt/prntbtlr/update.sh v0.2.0            # stable release
sudo /opt/prntbtlr/update.sh v0.2.0-beta.3     # beta release
```

**Docker** installs don't self-apply — the image *is* the update path. Pull the
new image instead (`:latest`/`:stable` or `:beta`) and recreate the container.

For maintainers: releases are cut entirely from the GitHub website — **Actions
→ Cut release** picks the next version tag itself (no CLI needed). Betas ship
as GitHub pre-releases, and **4 positive betas** since the last stable release
are promoted to a stable release automatically — or earlier on demand. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the release flow.

---

## Running it another way

### Docker

Use the pre-built multi-arch image (amd64 + arm64) from GHCR:

```bash
docker pull ghcr.io/w0rkingchr1s/prntbtlr:latest
```

…or build locally with Compose:

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
| `PRNTBTLR_SCAN_PAPER` | `A4` | Scan paper size: `A4`, `Letter`, `Legal`, or `Max` (full scanner bed). Applies to button scans and is the browser-scan default. |
| `PRNTBTLR_DEBUG` | `false` | Verbose logging + autoreload. |
| `PRNTBTLR_AUTH_ENABLED` | `false` | Require login to use the panel. |
| `PRNTBTLR_AUTH_USERNAME` | `admin` | Login username. |
| `PRNTBTLR_AUTH_PASSWORD_HASH` | — | PBKDF2 hash (preferred over plaintext). |
| `PRNTBTLR_SESSION_SECRET` | — | Key that signs the session cookie. |

Restart after changes: `sudo systemctl restart prntbtlr`.

### Authentication (optional)

The panel ships with an **opt-in login** — off by default so trusted-LAN setups
just work. Easiest way to turn it on:

```bash
sudo ENABLE_AUTH=1 ./scripts/install.sh                 # generates a password
sudo ENABLE_AUTH=1 AUTH_USER=me AUTH_PASSWORD=secret ./scripts/install.sh
```

The installer hashes the password (PBKDF2), mints a session secret, and writes
both to `/etc/prntbtlr/prntbtlr.env` (mode `600`) — the plaintext password is
never stored. To generate a hash yourself:

```bash
/opt/prntbtlr/.venv/bin/python -m app.auth hash    # prompts, prints the hash
```

> The login protects the UI, not the network: over plain HTTP the session cookie
> travels in clear. For anything internet-facing, run it behind a TLS reverse
> proxy. See [`SECURITY.md`](SECURITY.md).

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
`scanimage`, `systemctl`) — nothing proprietary. Its own footprint is the web
app plus, on Canon PIXMA hardware, the small `prntbtlr-scan-listen` USB-button
listener described above (`scanbd` handles the button on everything else).

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
