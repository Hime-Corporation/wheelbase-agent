"""Tests for get_recon_board tool (Batch B5)."""

import json

import wheelbase_core.tools.get_recon_board as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows or []

    def postgrest_get(self, table, params):
        self.calls.append({"table": table, "params": dict(params)})
        return self._rows

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append({"method": method, "table": table, "body": body})
        return []

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Success — nested tree
# ---------------------------------------------------------------------------

def test_get_recon_board_queries_work_item_tree_view(monkeypatch):
    client = FakeClient(rows=[])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.get_recon_board({"carId": "car-1"})
    assert client.calls[0]["table"] == "work_item_tree"


def test_get_recon_board_filters_by_car_id(monkeypatch):
    client = FakeClient(rows=[])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.get_recon_board({"carId": "car-abc"})
    params = client.calls[0]["params"]
    assert params["inventory_car_id"] == "eq.car-abc"


def test_get_recon_board_nests_stages_under_recon_run(monkeypatch):
    """Stages must appear as children of the recon_run root node."""
    rows = [
        {"id": "run-1", "type": "recon_run", "parent_id": None, "inventory_car_id": "car-1",
         "title": "Recon Run", "status": "in_progress", "priority": "medium",
         "rolled_est_cents": 0, "rolled_actual_cents": 0, "hold_type": None, "hold_reason": None,
         "due_at": None, "created_at": "2026-01-01", "sort_key": "0", "depth": 0,
         "root_id": "run-1", "effective_status": "in_progress"},
        {"id": "stage-1", "type": "stage", "parent_id": "run-1", "inventory_car_id": "car-1",
         "title": "Mechanical", "status": "todo", "priority": "medium",
         "rolled_est_cents": 0, "rolled_actual_cents": 0, "hold_type": None, "hold_reason": None,
         "due_at": None, "created_at": "2026-01-01", "sort_key": "1", "depth": 1,
         "root_id": "run-1", "effective_status": "todo"},
    ]
    client = FakeClient(rows=rows)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.get_recon_board({"carId": "car-1"}))
    assert "error" not in out
    assert "reconRun" in out
    recon_run = out["reconRun"]
    assert recon_run["id"] == "run-1"
    assert len(recon_run["children"]) == 1
    assert recon_run["children"][0]["id"] == "stage-1"


def test_get_recon_board_no_recon_run_returns_items(monkeypatch):
    """When no recon_run root exists, fall back to flat items list."""
    rows = [
        {"id": "task-1", "type": "task", "parent_id": None, "inventory_car_id": "car-1",
         "title": "Fix bumper", "status": "todo", "priority": "medium",
         "rolled_est_cents": 0, "rolled_actual_cents": 0, "hold_type": None, "hold_reason": None,
         "due_at": None, "created_at": "2026-01-01", "sort_key": "0", "depth": 0,
         "root_id": "task-1", "effective_status": "todo"},
    ]
    client = FakeClient(rows=rows)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.get_recon_board({"carId": "car-1"}))
    assert "error" not in out
    assert "items" in out
    assert "reconRun" not in out
    assert len(out["items"]) == 1


def test_get_recon_board_nested_finding_under_stage(monkeypatch):
    """Findings must appear nested under stages."""
    rows = [
        {"id": "run-1", "type": "recon_run", "parent_id": None, "inventory_car_id": "c",
         "title": "Run", "status": "todo", "priority": "medium",
         "rolled_est_cents": 0, "rolled_actual_cents": 0, "hold_type": None, "hold_reason": None,
         "due_at": None, "created_at": "2026-01-01", "sort_key": "0", "depth": 0,
         "root_id": "run-1", "effective_status": "todo"},
        {"id": "stage-1", "type": "stage", "parent_id": "run-1", "inventory_car_id": "c",
         "title": "Stage", "status": "todo", "priority": "medium",
         "rolled_est_cents": 0, "rolled_actual_cents": 0, "hold_type": None, "hold_reason": None,
         "due_at": None, "created_at": "2026-01-01", "sort_key": "1", "depth": 1,
         "root_id": "run-1", "effective_status": "todo"},
        {"id": "find-1", "type": "finding", "parent_id": "stage-1", "inventory_car_id": "c",
         "title": "Dent", "status": "todo", "priority": "medium",
         "rolled_est_cents": 0, "rolled_actual_cents": 0, "hold_type": None, "hold_reason": None,
         "due_at": None, "created_at": "2026-01-01", "sort_key": "2", "depth": 2,
         "root_id": "run-1", "effective_status": "todo"},
    ]
    client = FakeClient(rows=rows)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.get_recon_board({"carId": "c"}))
    stage = out["reconRun"]["children"][0]
    assert stage["id"] == "stage-1"
    assert len(stage["children"]) == 1
    assert stage["children"][0]["id"] == "find-1"


# ---------------------------------------------------------------------------
# Validation / auth
# ---------------------------------------------------------------------------

def test_get_recon_board_requires_car_id():
    out = json.loads(mod.get_recon_board({}))
    assert "error" in out
    assert "carId" in out["error"]


def test_get_recon_board_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")
    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.get_recon_board({"carId": "c"}))
    assert out["error"] == "not_signed_in"
