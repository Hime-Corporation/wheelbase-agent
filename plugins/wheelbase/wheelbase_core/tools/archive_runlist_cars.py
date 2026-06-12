"""archive_runlist_cars — soft-archive cars in a runlist (sets archived_at)."""

from datetime import datetime, timezone

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def archive_runlist_cars(args: dict, **kwargs) -> str:
    runlist_id = str(args.get("runlistId") or "").strip()
    if not runlist_id:
        return err("runlistId is required (uuid string)")

    car_ids = args.get("carIds")
    if not isinstance(car_ids, list) or len(car_ids) == 0:
        return err("carIds is required (array of uuid strings)")
    if len(car_ids) > 1000:
        return err("carIds must have at most 1000 elements")
    if not all(isinstance(c, str) for c in car_ids):
        return err("all carIds must be strings (uuids)")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        now = datetime.now(timezone.utc).isoformat()
        # PostgREST PATCH: set archived_at filtered by runlist_id + car_id in (...)
        client.postgrest_write(
            "PATCH",
            "runlist_cars",
            body={"archived_at": now},
            params={
                "runlist_id": f"eq.{runlist_id}",
                "car_id": f"in.({','.join(car_ids)})",
            },
            prefer="return=minimal",
        )
        return ok({
            "runlistId": runlist_id,
            "archivedCount": len(car_ids),
            "carIds": car_ids,
        })
    except Exception as e:  # noqa: BLE001
        return err(f"archive_runlist_cars failed: {e}")
    finally:
        client.close()
