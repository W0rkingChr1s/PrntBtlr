"""Tests for scan device parsing and the path-traversal guard."""

from app.config import settings
from app.services import scan


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
