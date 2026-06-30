"""inventory_stats — RPC-backed inventory analytics.

Tools:
  get_inventory_stats(args)          — summary statistics for a dealership's inventory
  get_inventory_filter_options(args) — available filter values (make/model/status/…)

Both accept an optional `dealershipId` arg; if omitted and the user belongs to exactly
one dealership the context is resolved automatically.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

from ._context import resolve_dealership_context


def get_inventory_stats(args: dict, **kwargs) -> str:
    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()

    try:
        ctx = resolve_dealership_context(client, args.get("dealershipId"))
        if ctx == "NO_DEALERSHIP":
            return err("No dealership found for this user")
        if ctx == "AMBIGUOUS":
            return err("Multiple dealerships — pass dealershipId")

        tenant_id, dealership_id = ctx
        result = client.postgrest_write(
            "POST",
            "rpc/get_inventory_stats",
            body={"p_tenant_id": tenant_id, "p_dealership_id": dealership_id},
        )
        return ok(result)

    except Exception as e:  # noqa: BLE001
        return err(f"get_inventory_stats failed: {e}")
    finally:
        client.close()


def get_inventory_filter_options(args: dict, **kwargs) -> str:
    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()

    try:
        ctx = resolve_dealership_context(client, args.get("dealershipId"))
        if ctx == "NO_DEALERSHIP":
            return err("No dealership found for this user")
        if ctx == "AMBIGUOUS":
            return err("Multiple dealerships — pass dealershipId")

        tenant_id, dealership_id = ctx
        result = client.postgrest_write(
            "POST",
            "rpc/get_inventory_filter_options",
            body={"p_tenant_id": tenant_id, "p_dealership_id": dealership_id},
        )
        return ok(result)

    except Exception as e:  # noqa: BLE001
        return err(f"get_inventory_filter_options failed: {e}")
    finally:
        client.close()
