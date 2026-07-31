import json

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
