"""wheelbase_sdk — shared client for Wheelbase Hermes plugins.

Public, FROZEN API:
    load_session() -> WheelbaseSession | None
    WheelbaseSession(access_token, expires_at)
    WheelbaseClient(*, transport=None, timeout=20.0)
        .postgrest_get(table, params) -> list[dict]
        .postgrest_write(method, table, *, body, params, prefer) -> Any
        .go_api(method, path, *, body, params) -> Any
    WheelbaseAuthError
    signed_out_result() / ok(data) / err(message, **extra) -> str
"""

from .client import WheelbaseClient
from .errors import WheelbaseAuthError, err, ok, signed_out_result
from .session import WheelbaseSession, load_session
from .workspace import workspace_dir

__all__ = [
    "WheelbaseClient",
    "WheelbaseSession",
    "load_session",
    "WheelbaseAuthError",
    "signed_out_result",
    "ok",
    "err",
    "workspace_dir",
]
