"""start_recon — set a car's status to 'recon' via the inventory_set_status RPC.

If no reconStatusId is supplied, resolves the id dynamically from
inventory_status_definition where code = 'recon'.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def start_recon(args: dict, **kwargs) -> str:
    car_id = str(args.get("carId") or "").strip()
    if not car_id:
        return err("carId is required (uuid string)")

    recon_status_id = args.get("reconStatusId")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        # Resolve recon status id if not supplied.
        if recon_status_id is None:
            status_rows = client.postgrest_get(
                "inventory_status_definition",
                {"code": "eq.recon", "select": "id", "limit": "1"},
            )
            if not status_rows:
                return err("Recon status definition not found (code='recon')")
            recon_status_id = status_rows[0]["id"]

        # Fetch car scoping for the RPC.
        car_rows = client.postgrest_get(
            "inventory_car",
            {"id": f"eq.{car_id}", "select": "tenant_id,dealership_id", "limit": "1"},
        )
        if not car_rows:
            return err(f"Vehicle not found: {car_id}")
        car = car_rows[0]

        client.postgrest_write(
            "POST",
            "rpc/inventory_set_status",
            body={
                "p_tenant_id": car["tenant_id"],
                "p_dealership_id": car["dealership_id"],
                "p_car_id": car_id,
                "p_new_status_id": recon_status_id,
            },
        )
        return ok({
            "carId": car_id,
            "statusId": recon_status_id,
            "note": "recon run + stages auto-created by backend trigger",
        })
    except Exception as e:  # noqa: BLE001
        return err(f"start_recon failed: {e}")
    finally:
        client.close()
