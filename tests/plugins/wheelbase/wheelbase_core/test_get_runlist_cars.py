"""Tests for get_runlist_cars tool."""

import json

import wheelbase_core.tools.get_runlist_cars as mod
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


def test_get_runlist_cars_returns_summaries(monkeypatch):
    rows = [
        {
            "id": "rc1",
            "runlist_id": "rl1",
            "inventory_car_id": "c1",
            "year": 2020,
            "make": "Toyota",
            "model": "Camry",
            "vin": "VIN001",
            "stock_number": "S001",
            "archived_at": None,
        }
    ]
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(rows))
    out = json.loads(mod.get_runlist_cars({"runlistId": "rl1"}))
    assert len(out) == 1
    assert out[0]["make"] == "Toyota"
    assert out[0]["vin"] == "VIN001"


def test_get_runlist_cars_queries_correct_table(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.get_runlist_cars({"runlistId": "rl1"})
    assert client.last_table == "runlist_cars_view"
    assert client.last_params["runlist_id"] == "eq.rl1"
    assert client.last_params["archived_at"] == "is.null"


def test_get_runlist_cars_make_filter(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.get_runlist_cars({"runlistId": "rl1", "make": "Honda"})
    assert "make" in client.last_params
    assert "Honda" in client.last_params["make"]


def test_get_runlist_cars_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.get_runlist_cars({"runlistId": "rl1"}))
    assert out["error"] == "not_signed_in"


def test_get_runlist_cars_missing_runlist_id():
    out = json.loads(mod.get_runlist_cars({}))
    assert "error" in out
    assert "runlistId" in out["error"]


def test_get_runlist_cars_distinct_runlist_car_id_and_car_id(monkeypatch):
    """Items must carry runlistCarId (junction row id) and carId (inventory_car_id) as distinct keys."""
    rows = [
        {
            "id": "junction-row-uuid",
            "runlist_id": "rl1",
            "inventory_car_id": "car-uuid",
            "year": 2022,
            "make": "Toyota",
            "model": "Camry",
            "vin": "VIN123",
            "stock_number": "S001",
            "archived_at": None,
            "asking_price_cents": None,
            "imx_score": None,
        }
    ]
    client = FakeClient(rows)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.get_runlist_cars({"runlistId": "rl1"}))
    assert isinstance(out, list) and len(out) == 1
    item = out[0]
    assert "runlistCarId" in item, "runlistCarId (junction row id) must be present"
    assert "carId" in item, "carId (inventory_car_id) must be present"
    assert item["runlistCarId"] == "junction-row-uuid"
    assert item["carId"] == "car-uuid"
    # They must NOT be the same value (the bug this test guards against).
    assert item["runlistCarId"] != item["carId"]
