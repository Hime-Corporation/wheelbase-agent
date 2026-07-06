"""Routing / fail-closed / fallback for the cloud-exec middleware (spec §5.1)."""
from __future__ import annotations

import importlib

import pytest

MOD = "plugins.wheelbase-desktop-exec"


@pytest.fixture()
def plug(monkeypatch):
    # Hyphenated package name → import via importlib.
    module = importlib.import_module("plugins")  # ensure namespace
    return importlib.import_module("plugins.wheelbase-desktop-exec")


@pytest.fixture(autouse=True)
def _clear_registry():
    from wheelbase_sdk import runtime
    yield
    runtime._current.set(None)
    with runtime._lock:
        runtime._by_task.clear()


def _next_call_spy():
    calls = {"n": 0, "args": None}

    def next_call(args):
        calls["n"] += 1
        calls["args"] = args
        return "SANDBOX_RESULT"

    return next_call, calls


def test_unrouted_tool_passes_through(plug):
    nc, calls = _next_call_spy()
    out = plug.route_or_passthrough(
        tool_name="web_search", args={"q": "x"}, next_call=nc, task_id="t1"
    )
    assert out == "SANDBOX_RESULT"
    assert calls["n"] == 1


def test_empty_task_id_fails_closed_to_sandbox(plug):
    nc, calls = _next_call_spy()
    out = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "ls"}, next_call=nc, task_id=""
    )
    assert out == "SANDBOX_RESULT"
    assert calls["n"] == 1


def test_missing_identity_fails_closed(plug):
    nc, calls = _next_call_spy()
    out = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "ls"}, next_call=nc, task_id="unknown"
    )
    assert out == "SANDBOX_RESULT"
    assert calls["n"] == 1


def test_no_shell_relay_url_falls_back(plug):
    from wheelbase_sdk import runtime
    runtime.set_task_identity("t-mobile", {"user_id": "u", "shell_relay_url": ""})
    nc, calls = _next_call_spy()
    out = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "ls"}, next_call=nc, task_id="t-mobile"
    )
    assert out == "SANDBOX_RESULT"
    assert calls["n"] == 1


def test_routed_with_relay_url_reaches_safety_seam(plug, monkeypatch):
    # With a relay_url present, we must proceed to the safety chain — proven
    # by the stub seam raising. (Real behavior arrives in Task 5+.)
    from wheelbase_sdk import runtime
    runtime.set_task_identity("t-desk", {"user_id": "u", "shell_relay_url": "wss://relay"})
    nc, _ = _next_call_spy()
    with pytest.raises(NotImplementedError):
        plug.route_or_passthrough(
            tool_name="terminal", args={"command": "ls"}, next_call=nc, task_id="t-desk"
        )
