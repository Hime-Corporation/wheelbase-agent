"""Tests for save_inventory_demand_labels (PostgREST write tool)."""

import json

import wheelbase_demand_matrix.tools.save_inventory_demand_labels as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, response=None):
        self._response = response
        self.calls = []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer=None):
        self.calls.append((method, table, body, params, prefer))
        return self._response

    def close(self):
        pass


VALID_LABELS = [
    {"inventoryCarId": "11111111-0000-0000-0000-000000000001", "key": "suv"},
    {"inventoryCarId": "22222222-0000-0000-0000-000000000002", "key": "sedan"},
]


class TestSaveInventoryDemandLabels:
    def test_calls_postgrest_write(self, monkeypatch):
        client = FakeClient(response=None)
        monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
        out = json.loads(mod.save_inventory_demand_labels({"labels": VALID_LABELS}))
        assert "error" not in out
        assert len(client.calls) == 1
        method, table, body, params, prefer = client.calls[0]
        assert method == "PATCH"
        assert table == "inventory_car"
        assert len(body) == 2
        assert body[0]["demand_category_key"] == "suv"

    def test_signed_out(self, monkeypatch):
        def boom():
            raise WheelbaseAuthError("no session")
        monkeypatch.setattr(mod, "WheelbaseClient", boom)
        out = json.loads(mod.save_inventory_demand_labels({"labels": VALID_LABELS}))
        assert out["error"] == "not_signed_in"

    def test_error_on_missing_labels(self):
        out = json.loads(mod.save_inventory_demand_labels({}))
        assert "error" in out

    def test_error_on_empty_labels(self):
        out = json.loads(mod.save_inventory_demand_labels({"labels": []}))
        assert "error" in out

    def test_error_on_missing_car_id(self):
        out = json.loads(
            mod.save_inventory_demand_labels({"labels": [{"key": "suv"}]})
        )
        assert "error" in out

    def test_error_on_missing_key(self):
        out = json.loads(
            mod.save_inventory_demand_labels(
                {"labels": [{"inventoryCarId": "abc", "key": ""}]}
            )
        )
        assert "error" in out

    def test_none_response_returns_labeled_count(self, monkeypatch):
        client = FakeClient(response=None)
        monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
        out = json.loads(mod.save_inventory_demand_labels({"labels": VALID_LABELS}))
        assert out["labeled"] == len(VALID_LABELS)

    def test_client_exception_returns_err(self, monkeypatch):
        class BoomClient:
            def postgrest_write(self, *a, **kw):
                raise RuntimeError("db error")
            def close(self):
                pass
        monkeypatch.setattr(mod, "WheelbaseClient", BoomClient)
        out = json.loads(mod.save_inventory_demand_labels({"labels": VALID_LABELS}))
        assert "error" in out
