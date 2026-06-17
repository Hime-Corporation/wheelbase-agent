"""inspection_render_checklist — pure validator, no DB access.

Validates the diagnostic question list produced by the model for a specific risk,
normalizes it, and returns DB-ready JSON via ok(). The Go backend reads the returned
JSON from function_call_output and persists it with the service-role Supabase client.

Handler contract:
  - signature: def fn(args: dict, **kwargs) -> str
  - NEVER raises; all errors return err(...)
  - NO WheelbaseClient; NO DB access; NO side effects

Question-type option rules (enforced here and mirrored in DB validation):
  - multiple_choice, multi_select → options REQUIRED and non-empty
  - free_response, yes_no, measurement, checklist → options must be absent / empty
"""

from __future__ import annotations

from wheelbase_sdk import ok, err

# Import catalog constants so tests can monkeypatch at module level if needed.
from ..catalog import QUESTION_TYPES

# Types that require a non-empty options list.
_CHOICE_TYPES: frozenset[str] = frozenset({"multiple_choice", "multi_select"})


def inspection_render_checklist(args: dict, **kwargs) -> str:
    try:
        inspection_id = str(args.get("inspection_id") or "").strip()
        if not inspection_id:
            return err("inspection_id must be a non-empty string")

        state_hash = str(args.get("state_hash") or "").strip() or None
        triggered_by_risk_id = str(args.get("triggered_by_risk_id") or "").strip() or None

        questions_raw = args.get("questions")
        if not isinstance(questions_raw, list) or len(questions_raw) == 0:
            return err("questions must be a non-empty list")

        normalized_questions = []

        for i, q in enumerate(questions_raw):
            if not isinstance(q, dict):
                return err(f"questions[{i}] must be an object")

            question_type = str(q.get("question_type") or "").strip().lower()
            if question_type not in QUESTION_TYPES:
                return err(
                    f"questions[{i}].question_type must be one of {sorted(QUESTION_TYPES)!r}; "
                    f"got {question_type!r}"
                )

            prompt = str(q.get("prompt") or "").strip()
            if not prompt:
                return err(f"questions[{i}].prompt must be a non-empty string")

            summary_label = str(q.get("summary_label") or "").strip()
            if not summary_label:
                return err(f"questions[{i}].summary_label must be a non-empty string")

            # Validate options per type
            options_raw = q.get("options")
            if question_type in _CHOICE_TYPES:
                if not isinstance(options_raw, list) or len(options_raw) == 0:
                    return err(
                        f"questions[{i}].options must be a non-empty list for "
                        f"question_type={question_type!r}"
                    )
                options: list[str] | None = [str(o) for o in options_raw]
            else:
                # For non-choice types, ignore options (don't error; model may include empty list)
                options = None

            # Optional fields — coerce to str or None
            step_id = str(q.get("step_id") or "").strip() or None
            item_id = str(q.get("item_id") or "").strip() or None
            help_text = str(q.get("help_text") or "").strip() or None
            required = bool(q["required"]) if "required" in q else None
            validation = q.get("validation") if isinstance(q.get("validation"), dict) else None

            normalized_q: dict = {
                "question_type": question_type,
                "prompt": prompt,
                "summary_label": summary_label,
            }
            if options is not None:
                normalized_q["options"] = options
            if step_id is not None:
                normalized_q["step_id"] = step_id
            if item_id is not None:
                normalized_q["item_id"] = item_id
            if help_text is not None:
                normalized_q["help_text"] = help_text
            if required is not None:
                normalized_q["required"] = required
            if validation is not None:
                normalized_q["validation"] = validation

            normalized_questions.append(normalized_q)

        payload: dict = {
            "render_type": "diagnostic_questions",
            "inspection_id": inspection_id,
            "questions": normalized_questions,
        }
        if state_hash is not None:
            payload["state_hash"] = state_hash
        if triggered_by_risk_id is not None:
            payload["triggered_by_risk_id"] = triggered_by_risk_id

        return ok(payload)

    except Exception as exc:  # noqa: BLE001 — handlers must never raise
        return err(f"inspection_render_checklist failed: {exc}")
