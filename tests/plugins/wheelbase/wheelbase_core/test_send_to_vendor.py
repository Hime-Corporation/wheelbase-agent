"""Tests for send_to_vendor tool."""

import json

import wheelbase_core.tools.send_to_vendor as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self):
        self.calls = []

    def postgrest_write(self, method, table, *, body=None, params=None, prefer="return=representation"):
        self.calls.append({"method": method, "table": table, "body": body, "params": params})

    def close(self):
        pass


def test_send_to_vendor_single_patch(monkeypatch):
    """send_to_vendor must issue exactly ONE PATCH with status='blocked' and hold_type='vendor'."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.send_to_vendor({"workOrderId": "wo1", "vendorId": "v1"}))
    assert "error" not in out
    assert out["workItemId"] == "wo1"
    assert out["vendorId"] == "v1"
    assert out["status"] == "blocked"
    assert out["holdType"] == "vendor"
    assert len(client.calls) == 1, f"Expected 1 PATCH call, got {len(client.calls)}"


def test_send_to_vendor_patch_body(monkeypatch):
    """The single PATCH body must set vendor_id, status='blocked', hold_type='vendor'."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.send_to_vendor({"workOrderId": "wo1", "vendorId": "v1"})
    patch = client.calls[0]
    assert patch["body"]["vendor_id"] == "v1"
    assert patch["body"]["status"] == "blocked"
    assert patch["body"]["hold_type"] == "vendor"
    assert patch["params"]["id"] == "eq.wo1"


def test_send_to_vendor_scheduled_at_included(monkeypatch):
    """scheduled_at (a valid work_item column) is forwarded in the single PATCH when provided."""
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.send_to_vendor({"workOrderId": "wo1", "vendorId": "v1", "scheduledAt": "2026-07-01T10:00:00Z"})
    patch = client.calls[0]
    assert patch["body"]["vendor_id"] == "v1"
    assert patch["body"]["status"] == "blocked"
    assert patch["body"]["hold_type"] == "vendor"
    assert patch["body"]["scheduled_at"] == "2026-07-01T10:00:00Z"
    assert patch["params"]["id"] == "eq.wo1"


def test_send_to_vendor_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.send_to_vendor({"workOrderId": "wo1", "vendorId": "v1"}))
    assert out["error"] == "not_signed_in"


def test_send_to_vendor_missing_work_order_id():
    out = json.loads(mod.send_to_vendor({"vendorId": "v1"}))
    assert "error" in out
    assert "workOrderId" in out["error"]


def test_send_to_vendor_missing_vendor_id():
    out = json.loads(mod.send_to_vendor({"workOrderId": "wo1"}))
    assert "error" in out
    assert "vendorId" in out["error"]
