"""Merge-specific coverage for the upstream ``session.resume``/``session.list``/
``session.workspace.move`` restructure (docs/upstream-merge-plan-2026-08-18.md
§4.6).

The bulk of the ownership/tip/db-handle-transfer behavior is already covered
by ``tests/test_wheelbase_multiuser.py`` and
``tests/tui_gateway/test_session_resume_db_ownership.py``. This file only adds
the genuine gaps surfaced by re-basing ``session.resume`` onto upstream's
outer try/finally + hydration-worker structure:

* ``session.list``'s ``total``/``has_more`` must stay scoped to the caller's
  own rows AND respect ``include_hidden`` together (existing tests check the
  ``sessions`` array or the scoping alone, never the paired total).
* ``session.workspace.move`` gates the PERSISTED row by owner, not just the
  live runtime session (the live-session gate already had a test; the
  durable-row gate is new in this merge).
* Every deferred-build branch (lazy / defer_history / cold-default) stamps
  ``wheelbase_identity`` on the record before it can be claimed live, not just
  the eager path.
"""

from __future__ import annotations

import pytest

from hermes_state import SessionDB
from tui_gateway import server
from tui_gateway.transport import bind_transport, reset_transport
from tui_gateway.wheelbase_identity import WheelbaseIdentity


class _FakeTransport:
    def __init__(self, identity=None):
        self.wheelbase_identity = identity


IDENT_A = WheelbaseIdentity(
    user_id="user-merge-a",
    tenant_id="t1",
    dealership_id="d1",
    jwt="jwt-a",
    session_jti_hash="jti-a",
    credential_revision=1,
    credential_expires_at=9999999999,
)
IDENT_B = WheelbaseIdentity(
    user_id="user-merge-b",
    tenant_id="t1",
    dealership_id="d1",
    jwt="jwt-b",
    session_jti_hash="jti-b",
    credential_revision=1,
    credential_expires_at=9999999999,
)


@pytest.fixture
def db(tmp_path):
    d = SessionDB(db_path=tmp_path / "state.db")
    yield d
    d.close()


@pytest.fixture
def bound():
    tokens = []

    def _bind(identity):
        token = bind_transport(_FakeTransport(identity))
        tokens.append(token)
        return token

    yield _bind
    for token in tokens:
        reset_transport(token)


@pytest.fixture(autouse=True)
def _no_leaked_sessions():
    known = set(server._sessions)
    yield
    with server._sessions_lock:
        for sid in [s for s in server._sessions if s not in known]:
            session = server._sessions.pop(sid, None)
            lease = (session or {}).get("active_session_lease")
            if lease is not None:
                lease.release()


# ── session.list: total/has_more must respect BOTH user scoping and hidden ──


def test_session_list_total_respects_user_scope_and_hidden_together(
    db, bound, monkeypatch
):
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_profile_is_launch_home", lambda _profile: True)

    for sid in ("a-1", "a-2", "a-3"):
        db.create_session(sid, source="tui", user_id=IDENT_A.user_id)
    db.create_session("a-hidden", source="tui", user_id=IDENT_A.user_id)
    assert db.set_session_hidden("a-hidden", True) is True
    for sid in ("b-1", "b-2"):
        db.create_session(sid, source="tui", user_id=IDENT_B.user_id)

    bound(IDENT_A)

    # Default (include_hidden defaults to False): A's total must count only
    # A's non-hidden rows (3), not B's rows and not A's own hidden row.
    resp = server.handle_request(
        {"id": 1, "method": "session.list", "params": {"limit": 2}}
    )
    result = resp["result"]
    # Pages come back newest-first, so page 1 is the two most recently started
    # of A's visible rows — a-3, a-2 — not a-1, a-2.
    assert {s["id"] for s in result["sessions"]} == {"a-3", "a-2"}
    assert result["total"] == 3
    assert result["has_more"] is True

    # Second page.
    resp = server.handle_request(
        {"id": 2, "method": "session.list", "params": {"limit": 2, "offset": 2}}
    )
    result = resp["result"]
    assert {s["id"] for s in result["sessions"]} == {"a-1"}
    assert result["total"] == 3
    assert result["has_more"] is False

    # include_hidden=True: total must grow to include the hidden row too, so
    # a caller that lists with the flag on gets a total that matches its page
    # (the hidden row must not silently inflate/deflate the OTHER call).
    resp = server.handle_request(
        {"id": 3, "method": "session.list", "params": {"include_hidden": True}}
    )
    result = resp["result"]
    assert {s["id"] for s in result["sessions"]} == {"a-1", "a-2", "a-3", "a-hidden"}
    assert result["total"] == 4


# ── session.workspace.move: persisted-row ownership gate ────────────────────


def test_workspace_move_denies_foreign_and_legacy_persisted_row(db, bound, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_profile_is_launch_home", lambda _profile: True)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)

    db.create_session("foreign-row", source="tui", user_id=IDENT_B.user_id)
    db.create_session("legacy-row", source="tui")  # NULL user_id

    from tui_gateway.wheelbase_inject import contain_workspace_path

    dest = "/workspace/dest-project"
    # Sanity: contain_workspace_path must accept the path we're about to move
    # to, or this test would be asserting the wrong failure mode.
    contain_workspace_path(dest)

    bound(IDENT_A)
    for target in ("foreign-row", "legacy-row", "no-such-row"):
        resp = server.handle_request(
            {
                "id": 1,
                "method": "session.workspace.move",
                "params": {"session_key": target, "cwd": dest},
            }
        )
        assert resp["error"]["code"] == 4007, (target, resp)
        assert resp["error"]["message"] == "session not found"

    # Neither foreign row's nor the legacy row's cwd was touched.
    assert db.get_session("foreign-row").get("cwd") in (None, "")
    assert db.get_session("legacy-row").get("cwd") in (None, "")


def test_workspace_move_allows_same_owner_persisted_row(db, bound, monkeypatch):
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_profile_is_launch_home", lambda _profile: True)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)

    db.create_session("own-row", source="tui", user_id=IDENT_A.user_id)

    dest = "/workspace/own-dest"
    bound(IDENT_A)
    resp = server.handle_request(
        {
            "id": 1,
            "method": "session.workspace.move",
            "params": {"session_key": "own-row", "cwd": dest},
        }
    )
    assert "error" not in resp, resp
    assert db.get_session("own-row")["cwd"] == resp["result"]["cwd"]


# ── every deferred-build branch stamps wheelbase_identity ───────────────────


def _quiet_resume(monkeypatch, db):
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_maybe_schedule_auto_continue", lambda *a, **k: None)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *a, **k: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda *a, **k: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)


def test_lazy_resume_stamps_identity_before_claim(db, bound, monkeypatch):
    _quiet_resume(monkeypatch, db)
    db.create_session("lazy-target", source="tui", user_id=IDENT_A.user_id)
    bound(IDENT_A)

    resp = server.handle_request(
        {
            "id": 1,
            "method": "session.resume",
            "params": {"session_id": "lazy-target", "lazy": True},
        }
    )
    assert "error" not in resp, resp
    sid = resp["result"]["session_id"]
    assert server._sessions[sid]["wheelbase_identity"] == IDENT_A
    # Durable runtime id: the lazy watch session key IS the Hermes session id.
    assert sid == "lazy-target"


def test_defer_history_resume_stamps_identity_before_claim(db, bound, monkeypatch):
    _quiet_resume(monkeypatch, db)
    db.create_session("defer-target", source="tui", user_id=IDENT_A.user_id)
    bound(IDENT_A)

    resp = server.handle_request(
        {
            "id": 1,
            "method": "session.resume",
            "params": {"session_id": "defer-target", "defer_history": True},
        }
    )
    assert "error" not in resp, resp
    sid = resp["result"]["session_id"]
    assert server._sessions[sid]["wheelbase_identity"] == IDENT_A
    assert sid == "defer-target"
    # Let the background hydration worker finish so it doesn't outlive the test.
    server._sessions[sid]["resume_history_ready"].wait(timeout=2.0)


def test_cold_default_resume_stamps_identity_before_claim(db, bound, monkeypatch):
    _quiet_resume(monkeypatch, db)
    db.create_session("cold-target", source="tui", user_id=IDENT_A.user_id)
    bound(IDENT_A)

    resp = server.handle_request(
        {
            "id": 1,
            "method": "session.resume",
            "params": {"session_id": "cold-target"},
        }
    )
    assert "error" not in resp, resp
    sid = resp["result"]["session_id"]
    assert server._sessions[sid]["wheelbase_identity"] == IDENT_A
    assert sid == "cold-target"
