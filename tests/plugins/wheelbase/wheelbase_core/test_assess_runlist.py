"""Tests for assess_runlist tool — backend AI (go_api) implementation."""

import json

import wheelbase_core.tools.assess_runlist as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, response=None):
        self._response = response or {"runlistId": "rl1", "scores": []}
        self.go_api_calls = []

    def go_api(self, method, path, *, body=None, params=None):
        self.go_api_calls.append({"method": method, "path": path, "body": body, "params": params})
        return self._response

    def close(self):
        pass


def test_assess_runlist_calls_go_api(monkeypatch):
    """assess_runlist delegates to the backend /v1/ai/imx/runlist endpoint."""
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    out = json.loads(mod.assess_runlist({"runlistId": "rl1"}))
    assert out["runlistId"] == "rl1"
    assert len(fake.go_api_calls) == 1
    call = fake.go_api_calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/v1/ai/imx/runlist"


def test_assess_runlist_sends_runlist_id_in_body(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.assess_runlist({"runlistId": "abc-123"})
    body = fake.go_api_calls[0]["body"]
    assert body["runlistId"] == "abc-123"


def test_assess_runlist_sends_criteria_in_body_when_provided(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.assess_runlist({"runlistId": "rl1", "criteria": "low mileage sedan"})
    body = fake.go_api_calls[0]["body"]
    assert body.get("criteria") == "low mileage sedan"


def test_assess_runlist_omits_criteria_when_blank(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.assess_runlist({"runlistId": "rl1", "criteria": ""})
    body = fake.go_api_calls[0]["body"]
    assert "criteria" not in body


def test_assess_runlist_returns_backend_result(monkeypatch):
    backend_payload = {"runlistId": "rl1", "scores": [{"carId": "c1", "score": 0.9}]}
    fake = FakeClient(response=backend_payload)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    out = json.loads(mod.assess_runlist({"runlistId": "rl1"}))
    assert out["scores"][0]["carId"] == "c1"
    assert out["scores"][0]["score"] == 0.9


def test_assess_runlist_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.assess_runlist({"runlistId": "rl1"}))
    assert out["error"] == "not_signed_in"


def test_assess_runlist_missing_runlist_id():
    out = json.loads(mod.assess_runlist({}))
    assert "error" in out
    assert "runlistId" in out["error"]
