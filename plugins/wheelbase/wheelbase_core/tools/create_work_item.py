"""create_work_item — insert a row into the unified `work_item` table."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

_ROOT_TYPES = {"task", "reminder", "recon_run"}
_CHILD_TYPES = {"finding", "work_order", "work_order_line", "stage"}
_ALLOWED = {"task", "work_order", "reminder", "finding", "work_order_line"}
_PRIORITIES = {"low", "medium", "high", "urgent"}


def create_work_item(args: dict, **kwargs) -> str:
    title = str(args.get("title") or "").strip()
    if not title:
        return err("title is required (non-empty string)")

    wtype = str(args.get("type") or "task").strip()
    if wtype not in _ALLOWED:
        return err(f"type must be one of {sorted(_ALLOWED)}")

    parent_id = args.get("parentId")
    if wtype in _CHILD_TYPES and not parent_id:
        return err(f"type '{wtype}' requires parentId")
    if wtype in _ROOT_TYPES and parent_id:
        return err(f"type '{wtype}' is a root item and must not have parentId")

    car_id = str(args.get("carId") or "").strip()
    if not car_id and not parent_id:
        return err("carId is required for root work items")

    priority = str(args.get("priority") or "medium")
    if priority not in _PRIORITIES:
        return err(f"priority must be one of {sorted(_PRIORITIES)}")

    est = args.get("estCostCents")
    if est is not None and (not isinstance(est, int) or est < 0):
        return err("estCostCents must be a non-negative integer")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        body: dict = {"title": title, "type": wtype, "status": "todo",
                      "priority": priority, "source": "manual"}
        if car_id: body["inventory_car_id"] = car_id
        if parent_id: body["parent_id"] = parent_id
        if args.get("description") is not None: body["description"] = args["description"]
        if args.get("vendorId") is not None: body["vendor_id"] = args["vendorId"]
        if args.get("dueAt") is not None: body["due_at"] = args["dueAt"]
        if est is not None: body["est_cost_cents"] = est
        if args.get("stageDefinitionId") is not None: body["stage_definition_id"] = args["stageDefinitionId"]

        result = client.postgrest_write("POST", "work_item", body=body)
        row = result[0] if isinstance(result, list) and result else result
        if not row or not row.get("id"):
            return err("Work item created but response was malformed.")
        return ok({"workItemId": row["id"], "carId": car_id or None,
                   "title": row.get("title"), "status": row.get("status"), "type": row.get("type")})
    except Exception as e:  # noqa: BLE001
        return err(f"create_work_item failed: {e}")
    finally:
        client.close()
