"""propose_demand_targets — present a structured proposal to the user.

Pure-data tool: no network calls. Returns the proposals as structured JSON
so the renderer can surface a confirmation card. The user's acceptance triggers
save_demand_overrides.
"""

from wheelbase_sdk import ok, err


def propose_demand_targets(args: dict, **kwargs) -> str:  # noqa: ARG001
    proposals = args.get("proposals")
    if not isinstance(proposals, list) or len(proposals) == 0:
        return err("proposals must be a non-empty array")

    for i, item in enumerate(proposals):
        if not isinstance(item, dict):
            return err(f"proposals[{i}] must be an object")
        key = item.get("key")
        target = item.get("target")
        if not isinstance(key, str) or not key.strip():
            return err(f"proposals[{i}].key must be a non-empty string")
        if not isinstance(target, int) or target < 0 or target > 200:
            return err(
                f"proposals[{i}].target must be an integer between 0 and 200"
            )

    try:
        return ok({"kind": "propose_demand_targets", "proposals": proposals})
    except Exception as exc:  # noqa: BLE001
        return err(f"propose_demand_targets failed: {exc}")
