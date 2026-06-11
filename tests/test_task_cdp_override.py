"""Tests for per-task CDP URL override (B3 — multi-user cloud gateway)."""

import importlib
import threading
import types

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_module():
    """Re-import browser_tool so registry state is isolated between tests."""
    import tools.browser_tool as bt
    # Clear per-task state between tests
    with bt._task_cdp_lock:
        bt._task_cdp_urls.clear()
    return bt


# ---------------------------------------------------------------------------
# Test 1: registered URL for task t1 is returned via _get_cdp_override
# ---------------------------------------------------------------------------


def test_registered_task_url_returned(monkeypatch):
    bt = _fresh_module()

    # Monkeypatch _resolve_cdp_override to identity so no HTTP lookup occurs
    monkeypatch.setattr(bt, "_resolve_cdp_override", lambda url: url)
    # Ensure env/config fallback is silent
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    bt.register_task_cdp_url("t1", "ws://relay-t1:9222")

    assert bt._get_cdp_override("t1") == "ws://relay-t1:9222"


# ---------------------------------------------------------------------------
# Test 2: unknown task t2 falls back to env path
# ---------------------------------------------------------------------------


def test_unknown_task_falls_back_to_env(monkeypatch):
    bt = _fresh_module()

    monkeypatch.setattr(bt, "_resolve_cdp_override", lambda url: url)
    monkeypatch.setenv("BROWSER_CDP_URL", "ws://global-cdp:9222")

    # t2 has no per-task registration
    assert bt._get_cdp_override("t2") == "ws://global-cdp:9222"


# ---------------------------------------------------------------------------
# Test 3: registering empty string unregisters a task
# ---------------------------------------------------------------------------


def test_empty_url_unregisters_task(monkeypatch):
    bt = _fresh_module()

    monkeypatch.setattr(bt, "_resolve_cdp_override", lambda url: url)
    monkeypatch.setenv("BROWSER_CDP_URL", "ws://global-cdp:9222")

    bt.register_task_cdp_url("t1", "ws://relay-t1:9222")
    # Confirm it's registered
    assert bt._get_cdp_override("t1") == "ws://relay-t1:9222"

    # Unregister
    bt.register_task_cdp_url("t1", "")
    # Now falls back to env
    assert bt._get_cdp_override("t1") == "ws://global-cdp:9222"


# ---------------------------------------------------------------------------
# Test 4: two tasks with different URLs resolve independently (no cross-bleed)
# ---------------------------------------------------------------------------


def test_two_tasks_independent(monkeypatch):
    bt = _fresh_module()

    monkeypatch.setattr(bt, "_resolve_cdp_override", lambda url: url)
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)

    bt.register_task_cdp_url("t1", "ws://relay-t1:9222")
    bt.register_task_cdp_url("t2", "ws://relay-t2:9222")

    assert bt._get_cdp_override("t1") == "ws://relay-t1:9222"
    assert bt._get_cdp_override("t2") == "ws://relay-t2:9222"
    # Unregistered task returns empty (no env fallback here)
    assert bt._get_cdp_override("t3") == ""
