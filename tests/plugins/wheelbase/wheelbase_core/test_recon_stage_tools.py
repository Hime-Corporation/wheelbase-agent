"""Tests for recon_stage_tools (complete_stage, update_stage, create_finding) — Batch B5."""

import json

import wheelbase_core.tools.recon_stage_tools as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self):
        self.calls = []

    def postgrest_get(self, table, params):
        self.calls.append({"method": "GET", "table": table, "params": dict(params)})
        return []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append({"method": method, "table": table, "body": body, "params": params})
        return [{"id": "wi-1", "status": body.get("status", "todo") if body else "todo"}]

    def close(self):
        pass


# ---------------------------------------------------------------------------
# complete_stage
# ---------------------------------------------------------------------------

def test_complete_stage_patches_status_done(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.complete_stage({"stageId": "stage-1"}))
    assert "error" not in out
    write = next(c for c in client.calls if c["method"] == "PATCH")
    assert write["table"] == "work_item"
    assert write["body"]["status"] == "done"
    assert write["params"]["id"] == "eq.stage-1"


def test_complete_stage_requires_stage_id():
    out = json.loads(mod.complete_stage({}))
    assert "error" in out
    assert "stageId" in out["error"]


def test_complete_stage_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no")
    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.complete_stage({"stageId": "s"}))
    assert out["error"] == "not_signed_in"


# ---------------------------------------------------------------------------
# update_stage
# ---------------------------------------------------------------------------

def test_update_stage_sets_status(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.update_stage({"stageId": "s-1", "status": "in_progress"}))
    assert "error" not in out
    write = next(c for c in client.calls if c["method"] == "PATCH")
    assert write["body"]["status"] == "in_progress"


def test_update_stage_hold_type_forces_blocked_status(monkeypatch):
    """When holdType is set and no explicit status, status must be forced to 'blocked'."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.update_stage({"stageId": "s-1", "holdType": "parts"}))
    assert "error" not in out
    write = next(c for c in client.calls if c["method"] == "PATCH")
    assert write["body"]["status"] == "blocked"
    assert write["body"]["hold_type"] == "parts"


def test_update_stage_explicit_status_overrides_hold_type_default(monkeypatch):
    """Explicit status is used even when holdType is provided."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.update_stage({"stageId": "s-1", "holdType": "vendor", "status": "in_progress"}))
    assert "error" not in out
    write = next(c for c in client.calls if c["method"] == "PATCH")
    assert write["body"]["status"] == "in_progress"


def test_update_stage_invalid_hold_type(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient())
    out = json.loads(mod.update_stage({"stageId": "s-1", "holdType": "magic"}))
    assert "error" in out
    assert "holdType" in out["error"]


def test_update_stage_requires_stage_id():
    out = json.loads(mod.update_stage({}))
    assert "error" in out
    assert "stageId" in out["error"]


def test_update_stage_no_fields_err(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.update_stage({"stageId": "s-1"}))
    assert "error" in out


def test_update_stage_assigns_vendor(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.update_stage({"stageId": "s-1", "vendorId": "v-99"}))
    assert "error" not in out
    write = next(c for c in client.calls if c["method"] == "PATCH")
    assert write["body"]["vendor_id"] == "v-99"


def test_update_stage_est_cost_cents(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.update_stage({"stageId": "s-1", "estCostCents": 5000}))
    assert "error" not in out
    write = next(c for c in client.calls if c["method"] == "PATCH")
    assert write["body"]["est_cost_cents"] == 5000


# ---------------------------------------------------------------------------
# create_finding
# ---------------------------------------------------------------------------

def test_create_finding_requires_parent_id():
    out = json.loads(mod.create_finding({"title": "Dent"}))
    assert "error" in out
    assert "parentId" in out["error"]


def test_create_finding_requires_title():
    out = json.loads(mod.create_finding({"parentId": "stage-1"}))
    assert "error" in out
    assert "title" in out["error"]


def test_create_finding_delegates_to_create_work_item(monkeypatch):
    """create_finding must call create_work_item with type='finding'."""
    captured = {}

    def fake_create_work_item(args):
        captured.update(args)
        # Return a valid ok() response
        import json as _json
        return _json.dumps({"workItemId": "wi-new", "type": "finding", "status": "todo", "title": args["title"], "carId": None})

    import wheelbase_core.tools.create_work_item as cwi_mod
    monkeypatch.setattr(cwi_mod, "create_work_item", fake_create_work_item)

    # Also patch the import inside recon_stage_tools
    import importlib
    import wheelbase_core.tools.recon_stage_tools as rst_mod
    monkeypatch.setattr(
        rst_mod,
        "create_finding",
        rst_mod.create_finding,  # use real one but patch the dep
    )

    # Direct call — patch via the cwi module
    import wheelbase_core.tools.create_work_item as cwi
    monkeypatch.setattr(cwi, "create_work_item", fake_create_work_item)

    out = json.loads(mod.create_finding({"parentId": "stage-1", "title": "Dent on hood", "estCostCents": 300}))
    # Either we get a success response or the captured dict has the right type
    assert captured.get("type") == "finding"
    assert captured.get("parentId") == "stage-1"
    assert captured.get("title") == "Dent on hood"
    assert captured.get("estCostCents") == 300


def test_create_finding_sets_type_finding(monkeypatch):
    """The delegated call must always set type='finding'."""
    import wheelbase_core.tools.create_work_item as cwi

    class _FakeWBClient:
        def __init__(self): self.calls = []
        def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
            self.calls.append((method, table, body))
            return [{"id": "wi-new", "title": body["title"], "status": "todo", "type": body["type"]}]
        def close(self): pass

    monkeypatch.setattr(cwi, "WheelbaseClient", lambda: _FakeWBClient())
    out = json.loads(mod.create_finding({"parentId": "stage-1", "title": "Crack in windshield"}))
    assert "error" not in out
    assert out.get("type") == "finding"
