"""list_vendors — query the `vendor` table with optional type/search filters."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

VALID_VENDOR_TYPES = {
    "body_shop",
    "detail",
    "mechanical",
    "glass",
    "tires",
    "reconditioning",
    "auction",
    "transport",
    "other",
}


def list_vendors(args: dict, **kwargs) -> str:
    vendor_type = args.get("type")
    if vendor_type is not None:
        if not isinstance(vendor_type, str):
            return err("type must be a string")
        if vendor_type not in VALID_VENDOR_TYPES:
            return err(f"type must be one of {', '.join(sorted(VALID_VENDOR_TYPES))}")

    search = args.get("search")
    if search is not None and not isinstance(search, str):
        return err("search must be a string")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        params: dict[str, str] = {
            "select": "id,name,vendor_type,phone,email",
            "order": "name.asc",
        }
        if vendor_type is not None:
            params["vendor_type"] = f"eq.{vendor_type}"
        if search and search.strip():
            safe_search = search.strip().replace("%", "\\%")
            params["name"] = f"ilike.*{safe_search}*"

        rows = client.postgrest_get("vendor", params)
        summaries = [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "vendor_type": r.get("vendor_type"),
                "phone": r.get("phone"),
                "email": r.get("email"),
            }
            for r in (rows or [])
        ]
        return ok(summaries)
    except Exception as e:  # noqa: BLE001
        return err(f"list_vendors failed: {e}")
    finally:
        client.close()
