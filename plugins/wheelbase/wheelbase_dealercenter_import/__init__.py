"""wheelbase-dealercenter-import plugin — registration.

All tools are marker-gated: they are hidden from the model unless the file
`.wheelbase-dealercenter-import-active` exists in the agent workspace directory
(resolved from TERMINAL_CWD or cwd).

A pre_tool_call hook gates dc_ingest behind the WHEELBASE_APPROVAL_GATE,
mirroring the pattern used by wheelbase_core/hooks.py for destructive tools.
"""

import logging
import os
import uuid
from pathlib import Path

from wheelbase_sdk import workspace_dir

from . import schemas
from .tools import dc_connect as dc_connect_mod
from .tools import dc_export_historic as dc_export_historic_mod
from .tools import dc_ingest as dc_ingest_mod

logger = logging.getLogger(__name__)

_MARKER_FILENAME = ".wheelbase-dealercenter-import-active"

# Tools that require human approval before execution when the gate is on.
_APPROVAL_REQUIRED_TOOLS = frozenset({"dc_ingest"})

_TOOL_DESCRIPTIONS = {
    "dc_ingest": "Import historic DealerCenter inventory records into Wheelbase (bulk write)",
}

# Guard: read once at module import so the hook is O(1) per call.
_APPROVAL_GATE_ENABLED: bool = os.getenv(
    "WHEELBASE_APPROVAL_GATE", ""
).strip().lower() in {"1", "true", "yes", "on"}


def _marker_active() -> bool:
    """Return True when the dealercenter-import marker file is present in the workspace."""
    try:
        return (workspace_dir() / _MARKER_FILENAME).exists()
    except Exception:  # noqa: BLE001
        return False


def _pre_tool_call(
    tool_name: str,
    args: dict,
    **_kwargs,
) -> dict | None:
    """Hook callback registered on pre_tool_call.

    Returns a pending_approval directive for dc_ingest when the approval gate
    is enabled and dryRun is False, or None (allow) otherwise.
    """
    if not _APPROVAL_GATE_ENABLED:
        return None

    if tool_name not in _APPROVAL_REQUIRED_TOOLS:
        return None

    # Allow dry-runs without approval — they are read-only.
    if args.get("dryRun", True):
        return None

    approval_id = uuid.uuid4().hex
    description = _TOOL_DESCRIPTIONS.get(tool_name, f"Execute tool: {tool_name}")
    logger.debug(
        "wheelbase_dealercenter_import approval-gate: tool=%s approval_id=%s",
        tool_name,
        approval_id,
    )
    return {
        "action": "pending_approval",
        "tool": tool_name,
        "args": args,
        "approval_id": approval_id,
        "description": description,
    }


def register(ctx) -> None:
    """Wire schemas to handlers and register the pre_tool_call hook."""
    ctx.register_tool(
        name="dc_connect",
        toolset="wheelbase_dealercenter_import",
        schema=schemas.DC_CONNECT,
        handler=dc_connect_mod.dc_connect,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="dc_export_historic",
        toolset="wheelbase_dealercenter_import",
        schema=schemas.DC_EXPORT_HISTORIC,
        handler=dc_export_historic_mod.dc_export_historic,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="dc_ingest",
        toolset="wheelbase_dealercenter_import",
        schema=schemas.DC_INGEST,
        handler=dc_ingest_mod.dc_ingest,
        check_fn=_marker_active,
    )

    ctx.register_hook("pre_tool_call", _pre_tool_call)
