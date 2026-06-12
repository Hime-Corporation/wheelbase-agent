"""list_runlists — return runlist stubs for a specific auction.

Reads `.wheelbase/auctions.json`, locates the requested auction by id, and
returns the runlists embedded in the record.  When a snapshot embeds a
`runlists` array we return it directly; otherwise we return an empty array
with a note directing the caller to use `refresh_runlist`.
"""

import json
from pathlib import Path

from wheelbase_sdk import ok, err, workspace_dir

_AUCTIONS_PATH = ".wheelbase/auctions.json"


def list_runlists(args: dict, **kwargs) -> str:  # noqa: ARG001
    auction_id = str(args.get("auctionId") or "").strip()
    if not auction_id:
        return err("auctionId is required")

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

        auction = next((a for a in data if a.get("id") == auction_id), None)
        if auction is None:
            return err(f"auction '{auction_id}' not found", auctionId=auction_id)

        runlists = auction.get("runlists")
        if isinstance(runlists, list) and len(runlists) > 0:
            return ok({"auctionId": auction_id, "runlists": runlists})

        return ok({
            "auctionId": auction_id,
            "runlists": [],
            "note": "snapshot includes counts only — call refresh_runlist for details",
        })
    except Exception as exc:  # noqa: BLE001
        return err(f"list_runlists failed: {exc}")
