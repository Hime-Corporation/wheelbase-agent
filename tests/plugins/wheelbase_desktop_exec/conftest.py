"""Shared fixtures for the wheelbase-desktop-exec plugin test suite.

The plugin now caches one relay transport per (task_id, relay_url) at module
scope (see plugins/wheelbase-desktop-exec/__init__.py's cache above
_make_transport). Several test files across this directory intentionally
reuse the SAME task_id/relay_url combo ("t-desk" / "wss://relay") across
many test functions — each one monkeypatches ``_make_transport`` to hand
back its OWN fresh FakeTransport and asserts against that instance. Without
clearing the cache between tests, the second test in a file would silently
get back the FIRST test's cached transport instead of its own (pytest runs
every test in a file in the same process/module unless marked
``no_isolate`` is opted out of — see pyproject.toml), so assertions against
"my" FakeTransport would see nothing sent to it.
"""
from __future__ import annotations

import importlib

import pytest

plug = importlib.import_module("plugins.wheelbase-desktop-exec")


@pytest.fixture(autouse=True)
def _clear_desktop_exec_transport_cache():
    yield
    with plug._transport_cache_lock:
        cached = list(plug._transport_cache.values())
        plug._transport_cache.clear()
    for transport in cached:
        try:
            transport.close()
        except Exception:
            pass
