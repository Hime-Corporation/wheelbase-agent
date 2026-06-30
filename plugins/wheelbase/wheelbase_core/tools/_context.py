"""_context — shared dealership-context resolver for Wheelbase tools.

Usage:
    result = resolve_dealership_context(client, dealership_id=args.get("dealershipId"))
    if result == "NO_DEALERSHIP":
        return err("No dealership found for this user")
    if result == "AMBIGUOUS":
        return err("Multiple dealerships — pass dealershipId")
    tenant_id, dealership_id = result
"""

from __future__ import annotations


def resolve_dealership_context(client, dealership_id: str | None = None) -> tuple | str:
    """Return (tenant_id, dealership_id) or a sentinel string.

    Sentinels:
      "NO_DEALERSHIP"  — query returned zero rows
      "AMBIGUOUS"      — query returned >1 row and no dealership_id was supplied
    """
    params: dict = {"select": "dealership_id,tenant_id"}
    if dealership_id:
        params["dealership_id"] = f"eq.{dealership_id}"
        params["limit"] = "1"

    rows = client.postgrest_get("dealership", params)

    if not rows:
        return "NO_DEALERSHIP"

    if len(rows) > 1:
        # No specific dealership was requested and multiple exist — caller must disambiguate.
        return "AMBIGUOUS"

    row = rows[0]
    return (row["tenant_id"], row["dealership_id"])
