"""Tests for the out-of-process notify CLI used by button scans."""

import pytest

from app import notify
from app.config import settings
from app.services import webhooks


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "webhook_state_file", tmp_path / "webhooks.json")


def test_parse_event_and_pairs():
    event, data = notify._parse(["scan.completed", "file=scan_1.pdf", "mode=Color", "pages=2"])
    assert event == "scan.completed"
    assert data == {"file": "scan_1.pdf", "mode": "Color", "pages": "2"}


def test_parse_ignores_malformed_pairs():
    event, data = notify._parse(["scan.completed", "noequalsign", "=novalue", "k=v"])
    assert event == "scan.completed"
    assert data == {"k": "v"}


def test_parse_empty():
    assert notify._parse([]) == (None, {})


def test_emit_sync_delivers_and_counts(monkeypatch):
    posts = []
    monkeypatch.setattr(
        webhooks, "_post", lambda u, b, h, t: posts.append((u, b)) or (True, "HTTP 200")
    )
    webhooks.add_webhook("https://match.example/hook", ["scan.completed"])
    webhooks.add_webhook("https://other.example/hook", ["printer.added"])

    n = webhooks.emit_sync("scan.completed", {"file": "scan_1.pdf"})
    assert n == 1
    assert len(posts) == 1
    assert posts[0][0] == "https://match.example/hook"


def test_emit_sync_counts_only_successes(monkeypatch):
    webhooks.add_webhook("https://a.example/hook", ["scan.completed"])
    webhooks.add_webhook("https://b.example/hook", ["scan.completed"])
    # First delivery succeeds, second fails.
    calls = {"n": 0}

    def flaky(url, body, headers, timeout):
        calls["n"] += 1
        return (calls["n"] == 1, "HTTP 200" if calls["n"] == 1 else "boom")

    monkeypatch.setattr(webhooks, "_post", flaky)
    assert webhooks.emit_sync("scan.completed", {}) == 1


def test_main_delivers_scan_completed(monkeypatch):
    captured = {}

    def fake_post(url, body, headers, timeout):
        captured["url"] = url
        captured["body"] = body
        return True, "HTTP 200"

    monkeypatch.setattr(webhooks, "_post", fake_post)
    webhooks.add_webhook("https://match.example/hook", ["scan.completed"])

    rc = notify.main(["scan.completed", "file=scan_9.pdf", "source=button"])
    assert rc == 0
    assert captured["url"] == "https://match.example/hook"
    import json

    assert json.loads(captured["body"])["data"] == {"file": "scan_9.pdf", "source": "button"}


def test_main_no_event_is_usage_error():
    assert notify.main([]) == 2


def test_main_never_raises_on_delivery_error(monkeypatch):
    monkeypatch.setattr(
        webhooks, "emit_sync", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
    )
    # A webhook problem must not turn into a non-zero exit that fails the scan.
    assert notify.main(["scan.completed", "file=x.pdf"]) == 0
