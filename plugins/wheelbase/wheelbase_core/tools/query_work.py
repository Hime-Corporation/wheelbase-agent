"""query_work — cross-org work_item filtering.

Optional args: status, type, vendorId, assignedToUserId, dueBefore, dueAfter,
               limit (default 50), offset (default 0).
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

_VALID_STATUSES = {"todo", "ready", "in_progress", "blocked", "done", "skipped", "cancelled"}
_VALID_TYPES = {"recon_run", "stage", "task", "work_order", "work_order_line", "finding", "reminder"}

_SELECT = (
    "id,type,parent_id,inventory_car_id,title,status,priority,"
    "rolled_est_cents,rolled_actual_cents,hold_type,hold_reason,"
    "vendor_id,assigned_to_user_id,due_at,created_at"
)


def query_work(args: dict, **kwargs) -> str:
    status = args.get("status")
    if status is not None and status not in _VALID_STATUSES:
        return err(f"status must be one of {sorted(_VALID_STATUSES)}")

    wtype = args.get("type")
    if wtype is not None and wtype not in _VALID_TYPES:
        return err(f"type must be one of {sorted(_VALID_TYPES)}")

    raw_limit = args.get("limit", 50)
    try:
        limit = int(raw_limit)
        if limit < 1:
            raise ValueError
    except (ValueError, TypeError):
        return err("limit must be a positive integer")

    raw_offset = args.get("offset", 0)
    try:
        offset = int(raw_offset)
        if offset < 0:
            raise ValueError
    except (ValueError, TypeError):
        return err("offset must be a non-negative integer")

    due_before = args.get("dueBefore")
    due_after = args.get("dueAfter")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()

    try:
        params: dict[str, str] = {
            "select": _SELECT,
            "order": "due_at.asc",
            "limit": str(limit),
            "offset": str(offset),
        }

        if status:
            params["status"] = f"eq.{status}"
        if wtype:
            params["type"] = f"eq.{wtype}"
        if args.get("vendorId"):
            params["vendor_id"] = f"eq.{args['vendorId']}"
        if args.get("assignedToUserId"):
            params["assigned_to_user_id"] = f"eq.{args['assignedToUserId']}"

        # Due-date range handling
        if due_before and due_after:
            # PostgREST logical-and expression for two conditions on the same column
            params["and"] = f"(due_at.gte.{due_after},due_at.lte.{due_before})"
        elif due_before:
            params["due_at"] = f"lte.{due_before}"
        elif due_after:
            params["due_at"] = f"gte.{due_after}"

        rows = client.postgrest_get("work_item", params)
        return ok({"items": rows})

    except Exception as e:  # noqa: BLE001
        return err(f"query_work failed: {e}")
    finally:
        client.close()
