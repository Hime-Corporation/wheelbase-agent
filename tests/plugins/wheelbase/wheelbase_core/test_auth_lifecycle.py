import json
import importlib

import pytest

import wheelbase_core.tools.inventory_search as inventory_search_module
from wheelbase_sdk.errors import WheelbaseAuthError


def test_auth_failure_during_tool_call_uses_signed_out_contract(monkeypatch):
    class RotatedClient:
        def postgrest_get_page(self, *args, **kwargs):
            raise WheelbaseAuthError("refresh_pending", reason="refresh_pending")

        def close(self):
            pass

    monkeypatch.setattr(inventory_search_module, "WheelbaseClient", RotatedClient)
    result = json.loads(inventory_search_module.inventory_search({"query": "civic"}))
    assert result == {
        "error": "not_signed_in",
        "message": "Sign in to Wheelbase to use this tool.",
    }


_TOOLS = [
    ("get_car", "get_car", {"carId": "car-1"}),
    ("inventory_search", "inventory_search", {"query": "civic"}),
    ("update_inventory_status", "update_inventory_status", {"carId": "car-1", "newStatusId": 2}),
    ("list_runlists", "list_runlists", {}),
    ("get_runlist_cars", "get_runlist_cars", {"runlistId": "run-1"}),
    ("assess_runlist", "assess_runlist", {"runlistId": "run-1"}),
    ("archive_runlist_cars", "archive_runlist_cars", {"runlistId": "run-1", "carIds": ["car-1"]}),
    ("create_work_item", "create_work_item", {"carId": "car-1", "title": "Inspect"}),
    ("get_work_item", "get_work_item", {"workItemId": "work-1"}),
    ("delete_work_item", "delete_work_item", {"workItemId": "work-1", "confirm": True}),
    ("list_inventory_statuses", "list_inventory_statuses", {}),
    ("create_inspection_note", "create_inspection_note", {"carId": "car-1", "note": "ok"}),
    ("bulk_inspect", "bulk_inspect", {"carIds": ["car-1"]}),
    ("list_vendors", "list_vendors", {}),
    ("get_vendor", "get_vendor", {"vendorId": "vendor-1"}),
    ("send_to_vendor", "send_to_vendor", {"workOrderId": "work-1", "vendorId": "vendor-1"}),
    ("generate_demand_score", "generate_demand_score", {}),
    ("get_recon_board", "get_recon_board", {"carId": "car-1"}),
    ("start_recon", "start_recon", {"carId": "car-1"}),
    ("recon_stage_tools", "complete_stage", {"stageId": "stage-1"}),
    ("recon_stage_tools", "update_stage", {"stageId": "stage-1", "status": "done"}),
    ("add_work_item_comment", "add_work_item_comment", {"workItemId": "work-1", "content": "note"}),
    ("query_work", "query_work", {}),
    ("inventory_stats", "get_inventory_stats", {}),
    ("inventory_stats", "get_inventory_filter_options", {}),
]


@pytest.mark.parametrize("module_name,handler_name,args", _TOOLS)
def test_every_client_tool_maps_mid_call_auth_failure(module_name, handler_name, args, monkeypatch):
    module = importlib.import_module(f"wheelbase_core.tools.{module_name}")

    class RotatedClient:
        def close(self):
            pass

        def __getattr__(self, _name):
            def fail(*_args, **_kwargs):
                raise WheelbaseAuthError("expired", reason="expired")
            return fail

    monkeypatch.setattr(module, "WheelbaseClient", RotatedClient)
    result = json.loads(getattr(module, handler_name)(args))
    assert result == {
        "error": "not_signed_in",
        "message": "Sign in to Wheelbase to use this tool.",
    }
