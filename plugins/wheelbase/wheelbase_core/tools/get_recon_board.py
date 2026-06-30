"""get_recon_board — fetch all work_item_tree rows for a car and assemble nested tree.

Returns the recon_run root (with stages → findings/work_orders nested under it).
If no recon_run root exists, returns a flat items list instead.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

_VIEW = "work_item_tree"
_SELECT = (
    "id,type,parent_id,inventory_car_id,title,status,priority,"
    "rolled_est_cents,rolled_actual_cents,hold_type,hold_reason,"
    "due_at,created_at,sort_key,depth,root_id,effective_status"
)


def _build_tree(rows: list[dict]) -> dict:
    """Build nested tree from flat rows.  Returns the recon_run root or None."""
    by_id = {r["id"]: dict(r, children=[]) for r in rows}
    roots = []
    for node in by_id.values():
        pid = node.get("parent_id")
        if pid and pid in by_id:
            by_id[pid]["children"].append(node)
        else:
            roots.append(node)
    # Find the recon_run root if present.
    for r in roots:
        if r.get("type") == "recon_run":
            return r
    return None


def get_recon_board(args: dict, **kwargs) -> str:
    car_id = str(args.get("carId") or "").strip()
    if not car_id:
        return err("carId is required (uuid string)")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()
    try:
        rows = client.postgrest_get(
            _VIEW,
            {
                "inventory_car_id": f"eq.{car_id}",
                "select": _SELECT,
                "order": "depth.asc,sort_key.asc",
            },
        )
        recon_run = _build_tree(rows)
        if recon_run is not None:
            return ok({"reconRun": recon_run})
        return ok({"items": rows})
    except Exception as e:  # noqa: BLE001
        return err(f"get_recon_board failed: {e}")
    finally:
        client.close()
