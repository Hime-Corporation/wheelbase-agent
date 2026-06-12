"""Tests for generate_demand_score tool."""

import json

import wheelbase_core.tools.generate_demand_score as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.last_params = None

    def postgrest_get(self, table, params):
        self.last_params = params
        return self._rows

    def close(self):
        pass


def test_generate_demand_score_returns_scores(monkeypatch):
    rows = [
        {"id": "c1", "year": 2020, "make": "Honda", "model": "Civic", "trim": None, "mileage": 25000, "imx_score": 70}
    ]
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(rows))
    out = json.loads(mod.generate_demand_score({}))
    assert len(out["scores"]) == 1
    assert out["scores"][0]["carId"] == "c1"
    assert isinstance(out["scores"][0]["score"], int)


def test_generate_demand_score_specific_car_ids(monkeypatch):
    rows = [{"id": "c1", "year": 2020, "make": "Honda", "model": "Civic", "trim": None, "mileage": 50000, "imx_score": 60}]
    client = FakeClient(rows)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.generate_demand_score({"carIds": ["c1"]}))
    assert len(out["scores"]) == 1
    # carIds query uses `in` param
    assert "in." in client.last_params.get("id", "")


def test_generate_demand_score_all_inventory_params(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.generate_demand_score({})
    assert client.last_params.get("is_archived") == "eq.false"
    assert client.last_params.get("limit") == "200"


def test_generate_demand_score_market_bonus(monkeypatch):
    rows = [{"id": "c1", "year": 2020, "make": "Honda", "model": "Civic", "trim": None, "mileage": 0, "imx_score": 50}]
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(rows))
    out_no_market = json.loads(mod.generate_demand_score({}))
    score_no_market = out_no_market["scores"][0]["score"]

    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(rows))
    out_market = json.loads(mod.generate_demand_score({"market": "Dallas"}))
    score_with_market = out_market["scores"][0]["score"]

    assert score_with_market == score_no_market + 2


def test_generate_demand_score_empty_inventory(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient([]))
    out = json.loads(mod.generate_demand_score({}))
    assert out["scores"] == []
    assert "No cars" in out["summary"]


def test_generate_demand_score_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.generate_demand_score({}))
    assert out["error"] == "not_signed_in"


def test_generate_demand_score_invalid_car_ids():
    out = json.loads(mod.generate_demand_score({"carIds": []}))
    assert "error" in out


def test_generate_demand_score_too_many_car_ids():
    out = json.loads(mod.generate_demand_score({"carIds": ["c"] * 201}))
    assert "error" in out
    assert "200" in out["error"]
