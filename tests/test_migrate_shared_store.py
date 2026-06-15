"""Tests for the legacy shared-store -> per-profile backfill migration.

See scripts/migrate_shared_store_to_profiles.py for context: the cloud gateway
moved from one shared HERMES_HOME=/data/hermes dashboard to the per-profile
router, stranding pre-cutover history in the shared state.db. The migration
copies each user's sessions+messages into their per-user profile store.
"""
import sqlite3

import pytest

from hermes_state import SessionDB

migrate = pytest.importorskip(
    "scripts.migrate_shared_store_to_profiles",
    reason="migration script must be importable as scripts.migrate_shared_store_to_profiles",
)


def _seed_shared_store(path):
    """Build a realistic legacy shared store: users A and B plus a legacy
    NULL-user session, each with messages."""
    db = SessionDB(db_path=path)
    try:
        db.create_session(session_id="a1", source="tui", user_id="user-a")
        db.create_session(session_id="a2", source="tui", user_id="user-a")
        db.create_session(session_id="b1", source="tui", user_id="user-b")
        db.create_session(session_id="legacy", source="tui")  # user_id NULL
        db.append_message("a1", "user", "hello from A in a1")
        db.append_message("a1", "assistant", "hi A")
        db.append_message("a2", "user", "second A convo")
        db.append_message("b1", "user", "hello from B")
        db.append_message("legacy", "user", "ancient unattributed message")
    finally:
        db.close()


def _msg_contents(db_path, session_id):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT content FROM messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _session_ids(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute("SELECT id FROM sessions").fetchall()}
    finally:
        conn.close()


def test_report_counts_users_and_flags_null(tmp_path):
    old = tmp_path / "state.db"
    _seed_shared_store(old)
    report = migrate.report(old)
    assert report.users == {"user-a": 2, "user-b": 1}
    assert report.null_user_sessions == 1


def test_copy_user_store_isolates_per_user(tmp_path):
    old = tmp_path / "state.db"
    _seed_shared_store(old)

    dst_a = tmp_path / "profiles" / "wb-user-a" / "state.db"
    stats = migrate.copy_user_store(old, dst_a, "user-a")

    assert stats.sessions_copied == 2
    assert stats.messages_copied == 3
    # user A's profile contains ONLY user A's sessions — no cross-user bleed.
    assert _session_ids(dst_a) == {"a1", "a2"}
    assert _msg_contents(dst_a, "a1") == ["hello from A in a1", "hi A"]
    assert _msg_contents(dst_a, "b1") == []  # B never appears in A's store


def test_copy_is_idempotent(tmp_path):
    old = tmp_path / "state.db"
    _seed_shared_store(old)
    dst = tmp_path / "profiles" / "wb-user-a" / "state.db"

    first = migrate.copy_user_store(old, dst, "user-a")
    second = migrate.copy_user_store(old, dst, "user-a")

    assert first.sessions_copied == 2 and first.messages_copied == 3
    assert second.sessions_copied == 0 and second.messages_copied == 0
    # no duplicate messages on re-run
    assert _msg_contents(dst, "a1") == ["hello from A in a1", "hi A"]


def test_copy_preserves_post_cutover_sessions(tmp_path):
    """A session the user created AFTER cutover (already in the profile) must
    survive the backfill."""
    old = tmp_path / "state.db"
    _seed_shared_store(old)
    dst = tmp_path / "profiles" / "wb-user-a" / "state.db"

    # Simulate a post-cutover session already living in the fresh profile.
    pre = SessionDB(db_path=dst)
    try:
        pre.create_session(session_id="post1", source="tui", user_id="user-a")
        pre.append_message("post1", "user", "created after cutover")
    finally:
        pre.close()

    migrate.copy_user_store(old, dst, "user-a")

    assert _session_ids(dst) == {"post1", "a1", "a2"}
    assert _msg_contents(dst, "post1") == ["created after cutover"]
    assert _msg_contents(dst, "a1") == ["hello from A in a1", "hi A"]
