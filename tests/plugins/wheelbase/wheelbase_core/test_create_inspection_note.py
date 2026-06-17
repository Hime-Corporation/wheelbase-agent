"""Tests for create_inspection_note tool — V2 implementation.

V2 flow:
1. GET vehicle_recon_intake_inspection?inventory_car_id=eq.<carId> → inspection_id
2. POST inspection_item_result with notes + item_id (derived from category)
"""

import json

import wheelbase_core.tools.create_inspection_note as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    """Simulates WheelbaseClient for the two-step V2 flow.

    Attributes
    ----------
    inspection_id: str
        Returned as the inspection's ``id`` in the GET lookup.
    write_response: list
        Returned by postgrest_write.
    calls: list
        Records all write calls for assertion.
    """

    def __init__(self, inspection_id="insp-uuid", tenant_id=None, write_response=None):
        self._inspection_id = inspection_id
        self._tenant_id = tenant_id
        self._write_response = write_response or [
            {"id": "item-result-uuid", "inspection_id": inspection_id}
        ]
        self.calls = []

    def postgrest_get(self, table, params):
        # Return the lookup row for the inspection table.
        if table == mod.INSPECTION_TABLE:
            return [{"id": self._inspection_id, "tenant_id": self._tenant_id}]
        return []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append({"method": method, "table": table, "body": body})
        return self._write_response

    def close(self):
        pass


class FakeClientNoInspection:
    """Simulates the case where no inspection row exists for the carId."""

    def postgrest_get(self, table, params):
        return []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        return []

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------

def test_create_inspection_note_success(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient())
    out = json.loads(mod.create_inspection_note({"carId": "car-uuid", "note": "Check brakes"}))
    assert out["carId"] == "car-uuid"
    assert out["note"] == "Check brakes"
    assert "id" in out


def test_create_inspection_note_writes_to_item_result_table(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.create_inspection_note({"carId": "car-uuid", "note": "Test"})
    assert client.calls[0]["table"] == mod.ITEM_RESULT_TABLE


def test_create_inspection_note_body_contains_notes_field(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.create_inspection_note({"carId": "car-uuid", "note": "Dent on hood"})
    body = client.calls[0]["body"]
    assert body["notes"] == "Dent on hood"


def test_create_inspection_note_with_category_sets_item_id(monkeypatch):
    """category arg is used as item_id in the V2 item_result row."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.create_inspection_note({"carId": "car-uuid", "note": "Dent on hood", "category": "cosmetic"})
    body = client.calls[0]["body"]
    assert body["notes"] == "Dent on hood"
    assert body["item_id"] == "cosmetic"


def test_create_inspection_note_default_item_id_when_no_category(monkeypatch):
    """Without a category, item_id defaults to 'general_note'."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.create_inspection_note({"carId": "car-uuid", "note": "General observation"})
    body = client.calls[0]["body"]
    assert body["item_id"] == mod._DEFAULT_ITEM_ID


def test_create_inspection_note_uses_resolved_inspection_id(monkeypatch):
    """The inspection_id in the write body matches the one returned by the lookup."""
    client = FakeClient(inspection_id="resolved-insp-id")
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.create_inspection_note({"carId": "car-uuid", "note": "Test"})
    body = client.calls[0]["body"]
    assert body["inspection_id"] == "resolved-insp-id"


def test_create_inspection_note_item_id_returned(monkeypatch):
    """itemId is present in the success result."""
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient())
    out = json.loads(mod.create_inspection_note({"carId": "car-uuid", "note": "Check brakes", "category": "brakes"}))
    assert out.get("itemId") == "brakes"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_create_inspection_note_no_inspection_found(monkeypatch):
    """Returns an error when no inspection row exists for carId."""
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClientNoInspection())
    out = json.loads(mod.create_inspection_note({"carId": "unknown-car", "note": "N"}))
    assert "error" in out


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
