"""Tests for create_work_order tool."""

import json

import wheelbase_core.tools.create_work_order as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, response=None):
        self._response = response or [{"id": "wo1", "title": "Test WO", "status": "open"}]
        self.calls = []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append({"method": method, "table": table, "body": body})
        return self._response

    def close(self):
        pass


def test_create_work_order_success(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient())
    out = json.loads(mod.create_work_order({"carId": "car-uuid", "title": "Oil Change"}))
    assert out["workOrderId"] == "wo1"
    assert out["carId"] == "car-uuid"


def test_create_work_order_sets_status_open(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.create_work_order({"carId": "car-uuid", "title": "Repair"})
    body = client.calls[0]["body"]
    assert body["status"] == "open"
    assert body["inventory_car_id"] == "car-uuid"


def test_create_work_order_optional_fields(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.create_work_order({
        "carId": "car-uuid",
        "title": "Detail",
        "description": "Full detail",
        "vendorId": "v1",
        "scheduledAt": "2026-07-01T10:00:00Z",
    })
    body = client.calls[0]["body"]
    assert body["description"] == "Full detail"
    assert body["vendor_id"] == "v1"
    assert body["scheduled_at"] == "2026-07-01T10:00:00Z"


def test_create_work_order_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.create_work_order({"carId": "c", "title": "T"}))
    assert out["error"] == "not_signed_in"


def test_create_work_order_missing_car_id():
    out = json.loads(mod.create_work_order({"title": "T"}))
    assert "error" in out
    assert "carId" in out["error"]


def test_create_work_order_missing_title():
    out = json.loads(mod.create_work_order({"carId": "c"}))
    assert "error" in out
    assert "title" in out["error"]
