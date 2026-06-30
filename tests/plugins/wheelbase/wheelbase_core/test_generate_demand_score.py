"""Tests for generate_demand_score tool — backend AI (go_api) implementation."""

import json

import wheelbase_core.tools.generate_demand_score as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, response=None):
        self._response = response or {"ranked": [], "summary": "ok"}
        self.go_api_calls = []

    def go_api(self, method, path, *, body=None, params=None):
        self.go_api_calls.append({"method": method, "path": path, "body": body, "params": params})
        return self._response

    def close(self):
        pass


def test_generate_demand_score_calls_go_api(monkeypatch):
    """generate_demand_score delegates to the backend /v1/ai/rank/matrix endpoint."""
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    out = json.loads(mod.generate_demand_score({}))
    assert len(fake.go_api_calls) == 1
    call = fake.go_api_calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/v1/ai/rank/matrix"


def test_generate_demand_score_returns_backend_result(monkeypatch):
    backend_payload = {"ranked": [{"carId": "c1", "score": 0.85}], "summary": "1 vehicle ranked"}
    fake = FakeClient(response=backend_payload)
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    out = json.loads(mod.generate_demand_score({}))
    assert out["ranked"][0]["carId"] == "c1"
    assert out["ranked"][0]["score"] == 0.85


def test_generate_demand_score_passes_top_k(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.generate_demand_score({"topK": 20})
    body = fake.go_api_calls[0]["body"]
    assert body["topK"] == 20


def test_generate_demand_score_passes_provider_and_mode(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.generate_demand_score({"provider": "gemini", "mode": "hybrid"})
    body = fake.go_api_calls[0]["body"]
    assert body["provider"] == "gemini"
    assert body["mode"] == "hybrid"


def test_generate_demand_score_passes_min_gap_ratio(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.generate_demand_score({"minGapRatio": 0.2})
    body = fake.go_api_calls[0]["body"]
    assert body["minGapRatio"] == 0.2


def test_generate_demand_score_empty_body_when_no_args(monkeypatch):
    """No args → body is empty dict (backend applies defaults)."""
    fake = FakeClient()
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: fake)
    mod.generate_demand_score({})
    body = fake.go_api_calls[0]["body"]
    assert isinstance(body, dict)
    assert "topK" not in body
    assert "provider" not in body


def test_generate_demand_score_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.generate_demand_score({}))
    assert out["error"] == "not_signed_in"


def test_generate_demand_score_invalid_car_ids():
    out = json.loads(mod.generate_demand_score({"carIds": []}))
    assert "error" in out


def test_generate_demand_score_too_many_car_ids():
    out = json.loads(mod.generate_demand_score({"carIds": ["c"] * 201}))
    assert "error" in out
    assert "200" in out["error"]
