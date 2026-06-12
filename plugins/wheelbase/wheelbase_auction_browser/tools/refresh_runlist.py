"""refresh_runlist — emit a structured instruction to re-score a runlist.

The tool itself only emits a structured instruction; the actual network call
and file-system write are executed by the renderer-side handler registered
via `useAuctionBrowserHandlers`.
"""

from wheelbase_sdk import ok, err


def refresh_runlist(args: dict, **kwargs) -> str:  # noqa: ARG001
    runlist_id = str(args.get("runlistId") or "").strip()
    if not runlist_id:
        return err("runlistId is required")

    try:
        return ok({"kind": "refresh_runlist", "runlistId": runlist_id})
    except Exception as exc:  # noqa: BLE001
        return err(f"refresh_runlist failed: {exc}")
