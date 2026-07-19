"""Smoke tests: every page renders 200 even with no printer/scanner present."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    [
        "/healthz",
        "/",
        "/printers",
        "/printers/add",
        "/scans",
        "/system",
        "/partials/services",
        "/partials/jobs",
        "/system/health/partial",
    ],
)
def test_pages_render(client, path):
    assert client.get(path).status_code == 200


def test_printers_page_flags_same_device_duplicates(client, monkeypatch):
    from app.routes import printers as printers_route
    from app.services import cups

    status = cups.CupsStatus(
        available=True,
        printers=[
            cups.Printer(
                "MX870", "idle", uri="usb://Canon/MX870%20series%20FAX?serial=10C5A0&interface=3"
            ),
            cups.Printer(
                "MX870-series", "idle", uri="usb://Canon/MX870%20series?serial=10C5A0&interface=1"
            ),
        ],
    )
    monkeypatch.setattr(printers_route.cups, "status", lambda: status)

    body = client.get("/printers").text
    assert "same device" in body  # duplicate badge/note rendered
    assert "fax" in body  # fax interface labelled
    assert "10C5A0" in body  # shared serial surfaced


def test_healthz_reports_health(client):
    body = client.get("/healthz").json()
    assert "health" in body
    assert body["health"]["overall"] in ("ok", "warn", "fail")
    assert isinstance(body["health"]["checks"], dict)
    for check in body["health"]["checks"].values():
        assert check["status"] in ("ok", "warn", "fail", "skip")
        assert check["value"] in (0, 1)


def test_self_repair_runs(client):
    r = client.post("/system/health/repair", follow_redirects=False)
    assert r.status_code == 303
    assert "/system#health" in r.headers["location"]


def test_healthz_payload(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["app"] == "PrntBtlr"


def test_healthz_reports_services(client):
    from app.config import settings

    body = client.get("/healthz").json()
    assert set(body["services"]) == set(settings.services)
    for state in body["services"].values():
        assert state["value"] in (0, 1)
        assert state["value"] == int(state["active"])
        assert isinstance(state["status"], str)
    assert body["services_total"] == len(settings.services)
    assert 0 <= body["services_active"] <= body["services_total"]


def test_healthz_prtg_format(client):
    # PRTG's HTTP Data Advanced sensor rejects anything but this shape (PE231).
    body = client.get("/healthz?format=prtg").json()
    assert "prtg" in body
    results = body["prtg"]["result"]
    assert isinstance(results, list) and results
    channels = {r["channel"] for r in results}
    assert {"Overall health", "Services active"} <= channels
    for r in results:
        assert isinstance(r["channel"], str)
        assert r["value"] in (0, 1, 2) or isinstance(r["value"], int)
        assert r["limitmode"] == 1


def test_healthz_rejects_unknown_format(client):
    assert client.get("/healthz?format=xml").status_code == 422


def test_prtg_services_active_allows_idle_scan_button():
    # The scan-button pair shares one USB scanner, so one unit is idle by
    # design. A fully healthy host has 4/5 active, and the "Services active"
    # channel must not flag that as an error (regression: it used to require
    # all units up and went red on every install).
    from app.main import _prtg_payload
    from app.services import health, system

    svc = [
        system.ServiceState("cups", True, True, "active"),
        system.ServiceState("scanbd", False, False, "inactive"),
        system.ServiceState("prntbtlr-scan-listen", True, True, "active"),
        system.ServiceState("smbd", True, True, "active"),
        system.ServiceState("avahi-daemon", True, True, "active"),
    ]
    report = health.HealthReport([health.Check("scanbutton", "Scan button", health.OK)])
    ch = next(
        r for r in _prtg_payload(svc, report)["prtg"]["result"] if r["channel"] == "Services active"
    )
    assert ch["value"] == 4
    assert ch["value"] >= ch["limitminerror"]  # green, not a false error

    # A genuinely-down required service still trips the error limit.
    svc[0] = system.ServiceState("cups", False, False, "inactive")
    ch = next(
        r for r in _prtg_payload(svc, report)["prtg"]["result"] if r["channel"] == "Services active"
    )
    assert ch["value"] < ch["limitminerror"]


def test_unknown_scan_download_redirects(client):
    r = client.get("/scans/file/nope.pdf", follow_redirects=False)
    assert r.status_code == 303
