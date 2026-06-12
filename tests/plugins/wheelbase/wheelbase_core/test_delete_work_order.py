"""Tests for delete_work_order tool."""

import json

import wheelbase_core.tools.delete_work_order as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self):
        self.calls = []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append({"method": method, "table": table, "params": params})

    def close(self):
        pass


def test_delete_work_order_calls_delete(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.delete_work_order({"workOrderId": "wo1", "carId": "car-uuid"}))
    assert out["workOrderId"] == "wo1"
    assert out["carId"] == "car-uuid"
    call = client.calls[0]
    assert call["method"] == "DELETE"
    assert call["table"] == "work_order"
    assert call["params"]["id"] == "eq.wo1"


def test_delete_work_order_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.delete_work_order({"workOrderId": "wo1", "carId": "c"}))
    assert out["error"] == "not_signed_in"


def test_delete_work_order_missing_work_order_id():
    out = json.loads(mod.delete_work_order({"carId": "c"}))
    assert "error" in out
    assert "workOrderId" in out["error"]


def test_delete_work_order_missing_car_id():
    out = json.loads(mod.delete_work_order({"workOrderId": "wo1"}))
    assert "error" in out
    assert "carId" in out["error"]
