"""Coverage for the Wheelbase/upstream hermes_state.py merge (schema v26 policy).

Wheelbase's v26 policy dropped the UNIQUE title index and
added a plain lookup index; upstream's v26 added title provenance
(``title_source``), hidden sessions, ``git_metadata_generation``, and two new
tables (``gateway_hygiene_state``, ``session_turn_leases``). The merge keeps
upstream's provenance/CAS title-write implementation and lifecycle handling,
restores Wheelbase's non-unique-title and per-user-list policies, and adds a
``session_count(include_hidden=...)`` parameter to match
``list_sessions_rich``.

This module only covers gaps not already exercised by ``tests/test_hermes_state.py``
(title uniqueness/migration, compression-ancestor titles, context-manager
close semantics) or ``tests/test_wheelbase_multiuser.py`` (NULL user_id
exclusion). See the merge plan, section 4.2, for the full policy.
"""

import sqlite3

from unittest.mock import patch

import pytest

from hermes_state import SessionDB
from hermes_state_common import SCHEMA_VERSION


@pytest.fixture
def db(tmp_path):
    database = SessionDB(tmp_path / "state.db")
    try:
        yield database
    finally:
        database.close()


class TestWheelbaseV26Reconciliation:
    """Reopening a real pre-merge Wheelbase-v26 store must reconcile the
    upstream additions even when its stamped version is already 26.
    """

    def test_reopening_a_real_wheelbase_v26_store_reconciles_upstream_additions(
        self, tmp_path
    ):
        db_path = tmp_path / "wheelbase_v26.db"

        # Build a real store with the current (merged) code, then strip it
        # back down to what Wheelbase's OWN v26 looked like immediately
        # before this merge: no title_source/hidden/git_metadata_generation
        # columns, no gateway_hygiene_state/session_turn_leases tables. Two
        # sessions share a title -- legal under Wheelbase v26 (no unique
        # index) even though it predates upstream's provenance column.
        seed = SessionDB(db_path=db_path)
        seed.create_session("older", "cli")
        seed.set_session_title("older", "Dealership Assistance Inquiry")
        seed.create_session("newer", "cli")
        seed.set_session_title("newer", "Dealership Assistance Inquiry")
        seed.close()

        with sqlite3.connect(db_path) as conn:
            version = conn.execute(
                "SELECT version FROM schema_version"
            ).fetchone()[0]
            assert version == SCHEMA_VERSION
            conn.execute("UPDATE schema_version SET version = 26")
            for column in ("title_source", "hidden", "git_metadata_generation"):
                conn.execute(f"ALTER TABLE sessions DROP COLUMN {column}")
            for table in ("gateway_hygiene_state", "session_turn_leases"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.commit()
            # The simulated pre-merge store is stamped v26. Reconciliation of
            # the missing columns/tables must not depend on a later version gate.

        reopened = SessionDB(db_path=db_path)
        try:
            conn = reopened._conn
            assert conn is not None

            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            assert {"title_source", "hidden", "git_metadata_generation"} <= columns

            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            assert {"gateway_hygiene_state", "session_turn_leases"} <= tables

            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' "
                    "AND tbl_name = 'sessions'"
                ).fetchall()
            }
            assert "idx_sessions_title_unique" not in indexes
            assert "idx_sessions_title" in indexes

            titles = {
                row["id"]: row["title"]
                for row in conn.execute("SELECT id, title FROM sessions").fetchall()
            }
            assert titles == {
                "older": "Dealership Assistance Inquiry",
                "newer": "Dealership Assistance Inquiry",
            }

            assert (
                conn.execute("SELECT version FROM schema_version").fetchone()[0]
                == SCHEMA_VERSION
            )

            # Both rows must still be usable through the normal API.
            assert reopened.get_session_title("older") == "Dealership Assistance Inquiry"
            assert reopened.get_session_title("newer") == "Dealership Assistance Inquiry"
        finally:
            reopened.close()


class TestTitleCasRace:
    """The exact-value compare-and-swap protects against a stale read, not
    just the provenance-rank check.
    """

    def test_concurrent_manual_rename_beats_a_stale_automatic_write(self, tmp_path):
        """A manual rename landing between an automatic write's read and its
        CAS UPDATE must survive, even though the automatic write's rank check
        (based on data read before the race) judged the write allowable.
        """
        db_path = tmp_path / "state.db"
        db = SessionDB(db_path=db_path)
        db.create_session("sess-1", "cli")
        assert db.set_auto_title("sess-1", "derived title", source="derived") is True

        other = SessionDB(db_path=db_path)
        real_rank = SessionDB._title_rank.__func__
        fired = []

        def racing_rank(cls, source):
            if not fired:
                fired.append(True)
                # A manual rename commits here -- after this automatic
                # write's SELECT already ran (it read "derived", rank 0,
                # which is < the incoming "llm" rank 1, so the rank check
                # alone would allow the write to proceed).
                assert other.set_session_title("sess-1", "User Rename") is True
            return real_rank(cls, source)

        try:
            with patch.object(SessionDB, "_title_rank", classmethod(racing_rank)):
                # The CAS predicate binds the pre-race (title, title_source)
                # values, so the UPDATE affects zero rows and the write is
                # correctly reported as not applied.
                assert (
                    db.set_auto_title("sess-1", "llm title", source="llm") is False
                )

            assert db.get_session_title("sess-1") == "User Rename"
            assert db.get_session_title_source("sess-1") == "user"
        finally:
            db.close()
            other.close()


class TestGetSessionByTitleDeterministicOrder:
    """Duplicate-title lookup: exact title, newest started_at, then newest rowid."""

    def test_rowid_breaks_a_started_at_tie(self, db):
        db.create_session("s1", "cli")
        db.create_session("s2", "cli")  # inserted after s1 -> higher rowid
        db.set_session_title("s1", "same title")
        db.set_session_title("s2", "same title")
        # Force an exact started_at tie so the ORDER BY falls through to the
        # rowid tiebreak.
        db._conn.execute(
            "UPDATE sessions SET started_at = 1000.0 WHERE id IN ('s1', 's2')"
        )
        db._conn.commit()

        winner = db.get_session_by_title("same title")
        assert winner["id"] == "s2"


class TestListAndCountScopeParity:
    """``list_sessions_rich`` and ``session_count`` must agree on scope so a
    caller pairing a page with a total gets a ``total``/``has_more`` that
    matches the actual rows returned for that (user_id, include_hidden) scope.
    """

    def _seed(self, db):
        db.create_session("a-visible-1", "cli", user_id="user-a")
        db.create_session("a-visible-2", "cli", user_id="user-a")
        db.create_session("a-hidden", "cli", user_id="user-a")
        db.set_session_hidden("a-hidden", True)
        db.create_session("b-visible", "cli", user_id="user-b")
        db.create_session("legacy-null-user", "cli")  # NULL user_id

    def test_default_scope_excludes_hidden_and_other_users(self, db):
        self._seed(db)

        rows = db.list_sessions_rich(user_id="user-a")
        total = db.session_count(user_id="user-a")

        assert {r["id"] for r in rows} == {"a-visible-1", "a-visible-2"}
        assert total == len(rows) == 2

    def test_include_hidden_true_matches_between_list_and_count(self, db):
        self._seed(db)

        rows = db.list_sessions_rich(user_id="user-a", include_hidden=True)
        total = db.session_count(user_id="user-a", include_hidden=True)

        assert {r["id"] for r in rows} == {
            "a-visible-1",
            "a-visible-2",
            "a-hidden",
        }
        assert total == len(rows) == 3

    def test_scoping_by_user_excludes_legacy_null_owner_rows(self, db):
        self._seed(db)

        rows_a = db.list_sessions_rich(user_id="user-a", include_hidden=True)
        rows_b = db.list_sessions_rich(user_id="user-b", include_hidden=True)
        count_a = db.session_count(user_id="user-a", include_hidden=True)
        count_b = db.session_count(user_id="user-b", include_hidden=True)

        assert "legacy-null-user" not in {r["id"] for r in rows_a}
        assert "legacy-null-user" not in {r["id"] for r in rows_b}
        assert count_a == len(rows_a) == 3
        assert count_b == len(rows_b) == 1

    def test_session_count_include_hidden_default_is_false(self, db):
        self._seed(db)

        # Backward compatibility: existing callers that never pass
        # include_hidden must keep getting the pre-hidden-feature behavior of
        # "matches the default (hidden-excluded) list", not a raw row count.
        assert db.session_count(user_id="user-a") == 2
