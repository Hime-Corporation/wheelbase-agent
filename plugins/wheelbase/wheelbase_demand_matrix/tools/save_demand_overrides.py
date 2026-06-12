"""save_demand_overrides — persist dealer overrides for demand categories.

Go-API write tool: PATCH /demand-matrix/overrides (bulk).
Maps: overrides[] → Go API body.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def save_demand_overrides(args: dict, **kwargs) -> str:  # noqa: ARG001
    overrides = args.get("overrides")
    if not isinstance(overrides, list) or len(overrides) == 0:
        return err("overrides must be a non-empty array")

    for i, item in enumerate(overrides):
        if not isinstance(item, dict):
            return err(f"overrides[{i}] must be an object")
        key = item.get("key")
        target = item.get("target")
        if not isinstance(key, str) or not key.strip():
            return err(f"overrides[{i}].key must be a non-empty string")
        if not isinstance(target, int) or target < 0 or target > 200:
            return err(
                f"overrides[{i}].target must be an integer between 0 and 200"
            )

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()

    try:
        result = client.go_api(
            "PATCH",
            "/demand-matrix/overrides",
            body={"overrides": overrides},
        )
        return ok(result if result is not None else {"saved": len(overrides)})
    except Exception as exc:  # noqa: BLE001
        return err(f"save_demand_overrides failed: {exc}")
    finally:
        client.close()
