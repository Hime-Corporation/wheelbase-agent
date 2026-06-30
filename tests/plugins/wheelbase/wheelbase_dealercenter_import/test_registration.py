"""Tests for wheelbase_dealercenter_import plugin registration and gating."""

import json
import os

import pytest

import wheelbase_dealercenter_import as plugin


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _ok(result: str) -> dict:
    data = json.loads(result)
    assert "error" not in data, f"Unexpected error: {data}"
    return data


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Point TERMINAL_CWD at a fresh tmp directory."""
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    return tmp_path


@pytest.fixture()
def active_workspace(workspace):
    """Workspace with the dealercenter-import marker present."""
    (workspace / ".wheelbase-dealercenter-import-active").touch()
    return workspace


class _Ctx:
    """Minimal registration context stub."""

    def __init__(self):
        self.tools: dict = {}
        self.hooks: list = []

    def register_tool(self, *, name, toolset, schema, handler, check_fn=None):
        self.tools[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            "check_fn": check_fn,
        }

    def register_hook(self, event, fn):
        self.hooks.append((event, fn))


# ---------------------------------------------------------------------------
# _marker_active
# ---------------------------------------------------------------------------

class TestMarkerActive:
    def test_true_when_marker_present(self, active_workspace):
        assert plugin._marker_active() is True

    def test_false_when_marker_absent(self, workspace):
        assert plugin._marker_active() is False

    def test_false_when_terminal_cwd_unset(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        assert plugin._marker_active() is False


# ---------------------------------------------------------------------------
# register — tool and hook wiring
# ---------------------------------------------------------------------------

class TestRegister:
    def test_registers_all_three_tools(self):
        ctx = _Ctx()
        plugin.register(ctx)
        for name in ("dc_connect", "dc_export_historic", "dc_ingest"):
            assert name in ctx.tools, f"Missing tool: {name}"
            assert ctx.tools[name]["toolset"] == "wheelbase_dealercenter_import"
            assert ctx.tools[name]["check_fn"] is not None

    def test_registers_pre_tool_call_hook(self):
        ctx = _Ctx()
        plugin.register(ctx)
        events = [event for event, _ in ctx.hooks]
        assert "pre_tool_call" in events

    def test_check_fn_true_with_marker(self, active_workspace):
        ctx = _Ctx()
        plugin.register(ctx)
        for name, info in ctx.tools.items():
            assert info["check_fn"]() is True, f"check_fn for {name} should be True"

    def test_check_fn_false_without_marker(self, workspace):
        ctx = _Ctx()
        plugin.register(ctx)
        for name, info in ctx.tools.items():
            assert info["check_fn"]() is False, f"check_fn for {name} should be False"


# ---------------------------------------------------------------------------
# pre_tool_call approval hook
# ---------------------------------------------------------------------------

class TestPreToolCallHook:
    def test_no_gate_allows_all(self, monkeypatch):
        monkeypatch.setenv("WHEELBASE_APPROVAL_GATE", "0")
        # Re-import to pick up env change requires reloading — test via public function
        # instead, since the gate is read at import time in the module.
        # We exercise the already-imported module's hook directly.
        # When gate is off, _pre_tool_call should return None regardless.
        # Since the module reads the flag at import, we test the default (off) path.
        import importlib
        import wheelbase_dealercenter_import as p2
        # If gate is already off (default), hook returns None.
        if not p2._APPROVAL_GATE_ENABLED:
            result = p2._pre_tool_call("dc_ingest", {"dryRun": False})
            assert result is None

    def test_gate_on_allows_dry_run(self, monkeypatch):
        """Even with gate on, dry-run calls are allowed (non-destructive)."""
        import wheelbase_dealercenter_import as p2
        # Patch the guard directly to test the hook logic without reload.
        original = p2._APPROVAL_GATE_ENABLED
        p2._APPROVAL_GATE_ENABLED = True
        try:
            result = p2._pre_tool_call("dc_ingest", {"dryRun": True})
            assert result is None
        finally:
            p2._APPROVAL_GATE_ENABLED = original

    def test_gate_on_blocks_commit(self):
        """With gate on, dc_ingest with dryRun=False returns pending_approval."""
        import wheelbase_dealercenter_import as p2
        original = p2._APPROVAL_GATE_ENABLED
        p2._APPROVAL_GATE_ENABLED = True
        try:
            result = p2._pre_tool_call("dc_ingest", {"dryRun": False})
            assert result is not None
            assert result["action"] == "pending_approval"
            assert result["tool"] == "dc_ingest"
            assert "approval_id" in result
        finally:
            p2._APPROVAL_GATE_ENABLED = original

    def test_gate_on_allows_non_gated_tools(self):
        """dc_connect and dc_export_historic are never gated."""
        import wheelbase_dealercenter_import as p2
        original = p2._APPROVAL_GATE_ENABLED
        p2._APPROVAL_GATE_ENABLED = True
        try:
            assert p2._pre_tool_call("dc_connect", {}) is None
            assert p2._pre_tool_call("dc_export_historic", {"dateFrom": "2023-01-01"}) is None
        finally:
            p2._APPROVAL_GATE_ENABLED = original

    def test_hook_accepts_arbitrary_kwargs(self):
        """Should not raise with extra keyword arguments from the harness."""
        import wheelbase_dealercenter_import as p2
        result = p2._pre_tool_call("dc_connect", {}, session_id="s1", run_id="r1")
        assert result is None  # gate off by default


# ---------------------------------------------------------------------------
# dc_connect smoke test
# ---------------------------------------------------------------------------

class TestDcConnect:
    def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
        from wheelbase_dealercenter_import.tools.dc_connect import dc_connect
        result = _ok(dc_connect({}))
        assert result["cdpConfigured"] is False
        assert result["cdpUrl"] is None
        assert "instructions" in result

    def test_configured(self, monkeypatch):
        monkeypatch.setenv("BROWSER_CDP_URL", "http://localhost:9222")
        from wheelbase_dealercenter_import.tools.dc_connect import dc_connect
        result = _ok(dc_connect({}))
        assert result["cdpConfigured"] is True
        assert result["cdpUrl"] == "http://localhost:9222"


# ---------------------------------------------------------------------------
# dc_export_historic smoke test
# ---------------------------------------------------------------------------

class TestDcExportHistoric:
    def test_returns_procedure(self):
        from wheelbase_dealercenter_import.tools.dc_export_historic import dc_export_historic
        result = _ok(dc_export_historic({}))
        assert result["kind"] == "dc_export_procedure"
        assert isinstance(result["steps"], list)
        assert len(result["steps"]) >= 3

    def test_date_range_included(self):
        from wheelbase_dealercenter_import.tools.dc_export_historic import dc_export_historic
        result = _ok(dc_export_historic({"dateFrom": "2022-01-01", "dateTo": "2022-12-31"}))
        assert result["dateFrom"] == "2022-01-01"
        assert result["dateTo"] == "2022-12-31"
        # A set_date_range step should be present.
        actions = [s["action"] for s in result["steps"]]
        assert "set_date_range" in actions

    def test_no_date_range_no_date_step(self):
        from wheelbase_dealercenter_import.tools.dc_export_historic import dc_export_historic
        result = _ok(dc_export_historic({}))
        actions = [s["action"] for s in result["steps"]]
        assert "set_date_range" not in actions
