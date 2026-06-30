"""Tests for start_recon tool (Batch B5)."""

import json

import wheelbase_core.tools.start_recon as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    """Simulates two-step flow: status definition lookup + car scoping + RPC call."""

    def __init__(self, recon_status_id=42, tenant_id="t-1", dealership_id="d-1"):
        self.calls = []
        self._recon_status_id = recon_status_id
        self._tenant_id = tenant_id
        self._dealership_id = dealership_id

    def postgrest_get(self, table, params):
        self.calls.append({"table": table, "params": dict(params)})
        if table == "inventory_status_definition":
            return [{"id": self._recon_status_id}]
        if table == "inventory_car":
            return [{"tenant_id": self._tenant_id, "dealership_id": self._dealership_id}]
        return []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append({"method": method, "table": table, "body": body})
        return {"ok": True}

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------

def test_start_recon_resolves_recon_status_code(monkeypatch):
    """Without reconStatusId, tool must look up code='recon' from inventory_status_definition."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.start_recon({"carId": "car-1"}))
    assert "error" not in out
    lookups = [c for c in client.calls if c["table"] == "inventory_status_definition"]
    assert len(lookups) == 1
    assert lookups[0]["params"]["code"] == "eq.recon"


def test_start_recon_calls_rpc_with_resolved_status_id(monkeypatch):
    client = FakeClient(recon_status_id=7)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.start_recon({"carId": "car-1"}))
    assert "error" not in out
    rpc_calls = [c for c in client.calls if c["table"] == "rpc/inventory_set_status"]
    assert len(rpc_calls) == 1
    assert rpc_calls[0]["body"]["p_new_status_id"] == 7


def test_start_recon_uses_fetched_tenant_and_dealership(monkeypatch):
    client = FakeClient(tenant_id="ten-abc", dealership_id="deal-xyz")
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.start_recon({"carId": "car-1"})
    rpc_call = next(c for c in client.calls if c["table"] == "rpc/inventory_set_status")
    assert rpc_call["body"]["p_tenant_id"] == "ten-abc"
    assert rpc_call["body"]["p_dealership_id"] == "deal-xyz"
    assert rpc_call["body"]["p_car_id"] == "car-1"


def test_start_recon_skips_status_lookup_when_reconStatusId_provided(monkeypatch):
    """When reconStatusId is provided, skip the status_definition lookup."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.start_recon({"carId": "car-1", "reconStatusId": 99}))
    assert "error" not in out
    lookups = [c for c in client.calls if c["table"] == "inventory_status_definition"]
    assert lookups == []
    rpc_call = next(c for c in client.calls if c["table"] == "rpc/inventory_set_status")
    assert rpc_call["body"]["p_new_status_id"] == 99


def test_start_recon_returns_car_id_and_status_id(monkeypatch):
    client = FakeClient(recon_status_id=5)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.start_recon({"carId": "car-xyz"}))
    assert out["carId"] == "car-xyz"
    assert out["statusId"] == 5
    assert "note" in out


def test_start_recon_err_when_recon_status_not_found(monkeypatch):
    class _NoStatus(FakeClient):
        def postgrest_get(self, table, params):
            self.calls.append({"table": table, "params": params})
            if table == "inventory_status_definition":
                return []
            return super().postgrest_get(table, params)

    monkeypatch.setattr(mod, "WheelbaseClient", lambda: _NoStatus())
    out = json.loads(mod.start_recon({"carId": "car-1"}))
    assert "error" in out
    assert "recon" in out["error"].lower()


def test_start_recon_err_when_car_not_found(monkeypatch):
    class _NoCar(FakeClient):
        def postgrest_get(self, table, params):
            self.calls.append({"table": table, "params": params})
            if table == "inventory_car":
                return []
            return super().postgrest_get(table, params)

    monkeypatch.setattr(mod, "WheelbaseClient", lambda: _NoCar())
    out = json.loads(mod.start_recon({"carId": "missing"}))
    assert "error" in out
    assert "not found" in out["error"].lower() or "missing" in out["error"].lower()


# ---------------------------------------------------------------------------
# Validation / auth
# ---------------------------------------------------------------------------

def test_start_recon_requires_car_id():
    out = json.loads(mod.start_recon({}))
    assert "error" in out
    assert "carId" in out["error"]


def test_start_recon_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")
    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.start_recon({"carId": "c"}))
    assert out["error"] == "not_signed_in"
