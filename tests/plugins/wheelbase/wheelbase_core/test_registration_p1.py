"""Phase-1 registration smoke test.

Verifies that:
  - The four new work-item tools ARE registered after register(ctx) is called.
  - The three obsolete work_order tools are NOT registered.
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


# ---------------------------------------------------------------------------
# New tools MUST be registered
# ---------------------------------------------------------------------------

def test_create_work_item_registered():
    ctx = _run()
    assert "create_work_item" in ctx.tools, "create_work_item must be registered"
    assert ctx.tools["create_work_item"]["toolset"] == "wheelbase"


def test_get_work_item_registered():
    ctx = _run()
    assert "get_work_item" in ctx.tools, "get_work_item must be registered"
    assert ctx.tools["get_work_item"]["toolset"] == "wheelbase"


def test_delete_work_item_registered():
    ctx = _run()
    assert "delete_work_item" in ctx.tools, "delete_work_item must be registered"
    assert ctx.tools["delete_work_item"]["toolset"] == "wheelbase"


def test_list_inventory_statuses_registered():
    ctx = _run()
    assert "list_inventory_statuses" in ctx.tools, "list_inventory_statuses must be registered"
    assert ctx.tools["list_inventory_statuses"]["toolset"] == "wheelbase"


# ---------------------------------------------------------------------------
# Old work_order tools must NOT be registered
# ---------------------------------------------------------------------------

def test_create_work_order_not_registered():
    ctx = _run()
    assert "create_work_order" not in ctx.tools, "create_work_order must not be registered (table dropped)"


def test_get_work_order_not_registered():
    ctx = _run()
    assert "get_work_order" not in ctx.tools, "get_work_order must not be registered (table dropped)"


def test_delete_work_order_not_registered():
    ctx = _run()
    assert "delete_work_order" not in ctx.tools, "delete_work_order must not be registered (table dropped)"


# ---------------------------------------------------------------------------
# Schema sanity checks
# ---------------------------------------------------------------------------

def test_create_work_item_schema_has_title_required():
    ctx = _run()
    schema = ctx.tools["create_work_item"]["schema"]
    assert "title" in schema["parameters"]["required"]


def test_delete_work_item_schema_has_work_item_id_required():
    ctx = _run()
    schema = ctx.tools["delete_work_item"]["schema"]
    assert "workItemId" in schema["parameters"]["required"]


def test_list_inventory_statuses_schema_has_no_required():
    ctx = _run()
    schema = ctx.tools["list_inventory_statuses"]["schema"]
    assert schema["parameters"]["required"] == []
