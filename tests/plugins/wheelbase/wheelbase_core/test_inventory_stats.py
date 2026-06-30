"""Tests for get_inventory_stats and get_inventory_filter_options."""

import json

import wheelbase_core.tools.inventory_stats as mod
from wheelbase_sdk.errors import WheelbaseAuthError


# ---------------------------------------------------------------------------
# FakeClient used for all inventory stats tests
# ---------------------------------------------------------------------------

class FakeClient:
    """Simulates WheelbaseClient for inventory stats tests.

    Records all calls. `dealership_rows` controls what postgrest_get returns;
    `rpc_result` is returned for postgrest_write POST calls.
    """

    def __init__(self, dealership_rows=None, rpc_result=None):
        self.calls = []
        self._dealership_rows = dealership_rows if dealership_rows is not None else []
        self._rpc_result = rpc_result if rpc_result is not None else {"total": 10}

    def postgrest_get(self, table, params):
        self.calls.append(("GET", table, params))
        if table == "dealership":
            return self._dealership_rows
        return []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append((method, table, body, params))
        return self._rpc_result

    def close(self):
        pass


_ONE_DEALERSHIP = [{"dealership_id": "d-1", "tenant_id": "t-abc"}]
_TWO_DEALERSHIPS = [
    {"dealership_id": "d-1", "tenant_id": "t-abc"},
    {"dealership_id": "d-2", "tenant_id": "t-abc"},
]


# ---------------------------------------------------------------------------
# get_inventory_stats — happy path
# ---------------------------------------------------------------------------

def test_get_inventory_stats_resolves_dealership(monkeypatch):
    fake = FakeClient(dealership_rows=_ONE_DEALERSHIP, rpc_result={"total": 42})
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    out = json.loads(mod.get_inventory_stats({}))
    # Should have queried the dealership table first
    get_call = next(c for c in fake.calls if c[0] == "GET")
    assert get_call[1] == "dealership"


def test_get_inventory_stats_posts_to_rpc(monkeypatch):
    fake = FakeClient(dealership_rows=_ONE_DEALERSHIP, rpc_result={"total": 5})
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.get_inventory_stats({})
    post_call = next(c for c in fake.calls if c[0] == "POST")
    assert post_call[1] == "rpc/get_inventory_stats"
    body = post_call[2]
    assert body["p_tenant_id"] == "t-abc"
    assert body["p_dealership_id"] == "d-1"


def test_get_inventory_stats_returns_rpc_result(monkeypatch):
    rpc_data = {"total": 7, "for_sale": 4, "in_recon": 3}
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(_ONE_DEALERSHIP, rpc_data))
    out = json.loads(mod.get_inventory_stats({}))
    assert out["total"] == 7
    assert out["for_sale"] == 4


def test_get_inventory_stats_explicit_dealership_id(monkeypatch):
    """If dealershipId is passed, it is used directly in the query filter."""
    fake = FakeClient(dealership_rows=[{"dealership_id": "d-99", "tenant_id": "t-xyz"}])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.get_inventory_stats({"dealershipId": "d-99"})
    get_call = next(c for c in fake.calls if c[0] == "GET")
    assert get_call[2].get("dealership_id") == "eq.d-99"
    post_call = next(c for c in fake.calls if c[0] == "POST")
    assert post_call[2]["p_dealership_id"] == "d-99"
    assert post_call[2]["p_tenant_id"] == "t-xyz"


# ---------------------------------------------------------------------------
# get_inventory_stats — error paths
# ---------------------------------------------------------------------------

def test_get_inventory_stats_no_dealership(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(dealership_rows=[]))
    out = json.loads(mod.get_inventory_stats({}))
    assert "error" in out
    assert "No dealership" in out["error"]


def test_get_inventory_stats_ambiguous(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(dealership_rows=_TWO_DEALERSHIPS))
    out = json.loads(mod.get_inventory_stats({}))
    assert "error" in out
    assert "Multiple dealerships" in out["error"]


def test_get_inventory_stats_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")
    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.get_inventory_stats({}))
    assert out["error"] == "not_signed_in"


def test_get_inventory_stats_rpc_exception(monkeypatch):
    class BrokenClient:
        def postgrest_get(self, *a, **kw):
            return _ONE_DEALERSHIP
        def postgrest_write(self, *a, **kw):
            raise RuntimeError("RPC unavailable")
        def close(self): pass

    monkeypatch.setattr(mod, "WheelbaseClient", lambda: BrokenClient())
    out = json.loads(mod.get_inventory_stats({}))
    assert "error" in out
    assert "get_inventory_stats failed" in out["error"]


# ---------------------------------------------------------------------------
# get_inventory_filter_options — happy path
# ---------------------------------------------------------------------------

def test_get_inventory_filter_options_posts_to_rpc(monkeypatch):
    options_data = {"makes": ["Toyota", "Ford"], "statuses": ["active"]}
    fake = FakeClient(dealership_rows=_ONE_DEALERSHIP, rpc_result=options_data)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    out = json.loads(mod.get_inventory_filter_options({}))
    post_call = next(c for c in fake.calls if c[0] == "POST")
    assert post_call[1] == "rpc/get_inventory_filter_options"
    assert post_call[2]["p_tenant_id"] == "t-abc"
    assert post_call[2]["p_dealership_id"] == "d-1"
    assert out["makes"] == ["Toyota", "Ford"]


def test_get_inventory_filter_options_no_dealership(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(dealership_rows=[]))
    out = json.loads(mod.get_inventory_filter_options({}))
    assert "error" in out
    assert "No dealership" in out["error"]


def test_get_inventory_filter_options_ambiguous(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(dealership_rows=_TWO_DEALERSHIPS))
    out = json.loads(mod.get_inventory_filter_options({}))
    assert "error" in out
    assert "Multiple dealerships" in out["error"]


def test_get_inventory_filter_options_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")
    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.get_inventory_filter_options({}))
    assert out["error"] == "not_signed_in"
