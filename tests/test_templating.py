"""Tests for the shared redirect/flash helper."""

from app.templating import redirect


def test_redirect_plain_path():
    r = redirect("/printers")
    assert r.status_code == 303
    assert r.headers["location"] == "/printers"


def test_redirect_carries_flash_in_query():
    r = redirect("/printers", "Printer 'MX870' created.")
    location = r.headers["location"]
    assert location.startswith("/printers?")
    assert "msg=" in location and "level=success" in location


def test_redirect_keeps_query_before_fragment():
    # Regression: the flash query used to be appended after the #fragment, so
    # the browser treated it as part of the fragment and the flash never showed.
    r = redirect("/system#health", "Self-repair done.", "success")
    location = r.headers["location"]
    query, _, fragment = location.partition("#")
    assert query.startswith("/system?")
    assert "msg=" in query
    assert fragment == "health"
