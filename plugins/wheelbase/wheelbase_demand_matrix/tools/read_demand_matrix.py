"""read_demand_matrix — read .wheelbase/demand-matrix.json from the agent workspace.

Workspace-file tool: no network calls. Reads a JSON snapshot written by the
Wheelbase desktop into the agent workspace directory.
"""

import json
from pathlib import Path

from wheelbase_sdk import workspace_dir, ok, err


def read_demand_matrix(args: dict, **kwargs) -> str:  # noqa: ARG001
    try:
        ws = workspace_dir()
        path = ws / ".wheelbase" / "demand-matrix.json"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return err("snapshot not found", file=".wheelbase/demand-matrix.json")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return err(f"demand-matrix.json is not valid JSON: {exc}")
        return ok(data)
    except Exception as exc:  # noqa: BLE001
        return err(f"read_demand_matrix failed: {exc}")
