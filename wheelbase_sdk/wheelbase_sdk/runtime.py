"""Task-scoped identity registry for the multi-user cloud gateway.

The gateway's per-session injector calls set_task_identity(task_id, ...)
before each turn; tools running inside that turn resolve the credential via
the task context. contextvars propagate through asyncio tasks and
to_thread; the dashboard server's turn worker sets the context at turn
start in-thread.

SECURITY (spec §5.4 — highest-risk path): a ContextVar value set on an OS
thread persists across reuses of that thread. To prevent a later turn that
*omits* injection from inheriting the previous user's identity, the seam
must fail CLOSED: set_task_identity returns a reset token, and the injector
must call reset_identity(token) when the turn ends (finally). current's
value is also defensively cleared by reset even if the registry entry stays.
"""
from __future__ import annotations

import contextvars
import threading
from typing import Any, Optional

_current: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("wb_task_identity", default=None)
_by_task: dict[str, dict] = {}
_lock = threading.Lock()

DESKTOP_UNAVAILABLE_CODE = "desktop_unavailable"
DESKTOP_UNAVAILABLE_MESSAGE = (
    "The originating desktop is unavailable. Reconnect that desktop and retry."
)


def desktop_unavailable_result(*, detail: str = "") -> dict[str, Any]:
    """Stable fail-closed tool result shared by desktop shell/browser paths."""
    return {
        "output": "",
        "returncode": 1,
        "exit_code": 1,
        "success": False,
        "status": "error",
        "error_code": DESKTOP_UNAVAILABLE_CODE,
        "error": DESKTOP_UNAVAILABLE_MESSAGE,
        **({"detail": detail} if detail else {}),
    }


def set_task_identity(task_id: str, identity: dict[str, Any]) -> contextvars.Token:
    """Bind the current execution context to a task's identity.

    Returns a token the caller MUST pass to reset_identity() when the turn
    ends (in a finally block) so reused threads never leak identity.
    """
    scoped = dict(identity)
    with _lock:
        _by_task[task_id] = scoped
    # The ContextVar and task registry deliberately share this object. A
    # broker capability refresh mutates it under _lock so a turn already in
    # progress observes the new URLs through both current_identity() and the
    # by-task tool routing path.
    return _current.set(scoped)


def reset_identity(token: contextvars.Token) -> None:
    """Restore the pre-turn context (fail-closed cleanup for thread reuse)."""
    try:
        _current.reset(token)
    except ValueError:
        # Token from a different context (e.g. cross-thread misuse): the
        # safe fallback is still to clear, never to leave an identity bound.
        _current.set(None)


def activate_task(task_id: str) -> Optional[contextvars.Token]:
    """Re-bind the current execution context to a task's identity.

    Returns a reset token when an identity was found, else binds None and
    returns that token too — callers treat it the same as set_task_identity.
    """
    with _lock:
        ident = _by_task.get(task_id)
    return _current.set(ident)


def current_identity() -> Optional[dict]:
    ident = _current.get()
    with _lock:
        return dict(ident) if ident is not None else None


def get_task_identity(task_id: str) -> Optional[dict]:
    """Return a COPY of the identity bound to *task_id*, or None.

    Fail-closed accessor for the cloud-exec plugin (spec §5.2): an empty or
    unknown task_id yields None so the caller routes to the sandboxed cloud
    path rather than a mis-scoped local machine. By-id (not current_identity)
    so a reused worker thread cannot leak a stale ContextVar into the lookup.
    """
    if not task_id:
        return None
    with _lock:
        ident = _by_task.get(task_id)
    return dict(ident) if ident is not None else None


def refresh_connection_tasks(
    connection_id: str, refreshed: dict[str, Any]
) -> tuple[str, ...]:
    """Refresh active tasks owned by one exact broker connection.

    Immutable scope fields must match before a task is touched. The shared
    per-task dict is mutated in place so an already-running ContextVar sees
    the same atomic capability replacement as by-task tool lookups.
    """
    if not connection_id:
        return ()
    immutable = ("tenant_id", "user_id", "client", "device_id")
    refreshed_scope = {
        field: str(refreshed.get(field) or "").strip() for field in immutable
    }
    updated: list[str] = []
    with _lock:
        for task_id, current in _by_task.items():
            if current.get("_connection_id") != connection_id:
                continue
            current_scope = {
                field: str(current.get(field) or "").strip() for field in immutable
            }
            if current_scope != refreshed_scope:
                continue
            old_revision = current.get("credential_revision", 0)
            new_revision = refreshed.get("credential_revision", 0)
            if not isinstance(old_revision, int) or not isinstance(new_revision, int):
                continue
            if new_revision < old_revision:
                continue
            update = dict(
                cdp_url=str(refreshed.get("cdp_url") or "").strip(),
                shell_relay_url=str(
                    refreshed.get("shell_relay_url") or ""
                ).strip(),
            )
            if new_revision > old_revision:
                update.update(
                    jwt=str(refreshed.get("jwt") or "").strip(),
                    credential_path=str(refreshed.get("credential_path") or "").strip(),
                    credential_revision=new_revision,
                    credential_expires_at=refreshed.get("credential_expires_at"),
                    credential_source=str(refreshed.get("credential_source") or "").strip(),
                )
            current.update(update)
            updated.append(task_id)
    return tuple(updated)


def clear_task(task_id: str) -> None:
    with _lock:
        _by_task.pop(task_id, None)
