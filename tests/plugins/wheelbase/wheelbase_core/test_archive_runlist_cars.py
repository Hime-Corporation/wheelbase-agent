"""Tests for archive_runlist_cars tool."""

import json

import wheelbase_core.tools.archive_runlist_cars as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self):
        self.calls = []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append({"method": method, "table": table, "body": body, "params": params})

    def close(self):
        pass


def test_archive_runlist_cars_patches_runlist_cars(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(
        mod.archive_runlist_cars({"runlistId": "rl1", "carIds": ["c1", "c2"]})
    )
    assert out["archivedCount"] == 2
    assert out["runlistId"] == "rl1"
    call = client.calls[0]
    assert call["method"] == "PATCH"
    assert call["table"] == "runlist_cars"
    assert "archived_at" in call["body"]
    assert call["params"]["runlist_id"] == "eq.rl1"
    assert "c1" in call["params"]["car_id"]
    assert "c2" in call["params"]["car_id"]


def test_archive_runlist_cars_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.archive_runlist_cars({"runlistId": "rl1", "carIds": ["c1"]}))
    assert out["error"] == "not_signed_in"


def test_archive_runlist_cars_missing_runlist_id():
    out = json.loads(mod.archive_runlist_cars({"carIds": ["c1"]}))
    assert "error" in out
    assert "runlistId" in out["error"]


def test_archive_runlist_cars_empty_car_ids():
    out = json.loads(mod.archive_runlist_cars({"runlistId": "rl1", "carIds": []}))
    assert "error" in out


def test_archive_runlist_cars_too_many_ids():
    out = json.loads(
        mod.archive_runlist_cars({"runlistId": "rl1", "carIds": ["c"] * 1001})
    )
    assert "error" in out
    assert "1000" in out["error"]
