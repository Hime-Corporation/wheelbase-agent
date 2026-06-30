"""add_work_item_comment — insert a comment on a work_item row.

Schema: work_item_comment(id, tenant_id, work_item_id, content, created_by [server default], created_at, updated_at)
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def add_work_item_comment(args: dict, **kwargs) -> str:
    work_item_id = str(args.get("workItemId") or "").strip()
    if not work_item_id:
        return err("workItemId is required (uuid string)")

    content = args.get("content")
    if not content or not isinstance(content, str) or not content.strip():
        return err("content is required (non-empty string)")
    content = content.strip()

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        # Resolve tenant_id from the work_item row.
        rows = client.postgrest_get(
            "work_item",
            {"id": f"eq.{work_item_id}", "select": "tenant_id", "limit": "1"},
        )
        if not rows:
            return err(f"Work item not found: {work_item_id}")
        tenant_id = rows[0].get("tenant_id")

        result = client.postgrest_write(
            "POST",
            "work_item_comment",
            body={
                "work_item_id": work_item_id,
                "content": content,
                "tenant_id": tenant_id,
            },
        )
        row = result[0] if isinstance(result, list) and result else result
        comment_id = row.get("id") if isinstance(row, dict) else None
        return ok({"commentId": comment_id})
    except Exception as e:  # noqa: BLE001
        return err(f"add_work_item_comment failed: {e}")
    finally:
        client.close()
