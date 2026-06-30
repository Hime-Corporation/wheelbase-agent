"""Tests for bulk_inspect tool — V2 status model."""

import json

import wheelbase_core.tools.bulk_inspect as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, responses=None):
        # responses: dict mapping car_id → row (or None for pending)
        self._responses = responses or {}
        self.call_count = 0
        self.last_params = None

    def postgrest_get(self, table, params):
        self.call_count += 1
        self.last_params = params
        car_id_filter = params.get("inventory_car_id", "")
        if car_id_filter.startswith("in.(") and car_id_filter.endswith(")"):
            # Batched query: parse in.(id1,id2,...) → return all matching rows.
            ids_str = car_id_filter[4:-1]
            car_ids = [c.strip() for c in ids_str.split(",")]
            return [self._responses[cid] for cid in car_ids if cid in self._responses]
        # Fallback: eq. filter (shouldn't be exercised by the batched tool).
        car_id = car_id_filter.removeprefix("eq.")
        row = self._responses.get(car_id)
        return [row] if row is not None else []

    def close(self):
        pass


# ---------------------------------------------------------------------------
# V2 lifecycle status mapping
# ---------------------------------------------------------------------------

def test_bulk_inspect_complete_v2(monkeypatch):
    """V2 'complete' enum value maps to state 'completed'."""
    responses = {"c1": {"id": "i1", "inventory_car_id": "c1", "status": "complete"}}
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(responses))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    assert out["results"][0]["state"] == "completed"
    assert "completed" in out["summary"]


def test_bulk_inspect_completed(monkeypatch):
    """Backward-compat: legacy 'completed' value still maps to 'completed'."""
    responses = {"c1": {"id": "i1", "inventory_car_id": "c1", "status": "completed"}}
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(responses))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    assert out["results"][0]["state"] == "completed"
    assert "completed" in out["summary"]


def test_bulk_inspect_draft_maps_to_pending(monkeypatch):
    """V2 'draft' enum value maps to state 'pending' (record exists, not started)."""
    responses = {"c1": {"id": "i1", "inventory_car_id": "c1", "status": "draft"}}
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(responses))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    assert out["results"][0]["state"] == "pending"
    assert "pending" in out["summary"]


def test_bulk_inspect_in_progress(monkeypatch):
    """V2 'in_progress' enum value maps to state 'in-progress'."""
    responses = {"c1": {"id": "i1", "inventory_car_id": "c1", "status": "in_progress"}}
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(responses))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    assert out["results"][0]["state"] == "in-progress"


def test_bulk_inspect_pending(monkeypatch):
    """No row → pending."""
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient({}))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    assert out["results"][0]["state"] == "pending"
    assert "pending" in out["summary"]


def test_bulk_inspect_mixed(monkeypatch):
    """Mixed V2 statuses produce the right per-car states."""
    responses = {
        "c1": {"id": "i1", "inventory_car_id": "c1", "status": "complete"},
        "c2": {"id": "i2", "inventory_car_id": "c2", "status": "in_progress"},
        "c3": {"id": "i3", "inventory_car_id": "c3", "status": "draft"},
    }
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(responses))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1", "c2", "c3", "c4"]}))
    states = {r["carId"]: r["state"] for r in out["results"]}
    assert states["c1"] == "completed"
    assert states["c2"] == "in-progress"
    assert states["c3"] == "pending"   # draft → pending
    assert states["c4"] == "pending"   # no row → pending


# ---------------------------------------------------------------------------
# V2 scoring fields surfaced in results
# ---------------------------------------------------------------------------

def test_bulk_inspect_scores_surfaced(monkeypatch):
    """mechanical_grade, safety_status and counters appear under 'scores' when present."""
    responses = {
        "c1": {
            "id": "i1",
            "inventory_car_id": "c1",
            "status": "complete",
            "mechanical_grade": "B",
            "safety_status": "pass",
            "pass_count": 80,
            "fail_count": 3,
            "monitor_count": 5,
            "fixed_count": 2,
            "na_count": 10,
        }
    }
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(responses))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    scores = out["results"][0].get("scores", {})
    assert scores["mechanical_grade"] == "B"
    assert scores["safety_status"] == "pass"
    assert scores["pass_count"] == 80
    assert scores["fail_count"] == 3
    assert scores["monitor_count"] == 5
    assert scores["fixed_count"] == 2
    assert scores["na_count"] == 10


def test_bulk_inspect_scores_absent_when_null(monkeypatch):
    """When scoring columns are null (not yet scored), 'scores' key is absent."""
    responses = {
        "c1": {
            "id": "i1",
            "inventory_car_id": "c1",
            "status": "in_progress",
            "mechanical_grade": None,
            "safety_status": None,
            "pass_count": 0,
            "fail_count": 0,
            "monitor_count": 0,
            "fixed_count": 0,
            "na_count": 0,
        }
    }
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(responses))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    result = out["results"][0]
    # pass_count=0 etc. are non-null so they should appear in scores
    scores = result.get("scores", {})
    assert scores.get("pass_count") == 0
    assert "mechanical_grade" not in scores   # null → omitted
    assert "safety_status" not in scores      # null → omitted


def test_bulk_inspect_no_scores_key_when_no_row(monkeypatch):
    """When no row exists (pending), no 'scores' key is added."""
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient({}))
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    assert "scores" not in out["results"][0]




# ---------------------------------------------------------------------------
# Batched query (no N+1)
# ---------------------------------------------------------------------------

def test_bulk_inspect_single_query(monkeypatch):
    """Exactly ONE postgrest_get call regardless of carIds count (no N+1)."""
    client = FakeClient({
        "c1": {"id": "i1", "inventory_car_id": "c1", "status": "complete"},
        "c2": {"id": "i2", "inventory_car_id": "c2", "status": "in_progress"},
    })
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.bulk_inspect({"carIds": ["c1", "c2", "c3"]})
    assert client.call_count == 1, f"Expected 1 batched query, got {client.call_count}"


def test_bulk_inspect_uses_in_filter(monkeypatch):
    """The batched query must use the in.(...) PostgREST filter syntax."""
    client = FakeClient({})
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.bulk_inspect({"carIds": ["car-a", "car-b", "car-c"]})
    assert client.call_count == 1
    assert client.last_params is not None
    assert client.last_params["inventory_car_id"].startswith("in.("), (
        f"Expected in.(...) filter, got: {client.last_params.get('inventory_car_id')!r}"
    )
# ---------------------------------------------------------------------------
# Error / auth / validation paths
# ---------------------------------------------------------------------------

def test_bulk_inspect_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.bulk_inspect({"carIds": ["c1"]}))
    assert out["error"] == "not_signed_in"


def test_bulk_inspect_empty_car_ids():
    out = json.loads(mod.bulk_inspect({"carIds": []}))
    assert "error" in out


def test_bulk_inspect_too_many_ids():
    out = json.loads(mod.bulk_inspect({"carIds": ["c"] * 201}))
    assert "error" in out
    assert "200" in out["error"]
