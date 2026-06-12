"""read_inventory_summary — read .wheelbase/inventory-summary.json from the workspace.

Workspace-file tool: no network calls. Reads a JSON snapshot written by the
Wheelbase desktop into the agent workspace directory.
"""

import json
from pathlib import Path

from wheelbase_sdk import workspace_dir, ok, err


def read_inventory_summary(args: dict, **kwargs) -> str:  # noqa: ARG001
    try:
        ws = workspace_dir()
        path = ws / ".wheelbase" / "inventory-summary.json"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return err("snapshot not found", file=".wheelbase/inventory-summary.json")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return err(f"inventory-summary.json is not valid JSON: {exc}")
        return ok(data)
    except Exception as exc:  # noqa: BLE001
        return err(f"read_inventory_summary failed: {exc}")
