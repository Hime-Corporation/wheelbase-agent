"""One user message must produce exactly one turn, even if the client re-sends.

The wire protocol carries no dedup token of its own: upstream's desktop sends
only ``session_id``/``text``/``interrupted``/``queued``/``truncate_*``, and the
JSON-RPC envelope ``id`` is a per-process correlation counter the handler never
reads. So *any* client that re-issues a submit — a bounded auto-drain retry
(upstream ``use-composer-queue``), or wheelbase-app's queue-drain effect firing
on a stale queue entry after the run terminal — gets a second real turn stored
against the session. The model then sees turn 1 in its context and answers
"Hello again!", which is what made the duplicate visible.

wheelbase-app already sends an ``idempotency_key`` on ``prompt.submit`` (it is
the queue item's id, so a re-send of the *same* item repeats the key while a
genuinely new message gets a fresh one). These tests pin the gateway honouring
it: a repeat of a key already accepted for this session replays the original
ACK instead of running a second turn.
"""

import threading
import types

from tui_gateway import server


def _session(**extra):
    return {
        "agent": types.SimpleNamespace(),
        "agent_ready": threading.Event(),
        "session_key": "session-key",
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "transport": None,
        "attached_images": [],
        **extra,
    }


def _install(monkeypatch, sid, session):
    """Stub everything prompt.submit touches except the turn dispatch itself."""
    dispatched: list[str] = []

    def _run(_rid, _sid, sess, text, **_kwargs):
        dispatched.append(text)
        with sess["history_lock"]:
            sess["running"] = False

    monkeypatch.setattr(server, "_run_prompt_submit", _run)
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _s: None)
    monkeypatch.setattr(server, "_persist_branch_seed", lambda _s: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_wait_agent_for_prompt", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_ensure_active_session_slot", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *_a, **_k: False)
    monkeypatch.setattr(server, "_emit", lambda *_a, **_k: None)
    server._sessions[sid] = session
    return dispatched


def _submit(sid, text, **params):
    resp = server.handle_request(
        {
            "id": f"rpc-{len(text)}",
            "method": "prompt.submit",
            "params": {"session_id": sid, "text": text, **params},
        }
    )
    thread = server._sessions[sid].get("_run_thread")
    if thread is not None:
        thread.join(timeout=5)
    return resp


def test_single_submit_runs_exactly_one_turn(monkeypatch):
    """Control: the gateway never doubles a turn on its own."""
    sid = "idem-control"
    session = _session()
    dispatched = _install(monkeypatch, sid, session)
    try:
        resp = _submit(sid, "hello", idempotency_key="run-1")
        assert resp["result"] == {"status": "streaming"}
        assert dispatched == ["hello"]
    finally:
        server._sessions.pop(sid, None)


def test_repeat_of_an_accepted_key_does_not_run_a_second_turn(monkeypatch):
    """The observed bug: an identical re-send lands after the turn settles.

    wheelbase-app clears ``activeRuns`` from the transport's ``onRunTerminal``
    subscriber but only drops the queue entry in ``sendToSession``'s ``finally``
    — a later microtask. The drain effect can therefore observe
    ``activeRuns == []`` with the item still queued and re-send it, carrying the
    SAME queue-item id as ``idempotency_key``.
    """
    sid = "idem-replay"
    session = _session()
    dispatched = _install(monkeypatch, sid, session)
    try:
        first = _submit(sid, "hello", idempotency_key="run-1")
        second = _submit(sid, "hello", idempotency_key="run-1")

        assert dispatched == ["hello"], "the same submission ran twice"
        assert second["result"] == first["result"]
    finally:
        server._sessions.pop(sid, None)


def test_repeat_while_the_first_turn_is_still_running_is_not_queued(monkeypatch):
    """The same replay, raced earlier: it must not become a queued next turn."""
    sid = "idem-replay-busy"
    session = _session()
    dispatched = _install(monkeypatch, sid, session)
    try:
        _submit(sid, "hello", idempotency_key="run-1")
        with session["history_lock"]:
            session["running"] = True  # turn 1 still unwinding
        _submit(sid, "hello", idempotency_key="run-1")

        assert dispatched == ["hello"]
        assert session.get("queued_prompt") is None
        assert not session.get("queued_prompts")
    finally:
        server._sessions.pop(sid, None)


def test_a_new_key_with_identical_text_still_runs(monkeypatch):
    """Deliberately re-typing the same message is a real second turn."""
    sid = "idem-distinct"
    session = _session()
    dispatched = _install(monkeypatch, sid, session)
    try:
        _submit(sid, "hello", idempotency_key="run-1")
        _submit(sid, "hello", idempotency_key="run-2")
        assert dispatched == ["hello", "hello"]
    finally:
        server._sessions.pop(sid, None)


def test_keyless_clients_are_unaffected(monkeypatch):
    """Upstream's desktop sends no key — its behaviour must be byte-identical."""
    sid = "idem-keyless"
    session = _session()
    dispatched = _install(monkeypatch, sid, session)
    try:
        _submit(sid, "hello")
        _submit(sid, "hello")
        assert dispatched == ["hello", "hello"]
    finally:
        server._sessions.pop(sid, None)
