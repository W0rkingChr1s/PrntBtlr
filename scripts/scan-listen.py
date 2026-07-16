#!/usr/bin/env python3
"""PrntBtlr button-scan listener for Canon PIXMA (raw USB interrupt).

Some Canon PIXMA MFPs — the MX870 among them — do not report their scan button
through SANE's pollable ``button-1``/``button-2`` options, so ``scanbd`` never
fires. They *do* emit the press on the USB interrupt endpoint, though. This
daemon reads that endpoint directly and, on a press, runs the same scan script
scanbd would, dropping a PDF into the shared scan folder:

    Color button  (buf[7] & 1)  -> scan2pdf.sh       (plain PDF)
    Black button  (buf[7] & 2)  -> scan2pdf-ocr.sh   (searchable PDF, if OCR set up)

The interrupt payload was decoded against the SANE pixma backend
(``pixma_mp150.c``): button number in ``buf[7]``, document type in ``buf[6]``,
ADF status in ``buf[8]``.

Config via environment:
    PRNTBTLR_SCAN_USB_VID    USB vendor id  (default 0x04a9 = Canon)
    PRNTBTLR_SCAN_USB_PID    USB product id (default: first matching device)
    PRNTBTLR_SCANBD_SCRIPTS  dir holding scan2pdf.sh (default /etc/scanbd/scripts)
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

try:
    import usb.core
    import usb.util
except ImportError:
    sys.exit("python3-usb (pyusb) is required: sudo apt-get install -y python3-usb")

CANON_VID = 0x04A9
VID = int(str(os.environ.get("PRNTBTLR_SCAN_USB_VID", CANON_VID)), 0)
_pid = os.environ.get("PRNTBTLR_SCAN_USB_PID")
PID = int(_pid, 0) if _pid else None

SCRIPT_DIR = os.environ.get("PRNTBTLR_SCANBD_SCRIPTS", "/etc/scanbd/scripts")
SCAN_COLOR = os.path.join(SCRIPT_DIR, "scan2pdf.sh")
SCAN_BLACK = os.path.join(SCRIPT_DIR, "scan2pdf-ocr.sh")

# The scanner is briefly busy right after the press (it kicks off its own "scan
# to PC" attempt), so wait before pulling; scan2pdf.sh also retries internally.
SETTLE_SECONDS = 2.0
# The device sends several interrupt packets per press; after a scan we pause to
# swallow repeats and let the device settle before listening again.
DRAIN_SECONDS = 3.0
SCAN_TIMEOUT = 600


def log(msg: str) -> None:
    print(f"prntbtlr-scan-listen: {msg}", flush=True)


def find_device():
    kw = {"idVendor": VID}
    if PID is not None:
        kw["idProduct"] = PID
    return usb.core.find(**kw)


def find_interrupt_in(dev):
    """Return (interface, endpoint) for the first interrupt-IN endpoint."""
    for cfg in dev:
        for intf in cfg:
            for ep in intf:
                is_in = usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN
                is_intr = usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_INTR
                if is_in and is_intr:
                    return intf, ep
    return None, None


def run_scan(which: int) -> None:
    script = SCAN_COLOR if which == 1 else SCAN_BLACK
    if not os.path.exists(script):
        log(f"scan script not found: {script} (run the installer, or set PRNTBTLR_SCANBD_SCRIPTS)")
        return
    env = dict(os.environ)
    if which == 2:
        # Black button → searchable PDF (best-effort; falls back to a plain scan).
        env.setdefault("PRNTBTLR_OCR", "1")
    label = "color → scan2pdf.sh" if which == 1 else "black → scan2pdf-ocr.sh"
    log(f"button {which} pressed ({label})")
    try:
        subprocess.run(["/bin/bash", script], env=env, timeout=SCAN_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        log("scan script timed out")
    log("scan finished")


def listen_once() -> bool:
    """Attach to the scanner and listen until a button fires or the device drops.

    Returns True if it should be retried immediately (button handled or a
    recoverable USB hiccup), False if no device is present (caller backs off).
    """
    dev = find_device()
    if dev is None:
        return False

    intf, ep = find_interrupt_in(dev)
    if ep is None:
        log("device has no interrupt-IN endpoint — cannot read buttons")
        time.sleep(10)
        return True

    ifnum = intf.bInterfaceNumber
    try:
        if dev.is_kernel_driver_active(ifnum):
            dev.detach_kernel_driver(ifnum)
    except (usb.core.USBError, NotImplementedError):
        pass

    log(f"listening on interrupt EP 0x{ep.bEndpointAddress:02x} (interface {ifnum})")

    # Only fire once we've seen a released state, so a stale "pressed" packet
    # right after (re)connecting can't trigger a phantom scan.
    armed = False
    while True:
        try:
            data = bytes(dev.read(ep.bEndpointAddress, ep.wMaxPacketSize, timeout=2000))
        except usb.core.USBError as err:
            if err.errno in (110, 60):  # timeout — normal, keep waiting
                continue
            # Device busy (e.g. a panel scan) or unplugged — re-discover.
            log(f"usb read error ({err.errno}); re-acquiring device")
            usb.util.dispose_resources(dev)
            return True

        if len(data) <= 7:
            continue

        pressed = bool(data[7] & 0x03)
        if not pressed:
            armed = True
            continue
        if not armed:
            continue

        which = 1 if (data[7] & 1) else 2
        # A plain release leaves the scanner mid-transaction from our interrupt
        # reads, and the device is still busy with its own "scan to PC" push — a
        # scan pulled right now comes back truncated. A USB reset puts it back
        # into a clean, idle state so scanimage gets a full page.
        try:
            dev.reset()
        except usb.core.USBError as err:
            log(f"usb reset failed ({err}); continuing")
        usb.util.dispose_resources(dev)
        time.sleep(SETTLE_SECONDS)
        run_scan(which)
        time.sleep(DRAIN_SECONDS)
        return True


def main() -> None:
    log("starting Canon PIXMA interrupt button listener")
    backoff = 2
    while True:
        try:
            retry_now = listen_once()
        except usb.core.USBError as err:
            log(f"usb error: {err}")
            retry_now = True
        if retry_now:
            backoff = 2
            continue
        time.sleep(backoff)
        backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    main()
