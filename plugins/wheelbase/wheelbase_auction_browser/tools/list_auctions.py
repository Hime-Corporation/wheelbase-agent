"""list_auctions — read the auctions snapshot from the workspace.

Reads `.wheelbase/auctions.json` in the agent workspace directory and returns
the raw array.  No network call; the renderer writes this file periodically
and on session start.

Handler contract:
  - signature `def fn(args: dict, **kwargs) -> str`
  - ALWAYS return a JSON string; NEVER raise
  - workspace_dir() reads TERMINAL_CWD (or cwd fallback)
"""

import json
from pathlib import Path

from wheelbase_sdk import ok, err, workspace_dir

_AUCTIONS_PATH = ".wheelbase/auctions.json"


def list_auctions(args: dict, **kwargs) -> str:  # noqa: ARG001 — no args expected
    try:
        path: Path = workspace_dir() / _AUCTIONS_PATH
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return err("snapshot not found", path=str(_AUCTIONS_PATH))
        except json.JSONDecodeError as exc:
            return err(f"auctions snapshot is not valid JSON: {exc}")
        if not isinstance(data, list):
            return err("auctions snapshot must be a JSON array")
        return ok(data)
    except Exception as exc:  # noqa: BLE001
        return err(f"list_auctions failed: {exc}")
