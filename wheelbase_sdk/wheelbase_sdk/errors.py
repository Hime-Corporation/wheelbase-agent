"""Error type + JSON result helpers shared by every Wheelbase tool handler.

Tool handlers must ALWAYS return a JSON string and never raise; these helpers
make that uniform.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class WheelbaseAuthError(Exception):
    """Raised by WheelbaseClient when there is no signed-in Supabase session."""

    VALID_REASONS = frozenset({"not_signed_in", "expired", "refresh_pending"})

    def __init__(self, message: str = "not signed in", *, reason: str | None = None) -> None:
        inferred = reason or (message if message in self.VALID_REASONS else "not_signed_in")
        self.reason = inferred if inferred in self.VALID_REASONS else "not_signed_in"
        super().__init__(message)


class WheelbaseForbiddenError(Exception):
    """Raised when authentication succeeded but the action is not permitted."""


def log_auth_lifecycle(
    reason: str,
    *,
    source: str,
    revision: Any = None,
    expires_at: Any = None,
    status: int | None = None,
) -> None:
    """Emit a bounded auth-rejection signal without credentials or paths."""
    signal_reasons = WheelbaseAuthError.VALID_REASONS | {"forbidden"}
    normalized_reason = reason if reason in signal_reasons else "not_signed_in"
    normalized_source = source if source in {"task", "local", "request"} else "unknown"
    payload: dict[str, Any] = {
        "event": "credential_rejected",
        "reason": normalized_reason,
        "source": normalized_source,
    }
    if isinstance(revision, int) and not isinstance(revision, bool):
        payload["revision"] = revision
    if isinstance(expires_at, int) and not isinstance(expires_at, bool):
        now = int(time.time())
        payload["expiry_age_s"] = now - expires_at
        payload["expiry_skew_s"] = expires_at - now
    if isinstance(status, int):
        payload["status"] = status
    logger.warning(
        "wheelbase_auth_lifecycle %s",
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
    )


def signed_out_result() -> str:
    """Standard tool result when the user isn't signed in to Wheelbase."""
    return json.dumps(
        {
            "error": "not_signed_in",
            "message": "Sign in to Wheelbase to use this tool.",
        }
    )


def forbidden_result() -> str:
    """Standard tool result for an authenticated but unauthorized action."""
    return json.dumps(
        {
            "error": "forbidden",
            "message": "You do not have permission to use this Wheelbase action.",
        }
    )


def ok(data: Any) -> str:
    """Serialize a successful tool result. `default=str` tolerates odd types."""
    return json.dumps(data, default=str)


def err(message: str, **extra: Any) -> str:
    """Serialize a tool error result with an optional structured payload."""
    return json.dumps({"error": message, **extra})
