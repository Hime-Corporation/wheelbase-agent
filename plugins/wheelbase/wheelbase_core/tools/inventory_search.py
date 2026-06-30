"""inventory_search — search inventory_car via PostgREST ilike filters.

query is optional: when omitted the tool returns recent active inventory
filtered only by the supplied make/statusId/yearRange/offset arguments.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def inventory_search(args: dict, **kwargs) -> str:
    query = str(args.get("query") or "").strip()

    make = args.get("make")
    status_id = args.get("statusId")
    year_range = args.get("yearRange")
    raw_limit = args.get("limit")
    raw_offset = args.get("offset")

    limit = 50
    if raw_limit is not None:
        try:
            limit = max(1, min(200, int(raw_limit)))
        except (TypeError, ValueError):
            return err("limit must be a number")

    offset = 0
    if raw_offset is not None:
        try:
            offset = max(0, int(raw_offset))
        except (TypeError, ValueError):
            return err("offset must be a number")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        params: dict[str, str] = {
            # Embed the status definition (FK status_id → inventory_status_definition)
            # so the result carries the human-readable label, not just the numeric id.
            "select": (
                "id,year,make,model,stock_number,status_id,asking_price_cents,"
                "inventory_status_definition(code,label)"
            ),
            "is_archived": "eq.false",
        }

        if query:
            safe_q = query.replace("%", "\\%")
            or_value = (
                f"vin.ilike.%{safe_q}%,"
                f"make.ilike.%{safe_q}%,"
                f"model.ilike.%{safe_q}%,"
                f"stock_number.ilike.%{safe_q}%"
            )
            params["or"] = f"({or_value})"
        else:
            # No text query — return most recent active inventory
            params["order"] = "created_at.desc"

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

        rows, next_offset = client.postgrest_get_page(
            "inventory_car", params, limit=limit, offset=offset
        )
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
                    "askingPriceCents": r.get("asking_price_cents"),
                }
            )
        return ok({"results": summaries, "nextOffset": next_offset})
    except Exception as e:  # noqa: BLE001
        return err(f"inventory_search failed: {e}")
    finally:
        client.close()
