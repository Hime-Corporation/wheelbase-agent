"""Tests for get_vendor tool."""

import json

import wheelbase_core.tools.get_vendor as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, rows=None):
        self._rows = rows

    def postgrest_get(self, table, params):
        assert table == "vendor"
        assert params["id"].startswith("eq.")
        return self._rows

    def close(self):
        pass


def test_get_vendor_returns_row(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient([{"id": "v1", "name": "Speed Shop"}]))
    out = json.loads(mod.get_vendor({"vendorId": "v1"}))
    assert out["name"] == "Speed Shop"


def test_get_vendor_not_found(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient([]))
    out = json.loads(mod.get_vendor({"vendorId": "missing"}))
    assert "error" in out
    assert "not found" in out["error"].lower()


def test_get_vendor_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.get_vendor({"vendorId": "v1"}))
    assert out["error"] == "not_signed_in"


def test_get_vendor_missing_vendor_id():
    out = json.loads(mod.get_vendor({}))
    assert "error" in out
    assert "vendorId" in out["error"]
