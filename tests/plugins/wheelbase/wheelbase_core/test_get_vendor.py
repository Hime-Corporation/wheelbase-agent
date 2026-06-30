"""Tests for get_vendor tool."""

import json

import wheelbase_core.tools.get_vendor as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, rows=None):
        self._rows = rows
        self.last_params: dict = {}

    def postgrest_get(self, table, params):
        assert table == "vendor"
        assert params["id"].startswith("eq.")
        self.last_params = dict(params)
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


# ---------------------------------------------------------------------------
# Explicit column projection (no tenant_id leak)
# ---------------------------------------------------------------------------

def test_get_vendor_select_excludes_tenant_id(monkeypatch):
    """select must list explicit columns and must NOT include tenant_id."""
    client = FakeClient(rows=[{"id": "v1", "name": "Speed Shop"}])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.get_vendor({"vendorId": "v1"})
    select = client.last_params.get("select", "")
    assert "tenant_id" not in select
    assert select != "*"


def test_get_vendor_select_excludes_star(monkeypatch):
    """select must be an explicit column list, not a wildcard."""
    client = FakeClient(rows=[{"id": "v1", "name": "Speed Shop"}])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.get_vendor({"vendorId": "v1"})
    assert "*" not in client.last_params.get("select", "")


def test_get_vendor_select_includes_expected_columns(monkeypatch):
    """select must include id, name, vendor_type, phone, email, city, state, notes."""
    client = FakeClient(rows=[{"id": "v1", "name": "Speed Shop"}])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.get_vendor({"vendorId": "v1"})
    select = client.last_params.get("select", "")
    for col in ("id", "name", "vendor_type", "phone", "email", "city", "state", "notes"):
        assert col in select, f"Missing column in select: {col}"
