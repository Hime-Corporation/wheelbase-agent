"""list_runlists — fetch runlist summaries from the `runlists` table."""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def list_runlists(args: dict, **kwargs) -> str:
    raw_limit = args.get("limit")
    limit = 25
    if raw_limit is not None:
        try:
            limit = max(1, min(500, int(raw_limit)))
        except (TypeError, ValueError):
            return err("limit must be a number")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        rows = client.postgrest_get(
            "runlists",
            {
                "select": "runlist_id,name,auction_id,auction_date,created_at",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )
        summaries = [
            {
                "runlistId": r.get("runlist_id"),
                "name": r.get("name"),
                "auctionId": r.get("auction_id"),
                "auctionDate": r.get("auction_date"),
                "createdAt": r.get("created_at"),
            }
            for r in (rows or [])
        ]
        return ok(summaries)
    except Exception as e:  # noqa: BLE001
        return err(f"list_runlists failed: {e}")
    finally:
        client.close()
