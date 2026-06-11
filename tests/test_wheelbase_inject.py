"""Cross-bleed tests for per-session injection (B5, spec §5.4/§12).

Two users, two tasks: every injected resource (CDP URL, sandbox volume,
credential file, SDK context) must resolve to its own user and never the
other's.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tools import browser_tool, terminal_tool
from tui_gateway.wheelbase_identity import WheelbaseIdentity, update_user_jwt
from tui_gateway.wheelbase_inject import apply_session_injection, workspace_volume

# The wheelbase_sdk plugin lives in the sibling wheelbase-app repo; make it
# importable when the umbrella checkout is present so the SDK context path
# is exercised too (otherwise those assertions are skipped).
_SDK_DIR = Path(__file__).resolve().parents[2] / "wheelbase-app" / "hermes-plugins" / "wheelbase_sdk"
if _SDK_DIR.is_dir() and str(_SDK_DIR) not in sys.path:
    sys.path.insert(0, str(_SDK_DIR))
try:
    from wheelbase_sdk import runtime as wb_runtime
except ImportError:  # pragma: no cover - bare checkout
    wb_runtime = None

IDENT_A = WheelbaseIdentity(
    user_id="user-aaaa", tenant_id="t1", dealership_id="d1",
    jwt="jwt-a", cdp_url="http://internal:8091/internal/agent/cdp/user-aaaa",
)
IDENT_B = WheelbaseIdentity(
    user_id="user-bbbb", tenant_id="t1", dealership_id="d2",
    jwt="jwt-b", cdp_url="http://internal:8091/internal/agent/cdp/user-bbbb",
)


@pytest.fixture(autouse=True)
def _sandboxed_env(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    # Resolver normally fetches /json/version; identity-resolve for tests.
    monkeypatch.setattr(browser_tool, "_resolve_cdp_override", lambda u: u)
    yield
    for t in ("task-a", "task-b"):
        browser_tool.register_task_cdp_url(t, "")
        terminal_tool._task_env_overrides.pop(t, None)
    if wb_runtime is not None:
        wb_runtime.clear_task("task-a")
        wb_runtime.clear_task("task-b")
        wb_runtime._current.set(None)


def test_two_users_no_cross_bleed(tmp_path):
    cleanup_a = apply_session_injection("task-a", IDENT_A, tmp_path)
    cleanup_b = apply_session_injection("task-b", IDENT_B, tmp_path)

    # Browser: each task resolves its own relay URL.
    assert browser_tool._get_cdp_override("task-a") == IDENT_A.cdp_url
    assert browser_tool._get_cdp_override("task-b") == IDENT_B.cdp_url

    # Sandbox: each task mounts only its own user volume.
    vols_a = terminal_tool._task_env_overrides["task-a"]["docker_volumes"]
    vols_b = terminal_tool._task_env_overrides["task-b"]["docker_volumes"]
    assert vols_a == [f"{workspace_volume(IDENT_A.user_id)}:/workspace"]
    assert vols_b == [f"{workspace_volume(IDENT_B.user_id)}:/workspace"]
    assert terminal_tool._task_env_overrides["task-a"]["docker_env"]["WHEELBASE_USER_ID"] == IDENT_A.user_id

    # Credentials: distinct files, distinct tokens.
    cred_a = json.loads((tmp_path / "wheelbase-sessions" / "user-aaaa.json").read_text())
    cred_b = json.loads((tmp_path / "wheelbase-sessions" / "user-bbbb.json").read_text())
    assert cred_a["access_token"] == "jwt-a"
    assert cred_b["access_token"] == "jwt-b"

    cleanup_a()
    cleanup_b()


@pytest.mark.skipif(wb_runtime is None, reason="wheelbase_sdk not importable")
def test_sdk_context_set_and_reset(tmp_path):
    cleanup = apply_session_injection("task-a", IDENT_A, tmp_path)
    ident = wb_runtime.current_identity()
    assert ident is not None
    assert ident["user_id"] == IDENT_A.user_id
    assert ident["credential_path"].endswith("user-aaaa.json")
    cleanup()
    assert wb_runtime.current_identity() is None, "cleanup must fail closed for thread reuse"


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
    assert ov_a["sandbox_key"] == user_sandbox_key(IDENT_A.user_id)
    assert ov_b["sandbox_key"] == user_sandbox_key(IDENT_B.user_id)
    assert ov_a["sandbox_key"] != ov_b["sandbox_key"]
    # Daytona persists via the sandbox itself — no bind-mounted volume.
    assert "docker_volumes" not in ov_a and "docker_volumes" not in ov_b

    # Every turn for a user collapses to that user's own sandbox, never shared.
    assert terminal_tool._resolve_container_task_id("task-a") == user_sandbox_key(IDENT_A.user_id)
    assert terminal_tool._resolve_container_task_id("task-b") == user_sandbox_key(IDENT_B.user_id)


def test_jwt_refresh_only_touches_own_user(tmp_path):
    apply_session_injection("task-a", IDENT_A, tmp_path)()
    apply_session_injection("task-b", IDENT_B, tmp_path)()
    update_user_jwt(IDENT_A.user_id, "jwt-a-rotated")
    try:
        apply_session_injection("task-a", IDENT_A, tmp_path)()
        cred_a = json.loads((tmp_path / "wheelbase-sessions" / "user-aaaa.json").read_text())
        cred_b = json.loads((tmp_path / "wheelbase-sessions" / "user-bbbb.json").read_text())
        assert cred_a["access_token"] == "jwt-a-rotated"
        assert cred_b["access_token"] == "jwt-b"
    finally:
        update_user_jwt(IDENT_A.user_id, "")


def test_missing_cdp_url_clears_registration(tmp_path):
    apply_session_injection("task-a", IDENT_A, tmp_path)()
    no_browser = WheelbaseIdentity(user_id="user-aaaa", jwt="jwt-a", cdp_url="")
    apply_session_injection("task-a", no_browser, tmp_path)()
    # Falls back to env/config path (empty in tests -> empty string).
    assert browser_tool._get_cdp_override("task-a") != IDENT_A.cdp_url


def test_rejects_anonymous_injection(tmp_path):
    with pytest.raises(ValueError):
        apply_session_injection("", IDENT_A, tmp_path)
    with pytest.raises(ValueError):
        apply_session_injection("task-a", WheelbaseIdentity(user_id=""), tmp_path)
