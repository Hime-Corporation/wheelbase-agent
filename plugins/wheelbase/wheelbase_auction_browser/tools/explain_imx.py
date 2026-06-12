"""explain_imx — human-readable IMX score breakdown for one car.

Reads `.wheelbase/runlists/<runlistId>.imx.json` and returns the tier label,
per-component sub-scores (demand fit, mileage health, vehicle age), and
category matches for the requested car.
"""

import json
from pathlib import Path

from wheelbase_sdk import ok, err, workspace_dir

_TIER_LABELS = ["Skip", "Risky", "Watch", "Pursue", "Must Buy"]


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"error": "snapshot not found"}
    except json.JSONDecodeError as exc:
        return {"error": f"invalid JSON: {exc}"}


def explain_imx(args: dict, **kwargs) -> str:  # noqa: ARG001
    runlist_id = str(args.get("runlistId") or "").strip()
    car_id = str(args.get("carId") or "").strip()
    if not runlist_id or not car_id:
        return err("runlistId and carId are required")

    try:
        imx_path = workspace_dir() / ".wheelbase" / "runlists" / f"{runlist_id}.imx.json"
        imx_result = _read_json(imx_path)

        if "error" in imx_result:
            return err("no score", runlistId=runlist_id, carId=car_id)

        scores = imx_result.get("scores") or {}
        s = scores.get(car_id)
        if s is None:
            return err("no score", runlistId=runlist_id, carId=car_id)

        components = s.get("components") or {}
        tier_idx = s.get("tier") or 0
        tier_label = _TIER_LABELS[tier_idx] if 0 <= tier_idx < len(_TIER_LABELS) else "Skip"

        reasons = [
            f"Demand fit: {round((components.get('fit') or 0) * 100)}/100",
            f"Mileage health: {round((components.get('mileage') or 0) * 100)}/100",
            f"Vehicle age: {round((components.get('age') or 0) * 100)}/100",
        ]

        return ok({
            "runlistId": runlist_id,
            "carId": car_id,
            "score": s.get("score"),
            "tier": tier_label,
            "reasons": reasons,
            "categoryMatches": s.get("categoryMatches") or {},
        })
    except Exception as exc:  # noqa: BLE001
        return err(f"explain_imx failed: {exc}")
