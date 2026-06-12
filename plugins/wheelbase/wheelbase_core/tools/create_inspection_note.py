"""create_inspection_note — upsert a note into vehicle_recon_intake_inspection.

The legacy create-inspection-note.ts was deferred (null export, v2 TODO).
This v1 implementation writes directly to `vehicle_recon_intake_inspection`
via PostgREST upsert (POST with onConflict=inventory_car_id). If a record
already exists for the car, the note and optional category are updated.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


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

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        body: dict = {
            "inventory_car_id": car_id,
            "notes": note,
        }
        if category is not None:
            body["category"] = category

        result = client.postgrest_write(
            "POST",
            "vehicle_recon_intake_inspection",
            body=body,
            prefer="resolution=merge-duplicates,return=representation",
        )
        row = result[0] if isinstance(result, list) and result else result
        record_id = row.get("id") if isinstance(row, dict) else None
        return ok({"carId": car_id, "id": record_id, "note": note})
    except Exception as e:  # noqa: BLE001
        return err(f"create_inspection_note failed: {e}")
    finally:
        client.close()
