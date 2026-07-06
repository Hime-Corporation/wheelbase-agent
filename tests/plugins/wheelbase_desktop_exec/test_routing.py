"""Routing / fail-closed / fallback for the cloud-exec middleware (spec §5.1)."""
from __future__ import annotations

import importlib
import json
import queue

import pytest

MOD = "plugins.wheelbase-desktop-exec"
transport_mod = importlib.import_module("plugins.wheelbase-desktop-exec.transport")


@pytest.fixture()
def plug(monkeypatch):
    # Hyphenated package name → import via importlib.
    module = importlib.import_module("plugins")  # ensure namespace
    return importlib.import_module("plugins.wheelbase-desktop-exec")


class FakeTransport(transport_mod.ExecTransport):
    """Scripts a successful exec frame sequence (mirrors test_relay_wiring.py)."""

    def __init__(self, frames_by_type=None, connected=True):
        self.sent = []
        self._connected = connected
        self._frames = frames_by_type or {}
        self._q = {}

    def send(self, frame):
        if not self._connected:
            raise transport_mod.PreDispatchError("no relay")
        self.sent.append(dict(frame))
        rid = frame["request_id"]
        q = self._q.setdefault(rid, queue.Queue())
        for f in self._frames.get(frame["type"], []):
            g = dict(f)
            g["request_id"] = rid
            q.put(g)

    def recv(self, request_id, timeout=None):
        return self._q[request_id].get(timeout=timeout or 5)

    def close(self):
        pass


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


@pytest.mark.parametrize("tool", ["patch", "execute_code", "search_files"])
def test_unmapped_tools_route_to_cloud_not_local(plug, monkeypatch, tool):
    # patch/execute_code/search_files do NOT map cleanly onto the relay frames;
    # they must fall through to next_call (cloud Daytona), never take the local
    # safety+relay path — even when a desktop relay url IS present.
    from wheelbase_sdk import runtime
    runtime.set_task_identity(
        "t-desk", {"user_id": "u", "shell_relay_url": "wss://relay", "workspace_root": "/work"}
    )
    # If either the local safety seam or the relay transport is touched, fail.
    monkeypatch.setattr(plug, "_safety_block",
                        lambda *a, **k: pytest.fail("local safety must not run"))
    monkeypatch.setattr(plug, "_make_transport",
                        lambda *a, **k: pytest.fail("local relay must not run"))
    nc, calls = _next_call_spy()
    out = plug.route_or_passthrough(
        tool_name=tool, args={"code": "print(1)"}, next_call=nc, task_id="t-desk"
    )
    assert out == "SANDBOX_RESULT"
    assert calls["n"] == 1


def test_routed_local_set_is_exactly_the_cleanly_mapped_tools(plug):
    # The routed-local set is the four tools that map cleanly onto the sidecar
    # primitives; the three unmapped tools are excluded (route to cloud).
    assert set(plug.ROUTED_TOOLS) == {"terminal", "process", "read_file", "write_file"}
    for excluded in ("patch", "execute_code", "search_files"):
        assert excluded not in plug.ROUTED_TOOLS


def test_guard_exception_fails_closed_to_deny(plug, monkeypatch):
    # If _safety_block RAISES (rather than returning a block dict), we must fail
    # CLOSED with a tool-error deny — not propagate (which would let the
    # framework auto-run the tool on cloud Daytona) and not relay locally.
    from wheelbase_sdk import runtime
    runtime.set_task_identity(
        "t-desk", {"user_id": "u", "shell_relay_url": "wss://relay", "workspace_root": "/work"}
    )

    def _boom(*a, **k):
        raise RuntimeError("guard blew up")

    monkeypatch.setattr(plug, "_safety_block", _boom)
    monkeypatch.setattr(plug, "_make_transport",
                        lambda *a, **k: pytest.fail("must not relay after guard error"))
    nc, calls = _next_call_spy()
    out = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "ls"}, next_call=nc, task_id="t-desk"
    )
    assert calls["n"] == 0                     # did NOT fall through to cloud
    parsed = json.loads(out)
    assert parsed["status"] == "error"         # explicit DENY
    assert parsed.get("exit_code", 1) != 0


def test_routed_with_relay_url_reaches_safety_seam(plug, monkeypatch):
    # With a relay_url present, routing must take the LOCAL path: run the
    # safety chain and relay via _make_transport — never fall back to the
    # cloud next_call.
    from wheelbase_sdk import runtime
    runtime.set_task_identity(
        "t-desk", {"user_id": "u", "shell_relay_url": "wss://relay", "workspace_root": "/work"}
    )
    nc, calls = _next_call_spy()

    safety_calls = {"n": 0}

    def _safety_ok(*args, **kwargs):
        safety_calls["n"] += 1
        return None

    monkeypatch.setattr(plug, "_safety_block", _safety_ok)

    ft = FakeTransport(frames_by_type={"exec": [
        {"type": "chunk", "stream": "stdout", "data": "ok\n"},
        {"type": "exit", "exit_code": 0},
    ]})
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)

    out = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "ls"}, next_call=nc, task_id="t-desk"
    )

    # Safety seam was invoked and the relay transport carried the command —
    # the cloud fallback (next_call) must NOT have run.
    assert safety_calls["n"] == 1
    assert ft.sent and ft.sent[0]["type"] == "exec"
    assert calls["n"] == 0
    assert "ok" in json.loads(out)["output"]
