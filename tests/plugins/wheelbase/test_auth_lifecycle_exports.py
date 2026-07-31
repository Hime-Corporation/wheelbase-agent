"""Behavioral auth-boundary matrix for registered Wheelbase tool handlers."""
from __future__ import annotations

import importlib
import json

import pytest

from wheelbase_sdk.errors import WheelbaseAuthError, WheelbaseForbiddenError


_SIGNED_OUT = {
    "error": "not_signed_in",
    "message": "Sign in to Wheelbase to use this tool.",
}
_FORBIDDEN = {
    "error": "forbidden",
    "message": "You do not have permission to use this Wheelbase action.",
}


class _Registration:
    def __init__(self) -> None:
        self.handlers = {}

    def register_tool(self, *, name, handler, **_kwargs) -> None:
        self.handlers[name] = handler

    def register_hook(self, *_args, **_kwargs) -> None:
        pass


_EXPORTED_CLIENT_TOOLS = [
    ("wheelbase_core", "get_car", "wheelbase_core.tools.get_car", {"carId": "car-1"}),
    ("wheelbase_core", "inventory_search", "wheelbase_core.tools.inventory_search", {"query": "civic"}),
    ("wheelbase_core", "update_inventory_status", "wheelbase_core.tools.update_inventory_status", {"carId": "car-1", "newStatusId": 2}),
    ("wheelbase_core", "list_runlists", "wheelbase_core.tools.list_runlists", {}),
    ("wheelbase_core", "get_runlist_cars", "wheelbase_core.tools.get_runlist_cars", {"runlistId": "run-1"}),
    ("wheelbase_core", "assess_runlist", "wheelbase_core.tools.assess_runlist", {"runlistId": "run-1"}),
    ("wheelbase_core", "archive_runlist_cars", "wheelbase_core.tools.archive_runlist_cars", {"runlistId": "run-1", "carIds": ["car-1"]}),
    ("wheelbase_core", "create_work_item", "wheelbase_core.tools.create_work_item", {"carId": "car-1", "title": "Inspect"}),
    ("wheelbase_core", "get_work_item", "wheelbase_core.tools.get_work_item", {"workItemId": "work-1"}),
    ("wheelbase_core", "delete_work_item", "wheelbase_core.tools.delete_work_item", {"workItemId": "work-1", "confirm": True}),
    ("wheelbase_core", "list_inventory_statuses", "wheelbase_core.tools.list_inventory_statuses", {}),
    ("wheelbase_core", "create_inspection_note", "wheelbase_core.tools.create_inspection_note", {"carId": "car-1", "note": "ok"}),
    ("wheelbase_core", "bulk_inspect", "wheelbase_core.tools.bulk_inspect", {"carIds": ["car-1"]}),
    ("wheelbase_core", "list_vendors", "wheelbase_core.tools.list_vendors", {}),
    ("wheelbase_core", "get_vendor", "wheelbase_core.tools.get_vendor", {"vendorId": "vendor-1"}),
    ("wheelbase_core", "send_to_vendor", "wheelbase_core.tools.send_to_vendor", {"workOrderId": "work-1", "vendorId": "vendor-1"}),
    ("wheelbase_core", "generate_demand_score", "wheelbase_core.tools.generate_demand_score", {}),
    ("wheelbase_core", "get_recon_board", "wheelbase_core.tools.get_recon_board", {"carId": "car-1"}),
    ("wheelbase_core", "start_recon", "wheelbase_core.tools.start_recon", {"carId": "car-1"}),
    ("wheelbase_core", "complete_stage", "wheelbase_core.tools.recon_stage_tools", {"stageId": "stage-1"}),
    ("wheelbase_core", "update_stage", "wheelbase_core.tools.recon_stage_tools", {"stageId": "stage-1", "status": "done"}),
    ("wheelbase_core", "create_finding", "wheelbase_core.tools.create_work_item", {"parentId": "stage-1", "title": "Dent"}),
    ("wheelbase_core", "add_work_item_comment", "wheelbase_core.tools.add_work_item_comment", {"workItemId": "work-1", "content": "note"}),
    ("wheelbase_core", "query_work", "wheelbase_core.tools.query_work", {}),
    ("wheelbase_core", "get_inventory_stats", "wheelbase_core.tools.inventory_stats", {}),
    ("wheelbase_core", "get_inventory_filter_options", "wheelbase_core.tools.inventory_stats", {}),
    ("wheelbase_demand_matrix", "save_demand_overrides", "wheelbase_demand_matrix.tools.save_demand_overrides", {"overrides": [{"key": "suv", "target": 10}]}),
    ("wheelbase_demand_matrix", "save_inventory_demand_labels", "wheelbase_demand_matrix.tools.save_inventory_demand_labels", {"labels": [{"inventoryCarId": "car-1", "key": "suv"}]}),
    ("wheelbase_demand_matrix", "complete_demand_matrix_setup", "wheelbase_demand_matrix.tools.complete_demand_matrix_setup", {}),
    ("wheelbase_dealercenter_import", "dc_ingest", "wheelbase_dealercenter_import.tools.dc_ingest", {"path": "/safe/export.csv", "dryRun": False}),
]


def _registered_handler(plugin_name: str, tool_name: str):
    registration = _Registration()
    importlib.import_module(plugin_name).register(registration)
    return registration.handlers[tool_name]


@pytest.mark.parametrize(
    "plugin_name,tool_name,client_module_name,args", _EXPORTED_CLIENT_TOOLS
)
@pytest.mark.parametrize("phase", ["construction", "call"])
@pytest.mark.parametrize("status", [401, 403])
def test_registered_client_tools_preserve_auth_and_forbidden_results(
    plugin_name,
    tool_name,
    client_module_name,
    args,
    phase,
    status,
    monkeypatch,
):
    client_module = importlib.import_module(client_module_name)
    if tool_name == "dc_ingest":
        monkeypatch.setattr(client_module, "parse_export", lambda _path: [{}])
        monkeypatch.setattr(
            client_module,
            "normalize_rows",
            lambda _raw: ([{"vin": "TESTVIN"}], []),
        )

    def failure():
        if status == 403:
            return WheelbaseForbiddenError("forbidden")
        return WheelbaseAuthError("not_signed_in", reason="not_signed_in")

    class FailingClient:
        def __init__(self):
            if phase == "construction":
                raise failure()

        def close(self):
            pass

        def __getattr__(self, _name):
            def fail(*_args, **_kwargs):
                raise failure()

            return fail

    monkeypatch.setattr(client_module, "WheelbaseClient", FailingClient)
    result = json.loads(_registered_handler(plugin_name, tool_name)(args))

    assert result == (_SIGNED_OUT if status == 401 else _FORBIDDEN)
