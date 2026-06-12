"""Tests for the complete_onboarding tool handler."""

import json

import wheelbase_onboarding.tools.complete_onboarding as mod


def test_complete_onboarding_returns_kind():
    out = json.loads(mod.complete_onboarding({}))
    assert out["kind"] == "complete_onboarding"


def test_complete_onboarding_no_extra_fields():
    out = json.loads(mod.complete_onboarding({}))
    assert set(out.keys()) == {"kind"}


def test_complete_onboarding_ignores_extra_args():
    # extra args are silently ignored — the tool takes no parameters
    out = json.loads(mod.complete_onboarding({"unexpected": "arg"}))
    assert out["kind"] == "complete_onboarding"


def test_complete_onboarding_none_args():
    # args=None should not crash; tools may receive None
    try:
        out = json.loads(mod.complete_onboarding(None))  # type: ignore[arg-type]
        assert out["kind"] == "complete_onboarding"
    except Exception as exc:
        raise AssertionError(f"complete_onboarding raised on None args: {exc}") from exc
