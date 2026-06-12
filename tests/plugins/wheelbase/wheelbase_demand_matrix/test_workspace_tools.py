"""Tests for workspace-file tools: read_demand_matrix, read_inventory_summary, read_unlabeled_cars."""

import json
import os

import pytest

import wheelbase_demand_matrix.tools.read_demand_matrix as rdm_mod
import wheelbase_demand_matrix.tools.read_inventory_summary as ris_mod
import wheelbase_demand_matrix.tools.read_unlabeled_cars as ruc_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_workspace(tmp_path, filename, data):
    """Write a JSON file under tmp_path/.wheelbase/."""
    wb_dir = tmp_path / ".wheelbase"
    wb_dir.mkdir(exist_ok=True)
    (wb_dir / filename).write_text(json.dumps(data), encoding="utf-8")


def _set_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))


# ---------------------------------------------------------------------------
# read_demand_matrix
# ---------------------------------------------------------------------------

class TestReadDemandMatrix:
    def test_returns_data_when_file_exists(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        payload = {"categories": [{"key": "suv", "target": 10}]}
        _write_workspace(tmp_path, "demand-matrix.json", payload)
        out = json.loads(rdm_mod.read_demand_matrix({}))
        assert out["categories"][0]["key"] == "suv"

    def test_error_when_file_missing(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        out = json.loads(rdm_mod.read_demand_matrix({}))
        assert "error" in out
        assert "snapshot not found" in out["error"]

    def test_error_on_invalid_json(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        wb_dir = tmp_path / ".wheelbase"
        wb_dir.mkdir()
        (wb_dir / "demand-matrix.json").write_text("not json", encoding="utf-8")
        out = json.loads(rdm_mod.read_demand_matrix({}))
        assert "error" in out
        assert "not valid JSON" in out["error"]

    def test_ignores_args(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        _write_workspace(tmp_path, "demand-matrix.json", {"x": 1})
        out = json.loads(rdm_mod.read_demand_matrix({"unexpected": "arg"}))
        assert "x" in out


# ---------------------------------------------------------------------------
# read_inventory_summary
# ---------------------------------------------------------------------------

class TestReadInventorySummary:
    def test_returns_data_when_file_exists(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        payload = {"suv": {"current": 5, "target": 10}}
        _write_workspace(tmp_path, "inventory-summary.json", payload)
        out = json.loads(ris_mod.read_inventory_summary({}))
        assert out["suv"]["target"] == 10

    def test_error_when_file_missing(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        out = json.loads(ris_mod.read_inventory_summary({}))
        assert "error" in out
        assert "snapshot not found" in out["error"]

    def test_error_on_invalid_json(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        wb_dir = tmp_path / ".wheelbase"
        wb_dir.mkdir()
        (wb_dir / "inventory-summary.json").write_text("{bad", encoding="utf-8")
        out = json.loads(ris_mod.read_inventory_summary({}))
        assert "error" in out


# ---------------------------------------------------------------------------
# read_unlabeled_cars
# ---------------------------------------------------------------------------

class TestReadUnlabeledCars:
    def _cars(self, n):
        return [{"id": f"car-{i}", "make": "Honda"} for i in range(n)]

    def test_returns_cars_with_default_limit(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        _write_workspace(tmp_path, "unlabeled-cars.json", self._cars(30))
        out = json.loads(ruc_mod.read_unlabeled_cars({}))
        assert out["total"] == 25
        assert len(out["cars"]) == 25

    def test_respects_limit_param(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        _write_workspace(tmp_path, "unlabeled-cars.json", self._cars(30))
        out = json.loads(ruc_mod.read_unlabeled_cars({"limit": 5}))
        assert out["total"] == 5

    def test_error_when_file_missing(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        out = json.loads(ruc_mod.read_unlabeled_cars({}))
        assert "error" in out

    def test_error_on_invalid_limit(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        _write_workspace(tmp_path, "unlabeled-cars.json", self._cars(5))
        out = json.loads(ruc_mod.read_unlabeled_cars({"limit": 0}))
        assert "error" in out

    def test_error_on_limit_too_large(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        _write_workspace(tmp_path, "unlabeled-cars.json", self._cars(5))
        out = json.loads(ruc_mod.read_unlabeled_cars({"limit": 101}))
        assert "error" in out

    def test_empty_list_when_file_has_non_array(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        _write_workspace(tmp_path, "unlabeled-cars.json", {"not": "a list"})
        out = json.loads(ruc_mod.read_unlabeled_cars({}))
        assert out["cars"] == []
        assert out["total"] == 0

    def test_float_limit_truncated(self, monkeypatch, tmp_path):
        _set_workspace(monkeypatch, tmp_path)
        _write_workspace(tmp_path, "unlabeled-cars.json", self._cars(10))
        out = json.loads(ruc_mod.read_unlabeled_cars({"limit": 3.9}))
        assert out["total"] == 3
