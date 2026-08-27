"""Gap-fill coverage for the Hermes-upstream merge (2026-08-18) in
``tui_gateway/methods_prompt.py``.

``tests/test_tui_gateway_prompt_idempotency.py`` and the bulk of
``tests/test_tui_gateway_server.py`` already exercise: same-key replay on the
direct and busy/queued paths, distinct keys, keyless upstream clients, and
consecutive rewinds using the returned ``survivor_user_row_ids``. This file
covers the remaining merge-specific behaviors that had no test anywhere:

1. Same-key replay on the ISOLATED compute-host dispatch path (the direct and
   busy/queued paths were already covered; the compute-host path was not).
2. A failed submit's error envelope is never cached — a retry with the same
   key must actually run, not replay the error.
3. All three truncation address forms (ordinal, row-id, message-id) re-expand
   a skill invocation — only the legacy ordinal form did before this merge.
4. A profile-owned session's truncation write goes to ``agent._session_db``
   and never falls through to the process-global ``_get_db()``.
"""

import threading
import types

from tui_gateway import server


def _session(agent=None, **extra):
    return {
        "agent": agent if agent is not None else types.SimpleNamespace(),
        "session_key": "session-key",
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "transport": None,
        "attached_images": [],
        "image_counter": 0,
        "cols": 80,
        "slash_worker": None,
        "show_reasoning": False,
        "tool_progress_mode": "all",
        **extra,
    }


def test_repeat_key_replays_on_compute_host_isolated_path_without_second_dispatch(
    monkeypatch,
):
    """Same-key duplicates must replay on the isolated compute-host dispatch
    path too — the direct and busy/queued paths were already covered, this
    one was not.
    """

    class FakeSupervisor:
        def __init__(self):
            self.frames = []

        def submit_turn(self, frame, *, on_complete=None):
            self.frames.append(frame)
            return frame["request_id"]

    supervisor = FakeSupervisor()
    sid = "iso-idem-sid"
    session = _session(history=[{"role": "user", "content": "previous"}])
    session["agent"] = None
    session["agent_ready"] = threading.Event()
    server._sessions[sid] = session
    monkeypatch.setattr(
        server, "_load_cfg", lambda: {"dashboard": {"turn_isolation": True}}
    )
    monkeypatch.setattr(
        server, "_get_compute_host_supervisor", lambda _cfg=None: supervisor
    )
    monkeypatch.setattr(server, "_ensure_active_session_slot", lambda *_a, **_k: None)

    try:
        first = server.handle_request(
            {
                "id": "1",
                "method": "prompt.submit",
                "params": {
                    "session_id": sid,
                    "text": "hello",
                    "idempotency_key": "iso-key",
                },
            }
        )
        assert first.get("error") is None, first

        second = server.handle_request(
            {
                "id": "2",
                "method": "prompt.submit",
                "params": {
                    "session_id": sid,
                    "text": "hello",
                    "idempotency_key": "iso-key",
                },
            }
        )
        assert len(supervisor.frames) == 1, (
            "a duplicate idempotency_key dispatched a second compute-host turn"
        )
        assert second["result"] == first["result"]
    finally:
        server._sessions.pop(sid, None)


def test_failed_submit_with_idempotency_key_is_not_cached(monkeypatch):
    """Merge invariant: 'Failed submissions are never cached.' A duplicate
    key sent after an induced failure must actually run the turn, not replay
    the error envelope.
    """
    sid = "idem-error-sid"
    session = _session(history=[])
    server._sessions[sid] = session

    calls = {"n": 0}

    def _flaky_ensure_row(_session):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")

    dispatched = []

    def _run(_rid, _sid, sess, text, **_kwargs):
        dispatched.append(text)
        with sess["history_lock"]:
            sess["running"] = False

    monkeypatch.setattr(server, "_ensure_session_db_row", _flaky_ensure_row)
    monkeypatch.setattr(server, "_persist_branch_seed", lambda _s: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_run_prompt_submit", _run)
    monkeypatch.setattr(server, "_ensure_active_session_slot", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *_a, **_k: False)
    monkeypatch.setattr(server, "_emit", lambda *_a, **_k: None)

    try:
        first = server.handle_request(
            {
                "id": "1",
                "method": "prompt.submit",
                "params": {
                    "session_id": sid,
                    "text": "hello",
                    "idempotency_key": "retry-key",
                },
            }
        )
        assert first.get("error") is not None, (
            "expected the induced failure to surface as an RPC error"
        )

        second = server.handle_request(
            {
                "id": "2",
                "method": "prompt.submit",
                "params": {
                    "session_id": sid,
                    "text": "hello",
                    "idempotency_key": "retry-key",
                },
            }
        )
        thread = session.get("_run_thread")
        if thread is not None:
            thread.join(timeout=5)

        assert second.get("error") is None, second
        assert dispatched == ["hello"], (
            "the retry after a failed submit must actually run the turn"
        )
    finally:
        server._sessions.pop(sid, None)


def test_all_three_truncation_forms_re_expand_skill_invocation(monkeypatch):
    """Merge decision (submit-setup hunk, item 4): ``has_truncation`` covers
    the ordinal, row-id, and message-id address forms, and a skill invocation
    must re-expand for ALL of them — before this merge only the legacy
    ordinal form triggered ``_expand_skill_invocation_for_replay``.
    """
    expand_calls = []

    def _fake_expand(text, _task_id):
        expand_calls.append(text)
        return f"EXPANDED::{text}"

    dispatched = []

    def _run(_rid, _sid, sess, text, **_kwargs):
        dispatched.append(text)
        with sess["history_lock"]:
            sess["running"] = False

    class _FakeDB:
        def replace_messages(
            self,
            key,
            messages,
            active_only=False,
            archive_dropped=False,
            reject_active_turn_lease=False,
        ):
            pass

    monkeypatch.setattr(server, "_expand_skill_invocation_for_replay", _fake_expand)
    monkeypatch.setattr(server, "_run_prompt_submit", _run)
    monkeypatch.setattr(server, "_start_agent_build", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_ensure_active_session_slot", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *_a, **_k: False)
    monkeypatch.setattr(server, "_get_db", lambda: _FakeDB())
    # Only the plain-ordinal branch consults the durable store to prove an
    # ordinal-only cut is safe on an unstamped session; keep it permissive so
    # this test isolates the skill-expansion behavior, not that check.
    monkeypatch.setattr(server, "_load_durable_truncation_history", lambda *_a, **_k: [])

    def _submit(sid, params):
        resp = server.handle_request(
            {
                "id": sid,
                "method": "prompt.submit",
                "params": {"session_id": sid, **params},
            }
        )
        thread = server._sessions[sid].get("_run_thread")
        if thread is not None:
            thread.join(timeout=5)
        return resp

    # -- ordinal form (no row/message ids stamped) --
    sid_ord = "skill-ord-sid"
    server._sessions[sid_ord] = _session(
        history=[
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply 1"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "reply 2"},
        ],
        session_key=sid_ord,
    )
    try:
        resp = _submit(
            sid_ord,
            {
                "text": "/work fix it",
                "truncate_before_user_ordinal": 1,
                "confirm_truncate": True,
            },
        )
        assert resp.get("error") is None, resp
    finally:
        server._sessions.pop(sid_ord, None)

    # -- row-id form --
    sid_row = "skill-row-sid"
    server._sessions[sid_row] = _session(
        history=[
            {"_row_id": 101, "role": "user", "content": "first"},
            {"_row_id": 102, "role": "assistant", "content": "reply 1"},
            {"_row_id": 103, "role": "user", "content": "second"},
            {"_row_id": 104, "role": "assistant", "content": "reply 2"},
        ],
        session_key=sid_row,
    )
    try:
        resp = _submit(
            sid_row,
            {
                "text": "/work fix it row",
                "truncate_before_row_id": 103,
                "confirm_truncate": True,
            },
        )
        assert resp.get("error") is None, resp
    finally:
        server._sessions.pop(sid_row, None)

    # -- message-id form --
    sid_msg = "skill-msg-sid"
    server._sessions[sid_msg] = _session(
        history=[
            {"id": "msg-1", "role": "user", "content": "first"},
            {"role": "assistant", "content": "reply 1"},
            {"id": "msg-2", "role": "user", "content": "second"},
            {"role": "assistant", "content": "reply 2"},
        ],
        session_key=sid_msg,
    )
    try:
        resp = _submit(
            sid_msg,
            {
                "text": "/work fix it msg",
                "truncate_before_message_id": "msg-2",
                "confirm_truncate": True,
            },
        )
        assert resp.get("error") is None, resp
    finally:
        server._sessions.pop(sid_msg, None)

    assert expand_calls == [
        "/work fix it",
        "/work fix it row",
        "/work fix it msg",
    ], expand_calls
    assert dispatched == [
        "EXPANDED::/work fix it",
        "EXPANDED::/work fix it row",
        "EXPANDED::/work fix it msg",
    ], dispatched


def test_truncation_on_a_profile_owned_session_never_touches_process_global_db(
    monkeypatch,
):
    """Merge decision (durable-truncation hunk, item 1): resolve the store as
    ``agent._session_db`` FIRST, with ``_get_db()`` only as a local fallback.
    A profile-owned session (cloud dashboard: one ``state.db`` per profile)
    must never have its rewind write land in the process-global db.
    """
    written = []

    class _ProfileDB:
        def replace_messages(
            self,
            key,
            messages,
            active_only=False,
            archive_dropped=False,
            reject_active_turn_lease=False,
        ):
            written.append((key, list(messages), active_only, archive_dropped))

    class _GlobalDB:
        def replace_messages(self, *_a, **_k):
            raise AssertionError(
                "profile-owned session truncation must not write through "
                "the process-global db"
            )

    monkeypatch.setattr(server, "_get_db", lambda: _GlobalDB())
    monkeypatch.setattr(server, "_start_agent_build", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_ensure_active_session_slot", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_session_uses_compute_host", lambda *_a, **_k: False)

    agent = types.SimpleNamespace(_session_db=_ProfileDB())
    sid = "profile-owned-trunc-sid"
    server._sessions[sid] = _session(
        agent=agent,
        history=[
            {"_row_id": 201, "role": "user", "content": "first"},
            {"_row_id": 202, "role": "assistant", "content": "reply 1"},
            {"_row_id": 203, "role": "user", "content": "second"},
            {"_row_id": 204, "role": "assistant", "content": "reply 2"},
        ],
        session_key="profile-session-key",
    )

    try:
        resp = server.handle_request(
            {
                "id": "1",
                "method": "prompt.submit",
                "params": {
                    "session_id": sid,
                    "text": "edited second",
                    "truncate_before_row_id": 203,
                    "confirm_truncate": True,
                },
            }
        )
        assert resp.get("error") is None, resp
        assert len(written) == 1
        key, messages, active_only, archive_dropped = written[0]
        # The durable key preference: session_key when present.
        assert key == "profile-session-key"
        assert active_only is True and archive_dropped is True
        # Compacted/archive rows are untouched: active_only=True scopes the
        # write to live rows, and archive_dropped=True soft-archives (never
        # deletes) whatever this cut drops.
        assert [m["content"] for m in messages] == ["first", "reply 1"]
    finally:
        server._sessions.pop(sid, None)
