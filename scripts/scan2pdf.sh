#!/bin/bash
# PrntBtlr — button-triggered scan handler.
#
# Invoked by scanbd when the hardware scan button is pressed. Tries the ADF
# first (multi-page), falls back to the flatbed (single page), assembles a PDF
# and drops it into the shared scan folder.
#
# Installed to /etc/scanbd/scripts/scan2pdf.sh by scripts/install.sh.

set -u

OUTDIR="${PRNTBTLR_SCAN_DIR:-/srv/scans}"
TS=$(date +%Y%m%d_%H%M%S)
DEV="${SCANBD_DEVICE:-pixma}"
TMP=$(mktemp -d) || exit 1
cd "$TMP" || exit 1

cleanup() { cd / && rm -rf "$TMP"; }
trap cleanup EXIT

mkdir -p "$OUTDIR"

# Prefer the document feeder (multi-page); fall back to the glass (single page).
if scanimage -d "$DEV" --source "Automatic Document Feeder" \
     --resolution 300 --mode Color --format=tiff \
     --batch=p_%03d.tiff 2>/dev/null; then
  :
else
  scanimage -d "$DEV" --resolution 300 --mode Color --format=tiff > p_001.tiff
fi

if ls p_*.tiff >/dev/null 2>&1; then
  OUT="$OUTDIR/scan_$TS.pdf"
  if command -v img2pdf >/dev/null 2>&1; then
    img2pdf p_*.tiff -o "$OUT"
  else
    convert p_*.tiff "$OUT"   # ImageMagick fallback
  fi
  chmod 664 "$OUT"
  logger -t prntbtlr "scan saved: $OUT"
else
  logger -t prntbtlr "scan produced no pages (device=$DEV)"
fi
