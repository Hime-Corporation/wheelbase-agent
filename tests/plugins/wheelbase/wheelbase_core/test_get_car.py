"""Reference test pattern: monkeypatch the module-level WheelbaseClient seam."""

import json

import wheelbase_core.tools.get_car as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, rows, fin_rows=None):
        self._rows = rows
        self._fin_rows = fin_rows if fin_rows is not None else []

    def postgrest_get(self, table, params):
        if table == "inventory_car":
            assert params["id"].startswith("eq.")
            return self._rows
        if table == "inventory_car_financials":
            return self._fin_rows
        return []

    def close(self):
        pass


def test_get_car_returns_row(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient([{"id": "c1", "make": "Toyota"}]))
    out = json.loads(mod.get_car({"carId": "c1"}))
    assert out["make"] == "Toyota"
    # photo_urls is always present (empty list when no photos)
    assert out["photo_urls"] == []


def test_get_car_flattens_status_label(monkeypatch):
    row = {
        "id": "c1",
        "make": "Audi",
        "status_id": 12,
        "inventory_status_definition": {"code": "recon", "label": "Reconditioning"},
        "inventory_photo": [
            {"url": "https://cdn.example.com/side.jpg", "label": "Side", "is_main": False, "sort_order": 2},
            {"url": "https://cdn.example.com/front.jpg", "label": "Front", "is_main": True, "sort_order": 1},
        ],
    }
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient([row]))
    out = json.loads(mod.get_car({"carId": "c1"}))
    assert out["status_id"] == 12
    assert out["status"] == "Reconditioning"
    assert out["status_code"] == "recon"
    # nested embed object is flattened away
    assert "inventory_status_definition" not in out
    # photo_urls: main photo first, then by sort_order
    assert out["photo_urls"] == [
        "https://cdn.example.com/front.jpg",
        "https://cdn.example.com/side.jpg",
    ]
    assert "inventory_photo" not in out


def test_get_car_status_none_when_unset(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient([{"id": "c1", "status_id": None}]))
    out = json.loads(mod.get_car({"carId": "c1"}))
    assert out["status"] is None
    assert out["status_code"] is None


def test_get_car_not_found(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient([]))
    out = json.loads(mod.get_car({"carId": "missing"}))
    assert out["error"].startswith("Vehicle not found")


def test_get_car_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.get_car({"carId": "c1"}))
    assert out["error"] == "not_signed_in"


def test_get_car_validates_car_id():
    out = json.loads(mod.get_car({"carId": ""}))
    assert out["error"].startswith("carId")




# ---------------------------------------------------------------------------
# Financials view (inventory_car_financials)
# ---------------------------------------------------------------------------

def test_get_car_returns_financials_key(monkeypatch):
    """get_car must include a 'financials' key in the response."""
    fin_row = {
        "total_cost_cents": 1500000,
        "gross_profit_cents": 300000,
        "margin_pct": 0.20,
        "days_in_stock": 15,
    }
    client = FakeClient(rows=[{"id": "c1", "make": "Toyota", "status_id": None}], fin_rows=[fin_row])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.get_car({"carId": "c1"}))
    assert "error" not in out
    assert "financials" in out


def test_get_car_financials_contains_expected_keys(monkeypatch):
    """financials must include total_cost_cents, gross_profit_cents, margin_pct, days_in_stock."""
    fin_row = {
        "total_cost_cents": 1500000,
        "gross_profit_cents": 300000,
        "margin_pct": 0.20,
        "days_in_stock": 15,
    }
    client = FakeClient(rows=[{"id": "c1", "make": "Toyota", "status_id": None}], fin_rows=[fin_row])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.get_car({"carId": "c1"}))
    fin = out["financials"]
    for key in ("total_cost_cents", "gross_profit_cents", "margin_pct", "days_in_stock"):
        assert key in fin, f"Missing financials key: {key}"
    assert fin["margin_pct"] == 0.20


def test_get_car_financials_empty_when_view_returns_nothing(monkeypatch):
    """If the financials view returns no rows, financials is {} (not an error)."""
    client = FakeClient(rows=[{"id": "c1", "make": "Ford", "status_id": None}], fin_rows=[])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.get_car({"carId": "c1"}))
    assert "error" not in out
    assert out["financials"] == {}


def test_register_wires_get_car():
    import wheelbase_core

    registered = {}
    hooks = {}

    class Ctx:
        def register_tool(self, *, name, toolset, schema, handler):
            registered[name] = (toolset, schema, handler)

        def register_hook(self, hook_name, callback):
            hooks[hook_name] = callback

    wheelbase_core.register(Ctx())
    assert "get_car" in registered
    assert registered["get_car"][0] == "wheelbase"
