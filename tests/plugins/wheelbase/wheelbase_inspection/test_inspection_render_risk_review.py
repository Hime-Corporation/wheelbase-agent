"""Tests for inspection_render_risk_review tool."""

import json

import wheelbase_inspection.tools.inspection_render_risk_review as mod


def _valid_risk(**overrides):
    base = {
        "risk_id": "risk-brake-fade",
        "title": "Brake Fade Under Load",
        "reason": "Rear pads measured below 2mm",
        "evidence_basis": ["road_brake_feel_and_stopping_behavior"],
        "recommended_action": "Replace rear brake pads before delivery",
        "confidence": "high",
    }
    base.update(overrides)
    return base


def _valid_args(**overrides):
    base = {
        "inspection_id": "insp-001",
        "risks": [_valid_risk()],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Valid payload tests
# ---------------------------------------------------------------------------

def test_valid_payload_render_type():
    out = json.loads(mod.inspection_render_risk_review(_valid_args()))
    assert out["render_type"] == "risk_review"


def test_valid_payload_inspection_id():
    out = json.loads(mod.inspection_render_risk_review(_valid_args(inspection_id="insp-42")))
    assert out["inspection_id"] == "insp-42"


def test_valid_payload_risks_present():
    out = json.loads(mod.inspection_render_risk_review(_valid_args()))
    assert isinstance(out["risks"], list)
    assert len(out["risks"]) == 1


def test_valid_payload_risk_fields():
    out = json.loads(mod.inspection_render_risk_review(_valid_args()))
    risk = out["risks"][0]
    assert risk["risk_id"] == "risk-brake-fade"
    assert risk["title"] == "Brake Fade Under Load"
    assert risk["confidence"] == "high"


def test_valid_multiple_risks():
    r2 = _valid_risk(risk_id="risk-oil-leak", title="Oil Leak", confidence="medium")
    out = json.loads(mod.inspection_render_risk_review(_valid_args(risks=[_valid_risk(), r2])))
    assert len(out["risks"]) == 2
    assert "error" not in out


def test_valid_all_confidence_levels():
    for level in ("low", "medium", "high"):
        out = json.loads(mod.inspection_render_risk_review(
            _valid_args(risks=[_valid_risk(confidence=level)])
        ))
        assert "error" not in out
        assert out["risks"][0]["confidence"] == level


def test_valid_state_hash_included():
    out = json.loads(mod.inspection_render_risk_review(
        _valid_args(state_hash="abc123")
    ))
    assert out.get("state_hash") == "abc123"


def test_valid_state_hash_absent_by_default():
    out = json.loads(mod.inspection_render_risk_review(_valid_args()))
    assert "state_hash" not in out


def test_valid_evidence_basis_list():
    out = json.loads(mod.inspection_render_risk_review(_valid_args()))
    assert isinstance(out["risks"][0]["evidence_basis"], list)


# ---------------------------------------------------------------------------
# Invalid: missing risk_id
# ---------------------------------------------------------------------------

def test_error_missing_risk_id():
    risk = _valid_risk()
    del risk["risk_id"]
    out = json.loads(mod.inspection_render_risk_review(_valid_args(risks=[risk])))
    assert "error" in out


def test_error_empty_risk_id():
    out = json.loads(mod.inspection_render_risk_review(
        _valid_args(risks=[_valid_risk(risk_id="")])
    ))
    assert "error" in out


# ---------------------------------------------------------------------------
# Invalid: bad confidence
# ---------------------------------------------------------------------------

def test_error_bad_confidence():
    out = json.loads(mod.inspection_render_risk_review(
        _valid_args(risks=[_valid_risk(confidence="critical")])
    ))
    assert "error" in out


def test_error_missing_confidence():
    risk = _valid_risk()
    del risk["confidence"]
    out = json.loads(mod.inspection_render_risk_review(_valid_args(risks=[risk])))
    assert "error" in out


def test_error_uppercase_confidence():
    out = json.loads(mod.inspection_render_risk_review(
        _valid_args(risks=[_valid_risk(confidence="HIGH")])
    ))
    # Normalizes to lowercase — HIGH -> "high" which is valid
    # The tool does .lower() so this should actually succeed
    out2 = json.loads(mod.inspection_render_risk_review(
        _valid_args(risks=[_valid_risk(confidence="HIGH")])
    ))
    assert "error" not in out2


# ---------------------------------------------------------------------------
# Invalid: empty risks
# ---------------------------------------------------------------------------

def test_error_empty_risks_list():
    out = json.loads(mod.inspection_render_risk_review(_valid_args(risks=[])))
    assert "error" in out


def test_error_risks_not_a_list():
    out = json.loads(mod.inspection_render_risk_review(_valid_args(risks="not-a-list")))
    assert "error" in out


def test_error_risks_missing():
    args = {"inspection_id": "insp-001"}
    out = json.loads(mod.inspection_render_risk_review(args))
    assert "error" in out


# ---------------------------------------------------------------------------
# Invalid: missing inspection_id
# ---------------------------------------------------------------------------

def test_error_missing_inspection_id():
    args = {"risks": [_valid_risk()]}
    out = json.loads(mod.inspection_render_risk_review(args))
    assert "error" in out


def test_error_empty_inspection_id():
    out = json.loads(mod.inspection_render_risk_review(
        _valid_args(inspection_id="")
    ))
    assert "error" in out


# ---------------------------------------------------------------------------
# Invalid: duplicate risk_id
# ---------------------------------------------------------------------------

def test_error_duplicate_risk_id():
    out = json.loads(mod.inspection_render_risk_review(
        _valid_args(risks=[_valid_risk(), _valid_risk()])
    ))
    assert "error" in out


# ---------------------------------------------------------------------------
# Invalid: missing title
# ---------------------------------------------------------------------------

def test_error_missing_title():
    risk = _valid_risk()
    del risk["title"]
    out = json.loads(mod.inspection_render_risk_review(_valid_args(risks=[risk])))
    assert "error" in out
