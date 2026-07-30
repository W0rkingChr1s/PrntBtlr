"""Scanning: device discovery, on-demand scans, and the saved-scan library.

Button-triggered scanning is handled out-of-process by ``scanbd`` calling
``scripts/scan2pdf.sh`` (see the install script). This module covers the parts
the web UI needs: listing SANE devices, triggering an ad-hoc scan from the
browser, and browsing/serving the resulting PDFs.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import settings
from . import cache, shell, statefile

# ``scanimage -L`` is the slowest discovery probe (it waits for the USB bus to
# settle) yet its answer barely changes between refreshes, so a short cache
# keeps it off the critical path of every page load and health poll.
_devices_cache = cache.TTLCache(settings.scan_devices_cache_ttl)

# ``device `pixma:MX870_1A2B3C' is a CANON Canon PIXMA MX870 ...``
_DEVICE_RE = re.compile(r"device `(?P<dev>[^']+)' is a (?P<desc>.+)$")

# Scan-window sizes in mm. Without an explicit -x/-y, scanners scan their
# maximum area (216x356 mm on a PIXMA ADF), so pages come out oversized instead
# of A4/Letter. "Max" (no entry) keeps the full bed.
PAPER_SIZES: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
}
PAPER_CHOICES: tuple[str, ...] = ("A4", "Letter", "Legal", "Max")

# Fallback scan options used until the scanner has been initialized (or when the
# probe turns up nothing). These match what the Scans form has always offered.
DEFAULT_MODES: tuple[str, ...] = ("Color", "Gray", "Lineart")
DEFAULT_SOURCES: tuple[str, ...] = ("Flatbed", "Automatic Document Feeder")
DEFAULT_RESOLUTIONS: tuple[int, ...] = (150, 300, 600)

# Friendly labels for the SANE mode/source names; anything unmapped is shown
# verbatim (a detected scanner may expose names we don't know about).
MODE_LABELS: dict[str, str] = {
    "Color": "Color",
    "Gray": "Grayscale",
    "Lineart": "Black & white",
}
SOURCE_LABELS: dict[str, str] = {
    "Flatbed": "Flatbed (glass)",
    "Automatic Document Feeder": "Document feeder (ADF)",
    "Automatic Document Feeder (Duplex)": "Document feeder — duplex",
    "ADF": "Document feeder (ADF)",
    "ADF Duplex": "Document feeder — duplex",
}

# One option line from ``scanimage -A``: ``    --mode Color|Gray|Lineart [Color]``
_OPT_RE = re.compile(r"^\s*(--[a-z0-9-]+)\s+(.*)$", re.IGNORECASE)


def _paper_dims(paper: str) -> tuple[str, tuple[float, float] | None]:
    """Normalize a paper name; returns ``(canonical_name, (w_mm, h_mm) | None)``."""
    for name, dims in PAPER_SIZES.items():
        if name.lower() == paper.strip().lower():
            return name, dims
    return "Max", None


@dataclass
class ScanDevice:
    device: str
    description: str = ""


@dataclass
class ScanCapabilities:
    """What a scanner actually supports, as detected by the init step.

    ``resolutions`` is the discrete list a scanner offers; scanners that report a
    continuous range instead fill ``resolution_range`` (min, max) and we derive a
    sensible pick list from it. Either way :meth:`resolution_choices` yields the
    values to show in the form.
    """

    device: str
    modes: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    resolutions: list[int] = field(default_factory=list)
    resolution_range: tuple[int, int] | None = None
    detected_at: str = ""

    def resolution_choices(self) -> list[int]:
        if self.resolutions:
            return self.resolutions
        if self.resolution_range:
            lo, hi = self.resolution_range
            # Offer the common steps that fall inside the supported range.
            picks = [r for r in (75, 150, 300, 600, 1200) if lo <= r <= hi]
            return picks or [lo, hi]
        return list(DEFAULT_RESOLUTIONS)


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
    """Run ``scanimage -L`` (cached briefly; can take seconds while USB settles)."""
    return _devices_cache.get_or_call("scanimage-L", _probe_devices)


def _probe_devices() -> list[ScanDevice]:
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
# Scanner initialization (capability probe)
# --------------------------------------------------------------------------- #
def _enum_values(spec: str) -> list[str]:
    """Pipe-separated option values, minus the trailing ``[default]`` marker."""
    spec = re.sub(r"\[.*?\]\s*$", "", spec).strip()
    return [v.strip() for v in spec.split("|") if v.strip()]


def _parse_resolution(spec: str) -> tuple[list[int], tuple[int, int] | None]:
    """Return ``(discrete_values, range)`` from a ``--resolution`` constraint.

    Handles both a discrete list (``75|150|300dpi``) and a continuous range
    (``75..1200dpi (in steps of 1)``).
    """
    if ".." in spec:
        m = re.search(r"(\d+(?:\.\d+)?)\s*\.\.\s*(\d+(?:\.\d+)?)", spec)
        if m:
            return [], (int(float(m.group(1))), int(float(m.group(2))))
        return [], None
    spec = re.sub(r"\[.*?\]\s*$", "", spec)  # drop the [default] before scanning
    values = [int(float(x)) for x in re.findall(r"\d+(?:\.\d+)?", spec)]
    return values, None


def parse_capabilities(device: str, output: str) -> ScanCapabilities:
    """Parse ``scanimage -A`` output into a :class:`ScanCapabilities`."""
    caps = ScanCapabilities(device=device, detected_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
    for line in output.splitlines():
        m = _OPT_RE.match(line)
        if not m:
            continue
        name, spec = m.group(1).lower(), m.group(2).strip()
        if name == "--mode":
            caps.modes = _enum_values(spec)
        elif name == "--source":
            caps.sources = _enum_values(spec)
        elif name == "--resolution":
            caps.resolutions, caps.resolution_range = _parse_resolution(spec)
    return caps


def probe_device(device: str | None = None) -> tuple[bool, ScanCapabilities | None, str]:
    """Initialize a scanner: ask it for all its options and cache what it supports.

    Runs ``scanimage -A -d <device>`` (``--all-options``), parses the available
    modes/sources/resolutions and stores them so the Scans page can offer exactly
    what the hardware does. Returns ``(ok, capabilities, message)``.
    """
    if not available():
        return False, None, "scanimage is not installed."
    dev = device or first_device()
    res = shell.run([settings.scanimage, "-d", dev, "-A"], timeout=settings.command_timeout)
    if not res.ok:
        return False, None, res.output or "Scanner did not respond."
    caps = parse_capabilities(dev, res.stdout)
    if not (caps.modes or caps.sources or caps.resolutions or caps.resolution_range):
        return False, None, "Connected, but no scan options were reported."
    try:
        _save_caps(caps)
    except OSError as exc:
        return True, caps, f"Detected options but could not save them: {exc}"
    parts = []
    if caps.modes:
        parts.append(f"{len(caps.modes)} modes")
    if caps.sources:
        parts.append(f"{len(caps.sources)} sources")
    choices = caps.resolution_choices()
    if choices:
        parts.append(f"resolution up to {max(choices)} dpi")
    return True, caps, "Scanner initialized — " + ", ".join(parts) + "."


def _save_caps(caps: ScanCapabilities) -> None:
    statefile.save_json(settings.scan_caps_file, asdict(caps))


def capabilities() -> ScanCapabilities | None:
    """The cached capabilities from the last successful init, or ``None``."""
    try:
        with open(settings.scan_caps_file) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "device" not in data:
        return None
    rng = data.get("resolution_range")
    return ScanCapabilities(
        device=data.get("device", ""),
        modes=list(data.get("modes") or []),
        sources=list(data.get("sources") or []),
        resolutions=[int(r) for r in (data.get("resolutions") or [])],
        resolution_range=(int(rng[0]), int(rng[1])) if rng else None,
        detected_at=data.get("detected_at", ""),
    )


def effective_options() -> tuple[list[str], list[str], list[int], ScanCapabilities | None]:
    """Scan form choices — detected capabilities if initialized, else defaults.

    Returns ``(modes, sources, resolutions, caps)`` where *caps* is the cached
    :class:`ScanCapabilities` (or ``None`` when the scanner hasn't been
    initialized yet), so the UI can note whether it's showing detected options.
    """
    caps = capabilities()
    if caps is None:
        return list(DEFAULT_MODES), list(DEFAULT_SOURCES), list(DEFAULT_RESOLUTIONS), None
    modes = caps.modes or list(DEFAULT_MODES)
    sources = caps.sources or list(DEFAULT_SOURCES)
    return modes, sources, caps.resolution_choices(), caps


# --------------------------------------------------------------------------- #
# Ad-hoc scan from the browser
# --------------------------------------------------------------------------- #
def scan_now(
    *,
    device: str | None = None,
    source: str = "Flatbed",
    mode: str = "Color",
    resolution: int = 300,
    paper: str | None = None,
    ocr: bool = False,
) -> tuple[bool, str, Path | None]:
    """Perform a single scan and convert it to a PDF in :data:`settings.scan_dir`.

    *paper* constrains the scan window (default :data:`settings.scan_paper`, A4);
    ``"Max"`` scans the full bed. When *ocr* is true and ocrmypdf is installed,
    the PDF is post-processed into a searchable PDF (text layer via tesseract).
    Returns ``(ok, message, path)``. The heavy lifting (multi-page ADF batches)
    lives in ``scan2pdf.sh``; from the browser we keep it to a single page so the
    request stays predictable.

    The PDF is assembled under a hidden ``.part`` name and renamed into place
    only when finished, so the SMB share (and the scan library) never shows a
    0-byte or half-written file.
    """
    if not available():
        return False, "scanimage is not installed.", None

    dev = device or first_device()
    settings.scan_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tiff = settings.scan_dir / f".prntbtlr_{ts}.tiff"
    part = settings.scan_dir / f".prntbtlr_{ts}.pdf.part"
    pdf = settings.scan_dir / f"scan_{ts}.pdf"

    paper_name, dims = _paper_dims(paper or settings.scan_paper)

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
    if dims is not None:
        cmd += ["-x", f"{dims[0]:g}", "-y", f"{dims[1]:g}"]
    if source:
        cmd += ["--source", source]

    # scanimage streams raw image bytes to stdout — write them straight to disk so
    # binary fidelity is preserved (text capture would corrupt the TIFF).
    proc = _scan_to_file(cmd, tiff)
    if not proc.ok:
        tiff.unlink(missing_ok=True)
        return False, proc.output or "Scan failed.", None

    if shell.which("img2pdf"):
        conv_cmd = ["img2pdf"]
        if dims is not None:
            # Pin the PDF page box to the exact standard size.
            conv_cmd += ["--pagesize", paper_name]
        conv = shell.run(conv_cmd + [str(tiff), "-o", str(part)])
    else:
        # ImageMagick fallback; the pdf: prefix names the format, which convert
        # can't infer from the hidden .part filename.
        conv = shell.run(["convert", str(tiff), f"pdf:{part}"])

    tiff.unlink(missing_ok=True)
    if not conv.ok:
        part.unlink(missing_ok=True)
        return False, f"PDF conversion failed: {conv.output}", None

    note = ""
    searchable = False
    if ocr:
        # OCR runs on the hidden .part file, before the scan becomes visible.
        searchable, note = _ocr_in_place(part)

    part.replace(pdf)  # atomic: the finished PDF appears in one step

    if searchable:
        return True, f"Saved {pdf.name} (searchable)", pdf
    if ocr:
        # OCR is best-effort: keep the plain scan, but tell the user it didn't run.
        return True, f"Saved {pdf.name} — OCR skipped: {note}", pdf
    return True, f"Saved {pdf.name}", pdf


def _ocr_in_place(pdf: Path) -> tuple[bool, str]:
    """Add a searchable text layer to *pdf* using ocrmypdf. Best-effort.

    The intermediate keeps *pdf*'s (possibly hidden ``.part``) name plus a
    suffix, so it never surfaces as a visible ``scan_*.pdf`` in the share.
    """
    if not ocr_available():
        return False, "ocrmypdf not installed"
    out = pdf.with_name(pdf.name + ".ocr")
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
        # Dotfiles are in-progress intermediates (.prntbtlr_*.tiff/.pdf.part) —
        # never list them.
        if entry.name.startswith("."):
            continue
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
    if candidate.suffix.lower() != ".pdf" or candidate.name.startswith("."):
        return None
    return candidate


def delete_scan(name: str) -> bool:
    path = resolve_scan(name)
    if path is None:
        return False
    path.unlink(missing_ok=True)
    return True
