"""Tests for propose_demand_targets (pure-data tool, no network)."""

import json

import wheelbase_demand_matrix.tools.propose_demand_targets as mod


VALID_PROPOSALS = [
    {"key": "suv", "target": 10, "reasoning": "High local demand"},
    {"key": "sedan", "target": 8},
]


class TestProposeDemandTargets:
    def test_returns_proposals(self):
        out = json.loads(mod.propose_demand_targets({"proposals": VALID_PROPOSALS}))
        assert out["kind"] == "propose_demand_targets"
        assert len(out["proposals"]) == 2

    def test_error_on_missing_proposals(self):
        out = json.loads(mod.propose_demand_targets({}))
        assert "error" in out

    def test_error_on_empty_proposals(self):
        out = json.loads(mod.propose_demand_targets({"proposals": []}))
        assert "error" in out

    def test_error_on_non_list(self):
        out = json.loads(mod.propose_demand_targets({"proposals": "bad"}))
        assert "error" in out

    def test_error_on_missing_key(self):
        out = json.loads(mod.propose_demand_targets({"proposals": [{"target": 5}]}))
        assert "error" in out

    def test_error_on_missing_target(self):
        out = json.loads(mod.propose_demand_targets({"proposals": [{"key": "suv"}]}))
        assert "error" in out

    def test_error_on_target_out_of_range(self):
        out = json.loads(
            mod.propose_demand_targets({"proposals": [{"key": "suv", "target": 300}]})
        )
        assert "error" in out

    def test_error_on_negative_target(self):
        out = json.loads(
            mod.propose_demand_targets({"proposals": [{"key": "suv", "target": -1}]})
        )
        assert "error" in out

    def test_reasoning_is_optional(self):
        out = json.loads(
            mod.propose_demand_targets({"proposals": [{"key": "suv", "target": 5}]})
        )
        assert out["kind"] == "propose_demand_targets"
