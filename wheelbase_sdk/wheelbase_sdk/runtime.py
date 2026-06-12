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


def set_task_identity(task_id: str, identity: dict[str, Any]) -> contextvars.Token:
    """Bind the current execution context to a task's identity.

    Returns a token the caller MUST pass to reset_identity() when the turn
    ends (in a finally block) so reused threads never leak identity.
    """
    with _lock:
        _by_task[task_id] = dict(identity)
    return _current.set(dict(identity))


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
    return _current.set(dict(ident) if ident else None)


def current_identity() -> Optional[dict]:
    return _current.get()


def clear_task(task_id: str) -> None:
    with _lock:
        _by_task.pop(task_id, None)
