"""create_work_order — POST a new row to the `work_order` table."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def create_work_order(args: dict, **kwargs) -> str:
    car_id = str(args.get("carId") or "").strip()
    if not car_id:
        return err("carId is required (uuid string)")

    title = str(args.get("title") or "").strip()
    if not title:
        return err("title is required (non-empty string)")

    description = args.get("description")
    if description is not None and not isinstance(description, str):
        return err("description must be a string")

    vendor_id = args.get("vendorId")
    if vendor_id is not None and not isinstance(vendor_id, str):
        return err("vendorId must be a string (uuid)")

    scheduled_at = args.get("scheduledAt")
    if scheduled_at is not None and not isinstance(scheduled_at, str):
        return err("scheduledAt must be a string (ISO date)")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        body: dict = {
            "inventory_car_id": car_id,
            "title": title,
            "status": "open",
        }
        if description is not None:
            body["description"] = description
        if vendor_id is not None:
            body["vendor_id"] = vendor_id
        if scheduled_at is not None:
            body["scheduled_at"] = scheduled_at

        result = client.postgrest_write("POST", "work_order", body=body)

        # postgrest_write with return=representation returns a list; take first element.
        row = result[0] if isinstance(result, list) and result else result
        if not row or not row.get("id"):
            return err("Work order was created but the response was malformed.")

        return ok({
            "workOrderId": row["id"],
            "carId": car_id,
            "title": row.get("title"),
            "status": row.get("status"),
        })
    except Exception as e:  # noqa: BLE001
        return err(f"create_work_order failed: {e}")
    finally:
        client.close()
