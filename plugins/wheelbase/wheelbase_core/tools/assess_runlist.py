"""assess_runlist — score all cars in a runlist with a v1 heuristic."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def _compute_score(car: dict, criteria: str | None) -> int:
    base = car.get("imx_score") or 0
    if not isinstance(base, (int, float)):
        base = 0
    match_bonus = 0
    if criteria:
        label = f"{car.get('year', '')} {car.get('make', '')} {car.get('model', '')}".lower()
        if criteria.lower() in label:
            match_bonus = 10
    return max(0, min(100, int(base + match_bonus)))


def assess_runlist(args: dict, **kwargs) -> str:
    runlist_id = str(args.get("runlistId") or "").strip()
    if not runlist_id:
        return err("runlistId must be a non-empty UUID string")

    criteria = args.get("criteria")
    if criteria is not None:
        criteria = str(criteria).strip() or None

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        rows = client.postgrest_get(
            "runlist_cars_view",
            {
                "select": "id,runlist_id,inventory_car_id,year,make,model,vin,stock_number,imx_score,archived_at",
                "runlist_id": f"eq.{runlist_id}",
                "archived_at": "is.null",
                "limit": "5000",
            },
        )
        cars = rows or []

        if not cars:
            return ok({
                "runlistId": runlist_id,
                "assessed": 0,
                "topScore": 0,
                "avgScore": 0,
                "summary": "Assessed 0 cars. No cars found in this runlist.",
            })

        scores = [_compute_score(c, criteria) for c in cars]
        top_score = max(scores)
        avg_score = round(sum(scores) / len(scores))
        summary = f"Assessed {len(cars)} cars. Top score: {top_score}. Average: {avg_score}."
        return ok({
            "runlistId": runlist_id,
            "assessed": len(cars),
            "topScore": top_score,
            "avgScore": avg_score,
            "summary": summary,
        })
    except Exception as e:  # noqa: BLE001
        return err(f"assess_runlist failed: {e}")
    finally:
        client.close()
