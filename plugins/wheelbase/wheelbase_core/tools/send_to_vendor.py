"""send_to_vendor — assign a work_item to a vendor and set it blocked with hold_type='vendor'.

Single PATCH mutation: sets vendor_id, status='blocked', hold_type='vendor', and optional
scheduled_at on the work_item row in one request. 'blocked' with hold_type='vendor' is the
correct state for items awaiting a vendor — 'scheduled' is not a valid work_status enum value.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def send_to_vendor(args: dict, **kwargs) -> str:
    work_order_id = str(args.get("workOrderId") or "").strip()
    if not work_order_id:
        return err("workOrderId is required (uuid string)")

    vendor_id = str(args.get("vendorId") or "").strip()
    if not vendor_id:
        return err("vendorId is required (uuid string)")

    scheduled_at = args.get("scheduledAt")
    if scheduled_at is not None and not isinstance(scheduled_at, str):
        return err("scheduledAt must be an ISO date string")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        body: dict = {"vendor_id": vendor_id, "status": "blocked", "hold_type": "vendor"}
        if scheduled_at is not None:
            body["scheduled_at"] = scheduled_at

        client.postgrest_write(
            "PATCH",
            "work_item",
            body=body,
            params={"id": f"eq.{work_order_id}"},
            prefer="return=minimal",
        )
        return ok({"workItemId": work_order_id, "vendorId": vendor_id, "status": "blocked", "holdType": "vendor"})
    except Exception as e:  # noqa: BLE001
        return err(f"send_to_vendor failed: {e}")
    finally:
        client.close()
