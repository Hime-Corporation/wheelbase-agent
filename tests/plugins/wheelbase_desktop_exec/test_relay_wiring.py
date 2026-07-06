from __future__ import annotations

import importlib
import json
import queue

import pytest

plug = importlib.import_module("plugins.wheelbase-desktop-exec")
transport_mod = importlib.import_module("plugins.wheelbase-desktop-exec.transport")


class FakeTransport(transport_mod.ExecTransport):
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
            g = dict(f); g["request_id"] = rid
            q.put(g)

    def recv(self, request_id, timeout=None):
        return self._q[request_id].get(timeout=timeout or 5)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _ident(monkeypatch):
    from wheelbase_sdk import runtime
    runtime.set_task_identity("t-desk", {"user_id": "u", "shell_relay_url": "wss://relay",
                                         "workspace_root": "/work"})
    yield
    runtime._current.set(None)
    with runtime._lock:
        runtime._by_task.clear()


def _no_next():
    def nc(args):
        raise AssertionError("next_call must NOT run after dispatch began")
    return nc


def test_predispatch_failure_falls_back_to_next_call(monkeypatch):
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: FakeTransport(connected=False))
    calls = {"n": 0}

    def nc(args):
        calls["n"] += 1
        return "SANDBOX"

    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)
    out = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "ls"}, next_call=nc,
        task_id="t-desk", tool_call_id="c1")
    assert out == "SANDBOX"
    assert calls["n"] == 1


def test_postdispatch_error_returns_tool_error_no_redispatch(monkeypatch):
    ft = FakeTransport(frames_by_type={"exec": [{"type": "error", "message": "desktop died"}]})
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)
    out = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "ls"}, next_call=_no_next(),
        task_id="t-desk", tool_call_id="c2")
    parsed = json.loads(out)
    assert parsed["returncode"] != 0 or parsed.get("exit_code", 1) != 0


def test_successful_terminal_relay(monkeypatch):
    ft = FakeTransport(frames_by_type={"exec": [
        {"type": "chunk", "stream": "stdout", "data": "ok\n"},
        {"type": "exit", "exit_code": 0},
    ]})
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)
    out = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "echo ok"}, next_call=_no_next(),
        task_id="t-desk", tool_call_id="c3")
    assert "ok" in json.loads(out)["output"]


def test_write_file_sends_write_frame(monkeypatch):
    ft = FakeTransport(frames_by_type={"write": [{"type": "result", "ok": True}]})
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)
    plug.route_or_passthrough(
        tool_name="write_file", args={"path": "/work/a.txt", "content": "hi"},
        next_call=_no_next(), task_id="t-desk", tool_call_id="c4")
    write_frames = [f for f in ft.sent if f["type"] == "write"]
    assert write_frames and write_frames[0]["path"] == "/work/a.txt"
    assert write_frames[0]["data"] == "hi"


def test_same_tool_call_id_across_tasks_does_not_collide(monkeypatch):
    # Shared-dashboard fallback runs many users in one process. A reused
    # tool_call_id across two DIFFERENT task_ids must NOT serve user A's cached
    # output to user B — the cache key is (task_id, tool_call_id).
    from wheelbase_sdk import runtime
    runtime.set_task_identity(
        "task-A", {"user_id": "A", "shell_relay_url": "wss://relayA", "workspace_root": "/a"})
    runtime.set_task_identity(
        "task-B", {"user_id": "B", "shell_relay_url": "wss://relayB", "workspace_root": "/b"})

    def _transport_for(url, ident):
        who = ident.get("user_id")
        return FakeTransport(frames_by_type={"exec": [
            {"type": "chunk", "stream": "stdout", "data": f"hello-{who}\n"},
            {"type": "exit", "exit_code": 0},
        ]})

    monkeypatch.setattr(plug, "_make_transport", _transport_for)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)

    out_a = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "whoami"}, next_call=_no_next(),
        task_id="task-A", tool_call_id="shared-id")
    out_b = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "whoami"}, next_call=_no_next(),
        task_id="task-B", tool_call_id="shared-id")

    assert "hello-A" in json.loads(out_a)["output"]
    assert "hello-B" in json.loads(out_b)["output"]   # NOT A's cached output


def test_idempotent_second_invocation_no_double_execute(monkeypatch):
    ft = FakeTransport(frames_by_type={"exec": [
        {"type": "chunk", "stream": "stdout", "data": "once\n"},
        {"type": "exit", "exit_code": 0},
    ]})
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)
    kw = dict(tool_name="terminal", args={"command": "echo once"},
              next_call=_no_next(), task_id="t-desk", tool_call_id="dup")
    out1 = plug.route_or_passthrough(**kw)
    out2 = plug.route_or_passthrough(**kw)
    assert out1 == out2
    exec_sends = [f for f in ft.sent if f["type"] == "exec"]
    assert len(exec_sends) == 1  # second call served from cache, not re-executed
