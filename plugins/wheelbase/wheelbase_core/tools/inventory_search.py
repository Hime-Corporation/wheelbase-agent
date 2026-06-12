"""inventory_search — search inventory_car via PostgREST ilike filters."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def inventory_search(args: dict, **kwargs) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return err("query must be a non-empty string")

    make = args.get("make")
    status_id = args.get("statusId")
    year_range = args.get("yearRange")
    raw_limit = args.get("limit")

    limit = 50
    if raw_limit is not None:
        try:
            limit = max(1, min(200, int(raw_limit)))
        except (TypeError, ValueError):
            return err("limit must be a number")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        safe_q = query.replace("%", "\\%")
        or_value = (
            f"vin.ilike.%{safe_q}%,"
            f"make.ilike.%{safe_q}%,"
            f"model.ilike.%{safe_q}%,"
            f"stock_number.ilike.%{safe_q}%"
        )
        params: dict[str, str] = {
            # Embed the status definition (FK status_id → inventory_status_definition)
            # so the result carries the human-readable label, not just the numeric id.
            "select": (
                "id,year,make,model,stock_number,status_id,asking_price,"
                "inventory_status_definition(code,label)"
            ),
            "or": f"({or_value})",
            "is_archived": "eq.false",
            "limit": str(limit),
        }

        if make is not None:
            safe_make = str(make).replace("%", "\\%")
            params["make"] = f"ilike.%{safe_make}%"

        if status_id is not None:
            params["status_id"] = f"eq.{int(status_id)}"

        year_min = None
        year_max = None
        if isinstance(year_range, dict):
            if "min" in year_range:
                year_min = year_range["min"]
            if "max" in year_range:
                year_max = year_range["max"]

        if year_min is not None and year_max is not None:
            params["and"] = f"(year.gte.{int(year_min)},year.lte.{int(year_max)})"
        elif year_min is not None:
            params["year"] = f"gte.{int(year_min)}"
        elif year_max is not None:
            params["year"] = f"lte.{int(year_max)}"

        rows = client.postgrest_get("inventory_car", params)
        summaries = []
        for r in rows or []:
            status_def = r.get("inventory_status_definition") or {}
            summaries.append(
                {
                    "id": r.get("id"),
                    "year": r.get("year"),
                    "make": r.get("make"),
                    "model": r.get("model"),
                    "stockNumber": r.get("stock_number"),
                    "statusId": r.get("status_id"),
                    # Human-readable status (e.g. "Frontline Ready") + machine code
                    # ("frontline"). Falls back to None when the FK is unset.
                    "status": status_def.get("label"),
                    "statusCode": status_def.get("code"),
                    "askingPrice": r.get("asking_price"),
                }
            )
        return ok(summaries)
    except Exception as e:  # noqa: BLE001
        return err(f"inventory_search failed: {e}")
    finally:
        client.close()
