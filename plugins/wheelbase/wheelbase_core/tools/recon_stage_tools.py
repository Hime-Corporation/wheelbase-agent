"""recon_stage_tools — complete_stage, update_stage, create_finding.

Three handlers for manipulating recon stages and findings.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

_VALID_HOLD_TYPES = {"parts", "vendor", "approval", "transport"}
_VALID_STATUSES = {"todo", "ready", "in_progress", "blocked", "done", "skipped", "cancelled"}


def complete_stage(args: dict, **kwargs) -> str:
    """Mark a recon stage as done."""
    stage_id = str(args.get("stageId") or "").strip()
    if not stage_id:
        return err("stageId is required (uuid string)")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        result = client.postgrest_write(
            "PATCH",
            "work_item",
            body={"status": "done"},
            params={"id": f"eq.{stage_id}"},
        )
        row = result[0] if isinstance(result, list) and result else result
        return ok({"stageId": stage_id, "status": "done", "result": row})
    except Exception as e:  # noqa: BLE001
        return err(f"complete_stage failed: {e}")
    finally:
        client.close()


def update_stage(args: dict, **kwargs) -> str:
    """Update mutable fields on a recon stage."""
    stage_id = str(args.get("stageId") or "").strip()
    if not stage_id:
        return err("stageId is required (uuid string)")

    hold_type = args.get("holdType")
    if hold_type is not None and hold_type not in _VALID_HOLD_TYPES:
        return err(f"holdType must be one of {sorted(_VALID_HOLD_TYPES)}")

    status = args.get("status")
    if status is not None and status not in _VALID_STATUSES:
        return err(f"status must be one of {sorted(_VALID_STATUSES)}")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        body: dict = {}
        # If holdType is set and no explicit status was given, force blocked.
        if status is not None:
            body["status"] = status
        elif hold_type is not None:
            body["status"] = "blocked"

        if hold_type is not None:
            body["hold_type"] = hold_type
        hold_reason = args.get("holdReason")
        if hold_reason is not None:
            body["hold_reason"] = hold_reason
        if args.get("assignedToUserId") is not None:
            body["assigned_to_user_id"] = args["assignedToUserId"]
        if args.get("vendorId") is not None:
            body["vendor_id"] = args["vendorId"]
        est = args.get("estCostCents")
        if est is not None:
            if isinstance(est, bool) or not isinstance(est, int) or est < 0:
                return err("estCostCents must be a non-negative integer")
            body["est_cost_cents"] = est

        if not body:
            return err("No updatable fields provided")

        result = client.postgrest_write(
            "PATCH",
            "work_item",
            body=body,
            params={"id": f"eq.{stage_id}"},
        )
        row = result[0] if isinstance(result, list) and result else result
        return ok({"stageId": stage_id, "updated": body, "result": row})
    except Exception as e:  # noqa: BLE001
        return err(f"update_stage failed: {e}")
    finally:
        client.close()


def create_finding(args: dict, **kwargs) -> str:
    """Create a finding under a recon stage (delegates to create_work_item)."""
    parent_id = str(args.get("parentId") or "").strip()
    if not parent_id:
        return err("parentId is required for create_finding")

    title = str(args.get("title") or "").strip()
    if not title:
        return err("title is required (non-empty string)")

    # Build delegate args — pass through optional fields.
    delegate: dict = {"parentId": parent_id, "title": title, "type": "finding"}
    if args.get("estCostCents") is not None:
        delegate["estCostCents"] = args["estCostCents"]
    if args.get("priority") is not None:
        delegate["priority"] = args["priority"]
    if args.get("carId") is not None:
        delegate["carId"] = args["carId"]

    from wheelbase_core.tools.create_work_item import create_work_item  # noqa: PLC0415
    return create_work_item(delegate)
