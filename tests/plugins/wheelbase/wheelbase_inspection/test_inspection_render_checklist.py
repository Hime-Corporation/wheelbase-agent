"""Tests for inspection_render_checklist tool."""

import json

import wheelbase_inspection.tools.inspection_render_checklist as mod


def _valid_question(**overrides):
    base = {
        "question_type": "yes_no",
        "prompt": "Are the front brake pads above 3mm?",
        "summary_label": "Front Pads OK",
    }
    base.update(overrides)
    return base


def _valid_args(**overrides):
    base = {
        "inspection_id": "insp-001",
        "questions": [_valid_question()],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Valid checklist tests
# ---------------------------------------------------------------------------

def test_valid_render_type():
    out = json.loads(mod.inspection_render_checklist(_valid_args()))
    assert out["render_type"] == "diagnostic_questions"


def test_valid_inspection_id():
    out = json.loads(mod.inspection_render_checklist(_valid_args(inspection_id="insp-99")))
    assert out["inspection_id"] == "insp-99"


def test_valid_questions_present():
    out = json.loads(mod.inspection_render_checklist(_valid_args()))
    assert isinstance(out["questions"], list)
    assert len(out["questions"]) == 1


def test_valid_question_fields():
    out = json.loads(mod.inspection_render_checklist(_valid_args()))
    q = out["questions"][0]
    assert q["question_type"] == "yes_no"
    assert q["prompt"] == "Are the front brake pads above 3mm?"
    assert q["summary_label"] == "Front Pads OK"


def test_valid_all_non_choice_types():
    for qtype in ("yes_no", "free_response", "measurement", "checklist"):
        args = _valid_args(questions=[_valid_question(question_type=qtype)])
        out = json.loads(mod.inspection_render_checklist(args))
        assert "error" not in out
        assert out["questions"][0]["question_type"] == qtype


def test_valid_multiple_choice_with_options():
    q = _valid_question(
        question_type="multiple_choice",
        options=["Good", "Fair", "Poor"],
    )
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" not in out
    assert out["questions"][0]["options"] == ["Good", "Fair", "Poor"]


def test_valid_multi_select_with_options():
    q = _valid_question(
        question_type="multi_select",
        options=["Noise", "Vibration", "Pull"],
    )
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" not in out
    assert out["questions"][0]["options"] == ["Noise", "Vibration", "Pull"]


def test_valid_state_hash_included():
    out = json.loads(mod.inspection_render_checklist(_valid_args(state_hash="xyz789")))
    assert out.get("state_hash") == "xyz789"


def test_valid_triggered_by_risk_id_included():
    out = json.loads(mod.inspection_render_checklist(
        _valid_args(triggered_by_risk_id="risk-brake-fade")
    ))
    assert out.get("triggered_by_risk_id") == "risk-brake-fade"


def test_valid_state_hash_absent_by_default():
    out = json.loads(mod.inspection_render_checklist(_valid_args()))
    assert "state_hash" not in out


def test_valid_triggered_by_risk_id_absent_by_default():
    out = json.loads(mod.inspection_render_checklist(_valid_args()))
    assert "triggered_by_risk_id" not in out


def test_valid_optional_fields_step_id_item_id():
    q = _valid_question(
        step_id="brakes",
        item_id="brakes_front_pads",
        help_text="Measure at thinnest point",
        required=True,
    )
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" not in out
    assert out["questions"][0]["step_id"] == "brakes"
    assert out["questions"][0]["item_id"] == "brakes_front_pads"


def test_valid_multiple_questions():
    q1 = _valid_question(prompt="Q1?", summary_label="Q1")
    q2 = _valid_question(
        question_type="free_response",
        prompt="Describe any noise heard?",
        summary_label="Noise Description",
    )
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q1, q2])))
    assert "error" not in out
    assert len(out["questions"]) == 2


def test_valid_non_choice_type_ignores_empty_options():
    # Non-choice types with an empty options list should not error
    q = _valid_question(question_type="yes_no", options=[])
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" not in out


# ---------------------------------------------------------------------------
# Invalid: bad question_type
# ---------------------------------------------------------------------------

def test_error_bad_question_type():
    q = _valid_question(question_type="rating_scale")
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" in out


def test_error_empty_question_type():
    q = _valid_question(question_type="")
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" in out


def test_error_missing_question_type():
    q = _valid_question()
    del q["question_type"]
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" in out


# ---------------------------------------------------------------------------
# Invalid: multiple_choice missing options
# ---------------------------------------------------------------------------

def test_error_multiple_choice_missing_options():
    q = _valid_question(question_type="multiple_choice")
    # No options key at all
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" in out


def test_error_multiple_choice_empty_options():
    q = _valid_question(question_type="multiple_choice", options=[])
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" in out


def test_error_multi_select_missing_options():
    q = _valid_question(question_type="multi_select")
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" in out


def test_error_multi_select_empty_options():
    q = _valid_question(question_type="multi_select", options=[])
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" in out


# ---------------------------------------------------------------------------
# Invalid: empty questions list
# ---------------------------------------------------------------------------

def test_error_empty_questions_list():
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[])))
    assert "error" in out


def test_error_questions_not_a_list():
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions="not-a-list")))
    assert "error" in out


def test_error_questions_missing():
    args = {"inspection_id": "insp-001"}
    out = json.loads(mod.inspection_render_checklist(args))
    assert "error" in out


# ---------------------------------------------------------------------------
# Invalid: missing inspection_id
# ---------------------------------------------------------------------------

def test_error_missing_inspection_id():
    args = {"questions": [_valid_question()]}
    out = json.loads(mod.inspection_render_checklist(args))
    assert "error" in out


def test_error_empty_inspection_id():
    out = json.loads(mod.inspection_render_checklist(_valid_args(inspection_id="")))
    assert "error" in out


# ---------------------------------------------------------------------------
# Invalid: missing required question fields
# ---------------------------------------------------------------------------

def test_error_missing_prompt():
    q = _valid_question()
    del q["prompt"]
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" in out


def test_error_missing_summary_label():
    q = _valid_question()
    del q["summary_label"]
    out = json.loads(mod.inspection_render_checklist(_valid_args(questions=[q])))
    assert "error" in out
