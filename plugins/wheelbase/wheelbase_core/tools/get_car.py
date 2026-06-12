"""get_car — fetch one vehicle by UUID from Supabase `inventory_car`.

REFERENCE HANDLER PATTERN (copy this for every Wheelbase tool):
  - signature `def fn(args: dict, **kwargs) -> str`
  - validate args → `err(...)` on bad input
  - build the client; `WheelbaseAuthError` → `signed_out_result()`
  - do the work; ALWAYS return a JSON string; NEVER raise (catch → `err(...)`)
  - close the client in `finally`
The module-level `WheelbaseClient` name is the test seam — tests monkeypatch it.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def get_car(args: dict, **kwargs) -> str:
    car_id = str(args.get("carId") or "").strip()
    if not car_id:
        return err("carId must be a non-empty UUID string")
    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        rows = client.postgrest_get(
            "inventory_car",
            {
                "id": f"eq.{car_id}",
                # Embed the status definition (FK status_id → inventory_status_definition)
                # so the record carries the human-readable label, not just the numeric id.
                "select": "*,inventory_status_definition(code,label)",
                "limit": "1",
            },
        )
        if not rows:
            return err(f"Vehicle not found: {car_id}", carId=car_id)
        car = rows[0]
        # Flatten the embedded status onto the record: `status` (label) +
        # `status_code`, keeping the numeric `status_id` already present.
        status_def = car.pop("inventory_status_definition", None) or {}
        car["status"] = status_def.get("label")
        car["status_code"] = status_def.get("code")
        return ok(car)
    except Exception as e:  # noqa: BLE001 — tools must never raise
        return err(f"get_car failed: {e}")
    finally:
        client.close()
