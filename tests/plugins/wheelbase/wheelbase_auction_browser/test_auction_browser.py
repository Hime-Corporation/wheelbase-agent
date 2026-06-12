"""Tests for wheelbase-auction-browser plugin.

Covers:
- list_auctions: happy path, snapshot missing, invalid JSON, not a list
- list_runlists: happy path (embedded runlists), counts-only note, auction not found,
  snapshot missing, missing auctionId
- get_runlist: happy path with IMX, happy path without IMX (note), missing runlist,
  missing runlistId
- top_imx_picks: happy path, min-tier filter, both snapshots required, bad args
- explain_imx: happy path, car not found, snapshot missing, missing args
- refresh_runlist: happy path, missing runlistId
- flag_car: happy path, with note, missing args
- vote_on_car: upvote, downvote, invalid vote, missing args
- _marker_active: True when marker file present, False otherwise (via TERMINAL_CWD)
- pre_llm_call hook: injects context when active, returns None when inactive
- register: wires all 8 tools + 1 hook with check_fn
"""

import json
import os

import pytest

import wheelbase_auction_browser as plugin
import wheelbase_auction_browser.tools.list_auctions as la_mod
import wheelbase_auction_browser.tools.list_runlists as lr_mod
import wheelbase_auction_browser.tools.get_runlist as gr_mod
import wheelbase_auction_browser.tools.top_imx_picks as tip_mod
import wheelbase_auction_browser.tools.explain_imx as ei_mod
import wheelbase_auction_browser.tools.refresh_runlist as rr_mod
import wheelbase_auction_browser.tools.flag_car as fc_mod
import wheelbase_auction_browser.tools.vote_on_car as voc_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(result: str) -> dict:
    """Parse a JSON result string and assert no error key."""
    data = json.loads(result)
    assert "error" not in data, f"Unexpected error: {data}"
    return data


def _err(result: str) -> dict:
    """Parse a JSON result string and assert an error key is present."""
    data = json.loads(result)
    assert "error" in data, f"Expected error, got: {data}"
    return data


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Point TERMINAL_CWD at a fresh tmp directory."""
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    return tmp_path


@pytest.fixture()
def active_workspace(workspace):
    """Workspace with the auction-browser marker present."""
    (workspace / ".wheelbase-auction-browser-active").touch()
    return workspace


@pytest.fixture()
def sample_auctions():
    return [
        {"id": "a1", "name": "Monday Sale", "location": "Dallas", "runlistCount": 2,
         "runlists": [{"id": "rl1", "name": "Lane 1"}, {"id": "rl2", "name": "Lane 2"}]},
        {"id": "a2", "name": "Friday Sale", "location": "Houston", "runlistCount": 1},
    ]


@pytest.fixture()
def sample_runlist():
    return {
        "id": "rl1",
        "auctionId": "a1",
        "name": "Lane 1",
        "cars": [
            {"id": "c1", "vin": "1HGCM82633A123456", "year": 2020, "make": "Honda", "model": "Civic"},
            {"id": "c2", "vin": "2T1BURHE0JC012345", "year": 2018, "make": "Toyota", "model": "Corolla"},
        ],
    }


@pytest.fixture()
def sample_imx():
    return {
        "runlistId": "rl1",
        "configVersion": "1.0",
        "scores": {
            "c1": {"score": 0.85, "tier": 3, "components": {"fit": 0.9, "mileage": 0.8, "age": 0.85},
                   "categoryMatches": {"sedan": True}},
            "c2": {"score": 0.45, "tier": 1, "components": {"fit": 0.5, "mileage": 0.4, "age": 0.45},
                   "categoryMatches": {}},
        },
    }


# ---------------------------------------------------------------------------
# list_auctions
# ---------------------------------------------------------------------------

class TestListAuctions:
    def test_returns_auctions(self, workspace, sample_auctions):
        _write_json(workspace / ".wheelbase" / "auctions.json", sample_auctions)
        result = _ok(la_mod.list_auctions({}))
        assert len(result) == 2
        assert result[0]["id"] == "a1"

    def test_snapshot_missing(self, workspace):
        result = _err(la_mod.list_auctions({}))
        assert "snapshot not found" in result["error"]

    def test_invalid_json(self, workspace):
        p = workspace / ".wheelbase" / "auctions.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("NOT JSON", encoding="utf-8")
        result = _err(la_mod.list_auctions({}))
        assert "JSON" in result["error"]

    def test_not_a_list(self, workspace):
        _write_json(workspace / ".wheelbase" / "auctions.json", {"bad": "shape"})
        result = _err(la_mod.list_auctions({}))
        assert "array" in result["error"]

    def test_empty_list(self, workspace):
        _write_json(workspace / ".wheelbase" / "auctions.json", [])
        result = _ok(la_mod.list_auctions({}))
        assert result == []


# ---------------------------------------------------------------------------
# list_runlists
# ---------------------------------------------------------------------------

class TestListRunlists:
    def test_returns_embedded_runlists(self, workspace, sample_auctions):
        _write_json(workspace / ".wheelbase" / "auctions.json", sample_auctions)
        result = _ok(lr_mod.list_runlists({"auctionId": "a1"}))
        assert result["auctionId"] == "a1"
        assert len(result["runlists"]) == 2

    def test_counts_only_note(self, workspace, sample_auctions):
        _write_json(workspace / ".wheelbase" / "auctions.json", sample_auctions)
        result = _ok(lr_mod.list_runlists({"auctionId": "a2"}))
        assert result["runlists"] == []
        assert "note" in result
        assert "refresh_runlist" in result["note"]

    def test_auction_not_found(self, workspace, sample_auctions):
        _write_json(workspace / ".wheelbase" / "auctions.json", sample_auctions)
        result = _err(lr_mod.list_runlists({"auctionId": "missing"}))
        assert "not found" in result["error"]

    def test_snapshot_missing(self, workspace):
        result = _err(lr_mod.list_runlists({"auctionId": "a1"}))
        assert "snapshot not found" in result["error"]

    def test_missing_auction_id(self, workspace):
        result = _err(lr_mod.list_runlists({}))
        assert "auctionId" in result["error"]

    def test_empty_auction_id(self, workspace):
        result = _err(lr_mod.list_runlists({"auctionId": ""}))
        assert "auctionId" in result["error"]


# ---------------------------------------------------------------------------
# get_runlist
# ---------------------------------------------------------------------------

class TestGetRunlist:
    def test_with_imx(self, workspace, sample_runlist, sample_imx):
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.json", sample_runlist)
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.imx.json", sample_imx)
        result = _ok(gr_mod.get_runlist({"runlistId": "rl1"}))
        assert result["runlistId"] == "rl1"
        assert len(result["cars"]) == 2
        car1 = next(c for c in result["cars"] if c["id"] == "c1")
        assert car1["imxScore"] == 0.85
        assert car1["imxTier"] == 3
        assert "note" not in result

    def test_without_imx_adds_note(self, workspace, sample_runlist):
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.json", sample_runlist)
        result = _ok(gr_mod.get_runlist({"runlistId": "rl1"}))
        assert result["note"] == "imx snapshot missing"
        assert len(result["cars"]) == 2
        assert "imxScore" not in result["cars"][0]

    def test_runlist_missing(self, workspace):
        result = _err(gr_mod.get_runlist({"runlistId": "missing_rl"}))
        assert "snapshot not found" in result["error"]

    def test_missing_runlist_id(self, workspace):
        result = _err(gr_mod.get_runlist({}))
        assert "runlistId" in result["error"]

    def test_car_without_imx_score_left_plain(self, workspace, sample_runlist, sample_imx):
        # Remove c2 from IMX scores so it has no entry
        sample_imx["scores"].pop("c2")
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.json", sample_runlist)
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.imx.json", sample_imx)
        result = _ok(gr_mod.get_runlist({"runlistId": "rl1"}))
        car2 = next(c for c in result["cars"] if c["id"] == "c2")
        assert "imxScore" not in car2


# ---------------------------------------------------------------------------
# top_imx_picks
# ---------------------------------------------------------------------------

class TestTopImxPicks:
    def test_returns_sorted_picks(self, workspace, sample_runlist, sample_imx):
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.json", sample_runlist)
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.imx.json", sample_imx)
        result = _ok(tip_mod.top_imx_picks({"runlistId": "rl1"}))
        assert result["runlistId"] == "rl1"
        assert result["picks"][0]["id"] == "c1"  # higher score
        assert result["picks"][1]["id"] == "c2"

    def test_min_tier_filter(self, workspace, sample_runlist, sample_imx):
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.json", sample_runlist)
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.imx.json", sample_imx)
        result = _ok(tip_mod.top_imx_picks({"runlistId": "rl1", "minTier": 3}))
        assert len(result["picks"]) == 1
        assert result["picks"][0]["id"] == "c1"

    def test_limit(self, workspace, sample_runlist, sample_imx):
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.json", sample_runlist)
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.imx.json", sample_imx)
        result = _ok(tip_mod.top_imx_picks({"runlistId": "rl1", "limit": 1}))
        assert len(result["picks"]) == 1

    def test_missing_runlist_snapshot(self, workspace, sample_imx):
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.imx.json", sample_imx)
        result = _err(tip_mod.top_imx_picks({"runlistId": "rl1"}))
        assert "missing snapshot" in result["error"]

    def test_missing_imx_snapshot(self, workspace, sample_runlist):
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.json", sample_runlist)
        result = _err(tip_mod.top_imx_picks({"runlistId": "rl1"}))
        assert "missing snapshot" in result["error"]

    def test_missing_runlist_id(self, workspace):
        result = _err(tip_mod.top_imx_picks({}))
        assert "runlistId" in result["error"]

    def test_bad_limit(self, workspace):
        result = _err(tip_mod.top_imx_picks({"runlistId": "rl1", "limit": "bad"}))
        assert "integer" in result["error"]

    def test_limit_out_of_range(self, workspace):
        result = _err(tip_mod.top_imx_picks({"runlistId": "rl1", "limit": 0}))
        assert "limit" in result["error"]


# ---------------------------------------------------------------------------
# explain_imx
# ---------------------------------------------------------------------------

class TestExplainImx:
    def test_happy_path(self, workspace, sample_imx):
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.imx.json", sample_imx)
        result = _ok(ei_mod.explain_imx({"runlistId": "rl1", "carId": "c1"}))
        assert result["score"] == 0.85
        assert result["tier"] == "Pursue"
        assert len(result["reasons"]) == 3
        assert "Demand fit: 90/100" in result["reasons"]

    def test_tier_labels(self, workspace, sample_imx):
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.imx.json", sample_imx)
        result = _ok(ei_mod.explain_imx({"runlistId": "rl1", "carId": "c2"}))
        assert result["tier"] == "Risky"  # tier 1

    def test_car_not_found(self, workspace, sample_imx):
        _write_json(workspace / ".wheelbase" / "runlists" / "rl1.imx.json", sample_imx)
        result = _err(ei_mod.explain_imx({"runlistId": "rl1", "carId": "missing"}))
        assert "no score" in result["error"]

    def test_snapshot_missing(self, workspace):
        result = _err(ei_mod.explain_imx({"runlistId": "rl1", "carId": "c1"}))
        assert "no score" in result["error"]

    def test_missing_args(self, workspace):
        result = _err(ei_mod.explain_imx({}))
        assert "required" in result["error"]

    def test_missing_car_id(self, workspace):
        result = _err(ei_mod.explain_imx({"runlistId": "rl1"}))
        assert "required" in result["error"]


# ---------------------------------------------------------------------------
# refresh_runlist
# ---------------------------------------------------------------------------

class TestRefreshRunlist:
    def test_emits_instruction(self):
        result = _ok(rr_mod.refresh_runlist({"runlistId": "rl1"}))
        assert result["kind"] == "refresh_runlist"
        assert result["runlistId"] == "rl1"

    def test_missing_runlist_id(self):
        result = _err(rr_mod.refresh_runlist({}))
        assert "runlistId" in result["error"]

    def test_empty_runlist_id(self):
        result = _err(rr_mod.refresh_runlist({"runlistId": ""}))
        assert "runlistId" in result["error"]


# ---------------------------------------------------------------------------
# flag_car
# ---------------------------------------------------------------------------

class TestFlagCar:
    def test_happy_path(self):
        result = _ok(fc_mod.flag_car({"runlistId": "rl1", "carId": "c1"}))
        assert result["kind"] == "flag_car"
        assert result["runlistId"] == "rl1"
        assert result["carId"] == "c1"
        assert result["note"] is None

    def test_with_note(self):
        result = _ok(fc_mod.flag_car({"runlistId": "rl1", "carId": "c1", "note": "Check body work"}))
        assert result["note"] == "Check body work"

    def test_missing_runlist_id(self):
        result = _err(fc_mod.flag_car({"carId": "c1"}))
        assert "required" in result["error"]

    def test_missing_car_id(self):
        result = _err(fc_mod.flag_car({"runlistId": "rl1"}))
        assert "required" in result["error"]

    def test_missing_both(self):
        result = _err(fc_mod.flag_car({}))
        assert "required" in result["error"]


# ---------------------------------------------------------------------------
# vote_on_car
# ---------------------------------------------------------------------------

class TestVoteOnCar:
    def test_upvote(self):
        result = _ok(voc_mod.vote_on_car({"runlistId": "rl1", "carId": "c1", "vote": 1}))
        assert result["kind"] == "vote_on_car"
        assert result["vote"] == 1
        assert result["note"] is None

    def test_downvote(self):
        result = _ok(voc_mod.vote_on_car({"runlistId": "rl1", "carId": "c1", "vote": -1}))
        assert result["vote"] == -1

    def test_with_note(self):
        result = _ok(voc_mod.vote_on_car({"runlistId": "rl1", "carId": "c1", "vote": 1, "note": "Great miles"}))
        assert result["note"] == "Great miles"

    def test_invalid_vote_value(self):
        result = _err(voc_mod.vote_on_car({"runlistId": "rl1", "carId": "c1", "vote": 2}))
        assert "vote must be 1 or -1" in result["error"]

    def test_invalid_vote_type(self):
        result = _err(voc_mod.vote_on_car({"runlistId": "rl1", "carId": "c1", "vote": "up"}))
        assert "vote must be 1 or -1" in result["error"]

    def test_missing_runlist_id(self):
        result = _err(voc_mod.vote_on_car({"carId": "c1", "vote": 1}))
        assert "required" in result["error"]

    def test_missing_car_id(self):
        result = _err(voc_mod.vote_on_car({"runlistId": "rl1", "vote": 1}))
        assert "required" in result["error"]

    def test_missing_vote(self):
        result = _err(voc_mod.vote_on_car({"runlistId": "rl1", "carId": "c1"}))
        assert "vote must be 1 or -1" in result["error"]


# ---------------------------------------------------------------------------
# Marker / mode gating
# ---------------------------------------------------------------------------

class TestMarkerActive:
    def test_true_when_marker_present(self, active_workspace):
        assert plugin._marker_active() is True

    def test_false_when_marker_absent(self, workspace):
        assert plugin._marker_active() is False

    def test_false_when_terminal_cwd_unset(self, monkeypatch, tmp_path):
        # Without marker in cwd, should be False
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        assert plugin._marker_active() is False


# ---------------------------------------------------------------------------
# pre_llm_call hook
# ---------------------------------------------------------------------------

class TestPreLlmCallHook:
    def test_injects_context_when_active(self, active_workspace, sample_auctions):
        _write_json(active_workspace / ".wheelbase" / "auctions.json", sample_auctions)
        result = plugin._pre_llm_call_hook()
        assert result is not None
        assert "context" in result
        text = result["context"]
        assert "Wheelbase Auction Browser Mode" in text
        assert "Currently 2 upcoming auctions" in text

    def test_injects_context_no_snapshot(self, active_workspace):
        result = plugin._pre_llm_call_hook()
        assert result is not None
        assert "No auction snapshot is loaded yet" in result["context"]

    def test_returns_none_when_inactive(self, workspace):
        result = plugin._pre_llm_call_hook()
        assert result is None

    def test_auction_count_singular(self, active_workspace):
        _write_json(active_workspace / ".wheelbase" / "auctions.json",
                    [{"id": "a1", "name": "One Auction"}])
        result = plugin._pre_llm_call_hook()
        assert "Currently 1 upcoming auction tracked" in result["context"]
        assert "auctions" not in result["context"].split("Currently 1 upcoming auction")[1].split("\n")[0]

    def test_accepts_kwargs(self, workspace):
        # Should not raise even if Hermes passes arbitrary keyword arguments
        result = plugin._pre_llm_call_hook(session_id="s1", user_message="hi", model="x")
        assert result is None  # marker absent


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------

class TestRegister:
    def test_registers_all_tools_and_hook(self):
        registered_tools = {}
        registered_hooks = []

        class Ctx:
            def register_tool(self, *, name, toolset, schema, handler, check_fn=None):
                registered_tools[name] = {
                    "toolset": toolset,
                    "schema": schema,
                    "handler": handler,
                    "check_fn": check_fn,
                }

            def register_hook(self, event, fn):
                registered_hooks.append((event, fn))

        plugin.register(Ctx())

        expected_tools = [
            "list_auctions", "list_runlists", "get_runlist",
            "top_imx_picks", "explain_imx", "refresh_runlist",
            "flag_car", "vote_on_car",
        ]
        for name in expected_tools:
            assert name in registered_tools, f"Missing tool: {name}"
            assert registered_tools[name]["toolset"] == "wheelbase_auction_browser"
            assert registered_tools[name]["check_fn"] is not None

        assert len(registered_hooks) == 1
        event, fn = registered_hooks[0]
        assert event == "pre_llm_call"

    def test_check_fn_honours_marker(self, active_workspace):
        registered_tools = {}

        class Ctx:
            def register_tool(self, *, name, toolset, schema, handler, check_fn=None):
                registered_tools[name] = check_fn

            def register_hook(self, event, fn):
                pass

        plugin.register(Ctx())

        for name, check_fn in registered_tools.items():
            assert check_fn() is True, f"check_fn for {name} should be True with marker present"

    def test_check_fn_false_without_marker(self, workspace):
        registered_tools = {}

        class Ctx:
            def register_tool(self, *, name, toolset, schema, handler, check_fn=None):
                registered_tools[name] = check_fn

            def register_hook(self, event, fn):
                pass

        plugin.register(Ctx())

        for name, check_fn in registered_tools.items():
            assert check_fn() is False, f"check_fn for {name} should be False without marker"
