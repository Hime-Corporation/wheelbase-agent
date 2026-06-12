"""read_unlabeled_cars — read .wheelbase/unlabeled-cars.json from the workspace.

Workspace-file tool: no network calls. Reads a JSON snapshot written by the
Wheelbase desktop into the agent workspace directory. Supports a `limit`
parameter to slice the list (default 25, max 100).
"""

import json

from wheelbase_sdk import workspace_dir, ok, err

_DEFAULT_LIMIT = 25
_MAX_LIMIT = 100


def read_unlabeled_cars(args: dict, **kwargs) -> str:  # noqa: ARG001
    # Validate limit
    raw_limit = args.get("limit")
    if raw_limit is None:
        limit = _DEFAULT_LIMIT
    elif not isinstance(raw_limit, (int, float)):
        return err("limit must be an integer between 1 and 100")
    else:
        limit = int(raw_limit)
        if limit < 1 or limit > _MAX_LIMIT:
            return err(f"limit must be between 1 and {_MAX_LIMIT}")

    try:
        ws = workspace_dir()
        path = ws / ".wheelbase" / "unlabeled-cars.json"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return err("snapshot not found", file=".wheelbase/unlabeled-cars.json")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return err(f"unlabeled-cars.json is not valid JSON: {exc}")

        cars = data if isinstance(data, list) else []
        sliced = cars[:limit]
        return ok({"cars": sliced, "total": len(sliced)})
    except Exception as exc:  # noqa: BLE001
        return err(f"read_unlabeled_cars failed: {exc}")
