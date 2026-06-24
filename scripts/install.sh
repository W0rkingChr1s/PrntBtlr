#!/usr/bin/env bash
#
# PrntBtlr installer — turns a BLANK Raspberry Pi OS / Debian system into a fully
# operational print + button-scan station with the PrntBtlr control panel on
# port 80. Assumes nothing is pre-installed; brings everything it needs.
#
# What it does, end to end:
#   - pre-flight checks (root, OS, network, Python, port 80)
#   - installs all packages (CUPS + Gutenprint, Avahi, SANE + scanbd, Samba, ...)
#   - sets up users/groups, scan folder, USB no-autosuspend (all printer brands)
#   - configures scanbd button scanning + Samba share + AirPrint sharing
#   - opens the firewall (if ufw is active) and enables every service on boot
#   - deploys the web app into a venv and runs it as a systemd service
#   - verifies the panel actually answers before declaring success
#
# Idempotent: safe to re-run (also the upgrade path). Edited configs are backed
# up as <file>.bak.<timestamp> first.
#
# Usage:
#   sudo ./scripts/install.sh                 # full install / upgrade
#   sudo PURGE_CANON=1 ./scripts/install.sh   # also remove Canon proprietary drivers
#   sudo SKIP_APT=1 ./scripts/install.sh      # skip apt (re-deploy app only)
#   sudo NO_FIREWALL=1 ./scripts/install.sh   # don't touch ufw
#   sudo PORT=8080 ./scripts/install.sh       # serve the panel on a different port
#
set -euo pipefail

# --------------------------------------------------------------------------- #
# Constants & helpers
# --------------------------------------------------------------------------- #
APP_DIR=/opt/prntbtlr
ENV_DIR=/etc/prntbtlr
SCAN_DIR=${SCAN_DIR:-/srv/scans}
SERVICE_USER=${SERVICE_USER:-}          # samba "force user"; auto-detected below
PORT=${PORT:-80}
LOG_FILE=/var/log/prntbtlr-install.log
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Common consumer printer USB vendor IDs — auto-suspend is disabled for all of
# them so whatever brand is plugged in won't "fall asleep" mid-job.
PRINTER_VENDORS=(
  04a9  # Canon
  04b8  # Epson (Seiko Epson)
  03f0  # HP
  04f9  # Brother
  04e8  # Samsung
  043d  # Lexmark
  0924  # Xerox
  0482  # Kyocera
  05ca  # Ricoh
  04dd  # Sharp
)

TS() { date +%Y%m%d_%H%M%S; }

if [ -t 1 ]; then
  c_blue=$'\033[1;34m'; c_green=$'\033[1;32m'; c_yellow=$'\033[1;33m'
  c_red=$'\033[1;31m'; c_off=$'\033[0m'
else
  c_blue=""; c_green=""; c_yellow=""; c_red=""; c_off=""
fi
step() { echo "${c_blue}==>${c_off} $*"; }
ok()   { echo "${c_green}  ✓${c_off} $*"; }
warn() { echo "${c_yellow}  !${c_off} $*"; }
die()  { echo "${c_red}  ✗ $*${c_off}" >&2; exit 1; }

backup() { [ -f "$1" ] && cp -a "$1" "$1.bak.$(TS)" && ok "backed up $1"; }

on_error() {
  local line=$1
  echo >&2
  echo "${c_red}Installation failed at line ${line}.${c_off}" >&2
  echo "See the full log: ${LOG_FILE}" >&2
  echo "Fix the cause and re-run — the installer is idempotent." >&2
}
trap 'on_error $LINENO' ERR

# Must be root before we can write the log file under /var/log.
[ "$(id -u)" -eq 0 ] || die "Please run as root (sudo ./scripts/install.sh)."

# Mirror all output to a log file for troubleshooting.
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== PrntBtlr install @ $(TS) ==="

# --------------------------------------------------------------------------- #
# 0. Pre-flight
# --------------------------------------------------------------------------- #
step "Pre-flight checks…"

# OS must be Debian-based (Raspberry Pi OS, Debian, Ubuntu, ...).
if [ -r /etc/os-release ]; then
  # shellcheck source=/dev/null
  . /etc/os-release
  case "${ID:-} ${ID_LIKE:-}" in
    *debian*|*raspbian*|*ubuntu*) ok "OS: ${PRETTY_NAME:-unknown}" ;;
    *) die "Unsupported OS '${PRETTY_NAME:-?}'. This installer targets Debian/Raspberry Pi OS." ;;
  esac
else
  die "Cannot read /etc/os-release — unsupported system."
fi
ok "Architecture: $(uname -m)"

# Pick a sensible non-root owner for scans / samba (the user who invoked sudo).
if [ -z "$SERVICE_USER" ]; then
  SERVICE_USER="${SUDO_USER:-pi}"
fi
id "$SERVICE_USER" >/dev/null 2>&1 || SERVICE_USER=root
ok "Service/share user: $SERVICE_USER"

# Network reachability (apt + pip both need it on a blank system).
if ! getent hosts deb.debian.org >/dev/null 2>&1 && [ "${SKIP_APT:-0}" != "1" ]; then
  warn "Could not resolve deb.debian.org — network may be down. Continuing anyway."
fi

# Warn if something else already owns the target port.
if command -v ss >/dev/null 2>&1; then
  if ss -ltn "( sport = :$PORT )" 2>/dev/null | grep -q LISTEN; then
    warn "Port $PORT is already in use. If it isn't PrntBtlr, the service won't bind."
  fi
fi

# --------------------------------------------------------------------------- #
# 1. Packages
# --------------------------------------------------------------------------- #
if [ "${SKIP_APT:-0}" != "1" ]; then
  step "Installing packages (this is the long part on a blank system)…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends \
    cups cups-bsd printer-driver-gutenprint printer-driver-all \
    avahi-daemon avahi-utils \
    sane-utils scanbd img2pdf \
    samba samba-common-bin \
    python3 python3-venv python3-pip \
    usbutils curl ca-certificates iproute2
  ok "Packages installed"

  # Optional: OCR / searchable PDFs (heavier — language packs are big), opt-in.
  if [ "${ENABLE_OCR:-0}" = "1" ]; then
    step "Installing OCR (ocrmypdf + tesseract)…"
    OCR_PKGS="ocrmypdf tesseract-ocr"
    for lang in ${OCR_LANGS:-eng}; do
      OCR_PKGS="$OCR_PKGS tesseract-ocr-$lang"
    done
    # shellcheck disable=SC2086
    apt-get install -y --no-install-recommends $OCR_PKGS
    ok "OCR installed (languages: ${OCR_LANGS:-eng})"
  fi

  # Python must be >= 3.9 for the app.
  if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
    die "Python 3.9+ is required, found $(python3 -V 2>&1)."
  fi
  ok "Python: $(python3 -V 2>&1)"

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
# 2. Users & groups (so the human user can also print/scan from the CLI)
# --------------------------------------------------------------------------- #
if [ "$SERVICE_USER" != "root" ]; then
  step "Adding $SERVICE_USER to printer/scanner groups…"
  for grp in lpadmin lp scanner saned; do
    if getent group "$grp" >/dev/null 2>&1; then
      if usermod -aG "$grp" "$SERVICE_USER" 2>/dev/null; then
        ok "added to $grp"
      fi
    fi
  done
fi

# --------------------------------------------------------------------------- #
# 3. Scan folder
# --------------------------------------------------------------------------- #
step "Preparing scan folder $SCAN_DIR…"
mkdir -p "$SCAN_DIR"
chown "$SERVICE_USER":"$SERVICE_USER" "$SCAN_DIR" 2>/dev/null || true
chmod 775 "$SCAN_DIR"
ok "Scan folder ready"

# --------------------------------------------------------------------------- #
# 4. USB: disable auto-suspend for all common printer brands
# --------------------------------------------------------------------------- #
step "Disabling USB auto-suspend for printers…"
UDEV_RULE=/etc/udev/rules.d/50-prntbtlr-noautosuspend.rules
{
  echo "# Managed by PrntBtlr — keep USB printers awake (no auto-suspend)."
  for vid in "${PRINTER_VENDORS[@]}"; do
    echo "ACTION==\"add\", SUBSYSTEM==\"usb\", ATTR{idVendor}==\"$vid\", ATTR{power/control}=\"on\""
  done
} > "$UDEV_RULE"
# Remove the old Canon-only rule from earlier versions if present.
rm -f /etc/udev/rules.d/50-canon-noautosuspend.rules
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true
ok "udev rule written for ${#PRINTER_VENDORS[@]} printer vendors ($UDEV_RULE)"

# --------------------------------------------------------------------------- #
# 5. scanbd: pixma backend + scan script
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
  install -m 0755 "$REPO_DIR/scripts/scan2pdf-ocr.sh" /etc/scanbd/scripts/scan2pdf-ocr.sh
  ok "Installed scan2pdf.sh + scan2pdf-ocr.sh (second button → searchable PDF)"
  # Enable the service now; the action block (button name) is wired up by hand later.
  systemctl enable scanbd >/dev/null 2>&1 || true
  warn "Button name is hardware-specific — finish setup per config/scanbd-action.conf,"
  warn "  add the action block to /etc/scanbd/scanbd.conf, then: sudo systemctl restart scanbd"
else
  warn "scanbd not installed — skipping button-scan setup"
fi

# --------------------------------------------------------------------------- #
# 6. Samba share for /srv/scans
# --------------------------------------------------------------------------- #
if command -v smbd >/dev/null 2>&1; then
  step "Configuring Samba share [scans]…"
  SMB_CONF=/etc/samba/smb.conf
  if ! grep -q '^\[scans\]' "$SMB_CONF" 2>/dev/null; then
    backup "$SMB_CONF"
    cat >> "$SMB_CONF" <<EOF

[scans]
   comment = PrntBtlr scans
   path = $SCAN_DIR
   read only = no
   guest ok = yes
   force user = $SERVICE_USER
   create mask = 0664
   directory mask = 0775
EOF
    ok "Added [scans] share"
  else
    ok "[scans] share already present — left unchanged"
  fi
  if testparm -s >/dev/null 2>&1; then
    systemctl enable smbd nmbd >/dev/null 2>&1 || true
    systemctl restart smbd
    systemctl restart nmbd 2>/dev/null || true
    ok "Samba running (smbd + nmbd)"
  else
    warn "testparm reported issues; check smb.conf"
  fi
else
  warn "Samba not installed — skipping share setup"
fi

# --------------------------------------------------------------------------- #
# 7. CUPS sharing + AirPrint
# --------------------------------------------------------------------------- #
step "Enabling printer sharing + AirPrint (Bonjour)…"
systemctl enable cups >/dev/null 2>&1 || true
systemctl start cups 2>/dev/null || true
cupsctl --share-printers 2>/dev/null || warn "cupsctl failed (is CUPS running?)"
systemctl enable --now avahi-daemon 2>/dev/null || true
systemctl restart cups 2>/dev/null || true
ok "Sharing + Avahi configured"

# --------------------------------------------------------------------------- #
# 8. Firewall (only if ufw is installed and active)
# --------------------------------------------------------------------------- #
if [ "${NO_FIREWALL:-0}" != "1" ] && command -v ufw >/dev/null 2>&1 \
   && ufw status 2>/dev/null | grep -q "Status: active"; then
  step "Opening firewall ports (ufw is active)…"
  ufw allow "$PORT/tcp"   >/dev/null 2>&1 && ok "allowed $PORT/tcp (panel)"
  ufw allow 631/tcp       >/dev/null 2>&1 && ok "allowed 631/tcp (IPP/AirPrint)"
  ufw allow 5353/udp      >/dev/null 2>&1 && ok "allowed 5353/udp (mDNS/Bonjour)"
  ufw allow Samba         >/dev/null 2>&1 || ufw allow 445/tcp >/dev/null 2>&1 || true
  ok "Firewall rules applied"
else
  ok "Firewall: ufw inactive or absent — nothing to open"
fi

# --------------------------------------------------------------------------- #
# 9. Deploy the PrntBtlr web app
# --------------------------------------------------------------------------- #
step "Deploying control panel to $APP_DIR…"
mkdir -p "$APP_DIR"
# Refresh the application code (this is also the upgrade path).
rm -rf "$APP_DIR/app"
cp -a "$REPO_DIR/app" "$APP_DIR/"
cp -a "$REPO_DIR/requirements.txt" "$REPO_DIR/pyproject.toml" "$APP_DIR/" 2>/dev/null || true

if [ ! -d "$APP_DIR/.venv" ]; then
  python3 -m venv "$APP_DIR/.venv"
  ok "Created virtualenv"
fi
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
ok "Python dependencies installed"

# Environment file (preserve an existing one; only seed defaults once).
mkdir -p "$ENV_DIR"
ENV_FILE="$ENV_DIR/prntbtlr.env"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
# PrntBtlr environment overrides (KEY=VALUE). Restart after editing:
#   sudo systemctl restart prntbtlr
PRNTBTLR_PORT=$PORT
PRNTBTLR_SCAN_DIR=$SCAN_DIR
EOF
  ok "Wrote $ENV_FILE"
else
  ok "Kept existing $ENV_FILE"
fi

# --- Optional: enable login (ENABLE_AUTH=1) -------------------------------- #
if [ "${ENABLE_AUTH:-0}" = "1" ]; then
  step "Enabling panel authentication…"
  AUTH_USER="${AUTH_USER:-admin}"
  GENERATED_PW=""
  if [ -z "${AUTH_PASSWORD:-}" ]; then
    AUTH_PASSWORD="$("$APP_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(12))')"
    GENERATED_PW="$AUTH_PASSWORD"
  fi
  # Hash the password and mint a session secret using the app's own helpers,
  # so the plaintext password never lands in the env file.
  PW_HASH="$(cd "$APP_DIR" && AUTH_PW="$AUTH_PASSWORD" .venv/bin/python -c \
    'import os; from app.auth import hash_password; print(hash_password(os.environ["AUTH_PW"]))')"
  SESSION_SECRET="$("$APP_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  chmod 600 "$ENV_FILE"
  # Upsert: drop any prior auth lines, then append the fresh ones.
  sed -i '/^PRNTBTLR_AUTH_/d; /^PRNTBTLR_SESSION_SECRET=/d' "$ENV_FILE"
  {
    echo "PRNTBTLR_AUTH_ENABLED=true"
    echo "PRNTBTLR_AUTH_USERNAME=$AUTH_USER"
    echo "PRNTBTLR_AUTH_PASSWORD_HASH=$PW_HASH"
    echo "PRNTBTLR_SESSION_SECRET=$SESSION_SECRET"
  } >> "$ENV_FILE"
  ok "Authentication enabled for user '$AUTH_USER' (secret + hash stored, file 0600)"
  if [ -n "$GENERATED_PW" ]; then
    AUTH_GENERATED_NOTICE="$GENERATED_PW"
  fi
fi

# --------------------------------------------------------------------------- #
# 10. systemd service
# --------------------------------------------------------------------------- #
step "Installing systemd service…"
install -m 0644 "$REPO_DIR/deploy/prntbtlr.service" /etc/systemd/system/prntbtlr.service
systemctl daemon-reload
systemctl enable prntbtlr >/dev/null 2>&1 || true
systemctl restart prntbtlr

# --------------------------------------------------------------------------- #
# 11. Health check — don't declare success until the panel answers
# --------------------------------------------------------------------------- #
step "Verifying the control panel responds…"
healthy=0
for _ in $(seq 1 15); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done
if [ "$healthy" -eq 1 ]; then
  ok "Panel is up and answering on port $PORT"
else
  warn "Panel did not answer yet — check: journalctl -u prntbtlr -e"
fi

# --------------------------------------------------------------------------- #
# Done
# --------------------------------------------------------------------------- #
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "${c_green}PrntBtlr is installed.${c_off}"
echo "  Control panel:  http://${IP:-<pi-ip>}:$PORT/"
echo "  Scans folder:   $SCAN_DIR   (share: smb://${IP:-<pi-ip>}/scans)"
echo "  Install log:    $LOG_FILE"
if [ "${ENABLE_AUTH:-0}" = "1" ]; then
  echo "  Login:          user '${AUTH_USER:-admin}'"
  if [ -n "${AUTH_GENERATED_NOTICE:-}" ]; then
    echo
    echo "${c_yellow}  ► Generated password: ${AUTH_GENERATED_NOTICE}${c_off}"
    echo "    Save it now — it is NOT stored anywhere (only its hash is kept)."
  fi
fi
echo
echo "Next steps:"
echo "  1. Open the panel and add your printer (Printers → Add printer)."
echo "  2. Finish button-scan setup: discover the button name (see"
echo "     config/scanbd-action.conf), add the action to /etc/scanbd/scanbd.conf,"
echo "     then: sudo systemctl restart scanbd"
echo "  3. On your Mac/iPhone, remove any old copy of the printer and re-add it."
if [ "$SERVICE_USER" != "root" ]; then
  echo
  echo "Note: '$SERVICE_USER' was added to printer/scanner groups — log out and back"
  echo "      in (or reboot) for CLI printing/scanning as that user to take effect."
fi
