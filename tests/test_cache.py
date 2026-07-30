"""Tests for the short-lived probe cache and the services/scanner it fronts."""

from app.services import cache, scan, system


def test_ttl_cache_memoizes_and_invalidates():
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return calls["n"]

    c = cache.TTLCache(ttl=60)
    assert c.get_or_call("k", producer) == 1
    # Second lookup inside the TTL returns the cached value, producer not re-run.
    assert c.get_or_call("k", producer) == 1
    assert calls["n"] == 1

    # A different key is computed independently.
    assert c.get_or_call("other", producer) == 2

    # Invalidation forces a recompute for that key on the next lookup.
    c.invalidate("k")
    assert c.get_or_call("k", producer) == 3


def test_ttl_zero_disables_caching():
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return calls["n"]

    c = cache.TTLCache(ttl=0)
    assert c.get_or_call("k", producer) == 1
    assert c.get_or_call("k", producer) == 2  # always live


def test_service_state_is_cached(monkeypatch):
    runs = []

    def fake_run(cmd, **kwargs):
        runs.append(cmd)
        verb = cmd[1]
        return system.shell.Result(True, 0, "active" if verb == "is-active" else "enabled", "")

    monkeypatch.setattr(system.shell, "which", lambda b: "/usr/bin/systemctl")
    monkeypatch.setattr(system.shell, "run", fake_run)

    first = system.service_state("cups")
    assert first.active and first.enabled
    n_after_first = len(runs)
    assert n_after_first == 2  # is-active + is-enabled

    # Repeated within the TTL: served from cache, no new shell-outs.
    system.service_state("cups")
    assert len(runs) == n_after_first


def test_service_action_busts_cache(monkeypatch):
    runs = []

    def fake_run(cmd, **kwargs):
        runs.append(cmd)
        verb = cmd[1]
        return system.shell.Result(True, 0, "active" if verb == "is-active" else "enabled", "")

    monkeypatch.setattr(system.shell, "which", lambda b: "/usr/bin/systemctl")
    monkeypatch.setattr(system.shell, "run", fake_run)

    system.service_state("cups")  # populate the cache
    system.restart_service("cups")  # a mutation must invalidate it
    runs.clear()
    system.service_state("cups")
    # Cache was busted, so the state is probed live again.
    assert [c[1] for c in runs] == ["is-active", "is-enabled"]


def test_list_devices_is_cached(monkeypatch):
    out = "device `pixma:MX870_1A2B3C' is a CANON Canon PIXMA MX870 multi-function peripheral\n"
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        return scan.shell.Result(True, 0, out, "")

    monkeypatch.setattr(scan.shell, "which", lambda b: "/usr/bin/scanimage")
    monkeypatch.setattr(scan.shell, "run", fake_run)

    assert scan.list_devices()[0].device == "pixma:MX870_1A2B3C"
    scan.list_devices()
    assert calls["n"] == 1  # second call served from cache
