"""Tests for get_work_order tool."""

import json

import wheelbase_core.tools.get_work_order as mod
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


def test_get_work_order_returns_list(monkeypatch):
    rows = [
        {"id": "wo1", "title": "Oil Change", "status": "open", "vendor_id": None, "scheduled_at": None}
    ]
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(rows))
    out = json.loads(mod.get_work_order({"carId": "car-uuid"}))
    assert len(out) == 1
    assert out[0]["id"] == "wo1"
    assert out[0]["status"] == "open"


def test_get_work_order_filters_by_car_id(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.get_work_order({"carId": "car-uuid"})
    assert client.last_params["inventory_car_id"] == "eq.car-uuid"


def test_get_work_order_status_filter(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.get_work_order({"carId": "car-uuid", "status": "completed"})
    assert client.last_params.get("status") == "eq.completed"


def test_get_work_order_invalid_status():
    out = json.loads(mod.get_work_order({"carId": "c", "status": "invalid_status"}))
    assert "error" in out
    assert "status" in out["error"]


def test_get_work_order_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.get_work_order({"carId": "c"}))
    assert out["error"] == "not_signed_in"


def test_get_work_order_missing_car_id():
    out = json.loads(mod.get_work_order({}))
    assert "error" in out
    assert "carId" in out["error"]
