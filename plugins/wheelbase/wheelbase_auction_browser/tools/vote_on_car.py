"""vote_on_car — emit a structured instruction to cast a vote on a car.

Increments or decrements a car's vote_count and appends a vote entry to its
vote_history jsonb in `runlist_cars`.  The tool emits a structured instruction;
the renderer handler executes the actual mutation.
"""

from wheelbase_sdk import ok, err


def vote_on_car(args: dict, **kwargs) -> str:  # noqa: ARG001
    runlist_id = str(args.get("runlistId") or "").strip()
    car_id = str(args.get("carId") or "").strip()
    if not runlist_id or not car_id:
        return err("runlistId and carId are required")

    vote = args.get("vote")
    try:
        vote = int(vote)
    except (TypeError, ValueError):
        return err("vote must be 1 or -1")
    if vote not in (1, -1):
        return err("vote must be 1 or -1")

    try:
        note_raw = args.get("note")
        note = str(note_raw).strip() if note_raw is not None else None
        return ok({
            "kind": "vote_on_car",
            "runlistId": runlist_id,
            "carId": car_id,
            "vote": vote,
            "note": note,
        })
    except Exception as exc:  # noqa: BLE001
        return err(f"vote_on_car failed: {exc}")
