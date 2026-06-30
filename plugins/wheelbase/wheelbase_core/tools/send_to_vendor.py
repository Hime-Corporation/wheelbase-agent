"""send_to_vendor — assign a work_item to a vendor and set status to 'scheduled'.

Two-step mutation (mirroring legacy send-to-vendor.ts):
  Step 1: PATCH vendor_id + optional scheduled_at on the work item.
  Step 2: PATCH status = "scheduled" on the same work item.
Status is 'scheduled' NOT 'sent' — 'sent' is not in the work_item status enum.
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
        step1_body: dict = {"vendor_id": vendor_id}
        if scheduled_at is not None:
            step1_body["scheduled_at"] = scheduled_at

        client.postgrest_write(
            "PATCH",
            "work_item",
            body=step1_body,
            params={"id": f"eq.{work_order_id}"},
            prefer="return=minimal",
        )
        client.postgrest_write(
            "PATCH",
            "work_item",
            body={"status": "scheduled"},
            params={"id": f"eq.{work_order_id}"},
            prefer="return=minimal",
        )
        return ok({"workOrderId": work_order_id, "vendorId": vendor_id, "status": "scheduled"})
    except Exception as e:  # noqa: BLE001
        return err(f"send_to_vendor failed: {e}")
    finally:
        client.close()
