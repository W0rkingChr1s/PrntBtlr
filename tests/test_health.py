"""Tests for the health checks (control instances) — system calls are mocked."""

from app.services import cups, health, system


def _svc(name, active=True, enabled=True, status=None):
    return system.ServiceState(
        name=name,
        active=active,
        enabled=enabled,
        status=status or ("active" if active else "inactive"),
    )


def test_network_ok_with_lan_ip(monkeypatch):
    monkeypatch.setattr(health.system, "_primary_ip", lambda: "192.168.1.20")
    c = health.check_network()
    assert c.status == health.OK
    assert not c.repairable


def test_network_fail_when_loopback_only(monkeypatch):
    monkeypatch.setattr(health.system, "_primary_ip", lambda: "127.0.0.1")
    c = health.check_network()
    assert c.status == health.FAIL
    # Network is diagnostic-only: never auto-repaired (would risk the session).
    assert not c.repairable


def test_cups_down_is_repairable(monkeypatch):
    monkeypatch.setattr(health.cups, "available", lambda: True)
    monkeypatch.setattr(health.cups, "scheduler_running", lambda: False)
    c = health.check_cups()
    assert c.status == health.FAIL
    assert c.repairable
    assert c.key == "cups"


def test_cups_skipped_when_not_installed(monkeypatch):
    monkeypatch.setattr(health.cups, "available", lambda: False)
    assert health.check_cups().status == health.SKIP


def test_core_service_down_fails_and_is_repairable(monkeypatch):
    def fake_state(name):
        return _svc(name, active=(name != "smbd"), enabled=True)

    monkeypatch.setattr(health.system, "service_state", fake_state)
    checks = {c.key: c for c in health._core_service_checks()}
    assert checks["service:smbd"].status == health.FAIL
    assert checks["service:smbd"].repairable
    assert checks["service:cups"].status == health.OK
    # The scan-button pair is handled separately, not as core services.
    assert "service:scanbd" not in checks


def test_service_not_enabled_is_warning(monkeypatch):
    monkeypatch.setattr(
        health.system, "service_state", lambda name: _svc(name, active=True, enabled=False)
    )
    checks = {c.key: c for c in health._core_service_checks()}
    assert checks["service:cups"].status == health.WARN
    assert checks["service:cups"].repairable


def test_scan_button_ok_when_one_handler_active(monkeypatch):
    def fake_state(name):
        if name == "prntbtlr-scan-listen":
            return _svc(name, active=True, enabled=True)
        return _svc(name, active=False, enabled=False, status="inactive")

    monkeypatch.setattr(health.system, "service_state", fake_state)
    c = health.check_scan_button()
    assert c.status == health.OK


def test_scan_button_fail_when_none_running(monkeypatch):
    def fake_state(name):
        # scanbd installed + enabled but not running; listener absent.
        if name == "scanbd":
            return _svc(name, active=False, enabled=True, status="inactive")
        return _svc(name, active=False, enabled=False, status="not-installed")

    monkeypatch.setattr(health.system, "service_state", fake_state)
    c = health.check_scan_button()
    assert c.status == health.FAIL
    assert c.repairable
    assert health.scan_button_target() == "scanbd"


def test_scan_button_skipped_when_none_installed(monkeypatch):
    monkeypatch.setattr(
        health.system,
        "service_state",
        lambda name: _svc(name, active=False, enabled=False, status="not-installed"),
    )
    assert health.check_scan_button().status == health.SKIP
    assert health.scan_button_target() is None


def test_printers_warn_when_none_configured(monkeypatch):
    monkeypatch.setattr(health.cups, "available", lambda: True)
    monkeypatch.setattr(health.cups, "list_printers", lambda: [])
    checks = health.check_printers()
    assert len(checks) == 1
    assert checks[0].status == health.WARN


def test_paused_printer_is_repairable(monkeypatch):
    p = cups.Printer(name="MX870", state="idle", uri="usb://Canon/MX870", enabled=False)
    monkeypatch.setattr(health.cups, "available", lambda: True)
    monkeypatch.setattr(health.cups, "list_printers", lambda: [p])
    monkeypatch.setattr(health.cups, "list_devices", lambda: [cups.Device(uri="usb://Canon/MX870")])
    c = health.check_printers()[0]
    assert c.status == health.FAIL
    assert c.repairable
    assert c.key == "printer:MX870"


def test_printer_usb_unplugged_is_warning(monkeypatch):
    p = cups.Printer(name="MX870", state="idle", uri="usb://Canon/MX870", enabled=True)
    monkeypatch.setattr(health.cups, "available", lambda: True)
    monkeypatch.setattr(health.cups, "list_printers", lambda: [p])
    # A different USB device is present, so the printer's URI isn't detected.
    monkeypatch.setattr(health.cups, "list_devices", lambda: [cups.Device(uri="usb://HP/Other")])
    c = health.check_printers()[0]
    assert c.status == health.WARN
    assert not c.repairable  # can't fix a physically unplugged printer


def test_healthy_printer_is_ok(monkeypatch):
    p = cups.Printer(name="MX870", state="idle", uri="usb://Canon/MX870?serial=1A", enabled=True)
    monkeypatch.setattr(health.cups, "available", lambda: True)
    monkeypatch.setattr(health.cups, "list_printers", lambda: [p])
    monkeypatch.setattr(health.cups, "list_devices", lambda: [cups.Device(uri="usb://Canon/MX870")])
    assert health.check_printers()[0].status == health.OK


def test_scanner_warns_when_none_detected(monkeypatch):
    monkeypatch.setattr(health.scan, "available", lambda: True)
    monkeypatch.setattr(health.scan, "list_devices", lambda: [])
    assert health.check_scanner().status == health.WARN


def test_sharing_off_is_repairable(monkeypatch):
    monkeypatch.setattr(health.cups, "available", lambda: True)
    monkeypatch.setattr(health.cups, "list_printers", lambda: [cups.Printer("MX870", "idle")])
    monkeypatch.setattr(health.cups, "sharing_enabled", lambda: False)
    c = health.check_sharing()
    assert c.status == health.WARN
    assert c.repairable


def test_overall_is_worst_status():
    report = health.HealthReport(
        [
            health.Check("a", "A", health.OK),
            health.Check("b", "B", health.SKIP),
            health.Check("c", "C", health.WARN),
        ]
    )
    assert report.overall == health.WARN
    report.checks.append(health.Check("d", "D", health.FAIL))
    assert report.overall == health.FAIL


def test_run_checks_degrades_gracefully_without_tools(monkeypatch):
    # No CUPS, no SANE, no systemctl → nothing crashes, everything skips/reports.
    monkeypatch.setattr(health.cups, "available", lambda: False)
    monkeypatch.setattr(health.scan, "available", lambda: False)
    monkeypatch.setattr(
        health.system,
        "service_state",
        lambda name: _svc(name, active=False, enabled=False, status="unknown"),
    )
    monkeypatch.setattr(health.system, "_primary_ip", lambda: "10.0.0.5")
    report = health.run_checks()
    assert report.checks
    assert report.overall in (health.OK, health.WARN, health.FAIL)
