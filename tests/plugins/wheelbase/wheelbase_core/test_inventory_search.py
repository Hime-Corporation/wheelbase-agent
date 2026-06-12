"""Tests for inventory_search tool."""

import json

import wheelbase_core.tools.inventory_search as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.last_table = None
        self.last_params = None

    def postgrest_get(self, table, params):
        self.last_table = table
        self.last_params = params
        return self._rows

    def close(self):
        pass


def test_inventory_search_returns_summaries(monkeypatch):
    rows = [
        {
            "id": "c1",
            "year": 2021,
            "make": "Honda",
            "model": "Civic",
            "stock_number": "S001",
            "status_id": 1,
            "asking_price": 15000,
        }
    ]
    client = FakeClient(rows)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.inventory_search({"query": "Honda"}))
    assert len(out) == 1
    assert out[0]["make"] == "Honda"
    assert out[0]["stockNumber"] == "S001"
    assert client.last_table == "inventory_car"


def test_inventory_search_resolves_status_label(monkeypatch):
    rows = [
        {
            "id": "c1",
            "year": 2021,
            "make": "Subaru",
            "model": "Impreza",
            "stock_number": "S001",
            "status_id": 14,
            "asking_price": 15000,
            "inventory_status_definition": {"code": "frontline", "label": "Frontline Ready"},
        }
    ]
    client = FakeClient(rows)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.inventory_search({"query": "Subaru"}))
    assert out[0]["statusId"] == 14
    assert out[0]["status"] == "Frontline Ready"
    assert out[0]["statusCode"] == "frontline"
    # the embed must be requested in the PostgREST select
    assert "inventory_status_definition(code,label)" in client.last_params["select"]


def test_inventory_search_status_label_none_when_unset(monkeypatch):
    rows = [{"id": "c1", "make": "Honda", "status_id": None}]
    client = FakeClient(rows)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.inventory_search({"query": "Honda"}))
    assert out[0]["status"] is None
    assert out[0]["statusCode"] is None


def test_inventory_search_or_filter_included(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.inventory_search({"query": "civic"})
    assert "or" in client.last_params
    assert "civic" in client.last_params["or"]


def test_inventory_search_make_filter(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.inventory_search({"query": "x", "make": "Toyota"})
    assert "make" in client.last_params
    assert "Toyota" in client.last_params["make"]


def test_inventory_search_status_id_filter(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.inventory_search({"query": "x", "statusId": 3})
    assert client.last_params.get("status_id") == "eq.3"


def test_inventory_search_year_range(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.inventory_search({"query": "x", "yearRange": {"min": 2018, "max": 2022}})
    assert "and" in client.last_params
    assert "2018" in client.last_params["and"]
    assert "2022" in client.last_params["and"]


def test_inventory_search_limit(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.inventory_search({"query": "x", "limit": 10})
    assert client.last_params["limit"] == "10"


def test_inventory_search_default_limit(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.inventory_search({"query": "x"})
    assert client.last_params["limit"] == "50"


def test_inventory_search_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.inventory_search({"query": "Honda"}))
    assert out["error"] == "not_signed_in"


def test_inventory_search_missing_query():
    out = json.loads(mod.inventory_search({}))
    assert "error" in out
    assert "query" in out["error"]
