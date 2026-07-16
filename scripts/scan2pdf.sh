#!/bin/bash
# PrntBtlr — button-triggered scan handler.
#
# Invoked by scanbd (or the PrntBtlr USB-interrupt listener, scan-listen.py) when
# the hardware scan button is pressed. Tries the ADF first (multi-page), falls
# back to the flatbed (single page), assembles a PDF and drops it into the shared
# scan folder.
#
# Set PRNTBTLR_OCR=1 to produce a searchable PDF via ocrmypdf (used by the
# scan2pdf-ocr.sh wrapper for a second scan button). PRNTBTLR_OCR_LANG selects
# the tesseract language(s), e.g. "deu+eng" (default "eng").
#
# Installed to /etc/scanbd/scripts/scan2pdf.sh by scripts/install.sh.

set -u

OUTDIR="${PRNTBTLR_SCAN_DIR:-/srv/scans}"
TS=$(date +%Y%m%d_%H%M%S)
DEV="${SCANBD_DEVICE:-pixma}"
MODE="${PRNTBTLR_SCAN_MODE:-Color}"   # Color | Gray | Lineart
OCR="${PRNTBTLR_OCR:-0}"
OCR_LANG="${PRNTBTLR_OCR_LANG:-eng}"
TMP=$(mktemp -d) || exit 1
cd "$TMP" || exit 1

cleanup() { cd / && rm -rf "$TMP"; }
trap cleanup EXIT

mkdir -p "$OUTDIR"

# Log that the button actually fired — makes "I pressed scan and nothing
# happened" diagnosable (check: journalctl -t prntbtlr).
logger -t prntbtlr "button scan fired (device=$DEV target=${SCANBD_TARGET:-?})"

OUT="$OUTDIR/scan_$TS.pdf"

# Assemble the scanned TIFF(s) into a PDF; succeeds only with a non-empty PDF.
build_pdf() {
  if command -v img2pdf >/dev/null 2>&1; then
    img2pdf p_*.tiff -o "$OUT" 2>/dev/null
  else
    convert p_*.tiff "$OUT" 2>/dev/null   # ImageMagick fallback
  fi
  [ -s "$OUT" ]
}

# Right after a hardware button press the scanner can be briefly busy (it starts
# its own "scan to PC" attempt), so a scan pulled immediately may open into
# nothing or come back truncated. Retry a few times and only accept a real page
# that converts to a non-empty PDF — an empty/partial scan never wins, and no
# 0-byte file is left behind.
saved=0
attempt=0
while [ "$attempt" -lt 4 ]; do
  attempt=$((attempt + 1))
  rm -f p_*.tiff

  # Prefer the document feeder (multi-page); fall back to the glass (single page).
  if ! { scanimage -d "$DEV" --source "Automatic Document Feeder" \
           --resolution 300 --mode "$MODE" --format=tiff \
           --batch=p_%03d.tiff 2>/dev/null && ls p_*.tiff >/dev/null 2>&1; }; then
    rm -f p_*.tiff
    scanimage -d "$DEV" --resolution 300 --mode "$MODE" --format=tiff \
      > p_001.tiff 2>/dev/null
  fi

  set -- p_*.tiff
  if [ -e "$1" ] && [ -s "$1" ] && build_pdf; then
    saved=1
    break
  fi

  rm -f p_*.tiff "$OUT"
  logger -t prntbtlr "scan attempt $attempt found no usable page (device busy?), retrying"
  sleep 2
done

if [ "$saved" != 1 ]; then
  rm -f "$OUT"
  logger -t prntbtlr "scan produced no pages (device=$DEV)"
  exit 0
fi

# Optional: add a searchable text layer (best-effort — keep the scan either way).
if [ "$OCR" = "1" ] && command -v ocrmypdf >/dev/null 2>&1; then
  if ocrmypdf -l "$OCR_LANG" --skip-text "$OUT" "$OUT.ocr" 2>/dev/null; then
    mv "$OUT.ocr" "$OUT"
    logger -t prntbtlr "OCR applied ($OCR_LANG)"
  else
    rm -f "$OUT.ocr"
    logger -t prntbtlr "OCR failed — saved plain scan"
  fi
fi

chmod 664 "$OUT"
logger -t prntbtlr "scan saved: $OUT"
