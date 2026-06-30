"""get_vendor — fetch the full record for a vendor by UUID."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def get_vendor(args: dict, **kwargs) -> str:
    vendor_id = str(args.get("vendorId") or "").strip()
    if not vendor_id:
        return err("vendorId is required (uuid string)")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        rows = client.postgrest_get(
            "vendor",
            {"id": f"eq.{vendor_id}", "select": "id,name,vendor_type,phone,email,city,state,notes", "limit": "1"},
        )
        if not rows:
            return err(f"Vendor not found: {vendor_id}", vendorId=vendor_id)
        return ok(rows[0])
    except Exception as e:  # noqa: BLE001
        return err(f"get_vendor failed: {e}")
    finally:
        client.close()
