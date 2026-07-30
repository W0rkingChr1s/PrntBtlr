"""Shared pytest fixtures."""

import pytest

from app.services import scan, system


@pytest.fixture(autouse=True)
def _clear_probe_caches():
    """Reset the short-lived probe caches around every test.

    Service states and ``scanimage -L`` results are memoized process-wide, so
    without this a value cached by one test (often via mocked shell-outs) could
    leak into the next and make outcomes depend on run order.
    """
    system._service_cache.invalidate()
    scan._devices_cache.invalidate()
    yield
    system._service_cache.invalidate()
    scan._devices_cache.invalidate()
