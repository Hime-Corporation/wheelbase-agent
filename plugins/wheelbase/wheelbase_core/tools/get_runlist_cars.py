"""get_runlist_cars — fetch vehicles from `runlist_cars_view` for a runlist."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def get_runlist_cars(args: dict, **kwargs) -> str:
    runlist_id = str(args.get("runlistId") or "").strip()
    if not runlist_id:
        return err("runlistId must be a non-empty string (UUID)")

    make = args.get("make")
    raw_limit = args.get("limit")
    limit = 100
    if raw_limit is not None:
        try:
            limit = max(1, min(5000, int(raw_limit)))
        except (TypeError, ValueError):
            return err("limit must be a number")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        params: dict[str, str] = {
            "select": "id,runlist_id,inventory_car_id,year,make,model,vin,stock_number,archived_at",
            "runlist_id": f"eq.{runlist_id}",
            "archived_at": "is.null",
            "limit": str(limit),
        }
        if make is not None:
            safe_make = str(make).replace("%", "\\%")
            params["make"] = f"ilike.%{safe_make}%"

        rows = client.postgrest_get("runlist_cars_view", params)
        summaries = [
            {
                "id": r.get("id"),
                "runlistId": r.get("runlist_id"),
                "inventoryCarId": r.get("inventory_car_id"),
                "year": r.get("year"),
                "make": r.get("make"),
                "model": r.get("model"),
                "vin": r.get("vin"),
                "stockNumber": r.get("stock_number"),
            }
            for r in (rows or [])
        ]
        return ok(summaries)
    except Exception as e:  # noqa: BLE001
        return err(f"get_runlist_cars failed: {e}")
    finally:
        client.close()
