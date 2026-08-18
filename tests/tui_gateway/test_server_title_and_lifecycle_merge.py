"""Regression tests for the hermes 8911e2e0e merge into tui_gateway/server.py.

Covers the two conflict hunks resolved in that merge:

1. `_tool_lifecycle_required_for_ui` must be the UNION of the Wheelbase side
   (``clarify``, ``todo``) and upstream's side (``clarify``, ``setup_mcp``),
   i.e. ``{"clarify", "todo", "setup_mcp"}`` — while ordinary tool chrome
   (e.g. ``terminal``) stays suppressed when tool progress is off.
2. The old post-turn ``maybe_auto_title`` call was deleted (title generation
   now runs once, in the shared turn prologue at
   ``agent.turn_context._maybe_title_session_at_turn_start``), while the
   profile-aware ``pending_title`` finalizer and the pre-``run_conversation``
   ``agent._on_session_title`` two-argument callback wiring were kept.
"""

import inspect
import threading

import pytest

import tui_gateway.server as server


def _session(agent=None, **extra):
    return {
        "agent": agent,
        "session_key": "session-key",
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "attached_images": [],
        "image_counter": 0,
        "cols": 80,
        "slash_worker": None,
        "show_reasoning": False,
        "tool_progress_mode": "all",
        **extra,
    }


# ── Tool lifecycle union ──────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["clarify", "todo", "setup_mcp"])
def test_tool_lifecycle_required_for_ui_union(name):
    """All three interactive-UI tool names survive the merge, not just one
    side's pre-merge pair."""
    assert server._tool_lifecycle_required_for_ui(name) is True


@pytest.mark.parametrize("name", ["terminal", "browser", "", "clarify.request"])
def test_tool_lifecycle_required_for_ui_excludes_ordinary_tools(name):
    assert server._tool_lifecycle_required_for_ui(name) is False


@pytest.mark.parametrize("tool_name", ["todo", "setup_mcp"])
def test_lifecycle_events_emit_when_tool_progress_off(monkeypatch, tool_name):
    """With tool_progress_mode == 'off', clarify/todo/setup_mcp events still
    fire (mirrors the pre-existing clarify-only coverage in
    tests/test_tui_gateway_server.py)."""
    events = []
    monkeypatch.setattr(
        server, "_emit", lambda event_type, sid, payload: events.append((event_type, sid, payload))
    )
    sid = f"{tool_name}-off-test"
    monkeypatch.setitem(
        server._sessions, sid, {"tool_progress_mode": "off", "tool_started_at": {}}
    )

    args = {"name": tool_name}
    server._on_tool_start(sid, "tool-1", tool_name, args)
    server._on_tool_complete(sid, "tool-1", tool_name, args, "ok")

    assert [event[0] for event in events] == ["tool.start", "tool.complete"]


def test_ordinary_tool_chrome_still_suppressed_when_progress_off(monkeypatch):
    """Sanity check that the union didn't accidentally widen suppression:
    a non-lifecycle tool stays silent when tool progress is off."""
    events = []
    monkeypatch.setattr(
        server, "_emit", lambda event_type, sid, payload: events.append((event_type, sid, payload))
    )
    sid = "ordinary-tool-off-test"
    monkeypatch.setitem(
        server._sessions, sid, {"tool_progress_mode": "off", "tool_started_at": {}}
    )

    server._on_tool_start(sid, "tool-1", "terminal", {"command": "pwd"})
    server._on_tool_complete(sid, "tool-1", "terminal", {"command": "pwd"}, "done")

    assert events == []


# ── Auto-title: single prologue call, no post-turn duplicate ─────────────


def test_server_module_no_longer_calls_maybe_auto_title():
    """The deleted post-turn block must not leave any live reference to
    agent.title_generator.maybe_auto_title in tui_gateway/server.py — a
    single call site would mean the prologue in agent/turn_context.py fires
    exactly once per turn, not twice."""
    source = inspect.getsource(server)
    assert "maybe_auto_title" not in source


def test_turn_start_title_prologue_exists_and_wires_on_session_title():
    """Guard against silently disabling auto-titling: confirm the turn
    prologue this merge relies on to replace the deleted post-turn call
    actually exists, and that it feeds the callback through
    ``agent._on_session_title``."""
    from agent import turn_context

    assert hasattr(turn_context, "_maybe_title_session_at_turn_start")
    prologue_source = inspect.getsource(turn_context._maybe_title_session_at_turn_start)
    assert "maybe_auto_title" in prologue_source
    assert '"_on_session_title"' in prologue_source


def test_prompt_submit_installs_two_arg_title_callback(monkeypatch):
    """agent._on_session_title must be a callable taking (title, source),
    matching the upstream two-argument callback contract, and must emit a
    live session.title event through the durable session key."""

    class _Agent:
        model = "gpt-5.6-sol"
        provider = "openai-codex"
        base_url = None
        api_key = None
        api_mode = None

        def run_conversation(self, prompt, conversation_history=None, stream_callback=None, **_kwargs):
            return {
                "final_response": "done",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "done"},
                ],
            }

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    agent = _Agent()
    server._sessions["sid"] = _session(agent=agent)
    emitted = []
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        server, "_emit", lambda kind, sid, payload=None, **kw: emitted.append((kind, payload))
    )
    monkeypatch.setattr(server, "make_stream_renderer", lambda cols: None)
    monkeypatch.setattr(server, "render_message", lambda raw, cols: None)
    monkeypatch.setattr(server, "_get_db", lambda: None)

    try:
        server.handle_request(
            {
                "id": "1",
                "method": "prompt.submit",
                "params": {"session_id": "sid", "text": "hi"},
            }
        )

        hook = getattr(agent, "_on_session_title", None)
        assert callable(hook)
        # Two-argument contract: (title, source).
        hook("Instant title", "derived")
        hook("Sharpened title", "llm")

        title_events = [payload for kind, payload in emitted if kind == "session.title"]
        assert [e["title"] for e in title_events] == ["Instant title", "Sharpened title"]
        assert all(e["session_id"] == "session-key" for e in title_events)
    finally:
        server._sessions.pop("sid", None)
