"""Scanning: device discovery, on-demand scans, and the saved-scan library.

Button-triggered scanning is handled out-of-process by ``scanbd`` calling
``scripts/scan2pdf.sh`` (see the install script). This module covers the parts
the web UI needs: listing SANE devices, triggering an ad-hoc scan from the
browser, and browsing/serving the resulting PDFs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import settings
from . import shell

# ``device `pixma:MX870_1A2B3C' is a CANON Canon PIXMA MX870 ...``
_DEVICE_RE = re.compile(r"device `(?P<dev>[^']+)' is a (?P<desc>.+)$")


@dataclass
class ScanDevice:
    device: str
    description: str = ""


@dataclass
class ScanFile:
    name: str
    size: int
    modified: datetime

    @property
    def size_human(self) -> str:
        return _human_size(self.size)

    @property
    def modified_human(self) -> str:
        return self.modified.strftime("%Y-%m-%d %H:%M")


def _human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


# --------------------------------------------------------------------------- #
# Devices
# --------------------------------------------------------------------------- #
def available() -> bool:
    return shell.which(settings.scanimage) is not None


def ocr_available() -> bool:
    """True when ocrmypdf is installed (enables searchable-PDF output)."""
    return shell.which("ocrmypdf") is not None


def list_devices() -> list[ScanDevice]:
    """Run ``scanimage -L``. Can take a few seconds while the USB bus settles."""
    res = shell.run([settings.scanimage, "-L"], timeout=settings.command_timeout)
    devices: list[ScanDevice] = []
    if not res.ok:
        return devices
    for line in res.stdout.splitlines():
        m = _DEVICE_RE.search(line.strip())
        if m:
            devices.append(ScanDevice(device=m.group("dev"), description=m.group("desc").strip()))
    return devices


def first_device() -> str:
    devices = list_devices()
    return devices[0].device if devices else settings.default_scan_device


# --------------------------------------------------------------------------- #
# Ad-hoc scan from the browser
# --------------------------------------------------------------------------- #
def scan_now(
    *,
    device: str | None = None,
    source: str = "Flatbed",
    mode: str = "Color",
    resolution: int = 300,
    ocr: bool = False,
) -> tuple[bool, str, Path | None]:
    """Perform a single scan and convert it to a PDF in :data:`settings.scan_dir`.

    When *ocr* is true and ocrmypdf is installed, the PDF is post-processed into a
    searchable PDF (text layer via tesseract). Returns ``(ok, message, path)``.
    The heavy lifting (multi-page ADF batches) lives in ``scan2pdf.sh``; from the
    browser we keep it to a single page so the request stays predictable.
    """
    if not available():
        return False, "scanimage is not installed.", None

    dev = device or first_device()
    settings.scan_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tiff = settings.scan_dir / f".prntbtlr_{ts}.tiff"
    pdf = settings.scan_dir / f"scan_{ts}.pdf"

    cmd = [
        settings.scanimage,
        "-d",
        dev,
        "--resolution",
        str(resolution),
        "--mode",
        mode,
        "--format=tiff",
    ]
    if source:
        cmd += ["--source", source]

    # scanimage streams raw image bytes to stdout — write them straight to disk so
    # binary fidelity is preserved (text capture would corrupt the TIFF).
    proc = _scan_to_file(cmd, tiff)
    if not proc.ok:
        tiff.unlink(missing_ok=True)
        return False, proc.output or "Scan failed.", None

    if shell.which("img2pdf"):
        conv = shell.run(["img2pdf", str(tiff), "-o", str(pdf)])
    else:
        conv = shell.run(["convert", str(tiff), str(pdf)])  # ImageMagick fallback

    tiff.unlink(missing_ok=True)
    if not conv.ok:
        return False, f"PDF conversion failed: {conv.output}", None

    if ocr:
        ok, note = _ocr_in_place(pdf)
        if ok:
            return True, f"Saved {pdf.name} (searchable)", pdf
        # OCR is best-effort: keep the plain scan, but tell the user it didn't run.
        return True, f"Saved {pdf.name} — OCR skipped: {note}", pdf

    return True, f"Saved {pdf.name}", pdf


def _ocr_in_place(pdf: Path) -> tuple[bool, str]:
    """Add a searchable text layer to *pdf* using ocrmypdf. Best-effort."""
    if not ocr_available():
        return False, "ocrmypdf not installed"
    out = pdf.with_suffix(".ocr.pdf")
    res = shell.run(
        [
            "ocrmypdf",
            "-l",
            settings.ocr_lang,
            "--skip-text",  # don't fail if a page already has text
            str(pdf),
            str(out),
        ],
        timeout=settings.scan_timeout,
    )
    if res.ok and out.exists():
        out.replace(pdf)
        return True, ""
    out.unlink(missing_ok=True)
    return False, res.output or "ocrmypdf failed"


def _scan_to_file(cmd: list[str], dest: Path) -> shell.Result:
    """Run scanimage writing raw bytes directly to *dest* (binary-safe)."""
    import subprocess

    try:
        with open(dest, "wb") as fh:
            proc = subprocess.run(
                cmd,
                stdout=fh,
                stderr=subprocess.PIPE,
                timeout=settings.scan_timeout,
                check=False,
            )
    except FileNotFoundError:
        return shell.Result(False, 127, "", f"command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        return shell.Result(False, 124, "", "scan timed out")
    return shell.Result(
        ok=proc.returncode == 0,
        returncode=proc.returncode,
        stdout="",
        stderr=(proc.stderr or b"").decode(errors="ignore"),
    )


# --------------------------------------------------------------------------- #
# Saved-scan library
# --------------------------------------------------------------------------- #
def list_scans() -> list[ScanFile]:
    directory = settings.scan_dir
    if not directory.exists():
        return []
    files: list[ScanFile] = []
    for entry in directory.iterdir():
        if entry.is_file() and entry.suffix.lower() == ".pdf":
            stat = entry.stat()
            files.append(
                ScanFile(
                    name=entry.name,
                    size=stat.st_size,
                    modified=datetime.fromtimestamp(stat.st_mtime),
                )
            )
    files.sort(key=lambda f: f.modified, reverse=True)
    return files


def resolve_scan(name: str) -> Path | None:
    """Return the path of a saved scan, guarding against path traversal."""
    candidate = (settings.scan_dir / name).resolve()
    base = settings.scan_dir.resolve()
    if base not in candidate.parents or not candidate.is_file():
        return None
    if candidate.suffix.lower() != ".pdf":
        return None
    return candidate


def delete_scan(name: str) -> bool:
    path = resolve_scan(name)
    if path is None:
        return False
    path.unlink(missing_ok=True)
    return True
