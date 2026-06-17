"""create_inspection_note — write a free-form note into an inspection record.

V2 design
---------
In the V2 schema the parent ``vehicle_recon_intake_inspection`` table no
longer carries a generic ``notes`` column; per-item notes live in
``inspection_item_result`` (columns: ``inspection_id``, ``step_id``,
``group_id``, ``item_id``, ``notes``).

This tool:
1. Looks up the inspection row for ``carId`` in
   ``vehicle_recon_intake_inspection`` to obtain the inspection ``id``.
2. Upserts a row in ``inspection_item_result`` keyed on
   ``(inspection_id, item_id)`` where ``item_id`` is derived from the
   optional ``category`` argument (defaults to ``"general_note"``).
   ``step_id`` and ``group_id`` are set to ``"general"`` for free-form notes
   that are not tied to a specific catalog step.

If no inspection record exists for ``carId`` yet, the tool returns an error
rather than silently creating a dangling note.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

INSPECTION_TABLE = "vehicle_recon_intake_inspection"
ITEM_RESULT_TABLE = "inspection_item_result"

# Sentinel step/group for free-form notes not tied to a catalog item.
_GENERAL_STEP = "general"
_GENERAL_GROUP = "general"
_DEFAULT_ITEM_ID = "general_note"


def create_inspection_note(args: dict, **kwargs) -> str:
    car_id = str(args.get("carId") or "").strip()
    if not car_id:
        return err("carId is required (uuid string)")

    note = args.get("note")
    if not note or not isinstance(note, str) or not note.strip():
        return err("note is required (non-empty string)")
    note = note.strip()

    category = args.get("category")
    if category is not None and not isinstance(category, str):
        return err("category must be a string")

    # Derive item_id from category (strip whitespace, replace spaces with _).
    item_id = (
        category.strip().replace(" ", "_") if category else _DEFAULT_ITEM_ID
    )

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        # Step 1 — resolve inspection_id from inventory_car_id.
        rows = client.postgrest_get(
            INSPECTION_TABLE,
            {
                "inventory_car_id": f"eq.{car_id}",
                "select": "id,tenant_id",
                "limit": "1",
            },
        )
        if not rows:
            return err(f"No inspection found for carId {car_id!r}")
        inspection_row = rows[0]
        inspection_id = inspection_row.get("id")
        tenant_id = inspection_row.get("tenant_id")

        # Step 2 — upsert note into inspection_item_result.
        body: dict = {
            "inspection_id": inspection_id,
            "step_id": _GENERAL_STEP,
            "group_id": _GENERAL_GROUP,
            "item_id": item_id,
            "notes": note,
        }
        if tenant_id is not None:
            body["tenant_id"] = tenant_id

        result = client.postgrest_write(
            "POST",
            ITEM_RESULT_TABLE,
            body=body,
            prefer="resolution=merge-duplicates,return=representation",
        )
        row = result[0] if isinstance(result, list) and result else result
        record_id = row.get("id") if isinstance(row, dict) else None
        return ok({"carId": car_id, "id": record_id, "note": note, "itemId": item_id})
    except Exception as e:  # noqa: BLE001
        return err(f"create_inspection_note failed: {e}")
    finally:
        client.close()
