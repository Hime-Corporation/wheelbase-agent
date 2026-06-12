"""bulk_inspect — fetch inspection records for a list of car IDs."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

INSPECTION_TABLE = "vehicle_recon_intake_inspection"


def _derive_state(row: dict | None) -> str:
    if not row:
        return "pending"
    s = str(row.get("status") or "").lower()
    if s in ("completed", "complete", "done"):
        return "completed"
    return "in-progress"


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
        results = []
        for car_id in car_ids:
            try:
                rows = client.postgrest_get(
                    INSPECTION_TABLE,
                    {
                        "inventory_car_id": f"eq.{car_id}",
                        "select": "id,inventory_car_id,status",
                        "limit": "1",
                    },
                )
                state = _derive_state(rows[0] if rows else None)
            except Exception:  # noqa: BLE001
                state = "pending"
            results.append({"carId": car_id, "state": state})

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
