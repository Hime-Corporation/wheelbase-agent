"""update_inventory_status — PATCH inventory_car status + insert history record."""

from datetime import datetime, timezone

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def update_inventory_status(args: dict, **kwargs) -> str:
    car_id = str(args.get("carId") or "").strip()
    if not car_id:
        return err("carId is required (uuid string)")

    new_status_id = args.get("newStatusId")
    if new_status_id is None:
        return err("newStatusId is required (integer)")
    try:
        new_status_id = int(new_status_id)
    except (TypeError, ValueError):
        return err("newStatusId must be an integer")

    note = args.get("note")
    if note is not None and not isinstance(note, str):
        return err("note must be a string")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        # --- read current status before patching (same embed pattern as get_car.py) ---
        previous_status: str | None = None
        try:
            rows = client.postgrest_get(
                "inventory_car",
                {
                    "id": f"eq.{car_id}",
                    "select": "status_id,inventory_status_definition(code,label)",
                    "limit": "1",
                },
            )
            if rows:
                status_def = (rows[0].get("inventory_status_definition") or {})
                previous_status = status_def.get("label")
        except Exception:  # noqa: BLE001 — non-fatal; we still proceed with the PATCH
            pass

        # --- read the target status label so we can return it in the result ---
        new_status: str | None = None
        try:
            status_rows = client.postgrest_get(
                "inventory_status_definition",
                {
                    "id": f"eq.{new_status_id}",
                    "select": "label",
                    "limit": "1",
                },
            )
            if status_rows:
                new_status = status_rows[0].get("label")
        except Exception:  # noqa: BLE001 — non-fatal
            pass

        now = datetime.now(timezone.utc).isoformat()
        client.postgrest_write(
            "PATCH",
            "inventory_car",
            body={"status_id": new_status_id, "status_updated_at": now},
            params={"id": f"eq.{car_id}"},
            prefer="return=minimal",
        )

        # Insert status history (mirrors tRPC updateCarStatus).
        # If this fails, log but do not surface — mirrors tRPC pattern.
        try:
            history_body: dict = {
                "inventory_car_id": car_id,
                "to_status_id": new_status_id,
                "changed_at": now,
            }
            if note is not None:
                history_body["note"] = note
            client.postgrest_write(
                "POST",
                "inventory_status_history",
                body=history_body,
                prefer="return=minimal",
            )
        except Exception:  # noqa: BLE001
            pass  # non-fatal — mirrors tRPC pattern

        return ok({
            "carId": car_id,
            "newStatusId": new_status_id,
            "previous_status": previous_status,
            "new_status": new_status,
        })
    except Exception as e:  # noqa: BLE001
        return err(f"update_inventory_status failed: {e}")
    finally:
        client.close()
