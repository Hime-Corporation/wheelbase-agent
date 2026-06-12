"""Tests for complete_demand_matrix_setup (Go-API write tool)."""

import json

import wheelbase_demand_matrix.tools.complete_demand_matrix_setup as mod
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


class TestCompleteDemandMatrixSetup:
    def test_calls_go_api(self, monkeypatch):
        client = FakeClient(response={"ok": True})
        monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
        out = json.loads(mod.complete_demand_matrix_setup({}))
        assert "error" not in out
        assert len(client.calls) == 1
        method, path, _ = client.calls[0]
        assert method == "POST"
        assert path == "/demand-matrix/complete"

    def test_signed_out(self, monkeypatch):
        def boom():
            raise WheelbaseAuthError("no session")
        monkeypatch.setattr(mod, "WheelbaseClient", boom)
        out = json.loads(mod.complete_demand_matrix_setup({}))
        assert out["error"] == "not_signed_in"

    def test_none_response_returns_kind(self, monkeypatch):
        client = FakeClient(response=None)
        monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
        out = json.loads(mod.complete_demand_matrix_setup({}))
        assert out["kind"] == "complete_demand_matrix_setup"

    def test_client_exception_returns_err(self, monkeypatch):
        class BoomClient:
            def go_api(self, *a, **kw):
                raise RuntimeError("timeout")
            def close(self):
                pass
        monkeypatch.setattr(mod, "WheelbaseClient", BoomClient)
        out = json.loads(mod.complete_demand_matrix_setup({}))
        assert "error" in out

    def test_ignores_args(self, monkeypatch):
        client = FakeClient(response={"done": True})
        monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
        out = json.loads(mod.complete_demand_matrix_setup({"extra": "ignored"}))
        assert "error" not in out
