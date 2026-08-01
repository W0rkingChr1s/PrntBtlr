#!/usr/bin/env bash
#
# PrntBtlr uninstaller — the mirror image of scripts/install.sh.
#
# It removes everything the installer put on the box: both systemd units, the
# deployed app, the udev rule, the scanbd scripts + button config (including the
# include line it added to scanbd.conf), the [scans] Samba share and the panel's
# firewall rule. Files it edited are backed up as <file>.bak.<timestamp> before
# the PrntBtlr block is cut out, so a hand-made change next to it survives.
#
# What it keeps unless you ask otherwise:
#   - your scans in /srv/scans          (PURGE_SCANS=1 deletes them)
#   - the config + state in /etc/prntbtlr (PURGE_CONFIG=1 removes it)
#   - CUPS / SANE / Samba themselves    (PURGE_PACKAGES=1 purges them)
#
# Usage:
#   sudo ./scripts/uninstall.sh                    # remove PrntBtlr
#   sudo ./scripts/uninstall.sh --dry-run          # show what would happen
#   sudo ./scripts/uninstall.sh --yes              # no confirmation prompt
#   sudo ./scripts/uninstall.sh --purge-config     # also drop /etc/prntbtlr
#   sudo ./scripts/uninstall.sh --purge-scans      # also delete the scans
#   sudo ./scripts/uninstall.sh --purge-packages   # also purge CUPS/SANE/Samba
#   sudo ./scripts/uninstall.sh --all              # all three of the above
#
# Every flag has an environment twin (PURGE_CONFIG=1, PURGE_SCANS=1,
# PURGE_PACKAGES=1, ASSUME_YES=1, DRY_RUN=1, NO_FIREWALL=1), so the old
# `sudo PURGE_CONFIG=1 ./scripts/uninstall.sh` still works.
#
set -euo pipefail

# --------------------------------------------------------------------------- #
# Constants & helpers
# --------------------------------------------------------------------------- #
APP_DIR=/opt/prntbtlr
SRC_DIR=/opt/prntbtlr-src              # bootstrap.sh's clone target
ENV_DIR=/etc/prntbtlr
ENV_FILE="$ENV_DIR/prntbtlr.env"
LOG_FILE=/var/log/prntbtlr-uninstall.log

UDEV_RULES=(
  /etc/udev/rules.d/50-prntbtlr-noautosuspend.rules
  /etc/udev/rules.d/50-canon-noautosuspend.rules   # legacy name (pre-0.2)
)
SCANBD_FILES=(
  /etc/scanbd/scripts/scan2pdf.sh
  /etc/scanbd/scripts/scan2pdf-ocr.sh
  /etc/scanbd/scanner.d/prntbtlr-pixma.conf
)
UNIT_FILES=(
  /etc/systemd/system/prntbtlr.service
  /etc/systemd/system/prntbtlr-scan-listen.service
)
OTHER_FILES=(
  /var/log/prntbtlr-install.log
  /var/log/prntbtlr-update.log
)
SCANBD_CONF=/etc/scanbd/scanbd.conf
SMB_CONF=/etc/samba/smb.conf

# Packages the installer brings in. Deliberately without python3, curl,
# ca-certificates, usbutils & friends — purging those off a Debian box is not
# something an app uninstaller gets to do.
PURGE_PKGS=(
  cups cups-bsd printer-driver-gutenprint printer-driver-all
  avahi-daemon avahi-utils
  sane-utils scanbd img2pdf
  samba samba-common-bin
  python3-usb
  ocrmypdf tesseract-ocr
)

# Refuse to rm -rf a system directory even if someone points SCAN_DIR at one.
PROTECTED_DIRS=(/ /bin /boot /dev /etc /home /lib /media /mnt /opt /proc /root /run /sbin /srv /sys /tmp /usr /var)

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

# Every mutating command goes through run(), which is what makes --dry-run
# honest: nothing below has to remember to check the flag.
run() {
  if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "  ${c_yellow}[dry-run]${c_off} $*"
    return 0
  fi
  "$@"
}

# Same, for commands whose own chatter we don't want. Redirecting at the call
# site would swallow the dry-run line too, which is why this exists.
run_quiet() {
  if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "  ${c_yellow}[dry-run]${c_off} $*"
    return 0
  fi
  "$@" >/dev/null 2>&1
}

backup() {
  [ -f "$1" ] || return 0
  run cp -a "$1" "$1.bak.$(TS)" && ok "backed up $1"
}

# Remove the PrntBtlr [scans] share from an smb.conf, leaving every other
# section — and a [scans] share that isn't ours — untouched. Blank lines that
# only exist because the installer padded its block go with it, so an
# install/uninstall cycle doesn't slowly grow the file.
#
# Exit status: 0 when a block was removed, 1 when there was nothing to do.
strip_samba_share() {
  local file=$1 tmp
  [ -f "$file" ] || return 1
  tmp="$(mktemp)"
  if awk '
    function flush_block(   i, last) {
      if (!ours) {
        for (i = 1; i <= np; i++) print pend[i]
        for (i = 1; i <= nb; i++) print blk[i]
      } else {
        removed = 1
        # The blank lines the block ends on separate it from whatever comes
        # next, so they belong to that section, not to ours — keep them.
        last = nb
        while (last > 0 && blk[last] ~ /^[[:space:]]*$/) last--
        for (i = last + 1; i <= nb; i++) print blk[i]
      }
      np = 0; nb = 0; inblk = 0; ours = 0
    }
    function flush_pending(   i) {
      for (i = 1; i <= np; i++) print pend[i]
      np = 0
    }
    /^[[:space:]]*\[/ {
      if (inblk) flush_block()
      if (tolower($0) ~ /^[[:space:]]*\[scans\][[:space:]]*$/) {
        inblk = 1; nb = 0; ours = 0; blk[++nb] = $0; next
      }
      flush_pending(); print; next
    }
    inblk {
      blk[++nb] = $0
      if ($0 ~ /PrntBtlr scans/) ours = 1
      next
    }
    /^[[:space:]]*$/ { pend[++np] = $0; next }
    { flush_pending(); print }
    END { if (inblk) flush_block(); else flush_pending(); exit(removed ? 0 : 1) }
  ' "$file" > "$tmp"; then
    run cp "$tmp" "$file"      # cp, not mv: keeps the original owner and mode
    rm -f "$tmp"
    return 0
  fi
  rm -f "$tmp"
  return 1
}

# Drop the `include(scanner.d/prntbtlr-pixma.conf)` line the installer appended
# to scanbd.conf, together with its comment header and the blank line in front.
# A glob include the distro ships (`include(scanner.d/*.conf)`) is left alone —
# that one isn't ours and other scanners may rely on it.
#
# Exit status: 0 when something was removed, 1 when there was nothing to do.
strip_scanbd_include() {
  local file=$1 tmp
  [ -f "$file" ] || return 1
  tmp="$(mktemp)"
  if awk '
    function flush_pending(   i) {
      for (i = 1; i <= np; i++) print pend[i]
      np = 0
    }
    /^[[:space:]]*include\([[:space:]]*scanner\.d\/prntbtlr-pixma\.conf[[:space:]]*\)/ {
      np = 0; removed = 1; next
    }
    /^[[:space:]]*#.*PrntBtlr/ { pend[++np] = $0; next }
    /^[[:space:]]*$/           { pend[++np] = $0; next }
    { flush_pending(); print }
    END { flush_pending(); exit(removed ? 0 : 1) }
  ' "$file" > "$tmp"; then
    run cp "$tmp" "$file"
    rm -f "$tmp"
    return 0
  fi
  rm -f "$tmp"
  return 1
}

# Read a KEY=VALUE out of the deployed env file, so we clean up the port and
# scan folder this box actually used — not the defaults.
env_value() {
  local key=$1
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^[[:space:]]*$key=//p" "$ENV_FILE" | tail -1 | tr -d '"'"'"'\r'
}

is_protected_dir() {
  local dir=$1 p
  case "$dir" in
    ""|*..*) return 0 ;;
    /*) ;;
    *) return 0 ;;   # relative path — refuse
  esac
  dir="${dir%/}"
  for p in "${PROTECTED_DIRS[@]}"; do
    [ "$dir" = "${p%/}" ] && return 0
  done
  return 1
}

usage() {
  cat <<'EOF'
PrntBtlr uninstaller — removes what scripts/install.sh put on this system.

Usage: sudo ./scripts/uninstall.sh [options]

  -y, --yes           Don't ask for confirmation.
  -n, --dry-run       Show what would be done, change nothing.
      --purge-config  Also remove /etc/prntbtlr (config, flags, webhooks).
      --purge-scans   Also delete the scans folder.
      --purge-packages
                      Also purge the CUPS / SANE / Samba packages.
      --all           --purge-config --purge-scans --purge-packages
      --no-firewall   Don't touch ufw.
  -h, --help          This text.

Each option has an environment twin: ASSUME_YES, DRY_RUN, PURGE_CONFIG,
PURGE_SCANS, PURGE_PACKAGES, NO_FIREWALL (all =1), plus SCAN_DIR and PORT to
override what is read from /etc/prntbtlr/prntbtlr.env.
EOF
}

# Sourced by the test suite: definitions only, no teardown of the host running
# the tests.
if [ "${PRNTBTLR_UNINSTALL_LIB:-0}" = "1" ]; then
  # shellcheck disable=SC2317  # reached when the script is sourced, not run
  return 0 2>/dev/null || exit 0
fi

# --------------------------------------------------------------------------- #
# Arguments
# --------------------------------------------------------------------------- #
PURGE_CONFIG=${PURGE_CONFIG:-0}
PURGE_SCANS=${PURGE_SCANS:-0}
PURGE_PACKAGES=${PURGE_PACKAGES:-0}
ASSUME_YES=${ASSUME_YES:-0}
DRY_RUN=${DRY_RUN:-0}
NO_FIREWALL=${NO_FIREWALL:-0}

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)        ASSUME_YES=1 ;;
    -n|--dry-run)    DRY_RUN=1 ;;
    --purge-config)  PURGE_CONFIG=1 ;;
    --purge-scans)   PURGE_SCANS=1 ;;
    --purge-packages) PURGE_PACKAGES=1 ;;
    --all)           PURGE_CONFIG=1; PURGE_SCANS=1; PURGE_PACKAGES=1 ;;
    --no-firewall)   NO_FIREWALL=1 ;;
    -h|--help)       usage; exit 0 ;;
    *)               echo "Unknown option: $1" >&2; echo >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[ "$(id -u)" -eq 0 ] || die "Please run as root (sudo ./scripts/uninstall.sh)."

# --------------------------------------------------------------------------- #
# Get out of the way before deleting the tree we might be running from
# --------------------------------------------------------------------------- #
# bash reads a script lazily, so `rm -rf /opt/prntbtlr-src` while running
# /opt/prntbtlr-src/scripts/uninstall.sh truncates the uninstaller mid-run. Copy
# ourselves somewhere neutral and continue from there.
SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || true)"
if [ -z "${PRNTBTLR_UNINSTALL_RELOCATED:-}" ] && [ -f "$SELF" ]; then
  case "$SELF" in
    "$APP_DIR"/*|"$SRC_DIR"/*)
      RELOC="$(mktemp -d)"
      cp "$SELF" "$RELOC/uninstall.sh"
      reloc_args=()
      for pair in "$ASSUME_YES:--yes" "$DRY_RUN:--dry-run" \
                  "$PURGE_CONFIG:--purge-config" "$PURGE_SCANS:--purge-scans" \
                  "$PURGE_PACKAGES:--purge-packages" "$NO_FIREWALL:--no-firewall"; do
        if [ "${pair%%:*}" = "1" ]; then
          reloc_args+=("${pair#*:}")
        fi
      done
      export PRNTBTLR_UNINSTALL_RELOCATED="$RELOC"
      exec bash "$RELOC/uninstall.sh" ${reloc_args+"${reloc_args[@]}"}
      ;;
  esac
fi
if [ -n "${PRNTBTLR_UNINSTALL_RELOCATED:-}" ]; then
  trap 'rm -rf "$PRNTBTLR_UNINSTALL_RELOCATED"' EXIT
fi

# Mirror everything into a log, same as the installer (dry runs stay read-only).
if [ "$DRY_RUN" != "1" ]; then
  exec > >(tee -a "$LOG_FILE") 2>&1
fi
echo "=== PrntBtlr uninstall @ $(TS) ==="

SCAN_DIR="${SCAN_DIR:-$(env_value PRNTBTLR_SCAN_DIR)}"
SCAN_DIR="${SCAN_DIR:-/srv/scans}"
PORT="${PORT:-$(env_value PRNTBTLR_PORT)}"
PORT="${PORT:-80}"

# --------------------------------------------------------------------------- #
# Plan & confirmation
# --------------------------------------------------------------------------- #
echo
echo "This removes PrntBtlr from $(hostname):"
echo "  - services prntbtlr + prntbtlr-scan-listen (stopped, disabled, unit files removed)"
echo "  - $APP_DIR and $SRC_DIR"
echo "  - udev rule, scanbd scripts + button config, scanbd.conf include"
echo "  - the [scans] Samba share and the panel's ufw rule (port $PORT)"
if [ "$PURGE_CONFIG" = "1" ]; then
  echo "  - $ENV_DIR (config, feature flags, webhooks, updater state)"
fi
if [ "$PURGE_SCANS" = "1" ]; then
  echo "  ${c_red}- every scan in $SCAN_DIR${c_off}"
fi
if [ "$PURGE_PACKAGES" = "1" ]; then
  echo "  ${c_red}- the CUPS / SANE / Samba packages themselves${c_off}"
fi
echo
echo "Kept:"
[ "$PURGE_SCANS"    = "1" ] || echo "  - your scans in $SCAN_DIR"
[ "$PURGE_CONFIG"   = "1" ] || echo "  - config + state in $ENV_DIR"
[ "$PURGE_PACKAGES" = "1" ] || echo "  - CUPS / SANE / Samba (and the printers you set up in CUPS)"
echo

if [ "$DRY_RUN" = "1" ]; then
  warn "Dry run — nothing will be changed."
elif [ "$ASSUME_YES" != "1" ]; then
  if [ ! -t 0 ]; then
    die "Not running interactively — re-run with --yes (or ASSUME_YES=1) to confirm."
  fi
  read -r -p "Type 'yes' to continue: " reply
  [ "$reply" = "yes" ] || die "Aborted — nothing was changed."
fi

# --------------------------------------------------------------------------- #
# 1. Services
# --------------------------------------------------------------------------- #
step "Stopping and disabling services…"
for unit in prntbtlr prntbtlr-scan-listen; do
  if systemctl cat "$unit.service" >/dev/null 2>&1; then
    run_quiet systemctl disable --now "$unit" || true
    run_quiet systemctl reset-failed "$unit" || true
    ok "$unit stopped and disabled"
  else
    ok "$unit not installed — nothing to stop"
  fi
done
for unit_file in "${UNIT_FILES[@]}"; do
  if [ -f "$unit_file" ]; then
    run rm -f "$unit_file"
    ok "removed $unit_file"
  fi
done
run_quiet systemctl daemon-reload || true

# --------------------------------------------------------------------------- #
# 2. Application files
# --------------------------------------------------------------------------- #
step "Removing application files…"
for dir in "$APP_DIR" "$SRC_DIR"; do
  if [ -d "$dir" ]; then
    run rm -rf "$dir"
    ok "removed $dir"
  fi
done

# --------------------------------------------------------------------------- #
# 3. udev rule
# --------------------------------------------------------------------------- #
step "Removing the USB no-autosuspend rule…"
removed_udev=0
for rule in "${UDEV_RULES[@]}"; do
  if [ -f "$rule" ]; then
    run rm -f "$rule"
    ok "removed $rule"
    removed_udev=1
  fi
done
if [ "$removed_udev" = "1" ]; then
  run_quiet udevadm control --reload-rules || true
  run_quiet udevadm trigger || true
  ok "udev rules reloaded"
else
  ok "No PrntBtlr udev rule present"
fi

# --------------------------------------------------------------------------- #
# 4. scanbd (button scanning)
# --------------------------------------------------------------------------- #
step "Removing the scanbd button-scan setup…"
for f in "${SCANBD_FILES[@]}"; do
  if [ -f "$f" ]; then
    run rm -f "$f"
    ok "removed $f"
  fi
done
if [ -f "$SCANBD_CONF" ] \
   && grep -q 'scanner\.d/prntbtlr-pixma\.conf' "$SCANBD_CONF" 2>/dev/null; then
  backup "$SCANBD_CONF"
  if strip_scanbd_include "$SCANBD_CONF"; then
    ok "Removed the PrntBtlr include from $SCANBD_CONF"
  else
    warn "Could not edit $SCANBD_CONF — remove the prntbtlr-pixma include by hand"
  fi
else
  ok "No PrntBtlr include in scanbd.conf"
fi
if command -v scanbd >/dev/null 2>&1; then
  if run_quiet systemctl restart scanbd; then
    ok "scanbd restarted without the PrntBtlr actions"
  else
    warn "scanbd is installed but not running — start it with: systemctl start scanbd"
  fi
  # The installer disables scanbd when it hands the button to the USB listener.
  # Whether it should come back is the user's call, not ours.
  if ! systemctl is-enabled scanbd >/dev/null 2>&1; then
    warn "scanbd is disabled (PrntBtlr may have done that for a Canon PIXMA)."
    warn "  Bring it back with: systemctl enable --now scanbd"
  fi
fi

# --------------------------------------------------------------------------- #
# 5. Samba share
# --------------------------------------------------------------------------- #
step "Removing the [scans] Samba share…"
if [ -f "$SMB_CONF" ] && grep -q 'PrntBtlr scans' "$SMB_CONF" 2>/dev/null; then
  backup "$SMB_CONF"
  if strip_samba_share "$SMB_CONF"; then
    ok "Removed the [scans] share from $SMB_CONF"
    # Only trust testparm's verdict when testparm is actually there — a leftover
    # smb.conf on a box without Samba must not look like a broken config.
    if [ "$DRY_RUN" != "1" ] && command -v testparm >/dev/null 2>&1 \
       && ! testparm -s >/dev/null 2>&1; then
      warn "testparm reports issues with $SMB_CONF — check it before restarting Samba"
    else
      run_quiet systemctl restart smbd || true
      run_quiet systemctl restart nmbd || true
      ok "Samba reloaded"
    fi
  else
    warn "Could not edit $SMB_CONF — remove the [scans] block by hand"
  fi
elif [ -f "$SMB_CONF" ] && grep -q '^\[scans\]' "$SMB_CONF" 2>/dev/null; then
  warn "A [scans] share exists but wasn't written by PrntBtlr — left untouched"
else
  ok "No PrntBtlr Samba share present"
fi

# --------------------------------------------------------------------------- #
# 6. Firewall
# --------------------------------------------------------------------------- #
if [ "$NO_FIREWALL" != "1" ] && command -v ufw >/dev/null 2>&1 \
   && ufw status 2>/dev/null | grep -q "Status: active"; then
  step "Closing the panel's firewall port…"
  if run_quiet ufw delete allow "$PORT/tcp"; then
    ok "removed the allow rule for $PORT/tcp"
  else
    warn "no ufw rule for $PORT/tcp (already gone?)"
  fi
  warn "631/tcp (IPP), 5353/udp (mDNS) and Samba were left open — CUPS and the"
  warn "  share still use them. Close them with: ufw delete allow 631/tcp, …"
else
  ok "Firewall: ufw inactive, absent or skipped — nothing to close"
fi

# --------------------------------------------------------------------------- #
# 7. Config & state (opt-in)
# --------------------------------------------------------------------------- #
if [ "$PURGE_CONFIG" = "1" ]; then
  step "Removing config and state…"
  if [ -d "$ENV_DIR" ]; then
    run rm -rf "$ENV_DIR"
    ok "removed $ENV_DIR (env file, feature flags, webhooks, updater state)"
  else
    ok "$ENV_DIR is already gone"
  fi
elif [ -d "$ENV_DIR" ]; then
  ok "Kept $ENV_DIR — re-run with --purge-config to remove it"
fi

# --------------------------------------------------------------------------- #
# 8. Scans (opt-in)
# --------------------------------------------------------------------------- #
if [ "$PURGE_SCANS" = "1" ]; then
  step "Deleting scans in $SCAN_DIR…"
  if is_protected_dir "$SCAN_DIR"; then
    warn "Refusing to delete '$SCAN_DIR' — that is a system directory. Left untouched."
  elif [ -d "$SCAN_DIR" ]; then
    run rm -rf "$SCAN_DIR"
    ok "removed $SCAN_DIR"
  else
    ok "$SCAN_DIR does not exist"
  fi
fi

# --------------------------------------------------------------------------- #
# 9. Packages (opt-in)
# --------------------------------------------------------------------------- #
if [ "$PURGE_PACKAGES" = "1" ]; then
  step "Purging print/scan packages…"
  installed=()
  for pkg in "${PURGE_PKGS[@]}"; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q '^install ok installed'; then
      installed+=("$pkg")
    fi
  done
  if [ "${#installed[@]}" -gt 0 ]; then
    export DEBIAN_FRONTEND=noninteractive
    run apt-get purge -y "${installed[@]}" || warn "apt purge reported errors"
    run_quiet apt-get autoremove -y || true
    ok "purged: ${installed[*]}"
    warn "CUPS is gone — the printer queues you configured went with it."
  else
    ok "None of the print/scan packages are installed"
  fi
fi

# --------------------------------------------------------------------------- #
# 10. Leftover logs
# --------------------------------------------------------------------------- #
step "Removing PrntBtlr log files…"
for f in "${OTHER_FILES[@]}"; do
  if [ -f "$f" ]; then
    run rm -f "$f"
    ok "removed $f"
  fi
done

# --------------------------------------------------------------------------- #
# Done
# --------------------------------------------------------------------------- #
echo
if [ "$DRY_RUN" = "1" ]; then
  echo "${c_green}Dry run complete — nothing was changed.${c_off}"
  echo "Re-run without --dry-run to actually uninstall."
  exit 0
fi

echo "${c_green}PrntBtlr has been removed.${c_off}"
if [ -d "$SCAN_DIR" ]; then echo "  Your scans are still in $SCAN_DIR."; fi
if [ -d "$ENV_DIR" ];  then echo "  Config and state are still in $ENV_DIR."; fi
echo "  Uninstall log: $LOG_FILE"
echo
echo "Left in place on purpose (say the word if you want them gone too):"
[ "$PURGE_PACKAGES" = "1" ] || echo "  - CUPS/SANE/Samba and the printers configured in CUPS"
echo "  - the 'pixma' line in scanbd's dll.conf (harmless; it may predate PrntBtlr)"
echo "  - group memberships (lpadmin, lp, scanner, saned) — remove with: gpasswd -d <user> <group>"
echo "  - printer sharing in CUPS (cupsctl --no-share-printers turns it off)"
