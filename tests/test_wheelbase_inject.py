"""Cross-bleed tests for per-session injection (B5, spec §5.4/§12).

Two users, two tasks: every injected resource (CDP URL, sandbox volume,
credential file, SDK context) must resolve to its own user and never the
other's.
"""
from __future__ import annotations

import json
import hashlib
import re

import pytest

from tools import browser_tool, browser_tool_cdp as browser_cdp, terminal_tool
from tui_gateway.wheelbase_identity import WheelbaseIdentity
from tui_gateway.wheelbase_inject import (
    apply_session_injection,
    cleanup_connection_credential,
    clear_task_credential_state,
    contain_workspace_path,
    user_sandbox_key,
    workspace_volume,
)
from wheelbase_sdk import runtime as wb_runtime

def _identity(*, user_id, tenant_id, **kwargs):
    jti = hashlib.sha256(f"{tenant_id}:{user_id}:{kwargs.get('device_id', '')}".encode()).hexdigest()
    return WheelbaseIdentity(user_id=user_id, tenant_id=tenant_id,
        jwt=kwargs.pop("jwt", f"jwt-{user_id}"), session_jti_hash=jti,
        credential_revision=kwargs.pop("credential_revision", 1),
        credential_expires_at=kwargs.pop("credential_expires_at", 9999999999), **kwargs)


IDENT_A = _identity(
    user_id="user-aaaa", tenant_id="t1", dealership_id="d1",
    jwt="jwt-a", cdp_url="http://internal:8091/internal/agent/cdp/user-aaaa",
)
IDENT_B = _identity(
    user_id="user-bbbb", tenant_id="t1", dealership_id="d2",
    jwt="jwt-b", cdp_url="http://internal:8091/internal/agent/cdp/user-bbbb",
)


@pytest.fixture(autouse=True)
def _sandboxed_env(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    # Resolver normally fetches /json/version; identity-resolve for tests.
    monkeypatch.setattr(browser_cdp, "_resolve_cdp_override", lambda u: u)
    yield
    for t in (
        "task-a",
        "task-b",
        "task-origin",
        "tenant-a-task",
        "tenant-b-task",
        "tenant-a-daytona",
        "tenant-b-daytona",
    ):
        browser_tool.register_task_cdp_url(t, "")
        terminal_tool._task_env_overrides.pop(t, None)
        wb_runtime.clear_task(t)
    wb_runtime._current.set(None)


def test_two_users_no_cross_bleed(tmp_path):
    cleanup_a = apply_session_injection("task-a", IDENT_A, tmp_path)
    cleanup_b = apply_session_injection("task-b", IDENT_B, tmp_path)

    # Browser: each task resolves its own relay URL.
    assert browser_cdp._get_cdp_override("task-a") == IDENT_A.cdp_url
    assert browser_cdp._get_cdp_override("task-b") == IDENT_B.cdp_url

    # Sandbox: each task mounts only its own user volume.
    vols_a = terminal_tool._task_env_overrides["task-a"]["docker_volumes"]
    vols_b = terminal_tool._task_env_overrides["task-b"]["docker_volumes"]
    assert vols_a == [
        f"{workspace_volume(IDENT_A.tenant_id, IDENT_A.user_id)}:/workspace"
    ]
    assert vols_b == [
        f"{workspace_volume(IDENT_B.tenant_id, IDENT_B.user_id)}:/workspace"
    ]
    assert (
        terminal_tool._task_env_overrides["task-a"]["docker_env"][
            "WHEELBASE_USER_ID"
        ]
        == IDENT_A.user_id
    )

    # Credentials: distinct files, distinct tokens.
    cred_a = json.loads((tmp_path / "wheelbase-sessions" / f"{IDENT_A.session_jti_hash}.json").read_text())
    cred_b = json.loads((tmp_path / "wheelbase-sessions" / f"{IDENT_B.session_jti_hash}.json").read_text())
    assert cred_a["access_token"] == "jwt-a"
    assert cred_b["access_token"] == "jwt-b"

    cleanup_a()
    cleanup_b()


def test_same_user_in_two_tenants_gets_distinct_external_resources(tmp_path, monkeypatch):
    same_user_a = _identity(user_id="shared-user", tenant_id="tenant-a")
    same_user_b = _identity(user_id="shared-user", tenant_id="tenant-b")

    apply_session_injection("tenant-a-task", same_user_a, tmp_path)()
    apply_session_injection("tenant-b-task", same_user_b, tmp_path)()
    vol_a = terminal_tool._task_env_overrides["tenant-a-task"]["docker_volumes"]
    vol_b = terminal_tool._task_env_overrides["tenant-b-task"]["docker_volumes"]
    assert vol_a != vol_b

    monkeypatch.setenv("TERMINAL_ENV", "daytona")
    apply_session_injection("tenant-a-daytona", same_user_a, tmp_path)()
    apply_session_injection("tenant-b-daytona", same_user_b, tmp_path)()
    key_a = terminal_tool._task_env_overrides["tenant-a-daytona"]["sandbox_key"]
    key_b = terminal_tool._task_env_overrides["tenant-b-daytona"]["sandbox_key"]
    assert key_a != key_b


def test_external_resource_keys_are_deterministic_bounded_and_safe(monkeypatch):
    monkeypatch.delenv("WHEELBASE_WORKSPACE_VOLUME_PREFIX", raising=False)
    monkeypatch.delenv("WHEELBASE_SANDBOX_KEY_PREFIX", raising=False)
    tenant_id = "t" * 64
    user_id = "u" * 64

    for key_factory in (workspace_volume, user_sandbox_key):
        first = key_factory(tenant_id, user_id)
        assert first == key_factory(tenant_id, user_id)
        assert len(first) <= 64
        assert re.fullmatch(r"[A-Za-z0-9_-]+", first)


def test_sdk_context_set_and_reset(tmp_path):
    cleanup = apply_session_injection("task-a", IDENT_A, tmp_path)
    ident = wb_runtime.current_identity()
    assert ident is not None
    assert ident["user_id"] == IDENT_A.user_id
    assert ident["credential_path"].endswith(f"{IDENT_A.session_jti_hash}.json")
    cleanup()
    assert wb_runtime.current_identity() is None, "cleanup must fail closed for thread reuse"


def test_sdk_task_identity_records_exact_connection_owner(tmp_path):
    cleanup = apply_session_injection(
        "task-origin",
        IDENT_A,
        tmp_path,
        connection_id="connection-d1",
    )
    try:
        assert (
            wb_runtime.get_task_identity("task-origin")["_connection_id"]
            == "connection-d1"
        )
    finally:
        cleanup()


def test_task_teardown_removes_only_exact_jti_credential(tmp_path, caplog):
    caplog.set_level("INFO")
    cleanup_a = apply_session_injection("task-a", IDENT_A, tmp_path)
    cleanup_b = apply_session_injection("task-b", IDENT_B, tmp_path)
    cleanup_a()
    cleanup_b()

    path_a = tmp_path / "wheelbase-sessions" / f"{IDENT_A.session_jti_hash}.json"
    path_b = tmp_path / "wheelbase-sessions" / f"{IDENT_B.session_jti_hash}.json"
    clear_task_credential_state("task-a", tmp_path, reason="test_teardown")

    assert not path_a.exists()
    assert path_b.exists()
    assert wb_runtime.get_task_identity("task-b") is not None
    signal = next(
        record.message for record in caplog.records
        if "wheelbase_identity_lifecycle" in record.message
    )
    assert '"event":"credential_cleanup"' in signal
    assert '"action":"deleted"' in signal
    assert IDENT_A.user_id not in signal
    assert IDENT_A.jwt not in signal
    assert IDENT_A.session_jti_hash not in signal


def test_task_teardown_retains_credential_while_same_jti_task_is_active(tmp_path):
    cleanup_a = apply_session_injection("task-a", IDENT_A, tmp_path)
    cleanup_origin = apply_session_injection("task-origin", IDENT_A, tmp_path)
    cleanup_a()
    cleanup_origin()
    path = tmp_path / "wheelbase-sessions" / f"{IDENT_A.session_jti_hash}.json"

    clear_task_credential_state("task-a", tmp_path, reason="test_teardown")
    assert path.exists()

    clear_task_credential_state("task-origin", tmp_path, reason="test_teardown")
    assert not path.exists()


def test_connection_cleanup_retains_active_jti_then_removes_when_unused(tmp_path):
    cleanup = apply_session_injection("task-a", IDENT_A, tmp_path)
    cleanup()
    path = tmp_path / "wheelbase-sessions" / f"{IDENT_A.session_jti_hash}.json"

    assert cleanup_connection_credential(IDENT_A, tmp_path, reason="disconnect") is False
    assert path.exists()

    wb_runtime.clear_task("task-a")
    assert cleanup_connection_credential(IDENT_A, tmp_path, reason="disconnect") is True
    assert not path.exists()


def test_sdk_context_carries_immutable_client_device_origin(tmp_path):
    identity = _identity(
        user_id="desktop-user",
        tenant_id="desktop-tenant",
        client="desktop",
        device_id="550e8400-e29b-41d4-a716-446655440000",
    )
    cleanup = apply_session_injection("task-origin", identity, tmp_path)
    try:
        scoped = wb_runtime.get_task_identity("task-origin")
        assert scoped["client"] == "desktop"
        assert scoped["device_id"] == identity.device_id
    finally:
        cleanup()
        wb_runtime.clear_task("task-origin")


def test_refused_without_docker_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.delenv("WHEELBASE_ALLOW_UNSANDBOXED", raising=False)
    with pytest.raises(RuntimeError, match="TERMINAL_ENV"):
        apply_session_injection("task-a", IDENT_A, tmp_path)
    # Dev escape hatch.
    monkeypatch.setenv("WHEELBASE_ALLOW_UNSANDBOXED", "1")
    apply_session_injection("task-a", IDENT_A, tmp_path)()


def test_daytona_mode_per_user_sandbox(tmp_path, monkeypatch):
    """In daytona mode each user gets ONE stable sandbox key (persistent
    workspace IS the sandbox); turns never resolve to another user's sandbox,
    and no docker volume is registered."""
    from tui_gateway.wheelbase_inject import user_sandbox_key

    monkeypatch.setenv("TERMINAL_ENV", "daytona")
    apply_session_injection("task-a", IDENT_A, tmp_path)()
    apply_session_injection("task-b", IDENT_B, tmp_path)()

    ov_a = terminal_tool._task_env_overrides["task-a"]
    ov_b = terminal_tool._task_env_overrides["task-b"]
    assert ov_a["sandbox_key"] == user_sandbox_key(
        IDENT_A.tenant_id, IDENT_A.user_id
    )
    assert ov_b["sandbox_key"] == user_sandbox_key(
        IDENT_B.tenant_id, IDENT_B.user_id
    )
    assert ov_a["sandbox_key"] != ov_b["sandbox_key"]
    # Daytona persists via the sandbox itself — no bind-mounted volume.
    assert "docker_volumes" not in ov_a and "docker_volumes" not in ov_b

    # Every turn for a user collapses to that user's own sandbox, never shared.
    assert terminal_tool._resolve_container_task_id("task-a") == user_sandbox_key(
        IDENT_A.tenant_id, IDENT_A.user_id
    )
    assert terminal_tool._resolve_container_task_id("task-b") == user_sandbox_key(
        IDENT_B.tenant_id, IDENT_B.user_id
    )


def test_conversation_cwd_docker(tmp_path):
    apply_session_injection(
        "task-a", IDENT_A, tmp_path, conversation_id="20260612_101500_ab12cd"
    )()
    overrides = terminal_tool._task_env_overrides["task-a"]
    assert overrides["cwd"] == "/workspace/conversations/20260612_101500_ab12cd"
    assert overrides["docker_volumes"] == [
        f"{workspace_volume(IDENT_A.tenant_id, IDENT_A.user_id)}:/workspace"
    ]


def test_conversation_cwd_daytona(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "daytona")
    apply_session_injection("task-a", IDENT_A, tmp_path, conversation_id="sess-1")()
    overrides = terminal_tool._task_env_overrides["task-a"]
    assert overrides["cwd"] == "/workspace/conversations/sess-1"
    assert overrides["sandbox_key"] == user_sandbox_key(IDENT_A.tenant_id, IDENT_A.user_id)


def test_no_conversation_id_falls_back_to_workspace_root(tmp_path):
    apply_session_injection("task-a", IDENT_A, tmp_path)()
    assert terminal_tool._task_env_overrides["task-a"]["cwd"] == "/workspace"


def test_invalid_conversation_id_rejected(tmp_path):
    with pytest.raises(ValueError):
        apply_session_injection("task-a", IDENT_A, tmp_path, conversation_id="../../etc")


def test_explicit_cwd_wins_over_conversation_default(tmp_path):
    apply_session_injection(
        "task-a",
        IDENT_A,
        tmp_path,
        conversation_id="sess-1",
        explicit_cwd="/workspace/shared/imports",
    )()
    assert terminal_tool._task_env_overrides["task-a"]["cwd"] == "/workspace/shared/imports"


def test_explicit_cwd_escape_fails_closed(tmp_path):
    with pytest.raises(ValueError):
        apply_session_injection(
            "task-a",
            IDENT_A,
            tmp_path,
            conversation_id="sess-1",
            explicit_cwd="/workspace/../etc",
        )


def test_contain_workspace_path():
    assert contain_workspace_path("/workspace") == "/workspace"
    assert contain_workspace_path("/workspace/foo/bar") == "/workspace/foo/bar"
    assert contain_workspace_path("/workspace/a/../b") == "/workspace/b"
    for bad in ("/etc", "/workspace/../etc", "../up", "workspace/foo", "", "/workspacefoo"):
        with pytest.raises(ValueError):
            contain_workspace_path(bad)


def test_live_env_cwd_change_invokes_ensure_cwd():
    class FakeEnv:
        cwd = "/workspace"
        ensured = 0

        def ensure_cwd(self):
            FakeEnv.ensured += 1

    env = FakeEnv()
    with terminal_tool._env_lock:
        terminal_tool._active_environments["task-live"] = env
    try:
        terminal_tool.register_task_env_overrides(
            "task-live", {"cwd": "/workspace/conversations/sess-9"}
        )
        assert env.cwd == "/workspace/conversations/sess-9"
        assert FakeEnv.ensured == 1
    finally:
        with terminal_tool._env_lock:
            terminal_tool._active_environments.pop("task-live", None)
        terminal_tool._task_env_overrides.pop("task-live", None)


def test_revision_refresh_only_touches_own_jti(tmp_path):
    apply_session_injection("task-a", IDENT_A, tmp_path)()
    apply_session_injection("task-b", IDENT_B, tmp_path)()
    refreshed = _identity(user_id=IDENT_A.user_id, tenant_id=IDENT_A.tenant_id,
        dealership_id=IDENT_A.dealership_id, jwt="jwt-a-rotated",
        credential_revision=2, cdp_url=IDENT_A.cdp_url)
    apply_session_injection("task-a", refreshed, tmp_path)()
    cred_a = json.loads((tmp_path / "wheelbase-sessions" / f"{IDENT_A.session_jti_hash}.json").read_text())
    cred_b = json.loads((tmp_path / "wheelbase-sessions" / f"{IDENT_B.session_jti_hash}.json").read_text())
    assert cred_a["access_token"] == "jwt-a-rotated"
    assert cred_b["access_token"] == "jwt-b"


def test_missing_cdp_url_clears_registration(tmp_path):
    apply_session_injection("task-a", IDENT_A, tmp_path)()
    no_browser = _identity(user_id="user-aaaa", tenant_id="t1", jwt="jwt-a", cdp_url="")
    apply_session_injection("task-a", no_browser, tmp_path)()
    # Falls back to env/config path (empty in tests -> empty string).
    assert browser_cdp._get_cdp_override("task-a") != IDENT_A.cdp_url


def test_rejects_anonymous_injection(tmp_path):
    with pytest.raises(ValueError):
        apply_session_injection("", IDENT_A, tmp_path)
    with pytest.raises(ValueError):
        apply_session_injection("task-a", WheelbaseIdentity(user_id=""), tmp_path)


def test_shell_relay_url_registered_for_task(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "daytona")
    from tui_gateway.wheelbase_identity import WheelbaseIdentity
    from wheelbase_sdk import runtime as wb_runtime

    identity = _identity(user_id="u1", tenant_id="t1", shell_relay_url="wss://relay/u1")
    cleanup = apply_session_injection("task-9", identity, tmp_path)
    try:
        ident = wb_runtime.get_task_identity("task-9")
        assert ident is not None
        assert ident["shell_relay_url"] == "wss://relay/u1"
    finally:
        cleanup()


# ─── Desktop exec relay exempts the sandboxed-TERMINAL_ENV requirement ──────
#
# A desktop session's shell/file tools run on the user's OWN machine via the
# wheelbase-desktop-exec plugin, not on the gateway host — so an identified
# session with a working relay must not be refused just because TERMINAL_ENV
# is "local" (the natural config once the relay is live). Mobile/offline
# sessions (no relay url) must keep hitting the exact refusal they always
# have; this bypass must never widen to them.


def test_desktop_relay_bypasses_sandbox_requirement(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.delenv("WHEELBASE_ALLOW_UNSANDBOXED", raising=False)
    identity = _identity(
        user_id="u-desktop",
        tenant_id="t1",
        shell_relay_url="wss://relay/u-desktop",
    )
    apply_session_injection("task-desktop", identity, tmp_path)()


def test_desktop_relay_with_already_sandboxed_env_still_works(tmp_path, monkeypatch):
    """A relay-available session on an ALSO-sandboxed gateway must not
    double-require anything extra — same as any other sandboxed session."""
    monkeypatch.setenv("TERMINAL_ENV", "daytona")
    identity = _identity(
        user_id="u-desktop",
        tenant_id="t1",
        shell_relay_url="wss://relay/u-desktop",
    )
    apply_session_injection("task-desktop", identity, tmp_path)()


def test_mobile_no_relay_url_still_refused_on_local(tmp_path, monkeypatch):
    """No shell_relay_url (mobile/offline) -> unchanged from today: refused."""
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.delenv("WHEELBASE_ALLOW_UNSANDBOXED", raising=False)
    identity = _identity(user_id="u-mobile", tenant_id="t1", shell_relay_url="")
    with pytest.raises(RuntimeError, match="TERMINAL_ENV"):
        apply_session_injection("task-mobile", identity, tmp_path)


def test_mobile_still_requires_sandboxed_env_regardless_of_desktop_bypass(tmp_path, monkeypatch):
    """Mobile keeps requiring a real sandboxed backend (daytona in practice) —
    confirms the desktop bypass is additive, not a general relaxation."""
    monkeypatch.setenv("TERMINAL_ENV", "daytona")
    identity = _identity(user_id="u-mobile", tenant_id="t1", shell_relay_url="")
    apply_session_injection("task-mobile", identity, tmp_path)()


def test_allow_unsandboxed_escape_hatch_unaffected_by_relay_change(tmp_path, monkeypatch):
    """The pre-existing dev/test escape hatch keeps working exactly as before
    for a session with NO relay url."""
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("WHEELBASE_ALLOW_UNSANDBOXED", "1")
    identity = _identity(user_id="u-dev", tenant_id="t1", shell_relay_url="")
    apply_session_injection("task-dev", identity, tmp_path)()
