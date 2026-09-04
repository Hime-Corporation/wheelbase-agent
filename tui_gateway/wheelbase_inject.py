"""Per-session injection: bind one turn's task_id to one user's identity.

Called from the dashboard server's pre-turn seam (_run_prompt_submit),
before the agent loop runs. This is the highest-risk path in the cloud
design (spec §5.4) — a mis-resolved identity here is cross-user data
exposure. Keep it small, deterministic, and covered by the cross-bleed
tests in tests/test_wheelbase_inject.py.

Fail-closed rules:
- Any unexpected error raises; the caller must abort the turn rather than
  run it unscoped.
- An identified session on a gateway whose terminal env is not docker is
  refused outright (shell tools would run unsandboxed on the gateway host)
  unless WHEELBASE_ALLOW_UNSANDBOXED=1 (dev/tests only).
- The returned cleanup callable resets the SDK identity context so reused
  worker threads never leak identity into a later turn.
"""
from __future__ import annotations

import hashlib
import logging
import os
import posixpath
import re
from pathlib import Path
from typing import Callable, Optional

from tui_gateway.wheelbase_identity import (
    WheelbaseIdentity,
    remove_credential_file,
    write_credential_file,
)

logger = logging.getLogger(__name__)

SANDBOX_MOUNT = "/workspace"
_CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_SCOPE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def contain_workspace_path(raw: str) -> str:
    """Normalize and require a sandbox cwd to remain under /workspace."""
    candidate = posixpath.normpath(str(raw or "").strip())
    if not candidate.startswith("/"):
        raise ValueError(f"cwd must be an absolute sandbox path: {raw!r}")
    if candidate != SANDBOX_MOUNT and not candidate.startswith(SANDBOX_MOUNT + "/"):
        raise ValueError(f"cwd escapes the {SANDBOX_MOUNT} sandbox: {raw!r}")
    return candidate


def conversation_cwd(conversation_id: str) -> str:
    if not _CONVERSATION_ID_RE.match(conversation_id or ""):
        raise ValueError(f"invalid conversation id: {conversation_id!r}")
    return f"{SANDBOX_MOUNT}/conversations/{conversation_id}"

# Terminal backends that isolate shell execution off the gateway host, so a
# multi-user turn cannot read the gateway's filesystem or another user's data:
#   docker      — per-user container (+ per-user volume) on a scoped daemon
#   daytona     — per-user cloud sandbox (own kernel/fs/network), spec §7
#   modal       — per-task serverless sandbox
#   singularity — rootless container
# `local` runs ON the gateway host and `ssh` lands every user on one shared
# remote host — both are unsafe for multi-tenant and are refused below.
SANDBOXED_TERMINAL_ENVS = frozenset({"docker", "daytona", "modal", "singularity"})


def _terminal_env() -> str:
    return (os.environ.get("TERMINAL_ENV", "local") or "local").strip().lower()


def _execution_scope_key(tenant_id: str, user_id: str) -> str:
    """Bounded deterministic external-resource key for one tenant/user pair."""
    if not _SCOPE_COMPONENT_RE.fullmatch(tenant_id or ""):
        raise ValueError("execution scope requires a valid tenant_id")
    if not _SCOPE_COMPONENT_RE.fullmatch(user_id or ""):
        raise ValueError("execution scope requires a valid user_id")
    digest = hashlib.sha256(f"{tenant_id}\0{user_id}".encode()).hexdigest()[:16]
    return f"t-{tenant_id[:12]}-u-{user_id[:12]}-{digest}"


def workspace_volume(tenant_id: str, user_id: str) -> str:
    """Stable tenant/user Docker volume name (persistent workspace)."""
    prefix = os.environ.get("WHEELBASE_WORKSPACE_VOLUME_PREFIX", "wb-ws-")
    return f"{prefix}{_execution_scope_key(tenant_id, user_id)}"


def user_sandbox_key(tenant_id: str, user_id: str) -> str:
    """Stable tenant/user sandbox key (daytona/non-volume backends).

    Returned by ``terminal_tool._resolve_container_task_id`` so every turn for
    one user reuses ONE sandbox (``hermes-<key>``) instead of spawning a fresh
    per-turn sandbox. Persistence for daytona is the sandbox itself (stop/start
    preserves the filesystem), not a separate mounted volume as in docker mode.
    """
    prefix = os.environ.get("WHEELBASE_SANDBOX_KEY_PREFIX", "wb-")
    return f"{prefix}{_execution_scope_key(tenant_id, user_id)}"


def _require_sandboxed_env(shell_relay_url: str = "") -> str:
    """Refuse an identified turn unless the terminal backend isolates execution
    off the gateway host — UNLESS the session has a desktop exec relay
    available (``shell_relay_url`` set), in which case the routed shell/file
    tools run on the user's OWN machine instead of the gateway host (the
    ``wheelbase-desktop-exec`` plugin), so the gateway's own TERMINAL_ENV is
    not a multi-tenant isolation concern for them. Mobile/offline sessions
    (no relay url) are unaffected — this bypass never applies to them.
    Returns the resolved terminal env either way (the caller still uses it to
    decide docker-volume vs. daytona-sandbox-key overrides for any tool that
    isn't relayed)."""
    terminal_env = _terminal_env()
    if shell_relay_url:
        logger.debug(
            "identified session has a desktop exec relay available; "
            "waiving the sandboxed-TERMINAL_ENV requirement (TERMINAL_ENV=%s)",
            terminal_env,
        )
        return terminal_env
    if terminal_env in SANDBOXED_TERMINAL_ENVS:
        return terminal_env
    if os.environ.get("WHEELBASE_ALLOW_UNSANDBOXED", "") == "1":
        logger.warning(
            "identified session running WITHOUT a sandbox (TERMINAL_ENV=%s, "
            "WHEELBASE_ALLOW_UNSANDBOXED=1 — dev/test only)",
            terminal_env,
        )
        return terminal_env
    raise RuntimeError(
        f"multi-user session refused: TERMINAL_ENV={terminal_env!r} is not "
        f"sandboxed; must be one of {sorted(SANDBOXED_TERMINAL_ENVS)} on a cloud "
        "gateway (spec §7); set WHEELBASE_ALLOW_UNSANDBOXED=1 only for local dev"
    )


def clear_ephemeral_task_state(task_id: str, hermes_home: Path | None = None) -> None:
    """Forget every task-keyed registration owned by one ephemeral worker.

    Background and preview task IDs are never resumed. Their terminal,
    browser, and SDK registrations must therefore disappear when the worker
    exits, without disturbing the durable parent session or sibling tasks.
    Each registry cleanup is idempotent and attempted independently so one
    cleanup failure cannot leave the other capability pointers reachable.
    """
    if not task_id:
        return

    try:
        from hermes_constants import get_hermes_home
        clear_task_credential_state(
            task_id,
            Path(hermes_home) if hermes_home is not None else Path(get_hermes_home()),
            reason="ephemeral_task_end",
        )
    except Exception:
        logger.exception("failed to clear ephemeral Wheelbase runtime task")

    try:
        from tools.browser_tool import register_task_cdp_url

        register_task_cdp_url(task_id, "")
    except Exception:
        logger.exception("failed to clear ephemeral browser task")

    try:
        from tools.terminal_tool import clear_task_env_overrides

        clear_task_env_overrides(task_id)
    except Exception:
        logger.exception("failed to clear ephemeral terminal task")


def clear_task_credential_state(
    task_id: str, hermes_home: Path, *, reason: str
) -> bool:
    """Release one task and unlink only its now-unused JTI credential."""
    if not task_id:
        return False
    try:
        from wheelbase_sdk import runtime as wb_runtime
    except ImportError:
        return False

    deleted = False

    def remove_exact(jti_hash: str) -> None:
        nonlocal deleted
        deleted = remove_credential_file(hermes_home, jti_hash)

    removed, retained = wb_runtime.release_task(
        task_id, on_credential_unused=remove_exact
    )
    if removed is not None:
        from tui_gateway.wheelbase_identity import log_identity_lifecycle

        log_identity_lifecycle(
            "credential_cleanup",
            removed,
            reason=reason,
            connection_id=str(removed.get("_connection_id") or ""),
            action="retained" if retained else ("deleted" if deleted else "absent"),
        )
    return deleted


def cleanup_connection_credential(
    identity: WheelbaseIdentity | None,
    hermes_home: Path,
    *,
    reason: str,
    connection_id: str = "",
) -> bool:
    """Unlink a disconnected connection's credential when no task retains it."""
    if identity is None or not identity.session_jti_hash:
        return False
    try:
        from wheelbase_sdk import runtime as wb_runtime
    except ImportError:
        return False

    deleted = False

    def remove_exact(jti_hash: str) -> None:
        nonlocal deleted
        deleted = remove_credential_file(hermes_home, jti_hash)

    unused = wb_runtime.cleanup_if_credential_unused(
        identity.session_jti_hash, remove_exact
    )
    from tui_gateway.wheelbase_identity import log_identity_lifecycle

    log_identity_lifecycle(
        "credential_cleanup",
        identity,
        reason=reason,
        connection_id=connection_id,
        action="deleted" if deleted else ("absent" if unused else "retained"),
    )
    return deleted


def apply_session_injection(
    task_id: str,
    identity: WheelbaseIdentity,
    hermes_home: Path,
    *,
    conversation_id: Optional[str] = None,
    explicit_cwd: Optional[str] = None,
    connection_id: str = "",
) -> Callable[[], None]:
    """Scope the upcoming turn to *identity*. Returns a cleanup callable the
    turn's finally block MUST invoke (resets the SDK context for thread reuse).
    """
    if not task_id or identity is None or not identity.user_id or not identity.tenant_id:
        raise ValueError(
            "session injection requires a task_id and an identified tenant/user"
        )

    terminal_env = _require_sandboxed_env(identity.shell_relay_url or "")

    # 1. Session-scoped Supabase JWT for the Wheelbase SDK (RLS scoping,
    #    spec §5.1.1). Freshest JWT wins (identity.update may have rotated it).
    cred_path = write_credential_file(hermes_home, identity)

    sdk_token = None
    try:
        from wheelbase_sdk import runtime as wb_runtime
    except ImportError:
        # SDK plugin not installed (bare dev gateway). Wheelbase tools are
        # absent too, so there is no credential consumer — safe to proceed.
        wb_runtime = None
        logger.debug("wheelbase_sdk not installed; skipping task identity context")
    if wb_runtime is not None:
        sdk_token = wb_runtime.set_task_identity(
            task_id,
            {
                "user_id": identity.user_id,
                "tenant_id": identity.tenant_id,
                "dealership_id": identity.dealership_id,
                "credential_path": str(cred_path),
                "session_jti_hash": identity.session_jti_hash,
                "credential_revision": identity.credential_revision,
                "credential_expires_at": identity.credential_expires_at,
                "credential_source": identity.credential_source,
                "shell_relay_url": identity.shell_relay_url or "",
                "cdp_url": identity.cdp_url or "",
                "client": identity.client or "",
                "device_id": identity.device_id or "",
                "_connection_id": str(connection_id or ""),
            },
        )

    # 2. Per-user browser endpoint (backend internal CDP relay, spec §6).
    #    No cdp_url (desktop offline / browserless user) → ensure no stale
    #    registration survives from a previous identity arrangement.
    from tools.browser_tool import register_task_cdp_url

    register_task_cdp_url(task_id, identity.cdp_url or "")

    # 3. Per-tenant/user sandbox (spec §7). register_task_env_overrides MERGES,
    #    so the dashboard's cwd registration earlier in the turn is overridden
    #    by ours, not lost. The override shape depends on how the backend persists:
    #      - daytona: persistence IS the sandbox. Pin a stable tenant/user
    #        sandbox_key so every turn reuses ONE scoped cloud sandbox rather
    #        than spawning a per-turn sandbox; there is no separate volume to
    #        mount. sandbox_key is an isolation key, so
    #        _resolve_container_task_id returns it and the env caches per scope.
    #      - docker (and other volume-mount backends): per-turn container that
    #        mounts the tenant/user scope's own named volume at /workspace.
    from tools.terminal_tool import register_task_env_overrides

    if explicit_cwd:
        cwd = contain_workspace_path(explicit_cwd)
    elif conversation_id:
        cwd = conversation_cwd(conversation_id)
    else:
        cwd = SANDBOX_MOUNT

    sandbox_env = {
        "WHEELBASE_TENANT_ID": identity.tenant_id,
        "WHEELBASE_USER_ID": identity.user_id,
        "WHEELBASE_DEALERSHIP_ID": identity.dealership_id,
    }
    if terminal_env == "daytona":
        register_task_env_overrides(
            task_id,
            {
                "cwd": cwd,
                "sandbox_key": user_sandbox_key(identity.tenant_id, identity.user_id),
                "docker_env": sandbox_env,
            },
        )
    else:
        register_task_env_overrides(
            task_id,
            {
                "cwd": cwd,
                "docker_volumes": [
                    f"{workspace_volume(identity.tenant_id, identity.user_id)}:{SANDBOX_MOUNT}"
                ],
                "docker_env": sandbox_env,
            },
        )

    def cleanup() -> None:
        if wb_runtime is not None and sdk_token is not None:
            wb_runtime.reset_identity(sdk_token)

    return cleanup
