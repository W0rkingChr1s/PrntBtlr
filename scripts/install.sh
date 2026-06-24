#!/usr/bin/env bash
#
# PrntBtlr installer — turns a fresh Raspberry Pi OS / Debian box into a managed
# print + button-scan station with the PrntBtlr control panel on port 80.
#
# Idempotent: safe to re-run. Every config it edits is backed up as
# <file>.bak.<timestamp> first. Mirrors the manual setup plan (CUPS + Gutenprint
# + AirPrint, scanbd button scanning, Samba share) and then deploys the web app.
#
# Usage:
#   sudo ./scripts/install.sh                 # full install
#   sudo PURGE_CANON=1 ./scripts/install.sh   # also remove Canon proprietary drivers
#   sudo SKIP_APT=1 ./scripts/install.sh      # skip apt (re-deploy app only)
#
set -euo pipefail

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
APP_DIR=/opt/prntbtlr
ENV_DIR=/etc/prntbtlr
SCAN_DIR=${SCAN_DIR:-/srv/scans}
SERVICE_USER=${SERVICE_USER:-}     # samba "force user"; auto-detected below
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS() { date +%Y%m%d_%H%M%S; }

c_blue=$'\033[1;34m'; c_green=$'\033[1;32m'; c_yellow=$'\033[1;33m'; c_red=$'\033[1;31m'; c_off=$'\033[0m'
step() { echo "${c_blue}==>${c_off} $*"; }
ok()   { echo "${c_green}  ✓${c_off} $*"; }
warn() { echo "${c_yellow}  !${c_off} $*"; }
die()  { echo "${c_red}  ✗ $*${c_off}" >&2; exit 1; }

backup() { [ -f "$1" ] && cp -a "$1" "$1.bak.$(TS)" && ok "backed up $1"; }

[ "$(id -u)" -eq 0 ] || die "Please run as root (sudo)."

# Pick a sensible non-root owner for scans / samba (the user who invoked sudo).
if [ -z "$SERVICE_USER" ]; then
  SERVICE_USER="${SUDO_USER:-pi}"
fi
id "$SERVICE_USER" >/dev/null 2>&1 || SERVICE_USER=root
ok "Service/share user: $SERVICE_USER"

# --------------------------------------------------------------------------- #
# 1. Packages
# --------------------------------------------------------------------------- #
if [ "${SKIP_APT:-0}" != "1" ]; then
  step "Installing packages (CUPS, Gutenprint, SANE, scanbd, Samba, Python)…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y \
    cups printer-driver-gutenprint avahi-daemon \
    sane-utils scanbd img2pdf samba \
    python3 python3-venv python3-pip
  ok "Packages installed"

  if [ "${PURGE_CANON:-0}" = "1" ]; then
    step "Removing Canon proprietary drivers (common source of stuck jobs)…"
    apt-get purge -y 'cnijfilter*' 'scangearmp*' 2>/dev/null || true
    apt-get autoremove -y || true
    ok "Canon proprietary drivers removed"
  fi
else
  warn "SKIP_APT=1 — skipping package installation"
fi

# --------------------------------------------------------------------------- #
# 2. Scan folder
# --------------------------------------------------------------------------- #
step "Preparing scan folder $SCAN_DIR…"
mkdir -p "$SCAN_DIR"
chown "$SERVICE_USER":"$SERVICE_USER" "$SCAN_DIR" 2>/dev/null || true
chmod 775 "$SCAN_DIR"
ok "Scan folder ready"

# --------------------------------------------------------------------------- #
# 3. USB: disable auto-suspend for the printer (keeps it from "sleeping")
# --------------------------------------------------------------------------- #
step "Disabling USB auto-suspend for Canon devices (04a9)…"
UDEV_RULE=/etc/udev/rules.d/50-canon-noautosuspend.rules
echo 'ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="04a9", ATTR{power/control}="on"' > "$UDEV_RULE"
udevadm control --reload-rules || true
ok "udev rule written ($UDEV_RULE)"
warn "Non-Canon printer? Edit idVendor in $UDEV_RULE (find it with: lsusb)."

# --------------------------------------------------------------------------- #
# 4. scanbd: pixma backend + scan script
# --------------------------------------------------------------------------- #
if command -v scanbd >/dev/null 2>&1; then
  step "Configuring scanbd (button scanning)…"
  SCANBD_DLL="$(find /etc/scanbd -name dll.conf 2>/dev/null | head -1 || true)"
  if [ -n "$SCANBD_DLL" ]; then
    grep -q '^pixma' "$SCANBD_DLL" || echo 'pixma' >> "$SCANBD_DLL"
    ok "pixma backend enabled in $SCANBD_DLL"
  else
    warn "Could not locate scanbd dll.conf — enable the 'pixma' backend manually."
  fi

  install -d /etc/scanbd/scripts
  install -m 0755 "$REPO_DIR/scripts/scan2pdf.sh" /etc/scanbd/scripts/scan2pdf.sh
  ok "Installed /etc/scanbd/scripts/scan2pdf.sh"
  warn "Button name is hardware-specific — finish setup per config/scanbd-action.conf"
  warn "  then add the action block to /etc/scanbd/scanbd.conf and: systemctl enable --now scanbd"
else
  warn "scanbd not installed — skipping button-scan setup"
fi

# --------------------------------------------------------------------------- #
# 5. Samba share for /srv/scans
# --------------------------------------------------------------------------- #
if command -v smbd >/dev/null 2>&1; then
  step "Configuring Samba share [scans]…"
  SMB_CONF=/etc/samba/smb.conf
  if ! grep -q '^\[scans\]' "$SMB_CONF" 2>/dev/null; then
    backup "$SMB_CONF"
    cat >> "$SMB_CONF" <<EOF

[scans]
   path = $SCAN_DIR
   read only = no
   guest ok = yes
   force user = $SERVICE_USER
EOF
    ok "Added [scans] share"
  else
    ok "[scans] share already present — left unchanged"
  fi
  testparm -s >/dev/null 2>&1 && systemctl restart smbd || warn "testparm reported issues; check smb.conf"
else
  warn "Samba not installed — skipping share setup"
fi

# --------------------------------------------------------------------------- #
# 6. CUPS sharing + AirPrint
# --------------------------------------------------------------------------- #
step "Enabling printer sharing + AirPrint (Bonjour)…"
cupsctl --share-printers || warn "cupsctl failed (is CUPS running?)"
systemctl enable --now avahi-daemon 2>/dev/null || true
systemctl restart cups 2>/dev/null || true
ok "Sharing + Avahi configured"

# --------------------------------------------------------------------------- #
# 7. Deploy the PrntBtlr web app
# --------------------------------------------------------------------------- #
step "Deploying control panel to $APP_DIR…"
mkdir -p "$APP_DIR"
# Copy the application (app/, requirements, pyproject) without dev cruft.
cp -a "$REPO_DIR/app" "$APP_DIR/"
cp -a "$REPO_DIR/requirements.txt" "$REPO_DIR/pyproject.toml" "$APP_DIR/" 2>/dev/null || true

if [ ! -d "$APP_DIR/.venv" ]; then
  python3 -m venv "$APP_DIR/.venv"
  ok "Created virtualenv"
fi
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
ok "Python dependencies installed"

# Environment file
mkdir -p "$ENV_DIR"
if [ ! -f "$ENV_DIR/prntbtlr.env" ]; then
  cat > "$ENV_DIR/prntbtlr.env" <<EOF
# PrntBtlr environment overrides (KEY=VALUE). Restart the service after editing:
#   sudo systemctl restart prntbtlr
PRNTBTLR_PORT=80
PRNTBTLR_SCAN_DIR=$SCAN_DIR
EOF
  ok "Wrote $ENV_DIR/prntbtlr.env"
fi

# --------------------------------------------------------------------------- #
# 8. systemd service
# --------------------------------------------------------------------------- #
step "Installing systemd service…"
install -m 0644 "$REPO_DIR/deploy/prntbtlr.service" /etc/systemd/system/prntbtlr.service
systemctl daemon-reload
systemctl enable --now prntbtlr
sleep 1
if systemctl is-active --quiet prntbtlr; then
  ok "prntbtlr service is running"
else
  warn "Service not active yet — check: journalctl -u prntbtlr -e"
fi

# --------------------------------------------------------------------------- #
# Done
# --------------------------------------------------------------------------- #
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "${c_green}PrntBtlr is installed.${c_off}"
echo "  Control panel:  http://${IP:-<pi-ip>}/"
echo "  Scans folder:   $SCAN_DIR   (share: smb://${IP:-<pi-ip>}/scans)"
echo
echo "Next steps:"
echo "  1. Open the panel and add your printer (Printers → Add printer)."
echo "  2. Finish button-scan setup: discover the button name (see"
echo "     config/scanbd-action.conf), add the action to /etc/scanbd/scanbd.conf,"
echo "     then: sudo systemctl enable --now scanbd"
echo "  3. On your Mac/iPhone, remove any old copy of the printer and re-add it."
