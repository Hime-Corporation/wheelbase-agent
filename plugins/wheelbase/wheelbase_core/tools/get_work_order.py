"""get_work_order — query `work_order` rows by inventory_car_id."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

VALID_STATUSES = {"draft", "open", "scheduled", "in_progress", "completed", "cancelled"}


def get_work_order(args: dict, **kwargs) -> str:
    car_id = str(args.get("carId") or "").strip()
    if not car_id:
        return err("carId is required (uuid string)")

    status = args.get("status")
    if status is not None:
        if not isinstance(status, str):
            return err("status must be a string")
        if status not in VALID_STATUSES:
            return err(f"status must be one of {', '.join(sorted(VALID_STATUSES))}")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        params: dict[str, str] = {
            "inventory_car_id": f"eq.{car_id}",
            "select": "id,title,status,vendor_id,scheduled_at",
            "order": "created_at.desc",
        }
        if status is not None:
            params["status"] = f"eq.{status}"

        rows = client.postgrest_get("work_order", params)
        summaries = [
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "status": r.get("status"),
                "vendor_id": r.get("vendor_id"),
                "scheduled_at": r.get("scheduled_at"),
            }
            for r in (rows or [])
        ]
        return ok(summaries)
    except Exception as e:  # noqa: BLE001
        return err(f"get_work_order failed: {e}")
    finally:
        client.close()
