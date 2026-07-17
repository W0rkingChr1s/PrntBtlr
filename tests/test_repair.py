"""Tests for the self-repair engine — every system call is mocked."""

from app.services import health, repair, shell, system


def _ok():
    return shell.Result(ok=True, returncode=0, stdout="", stderr="")


def _svc(name, active=True, enabled=True, status=None):
    return system.ServiceState(
        name=name,
        active=active,
        enabled=enabled,
        status=status or ("active" if active else "inactive"),
    )


def test_repair_service_restarts_and_enables(monkeypatch):
    calls = []
    monkeypatch.setattr(
        repair.system, "service_state", lambda n: _svc(n, active=False, enabled=False)
    )
    monkeypatch.setattr(
        repair.system, "restart_service", lambda n: calls.append(("restart", n)) or _ok()
    )
    monkeypatch.setattr(
        repair.system, "enable_service", lambda n: calls.append(("enable", n)) or _ok()
    )
    actions = repair.repair_service("smbd")
    assert ("restart", "smbd") in calls
    assert ("enable", "smbd") in calls
    assert len(actions) == 2
    assert all(a.ok for a in actions)


def test_repair_service_noop_when_healthy(monkeypatch):
    monkeypatch.setattr(
        repair.system, "service_state", lambda n: _svc(n, active=True, enabled=True)
    )
    assert repair.repair_service("cups") == []


def test_repair_service_skips_uninstalled(monkeypatch):
    monkeypatch.setattr(
        repair.system, "service_state", lambda n: _svc(n, active=False, status="not-installed")
    )
    assert repair.repair_service("scanbd") == []


def test_repair_printer_resumes_and_sets_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(
        repair.cups, "set_enabled", lambda n, on: calls.append(("enable", n, on)) or _ok()
    )
    monkeypatch.setattr(
        repair.cups, "set_error_policy", lambda n, p: calls.append(("policy", n, p)) or _ok()
    )
    actions = repair.repair_printer("MX870")
    assert ("enable", "MX870", True) in calls
    assert ("policy", "MX870", "retry-job") in calls
    assert actions[0].ok


def test_repair_storage_creates_missing_dir(monkeypatch, tmp_path):
    target = tmp_path / "scans"
    monkeypatch.setattr(repair.settings, "scan_dir", target)
    actions = repair.repair_storage()
    assert target.exists()
    assert actions and actions[0].ok


def test_repair_storage_noop_when_present(monkeypatch, tmp_path):
    monkeypatch.setattr(repair.settings, "scan_dir", tmp_path)
    assert repair.repair_storage() == []


def test_run_only_dispatches_repairable(monkeypatch):
    report = health.HealthReport(
        [
            health.Check("network", "Network", health.FAIL),  # not repairable
            health.Check("cups", "CUPS", health.FAIL, fix_hint="restart"),  # repairable
            health.Check("scanner", "Scanner", health.OK),
        ]
    )
    dispatched = []

    def fake_cups():
        dispatched.append("cups")
        return [repair.RepairAction("cups", "Restart CUPS", True, "ok")]

    monkeypatch.setattr(repair, "repair_cups", fake_cups)
    monkeypatch.setattr(repair.health, "run_checks", lambda: report)
    actions, after = repair.run(report)
    assert dispatched == ["cups"]  # network (not repairable) skipped
    assert len(actions) == 1
    assert after is not None


def test_run_with_nothing_to_fix_returns_same_report(monkeypatch):
    report = health.HealthReport([health.Check("a", "A", health.OK)])
    actions, after = repair.run(report)
    assert actions == []
    assert after is report


def test_dispatch_routes_by_key(monkeypatch):
    seen = {}
    monkeypatch.setattr(repair, "repair_service", lambda n: seen.setdefault("service", n) or [])
    monkeypatch.setattr(repair, "repair_printer", lambda n: seen.setdefault("printer", n) or [])
    repair._dispatch(health.Check("service:smbd", "", health.FAIL, fix_hint="x"))
    repair._dispatch(health.Check("printer:MX870", "", health.FAIL, fix_hint="x"))
    assert seen == {"service": "smbd", "printer": "MX870"}
