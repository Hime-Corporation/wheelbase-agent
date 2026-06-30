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
                # Also embed inventory_photo rows (one-to-many) so callers get photo URLs
                # without a second round-trip.  The table may be empty — that's fine.
                "select": "*,inventory_status_definition(code,label),inventory_photo(url,label,is_main,sort_order)",
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
        # Build photo_urls: main photo first (is_main desc), then by sort_order asc.
        # PostgREST returns the embedded rows as a list (empty list when none exist).
        photos = car.pop("inventory_photo", None) or []
        car["photo_urls"] = [
            p["url"]
            for p in sorted(photos, key=lambda p: (not p.get("is_main"), p.get("sort_order") or 0))
        ]

        # Fetch financial summary from the inventory_car_financials view.
        try:
            fin_rows = client.postgrest_get(
                "inventory_car_financials",
                {
                    "id": f"eq.{car_id}",
                    "select": "total_cost_cents,gross_profit_cents,margin_pct,days_in_stock",
                    "limit": "1",
                },
            )
            car["financials"] = fin_rows[0] if fin_rows else {}
        except Exception:  # noqa: BLE001 — non-fatal; view may not exist in all envs
            car["financials"] = {}

        return ok(car)
    except Exception as e:  # noqa: BLE001 — tools must never raise
        return err(f"get_car failed: {e}")
    finally:
        client.close()
