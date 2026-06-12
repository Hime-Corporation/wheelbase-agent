"""Tests for the clutch_react tool handler."""

import json

import wheelbase_onboarding.tools.clutch_react as mod


# ---------------------------------------------------------------------------
# Happy-path: required field only
# ---------------------------------------------------------------------------

def test_clutch_react_minimal():
    out = json.loads(mod.clutch_react({"state": "idle"}))
    assert out["kind"] == "clutch_react"
    assert out["state"] == "idle"
    assert out["ttlMs"] == 4000
    assert "speech" not in out
    assert "tip" not in out


# ---------------------------------------------------------------------------
# All optional fields present
# ---------------------------------------------------------------------------

def test_clutch_react_all_fields():
    out = json.loads(mod.clutch_react({
        "state": "champ",
        "speech": "Nice work!",
        "tip": "Keep going.",
        "ttlMs": 6000,
    }))
    assert out["state"] == "champ"
    assert out["speech"] == "Nice work!"
    assert out["tip"] == "Keep going."
    assert out["ttlMs"] == 6000


# ---------------------------------------------------------------------------
# Unknown state falls back to "idle"
# ---------------------------------------------------------------------------

def test_clutch_react_unknown_state_fallback():
    out = json.loads(mod.clutch_react({"state": "nonexistent_state"}))
    assert out["state"] == "idle"


# ---------------------------------------------------------------------------
# All valid mascot states are accepted
# ---------------------------------------------------------------------------

def test_clutch_react_all_valid_states():
    states = [
        "idle", "speed", "cry", "code", "nitro", "alert", "repair",
        "champ", "finish", "think", "greeting", "loading", "boost", "tune",
    ]
    for state in states:
        out = json.loads(mod.clutch_react({"state": state}))
        assert out["state"] == state, f"Expected {state}, got {out['state']}"


# ---------------------------------------------------------------------------
# speech / tip truncation
# ---------------------------------------------------------------------------

def test_clutch_react_speech_truncated_at_140():
    long_speech = "x" * 200
    out = json.loads(mod.clutch_react({"state": "idle", "speech": long_speech}))
    assert len(out["speech"]) == 140


def test_clutch_react_tip_truncated_at_240():
    long_tip = "y" * 300
    out = json.loads(mod.clutch_react({"state": "idle", "tip": long_tip}))
    assert len(out["tip"]) == 240


# ---------------------------------------------------------------------------
# ttlMs clamping
# ---------------------------------------------------------------------------

def test_clutch_react_ttl_clamped_to_max():
    out = json.loads(mod.clutch_react({"state": "idle", "ttlMs": 99999}))
    assert out["ttlMs"] == 15000


def test_clutch_react_ttl_clamped_to_min():
    out = json.loads(mod.clutch_react({"state": "idle", "ttlMs": -100}))
    assert out["ttlMs"] == 0


# ---------------------------------------------------------------------------
# Missing / empty required field
# ---------------------------------------------------------------------------

def test_clutch_react_missing_state_returns_error():
    out = json.loads(mod.clutch_react({}))
    assert "error" in out


def test_clutch_react_empty_state_returns_error():
    out = json.loads(mod.clutch_react({"state": ""}))
    assert "error" in out


# ---------------------------------------------------------------------------
# Empty speech / tip are omitted
# ---------------------------------------------------------------------------

def test_clutch_react_empty_speech_omitted():
    out = json.loads(mod.clutch_react({"state": "idle", "speech": ""}))
    assert "speech" not in out


def test_clutch_react_empty_tip_omitted():
    out = json.loads(mod.clutch_react({"state": "idle", "tip": ""}))
    assert "tip" not in out


# ---------------------------------------------------------------------------
# Empty args dict
# ---------------------------------------------------------------------------

def test_clutch_react_empty_args_returns_error():
    # state is required
    out = json.loads(mod.clutch_react({}))
    assert "error" in out
