"""save_inventory_demand_labels — assign demand category labels to unlabeled cars.

PostgREST write tool: PATCH inventory_car (bulk upsert via prefer=resolution=merge-duplicates).
Maps: labels[].inventoryCarId → inventory_car.id, labels[].key → inventory_car.demand_category_key.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err


def save_inventory_demand_labels(args: dict, **kwargs) -> str:  # noqa: ARG001
    labels = args.get("labels")
    if not isinstance(labels, list) or len(labels) == 0:
        return err("labels must be a non-empty array")

    for i, item in enumerate(labels):
        if not isinstance(item, dict):
            return err(f"labels[{i}] must be an object")
        car_id = item.get("inventoryCarId")
        key = item.get("key")
        if not isinstance(car_id, str) or not car_id.strip():
            return err(f"labels[{i}].inventoryCarId must be a non-empty UUID string")
        if not isinstance(key, str) or not key.strip():
            return err(f"labels[{i}].key must be a non-empty string")

    try:
        client = WheelbaseClient()
    except WheelbaseAuthError:
        return signed_out_result()

    try:
        # Build the batch of updates: set demand_category_key on each car row.
        rows = [
            {"id": item["inventoryCarId"], "demand_category_key": item["key"]}
            for item in labels
        ]
        result = client.postgrest_write(
            "PATCH",
            "inventory_car",
            body=rows,
            params={"id": f"in.({','.join(r['id'] for r in rows)})"},
            prefer="return=minimal",
        )
        return ok(
            result if result is not None else {"labeled": len(labels)}
        )
    except Exception as exc:  # noqa: BLE001
        return err(f"save_inventory_demand_labels failed: {exc}")
    finally:
        client.close()
