"""Tests for the shared owner-only JSON state-file writer."""

import json

from app.config import settings
from app.services import statefile, webhooks


def _mode(path) -> int:
    return path.stat().st_mode & 0o777


def test_save_json_writes_atomically_with_owner_only_perms(tmp_path):
    path = tmp_path / "state.json"
    statefile.save_json(path, {"hello": "world"})
    assert json.loads(path.read_text()) == {"hello": "world"}
    assert _mode(path) == 0o600
    assert not path.with_suffix(".tmp").exists()  # temp file renamed away


def test_save_json_tightens_existing_world_readable_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{}")
    path.chmod(0o644)  # e.g. written by an older version
    statefile.save_json(path, {"n": 1})
    assert _mode(path) == 0o600


def test_webhook_state_is_owner_only(tmp_path, monkeypatch):
    # The webhook state carries HMAC signing secrets — it must never be
    # world-readable on a multi-user box.
    state = tmp_path / "webhooks.json"
    monkeypatch.setattr(settings, "webhook_state_file", state)
    ok, _ = webhooks.add_webhook("http://192.168.1.50/hook", ["scan.completed"], secret="s3cret")
    assert ok
    assert _mode(state) == 0o600
