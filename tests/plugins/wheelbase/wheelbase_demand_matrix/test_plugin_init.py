"""Tests for plugin __init__: check_fn (_marker_active) and pre_llm_call hook."""

import json
import os

import pytest

import wheelbase_demand_matrix as plugin_mod
from wheelbase_demand_matrix import _marker_active, _pre_llm_call, _MARKER_FILENAME


# ---------------------------------------------------------------------------
# _marker_active (check_fn)
# ---------------------------------------------------------------------------

class TestMarkerActive:
    def test_returns_false_when_marker_absent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        assert _marker_active() is False

    def test_returns_true_when_marker_present(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        (tmp_path / _MARKER_FILENAME).touch()
        assert _marker_active() is True

    def test_marker_must_be_file_not_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        # Create a directory with the marker name — should still return True
        # because Path.exists() is True for both files and dirs.
        (tmp_path / _MARKER_FILENAME).mkdir()
        assert _marker_active() is True


# ---------------------------------------------------------------------------
# _pre_llm_call hook
# ---------------------------------------------------------------------------

class TestPreLlmCall:
    def test_returns_none_when_marker_absent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        result = _pre_llm_call()
        assert result is None

    def test_returns_context_when_marker_present(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        (tmp_path / _MARKER_FILENAME).touch()
        result = _pre_llm_call()
        assert result is not None
        assert "context" in result
        assert "Wheelbase Demand-Matrix Setup Mode" in result["context"]

    def test_injects_demand_matrix_json(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        (tmp_path / _MARKER_FILENAME).touch()
        wb_dir = tmp_path / ".wheelbase"
        wb_dir.mkdir()
        (wb_dir / "demand-matrix.json").write_text('{"categories": []}', encoding="utf-8")
        result = _pre_llm_call()
        assert "demand-matrix.json" in result["context"]
        assert '{"categories": []}' in result["context"]

    def test_injects_inventory_summary_json(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        (tmp_path / _MARKER_FILENAME).touch()
        wb_dir = tmp_path / ".wheelbase"
        wb_dir.mkdir()
        (wb_dir / "inventory-summary.json").write_text('{"suv": {}}', encoding="utf-8")
        result = _pre_llm_call()
        assert "inventory-summary.json" in result["context"]

    def test_injects_dealership_md(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        (tmp_path / _MARKER_FILENAME).touch()
        wb_dir = tmp_path / ".wheelbase"
        wb_dir.mkdir()
        (wb_dir / "dealership.md").write_text("# Acme Cars\nSome info", encoding="utf-8")
        result = _pre_llm_call()
        assert "dealership.md" in result["context"]
        assert "Acme Cars" in result["context"]

    def test_skips_empty_files(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        (tmp_path / _MARKER_FILENAME).touch()
        wb_dir = tmp_path / ".wheelbase"
        wb_dir.mkdir()
        (wb_dir / "demand-matrix.json").write_text("   ", encoding="utf-8")
        result = _pre_llm_call()
        assert "demand-matrix.json" not in result["context"]

    def test_no_wheelbase_dir_still_returns_addendum(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        (tmp_path / _MARKER_FILENAME).touch()
        result = _pre_llm_call()
        assert "Wheelbase Demand-Matrix Setup Mode" in result["context"]


# ---------------------------------------------------------------------------
# register() wires all tools and the hook
# ---------------------------------------------------------------------------

class TestRegister:
    def test_registers_all_tools_and_hook(self):
        registered_tools = {}
        registered_hooks = {}

        class Ctx:
            def register_tool(self, *, name, toolset, schema, handler, check_fn=None):
                registered_tools[name] = {
                    "toolset": toolset,
                    "schema": schema,
                    "handler": handler,
                    "check_fn": check_fn,
                }

            def register_hook(self, event, fn):
                registered_hooks[event] = fn

        plugin_mod.register(Ctx())

        expected_tools = [
            "read_demand_matrix",
            "read_inventory_summary",
            "read_unlabeled_cars",
            "propose_demand_targets",
            "save_demand_overrides",
            "save_inventory_demand_labels",
            "complete_demand_matrix_setup",
        ]
        for name in expected_tools:
            assert name in registered_tools, f"Tool not registered: {name}"
            assert registered_tools[name]["toolset"] == "wheelbase_demand_matrix"
            assert callable(registered_tools[name]["check_fn"])

        assert "pre_llm_call" in registered_hooks
        assert callable(registered_hooks["pre_llm_call"])

    def test_check_fn_honours_marker(self, monkeypatch, tmp_path):
        """check_fn in every registered tool correctly reflects marker state."""
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))

        registered_tools = {}

        class Ctx:
            def register_tool(self, *, name, toolset, schema, handler, check_fn=None):
                registered_tools[name] = check_fn

            def register_hook(self, event, fn):
                pass

        plugin_mod.register(Ctx())

        # Marker absent
        for name, check_fn in registered_tools.items():
            assert check_fn() is False, f"{name}: expected False without marker"

        # Marker present
        (tmp_path / _MARKER_FILENAME).touch()
        for name, check_fn in registered_tools.items():
            assert check_fn() is True, f"{name}: expected True with marker"
