"""Error type + JSON result helpers shared by every Wheelbase tool handler.

Tool handlers must ALWAYS return a JSON string and never raise; these helpers
make that uniform.
"""

from __future__ import annotations

import json
from typing import Any


class WheelbaseAuthError(Exception):
    """Raised by WheelbaseClient when there is no signed-in Supabase session."""


def signed_out_result() -> str:
    """Standard tool result when the user isn't signed in to Wheelbase."""
    return json.dumps(
        {
            "error": "not_signed_in",
            "message": "Sign in to Wheelbase to use this tool.",
        }
    )


def ok(data: Any) -> str:
    """Serialize a successful tool result. `default=str` tolerates odd types."""
    return json.dumps(data, default=str)


def err(message: str, **extra: Any) -> str:
    """Serialize a tool error result with an optional structured payload."""
    return json.dumps({"error": message, **extra})
