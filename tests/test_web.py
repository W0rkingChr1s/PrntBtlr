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


def test_unknown_scan_download_redirects(client):
    r = client.get("/scans/file/nope.pdf", follow_redirects=False)
    assert r.status_code == 303
