"""Tests for update_inventory_status tool.

The tool calls rpc/inventory_set_status (a single Postgres RPC) instead of
raw PATCH + history-insert.  It first fetches the car's tenant_id/dealership_id
(required SQL params) then POSTs to the RPC endpoint.
"""

import json

import wheelbase_core.tools.update_inventory_status as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    """Serves a minimal car row for the lookup step; records all write calls."""

    def __init__(self, car_row=None):
        self.calls: list[dict] = []
        self._car_row = car_row or {
            "id": "car-uuid",
            "tenant_id": "tenant-1",
            "dealership_id": "dealer-1",
        }

    def postgrest_get(self, table, params):
        self.calls.append({"method": "GET", "table": table, "params": dict(params)})
        if table == "inventory_car":
            return [self._car_row]
        return []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append({"method": method, "table": table, "body": body, "params": params})
        return {"updated": True}

    def close(self):
        pass


# ---------------------------------------------------------------------------
# RPC path — must POST to rpc/inventory_set_status
# ---------------------------------------------------------------------------

def test_update_status_calls_rpc(monkeypatch):
    """Tool must POST to rpc/inventory_set_status, not PATCH inventory_car."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.update_inventory_status({"carId": "car-uuid", "newStatusId": 5}))
    assert "error" not in out
    rpc_calls = [c for c in client.calls if c["table"] == "rpc/inventory_set_status"]
    assert len(rpc_calls) == 1
    assert rpc_calls[0]["method"] == "POST"
    body = rpc_calls[0]["body"]
    assert body["p_car_id"] == "car-uuid"
    assert body["p_new_status_id"] == 5
    assert body["p_tenant_id"] == "tenant-1"
    assert body["p_dealership_id"] == "dealer-1"


def test_update_status_no_patch_inventory_car(monkeypatch):
    """Must not raw-PATCH inventory_car."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.update_inventory_status({"carId": "car-uuid", "newStatusId": 5})
    patch_calls = [
        c for c in client.calls
        if c["method"] == "PATCH" and c["table"] == "inventory_car"
    ]
    assert patch_calls == []


def test_update_status_no_history_insert(monkeypatch):
    """Must not insert directly into inventory_status_history."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.update_inventory_status({"carId": "car-uuid", "newStatusId": 5})
    hist_calls = [c for c in client.calls if c["table"] == "inventory_status_history"]
    assert hist_calls == []


def test_update_status_includes_note(monkeypatch):
    """note arg must be forwarded to the RPC as p_note."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.update_inventory_status({"carId": "car-uuid", "newStatusId": 2, "note": "Sold"})
    rpc_call = next(c for c in client.calls if c["table"] == "rpc/inventory_set_status")
    assert rpc_call["body"]["p_note"] == "Sold"


def test_update_status_omits_note_when_absent(monkeypatch):
    """When note is absent the RPC body must not include p_note."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.update_inventory_status({"carId": "car-uuid", "newStatusId": 2})
    rpc_call = next(c for c in client.calls if c["table"] == "rpc/inventory_set_status")
    assert "p_note" not in rpc_call["body"]


def test_update_status_car_not_found(monkeypatch):
    """If the car lookup returns no rows, return an error without calling the RPC."""
    class _NoCarClient(FakeClient):
        def postgrest_get(self, table, params):
            self.calls.append({"method": "GET", "table": table, "params": dict(params)})
            return []

    monkeypatch.setattr(mod, "WheelbaseClient", lambda: _NoCarClient())
    out = json.loads(mod.update_inventory_status({"carId": "missing-uuid", "newStatusId": 5}))
    assert "error" in out
    assert "missing-uuid" in out["error"] or "not found" in out["error"].lower()


def test_update_status_rpc_body_keys_match_sql_signature(monkeypatch):
    """Exact param names must match the SQL function signature."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.update_inventory_status({"carId": "car-abc", "newStatusId": 7, "note": "test"})
    rpc_call = next(c for c in client.calls if c["table"] == "rpc/inventory_set_status")
    body = rpc_call["body"]
    assert "p_tenant_id" in body
    assert "p_dealership_id" in body
    assert "p_car_id" in body
    assert "p_new_status_id" in body
    assert "p_note" in body
    # Must NOT use old raw-write field names
    assert "status_id" not in body
    assert "carId" not in body


# ---------------------------------------------------------------------------
# Validation / auth paths
# ---------------------------------------------------------------------------

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


def test_update_status_invalid_new_status_id(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient())
    out = json.loads(mod.update_inventory_status({"carId": "car-1", "newStatusId": "not-an-int"}))
    assert "error" in out


def test_update_status_new_status_id_coerced_to_int(monkeypatch):
    """String newStatusId must be coerced to int before sending to the RPC."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.update_inventory_status({"carId": "car-uuid", "newStatusId": "5"}))
    assert "error" not in out
    rpc_call = next(c for c in client.calls if c["table"] == "rpc/inventory_set_status")
    assert rpc_call["body"]["p_new_status_id"] == 5
