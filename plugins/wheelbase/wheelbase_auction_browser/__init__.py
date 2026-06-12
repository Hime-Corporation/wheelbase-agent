"""wheelbase-auction-browser plugin — registration.

All tools are marker-gated: they are hidden from the model unless the file
`.wheelbase-auction-browser-active` exists in the agent workspace directory
(resolved from TERMINAL_CWD or cwd).

The pre_llm_call hook injects an auction-browser context block into every
turn when the marker is active, including the current auction count from the
workspace snapshot.
"""

import json
import logging
from pathlib import Path

from wheelbase_sdk import workspace_dir

from . import schemas
from .tools import (
    list_auctions as list_auctions_mod,
    list_runlists as list_runlists_mod,
    get_runlist as get_runlist_mod,
    top_imx_picks as top_imx_picks_mod,
    explain_imx as explain_imx_mod,
    refresh_runlist as refresh_runlist_mod,
    flag_car as flag_car_mod,
    vote_on_car as vote_on_car_mod,
)

logger = logging.getLogger(__name__)

_MARKER_FILENAME = ".wheelbase-auction-browser-active"
_AUCTIONS_PATH = ".wheelbase/auctions.json"


def _marker_active() -> bool:
    """Return True when the auction-browser marker file is present in the workspace."""
    try:
        return (workspace_dir() / _MARKER_FILENAME).exists()
    except Exception:  # noqa: BLE001
        return False


def _read_auction_count() -> int | None:
    """Read the number of auctions in the workspace snapshot, or None if unavailable."""
    try:
        path = workspace_dir() / _AUCTIONS_PATH
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return len(data)
        return None
    except Exception:  # noqa: BLE001
        return None


def _build_auction_browser_context() -> str:
    """Build the system-prompt addendum text for auction-browser mode."""
    auction_count = _read_auction_count()
    if auction_count is not None:
        count_line = (
            f"Currently {auction_count} upcoming auction"
            f"{'s' if auction_count != 1 else ''} tracked in workspace snapshots."
        )
    else:
        count_line = "No auction snapshot is loaded yet in the workspace."

    return "\n".join([
        "## Wheelbase Auction Browser Mode",
        "You are helping the user navigate auctions and identify high-value cars on",
        "runlists using IMX (Inventory Match Index) scores.",
        "",
        count_line,
        "",
        "Use `list_auctions` to see upcoming auctions, `list_runlists` to inspect a",
        "specific auction's runlists, and `get_runlist` to inspect cars in one runlist",
        "with their IMX scores. Use `top_imx_picks` to surface the best fits for the",
        "dealer's demand matrix. Use `explain_imx` to break down why a specific car",
        "scored as it did. Use `flag_car` and `vote_on_car` to act on user requests.",
        "If the IMX snapshot looks stale, call `refresh_runlist` to request a fresh",
        "score from the backend.",
        "",
        "Snapshot files live under `.wheelbase/auctions.json`,",
        "`.wheelbase/runlists/<runlist_id>.json`, and",
        "`.wheelbase/runlists/<runlist_id>.imx.json`. The host re-writes them on",
        "`refresh_runlist` and on a 60-second timer while the chat is open.",
        "",
    ])


def _pre_llm_call_hook(**kwargs) -> dict | None:  # noqa: ARG001
    """Inject auction-browser context when the marker is active."""
    if not _marker_active():
        return None
    try:
        return {"context": _build_auction_browser_context()}
    except Exception:  # noqa: BLE001
        logger.exception("wheelbase_auction_browser: pre_llm_call hook failed")
        return None


def register(ctx) -> None:
    """Wire schemas to handlers and register the pre_llm_call hook."""
    ctx.register_tool(
        name="list_auctions",
        toolset="wheelbase_auction_browser",
        schema=schemas.LIST_AUCTIONS,
        handler=list_auctions_mod.list_auctions,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="list_runlists",
        toolset="wheelbase_auction_browser",
        schema=schemas.LIST_RUNLISTS,
        handler=list_runlists_mod.list_runlists,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="get_runlist",
        toolset="wheelbase_auction_browser",
        schema=schemas.GET_RUNLIST,
        handler=get_runlist_mod.get_runlist,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="top_imx_picks",
        toolset="wheelbase_auction_browser",
        schema=schemas.TOP_IMX_PICKS,
        handler=top_imx_picks_mod.top_imx_picks,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="explain_imx",
        toolset="wheelbase_auction_browser",
        schema=schemas.EXPLAIN_IMX,
        handler=explain_imx_mod.explain_imx,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="refresh_runlist",
        toolset="wheelbase_auction_browser",
        schema=schemas.REFRESH_RUNLIST,
        handler=refresh_runlist_mod.refresh_runlist,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="flag_car",
        toolset="wheelbase_auction_browser",
        schema=schemas.FLAG_CAR,
        handler=flag_car_mod.flag_car,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="vote_on_car",
        toolset="wheelbase_auction_browser",
        schema=schemas.VOTE_ON_CAR,
        handler=vote_on_car_mod.vote_on_car,
        check_fn=_marker_active,
    )

    ctx.register_hook("pre_llm_call", _pre_llm_call_hook)
