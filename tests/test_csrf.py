"""Tests for the cross-origin (CSRF) check on state-changing requests."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

# Any POST route works — this one has no side effects for an unknown service.
TARGET = "/system/services/nonexistent/restart"


@pytest.fixture
def client():
    return TestClient(app)


def test_post_with_foreign_origin_rejected(client):
    r = client.post(TARGET, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_post_with_foreign_referer_rejected(client):
    r = client.post(TARGET, headers={"Referer": "http://evil.example/attack.html"})
    assert r.status_code == 403


def test_post_with_null_origin_rejected(client):
    # Sandboxed iframes send the literal "null" origin — a classic CSRF vehicle.
    r = client.post(TARGET, headers={"Origin": "null"})
    assert r.status_code == 403


def test_post_with_same_origin_allowed(client):
    # The browser's own forms send our host as the Origin.
    r = client.post(TARGET, headers={"Origin": "http://testserver"}, follow_redirects=False)
    assert r.status_code == 303  # passed the gate, handled by the route


def test_post_without_origin_or_referer_allowed(client):
    # Non-browser clients (curl, scripts, monitoring) send neither header; they
    # aren't riding a victim's browser, so they pass.
    r = client.post(TARGET, follow_redirects=False)
    assert r.status_code == 303


def test_get_never_blocked(client):
    r = client.get("/healthz", headers={"Origin": "http://evil.example"})
    assert r.status_code == 200
