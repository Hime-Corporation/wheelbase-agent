"""Tests for create_inspection_note tool."""

import json

import wheelbase_core.tools.create_inspection_note as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, response=None):
        self._response = response or [{"id": "insp1", "inventory_car_id": "car-uuid"}]
        self.calls = []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append({"method": method, "table": table, "body": body})
        return self._response

    def close(self):
        pass


def test_create_inspection_note_success(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient())
    out = json.loads(mod.create_inspection_note({"carId": "car-uuid", "note": "Check brakes"}))
    assert out["carId"] == "car-uuid"
    assert out["note"] == "Check brakes"


def test_create_inspection_note_with_category(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.create_inspection_note({"carId": "car-uuid", "note": "Dent on hood", "category": "cosmetic"})
    body = client.calls[0]["body"]
    assert body["category"] == "cosmetic"
    assert body["notes"] == "Dent on hood"


def test_create_inspection_note_correct_table(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.create_inspection_note({"carId": "car-uuid", "note": "Test"})
    assert client.calls[0]["table"] == "vehicle_recon_intake_inspection"


def test_create_inspection_note_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.create_inspection_note({"carId": "c", "note": "N"}))
    assert out["error"] == "not_signed_in"


def test_create_inspection_note_missing_car_id():
    out = json.loads(mod.create_inspection_note({"note": "N"}))
    assert "error" in out
    assert "carId" in out["error"]


def test_create_inspection_note_missing_note():
    out = json.loads(mod.create_inspection_note({"carId": "c"}))
    assert "error" in out
    assert "note" in out["error"]
