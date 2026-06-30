"""dc_ingest — parse and import a DealerCenter export into Wheelbase inventory.

REFERENCE HANDLER PATTERN (copy this for every Wheelbase tool):
  - signature `def fn(args: dict, **kwargs) -> str`
  - validate args -> `err(...)` on bad input
  - build the client; `WheelbaseAuthError` -> `signed_out_result()`
  - do the work; ALWAYS return a JSON string; NEVER raise (catch -> `err(...)`)
  - close the client in `finally`
The module-level `WheelbaseClient` name is the test seam — tests monkeypatch it.
"""

from wheelbase_sdk import WheelbaseClient, WheelbaseAuthError, signed_out_result, ok, err

from ..parsing import parse_export, normalize_rows

_BATCH_SIZE = 200


def dc_ingest(args: dict, **kwargs) -> str:  # noqa: ARG001
    path = str(args.get("path") or "").strip()
    if not path:
        return err("path is required")

    dry_run: bool = args.get("dryRun", True)
    if not isinstance(dry_run, bool):
        dry_run = True

    raw = parse_export(path)
    if isinstance(raw, str):  # error string from parse_export
        return err(raw)

    rows, unmapped = normalize_rows(raw)

    if dry_run:
        return ok({
            "dryRun": True,
            "preview": rows[:10],
            "counts": {"rows": len(rows)},
            "unmappedHeaders": unmapped,
        })

    client = None
    try:
        client = WheelbaseClient()
        created = 0
        updated = 0
        skipped = 0
        errors: list = []

        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i: i + _BATCH_SIZE]
            resp = client.go_api("POST", "/v1/inventory/import/historic", body={"rows": batch})
            if resp:
                created += resp.get("created", 0)
                updated += resp.get("updated", 0)
                skipped += resp.get("skipped", 0)
                errors.extend(resp.get("errors", []))

        return ok({
            "dryRun": False,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
        })
    except WheelbaseAuthError:
        return signed_out_result()
    except Exception as e:  # noqa: BLE001
        return err(str(e))
    finally:
        if client is not None:
            client.close()
