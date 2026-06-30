"""bulk_inspect — fetch inspection records for a list of car IDs.

V2 status model
---------------
The ``vehicle_recon_intake_inspection`` table now carries a proper
``inspection_lifecycle_status`` enum column (``draft`` | ``in_progress`` |
``complete``) instead of the old ``completed_at``-only heuristic.

State mapping (backward-tolerant — older rows whose status field is
null/empty fall through to ``in-progress``):
  draft        → "pending"      (record exists but not yet started)
  in_progress  → "in-progress"
  complete     → "completed"
  (no row)     → "pending"

V2 scoring columns (``mechanical_grade``, ``safety_status``,
``pass_count``, ``fail_count``, ``monitor_count``, ``fixed_count``,
``na_count``) are included in the per-car result when the row is present;
null/missing values are omitted so callers can distinguish "not yet scored"
from zero.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

INSPECTION_TABLE = "vehicle_recon_intake_inspection"

# V2 lifecycle enum values (lowercase, as returned by PostgREST)
_V2_STATUS_MAP = {
    "draft": "pending",
    "in_progress": "in-progress",
    "in-progress": "in-progress",  # defensive: tolerate hyphen form
    "complete": "completed",
    "completed": "completed",      # backward compat: pre-V2 rows
    "done": "completed",           # backward compat
}

_V2_SCORE_FIELDS = (
    "mechanical_grade",
    "safety_status",
    "pass_count",
    "fail_count",
    "monitor_count",
    "fixed_count",
    "na_count",
)


def _derive_state(row: dict | None) -> str:
    """Map a DB row's status field to the tool's three-value state enum."""
    if not row:
        return "pending"
    s = str(row.get("status") or "").lower().strip()
    return _V2_STATUS_MAP.get(s, "in-progress")


def _extract_scores(row: dict) -> dict:
    """Return non-null V2 scoring fields from *row* as a flat dict."""
    scores: dict = {}
    for field in _V2_SCORE_FIELDS:
        val = row.get(field)
        if val is not None:
            scores[field] = val
    return scores


def bulk_inspect(args: dict, **kwargs) -> str:
    car_ids = args.get("carIds")
    if not isinstance(car_ids, list) or len(car_ids) == 0:
        return err("carIds must be a non-empty array of strings")
    if not all(isinstance(c, str) for c in car_ids):
        return err("every entry in carIds must be a string UUID")
    if len(car_ids) > 200:
        return err("carIds may contain at most 200 entries")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        score_select = ",".join(_V2_SCORE_FIELDS)
        # Single batched query — one round-trip for all car IDs (no N+1).
        ids_csv = ",".join(car_ids)
        try:
            rows = client.postgrest_get(
                INSPECTION_TABLE,
                {
                    "inventory_car_id": f"in.({ids_csv})",
                    "select": f"id,inventory_car_id,status,{score_select}",
                },
            )
        except Exception:  # noqa: BLE001
            rows = []

        # Build lookup keyed by inventory_car_id for O(1) per-car access.
        row_map: dict[str, dict] = {}
        for row in (rows or []):
            cid = row.get("inventory_car_id")
            if cid:
                row_map[cid] = row

        results = []
        for car_id in car_ids:
            row = row_map.get(car_id)  # None when no inspection exists → "pending"
            state = _derive_state(row)
            entry: dict = {"carId": car_id, "state": state}
            if row:
                scores = _extract_scores(row)
                if scores:
                    entry["scores"] = scores
            results.append(entry)

        completed = sum(1 for r in results if r["state"] == "completed")
        in_progress = sum(1 for r in results if r["state"] == "in-progress")
        pending = sum(1 for r in results if r["state"] == "pending")
        total = len(results)

        parts = []
        if completed:
            parts.append(f"{completed} completed")
        if in_progress:
            parts.append(f"{in_progress} in-progress")
        if pending:
            parts.append(f"{pending} pending")

        summary = (
            f"Inspected {total} car{'s' if total != 1 else ''}: "
            + (", ".join(parts) if parts else "no results")
            + "."
        )

        return ok({"results": results, "summary": summary})
    except Exception as e:  # noqa: BLE001
        return err(f"bulk_inspect failed: {e}")
    finally:
        client.close()
