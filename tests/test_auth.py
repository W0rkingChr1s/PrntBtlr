"""Tests for password hashing, credential checks, and the login gate."""

import importlib

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import settings


def test_hash_verify_roundtrip():
    h = auth.hash_password("s3cret")
    assert h.startswith("pbkdf2_sha256$")
    assert auth.verify_password("s3cret", h)
    assert not auth.verify_password("wrong", h)
    # Malformed stored hashes must not raise, just fail.
    assert not auth.verify_password("s3cret", "garbage")
    assert not auth.verify_password("s3cret", "")


def test_check_credentials_plaintext(monkeypatch):
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password", "pw")
    monkeypatch.setattr(settings, "auth_password_hash", "")
    assert auth.check_credentials("admin", "pw")
    assert not auth.check_credentials("admin", "bad")
    assert not auth.check_credentials("root", "pw")
    assert not auth.check_credentials("admin", "")


def test_check_credentials_hashed(monkeypatch):
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password", "")
    monkeypatch.setattr(settings, "auth_password_hash", auth.hash_password("pw"))
    assert auth.check_credentials("admin", "pw")
    assert not auth.check_credentials("admin", "bad")


def test_auth_is_usable(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_password", "")
    monkeypatch.setattr(settings, "auth_password_hash", "")
    assert auth.auth_is_usable() is False
    monkeypatch.setattr(settings, "auth_password", "pw")
    assert auth.auth_is_usable() is True


@pytest.fixture
def auth_client(monkeypatch):
    """A TestClient backed by an app rebuilt with auth turned on."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password", "pw")
    monkeypatch.setattr(settings, "auth_password_hash", "")
    monkeypatch.setattr(settings, "session_secret", "unit-test-secret")
    import app.main as main

    importlib.reload(main)
    try:
        yield TestClient(main.app)
    finally:
        monkeypatch.undo()
        importlib.reload(main)


def test_protected_page_redirects_to_login(auth_client):
    r = auth_client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_healthz_stays_public(auth_client):
    assert auth_client.get("/healthz").status_code == 200


def test_login_flow(auth_client):
    assert auth_client.get("/login").status_code == 200

    bad = auth_client.post(
        "/login", data={"username": "admin", "password": "nope"}, follow_redirects=False
    )
    assert bad.status_code == 200  # re-rendered form with error

    good = auth_client.post(
        "/login", data={"username": "admin", "password": "pw"}, follow_redirects=False
    )
    assert good.status_code == 303
    # Session cookie now lets us in.
    assert auth_client.get("/", follow_redirects=False).status_code == 200

    auth_client.post("/logout", follow_redirects=False)
    assert auth_client.get("/", follow_redirects=False).status_code == 303


def test_login_open_redirect_blocked(auth_client):
    auth_client.post("/login", data={"username": "admin", "password": "pw"})
    r = auth_client.get("/login?next=https://evil.example", follow_redirects=False)
    # Already logged in → redirect, but never to an off-site target.
    assert r.headers["location"] in ("/", "/login")
