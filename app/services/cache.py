"""A tiny thread-safe TTL cache for expensive, rarely-changing shell probes.

Some discovery shell-outs are both slow and stable: ``scanimage -L`` waits for
the USB bus to settle (seconds), yet a scanner rarely appears or vanishes
between two page refreshes. Re-running them on every request — and on every
background poll — is the bulk of the remaining load time once the calls already
run concurrently.

This caches a producer's result for a short window, keyed so callers with
different arguments don't collide. The value is computed *outside* the lock, so
concurrent lookups for different keys (e.g. one ``service_state`` per service,
fanned out by :func:`shell.gather`) never serialize on the cache — the lock only
guards the small dict. The trade-off is a possible duplicate computation when
two callers miss the same key at once, which is cheaper than holding every
caller behind one slow shell-out.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable
from typing import TypeVar

T = TypeVar("T")


class TTLCache:
    """Memoize zero-argument producers under a hashable key for *ttl* seconds."""

    def __init__(self, ttl: float):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._store: dict[Hashable, tuple[float, object]] = {}

    def get_or_call(self, key: Hashable, producer: Callable[[], T]) -> T:
        """Return the cached value for *key*, or compute it with *producer*.

        A ``ttl`` of zero (or less) disables caching entirely, so callers can
        turn it off via configuration without a code path of their own.
        """
        if self.ttl <= 0:
            return producer()
        now = time.monotonic()
        with self._lock:
            hit = self._store.get(key)
            if hit is not None and now - hit[0] < self.ttl:
                return hit[1]  # type: ignore[return-value]
        value = producer()
        with self._lock:
            self._store[key] = (time.monotonic(), value)
        return value

    def invalidate(self, key: Hashable | None = None) -> None:
        """Drop a single *key* (or the whole cache when *key* is ``None``).

        Called right after a mutation — restarting a service, say — so the next
        read reflects the new state immediately instead of waiting out the TTL.
        """
        with self._lock:
            if key is None:
                self._store.clear()
            else:
                self._store.pop(key, None)
