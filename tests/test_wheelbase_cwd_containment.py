"""cwd containment for identified cloud sessions."""
from __future__ import annotations

import pytest

from tools import terminal_tool
from tui_gateway import server
from tui_gateway.wheelbase_identity import WheelbaseIdentity

IDENT = WheelbaseIdentity(user_id="user-aaaa", tenant_id="t1", dealership_id="d1", jwt="j")


@pytest.fixture
def identified_session(monkeypatch):
    monkeypatch.setattr(server, "_get_db", lambda: None)
    session = {
        "session_key": "20260612_101500_ab12cd",
        "cwd": "/workspace/conversations/20260612_101500_ab12cd",
        "wheelbase_identity": IDENT,
    }
    yield session
    terminal_tool._task_env_overrides.pop(session["session_key"], None)


def test_accepts_workspace_subfolder(identified_session):
    out = server._set_session_cwd(identified_session, "/workspace/foo")
    assert out == "/workspace/foo"
    assert identified_session["cwd"] == "/workspace/foo"
    assert identified_session["explicit_cwd"] is True
    override = terminal_tool._task_env_overrides[identified_session["session_key"]]
    assert override["cwd"] == "/workspace/foo"


def test_accepts_workspace_root_and_normalizes_dotdot_inside(identified_session):
    assert server._set_session_cwd(identified_session, "/workspace") == "/workspace"
    assert server._set_session_cwd(identified_session, "/workspace/a/../b") == "/workspace/b"


def test_rejects_etc_and_dotdot_escape(identified_session):
    original = identified_session["cwd"]
    for bad in (
        "/etc",
        "/workspace/../etc",
        "/workspace/../../root",
        "relative/path",
        "/workspacefoo",
    ):
        with pytest.raises(ValueError):
            server._set_session_cwd(identified_session, bad)
    assert identified_session["cwd"] == original


def test_no_host_isdir_requirement_for_identified(identified_session):
    out = server._set_session_cwd(identified_session, "/workspace/never-created-on-host")
    assert out == "/workspace/never-created-on-host"


def test_anonymous_sessions_keep_host_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_get_db", lambda: None)
    session = {"session_key": "legacy", "cwd": str(tmp_path), "wheelbase_identity": None}
    assert server._set_session_cwd(session, str(tmp_path)) == str(tmp_path)
    with pytest.raises(ValueError):
        server._set_session_cwd(session, str(tmp_path / "missing"))
    terminal_tool._task_env_overrides.pop("legacy", None)
