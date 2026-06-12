"""generate_demand_score — score inventory cars using v1 IMX/mileage heuristic.

v1 placeholder formula (intentionally simple until demand matrix model is ready):
  base = imx_score (default 50 if null)
  mileage_penalty = max(0, (mileage - 30000) / 10000)  [every 10k miles over 30k → -1 pt]
  market_bonus = 2 if market string provided, else 0
  score = clamp(round(base - mileage_penalty + market_bonus), 0, 100)
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def _compute_score(car: dict, market: str | None) -> int:
    base = car.get("imx_score")
    base = base if isinstance(base, (int, float)) else 50
    mileage = car.get("mileage")
    mileage_penalty = max(0.0, (mileage - 30_000) / 10_000) if isinstance(mileage, (int, float)) else 0.0
    market_bonus = 2 if market else 0
    return max(0, min(100, round(base - mileage_penalty + market_bonus)))


def generate_demand_score(args: dict, **kwargs) -> str:
    car_ids = args.get("carIds")
    if car_ids is not None:
        if not isinstance(car_ids, list) or len(car_ids) == 0:
            return err("carIds must be a non-empty array of strings")
        if not all(isinstance(c, str) for c in car_ids):
            return err("carIds must be a non-empty array of strings")
        if len(car_ids) > 200:
            return err("carIds may contain at most 200 entries")

    market = args.get("market")
    if market is not None:
        if not isinstance(market, str):
            return err("market must be a string")
        market = market.strip() or None

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        if car_ids:
            cars = client.postgrest_get(
                "inventory_car",
                {
                    "select": "id,year,make,model,trim,mileage,imx_score",
                    "id": f"in.({','.join(car_ids)})",
                },
            )
        else:
            cars = client.postgrest_get(
                "inventory_car",
                {
                    "select": "id,year,make,model,trim,mileage,imx_score",
                    "is_archived": "eq.false",
                    "order": "created_at.desc",
                    "limit": "200",
                },
            )

        cars = cars or []
        if not cars:
            return ok({"scores": [], "summary": "No cars to score — inventory is empty or no matching cars found."})

        scores = [{"carId": c["id"], "score": _compute_score(c, market)} for c in cars]
        summary = (
            f"Scored {len(scores)} vehicle{'s' if len(scores) != 1 else ''}"
            + (f" for market: {market}" if market else "")
            + "."
        )
        return ok({"scores": scores, "summary": summary})
    except Exception as e:  # noqa: BLE001
        return err(f"generate_demand_score failed: {e}")
    finally:
        client.close()
