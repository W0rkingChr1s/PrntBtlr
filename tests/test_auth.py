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


def test_check_credentials_non_ascii_does_not_raise(monkeypatch):
    # compare_digest rejects non-ASCII str with a TypeError; a stray umlaut in
    # the login form must fail the check, not crash the request.
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password", "pw")
    monkeypatch.setattr(settings, "auth_password_hash", "")
    assert not auth.check_credentials("ädmin", "pw")
    assert not auth.check_credentials("admin", "pässwort")
    monkeypatch.setattr(settings, "auth_username", "ädmin")
    monkeypatch.setattr(settings, "auth_password", "pässwort")
    assert auth.check_credentials("ädmin", "pässwort")


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
    for target in ("https://evil.example", "//evil.example", "/\\evil.example"):
        r = auth_client.get(f"/login?next={target}", follow_redirects=False)
        # Already logged in → redirect, but never to an off-site target.
        assert r.headers["location"] in ("/", "/login")


def test_login_redirect_preserves_target_query(auth_client):
    # The next value must be URL-encoded, or the target's own ?query splits
    # into separate /login parameters and the deep link is truncated.
    r = auth_client.get("/scans?msg=hello&level=info", follow_redirects=False)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/login?next=")
    from urllib.parse import parse_qs, urlparse

    params = parse_qs(urlparse(location).query)
    assert params["next"] == ["/scans?msg=hello&level=info"]
