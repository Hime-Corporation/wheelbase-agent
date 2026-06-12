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


def test_send_to_vendor_two_patches(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    out = json.loads(mod.send_to_vendor({"workOrderId": "wo1", "vendorId": "v1"}))
    assert out["workOrderId"] == "wo1"
    assert out["vendorId"] == "v1"
    assert out["status"] == "scheduled"
    assert len(client.calls) == 2


def test_send_to_vendor_step1_sets_vendor(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.send_to_vendor({"workOrderId": "wo1", "vendorId": "v1", "scheduledAt": "2026-07-01T10:00:00Z"})
    step1 = client.calls[0]
    assert step1["body"]["vendor_id"] == "v1"
    assert step1["body"]["scheduled_at"] == "2026-07-01T10:00:00Z"
    assert step1["params"]["id"] == "eq.wo1"


def test_send_to_vendor_step2_sets_scheduled(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.send_to_vendor({"workOrderId": "wo1", "vendorId": "v1"})
    step2 = client.calls[1]
    assert step2["body"]["status"] == "scheduled"
    assert step2["params"]["id"] == "eq.wo1"


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
