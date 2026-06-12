"""Tests for assess_runlist tool."""

import json

import wheelbase_core.tools.assess_runlist as mod
from wheelbase_sdk.errors import WheelbaseAuthError


class FakeClient:
    def __init__(self, rows=None):
        self._rows = rows or []

    def postgrest_get(self, table, params):
        return self._rows

    def close(self):
        pass


def test_assess_runlist_summary(monkeypatch):
    rows = [
        {"id": "c1", "make": "Honda", "model": "Civic", "year": 2020, "imx_score": 70},
        {"id": "c2", "make": "Toyota", "model": "Camry", "year": 2019, "imx_score": 80},
    ]
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(rows))
    out = json.loads(mod.assess_runlist({"runlistId": "rl1"}))
    assert out["assessed"] == 2
    assert out["topScore"] == 80
    assert out["avgScore"] == 75


def test_assess_runlist_criteria_bonus(monkeypatch):
    rows = [
        {"id": "c1", "make": "Honda", "model": "Civic", "year": 2020, "imx_score": 70},
    ]
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient(rows))
    out = json.loads(mod.assess_runlist({"runlistId": "rl1", "criteria": "Honda"}))
    # criteria matches → bonus 10 pts, 70+10=80
    assert out["topScore"] == 80


def test_assess_runlist_empty(monkeypatch):
    monkeypatch.setattr(mod, "WheelbaseClient", lambda: FakeClient([]))
    out = json.loads(mod.assess_runlist({"runlistId": "rl1"}))
    assert out["assessed"] == 0
    assert "No cars" in out["summary"]


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
