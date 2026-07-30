"""Tests for outbound webhooks — no real network (the HTTP POST is stubbed)."""

import hashlib
import hmac
import json

import pytest

from app.config import settings
from app.services import webhooks


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Point the state file at a temp path so tests never touch /etc."""
    monkeypatch.setattr(settings, "webhook_state_file", tmp_path / "webhooks.json")


class _Inline:
    """Stand-in for the delivery pool that runs submissions synchronously."""

    def submit(self, fn, *args):
        fn(*args)


def test_valid_url():
    assert webhooks.valid_url("http://example.com/hook")
    assert webhooks.valid_url("https://n8n.local/webhook/abc")
    assert not webhooks.valid_url("ftp://example.com")
    assert not webhooks.valid_url("example.com/hook")
    assert not webhooks.valid_url("")


def test_valid_events_filters_and_orders():
    # Unknown keys dropped; result follows the catalogue order, not input order.
    got = webhooks.valid_events(["printer.added", "scan.completed", "bogus"])
    assert got == ["scan.completed", "printer.added"]


def test_has_subscriber():
    webhooks.add_webhook("https://a.example/hook", ["print.submitted"])
    assert webhooks.has_subscriber(webhooks.PRINT_EVENTS)
    assert not webhooks.has_subscriber(webhooks.HEALTH_EVENTS)
    # A disabled endpoint doesn't count.
    webhooks.set_enabled(webhooks.list_webhooks()[0].id, False)
    assert not webhooks.has_subscriber(webhooks.PRINT_EVENTS)


def test_wants_matching():
    h = webhooks.Webhook("id", "http://x", ["scan.completed"], enabled=True)
    assert h.wants("scan.completed")
    assert not h.wants("printer.added")
    h.enabled = False
    assert not h.wants("scan.completed")
    star = webhooks.Webhook("id2", "http://x", ["*"], enabled=True)
    assert star.wants("anything.at.all")


def test_add_list_and_delete_persists():
    ok, _ = webhooks.add_webhook("https://example.com/hook", ["scan.completed"], secret="s3cret")
    assert ok
    hooks = webhooks.list_webhooks()
    assert len(hooks) == 1
    assert hooks[0].url == "https://example.com/hook"
    assert hooks[0].events == ["scan.completed"]
    assert hooks[0].secret == "s3cret"

    assert webhooks.delete_webhook(hooks[0].id)
    assert webhooks.list_webhooks() == []
    assert not webhooks.delete_webhook("nonexistent")


def test_add_rejects_bad_url_and_empty_events():
    ok, msg = webhooks.add_webhook("not-a-url", ["scan.completed"])
    assert not ok and "URL" in msg
    ok, msg = webhooks.add_webhook("https://example.com", [])
    assert not ok and "event" in msg.lower()


def test_set_enabled_toggles():
    webhooks.add_webhook("https://example.com/hook", ["scan.completed"])
    hook_id = webhooks.list_webhooks()[0].id
    assert webhooks.set_enabled(hook_id, False)
    assert webhooks.list_webhooks()[0].enabled is False
    assert webhooks.set_enabled(hook_id, True)
    assert webhooks.list_webhooks()[0].enabled is True


def test_deliver_signs_body_when_secret_set(monkeypatch):
    captured = {}

    def fake_post(url, body, headers, timeout):
        captured["url"] = url
        captured["body"] = body
        captured["headers"] = headers
        return True, "HTTP 200"

    monkeypatch.setattr(webhooks, "_post", fake_post)
    hook = webhooks.Webhook(
        "id", "https://example.com/hook", ["scan.completed"], secret="topsecret"
    )
    payload = webhooks._build_payload("scan.completed", {"file": "scan_1.pdf"})
    ok, _ = webhooks.deliver(hook, "scan.completed", payload)

    assert ok
    assert captured["headers"]["X-Prntbtlr-Event"] == "scan.completed"
    expected = "sha256=" + hmac.new(b"topsecret", captured["body"], hashlib.sha256).hexdigest()
    assert captured["headers"]["X-Prntbtlr-Signature"] == expected
    # Body is the JSON payload we passed.
    assert json.loads(captured["body"])["data"]["file"] == "scan_1.pdf"


def test_deliver_omits_signature_without_secret(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        webhooks, "_post", lambda u, b, h, t: captured.update(headers=h) or (True, "HTTP 200")
    )
    hook = webhooks.Webhook("id", "https://example.com/hook", ["scan.completed"])
    webhooks.deliver(hook, "scan.completed", webhooks._build_payload("scan.completed", {}))
    assert "X-Prntbtlr-Signature" not in captured["headers"]


def test_emit_dispatches_only_to_matching_enabled(monkeypatch):
    posts = []
    monkeypatch.setattr(webhooks, "_pool", _Inline())
    monkeypatch.setattr(webhooks, "_post", lambda u, b, h, t: posts.append(u) or (True, "HTTP 200"))

    webhooks.add_webhook("https://match.example/hook", ["scan.completed"])
    webhooks.add_webhook("https://other.example/hook", ["printer.added"])
    webhooks.add_webhook("https://off.example/hook", ["scan.completed"])
    off_id = webhooks.list_webhooks()[2].id
    webhooks.set_enabled(off_id, False)

    n = webhooks.emit("scan.completed", {"file": "scan_1.pdf"})
    assert n == 1
    assert posts == ["https://match.example/hook"]


def test_emit_with_no_subscribers_is_noop(monkeypatch):
    monkeypatch.setattr(webhooks, "_pool", _Inline())
    monkeypatch.setattr(
        webhooks, "_post", lambda *a: (_ for _ in ()).throw(AssertionError("posted"))
    )
    assert webhooks.emit("scan.completed", {}) == 0


def test_send_test_reports_result(monkeypatch):
    monkeypatch.setattr(webhooks, "_post", lambda u, b, h, t: (True, "HTTP 204"))
    webhooks.add_webhook("https://example.com/hook", ["scan.completed"])
    hook_id = webhooks.list_webhooks()[0].id
    ok, msg = webhooks.send_test(hook_id)
    assert ok and "204" in msg

    monkeypatch.setattr(webhooks, "_post", lambda u, b, h, t: (False, "Connection refused"))
    ok, msg = webhooks.send_test(hook_id)
    assert not ok and "Connection refused" in msg

    assert webhooks.send_test("nonexistent")[0] is False


def test_note_health_transitions(monkeypatch):
    fired = []
    monkeypatch.setattr(webhooks, "emit", lambda key, data=None: fired.append((key, data)))

    # First observation: baseline only, nothing fired.
    assert webhooks.note_health("ok", None) is None
    # No change: nothing.
    assert webhooks.note_health("ok", "ok") is None
    # ok -> warn is a degrade.
    assert webhooks.note_health("warn", "ok") == "health.degraded"
    # warn -> fail stays a degrade (escalation still notifies).
    assert webhooks.note_health("fail", "warn") == "health.degraded"
    # fail -> ok is a recovery.
    assert webhooks.note_health("ok", "fail") == "health.recovered"
    assert [k for k, _ in fired] == ["health.degraded", "health.degraded", "health.recovered"]
