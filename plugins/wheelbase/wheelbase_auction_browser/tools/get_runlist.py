"""get_runlist — fetch a runlist snapshot with IMX scores merged per car.

Reads `.wheelbase/runlists/<runlistId>.json` and (optionally)
`.wheelbase/runlists/<runlistId>.imx.json`, then merges each car's IMX
score into the car record.  If the IMX snapshot is missing, the runlist is
still returned with a `note: "imx snapshot missing"` field.
"""

import json
from pathlib import Path

from wheelbase_sdk import ok, err, workspace_dir


def _read_json(path: Path):
    """Return parsed JSON or a dict with an "error" key."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"error": "snapshot not found"}
    except json.JSONDecodeError as exc:
        return {"error": f"invalid JSON: {exc}"}


def get_runlist(args: dict, **kwargs) -> str:  # noqa: ARG001
    runlist_id = str(args.get("runlistId") or "").strip()
    if not runlist_id:
        return err("runlistId is required")

    try:
        base = workspace_dir() / ".wheelbase" / "runlists"
        runlist_result = _read_json(base / f"{runlist_id}.json")
        imx_result = _read_json(base / f"{runlist_id}.imx.json")

        if "error" in runlist_result:
            return err(runlist_result["error"], runlistId=runlist_id)

        imx_missing = "error" in imx_result
        imx = None if imx_missing else imx_result

        cars = []
        for car in runlist_result.get("cars") or []:
            if imx is not None:
                score = (imx.get("scores") or {}).get(car.get("id"))
                if score:
                    car = {
                        **car,
                        "imxScore": score.get("score"),
                        "imxTier": score.get("tier"),
                        "imxComponents": score.get("components"),
                    }
            cars.append(car)

        result = {
            "runlistId": runlist_result.get("id") or runlist_id,
            "auctionId": runlist_result.get("auctionId"),
            "name": runlist_result.get("name"),
            "cars": cars,
        }
        if imx_missing:
            result["note"] = "imx snapshot missing"

        return ok(result)
    except Exception as exc:  # noqa: BLE001
        return err(f"get_runlist failed: {exc}")
