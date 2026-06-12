"""Tests for list_vendors tool."""

import json

import wheelbase_core.tools.list_vendors as mod
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


def test_list_vendors_returns_summaries(monkeypatch):
    rows = [
        {"id": "v1", "name": "Best Body Shop", "vendor_type": "body_shop", "phone": "555-0100", "email": "shop@example.com"}
    ]
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(rows))
    out = json.loads(mod.list_vendors({}))
    assert len(out) == 1
    assert out[0]["name"] == "Best Body Shop"
    assert out[0]["vendor_type"] == "body_shop"


def test_list_vendors_type_filter(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.list_vendors({"type": "mechanical"})
    assert client.last_params.get("vendor_type") == "eq.mechanical"


def test_list_vendors_search_filter(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.list_vendors({"search": "Best"})
    assert "name" in client.last_params
    assert "Best" in client.last_params["name"]


def test_list_vendors_invalid_type():
    out = json.loads(mod.list_vendors({"type": "not_a_type"}))
    assert "error" in out
    assert "type" in out["error"]


def test_list_vendors_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.list_vendors({}))
    assert out["error"] == "not_signed_in"
