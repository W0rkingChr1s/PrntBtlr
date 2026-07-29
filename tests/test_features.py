"""Feature toggles: persistence, defaults, and route/nav gating."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import features, system


@pytest.fixture
def capture_services(monkeypatch):
    """Pretend every unit is installed and record start/stop/enable/disable calls."""
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        features.system,
        "service_state",
        lambda name: system.ServiceState(name, True, True, "active"),
    )
    for action in ("start_service", "stop_service", "enable_service", "disable_service"):
        monkeypatch.setattr(
            features.system,
            action,
            (
                lambda act: (
                    lambda name: calls.append((act, name)) or system.shell.Result(True, 0, "", "")
                )
            )(action),
        )
    return calls


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "feature_state_file", tmp_path / "features.json")
    return tmp_path / "features.json"


@pytest.fixture
def client():
    return TestClient(app)


def test_defaults_all_on_without_state(state_file):
    # A fresh box (no state file) has every function switched on.
    assert features.states() == {"printers": True, "scans": True, "updates": True}
    assert features.is_enabled("printers") is True
    assert features.is_enabled("scans") is True


def test_unknown_key_defaults_on(state_file):
    assert features.is_enabled("something-new") is True


def test_save_persists_selection(state_file):
    features.save({"printers"})  # only printers on
    assert features.is_enabled("printers") is True
    assert features.is_enabled("scans") is False
    assert features.is_enabled("updates") is False
    # A second load sees the same thing.
    assert features.states() == {"printers": True, "scans": False, "updates": False}


def test_all_features_carries_state(state_file):
    features.save({"scans"})
    by_key = {f.key: f for f in features.all_features()}
    assert by_key["scans"].enabled is True
    assert by_key["printers"].enabled is False
    assert by_key["printers"].label  # human label present


def test_disabled_scans_route_redirects_to_dashboard(state_file, client):
    features.save({"printers", "updates"})  # scans off
    r = client.get("/scans", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/")
    assert "switched" in r.headers["location"]  # spaces are +-encoded in the query


def test_disabled_printers_route_redirects(state_file, client):
    features.save({"scans", "updates"})  # printers off
    r = client.post("/printers/MX870/test", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/?")


def test_enabled_routes_still_serve(state_file, client):
    features.save({"printers", "scans", "updates"})
    assert client.get("/printers").status_code == 200
    assert client.get("/scans").status_code == 200


def test_nav_hides_disabled_function(state_file, client):
    features.save({"printers", "updates"})  # scans off
    body = client.get("/").text
    assert 'href="/printers"' in body
    assert 'href="/scans"' not in body


def test_system_page_saves_toggles(state_file, client):
    r = client.post("/system/features", data={"printers": "true"}, follow_redirects=False)
    assert r.status_code == 303
    assert features.is_enabled("printers") is True
    assert features.is_enabled("scans") is False


def test_services_for_mapping():
    assert features.services_for("printers") == ("cups",)
    assert "scanbd" in features.services_for("scans")
    assert features.services_for("updates") == ()
    assert features.services_for("nope") == ()


def test_apply_services_stops_units_when_switched_off(capture_services):
    before = {"printers": True, "scans": True, "updates": True}
    after = {"printers": False, "scans": True, "updates": True}
    notes = features.apply_services(before, after)
    assert ("disable_service", "cups") in capture_services
    assert ("stop_service", "cups") in capture_services
    assert any("stopped cups" in n for n in notes)
    # An unchanged function is left completely alone.
    assert all(name != "scanbd" for _, name in capture_services)


def test_apply_services_starts_units_when_switched_on(capture_services):
    before = {"printers": True, "scans": False, "updates": True}
    after = {"printers": True, "scans": True, "updates": True}
    features.apply_services(before, after)
    assert ("enable_service", "scanbd") in capture_services
    assert ("start_service", "scanbd") in capture_services
    assert ("start_service", "prntbtlr-scan-listen") in capture_services


def test_apply_services_skips_uninstalled_units(monkeypatch):
    monkeypatch.setattr(
        features.system,
        "service_state",
        lambda name: system.ServiceState(name, False, False, "not-installed"),
    )
    called = []
    monkeypatch.setattr(features.system, "stop_service", lambda n: called.append(n))
    notes = features.apply_services({"printers": True}, {"printers": False})
    assert called == []  # nothing to stop on a host without cups
    assert notes == []


def test_save_features_route_stops_service(state_file, client, capture_services):
    # Default is all-on; posting only "scans" turns Printers off → cups stopped.
    r = client.post(
        "/system/features", data={"scans": "true", "updates": "true"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert ("stop_service", "cups") in capture_services


def test_update_actions_blocked_when_updates_off(state_file, client):
    features.save({"printers", "scans"})  # updates off
    r = client.post("/system/updates/check", follow_redirects=False)
    assert r.status_code == 303
    assert "switched" in r.headers["location"]  # spaces are +-encoded in the query
