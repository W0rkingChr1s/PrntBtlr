"""Tests for scan device parsing, OCR, and the path-traversal guard."""

from pathlib import Path

from app.config import settings
from app.services import scan


def test_ocr_available(monkeypatch):
    monkeypatch.setattr(scan.shell, "which", lambda b: "/usr/bin/ocrmypdf")
    assert scan.ocr_available() is True
    monkeypatch.setattr(scan.shell, "which", lambda b: None)
    assert scan.ocr_available() is False


def _wire_fake_scan(monkeypatch, tmp_path, *, ocr_installed=True):
    """Stub out scanimage + img2pdf (+ optionally ocrmypdf) with real file writes.

    Returns a dict capturing the commands that were run, for asserting on flags.
    """
    monkeypatch.setattr(settings, "scan_dir", tmp_path)
    monkeypatch.setattr(scan, "available", lambda: True)
    monkeypatch.setattr(scan, "first_device", lambda: "pixma")
    captured = {"scanimage": None, "runs": []}

    def fake_scan_to_file(cmd, dest):
        captured["scanimage"] = cmd
        Path(dest).write_bytes(b"II*\x00fake-tiff")
        return scan.shell.Result(True, 0, "", "")

    monkeypatch.setattr(scan, "_scan_to_file", fake_scan_to_file)
    monkeypatch.setattr(
        scan.shell,
        "which",
        lambda b: None if (b == "ocrmypdf" and not ocr_installed) else f"/usr/bin/{b}",
    )

    def fake_run(cmd, **k):
        captured["runs"].append(cmd)
        out = cmd[cmd.index("-o") + 1] if "-o" in cmd else cmd[-1]
        Path(out).write_bytes(b"%PDF-1.4\n")
        return scan.shell.Result(True, 0, "", "")

    monkeypatch.setattr(scan.shell, "run", fake_run)
    return captured


def test_scan_now_with_ocr(tmp_path, monkeypatch):
    _wire_fake_scan(monkeypatch, tmp_path, ocr_installed=True)
    ok, msg, path = scan.scan_now(ocr=True)
    assert ok and path is not None and path.exists()
    assert "searchable" in msg
    # No temp tiff left behind.
    assert not list(tmp_path.glob("*.tiff"))


def test_scan_now_ocr_requested_but_unavailable(tmp_path, monkeypatch):
    _wire_fake_scan(monkeypatch, tmp_path, ocr_installed=False)
    ok, msg, path = scan.scan_now(ocr=True)
    assert ok and path is not None and path.exists()  # plain scan still saved
    assert "OCR skipped" in msg


def test_scan_now_defaults_to_a4(tmp_path, monkeypatch):
    captured = _wire_fake_scan(monkeypatch, tmp_path)
    ok, _, _ = scan.scan_now()
    assert ok
    cmd = captured["scanimage"]
    assert cmd[cmd.index("-x") + 1] == "210"
    assert cmd[cmd.index("-y") + 1] == "297"
    # The PDF page box is pinned to the exact standard size.
    img2pdf_cmd = captured["runs"][0]
    assert img2pdf_cmd[img2pdf_cmd.index("--pagesize") + 1] == "A4"


def test_scan_now_max_paper_scans_full_bed(tmp_path, monkeypatch):
    captured = _wire_fake_scan(monkeypatch, tmp_path)
    ok, _, _ = scan.scan_now(paper="Max")
    assert ok
    cmd = captured["scanimage"]
    assert "-x" not in cmd and "-y" not in cmd
    assert "--pagesize" not in captured["runs"][0]


def test_scan_now_leaves_no_intermediates(tmp_path, monkeypatch):
    _wire_fake_scan(monkeypatch, tmp_path)
    ok, _, path = scan.scan_now(ocr=True)
    assert ok and path is not None and path.exists()
    # Only the finished PDF remains — no .tiff, .part, or .ocr intermediates.
    assert [p.name for p in tmp_path.iterdir()] == [path.name]


def test_list_devices_parses_scanimage(monkeypatch):
    out = "device `pixma:MX870_1A2B3C' is a CANON Canon PIXMA MX870 multi-function peripheral\n"
    monkeypatch.setattr(scan.shell, "which", lambda b: "/usr/bin/scanimage")
    monkeypatch.setattr(scan.shell, "run", lambda cmd, **k: scan.shell.Result(True, 0, out, ""))
    devices = scan.list_devices()
    assert len(devices) == 1
    assert devices[0].device == "pixma:MX870_1A2B3C"
    assert "MX870" in devices[0].description


def test_resolve_scan_blocks_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "scan_dir", tmp_path)
    (tmp_path / "ok.pdf").write_bytes(b"%PDF-1.4\n")

    assert scan.resolve_scan("ok.pdf") is not None
    # Path traversal and non-PDF must be rejected.
    assert scan.resolve_scan("../../etc/passwd") is None
    assert scan.resolve_scan("../ok.pdf") is None
    assert scan.resolve_scan("missing.pdf") is None


def test_list_scans_sorted_newest_first(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setattr(settings, "scan_dir", tmp_path)
    old = tmp_path / "scan_old.pdf"
    new = tmp_path / "scan_new.pdf"
    old.write_bytes(b"%PDF old")
    new.write_bytes(b"%PDF new")
    os.utime(old, (time.time() - 100, time.time() - 100))

    scans = scan.list_scans()
    assert [s.name for s in scans] == ["scan_new.pdf", "scan_old.pdf"]


def test_parse_capabilities_discrete_lists():
    out = """
Options specific to device `pixma:MX870_1A2B3C':
  Scan mode:
    --resolution 75|150|300|600|1200dpi [75]
        Sets the resolution of the scanned image.
    --mode Color|Gray|Lineart [Color]
        Selects the scan mode.
    --source Flatbed|Automatic Document Feeder|Automatic Document Feeder (Duplex) [Flatbed]
        Selects the scan source.
  Geometry:
    -l 0..216.07mm [0]
"""
    caps = scan.parse_capabilities("pixma:MX870_1A2B3C", out)
    assert caps.modes == ["Color", "Gray", "Lineart"]
    assert "Automatic Document Feeder" in caps.sources
    assert "Automatic Document Feeder (Duplex)" in caps.sources  # value with spaces & parens
    assert caps.resolutions == [75, 150, 300, 600, 1200]
    assert caps.resolution_range is None
    assert caps.resolution_choices() == [75, 150, 300, 600, 1200]


def test_parse_capabilities_resolution_range():
    out = "    --resolution 75..1200dpi (in steps of 1) [75]\n    --mode Color|Gray [Color]\n"
    caps = scan.parse_capabilities("dev", out)
    assert caps.resolution_range == (75, 1200)
    assert caps.resolutions == []
    # A pick list is derived from the range, bounded by it.
    assert caps.resolution_choices() == [75, 150, 300, 600, 1200]


def test_probe_device_saves_capabilities(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "scan_caps_file", tmp_path / "caps.json")
    monkeypatch.setattr(scan, "available", lambda: True)
    out = "    --mode Color|Gray [Color]\n    --source Flatbed [Flatbed]\n    --resolution 150|300dpi [150]\n"
    monkeypatch.setattr(scan.shell, "run", lambda cmd, **k: scan.shell.Result(True, 0, out, ""))

    ok, caps, msg = scan.probe_device("pixma")
    assert ok and caps is not None
    assert "initialized" in msg.lower()
    # Cached and reloadable.
    reloaded = scan.capabilities()
    assert reloaded is not None
    assert reloaded.modes == ["Color", "Gray"]
    assert reloaded.resolutions == [150, 300]


def test_probe_device_reports_no_options(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "scan_caps_file", tmp_path / "caps.json")
    monkeypatch.setattr(scan, "available", lambda: True)
    monkeypatch.setattr(
        scan.shell, "run", lambda cmd, **k: scan.shell.Result(True, 0, "nothing\n", "")
    )
    ok, caps, msg = scan.probe_device("pixma")
    assert not ok and caps is None
    assert "no scan options" in msg.lower()
    assert not (tmp_path / "caps.json").exists()  # nothing cached on an empty probe


def test_probe_device_unavailable(monkeypatch):
    monkeypatch.setattr(scan, "available", lambda: False)
    ok, caps, msg = scan.probe_device()
    assert not ok and caps is None
    assert "not installed" in msg


def test_effective_options_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "scan_caps_file", tmp_path / "missing.json")
    modes, sources, resolutions, caps = scan.effective_options()
    assert caps is None
    assert modes == list(scan.DEFAULT_MODES)
    assert resolutions == list(scan.DEFAULT_RESOLUTIONS)


def test_effective_options_uses_detected(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "scan_caps_file", tmp_path / "caps.json")
    monkeypatch.setattr(scan, "available", lambda: True)
    out = "    --mode Color [Color]\n    --source ADF [ADF]\n    --resolution 200|400dpi [200]\n"
    monkeypatch.setattr(scan.shell, "run", lambda cmd, **k: scan.shell.Result(True, 0, out, ""))
    scan.probe_device("dev")

    modes, sources, resolutions, caps = scan.effective_options()
    assert caps is not None
    assert modes == ["Color"]
    assert sources == ["ADF"]
    assert resolutions == [200, 400]


def test_list_scans_ignores_in_progress_dotfiles(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "scan_dir", tmp_path)
    (tmp_path / "scan_done.pdf").write_bytes(b"%PDF done")
    (tmp_path / ".prntbtlr_x.pdf.part").write_bytes(b"")
    (tmp_path / ".prntbtlr_x.tiff").write_bytes(b"II*\x00")
    (tmp_path / ".hidden.pdf").write_bytes(b"%PDF hidden")

    assert [s.name for s in scan.list_scans()] == ["scan_done.pdf"]
    assert scan.resolve_scan(".hidden.pdf") is None
