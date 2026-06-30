"""Tests for create_work_item, get_work_item, delete_work_item (Batch B1).

NOTE: wheelbase_sdk.ok(data) serializes data directly to JSON (no "ok" wrapper).
      wheelbase_sdk.err(msg) returns {"error": msg}.
      Assertions here match that actual SDK behaviour.
"""
import json

import wheelbase_core.tools.create_work_item as mod


class _FakeClient:
    def __init__(self): self.calls = []
    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append((method, table, body, params))
        return [{"id": "wi-1", "title": body["title"], "status": body["status"], "type": body["type"]}]
    def close(self): pass


# ── create_work_item ─────────────────────────────────────────────────────────

def test_create_work_item_defaults_to_task(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    out = json.loads(mod.create_work_item({"carId": "car-1", "title": "Replace tires"}))
    # ok() returns the dict directly (no "ok" wrapper)
    assert "error" not in out
    assert out["workItemId"] == "wi-1"
    method, table, body, _ = fake.calls[0]
    assert table == "work_item"
    assert body["type"] == "task"
    assert body["status"] == "todo"
    assert body["inventory_car_id"] == "car-1"
    assert body["title"] == "Replace tires"


def test_create_work_item_child_requires_parent(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: _FakeClient())
    out = json.loads(mod.create_work_item({"carId": "c", "title": "t", "type": "finding"}))
    # err() returns {"error": "..."}
    assert "error" in out
    assert "parentId" in out["error"]


# ── get_work_item ─────────────────────────────────────────────────────────────

def test_get_work_item_requires_car_or_id(monkeypatch):
    import wheelbase_core.tools.get_work_item as gmod
    monkeypatch.setattr(gmod, "WheelbaseClient", lambda: _FakeClient())
    out = json.loads(gmod.get_work_item({}))
    assert "error" in out
    assert "carId" in out["error"] or "workItemId" in out["error"]


def test_get_work_item_queries_work_item_table(monkeypatch):
    import wheelbase_core.tools.get_work_item as gmod

    class _C(_FakeClient):
        def postgrest_get(self, table, params):
            self.calls.append((table, params))
            return [{"id": "wi-1", "type": "task", "status": "todo", "effective_status": "todo"}]

    fake = _C()
    monkeypatch.setattr(gmod, "WheelbaseClient", lambda: fake)
    out = json.loads(gmod.get_work_item({"carId": "car-1"}))
    assert "error" not in out
    assert "items" in out
    assert fake.calls[0][0] == "work_item"
    assert fake.calls[0][1]["inventory_car_id"] == "eq.car-1"


# ── delete_work_item ──────────────────────────────────────────────────────────

def test_delete_work_item_prefetches_children(monkeypatch):
    import wheelbase_core.tools.delete_work_item as dmod

    class _C(_FakeClient):
        def postgrest_get(self, table, params):
            if params.get("id"): return [{"id": "wi-1", "title": "Brakes"}]
            return [{"id": "child-1"}, {"id": "child-2"}]
        def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
            self.calls.append((method, table, params)); return None

    fake = _C()
    monkeypatch.setattr(dmod, "WheelbaseClient", lambda: fake)
    out = json.loads(dmod.delete_work_item({"workItemId": "wi-1"}))
    assert "error" not in out
    assert out["childCount"] == 2
    assert ("DELETE", "work_item", {"id": "eq.wi-1"}) in fake.calls
