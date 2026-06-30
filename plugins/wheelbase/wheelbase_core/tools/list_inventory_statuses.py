"""list_inventory_statuses — return all configured inventory status definitions."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def list_inventory_statuses(args: dict, **kwargs) -> str:
    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        rows = client.postgrest_get(
            "inventory_status_definition",
            {"select": "id,code,label,sort_order", "order": "sort_order.asc"},
        )
        return ok({"statuses": rows})
    except Exception as e:  # noqa: BLE001
        return err(f"list_inventory_statuses failed: {e}")
    finally:
        client.close()
