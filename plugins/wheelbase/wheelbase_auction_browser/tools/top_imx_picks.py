"""top_imx_picks — filter and sort runlist cars by IMX score.

Requires both the runlist snapshot and the IMX snapshot to be present.
Returns the top N cars (default 10) optionally filtered by minimum tier.
"""

import json
from pathlib import Path

from wheelbase_sdk import ok, err, workspace_dir


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"error": "snapshot not found"}
    except json.JSONDecodeError as exc:
        return {"error": f"invalid JSON: {exc}"}


def top_imx_picks(args: dict, **kwargs) -> str:  # noqa: ARG001
    runlist_id = str(args.get("runlistId") or "").strip()
    if not runlist_id:
        return err("runlistId is required")

    raw_limit = args.get("limit")
    raw_min_tier = args.get("minTier")

    try:
        limit = int(raw_limit) if raw_limit is not None else 10
        min_tier = int(raw_min_tier) if raw_min_tier is not None else 0
    except (TypeError, ValueError):
        return err("limit and minTier must be integers")

    if not (1 <= limit <= 100):
        return err("limit must be between 1 and 100")
    if not (0 <= min_tier <= 4):
        return err("minTier must be between 0 and 4")

    try:
        base = workspace_dir() / ".wheelbase" / "runlists"
        runlist_result = _read_json(base / f"{runlist_id}.json")
        imx_result = _read_json(base / f"{runlist_id}.imx.json")

        if "error" in runlist_result:
            return err("missing snapshot", runlistId=runlist_id)
        if "error" in imx_result:
            return err("missing snapshot", runlistId=runlist_id)

        scores = imx_result.get("scores") or {}
        picks = []
        for car in runlist_result.get("cars") or []:
            car_id = car.get("id")
            s = scores.get(car_id)
            if s is None:
                continue
            tier = s.get("tier") or 0
            if tier < min_tier:
                continue
            picks.append({
                **car,
                "score": s.get("score") or 0,
                "tier": tier,
                "components": s.get("components") or {},
            })

        picks.sort(key=lambda c: c["score"], reverse=True)
        picks = picks[:limit]

        return ok({"runlistId": runlist_id, "picks": picks})
    except Exception as exc:  # noqa: BLE001
        return err(f"top_imx_picks failed: {exc}")
