"""inspection_render_risk_review — pure validator, no DB access.

Validates the risk-review payload produced by the model after researching an
inspection, normalizes it, and returns DB-ready JSON via ok(). The Go backend
reads the returned JSON from function_call_output and persists it with the
service-role Supabase client.

Handler contract:
  - signature: def fn(args: dict, **kwargs) -> str
  - NEVER raises; all errors return err(...)
  - NO WheelbaseClient; NO DB access; NO side effects
"""

from __future__ import annotations

from wheelbase_sdk import ok, err

# Import catalog constants so tests can monkeypatch at module level if needed.
from ..catalog import CONFIDENCE_LEVELS


def inspection_render_risk_review(args: dict, **kwargs) -> str:
    try:
        inspection_id = str(args.get("inspection_id") or "").strip()
        if not inspection_id:
            return err("inspection_id must be a non-empty string")

        state_hash = str(args.get("state_hash") or "").strip() or None

        risks_raw = args.get("risks")
        if not isinstance(risks_raw, list) or len(risks_raw) == 0:
            return err("risks must be a non-empty list")

        normalized_risks = []
        seen_ids: set[str] = set()

        for i, risk in enumerate(risks_raw):
            if not isinstance(risk, dict):
                return err(f"risks[{i}] must be an object")

            risk_id = str(risk.get("risk_id") or "").strip()
            if not risk_id:
                return err(f"risks[{i}].risk_id must be a non-empty string")
            if risk_id in seen_ids:
                return err(f"Duplicate risk_id '{risk_id}' at risks[{i}]")
            seen_ids.add(risk_id)

            title = str(risk.get("title") or "").strip()
            if not title:
                return err(f"risks[{i}].title must be a non-empty string (risk_id={risk_id!r})")

            reason = str(risk.get("reason") or "").strip()

            evidence_basis_raw = risk.get("evidence_basis")
            if not isinstance(evidence_basis_raw, list):
                evidence_basis = []
            else:
                evidence_basis = [str(e) for e in evidence_basis_raw if e is not None]

            recommended_action = str(risk.get("recommended_action") or "").strip()

            confidence = str(risk.get("confidence") or "").strip().lower()
            if confidence not in CONFIDENCE_LEVELS:
                return err(
                    f"risks[{i}].confidence must be one of {sorted(CONFIDENCE_LEVELS)!r}; "
                    f"got {confidence!r} (risk_id={risk_id!r})"
                )

            normalized_risks.append({
                "risk_id": risk_id,
                "title": title,
                "reason": reason,
                "evidence_basis": evidence_basis,
                "recommended_action": recommended_action,
                "confidence": confidence,
            })

        payload: dict = {
            "render_type": "risk_review",
            "inspection_id": inspection_id,
            "risks": normalized_risks,
        }
        if state_hash is not None:
            payload["state_hash"] = state_hash

        return ok(payload)

    except Exception as exc:  # noqa: BLE001 — handlers must never raise
        return err(f"inspection_render_risk_review failed: {exc}")
