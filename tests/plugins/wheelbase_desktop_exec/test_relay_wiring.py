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


def test_predispatch_failure_returns_desktop_unavailable(monkeypatch):
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: FakeTransport(connected=False))
    calls = {"n": 0}

    def nc(args):
        calls["n"] += 1
        return "SANDBOX"

    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)
    out = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "ls"}, next_call=nc,
        task_id="t-desk", tool_call_id="c1")
    assert json.loads(out)["error_code"] == "desktop_unavailable"
    assert calls["n"] == 0


def test_postdispatch_error_returns_tool_error_no_redispatch(monkeypatch):
    ft = FakeTransport(frames_by_type={"exec": [{"type": "error", "message": "desktop died"}]})
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)
    out = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "ls"}, next_call=_no_next(),
        task_id="t-desk", tool_call_id="c2")
    parsed = json.loads(out)
    assert parsed["error_code"] == "desktop_unavailable"


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


# "/workspace" is a cloud-sandbox path. The desktop jail root is the user's
# ~/Wheelbase, and exec-sidecar/jail.ts rejects every absolute path outside it
# ("path escapes workspace: /workspace"), so that default failed every relayed
# shell command while file ops, which send jail-relative paths, kept working.
def test_relay_cwd_defaults_to_the_desktop_jail_root():
    assert plug._relay_cwd({}) == "."
    assert plug._relay_cwd({"shell_relay_url": "wss://relay"}) == "."


def test_relay_cwd_prefers_an_explicit_identity_cwd():
    assert plug._relay_cwd({"cwd": "/Users/x/Wheelbase/proj"}) == "/Users/x/Wheelbase/proj"
    assert plug._relay_cwd({"workspace_root": "/Users/x/Wheelbase"}) == "/Users/x/Wheelbase"
    assert plug._relay_cwd({"cwd": "a", "workspace_root": "b"}) == "a"


# --- Shared transport cache (one WebSocket per (task_id, relay_url)) -------
#
# Before this, _relay called _make_transport() on EVERY relayed tool call and
# never closed the connection on success — a leak — while the Go ExecHub
# allows only one gateway connection per desktop and evicts the previous one
# without a close handshake, so two back-to-back calls for the same task
# could race each other's transport into existence and die mid-flight. These
# tests exercise the module-level cache that replaces that per-call model.

def test_relay_reuses_one_transport_across_calls_for_the_same_task(monkeypatch):
    ft = FakeTransport(frames_by_type={"exec": [
        {"type": "chunk", "stream": "stdout", "data": "ok\n"},
        {"type": "exit", "exit_code": 0},
    ]})
    built = {"n": 0}

    def make(url, ident):
        built["n"] += 1
        return ft

    monkeypatch.setattr(plug, "_make_transport", make)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)

    out1 = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "echo ok"}, next_call=_no_next(),
        task_id="t-desk", tool_call_id="reuse-1")
    out2 = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "echo ok"}, next_call=_no_next(),
        task_id="t-desk", tool_call_id="reuse-2")

    assert built["n"] == 1  # ONE transport built and reused for both calls
    assert "ok" in json.loads(out1)["output"]
    assert "ok" in json.loads(out2)["output"]


def test_relay_evicts_and_retries_once_after_a_stale_cached_send_failure(monkeypatch):
    # A send that raises PreDispatchError against a CACHED transport never
    # reached the desktop, so it is safe to rebuild and re-send exactly once
    # (M4 distinguishes this from a post-dispatch failure, which is never
    # retried). Simulates the Go ExecHub having evicted this gateway
    # connection out from under us between calls.
    stale_ft = FakeTransport(frames_by_type={"exec": [
        {"type": "chunk", "stream": "stdout", "data": "first\n"},
        {"type": "exit", "exit_code": 0},
    ]})
    fresh_ft = FakeTransport(frames_by_type={"exec": [
        {"type": "chunk", "stream": "stdout", "data": "second\n"},
        {"type": "exit", "exit_code": 0},
    ]})
    built = {"n": 0}

    def make(url, ident):
        built["n"] += 1
        return stale_ft if built["n"] == 1 else fresh_ft

    monkeypatch.setattr(plug, "_make_transport", make)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)

    out1 = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "echo first"}, next_call=_no_next(),
        task_id="t-desk", tool_call_id="stale-1")
    assert "first" in json.loads(out1)["output"]

    def dead_send(frame):
        raise transport_mod.PreDispatchError("connection evicted by a newer dial")
    stale_ft.send = dead_send

    out2 = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "echo second"}, next_call=_no_next(),
        task_id="t-desk", tool_call_id="stale-2")

    assert built["n"] == 2  # evicted the dead one, rebuilt exactly once
    assert "second" in json.loads(out2)["output"]
    with plug._transport_cache_lock:
        cached = plug._transport_cache[("t-desk", "wss://relay")]
    assert cached is fresh_ft


def test_relay_retry_exhaustion_still_fails_closed_never_falls_back_to_next_call(monkeypatch):
    ft = FakeTransport(frames_by_type={"exec": [
        {"type": "chunk", "stream": "stdout", "data": "first\n"},
        {"type": "exit", "exit_code": 0},
    ]})
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)

    plug.route_or_passthrough(
        tool_name="terminal", args={"command": "echo first"}, next_call=_no_next(),
        task_id="t-desk", tool_call_id="always-dead-1")

    def dead_send(frame):
        raise transport_mod.PreDispatchError("still dead")
    ft.send = dead_send

    out = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "echo x"}, next_call=_no_next(),
        task_id="t-desk", tool_call_id="always-dead-2")
    assert json.loads(out)["error_code"] == "desktop_unavailable"


def test_relay_rebuilds_when_cached_transport_is_marked_closed(monkeypatch):
    ft1 = FakeTransport(frames_by_type={"exec": [
        {"type": "chunk", "stream": "stdout", "data": "one\n"},
        {"type": "exit", "exit_code": 0},
    ]})
    ft2 = FakeTransport(frames_by_type={"exec": [
        {"type": "chunk", "stream": "stdout", "data": "two\n"},
        {"type": "exit", "exit_code": 0},
    ]})
    built = {"n": 0}

    def make(url, ident):
        built["n"] += 1
        return ft1 if built["n"] == 1 else ft2

    monkeypatch.setattr(plug, "_make_transport", make)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)

    plug.route_or_passthrough(
        tool_name="terminal", args={"command": "echo one"}, next_call=_no_next(),
        task_id="t-desk", tool_call_id="closed-1")
    ft1._closed = True  # e.g. explicitly closed by an eviction elsewhere

    out = plug.route_or_passthrough(
        tool_name="terminal", args={"command": "echo two"}, next_call=_no_next(),
        task_id="t-desk", tool_call_id="closed-2")

    assert built["n"] == 2
    assert "two" in json.loads(out)["output"]


def test_relay_does_not_retry_a_multi_frame_op_that_already_dispatched(monkeypatch):
    # patch/search_files run through ShellFileOperations, which issues SEVERAL
    # exec frames over one transport. If the connection dies partway, earlier
    # frames have ALREADY executed on the user's machine, so re-running the
    # whole operation would repeat real side effects. The plain "it was a
    # PreDispatchError, so nothing ran" reasoning is only valid for
    # single-frame tools; this is the case that makes the sends_completed
    # guard load-bearing rather than decorative.
    ft = FakeTransport(frames_by_type={"exec": [
        {"type": "chunk", "stream": "stdout", "data": "ok\n"},
        {"type": "exit", "exit_code": 0},
    ]})
    built = {"n": 0}

    def make(url, ident):
        built["n"] += 1
        return ft

    monkeypatch.setattr(plug, "_make_transport", make)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)

    # Prime the cache so the next call takes the from_cache branch.
    plug.route_or_passthrough(
        tool_name="terminal", args={"command": "echo ok"}, next_call=_no_next(),
        task_id="t-desk", tool_call_id="prime-1")
    assert built["n"] == 1

    # Now let the FIRST frame through and kill the connection on the second,
    # which is precisely the partial-dispatch shape.
    real_send = ft.send
    calls = {"n": 0}

    def flaky_send(frame):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_send(frame)
        raise transport_mod.PreDispatchError("connection evicted mid-operation")

    ft.send = flaky_send

    out = plug.route_or_passthrough(
        tool_name="search_files", args={"pattern": "needle", "target": "."},
        next_call=_no_next(), task_id="t-desk", tool_call_id="partial-1")

    # No rebuild: one frame already ran, so the operation is NOT replayed.
    assert built["n"] == 1, "must not re-dispatch an operation that already executed frames"
    assert json.loads(out).get("status") == "error" or "unavailable" in out.lower()
