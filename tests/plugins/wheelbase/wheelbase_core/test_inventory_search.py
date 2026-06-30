"""Tests for inventory_search tool."""

import json

import wheelbase_core.tools.inventory_search as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, rows=None, next_offset=None):
        self._rows = rows or []
        self._next_offset = next_offset
        self.last_table = None
        self.last_params = None
        self.last_limit = None
        self.last_offset = None

    def postgrest_get_page(self, table, params, *, limit, offset=0):
        self.last_table = table
        self.last_params = params
        self.last_limit = limit
        self.last_offset = offset
        return self._rows, self._next_offset

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Basic result shape
# ---------------------------------------------------------------------------

def test_inventory_search_returns_results_key(monkeypatch):
    rows = [
        {
            "id": "c1",
            "year": 2021,
            "make": "Honda",
            "model": "Civic",
            "stock_number": "S001",
            "status_id": 1,
            "asking_price_cents": 1500000,
        }
    ]
    client = FakeClient(rows)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.inventory_search({"query": "Honda"}))
    assert "results" in out
    assert len(out["results"]) == 1
    assert out["results"][0]["make"] == "Honda"
    assert out["results"][0]["stockNumber"] == "S001"
    assert out["results"][0]["askingPriceCents"] == 1500000
    assert client.last_table == "inventory_car"


def test_inventory_search_next_offset_returned(monkeypatch):
    client = FakeClient([], next_offset=50)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.inventory_search({"query": "Honda"}))
    assert out["nextOffset"] == 50


def test_inventory_search_next_offset_none_when_exhausted(monkeypatch):
    client = FakeClient([], next_offset=None)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.inventory_search({"query": "Honda"}))
    assert out["nextOffset"] is None


def test_inventory_search_resolves_status_label(monkeypatch):
    rows = [
        {
            "id": "c1",
            "year": 2021,
            "make": "Subaru",
            "model": "Impreza",
            "stock_number": "S001",
            "status_id": 14,
            "asking_price_cents": 1500000,
            "inventory_status_definition": {"code": "frontline", "label": "Frontline Ready"},
        }
    ]
    client = FakeClient(rows)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.inventory_search({"query": "Subaru"}))
    result = out["results"][0]
    assert result["statusId"] == 14
    assert result["status"] == "Frontline Ready"
    assert result["statusCode"] == "frontline"
    # the embed must be requested in the PostgREST select
    assert "inventory_status_definition(code,label)" in client.last_params["select"]


def test_inventory_search_status_label_none_when_unset(monkeypatch):
    rows = [{"id": "c1", "make": "Honda", "status_id": None}]
    client = FakeClient(rows)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.inventory_search({"query": "Honda"}))
    assert out["results"][0]["status"] is None
    assert out["results"][0]["statusCode"] is None


# ---------------------------------------------------------------------------
# Filter params
# ---------------------------------------------------------------------------

def test_inventory_search_or_filter_included_when_query_given(monkeypatch):
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
    assert client.last_limit == 10


def test_inventory_search_default_limit(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.inventory_search({"query": "x"})
    assert client.last_limit == 50


def test_inventory_search_offset_forwarded(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.inventory_search({"query": "x", "offset": 50})
    assert client.last_offset == 50


# ---------------------------------------------------------------------------
# Optional query (filter-only mode)
# ---------------------------------------------------------------------------

def test_inventory_search_no_query_is_allowed(monkeypatch):
    """inventory_search with no query should succeed and return recent inventory."""
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.inventory_search({}))
    assert "results" in out
    # No 'or' param when no query provided
    assert "or" not in client.last_params


def test_inventory_search_no_query_orders_by_created_at(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.inventory_search({})
    assert client.last_params.get("order") == "created_at.desc"


def test_inventory_search_no_query_with_make_filter(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.inventory_search({"make": "Toyota"}))
    assert "results" in out
    assert "make" in client.last_params
    assert "Toyota" in client.last_params["make"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_inventory_search_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.inventory_search({"query": "Honda"}))
    assert out["error"] == "not_signed_in"


def test_inventory_search_asking_price_cents_in_select(monkeypatch):
    """asking_price_cents (not legacy asking_price) must be in the select."""
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.inventory_search({"query": "Honda"})
    assert "asking_price_cents" in client.last_params["select"]
