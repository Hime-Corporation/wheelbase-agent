"""flag_car — emit a structured instruction to flag a car for follow-up.

Appends a `{ type: "flag", note, … }` entry to the car's vote_history.
The tool emits a structured instruction; the renderer handler executes the
actual mutation.
"""

from wheelbase_sdk import ok, err


def flag_car(args: dict, **kwargs) -> str:  # noqa: ARG001
    runlist_id = str(args.get("runlistId") or "").strip()
    car_id = str(args.get("carId") or "").strip()
    if not runlist_id or not car_id:
        return err("runlistId and carId are required")

    try:
        note_raw = args.get("note")
        note = str(note_raw).strip() if note_raw is not None else None
        return ok({
            "kind": "flag_car",
            "runlistId": runlist_id,
            "carId": car_id,
            "note": note,
        })
    except Exception as exc:  # noqa: BLE001
        return err(f"flag_car failed: {exc}")
