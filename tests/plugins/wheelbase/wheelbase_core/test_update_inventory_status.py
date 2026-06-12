"""Tests for update_inventory_status tool."""

import json

import wheelbase_core.tools.update_inventory_status as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self):
        self.calls = []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append({"method": method, "table": table, "body": body, "params": params})

    def close(self):
        pass


def test_update_status_patches_inventory_car(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.update_inventory_status({"carId": "car-uuid", "newStatusId": 5}))
    assert out["carId"] == "car-uuid"
    assert out["newStatusId"] == 5
    # First call must be the PATCH on inventory_car
    patch_call = client.calls[0]
    assert patch_call["method"] == "PATCH"
    assert patch_call["table"] == "inventory_car"
    assert patch_call["body"]["status_id"] == 5
    assert patch_call["params"]["id"] == "eq.car-uuid"


def test_update_status_inserts_history_with_note(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.update_inventory_status({"carId": "car-uuid", "newStatusId": 2, "note": "Sold"})
    # Second call is the history insert
    history_call = client.calls[1]
    assert history_call["method"] == "POST"
    assert history_call["table"] == "inventory_status_history"
    assert history_call["body"]["note"] == "Sold"


def test_update_status_inserts_history_without_note(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.update_inventory_status({"carId": "car-uuid", "newStatusId": 2})
    history_call = client.calls[1]
    assert "note" not in history_call["body"]


def test_update_status_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.update_inventory_status({"carId": "c", "newStatusId": 1}))
    assert out["error"] == "not_signed_in"


def test_update_status_missing_car_id():
    out = json.loads(mod.update_inventory_status({"newStatusId": 1}))
    assert "error" in out
    assert "carId" in out["error"]


def test_update_status_missing_status_id():
    out = json.loads(mod.update_inventory_status({"carId": "c"}))
    assert "error" in out
    assert "newStatusId" in out["error"]
