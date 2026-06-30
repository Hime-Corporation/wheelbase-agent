"""Tests for query_work — cross-org work_item filtering."""

import json

import wheelbase_core.tools.query_work as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows if rows is not None else []

    def postgrest_get(self, table, params):
        self.calls.append(("GET", table, params))
        return self._rows

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Basic filtering
# ---------------------------------------------------------------------------

def test_query_work_status_filter(monkeypatch):
    fake = FakeClient([{"id": "wi-1", "status": "blocked"}])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    out = json.loads(mod.query_work({"status": "blocked"}))
    assert "error" not in out
    _, table, params = fake.calls[0]
    assert table == "work_item"
    assert params["status"] == "eq.blocked"


def test_query_work_due_before_only(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.query_work({"status": "blocked", "dueBefore": "2026-07-01"})
    _, _, params = fake.calls[0]
    assert params["status"] == "eq.blocked"
    assert params["due_at"] == "lte.2026-07-01"
    assert "and" not in params


def test_query_work_due_after_only(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.query_work({"dueAfter": "2026-06-01"})
    _, _, params = fake.calls[0]
    assert params["due_at"] == "gte.2026-06-01"
    assert "and" not in params


def test_query_work_due_range_uses_and_expression(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.query_work({"dueBefore": "2026-07-01", "dueAfter": "2026-06-01"})
    _, _, params = fake.calls[0]
    assert "due_at" not in params
    assert "and" in params
    assert "due_at.gte.2026-06-01" in params["and"]
    assert "due_at.lte.2026-07-01" in params["and"]


def test_query_work_select_excludes_effective_status(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.query_work({})
    _, _, params = fake.calls[0]
    assert "effective_status" not in params["select"]


def test_query_work_returns_items_key(monkeypatch):
    rows = [{"id": "wi-1"}, {"id": "wi-2"}]
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(rows))
    out = json.loads(mod.query_work({}))
    assert "items" in out
    assert len(out["items"]) == 2


def test_query_work_vendor_and_assigned_filters(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.query_work({"vendorId": "v-99", "assignedToUserId": "u-42"})
    _, _, params = fake.calls[0]
    assert params["vendor_id"] == "eq.v-99"
    assert params["assigned_to_user_id"] == "eq.u-42"


def test_query_work_type_filter(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.query_work({"type": "work_order"})
    _, _, params = fake.calls[0]
    assert params["type"] == "eq.work_order"


def test_query_work_default_limit_and_order(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.query_work({})
    _, _, params = fake.calls[0]
    assert params["limit"] == "50"
    assert params["order"] == "due_at.asc"


def test_query_work_custom_limit_offset(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.query_work({"limit": 10, "offset": 20})
    _, _, params = fake.calls[0]
    assert params["limit"] == "10"
    assert params["offset"] == "20"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_query_work_invalid_status(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient())
    out = json.loads(mod.query_work({"status": "nonsense"}))
    assert "error" in out


def test_query_work_invalid_type(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient())
    out = json.loads(mod.query_work({"type": "bogus_type"}))
    assert "error" in out


# ---------------------------------------------------------------------------
# Auth / error paths
# ---------------------------------------------------------------------------

def test_query_work_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")
    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.query_work({}))
    assert out["error"] == "not_signed_in"


def test_query_work_client_exception(monkeypatch):
    class BrokenClient:
        def postgrest_get(self, *a, **kw):
            raise RuntimeError("db is down")
        def close(self): pass

    monkeypatch.setattr(mod, "WheelbaseClient", lambda: BrokenClient())
    out = json.loads(mod.query_work({}))
    assert "error" in out
    assert "query_work failed" in out["error"]
