"""One-time startup migration from flat to tenant-keyed profile layout.

The shared gateway container is moving profile storage from a flat layout::

    <hermes_root>/profiles/wb-<uid>/

to a tenant-keyed layout::

    <hermes_root>/tenants/<tenant_id>/profiles/wb-<uid>/

:func:`run_tenant_migration` performs that move once, at gateway startup,
before the router begins resolving profile paths for incoming connections.
This module only *moves directories on disk* — it does not know how the
router resolves paths and does not touch ``profile_router.py``.

Ordering matters: routing to tenant-keyed paths must not start until a
migration pass has *succeeded* (marker written) for any host that already
has legacy ``profiles/wb-<uid>`` directories. If the router starts resolving
tenant-keyed paths against a host where migration was skipped (e.g. missing
Supabase credentials), previously-active users appear to have no profile and
get a **fresh, empty** tenant-keyed profile — not data loss, since the
legacy directory is left untouched on disk, but a confusing UX regression
until the next restart runs migration successfully. Operators should ensure
Supabase credentials are present before restarting the router after this
change ships.

Completion marker
------------------
``<hermes_root>/tenants/.migration-completed`` marks a *successful* pass
(including a pass that produced orphans — orphans are a handled outcome,
not a failure). While the marker is absent, the router should keep treating
migration as pending; once present, migration is permanently skipped on
every subsequent boot. A short-circuit return (marker already present)
yields an all-zero :class:`MigrationReport` — that is a deliberate no-op
signal, distinct from the ``skipped`` *counter*, which only increments for
individual invalid-looking directories encountered during an active pass.

Failure handling
-----------------
* No Supabase credentials configured, but legacy profiles exist: nothing is
  moved, the marker is **not** written (so the next boot retries), and a
  loud warning + report error are recorded.
* Supabase lookup fails after one retry (persisting network failure):
  nothing further is moved in this pass, the marker is **not** written, and
  a report error is recorded. Per-directory moves are independently atomic
  (``os.rename`` within the same volume), so a prior partial pass's moved
  directories are simply absent from the next pass's scan — re-running is
  safe and idempotent.
* A legacy directory name that doesn't look like ``wb-<safe-id>`` is left
  alone and counted in ``skipped`` — it is not ours to move.
* A uid with no matching Supabase ``user_profile`` row (or a tenant_id that
  fails validation) is moved to ``<hermes_root>/_orphaned/wb-<uid>/``
  (collision-safe — a numeric suffix is appended if the target exists) and
  counted in ``orphaned``.
* ``<hermes_root>/auth.json`` (pre-existing operator-global credentials) is
  never moved or deleted. Its presence is logged at warning level and noted
  in the report as a cross-tenant fallback pending a human operator audit.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)

PROFILE_PREFIX = "wb-"
_MARKER_NAME = ".migration-completed"
_ORPHAN_DIR_NAME = "_orphaned"
_TENANTS_DIR_NAME = "tenants"
_PROFILES_DIR_NAME = "profiles"
_AUTH_JSON_NAME = "auth.json"

# Same safe-identifier pattern used for Wheelbase user ids
# (tui_gateway.wheelbase_identity.is_valid_user_id) — reimplemented here
# rather than imported so this module has no dependency on profile_router's
# neighbors beyond the standard library and httpx.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_CHUNK_SIZE = 50
_REQUEST_TIMEOUT_S = 10.0
_MAX_ATTEMPTS = 2  # one initial attempt + one retry


@dataclass
class MigrationReport:
    migrated: int = 0
    orphaned: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _is_safe_id(value: str) -> bool:
    return bool(value) and bool(_SAFE_ID_RE.match(value))


def _marker_path(hermes_root: Path) -> Path:
    return hermes_root / _TENANTS_DIR_NAME / _MARKER_NAME


def _write_marker(hermes_root: Path) -> None:
    marker = _marker_path(hermes_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("")


def _legacy_profile_dirs(hermes_root: Path) -> list[Path]:
    profiles_root = hermes_root / _PROFILES_DIR_NAME
    if not profiles_root.is_dir():
        return []
    return sorted(
        p
        for p in profiles_root.iterdir()
        if p.is_dir() and p.name.startswith(PROFILE_PREFIX)
    )


def _note_auth_json(hermes_root: Path, report: MigrationReport) -> None:
    auth_json = hermes_root / _AUTH_JSON_NAME
    if not auth_json.exists():
        return
    msg = (
        f"tenant migration: {auth_json} exists and is now a cross-tenant "
        "fallback pending operator audit — left in place (not moved or "
        "deleted) by tenant migration"
    )
    logger.warning(msg)
    report.errors.append(f"note: {msg}")


def _orphan_dest(hermes_root: Path, dir_name: str) -> Path:
    base = hermes_root / _ORPHAN_DIR_NAME
    candidate = base / dir_name
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        candidate = base / f"{dir_name}-{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


def _fetch_tenant_map(
    supabase_url: str, supabase_key: str, uids: list[str]
) -> dict[str, str]:
    """Batch-resolve ``uid -> tenant_id`` via PostgREST ``id=in.(...)``.

    Raises the underlying exception if a chunk fails after one retry — the
    caller decides how to treat that as a persisting network failure.
    """
    result: dict[str, str] = {}
    base = supabase_url.rstrip("/")
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
    }
    url = f"{base}/rest/v1/user_profile"

    for start in range(0, len(uids), _CHUNK_SIZE):
        chunk = uids[start : start + _CHUNK_SIZE]
        params = {
            "id": "in.(" + ",".join(chunk) + ")",
            "select": "id,tenant_id",
        }
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = httpx.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=_REQUEST_TIMEOUT_S,
                )
                response.raise_for_status()
                rows = response.json()
                for row in rows:
                    rid = row.get("id")
                    tid = row.get("tenant_id")
                    if rid and tid:
                        result[rid] = tid
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001 - retried below
                last_exc = exc
                logger.warning(
                    "tenant migration: supabase lookup attempt %d/%d failed: %s",
                    attempt,
                    _MAX_ATTEMPTS,
                    exc,
                )
        if last_exc is not None:
            raise last_exc

    return result


def _partition_legacy_dirs(
    legacy_dirs: Iterable[Path], report: MigrationReport
) -> list[tuple[str, Path]]:
    valid: list[tuple[str, Path]] = []
    for d in legacy_dirs:
        uid = d.name[len(PROFILE_PREFIX) :]
        if _is_safe_id(uid):
            valid.append((uid, d))
        else:
            logger.warning(
                "tenant migration: skipping legacy dir with unsafe name %r",
                d.name,
            )
            report.skipped += 1
    return valid


def _move_dir(src: Path, dest: Path, report: MigrationReport, *, orphan: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
    except OSError as exc:
        msg = f"tenant migration: failed to move {src} -> {dest}: {exc}"
        logger.warning(msg)
        report.errors.append(msg)
        return
    if orphan:
        report.orphaned += 1
        logger.info("tenant migration: orphaned %s -> %s (no tenant match)", src.name, dest)
    else:
        report.migrated += 1
        logger.info("tenant migration: migrated %s -> %s", src.name, dest)


def run_tenant_migration(
    hermes_root: Path, supabase_url: str, supabase_key: str
) -> MigrationReport:
    """Run the one-time flat -> tenant-keyed profile migration.

    Idempotent and safe to call on every gateway startup: it short-circuits
    once the completion marker exists, and any prior partially-completed
    pass leaves already-moved directories simply absent from the next
    pass's scan (per-directory ``os.rename`` moves are independently atomic
    within the same volume).

    See the module docstring for the full behavioral contract (marker
    semantics, missing-credentials handling, network-failure handling,
    orphan handling, and the ``auth.json`` note).
    """
    hermes_root = Path(hermes_root)
    report = MigrationReport()

    if _marker_path(hermes_root).exists():
        return report

    legacy_dirs = _legacy_profile_dirs(hermes_root)

    if not legacy_dirs:
        # Fresh install (or a host migrated by some other means) — nothing
        # to move. Mark complete so future boots skip the directory scan.
        _note_auth_json(hermes_root, report)
        _write_marker(hermes_root)
        logger.info(
            "tenant migration: no legacy profiles found under %s, marker written",
            hermes_root / _PROFILES_DIR_NAME,
        )
        return report

    if not supabase_url or not supabase_key:
        msg = (
            f"tenant migration: {len(legacy_dirs)} legacy profile dir(s) found "
            f"under {hermes_root / _PROFILES_DIR_NAME} but supabase_url/"
            "supabase_key were not provided — skipping migration. Existing "
            "users will get fresh empty tenant-keyed profiles until "
            "credentials are supplied and the gateway is restarted; no data "
            "was moved or lost."
        )
        logger.warning(msg)
        report.errors.append(msg)
        return report

    valid = _partition_legacy_dirs(legacy_dirs, report)
    uids = [uid for uid, _ in valid]

    tenant_map: dict[str, str] = {}
    if uids:
        try:
            tenant_map = _fetch_tenant_map(supabase_url, supabase_key, uids)
        except Exception as exc:  # noqa: BLE001 - persisting network failure
            msg = f"tenant migration: supabase lookup failed after retry: {exc}"
            logger.warning(msg)
            report.errors.append(msg)
            # Nothing has moved yet in this pass — bail without the marker
            # so the next boot retries the whole scan.
            return report

    for uid, src in valid:
        tenant_id = tenant_map.get(uid)
        if tenant_id and _is_safe_id(tenant_id):
            dest = hermes_root / _TENANTS_DIR_NAME / tenant_id / _PROFILES_DIR_NAME / src.name
            _move_dir(src, dest, report, orphan=False)
        else:
            dest = _orphan_dest(hermes_root, src.name)
            _move_dir(src, dest, report, orphan=True)

    _note_auth_json(hermes_root, report)
    _write_marker(hermes_root)
    logger.info(
        "tenant migration summary: migrated=%d orphaned=%d skipped=%d errors=%d",
        report.migrated,
        report.orphaned,
        report.skipped,
        len(report.errors),
    )
    return report
