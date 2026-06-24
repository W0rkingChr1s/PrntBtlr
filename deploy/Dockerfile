# PrntBtlr control panel — container image.
#
# This image ships the panel plus the CUPS/SANE *client* tools so it can drive a
# CUPS daemon and scanner. The button-scan flow (scanbd) is a host-level concern
# and is NOT part of this image — run the installer on the host for that, or use
# this container purely as the management panel + ad-hoc scanning front door.
#
# See deploy/docker-compose.yml for how to wire up CUPS, USB and the scan volume.

FROM python:3.14-slim

# CLI tooling the app shells out to. cups-client gives lpstat/lpadmin/lpinfo/lp;
# sane-utils gives scanimage; img2pdf assembles scanned PDFs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cups-client sane-utils img2pdf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/prntbtlr

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY pyproject.toml ./

ENV PRNTBTLR_PORT=80 \
    PRNTBTLR_SCAN_DIR=/srv/scans \
    PYTHONUNBUFFERED=1

EXPOSE 80
CMD ["python", "-m", "app.main"]
