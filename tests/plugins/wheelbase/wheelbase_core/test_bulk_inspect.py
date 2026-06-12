"""Tests for bulk_inspect tool."""

import json

import wheelbase_core.tools.bulk_inspect as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, responses=None):
        # responses: dict mapping car_id → row (or None for pending)
        self._responses = responses or {}

    def postgrest_get(self, table, params):
        car_id_filter = params.get("inventory_car_id", "")
        car_id = car_id_filter.replace("eq.", "")
        row = self._responses.get(car_id)
        return [row] if row is not None else []

    def close(self):
        pass


def test_bulk_inspect_completed(monkeypatch):
    responses = {"c1": {"id": "i1", "inventory_car_id": "c1", "status": "completed"}}
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(responses))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    assert out["results"][0]["state"] == "completed"
    assert "completed" in out["summary"]


def test_bulk_inspect_in_progress(monkeypatch):
    responses = {"c1": {"id": "i1", "inventory_car_id": "c1", "status": "in_progress"}}
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(responses))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    assert out["results"][0]["state"] == "in-progress"


def test_bulk_inspect_pending(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient({}))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    assert out["results"][0]["state"] == "pending"
    assert "pending" in out["summary"]


def test_bulk_inspect_mixed(monkeypatch):
    responses = {
        "c1": {"id": "i1", "inventory_car_id": "c1", "status": "completed"},
        "c2": {"id": "i2", "inventory_car_id": "c2", "status": "draft"},
    }
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(responses))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1", "c2", "c3"]}))
    states = {r["carId"]: r["state"] for r in out["results"]}
    assert states["c1"] == "completed"
    assert states["c2"] == "in-progress"
    assert states["c3"] == "pending"


def test_bulk_inspect_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    assert out["error"] == "not_signed_in"


def test_bulk_inspect_empty_car_ids():
    out = json.loads(mod.bulk_inspect({"carIds": []}))
    assert "error" in out


def test_bulk_inspect_too_many_ids():
    out = json.loads(mod.bulk_inspect({"carIds": ["c"] * 201}))
    assert "error" in out
    assert "200" in out["error"]
