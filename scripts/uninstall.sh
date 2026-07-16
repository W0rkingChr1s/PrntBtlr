#!/usr/bin/env bash
#
# PrntBtlr uninstaller — removes the control panel and its system integration.
# Leaves CUPS/SANE/Samba packages and your scans in place by default.
#
# Usage:
#   sudo ./scripts/uninstall.sh                 # remove app + service
#   sudo PURGE_CONFIG=1 ./scripts/uninstall.sh  # also remove udev rule + scan script
#
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }

echo "==> Stopping and disabling prntbtlr service…"
systemctl disable --now prntbtlr 2>/dev/null || true
rm -f /etc/systemd/system/prntbtlr.service
systemctl daemon-reload

echo "==> Removing application files…"
rm -rf /opt/prntbtlr /opt/prntbtlr-src

if [ "${PURGE_CONFIG:-0}" = "1" ]; then
  echo "==> Removing PrntBtlr system config (udev rule, scan script, env)…"
  rm -f /etc/udev/rules.d/50-prntbtlr-noautosuspend.rules
  rm -f /etc/udev/rules.d/50-canon-noautosuspend.rules  # legacy name
  rm -f /etc/scanbd/scripts/scan2pdf.sh /etc/scanbd/scripts/scan2pdf-ocr.sh
  rm -f /etc/scanbd/scanner.d/prntbtlr-pixma.conf
  rm -rf /etc/prntbtlr
  udevadm control --reload-rules 2>/dev/null || true
  echo "    Note: the Samba [scans] block and the scanbd.conf include line were"
  echo "    left in place. Remove them by hand from /etc/samba/smb.conf and"
  echo "    /etc/scanbd/scanbd.conf if you want a full cleanup."
fi

echo "Done. Your scans in /srv/scans were left untouched."
