"""update_inventory_status — change inventory car status via inventory_set_status RPC.

The SQL function signature is:
  inventory_set_status(p_tenant_id, p_dealership_id, p_car_id,
                       p_new_status_id, p_changed_by DEFAULT NULL, p_note DEFAULT NULL)

p_tenant_id and p_dealership_id have no defaults, so we fetch the car's
scoping first (RLS-scoped — only returns the caller's own car).
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def update_inventory_status(args: dict, **kwargs) -> str:
    car_id = str(args.get("carId") or "").strip()
    if not car_id:
        return err("carId is required (uuid string)")

    new_status_id = args.get("newStatusId")
    if new_status_id is None:
        return err("newStatusId is required (integer)")
    try:
        new_status_id = int(new_status_id)
    except (TypeError, ValueError):
        return err("newStatusId must be an integer")

    note = args.get("note")
    if note is not None and not isinstance(note, str):
        return err("note must be a string")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        # Fetch the car's tenant/dealership scoping (RLS-scoped — only returns caller's car).
        car_rows = client.postgrest_get(
            "inventory_car",
            {"id": f"eq.{car_id}", "select": "tenant_id,dealership_id", "limit": "1"},
        )
        if not car_rows:
            return err(f"Vehicle not found: {car_id}")
        car = car_rows[0]

        body: dict = {
            "p_tenant_id": car["tenant_id"],
            "p_dealership_id": car["dealership_id"],
            "p_car_id": car_id,
            "p_new_status_id": new_status_id,
        }
        if note is not None:
            body["p_note"] = note

        result = client.postgrest_write("POST", "rpc/inventory_set_status", body=body)
        return ok(result)
    except Exception as e:  # noqa: BLE001
        return err(f"update_inventory_status failed: {e}")
    finally:
        client.close()
