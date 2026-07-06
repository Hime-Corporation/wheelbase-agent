"""F3: relayed results must re-fire the built-in post-processing that the outer
tool_execution wrapper bypasses — post_tool_call (observer) and
transform_tool_result (content-scan / canonicalization seam). Without this, the
shipped security-guidance plugin's transform_tool_result never runs on local
desktop writes.
"""
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


@pytest.fixture()
def hooks():
    """Register hooks on the real plugin manager, restore _hooks afterwards."""
    from hermes_cli.plugins import get_plugin_manager
    mgr = get_plugin_manager()
    saved = {k: list(v) for k, v in mgr._hooks.items()}
    yield mgr
    mgr._hooks.clear()
    mgr._hooks.update(saved)


def _no_next():
    def nc(args):
        raise AssertionError("next_call must NOT run after dispatch began")
    return nc


def test_relayed_write_file_applies_transform_and_fires_post_hook(monkeypatch, hooks):
    ft = FakeTransport(frames_by_type={"write": [{"type": "result", "ok": True}]})
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)

    seen = {"post": [], "transform": []}

    def post_cb(**kw):
        seen["post"].append((kw.get("tool_name"), kw.get("result")))
        return None

    def transform_cb(**kw):
        # Mirror the security-guidance content-scan: replace the result string.
        seen["transform"].append((kw.get("tool_name"), kw.get("args")))
        return json.dumps({"status": "scanned", "redacted": True})

    hooks._hooks.setdefault("post_tool_call", []).append(post_cb)
    hooks._hooks.setdefault("transform_tool_result", []).append(transform_cb)

    out = plug.route_or_passthrough(
        tool_name="write_file", args={"path": "/work/a.txt", "content": "secret"},
        next_call=_no_next(), task_id="t-desk", tool_call_id="pp1",
        turn_id="turn-1", api_request_id="req-1",
    )

    # transform_tool_result replaced the relayed result.
    parsed = json.loads(out)
    assert parsed["status"] == "scanned"
    assert parsed["redacted"] is True
    assert seen["transform"] and seen["transform"][0][0] == "write_file"

    # post_tool_call fired with the tool name (before the transform result).
    assert seen["post"] and seen["post"][0][0] == "write_file"


def test_post_processing_hook_that_raises_does_not_fail_the_tool(monkeypatch, hooks):
    ft = FakeTransport(frames_by_type={"write": [{"type": "result", "ok": True}]})
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)

    def boom(**kw):
        raise RuntimeError("hook exploded")

    hooks._hooks.setdefault("post_tool_call", []).append(boom)
    hooks._hooks.setdefault("transform_tool_result", []).append(boom)

    out = plug.route_or_passthrough(
        tool_name="write_file", args={"path": "/work/a.txt", "content": "hi"},
        next_call=_no_next(), task_id="t-desk", tool_call_id="pp2")

    # A raising hook is swallowed; the original relay result is returned intact.
    parsed = json.loads(out)
    assert parsed["success"] is True
