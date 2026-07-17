"""Updater: version ordering, channel selection, prefs persistence and routes."""

import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import updater


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "updater.json"
    monkeypatch.setattr(settings, "update_state_file", path)
    return path


def _release(tag, prerelease=False, draft=False, name="", body=""):
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": draft,
        "name": name or tag,
        "body": body,
        "html_url": f"https://example.test/releases/{tag}",
        "published_at": "2026-07-17T00:00:00Z",
    }


# --------------------------------------------------------------------------- #
# Version parsing & ordering
# --------------------------------------------------------------------------- #
def test_parse_version_accepts_tags_and_bare_versions():
    assert updater.parse_version("v1.2.3") == updater.parse_version("1.2.3")
    assert updater.parse_version("not-a-version") is None
    assert updater.parse_version("v1.2") is None


def test_beta_sorts_below_its_stable_release():
    beta = updater.parse_version("v0.2.0-beta.4")
    stable = updater.parse_version("v0.2.0")
    assert beta < stable
    assert updater.parse_version("v0.2.0-beta.2") > updater.parse_version("v0.2.0-beta.1")
    assert updater.parse_version("v0.2.0-beta.1") > updater.parse_version("v0.1.9")


# --------------------------------------------------------------------------- #
# Release selection per channel
# --------------------------------------------------------------------------- #
def test_stable_channel_ignores_prereleases():
    releases = [_release("v0.3.0-beta.1", prerelease=True), _release("v0.2.0")]
    picked = updater.pick_latest(releases, "stable")
    assert picked.tag == "v0.2.0"
    assert picked.prerelease is False


def test_beta_channel_sees_betas_and_newer_stables():
    releases = [_release("v0.2.0-beta.3", prerelease=True), _release("v0.2.0")]
    assert updater.pick_latest(releases, "beta").tag == "v0.2.0"
    releases.append(_release("v0.3.0-beta.1", prerelease=True))
    assert updater.pick_latest(releases, "beta").tag == "v0.3.0-beta.1"


def test_failed_and_draft_releases_are_skipped():
    releases = [
        _release("v0.3.0-beta.2", prerelease=True, name="v0.3.0-beta.2 [FAILED]"),
        _release("v0.3.0-beta.3", prerelease=True, body="broken, see #12 [failed]"),
        _release("v0.4.0", draft=True),
        _release("v0.3.0-beta.1", prerelease=True),
    ]
    assert updater.pick_latest(releases, "beta").tag == "v0.3.0-beta.1"
    assert updater.pick_latest(releases, "stable") is None


# --------------------------------------------------------------------------- #
# Preferences & state
# --------------------------------------------------------------------------- #
def test_prefs_roundtrip(state_file):
    updater.save_prefs(channel="beta", auto_update=True)
    st = updater.status()
    assert st.channel == "beta"
    assert st.auto_update is True
    assert json.loads(state_file.read_text())["channel"] == "beta"


def test_channel_switch_drops_cached_candidate(state_file):
    updater.save_prefs(channel="beta", auto_update=False)
    state = json.loads(state_file.read_text())
    state["available"] = {"tag": "v9.9.9-beta.1"}
    state_file.write_text(json.dumps(state))
    updater.save_prefs(channel="stable", auto_update=False)
    assert updater.status().available is None


def test_save_prefs_rejects_unknown_channel(state_file):
    with pytest.raises(ValueError):
        updater.save_prefs(channel="nightly", auto_update=False)


# --------------------------------------------------------------------------- #
# check_for_update
# --------------------------------------------------------------------------- #
def test_check_records_newer_release(state_file, monkeypatch):
    monkeypatch.setattr(updater, "fetch_releases", lambda: [_release("v9.9.9")])
    st = updater.check_for_update()
    assert st.available["tag"] == "v9.9.9"
    assert st.last_error is None
    assert updater.notice()["tag"] == "v9.9.9"


def test_check_ignores_current_and_older_releases(state_file, monkeypatch):
    monkeypatch.setattr(updater, "fetch_releases", lambda: [_release("v" + updater.__version__)])
    result = updater.check_for_update()
    assert result.available is None
    assert updater.notice() is None


def test_check_surfaces_fetch_errors(state_file, monkeypatch):
    def boom():
        raise OSError("network down")

    monkeypatch.setattr(updater, "fetch_releases", boom)
    st = updater.check_for_update()
    assert st.available is None
    assert "network down" in st.last_error


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #
def test_start_update_rejects_bad_tags():
    res = updater.start_update("v1.0.0; rm -rf /")
    assert not res.ok
    assert "invalid release tag" in res.output


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def test_system_page_shows_updates_card(client, state_file):
    body = client.get("/system").text
    assert 'id="updates"' in body
    assert "Beta channel" in body
    assert "Install updates automatically" in body


def test_settings_post_persists_checkboxes(client, state_file):
    r = client.post(
        "/system/updates/settings",
        data={"beta": "true", "auto": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    st = updater.status()
    assert st.channel == "beta"
    assert st.auto_update is True

    # Unticked checkboxes are simply absent from the form.
    client.post("/system/updates/settings", data={}, follow_redirects=False)
    st = updater.status()
    assert st.channel == "stable"
    assert st.auto_update is False


def test_check_route_reports_up_to_date(client, state_file, monkeypatch):
    monkeypatch.setattr(updater, "fetch_releases", lambda: [])
    r = client.post("/system/updates/check", follow_redirects=False)
    assert r.status_code == 303
    assert "up%20to%20date" in r.headers["location"].replace("+", "%20")


def test_apply_route_rejects_unknown_tag(client, state_file):
    r = client.post("/system/updates/apply", data={"tag": "v9.9.9"}, follow_redirects=False)
    assert r.status_code == 303
    assert "level=error" in r.headers["location"]
