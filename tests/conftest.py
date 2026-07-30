"""Shared pytest fixtures."""

import pytest

from app import auth
from app.services import scan, system


@pytest.fixture(autouse=True)
def _clear_probe_caches():
    """Reset process-wide state around every test.

    Service states and ``scanimage -L`` results are memoized process-wide, and
    the login throttle counts failures across requests — without this a value
    cached (or a failure recorded) by one test could leak into the next and
    make outcomes depend on run order.
    """
    system._service_cache.invalidate()
    scan._devices_cache.invalidate()
    auth.throttle.reset()
    yield
    system._service_cache.invalidate()
    scan._devices_cache.invalidate()
    auth.throttle.reset()
