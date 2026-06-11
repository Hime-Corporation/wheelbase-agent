"""Multi-user session scoping for the cloud gateway (B2).

Covers: per-user session.list filtering, ownership-gated resume, user_id
persistence on session rows, and the identity.update credential refresh.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_state import SessionDB
from tui_gateway import server
from tui_gateway.transport import bind_transport, reset_transport
from tui_gateway.wheelbase_identity import WheelbaseIdentity


class _FakeTransport:
    def __init__(self, identity=None):
        self.wheelbase_identity = identity
        self.written = []

    def write(self, obj):
        self.written.append(obj)


IDENT_A = WheelbaseIdentity(user_id="user-aaaa", tenant_id="t1", dealership_id="d1", jwt="jwt-a")
IDENT_B = WheelbaseIdentity(user_id="user-bbbb", tenant_id="t1", dealership_id="d1", jwt="jwt-b")


@pytest.fixture
def db(tmp_path):
    d = SessionDB(db_path=tmp_path / "state.db")
    yield d
    d.close()


@pytest.fixture
def _bound(request):
    """Bind a fake transport (with optional identity) for the test body."""

    def bind(identity):
        transport = _FakeTransport(identity)
        token = bind_transport(transport)
        request.addfinalizer(lambda: reset_transport(token))
        return transport

    return bind


def test_list_sessions_rich_filters_by_user(db):
    db.create_session("s-a", source="tui", user_id=IDENT_A.user_id)
    db.create_session("s-b", source="tui", user_id=IDENT_B.user_id)
    db.create_session("s-legacy", source="tui")  # NULL user_id

    ids_a = {r["id"] for r in db.list_sessions_rich(user_id=IDENT_A.user_id)}
    assert ids_a == {"s-a"}, "identified user must see only their own rows (no legacy rows)"

    ids_all = {r["id"] for r in db.list_sessions_rich()}
    assert {"s-a", "s-b", "s-legacy"} <= ids_all, "legacy unfiltered behavior unchanged"


def test_session_list_handler_scopes_to_identity(db, _bound, monkeypatch):
    monkeypatch.setattr(server, "_get_db", lambda: db)
    db.create_session("s-a", source="tui", user_id=IDENT_A.user_id)
    db.create_session("s-b", source="tui", user_id=IDENT_B.user_id)

    _bound(IDENT_A)
    resp = server.handle_request({"id": 1, "method": "session.list", "params": {}})
    ids = {s["id"] for s in resp["result"]["sessions"]}
    assert ids == {"s-a"}


def test_session_list_handler_unscoped_without_identity(db, _bound, monkeypatch):
    monkeypatch.setattr(server, "_get_db", lambda: db)
    db.create_session("s-a", source="tui", user_id=IDENT_A.user_id)
    db.create_session("s-legacy", source="tui")

    _bound(None)
    resp = server.handle_request({"id": 1, "method": "session.list", "params": {}})
    ids = {s["id"] for s in resp["result"]["sessions"]}
    assert {"s-a", "s-legacy"} <= ids


def test_resume_denies_foreign_and_legacy_rows(db, _bound, monkeypatch):
    monkeypatch.setattr(server, "_get_db", lambda: db)
    db.create_session("s-b", source="tui", user_id=IDENT_B.user_id)
    db.create_session("s-legacy", source="tui")

    _bound(IDENT_A)
    for target in ("s-b", "s-legacy"):
        resp = server.handle_request(
            {"id": 1, "method": "session.resume", "params": {"session_id": target}}
        )
        assert resp["error"]["code"] == 4007, f"resume of {target} must look like 'not found'"
        assert "not found" in resp["error"]["message"]


def test_ensure_session_db_row_persists_user_id(db, monkeypatch):
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_resolve_model", lambda: "test-model")
    session = {
        "session_key": "sess-key-1",
        "wheelbase_identity": IDENT_A,
        "explicit_cwd": False,
    }
    server._ensure_session_db_row(session)
    row = db.get_session("sess-key-1")
    assert row is not None
    assert row["user_id"] == IDENT_A.user_id

    # Legacy: no identity -> NULL user_id.
    server._ensure_session_db_row({"session_key": "sess-key-2", "explicit_cwd": False})
    assert db.get_session("sess-key-2")["user_id"] is None


def test_identity_update_rewrites_credential_file(tmp_path, _bound, monkeypatch):
    monkeypatch.setattr(server, "get_hermes_home", lambda: str(tmp_path))
    _bound(IDENT_A)
    resp = server.handle_request(
        {"id": 1, "method": "identity.update", "params": {"jwt": "jwt-rotated"}}
    )
    assert resp["result"]["ok"] is True
    cred = tmp_path / "wheelbase-sessions" / f"{IDENT_A.user_id}.json"
    data = json.loads(cred.read_text(encoding="utf-8"))
    assert data["access_token"] == "jwt-rotated"


def test_identity_update_rejected_without_identity(_bound):
    _bound(None)
    resp = server.handle_request(
        {"id": 1, "method": "identity.update", "params": {"jwt": "x"}}
    )
    assert resp["error"]["code"] == 4030
