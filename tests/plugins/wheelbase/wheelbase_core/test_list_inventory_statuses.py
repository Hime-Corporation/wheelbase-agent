"""Tests for list_inventory_statuses tool."""

import json

import wheelbase_core.tools.list_inventory_statuses as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, rows=None):
        self._rows = rows if rows is not None else [
            {"id": 1, "code": "available", "label": "Available", "sort_order": 1},
            {"id": 2, "code": "recon", "label": "In Recon", "sort_order": 2},
        ]
        self.last_params: dict = {}

    def postgrest_get(self, table, params):
        self.last_params = dict(params)
        if table == "inventory_status_definition":
            return list(self._rows)
        return []

    def close(self):
        pass


def test_list_inventory_statuses_queries_correct_table(monkeypatch):
    """Must query inventory_status_definition (not a different table)."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.list_inventory_statuses({}))
    assert "error" not in out
    assert "statuses" in out


def test_list_inventory_statuses_returns_list(monkeypatch):
    """statuses value must be a list of definition rows."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.list_inventory_statuses({}))
    assert isinstance(out["statuses"], list)
    assert len(out["statuses"]) == 2


def test_list_inventory_statuses_select_includes_sort_order(monkeypatch):
    """select must include sort_order so callers can display statuses in order."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.list_inventory_statuses({})
    assert "sort_order" in client.last_params.get("select", "")


def test_list_inventory_statuses_orders_by_sort_order(monkeypatch):
    """order param must sort ascending by sort_order."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.list_inventory_statuses({})
    assert "sort_order.asc" in client.last_params.get("order", "")


def test_list_inventory_statuses_select_includes_code_and_label(monkeypatch):
    """select must include code, label, and id."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.list_inventory_statuses({})
    select = client.last_params.get("select", "")
    for col in ("id", "code", "label"):
        assert col in select, f"Missing column in select: {col}"


def test_list_inventory_statuses_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.list_inventory_statuses({}))
    assert out["error"] == "not_signed_in"


def test_list_inventory_statuses_empty_table(monkeypatch):
    """Empty table should return empty statuses list, not an error."""
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(rows=[]))
    out = json.loads(mod.list_inventory_statuses({}))
    assert "error" not in out
    assert out["statuses"] == []
