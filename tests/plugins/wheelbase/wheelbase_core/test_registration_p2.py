"""Phase-2 registration smoke test.

Verifies that:
  - The nine new Phase-2 tools ARE registered after register(ctx) is called.
  - The Phase-1 tools are still registered (no regression).
  - Required-param schema sanity for the new tools.
"""

import wheelbase_core


class _FakeCtx:
    """Minimal fake context that records register_tool and register_hook calls."""

    def __init__(self):
        self.tools: dict[str, dict] = {}
        self.hooks: list[str] = []

    def register_tool(self, *, name: str, toolset: str, schema: dict, handler):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_hook(self, hook_name: str, callback):
        self.hooks.append(hook_name)


def _run():
    ctx = _FakeCtx()
    wheelbase_core.register(ctx)
    return ctx


_PHASE_2_TOOLS = [
    "get_recon_board",
    "start_recon",
    "complete_stage",
    "update_stage",
    "create_finding",
    "add_work_item_comment",
    "query_work",
    "get_inventory_stats",
    "get_inventory_filter_options",
]

_PHASE_1_TOOLS = [
    "get_car",
    "inventory_search",
    "update_inventory_status",
    "list_runlists",
    "get_runlist_cars",
    "assess_runlist",
    "archive_runlist_cars",
    "create_work_item",
    "get_work_item",
    "delete_work_item",
    "list_inventory_statuses",
    "create_inspection_note",
    "bulk_inspect",
    "list_vendors",
    "get_vendor",
    "send_to_vendor",
    "generate_demand_score",
]


# ---------------------------------------------------------------------------
# New Phase-2 tools MUST be registered
# ---------------------------------------------------------------------------

def test_all_phase2_tools_registered():
    ctx = _run()
    for name in _PHASE_2_TOOLS:
        assert name in ctx.tools, f"{name} must be registered"
        assert ctx.tools[name]["toolset"] == "wheelbase", f"{name} must be in wheelbase toolset"


# ---------------------------------------------------------------------------
# Phase-1 tools must still be registered (no regression)
# ---------------------------------------------------------------------------

def test_phase1_tools_still_registered():
    ctx = _run()
    for name in _PHASE_1_TOOLS:
        assert name in ctx.tools, f"Phase-1 tool {name} must still be registered"


# ---------------------------------------------------------------------------
# Schema sanity checks — required params
# ---------------------------------------------------------------------------

def test_get_recon_board_requires_car_id():
    ctx = _run()
    schema = ctx.tools["get_recon_board"]["schema"]
    assert schema["parameters"]["required"] == ["carId"]


def test_start_recon_requires_car_id():
    ctx = _run()
    schema = ctx.tools["start_recon"]["schema"]
    assert schema["parameters"]["required"] == ["carId"]


def test_complete_stage_requires_stage_id():
    ctx = _run()
    schema = ctx.tools["complete_stage"]["schema"]
    assert schema["parameters"]["required"] == ["stageId"]


def test_update_stage_requires_stage_id():
    ctx = _run()
    schema = ctx.tools["update_stage"]["schema"]
    assert schema["parameters"]["required"] == ["stageId"]


def test_create_finding_requires_parent_and_title():
    ctx = _run()
    schema = ctx.tools["create_finding"]["schema"]
    assert "parentId" in schema["parameters"]["required"]
    assert "title" in schema["parameters"]["required"]


def test_add_work_item_comment_requires_work_item_and_content():
    ctx = _run()
    schema = ctx.tools["add_work_item_comment"]["schema"]
    assert "workItemId" in schema["parameters"]["required"]
    assert "content" in schema["parameters"]["required"]


def test_query_work_has_no_required():
    ctx = _run()
    schema = ctx.tools["query_work"]["schema"]
    assert schema["parameters"]["required"] == []


def test_get_inventory_stats_has_no_required():
    ctx = _run()
    schema = ctx.tools["get_inventory_stats"]["schema"]
    assert schema["parameters"]["required"] == []


def test_get_inventory_filter_options_has_no_required():
    ctx = _run()
    schema = ctx.tools["get_inventory_filter_options"]["schema"]
    assert schema["parameters"]["required"] == []
