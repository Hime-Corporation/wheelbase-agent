"""delete_work_order — DELETE a row from `work_order` by id."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def delete_work_order(args: dict, **kwargs) -> str:
    work_order_id = str(args.get("workOrderId") or "").strip()
    if not work_order_id:
        return err("workOrderId is required (uuid string)")

    car_id = str(args.get("carId") or "").strip()
    if not car_id:
        return err("carId is required (uuid string)")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        client.postgrest_write(
            "DELETE",
            "work_order",
            params={"id": f"eq.{work_order_id}"},
            prefer="return=minimal",
        )
        return ok({"workOrderId": work_order_id, "carId": car_id})
    except Exception as e:  # noqa: BLE001
        return err(f"delete_work_order failed: {e}")
    finally:
        client.close()
