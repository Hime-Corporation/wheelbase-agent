"""get_work_item — read work_item rows (flat) or the work_item_tree view (nested)."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

# Flat mode: query `work_item` base table — effective_status, depth, root_id are VIEW-only columns.
_SELECT_FLAT = (
    "id,type,parent_id,inventory_car_id,title,status,priority,"
    "rolled_est_cents,rolled_actual_cents,hold_type,hold_reason,vendor_id,due_at,created_at"
)
# Tree mode: query `work_item_tree` view which adds the computed columns.
_SELECT_TREE = _SELECT_FLAT + ",effective_status,depth,root_id"


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
        use_tree = bool(args.get("tree"))
        table = "work_item_tree" if use_tree else "work_item"
        select = _SELECT_TREE if use_tree else _SELECT_FLAT
        params: dict[str, str] = {"select": select, "order": "created_at.asc",
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
