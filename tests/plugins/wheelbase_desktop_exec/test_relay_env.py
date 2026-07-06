from __future__ import annotations

import importlib
import queue

import pytest

relay_env = importlib.import_module("plugins.wheelbase-desktop-exec.relay_env")
transport_mod = importlib.import_module("plugins.wheelbase-desktop-exec.transport")


class FakeTransport(transport_mod.ExecTransport):
    """Scripts ExecOutbound frames per request_id; records ExecInbound sends."""

    def __init__(self, connected=True):
        self.sent = []
        self._connected = connected
        self._scripts: dict[str, "queue.Queue[dict]"] = {}
        self.cancelled = []

    def script(self, request_id, frames):
        q: "queue.Queue[dict]" = queue.Queue()
        for f in frames:
            q.put(f)
        self._scripts[request_id] = q

    def send(self, frame):
        if not self._connected:
            raise transport_mod.PreDispatchError("relay not connected")
        self.sent.append(dict(frame))
        if frame["type"] in ("interrupt", "cancel"):
            self.cancelled.append(frame["request_id"])

    def recv(self, request_id, timeout=None):
        # setdefault: an unscripted request_id blocks on an empty queue
        # (real "no frame yet") rather than KeyError-ing, so a command with
        # no scripted terminal frame stays "running" until interrupted.
        q = self._scripts.setdefault(request_id, queue.Queue())
        return q.get(timeout=timeout or 5)


def _env(transport, **kw):
    return relay_env.DesktopRelayEnvironment(
        transport=transport, cwd="/work", timeout=30, workspace_root="/work", **kw
    )


def test_exec_frame_shape_and_streamed_output(monkeypatch):
    t = FakeTransport()
    env = _env(t)
    monkeypatch.setattr(env, "_snapshot_ready", True)  # skip init_session bootstrap
    # request_id is generated per call; capture it by scripting lazily.
    orig_run = env._run_bash

    def run(cmd_string, **kw):
        handle = orig_run(cmd_string, **kw)
        return handle

    # Script by intercepting the first send.
    real_send = t.send

    def send(frame):
        real_send(frame)
        if frame["type"] == "exec":
            t.script(frame["request_id"], [
                {"type": "chunk", "request_id": frame["request_id"], "stream": "stdout", "data": "hello\n"},
                {"type": "exit", "request_id": frame["request_id"], "exit_code": 0},
            ])
    t.send = send

    result = env.execute("echo hello")
    assert "hello" in result["output"]
    assert result["returncode"] == 0
    exec_frames = [f for f in t.sent if f["type"] == "exec"]
    assert exec_frames and exec_frames[0]["kind"] == "bash"
    assert exec_frames[0]["workspace_root"] == "/work"


def test_nonzero_exit_code_propagates(monkeypatch):
    t = FakeTransport()
    env = _env(t)
    monkeypatch.setattr(env, "_snapshot_ready", True)
    real_send = t.send

    def send(frame):
        real_send(frame)
        if frame["type"] == "exec":
            t.script(frame["request_id"], [
                {"type": "chunk", "request_id": frame["request_id"], "stream": "stderr", "data": "boom"},
                {"type": "exit", "request_id": frame["request_id"], "exit_code": 3},
            ])
    t.send = send
    result = env.execute("false")
    assert result["returncode"] == 3


def test_error_frame_becomes_failed_result(monkeypatch):
    t = FakeTransport()
    env = _env(t)
    monkeypatch.setattr(env, "_snapshot_ready", True)
    real_send = t.send

    def send(frame):
        real_send(frame)
        if frame["type"] == "exec":
            t.script(frame["request_id"], [
                {"type": "error", "request_id": frame["request_id"], "message": "desktop dropped"},
            ])
    t.send = send
    result = env.execute("sleep 1")
    assert result["returncode"] != 0
    assert "desktop dropped" in result["output"]


def test_predispatch_send_failure_raises(monkeypatch):
    t = FakeTransport(connected=False)
    env = _env(t)
    monkeypatch.setattr(env, "_snapshot_ready", True)
    with pytest.raises(transport_mod.PreDispatchError):
        env.execute("echo hi")


def test_interrupt_sends_interrupt_frame(monkeypatch):
    import threading
    t = FakeTransport()
    env = _env(t)
    monkeypatch.setattr(env, "_snapshot_ready", True)
    real_send = t.send

    def send(frame):
        real_send(frame)
        # never enqueue a terminal frame → command "runs" until interrupted
    t.send = send
    # is_interrupted is imported via `from tools.interrupt import is_interrupted`
    # in tools/environments/base.py, so the name used by _wait_for_process must
    # be patched where it's bound (base.py), not on the tools.interrupt module
    # (see tests/tools/test_daytona_environment.py:62-63,369 for precedent).
    monkeypatch.setattr("tools.environments.base.is_interrupted", lambda: True)
    result = env.execute("sleep 100", timeout=5)
    assert result["returncode"] == 130
    assert t.cancelled  # an interrupt frame was sent via cancel_fn
