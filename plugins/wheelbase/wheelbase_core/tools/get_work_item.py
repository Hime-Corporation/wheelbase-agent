"""get_work_item — read work_item rows (flat) or the work_item_tree view (nested)."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

_SELECT = ("id,type,parent_id,title,status,priority,effective_status,"
           "rolled_est_cents,rolled_actual_cents,hold_type,hold_reason,vendor_id,due_at,created_at")


def get_work_item(args: dict, **kwargs) -> str:
    car_id = str(args.get("carId") or "").strip()
    work_item_id = str(args.get("workItemId") or "").strip()
    if not car_id and not work_item_id:
        return err("Provide carId and/or workItemId")
    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        table = "work_item_tree" if args.get("tree") else "work_item"
        params: dict[str, str] = {"select": _SELECT, "order": "created_at.asc",
                                  "limit": str(int(args.get("limit") or 100))}
        if args.get("offset"): params["offset"] = str(int(args["offset"]))
        if work_item_id: params["id"] = f"eq.{work_item_id}"
        if car_id: params["inventory_car_id"] = f"eq.{car_id}"
        if args.get("type"): params["type"] = f"eq.{args['type']}"
        if args.get("status"): params["status"] = f"eq.{args['status']}"
        rows = client.postgrest_get(table, params)
        return ok({"items": rows})
    except Exception as e:  # noqa: BLE001
        return err(f"get_work_item failed: {e}")
    finally:
        client.close()
