"""wheelbase-demand-matrix plugin — registration.

Tools are marker-gated: they are only visible to the model when
.wheelbase-demand-matrix-active exists in the agent workspace.
The pre_llm_call hook injects demand-matrix system context when active.
"""

import json
from pathlib import Path

from wheelbase_sdk import workspace_dir

from . import schemas
from .tools import (
    complete_demand_matrix_setup as complete_tool,
    propose_demand_targets as propose_tool,
    read_demand_matrix as rdm_tool,
    read_inventory_summary as ris_tool,
    read_unlabeled_cars as ruc_tool,
    save_demand_overrides as sdo_tool,
    save_inventory_demand_labels as sidl_tool,
)

_MARKER_FILENAME = ".wheelbase-demand-matrix-active"

# Demand-matrix addendum (ported from addendum.ts §12.3)
_ADDENDUM = "\n".join([
    "## Wheelbase Demand-Matrix Setup Mode",
    "",
    "You are guiding the dealer through demand-matrix setup. Goals:",
    "1. Confirm or revise target counts for each of the 13 demand categories based on their lot, location, and customer base.",
    "2. Auto-tag the first batch of unlabeled inventory cars with the categories you've defined.",
    "3. Save changes via the appropriate write tools.",
    "",
    "Use `read_demand_matrix` and `read_inventory_summary` for context.",
    "Use `propose_demand_targets` to present a structured proposal to the user;",
    "once accepted, call `save_demand_overrides` to persist.",
    "Use `read_unlabeled_cars` + `save_inventory_demand_labels` to bulk-label.",
    "End with `complete_demand_matrix_setup`.",
    "",
    "(These tools will be available in subsequent tasks; do not call them until they exist.)",
])


def _marker_active() -> bool:
    """Return True when the demand-matrix marker file exists in the workspace."""
    return (workspace_dir() / _MARKER_FILENAME).exists()


def _read_non_empty(path: Path) -> str | None:
    """Return file text if the file exists and is non-empty, else None."""
    try:
        text = path.read_text(encoding="utf-8")
        return text if text.strip() else None
    except OSError:
        return None


def _pre_llm_call(**kwargs) -> dict | None:  # noqa: ARG001
    """Inject demand-matrix context into every turn while the marker is active."""
    if not _marker_active():
        return None

    sections = [_ADDENDUM]

    ws = workspace_dir()
    wheelbase_dir = ws / ".wheelbase"
    if wheelbase_dir.is_dir():
        demand_matrix = _read_non_empty(wheelbase_dir / "demand-matrix.json")
        if demand_matrix is not None:
            sections.extend(["", "## demand-matrix.json:", "", demand_matrix])

        inventory_summary = _read_non_empty(wheelbase_dir / "inventory-summary.json")
        if inventory_summary is not None:
            sections.extend(["", "## inventory-summary.json:", "", inventory_summary])

        dealership_md = _read_non_empty(wheelbase_dir / "dealership.md")
        if dealership_md is not None:
            sections.extend(["", "## dealership.md:", "", dealership_md])

    return {"context": "\n".join(sections)}


def register(ctx):
    """Wire schemas to handlers, register the pre_llm_call hook."""
    ctx.register_tool(
        name="read_demand_matrix",
        toolset="wheelbase_demand_matrix",
        schema=schemas.READ_DEMAND_MATRIX,
        handler=rdm_tool.read_demand_matrix,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="read_inventory_summary",
        toolset="wheelbase_demand_matrix",
        schema=schemas.READ_INVENTORY_SUMMARY,
        handler=ris_tool.read_inventory_summary,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="read_unlabeled_cars",
        toolset="wheelbase_demand_matrix",
        schema=schemas.READ_UNLABELED_CARS,
        handler=ruc_tool.read_unlabeled_cars,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="propose_demand_targets",
        toolset="wheelbase_demand_matrix",
        schema=schemas.PROPOSE_DEMAND_TARGETS,
        handler=propose_tool.propose_demand_targets,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="save_demand_overrides",
        toolset="wheelbase_demand_matrix",
        schema=schemas.SAVE_DEMAND_OVERRIDES,
        handler=sdo_tool.save_demand_overrides,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="save_inventory_demand_labels",
        toolset="wheelbase_demand_matrix",
        schema=schemas.SAVE_INVENTORY_DEMAND_LABELS,
        handler=sidl_tool.save_inventory_demand_labels,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="complete_demand_matrix_setup",
        toolset="wheelbase_demand_matrix",
        schema=schemas.COMPLETE_DEMAND_MATRIX_SETUP,
        handler=complete_tool.complete_demand_matrix_setup,
        check_fn=_marker_active,
    )
    ctx.register_hook("pre_llm_call", _pre_llm_call)
