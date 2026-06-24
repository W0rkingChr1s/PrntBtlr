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
    """Stub out scanimage + img2pdf (+ optionally ocrmypdf) with real file writes."""
    monkeypatch.setattr(settings, "scan_dir", tmp_path)
    monkeypatch.setattr(scan, "available", lambda: True)
    monkeypatch.setattr(scan, "first_device", lambda: "pixma")

    def fake_scan_to_file(cmd, dest):
        Path(dest).write_bytes(b"II*\x00fake-tiff")
        return scan.shell.Result(True, 0, "", "")

    monkeypatch.setattr(scan, "_scan_to_file", fake_scan_to_file)
    monkeypatch.setattr(
        scan.shell,
        "which",
        lambda b: None if (b == "ocrmypdf" and not ocr_installed) else f"/usr/bin/{b}",
    )

    def fake_run(cmd, **k):
        out = cmd[cmd.index("-o") + 1] if "-o" in cmd else cmd[-1]
        Path(out).write_bytes(b"%PDF-1.4\n")
        return scan.shell.Result(True, 0, "", "")

    monkeypatch.setattr(scan.shell, "run", fake_run)


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
