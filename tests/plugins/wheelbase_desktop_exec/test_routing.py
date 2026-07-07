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


def test_routed_local_set_is_all_seven_tools(plug):
    # All 7 built-in tools now route to the desktop when a relay url is
    # present: terminal/process/read_file/write_file (unchanged) plus
    # patch/search_files (via _relay_file_ops/ShellFileOperations) and
    # execute_code (via _relay_execute_code/env-cache injection).
    assert set(plug.ROUTED_TOOLS) == {
        "terminal", "process", "read_file", "write_file",
        "patch", "search_files", "execute_code",
    }


@pytest.mark.parametrize("tool", ["patch", "search_files"])
def test_patch_and_search_files_reach_the_file_ops_safety_seam(plug, monkeypatch, tool):
    # patch/search_files now take the SAME safety+relay path as the other
    # file tools (unlike execute_code, which has its own dedicated branch) —
    # _safety_block must run and the relay transport must be built. Full
    # happy-path relay behavior (real ShellFileOperations round-trip) is
    # covered in test_file_ops_relay.py; here we only prove the DISPATCH
    # reaches _relay_file_ops instead of falling back to cloud.
    from wheelbase_sdk import runtime
    runtime.set_task_identity(
        "t-desk", {"user_id": "u", "shell_relay_url": "wss://relay", "workspace_root": "/work"}
    )
    safety_calls = {"n": 0}

    def _safety_ok(*a, **k):
        safety_calls["n"] += 1
        return None

    monkeypatch.setattr(plug, "_safety_block", _safety_ok)

    transport_calls = {"n": 0}

    class _BrokenTransport(transport_mod.ExecTransport):
        def send(self, frame):
            raise RuntimeError("post-dispatch: transport not wired in this unit test")

        def recv(self, request_id, timeout=None):
            raise RuntimeError("post-dispatch: transport not wired in this unit test")

    def _fake_transport(url, ident):
        transport_calls["n"] += 1
        return _BrokenTransport()

    monkeypatch.setattr(plug, "_make_transport", _fake_transport)
    nc, calls = _next_call_spy()
    args = ({"mode": "replace", "path": "/x", "old_string": "a", "new_string": "b"}
            if tool == "patch" else {"pattern": "x"})
    out = plug.route_or_passthrough(
        tool_name=tool, args=args, next_call=nc, task_id="t-desk"
    )
    assert safety_calls["n"] == 1        # safety seam DID run
    assert calls["n"] == 0                # never fell back to cloud (M4: post-dispatch failure)
    assert transport_calls["n"] == 1      # DID attempt to build the relay transport
    parsed = json.loads(out)
    assert parsed.get("status") == "error" or parsed.get("error")


def test_execute_code_does_not_reach_the_generic_safety_seam(plug, monkeypatch):
    # execute_code is intercepted BEFORE the generic _safety_block call (its
    # own dedicated branch, guarded once by the built-in handler instead) —
    # see test_execute_code_relay.py for full coverage of that path.
    from wheelbase_sdk import runtime
    runtime.set_task_identity(
        "t-desk", {"user_id": "u", "shell_relay_url": "wss://relay", "workspace_root": "/work"}
    )
    monkeypatch.setattr(plug, "_safety_block",
                        lambda *a, **k: pytest.fail("generic _safety_block must not run for execute_code"))

    def _boom_transport(url, ident):
        raise RuntimeError("no transport in this unit test")

    monkeypatch.setattr(plug, "_make_transport", _boom_transport)
    nc, calls = _next_call_spy()
    out = plug.route_or_passthrough(
        tool_name="execute_code", args={"code": "1"}, next_call=nc, task_id="t-desk"
    )
    # transport build failed -> fail-closed to cloud, but via the DEDICATED
    # execute_code branch, never touching the generic safety seam.
    assert out == "SANDBOX_RESULT"
    assert calls["n"] == 1


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
