"""Multi-user session scoping for the cloud gateway (B2).

Covers: per-user session.list filtering, ownership-gated resume, user_id
persistence on session rows, and the identity.update credential refresh.
"""
from __future__ import annotations

import importlib
import json
import threading
from pathlib import Path

import pytest

from hermes_state import SessionDB
from tui_gateway import server
from tui_gateway.transport import bind_transport, reset_transport
from tui_gateway import wheelbase_identity
from tui_gateway.wheelbase_identity import WheelbaseIdentity
from wheelbase_sdk import runtime as wb_runtime


@pytest.fixture(autouse=True)
def _clear_jwt_cache():
    """The module-level JWT cache must not leak across tests/files."""
    yield
    with wheelbase_identity._lock:
        wheelbase_identity._jwt_by_user.clear()


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
    monkeypatch.setattr(server, "_profile_is_launch_home", lambda _profile: True)
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
        assert resp["error"]["code"] == 4007
        assert resp["error"]["message"] == "session unavailable"


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


def test_identity_update_atomically_refreshes_capabilities_without_scope_mutation(
    tmp_path, _bound, monkeypatch
):
    old = WheelbaseIdentity(
        user_id="user-aaaa",
        tenant_id="t1",
        client="desktop",
        device_id="device-1",
        jwt="jwt-old",
        cdp_url="https://old-cdp",
        shell_relay_url="wss://old-shell",
    )
    transport = _bound(old)
    session = {
        "session_key": "session-a",
        "transport": transport,
        "wheelbase_identity": old,
    }
    monkeypatch.setattr(server, "_sessions", {"session-a": session})
    monkeypatch.setattr(server, "get_hermes_home", lambda: str(tmp_path))

    response = server.handle_request(
        {
            "id": 1,
            "method": "identity.update",
            "params": {
                "jwt": "jwt-new",
                "user_id": old.user_id,
                "tenant_id": old.tenant_id,
                "client": old.client,
                "device_id": old.device_id,
                "cdp_url": "https://new-cdp",
                "shell_relay_url": "wss://new-shell",
            },
        }
    )

    assert response["result"]["ok"] is True
    assert transport.wheelbase_identity.cdp_url == "https://new-cdp"
    assert transport.wheelbase_identity.shell_relay_url == "wss://new-shell"
    assert session["wheelbase_identity"] is transport.wheelbase_identity
    assert session["wheelbase_identity"].tenant_id == old.tenant_id
    assert session["wheelbase_identity"].user_id == old.user_id

    cleared = server.handle_request(
        {
            "id": 2,
            "method": "identity.update",
            "params": {"jwt": "jwt-newer", "client": old.client, "device_id": old.device_id},
        }
    )
    assert cleared["result"]["ok"] is True
    assert transport.wheelbase_identity.cdp_url == ""
    assert transport.wheelbase_identity.shell_relay_url == ""


def test_queued_prompt_carries_requesting_device_identity(monkeypatch):
    old = WheelbaseIdentity(
        user_id="user-aaaa",
        tenant_id="t1",
        client="desktop",
        device_id="device-1",
        cdp_url="https://old-cdp",
    )
    refreshed = WheelbaseIdentity(
        user_id="user-aaaa",
        tenant_id="t1",
        client="desktop",
        device_id="device-2",
        cdp_url="https://fresh-cdp",
    )
    old_transport = _FakeTransport(old)
    requesting_transport = _FakeTransport(refreshed)
    session = {
        "history_lock": threading.Lock(),
        "running": False,
        "transport": old_transport,
        "wheelbase_identity": old,
    }
    observed = {}

    def run_queued(_rid, _sid, queued_session, text):
        observed["identity"] = queued_session["wheelbase_identity"]
        observed["text"] = text

    monkeypatch.setattr(server, "_run_prompt_submit", run_queued)
    server._enqueue_prompt(session, "next turn", requesting_transport)

    assert server._drain_queued_prompt("rid", "sid", session) is True
    assert session["transport"] is requesting_transport
    assert observed == {"identity": refreshed, "text": "next turn"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", "other-user"),
        ("tenant_id", "other-tenant"),
        ("client", "mobile"),
        ("device_id", "device-2"),
    ],
)
def test_identity_update_rejects_immutable_scope_changes(field, value, _bound):
    original = WheelbaseIdentity(
        user_id="user-aaaa", tenant_id="t1", client="desktop", device_id="device-1"
    )
    transport = _bound(original)
    response = server.handle_request(
        {"id": 1, "method": "identity.update", "params": {"jwt": "new", field: value}}
    )
    assert response["error"]["code"] == 4032
    assert transport.wheelbase_identity is original


def test_identity_update_rejected_without_identity(_bound):
    _bound(None)
    resp = server.handle_request(
        {"id": 1, "method": "identity.update", "params": {"jwt": "x"}}
    )
    assert resp["error"]["code"] == 4030


@pytest.mark.parametrize(
    "new_cdp,new_shell",
    [
        ("wss://cdp/new", "wss://shell/new"),
        ("", ""),
    ],
)
def test_identity_update_refreshes_active_shell_and_browser_policy_immediately(
    new_cdp, new_shell, tmp_path, monkeypatch
):
    from tools import browser_tool

    plugin = importlib.import_module("plugins.wheelbase-desktop-exec")
    task_id = "active-d1"
    connection_id = "connection-d1"
    old = WheelbaseIdentity(
        user_id="user-aaaa",
        tenant_id="t1",
        client="desktop",
        device_id="device-1",
        jwt="jwt-old",
        cdp_url="wss://cdp/old",
        shell_relay_url="wss://shell/old",
    )
    transport = _FakeTransport(old)
    transport._wheelbase_connection_id = connection_id
    session = {
        "session_key": task_id,
        "transport": transport,
        "wheelbase_identity": old,
    }
    runtime_token = wb_runtime.set_task_identity(
        task_id,
        {**old.__dict__, "_connection_id": connection_id},
    )
    browser_tool.register_task_cdp_url(task_id, old.cdp_url)
    monkeypatch.setattr(server, "_sessions", {task_id: session})
    monkeypatch.setattr(server, "get_hermes_home", lambda: str(tmp_path))
    transport_token = bind_transport(transport)
    fallback_calls = []
    try:
        response = server.handle_request(
            {
                "id": "refresh",
                "method": "identity.update",
                "params": {
                    "jwt": "jwt-new",
                    "client": "desktop",
                    "device_id": "device-1",
                    "cdp_url": new_cdp,
                    "shell_relay_url": new_shell,
                },
            }
        )
        assert response["result"]["ok"] is True

        active_identity = wb_runtime.get_task_identity(task_id)
        assert active_identity["cdp_url"] == new_cdp
        assert active_identity["shell_relay_url"] == new_shell
        assert wb_runtime.current_identity()["cdp_url"] == new_cdp
        assert browser_tool._desktop_task_cdp_raw(task_id) == new_cdp

        if new_shell:
            relayed = {}
            monkeypatch.setattr(
                plugin,
                "_make_transport",
                lambda relay_url, identity: relayed.update(
                    relay_url=relay_url, identity=identity
                )
                or object(),
            )
            monkeypatch.setattr(
                plugin,
                "_relay_command",
                lambda *_args, **_kwargs: json.dumps({"success": True}),
            )
            shell_result = json.loads(
                plugin.route_or_passthrough(
                    tool_name="terminal",
                    args={"command": ":"},
                    next_call=lambda args: fallback_calls.append(args),
                    task_id=task_id,
                    tool_call_id="renewed-shell",
                )
            )
            assert shell_result["success"] is True
            assert relayed["relay_url"] == new_shell
            resolved_cdp = []
            monkeypatch.setattr(
                browser_tool,
                "_resolve_cdp_override",
                lambda raw: resolved_cdp.append(raw) or raw,
            )
            monkeypatch.setattr(
                browser_tool,
                "_find_agent_browser",
                lambda: (_ for _ in ()).throw(FileNotFoundError("diagnostic")),
            )
            browser_tool._run_browser_command(task_id, "snapshot", [])
            assert resolved_cdp == [new_cdp]
        else:
            shell_result = json.loads(
                plugin.route_or_passthrough(
                    tool_name="terminal",
                    args={"command": ":"},
                    next_call=lambda args: fallback_calls.append(args),
                    task_id=task_id,
                    tool_call_id="disconnected-shell",
                )
            )
            assert shell_result["error_code"] == "desktop_unavailable"
            browser_result = browser_tool._run_browser_command(
                task_id, "snapshot", []
            )
            assert browser_result["error_code"] == "desktop_unavailable"
        assert fallback_calls == []
    finally:
        reset_transport(transport_token)
        wb_runtime.reset_identity(runtime_token)
        wb_runtime.clear_task(task_id)
        browser_tool.register_task_cdp_url(task_id, "")


def test_identity_update_does_not_refresh_another_device_active_tasks(
    tmp_path, monkeypatch
):
    from tools import browser_tool

    d1 = WheelbaseIdentity(
        user_id="user-aaaa",
        tenant_id="t1",
        client="desktop",
        device_id="device-1",
        cdp_url="wss://cdp/d1-old",
        shell_relay_url="wss://shell/d1-old",
    )
    d2 = WheelbaseIdentity(
        user_id="user-aaaa",
        tenant_id="t1",
        client="desktop",
        device_id="device-2",
        cdp_url="wss://cdp/d2",
        shell_relay_url="wss://shell/d2",
    )
    t1 = _FakeTransport(d1)
    t1._wheelbase_connection_id = "connection-d1"
    t2 = _FakeTransport(d2)
    t2._wheelbase_connection_id = "connection-d2"
    task1, task2 = "active-d1", "active-d2"
    token1 = wb_runtime.set_task_identity(
        task1, {**d1.__dict__, "_connection_id": "connection-d1"}
    )
    token2 = wb_runtime.set_task_identity(
        task2, {**d2.__dict__, "_connection_id": "connection-d2"}
    )
    browser_tool.register_task_cdp_url(task1, d1.cdp_url)
    browser_tool.register_task_cdp_url(task2, d2.cdp_url)
    monkeypatch.setattr(
        server,
        "_sessions",
        {
            task1: {
                "session_key": task1,
                "transport": t1,
                "wheelbase_identity": d1,
            },
            task2: {
                "session_key": task2,
                "transport": t2,
                "wheelbase_identity": d2,
            },
        },
    )
    monkeypatch.setattr(server, "get_hermes_home", lambda: str(tmp_path))
    transport_token = bind_transport(t1)
    try:
        server.handle_request(
            {
                "id": "disconnect-d1",
                "method": "identity.update",
                "params": {
                    "jwt": "jwt-new",
                    "client": "desktop",
                    "device_id": "device-1",
                    "cdp_url": "",
                    "shell_relay_url": "",
                },
            }
        )
        assert wb_runtime.get_task_identity(task1)["shell_relay_url"] == ""
        assert browser_tool._desktop_task_cdp_raw(task1) == ""
        assert (
            wb_runtime.get_task_identity(task2)["shell_relay_url"]
            == d2.shell_relay_url
        )
        assert browser_tool._desktop_task_cdp_raw(task2) == d2.cdp_url
    finally:
        reset_transport(transport_token)
        wb_runtime.reset_identity(token2)
        wb_runtime.reset_identity(token1)
        for task_id in (task1, task2):
            wb_runtime.clear_task(task_id)
            browser_tool.register_task_cdp_url(task_id, "")


@pytest.mark.parametrize(
    "identity,relay_status,attempted,error_code",
    [
        (
            WheelbaseIdentity(
                user_id="user-aaaa",
                tenant_id="t1",
                client="desktop",
                device_id="device-1",
            ),
            {
                "version": 2,
                "client": "desktop",
                "device_id": "device-1",
                "cdp_relay_challenge": "failed",
                "shell_relay_challenge": "unavailable",
            },
            True,
            "desktop_unavailable",
        ),
        (
            WheelbaseIdentity(
                user_id="user-aaaa",
                tenant_id="t1",
                client="desktop",
                device_id="device-1",
                cdp_url="wss://cdp/online",
                shell_relay_url="wss://shell/online",
            ),
            {
                "version": 2,
                "client": "desktop",
                "device_id": "device-1",
                "cdp_relay_challenge": "passed",
                "shell_relay_challenge": "passed",
            },
            False,
            "challenge_passed",
        ),
        (
            WheelbaseIdentity(
                user_id="user-aaaa",
                tenant_id="t1",
                client="mobile",
            ),
            {
                "version": 2,
                "client": "mobile",
                "cdp_relay_challenge": "not_applicable",
                "shell_relay_challenge": "not_applicable",
            },
            False,
            "desktop_identity_required",
        ),
    ],
)
def test_runtime_probe_returns_safe_profile_and_fail_closed_evidence(
    identity, relay_status, attempted, error_code, tmp_path, _bound, monkeypatch
):
    from tools import browser_tool

    profile_home = (
        tmp_path
        / "tenants"
        / identity.tenant_id
        / "profiles"
        / f"wb-{identity.user_id}"
    )
    monkeypatch.setattr(server, "get_hermes_home", lambda: profile_home)
    monkeypatch.setattr(
        browser_tool,
        "_find_agent_browser",
        lambda: (_ for _ in ()).throw(AssertionError("host browser action")),
    )
    _bound(identity)

    response = server.handle_request(
        {
            "id": "probe",
            "method": "wheelbase.runtime.probe",
            "params": {"relay_status_v2": relay_status},
        }
    )

    result = response["result"]
    assert set(result) == {
        "version",
        "instance_fingerprint",
        "profile_fingerprint",
        "profile_scope_match",
        "relay_challenge",
        "desktop_policies",
    }
    assert result["version"] == 2
    assert result["profile_scope_match"] is True
    assert len(result["instance_fingerprint"]) == 20
    assert len(result["profile_fingerprint"]) == 20
    assert result["relay_challenge"] == {
        "client": relay_status["client"],
        "scope_match": True,
        "cdp_relay_challenge": relay_status["cdp_relay_challenge"],
        "shell_relay_challenge": relay_status["shell_relay_challenge"],
    }
    assert set(result["desktop_policies"]) == {"cdp", "shell"}
    for policy in result["desktop_policies"].values():
        assert policy == {
            "attempted": attempted,
            "error_code": error_code,
            "fallback_invocations": 0,
        }


def test_runtime_probe_rejects_relay_challenge_from_another_device(
    tmp_path, _bound, monkeypatch
):
    identity = WheelbaseIdentity(
        user_id="user-aaaa",
        tenant_id="t1",
        client="desktop",
        device_id="device-1",
    )
    monkeypatch.setattr(server, "get_hermes_home", lambda: tmp_path)
    _bound(identity)

    response = server.handle_request(
        {
            "id": "wrong-device",
            "method": "wheelbase.runtime.probe",
            "params": {
                "relay_status_v2": {
                    "version": 2,
                    "client": "desktop",
                    "device_id": "device-2",
                    "cdp_relay_challenge": "unavailable",
                    "shell_relay_challenge": "unavailable",
                }
            },
        }
    )
    assert response["error"]["code"] == 4032


@pytest.mark.parametrize(
    "relay_status",
    [
        {},
        {
            "version": 1,
            "client": "desktop",
            "device_id": "device-1",
            "cdp_relay_challenge": "passed",
            "shell_relay_challenge": "passed",
        },
        {
            "version": 2,
            "client": "desktop",
            "device_id": "device-1",
            "cdp_relay_challenge": "passed",
            "shell_relay_challenge": "passed",
            "nonce": "must-not-be-forwarded",
        },
        {
            "version": 2,
            "client": "mobile",
            "device_id": "device-1",
            "cdp_relay_challenge": "not_applicable",
            "shell_relay_challenge": "not_applicable",
        },
    ],
)
def test_runtime_probe_rejects_noncanonical_relay_status_v2(
    relay_status, tmp_path, _bound, monkeypatch
):
    identity = WheelbaseIdentity(
        user_id="user-aaaa",
        tenant_id="t1",
        client=str(relay_status.get("client") or "desktop"),
        device_id=(
            "device-1" if relay_status.get("client", "desktop") == "desktop" else ""
        ),
    )
    monkeypatch.setattr(server, "get_hermes_home", lambda: tmp_path)
    _bound(identity)

    response = server.handle_request(
        {
            "id": "bad-contract",
            "method": "wheelbase.runtime.probe",
            "params": {"relay_status_v2": relay_status},
        }
    )

    assert response["error"]["code"] == 4004


def test_runtime_probe_evaluates_lost_surface_independently(
    tmp_path, _bound, monkeypatch
):
    from tools import browser_tool

    identity = WheelbaseIdentity(
        user_id="user-aaaa",
        tenant_id="t1",
        client="desktop",
        device_id="device-1",
        cdp_url="wss://cdp/still-live",
        shell_relay_url="",
    )
    monkeypatch.setattr(server, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(
        browser_tool,
        "_find_agent_browser",
        lambda: (_ for _ in ()).throw(AssertionError("host browser action")),
    )
    _bound(identity)

    response = server.handle_request(
        {
            "id": "mixed",
            "method": "wheelbase.runtime.probe",
            "params": {
                "relay_status_v2": {
                    "version": 2,
                    "client": "desktop",
                    "device_id": "device-1",
                    "cdp_relay_challenge": "passed",
                    "shell_relay_challenge": "unavailable",
                }
            },
        }
    )

    assert response["result"]["desktop_policies"] == {
        "cdp": {
            "attempted": False,
            "error_code": "challenge_passed",
            "fallback_invocations": 0,
        },
        "shell": {
            "attempted": True,
            "error_code": "desktop_unavailable",
            "fallback_invocations": 0,
        },
    }


def test_prompt_background_fail_closed_on_injection_error(monkeypatch):
    """prompt.background must abort (not run unscoped) when injection fails."""
    import sys
    import time as _time
    import types

    agent_constructed = []
    fake_run_agent = types.ModuleType("run_agent")

    class _FakeAgent:
        def __init__(self, **kw):
            agent_constructed.append(kw)

        def run_conversation(self, **kw):
            return "ran"

    fake_run_agent.AIAgent = _FakeAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)
    monkeypatch.setattr(server, "_background_agent_kwargs", lambda agent, task_id: {})

    from tui_gateway import wheelbase_inject

    def _boom(task_id, ident, home, **kwargs):
        raise RuntimeError("sandbox unavailable")

    monkeypatch.setattr(wheelbase_inject, "apply_session_injection", _boom)

    events = []
    monkeypatch.setattr(server, "_emit", lambda ev, sid, payload=None: events.append((ev, payload)))

    sid = "bgtest1"
    server._sessions[sid] = {
        "agent": object(),
        "session_key": "bg-session-key",
        "wheelbase_identity": IDENT_A,
        "cwd": "/tmp",
        "history_lock": __import__("threading").Lock(),
    }
    try:
        resp = server.handle_request(
            {"id": 1, "method": "prompt.background", "params": {"session_id": sid, "text": "do it"}}
        )
        assert "result" in resp
        deadline = _time.time() + 5
        while _time.time() < deadline:
            if any(ev == "background.complete" for ev, _ in events):
                break
            _time.sleep(0.02)
        completes = [p for ev, p in events if ev == "background.complete"]
        assert completes, "background task never completed"
        assert "error" in completes[0]["text"]
        assert "sandbox unavailable" in completes[0]["text"]
        assert not agent_constructed, "agent ran despite failed injection (UNSCOPED EXECUTION)"
    finally:
        server._sessions.pop(sid, None)


@pytest.mark.parametrize("method", ["prompt.background", "preview.restart"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancelled"])
def test_ephemeral_tasks_clear_only_their_per_task_registries(
    method, outcome, tmp_path, _bound, monkeypatch
):
    import asyncio
    import sys
    import types

    from tools import browser_tool, terminal_tool
    from tui_gateway.wheelbase_inject import apply_session_injection

    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setattr(server, "get_hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_background_agent_kwargs", lambda *_args: {})
    monkeypatch.setattr(server, "_ephemeral_preview_agent_kwargs", lambda *_args: {})
    monkeypatch.setattr(server, "_preview_restart_callbacks", lambda *_args: {})
    parent_wb = WheelbaseIdentity(
        user_id=IDENT_A.user_id,
        tenant_id=IDENT_A.tenant_id,
        dealership_id=IDENT_A.dealership_id,
        jwt=IDENT_A.jwt,
        client="desktop",
        device_id="device-parent",
        cdp_url="wss://cdp/parent",
        shell_relay_url="wss://shell/parent",
    )
    sibling_wb = WheelbaseIdentity(
        user_id=IDENT_B.user_id,
        tenant_id=IDENT_B.tenant_id,
        dealership_id=IDENT_B.dealership_id,
        jwt=IDENT_B.jwt,
        client="desktop",
        device_id="device-sibling",
        cdp_url="wss://cdp/sibling",
        shell_relay_url="wss://shell/sibling",
    )
    _bound(parent_wb)

    parent_task = f"parent-{method}-{outcome}"
    sibling_task = f"sibling-{method}-{outcome}"
    sibling_cleanup = apply_session_injection(
        sibling_task, sibling_wb, tmp_path, connection_id="connection-sibling"
    )
    sibling_cleanup()
    parent_cleanup = apply_session_injection(
        parent_task, parent_wb, tmp_path, connection_id="connection-parent"
    )
    parent_identity = wb_runtime.get_task_identity(parent_task)
    sibling_identity = wb_runtime.get_task_identity(sibling_task)
    parent_terminal = dict(terminal_tool._task_env_overrides[parent_task])
    sibling_terminal = dict(terminal_tool._task_env_overrides[sibling_task])

    fake_run_agent = types.ModuleType("run_agent")

    class _FakeAgent:
        def __init__(self, **_kwargs):
            pass

        def run_conversation(self, **_kwargs):
            if outcome == "error":
                raise RuntimeError("ephemeral failure")
            if outcome == "cancelled":
                raise asyncio.CancelledError()
            return {"final_response": "done"}

    fake_run_agent.AIAgent = _FakeAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    escaped = []

    class _InlineThread:
        def __init__(self, *, target, **_kwargs):
            self._target = target

        def start(self):
            try:
                self._target()
            except BaseException as exc:
                escaped.append(exc)

    monkeypatch.setattr(server.threading, "Thread", _InlineThread)

    sid = f"session-{method}-{outcome}"
    server._sessions[sid] = {
        "agent": object(),
        "session_key": parent_task,
        "wheelbase_identity": parent_wb,
        "cwd": str(tmp_path),
        "history": [],
        "history_lock": threading.Lock(),
    }
    ephemeral_task = ""
    try:
        params = {"session_id": sid}
        if method == "prompt.background":
            params["text"] = "do it"
        else:
            params["url"] = "http://127.0.0.1:3000"
        response = server.handle_request(
            {"id": "ephemeral", "method": method, "params": params}
        )
        ephemeral_task = response["result"]["task_id"]

        assert wb_runtime.get_task_identity(ephemeral_task) is None
        assert ephemeral_task not in browser_tool._task_cdp_urls
        assert ephemeral_task not in terminal_tool._task_env_overrides
        assert ephemeral_task not in terminal_tool._session_cwd

        activation_token = wb_runtime.activate_task(ephemeral_task)
        try:
            assert wb_runtime.current_identity() is None
        finally:
            wb_runtime.reset_identity(activation_token)

        assert wb_runtime.get_task_identity(parent_task) == parent_identity
        assert wb_runtime.get_task_identity(sibling_task) == sibling_identity
        assert browser_tool._task_cdp_urls[parent_task] == parent_wb.cdp_url
        assert browser_tool._task_cdp_urls[sibling_task] == sibling_wb.cdp_url
        assert terminal_tool._task_env_overrides[parent_task] == parent_terminal
        assert terminal_tool._task_env_overrides[sibling_task] == sibling_terminal
        assert wb_runtime.current_identity() == parent_identity

        if outcome == "cancelled":
            assert len(escaped) == 1
            assert isinstance(escaped[0], asyncio.CancelledError)
        else:
            assert escaped == []
    finally:
        server._sessions.pop(sid, None)
        parent_cleanup()
        for task_id in (ephemeral_task, parent_task, sibling_task):
            if not task_id:
                continue
            wb_runtime.clear_task(task_id)
            browser_tool.register_task_cdp_url(task_id, "")
            terminal_tool.clear_task_env_overrides(task_id)
