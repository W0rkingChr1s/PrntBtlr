#!/bin/bash
# PrntBtlr — button-triggered scan handler that produces a SEARCHABLE PDF.
#
# Thin wrapper around scan2pdf.sh with OCR enabled. Wire this to a second scan
# button (e.g. the MX870's second task button) so one button gives a plain PDF
# and the other an OCR'd, searchable PDF.
#
# PRNTBTLR_OCR_LANG selects the tesseract language(s), e.g. "deu+eng".
#
# Installed to /etc/scanbd/scripts/scan2pdf-ocr.sh by scripts/install.sh.

export PRNTBTLR_OCR=1
exec "$(dirname "$0")/scan2pdf.sh"
