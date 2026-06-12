"""Tests for save_demand_overrides (Go-API write tool)."""

import json

import wheelbase_demand_matrix.tools.save_demand_overrides as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, response=None):
        self._response = response
        self.calls = []

    def go_api(self, method, path, *, body=None, params=None):
        self.calls.append((method, path, body))
        return self._response

    def close(self):
        pass


VALID_OVERRIDES = [
    {"key": "suv", "target": 10},
    {"key": "sedan", "target": 8, "keywords": ["compact", "fuel-efficient"]},
]


class TestSaveDemandOverrides:
    def test_calls_go_api(self, monkeypatch):
        client = FakeClient({"saved": 2})
        monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
        out = json.loads(mod.save_demand_overrides({"overrides": VALID_OVERRIDES}))
        assert "saved" in out or "error" not in out
        assert len(client.calls) == 1
        method, path, body = client.calls[0]
        assert method == "PATCH"
        assert path == "/demand-matrix/overrides"
        assert len(body["overrides"]) == 2

    def test_signed_out(self, monkeypatch):
        monkeypatch.setattr(mod, "WheelbaseClient", lambda: (_ for _ in ()).throw(WheelbaseAuthError("no session")))
        out = json.loads(mod.save_demand_overrides({"overrides": VALID_OVERRIDES}))
        assert out["error"] == "not_signed_in"

    def test_error_on_missing_overrides(self, monkeypatch):
        out = json.loads(mod.save_demand_overrides({}))
        assert "error" in out

    def test_error_on_empty_overrides(self, monkeypatch):
        out = json.loads(mod.save_demand_overrides({"overrides": []}))
        assert "error" in out

    def test_error_on_invalid_key(self, monkeypatch):
        out = json.loads(mod.save_demand_overrides({"overrides": [{"key": "", "target": 5}]}))
        assert "error" in out

    def test_error_on_target_out_of_range(self, monkeypatch):
        out = json.loads(mod.save_demand_overrides({"overrides": [{"key": "suv", "target": 999}]}))
        assert "error" in out

    def test_none_response_returns_saved_count(self, monkeypatch):
        client = FakeClient(response=None)
        monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
        out = json.loads(mod.save_demand_overrides({"overrides": VALID_OVERRIDES}))
        assert out["saved"] == len(VALID_OVERRIDES)

    def test_client_exception_returns_err(self, monkeypatch):
        class BoomClient:
            def go_api(self, *a, **kw):
                raise RuntimeError("network down")
            def close(self):
                pass
        monkeypatch.setattr(mod, "WheelbaseClient", BoomClient)
        out = json.loads(mod.save_demand_overrides({"overrides": VALID_OVERRIDES}))
        assert "error" in out
