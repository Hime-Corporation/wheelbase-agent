"""Tests for marker gating (_marker_active) and the pre_llm_call hook.

Tests monkeypatch TERMINAL_CWD (via os.environ) so that workspace_dir()
returns a tmp_path with or without the marker file.
"""

import os

import pytest

import wheelbase_onboarding as plugin
import wheelbase_onboarding.tools.clutch_react as clutch_react_mod

_MARKER = ".wheelbase-onboarding-active"


# ---------------------------------------------------------------------------
# Helper: create a fresh fake registration context
# ---------------------------------------------------------------------------

class FakeCtx:
    def __init__(self):
        self.tools: dict = {}
        self.hooks: list = []

    def register_tool(self, *, name, toolset, schema, handler, check_fn=None):
        self.tools[name] = {"toolset": toolset, "schema": schema,
                            "handler": handler, "check_fn": check_fn}

    def register_hook(self, event, fn):
        self.hooks.append((event, fn))


# ---------------------------------------------------------------------------
# _marker_active with marker absent
# ---------------------------------------------------------------------------

def test_marker_active_false_when_no_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    assert plugin._marker_active() is False


# ---------------------------------------------------------------------------
# _marker_active with marker present
# ---------------------------------------------------------------------------

def test_marker_active_true_when_marker_present(tmp_path, monkeypatch):
    (tmp_path / _MARKER).touch()
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    assert plugin._marker_active() is True


# ---------------------------------------------------------------------------
# _marker_active with non-existent workspace dir
# ---------------------------------------------------------------------------

def test_marker_active_false_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path / "nonexistent"))
    assert plugin._marker_active() is False


# ---------------------------------------------------------------------------
# register wires check_fn correctly
# ---------------------------------------------------------------------------

def test_register_wires_check_fn():
    ctx = FakeCtx()
    plugin.register(ctx)

    assert "clutch_react" in ctx.tools
    assert "complete_onboarding" in ctx.tools

    assert ctx.tools["clutch_react"]["check_fn"] is plugin._marker_active
    assert ctx.tools["complete_onboarding"]["check_fn"] is plugin._marker_active


def test_register_wires_toolset():
    ctx = FakeCtx()
    plugin.register(ctx)
    assert ctx.tools["clutch_react"]["toolset"] == "wheelbase_onboarding"
    assert ctx.tools["complete_onboarding"]["toolset"] == "wheelbase_onboarding"


def test_register_wires_pre_llm_call_hook():
    ctx = FakeCtx()
    plugin.register(ctx)
    hook_names = [event for event, _ in ctx.hooks]
    assert "pre_llm_call" in hook_names


# ---------------------------------------------------------------------------
# pre_llm_call hook returns None when marker absent
# ---------------------------------------------------------------------------

def test_pre_llm_call_returns_none_without_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    result = plugin._pre_llm_call_hook()
    assert result is None


# ---------------------------------------------------------------------------
# pre_llm_call hook injects context when marker present (no workspace files)
# ---------------------------------------------------------------------------

def test_pre_llm_call_injects_context_with_marker(tmp_path, monkeypatch):
    (tmp_path / _MARKER).touch()
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    result = plugin._pre_llm_call_hook()
    assert result is not None
    assert "context" in result
    assert "Wheelbase Onboarding Mode" in result["context"]
    assert "Clutch" in result["context"]


# ---------------------------------------------------------------------------
# pre_llm_call hook includes DEALERSHIP.md when present
# ---------------------------------------------------------------------------

def test_pre_llm_call_includes_dealership_md(tmp_path, monkeypatch):
    (tmp_path / _MARKER).touch()
    (tmp_path / "DEALERSHIP.md").write_text("# Acme Motors\nLocation: Austin, TX")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    result = plugin._pre_llm_call_hook()
    assert result is not None
    ctx_text = result["context"]
    assert "## DEALERSHIP context:" in ctx_text
    assert "Acme Motors" in ctx_text


# ---------------------------------------------------------------------------
# pre_llm_call hook includes TEAM.md when present
# ---------------------------------------------------------------------------

def test_pre_llm_call_includes_team_md(tmp_path, monkeypatch):
    (tmp_path / _MARKER).touch()
    (tmp_path / "TEAM.md").write_text("# Team\n- Alice: GM")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    result = plugin._pre_llm_call_hook()
    assert result is not None
    ctx_text = result["context"]
    assert "## TEAM context:" in ctx_text
    assert "Alice" in ctx_text


# ---------------------------------------------------------------------------
# pre_llm_call hook includes both DEALERSHIP.md and TEAM.md when both present
# ---------------------------------------------------------------------------

def test_pre_llm_call_includes_both_context_files(tmp_path, monkeypatch):
    (tmp_path / _MARKER).touch()
    (tmp_path / "DEALERSHIP.md").write_text("Dealership info here.")
    (tmp_path / "TEAM.md").write_text("Team info here.")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    result = plugin._pre_llm_call_hook()
    assert result is not None
    ctx_text = result["context"]
    assert "## DEALERSHIP context:" in ctx_text
    assert "## TEAM context:" in ctx_text
    assert "Dealership info here." in ctx_text
    assert "Team info here." in ctx_text


# ---------------------------------------------------------------------------
# pre_llm_call hook omits empty/whitespace-only workspace files
# ---------------------------------------------------------------------------

def test_pre_llm_call_omits_empty_dealership_md(tmp_path, monkeypatch):
    (tmp_path / _MARKER).touch()
    (tmp_path / "DEALERSHIP.md").write_text("   \n  ")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    result = plugin._pre_llm_call_hook()
    assert result is not None
    assert "## DEALERSHIP context:" not in result["context"]


# ---------------------------------------------------------------------------
# Addendum content spot-checks
# ---------------------------------------------------------------------------

def test_addendum_contains_interview_prompt(tmp_path, monkeypatch):
    (tmp_path / _MARKER).touch()
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    result = plugin._pre_llm_call_hook()
    ctx_text = result["context"]
    assert "ONBOARDING_INTERVIEW Prompt" in ctx_text


def test_addendum_contains_mascot_states(tmp_path, monkeypatch):
    (tmp_path / _MARKER).touch()
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    result = plugin._pre_llm_call_hook()
    ctx_text = result["context"]
    # A sampling of mascot states should appear in the addendum
    for state in ("champ", "boost", "nitro", "think", "finish"):
        assert state in ctx_text


def test_addendum_contains_completion_signal_section(tmp_path, monkeypatch):
    (tmp_path / _MARKER).touch()
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    result = plugin._pre_llm_call_hook()
    assert "complete_onboarding" in result["context"]


# ---------------------------------------------------------------------------
# check_fn honours marker dynamically (toggle test)
# ---------------------------------------------------------------------------

def test_check_fn_toggles_with_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    assert plugin._marker_active() is False

    marker_file = tmp_path / _MARKER
    marker_file.touch()
    assert plugin._marker_active() is True

    marker_file.unlink()
    assert plugin._marker_active() is False
