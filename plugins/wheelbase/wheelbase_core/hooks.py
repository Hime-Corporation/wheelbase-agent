"""Wheelbase pre_tool_call hook — approval-gating for destructive tools.

When the ``WHEELBASE_APPROVAL_GATE`` environment variable is set to a
truthy value (``1``, ``true``, ``yes``, ``on``), two destructive
wheelbase tools require explicit user approval before the agent is
allowed to execute them:

  - ``send_to_vendor``
  - ``delete_work_item``

The hook returns ``{"action": "pending_approval", ...}`` for those tools.
``hermes_cli.plugins.get_pre_tool_call_block_message`` recognises that
action, emits an ``approval.request`` SSE event through the existing
gateway notify-callback machinery (``tools.approval``), blocks the agent
thread on a ``threading.Event`` until the client POSTs to
``POST /v1/runs/{run_id}/approval``, then:

  - resumes tool execution transparently on ``once`` / ``session`` /
    ``always`` — the hook returns ``None`` so the tool proceeds.
  - returns a block message on ``deny`` or timeout — the tool is
    cancelled and the agent surfaces the denial to the model.

When the flag is OFF (default), this hook returns ``None`` immediately
so existing tool execution is entirely unaffected.
"""

from __future__ import annotations

import logging
import os
import uuid

logger = logging.getLogger(__name__)

# Tools that require human approval before execution.
_APPROVAL_REQUIRED_TOOLS = frozenset({
    "send_to_vendor",
    "delete_work_item",
})

# Human-readable descriptions shown in the approval.request event.
_TOOL_DESCRIPTIONS = {
    "send_to_vendor": "Send a vehicle to an external vendor (irreversible dispatch)",
    "delete_work_item": "Permanently delete a work item and its child items (cascade)",
}

# Guard: read once at module import so the hook is O(1) per call.
_APPROVAL_GATE_ENABLED: bool = os.getenv(
    "WHEELBASE_APPROVAL_GATE", ""
).strip().lower() in {"1", "true", "yes", "on"}


def _pre_tool_call(
    tool_name: str,
    args: dict,
    **_kwargs,
) -> dict | None:
    """Hook callback registered on ``pre_tool_call``.

    Returns a ``pending_approval`` directive for gated destructive tools
    when the approval gate is enabled, or ``None`` (allow) otherwise.

    The returned dict is intentionally NOT ``{"action": "block"}`` — it
    uses the new ``pending_approval`` action so the caller in
    ``get_pre_tool_call_block_message`` can run the async approval flow
    rather than hard-blocking the tool immediately.
    """
    if not _APPROVAL_GATE_ENABLED:
        return None

    if tool_name not in _APPROVAL_REQUIRED_TOOLS:
        return None

    approval_id = uuid.uuid4().hex
    description = _TOOL_DESCRIPTIONS.get(tool_name, f"Execute destructive tool: {tool_name}")
    logger.debug(
        "wheelbase approval-gate: tool=%s approval_id=%s", tool_name, approval_id
    )
    return {
        "action": "pending_approval",
        "tool": tool_name,
        "args": args,
        "approval_id": approval_id,
        "description": description,
    }
