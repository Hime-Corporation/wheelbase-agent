"""delete_work_item — delete a work_item row (with cascade-aware confirmation pre-fetch)."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def delete_work_item(args: dict, **kwargs) -> str:
    work_item_id = str(args.get("workItemId") or "").strip()
    if not work_item_id:
        return err("workItemId is required (uuid string)")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        # Pre-fetch the item title
        item_rows = client.postgrest_get(
            "work_item",
            {"id": f"eq.{work_item_id}", "select": "id,title", "limit": "1"},
        )
        if not item_rows:
            return err(f"work_item '{work_item_id}' not found")
        title = item_rows[0].get("title") or ""

        # Count direct children
        children = client.postgrest_get(
            "work_item",
            {"parent_id": f"eq.{work_item_id}", "select": "id"},
        )
        child_count = len(children) if children else 0

        # Delete
        client.postgrest_write(
            "DELETE",
            "work_item",
            params={"id": f"eq.{work_item_id}"},
        )
        return ok({"deletedId": work_item_id, "title": title, "childCount": child_count})
    except Exception as e:  # noqa: BLE001
        return err(f"delete_work_item failed: {e}")
    finally:
        client.close()
