#!/usr/bin/env python3
"""One-shot backfill: split the legacy SHARED Hermes store into per-user profiles.

Context
-------
The cloud gateway moved from a single shared dashboard
(``HERMES_HOME=/data/hermes``, one ``state.db`` for every dealership user) to the
per-profile router (``tui_gateway/profile_router.py``), which routes each
authenticated user to a freshly-provisioned ``/data/hermes/profiles/wb-<user_id>``
home. Pre-cutover chat history still lives in the old shared
``/data/hermes/state.db`` and is no longer reachable by any user — that is why
existing conversations open empty after the cutover.

This script copies each user's sessions + messages out of the shared store and
into their per-user profile store. It is:

* **Non-destructive** — it only READS the shared store; it never mutates or
  deletes it. Roll-forward stays reversible (delete the new profile state.db's).
* **Idempotent** — re-running copies nothing already present. Each user is
  migrated inside a single transaction (all-or-nothing).
* **Attribution-safe** — sessions are split by ``sessions.user_id`` (stamped
  since the multi-user scoping change). Legacy sessions with a NULL ``user_id``
  cannot be attributed and are reported, never guessed.

Usage
-----
    # dry run (default) — report what WOULD be migrated, change nothing
    python scripts/migrate_shared_store_to_profiles.py --hermes-home /data/hermes

    # apply
    python scripts/migrate_shared_store_to_profiles.py --hermes-home /data/hermes --apply

    # single user (e.g. to validate one account first)
    python scripts/migrate_shared_store_to_profiles.py --hermes-home /data/hermes --apply --user <uuid>

Run it INSIDE the gateway container, against the mounted volume, e.g.:
    docker exec -it <gateway> python scripts/migrate_shared_store_to_profiles.py \
        --hermes-home /data/hermes --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Importable both as a module (tests: scripts.migrate_shared_store_to_profiles)
# and as a script. When run directly, ensure the repo root is on sys.path so
# `hermes_state` resolves.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PROFILE_PREFIX = "wb-"


@dataclass
class Report:
    users: dict[str, int] = field(default_factory=dict)
    null_user_sessions: int = 0


@dataclass
class CopyStats:
    sessions_copied: int = 0
    messages_copied: int = 0


def _table_columns(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    return [
        row[1]
        for row in conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()
    ]


def report(shared_db: Path) -> Report:
    """Summarise the shared store: sessions per user_id and NULL-user count.

    Read-only; safe to run against the live shared store.
    """
    conn = sqlite3.connect(f"file:{shared_db}?mode=ro", uri=True)
    try:
        if "user_id" not in _table_columns(conn, "sessions"):
            raise SystemExit(
                "shared store has no sessions.user_id column — cannot attribute "
                "history per user. This store predates multi-user scoping; a "
                "per-user split is not possible (consider the single-store "
                "rollback path instead)."
            )
        rep = Report()
        for user_id, count in conn.execute(
            "SELECT user_id, COUNT(*) FROM sessions GROUP BY user_id"
        ).fetchall():
            if user_id is None or user_id == "":
                rep.null_user_sessions += count
            else:
                rep.users[user_id] = count
        return rep
    finally:
        conn.close()


def copy_user_store(shared_db: Path, profile_db: Path, user_id: str) -> CopyStats:
    """Copy one user's sessions + messages from the shared store into their
    profile store. Idempotent and atomic. Returns counts actually copied.

    The profile store schema is created (at the current SCHEMA_VERSION) via
    SessionDB if it does not yet exist, then the copy runs over a raw connection
    with foreign keys OFF so we can move rows in any order and break cross-user
    parent links without tripping FK enforcement.
    """
    from hermes_state import SessionDB

    profile_db.parent.mkdir(parents=True, exist_ok=True)
    # Ensure the destination schema exists at the current version.
    SessionDB(db_path=profile_db).close()

    # uri=True so the read-only file: ATTACH below takes no write lock on the
    # live shared store.
    conn = sqlite3.connect(str(profile_db), isolation_level=None, uri=True)
    stats = CopyStats()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ATTACH DATABASE ? AS src", (f"file:{shared_db}?mode=ro",))

    try:
        # Column intersection makes the copy resilient to schema drift between
        # an older shared store and the current profile schema.
        src_sess_cols = set(_table_columns(conn, "sessions", "src"))
        src_msg_cols = set(_table_columns(conn, "messages", "src"))
        sess_cols = [c for c in _table_columns(conn, "sessions") if c in src_sess_cols]
        msg_cols = [
            c for c in _table_columns(conn, "messages")
            if c in src_msg_cols and c != "id"
        ]
        sess_list = ", ".join(sess_cols)
        msg_list = ", ".join(msg_cols)

        conn.execute("BEGIN IMMEDIATE")

        # Which of this user's shared sessions are not yet in the profile?
        existing = {
            r[0] for r in conn.execute("SELECT id FROM sessions").fetchall()
        }
        user_sessions = [
            r[0] for r in conn.execute(
                "SELECT id FROM src.sessions WHERE user_id = ?", (user_id,)
            ).fetchall()
        ]
        new_sessions = [s for s in user_sessions if s not in existing]

        if new_sessions:
            placeholders = ", ".join("?" for _ in new_sessions)
            conn.execute(
                f"INSERT INTO sessions ({sess_list}) "
                f"SELECT {sess_list} FROM src.sessions "
                f"WHERE id IN ({placeholders})",
                new_sessions,
            )
            stats.sessions_copied = len(new_sessions)

            # Copy messages only for the freshly-inserted sessions (idempotent:
            # a re-run finds those sessions already present and skips them).
            cur = conn.execute(
                f"INSERT INTO messages ({msg_list}) "
                f"SELECT {msg_list} FROM src.messages "
                f"WHERE session_id IN ({placeholders})",
                new_sessions,
            )
            stats.messages_copied = max(cur.rowcount, 0)

            # Break parent links that point at sessions not present in this
            # profile (e.g. a parent owned by a different user). FK is off so
            # this is purely a data-cleanliness step.
            conn.execute(
                "UPDATE sessions SET parent_session_id = NULL "
                "WHERE parent_session_id IS NOT NULL "
                "AND parent_session_id NOT IN (SELECT id FROM sessions)"
            )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        try:
            conn.execute("DETACH DATABASE src")
        except sqlite3.OperationalError:
            pass
        conn.close()
    return stats


def _profiles_root(hermes_home: Path) -> Path:
    return hermes_home / "profiles"


def migrate(hermes_home: Path, *, apply: bool, only_user: str | None = None) -> int:
    shared_db = hermes_home / "state.db"
    if not shared_db.exists():
        print(f"no shared store at {shared_db} — nothing to migrate")
        return 0

    rep = report(shared_db)
    targets = (
        {only_user: rep.users.get(only_user, 0)} if only_user else dict(rep.users)
    )

    print(f"shared store: {shared_db}")
    print(f"users with attributable history: {len(rep.users)}")
    if rep.null_user_sessions:
        print(
            f"  ! {rep.null_user_sessions} session(s) have NULL user_id "
            f"(legacy / pre-scoping) — NOT migrated, left in shared store"
        )
    print(f"mode: {'APPLY' if apply else 'DRY RUN (no changes)'}\n")

    total_sessions = total_messages = 0
    for user_id, shared_count in targets.items():
        profile_db = _profiles_root(hermes_home) / f"{PROFILE_PREFIX}{user_id}" / "state.db"
        if not apply:
            print(f"  would migrate user {user_id}: up to {shared_count} session(s) -> {profile_db}")
            continue
        # Provision the profile (config/SOUL/skills) the same way the router
        # would on first connect, so the migrated profile is fully valid.
        _provision_profile(_profiles_root(hermes_home) / f"{PROFILE_PREFIX}{user_id}")
        stats = copy_user_store(shared_db, profile_db, user_id)
        total_sessions += stats.sessions_copied
        total_messages += stats.messages_copied
        print(
            f"  user {user_id}: copied {stats.sessions_copied} session(s), "
            f"{stats.messages_copied} message(s) -> {profile_db}"
        )

    if apply:
        print(f"\ndone: {total_sessions} session(s), {total_messages} message(s) migrated")
    return 0


def _provision_profile(profile_dir: Path) -> None:
    """Best-effort profile provisioning via the router's own helper, so the
    migrated profile carries the standard config/SOUL/skills. Falls back to a
    bare mkdir if the router module's heavier deps aren't importable."""
    try:
        from tui_gateway.profile_router import provision_profile

        provision_profile(profile_dir)
    except Exception as exc:  # noqa: BLE001 - provisioning is best-effort
        profile_dir.mkdir(parents=True, exist_ok=True)
        print(f"  (note: standard provisioning skipped for {profile_dir.name}: {exc})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hermes-home",
        type=Path,
        default=Path("/data/hermes"),
        help="gateway HERMES_HOME (contains the shared state.db and profiles/)",
    )
    parser.add_argument("--apply", action="store_true", help="perform the migration (default: dry run)")
    parser.add_argument("--user", dest="only_user", default=None, help="migrate a single user_id")
    args = parser.parse_args(argv)
    return migrate(args.hermes_home, apply=args.apply, only_user=args.only_user)


if __name__ == "__main__":
    raise SystemExit(main())
