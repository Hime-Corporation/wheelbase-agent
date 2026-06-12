"""Tests for list_runlists tool."""

import json

import wheelbase_core.tools.list_runlists as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.last_params = None

    def postgrest_get(self, table, params):
        self.last_params = params
        return self._rows

    def close(self):
        pass


def test_list_runlists_returns_summaries(monkeypatch):
    rows = [
        {
            "runlist_id": "r1",
            "name": "June Auction",
            "auction_id": "a1",
            "auction_date": "2026-06-15",
            "created_at": "2026-06-01T00:00:00Z",
        }
    ]
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(rows))
    out = json.loads(mod.list_runlists({}))
    assert len(out) == 1
    assert out[0]["runlistId"] == "r1"
    assert out[0]["name"] == "June Auction"


def test_list_runlists_default_limit(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.list_runlists({})
    assert client.last_params["limit"] == "25"


def test_list_runlists_custom_limit(monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: client)
    mod.list_runlists({"limit": 10})
    assert client.last_params["limit"] == "10"


def test_list_runlists_signed_out(monkeypatch):
    def boom():
        raise WheelbaseAuthError("no session")

    monkeypatch.setattr(mod, "WheelbaseClient", boom)
    out = json.loads(mod.list_runlists({}))
    assert out["error"] == "not_signed_in"
