"""Backward-cursor contract for the dashboard's session-messages route.

``GET /api/sessions/{session_id}/messages?before=<cursor>`` as served by
:mod:`hermes_cli.web_routers.sessions` — the implementation the Wheelbase
desktop actually calls (``getHermesSessionMessages`` reads ``result.messages``).
The aiohttp gateway half is covered by
``tests/gateway/test_session_messages_cursor.py``; both are asserted against
the same :meth:`SessionDB.get_messages` ``before_id`` primitive so the two
servers cannot drift apart silently.
"""

import pytest

from hermes_message_cursor import encode_message_cursor


class TestSessionMessagesCursor:
    @pytest.fixture(autouse=True)
    def _setup_test_client(self, monkeypatch, _isolate_hermes_home):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/starlette not installed")

        import hermes_state
        from hermes_constants import get_hermes_home
        from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

        monkeypatch.setattr(
            hermes_state, "DEFAULT_DB_PATH", get_hermes_home() / "state.db"
        )
        self.client = TestClient(app)
        self.client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN

    @staticmethod
    def _seed(session_id, n=10, prefix="msg"):
        from hermes_state import SessionDB

        db = SessionDB()
        try:
            db.create_session(session_id=session_id, source="cli")
            db.append_messages_batch(
                session_id,
                [{"role": "user", "content": f"{prefix}-{i}"} for i in range(n)],
            )
        finally:
            db.close()

    def _get(self, session_id, query=""):
        suffix = f"?{query}" if query else ""
        return self.client.get(f"/api/sessions/{session_id}/messages{suffix}")

    # -- 1. first page --------------------------------------------------

    def test_first_page_is_newest_chronological_and_shape_is_unchanged(self):
        """The no-``before`` default is byte-identical to what it was before
        the cursor contract; the new fields sit alongside it."""
        self._seed("cur-first", n=10)

        payload = self._get("cur-first").json()

        # Pre-existing contract, untouched.
        assert payload["session_id"] == "cur-first"
        assert payload["pagination"] == {
            "limit": 500,
            "offset": 0,
            "order": "latest",
            "returned": 10,
        }
        assert [m["content"] for m in payload["messages"]] == [
            f"msg-{i}" for i in range(10)
        ]
        # Additive contract.
        assert payload["cursor_version"] == 1
        assert payload["has_more"] is False  # whole transcript fits in one page
        assert payload["next_cursor"] is None
        assert payload["oldest_message_id"] == str(payload["messages"][0]["id"])

        bounded = self._get("cur-first", "limit=4&order=latest").json()
        assert [m["content"] for m in bounded["messages"]] == [
            "msg-6", "msg-7", "msg-8", "msg-9",
        ]
        assert bounded["pagination"]["returned"] == 4
        assert bounded["has_more"] is True
        assert bounded["next_cursor"] == encode_message_cursor(
            bounded["messages"][0]["id"]
        )

    # -- 2. strictly-older, exclusive overlap ---------------------------

    def test_before_returns_strictly_older_rows_exclusive_of_the_cursor(self):
        self._seed("cur-walk", n=10)

        newest = self._get("cur-walk", "limit=4&order=latest").json()
        older = self._get("cur-walk", f"limit=4&before={newest['next_cursor']}").json()
        oldest = self._get("cur-walk", f"limit=4&before={older['next_cursor']}").json()
        beyond = self._get(
            "cur-walk",
            f"limit=4&before={encode_message_cursor(oldest['messages'][0]['id'])}",
        ).json()

        assert [m["content"] for m in newest["messages"]] == [
            "msg-6", "msg-7", "msg-8", "msg-9",
        ]
        assert [m["content"] for m in older["messages"]] == [
            "msg-2", "msg-3", "msg-4", "msg-5",
        ]
        assert [m["content"] for m in oldest["messages"]] == ["msg-0", "msg-1"]
        assert beyond["messages"] == []

        # EXCLUSIVE overlap: concatenating the walk reproduces the transcript
        # exactly once, with no repeated boundary row.
        walked = [
            m["content"]
            for m in oldest["messages"] + older["messages"] + newest["messages"]
        ]
        assert walked == [f"msg-{i}" for i in range(10)]

        # ``before`` is newest-anchored, so pagination reports "latest" even
        # though an explicit limit was supplied.
        assert older["pagination"] == {
            "limit": 4,
            "offset": 0,
            "order": "latest",
            "returned": 4,
        }
        assert oldest["has_more"] is False and oldest["next_cursor"] is None
        assert beyond["oldest_message_id"] is None

    # -- 3. concurrent tail append --------------------------------------

    def test_concurrent_tail_append_does_not_shift_the_older_page(self):
        """Insert rows between two identical older-page reads: the older page
        must come back unchanged. The equivalent OFFSET read does not."""
        from hermes_state import SessionDB

        self._seed("cur-concurrent", n=10)
        newest = self._get("cur-concurrent", "limit=4&order=latest").json()
        cursor = newest["next_cursor"]

        first = self._get("cur-concurrent", f"limit=4&before={cursor}").json()

        db = SessionDB()
        try:
            db.append_messages_batch(
                "cur-concurrent",
                [{"role": "user", "content": f"late-{i}"} for i in range(3)],
            )
        finally:
            db.close()

        second = self._get("cur-concurrent", f"limit=4&before={cursor}").json()
        shifted = self._get(
            "cur-concurrent", "limit=4&offset=4&order=latest"
        ).json()

        assert first["messages"] == second["messages"]
        assert [m["content"] for m in second["messages"]] == [
            "msg-2", "msg-3", "msg-4", "msg-5",
        ]
        assert second["next_cursor"] == first["next_cursor"]
        assert [m["content"] for m in shifted["messages"]] == [
            "msg-5", "msg-6", "msg-7", "msg-8",
        ]

    # -- 4. typed errors, no existence leak -----------------------------

    @pytest.mark.parametrize(
        "bad", ["not-a-cursor", "42", "djI6NDI", "djE6MDA", "!!!!", "a" * 65]
    )
    def test_malformed_cursor_is_a_typed_400(self, bad):
        self._seed("cur-bad", n=3)
        resp = self._get("cur-bad", f"before={bad}")
        assert resp.status_code == 400
        assert resp.json() == {"detail": "Invalid pagination cursor"}

    def test_malformed_cursor_does_not_reveal_session_existence(self):
        """The cursor is decoded before the session is resolved, so a bad
        cursor answers identically for a real and an imaginary session."""
        self._seed("cur-exists", n=3)
        real = self._get("cur-exists", "before=not-a-cursor")
        ghost = self._get("cur-no-such-session-anywhere", "before=not-a-cursor")

        assert real.status_code == ghost.status_code == 400
        assert real.json() == ghost.json()

    def test_foreign_cursor_is_a_scoped_ordering_bound_not_an_error(self):
        """A valid cursor minted in another session is indistinguishable from
        one whose row never existed — deliberately, so it cannot be used as an
        existence oracle. Both return this session's page."""
        self._seed("cur-scope-a", n=4, prefix="a")
        self._seed("cur-scope-b", n=4, prefix="b")

        from hermes_state import SessionDB

        db = SessionDB()
        try:
            b_rows = db.get_messages("cur-scope-b")
        finally:
            db.close()

        foreign = encode_message_cursor(b_rows[-1]["id"])
        never_existed = encode_message_cursor(b_rows[-1]["id"] + 10_000)

        with_foreign = self._get("cur-scope-a", f"limit=10&before={foreign}")
        with_unknown = self._get("cur-scope-a", f"limit=10&before={never_existed}")

        assert with_foreign.status_code == with_unknown.status_code == 200
        assert with_foreign.json() == with_unknown.json()
        assert [m["content"] for m in with_foreign.json()["messages"]] == [
            "a-0", "a-1", "a-2", "a-3",
        ]
        assert all(
            m["session_id"] == "cur-scope-a" for m in with_foreign.json()["messages"]
        )

    def test_before_rejects_offset(self):
        self._seed("cur-offset", n=4)
        resp = self._get(
            "cur-offset", f"limit=2&offset=1&before={encode_message_cursor(10**6)}"
        )
        assert resp.status_code == 400
        assert resp.json() == {"detail": "before cannot be combined with offset"}

    # -- 5. compaction ---------------------------------------------------

    def test_cursor_pages_deduped_compacted_display_history(self):
        """``include_compacted=true`` + ``before`` is SUPPORTED (unlike
        ``after_id``) and returns correct deduped older pages."""
        from hermes_state import SessionDB

        db = SessionDB()
        try:
            db.create_session(session_id="cur-compacted", source="cli")
            db.append_messages_batch(
                "cur-compacted",
                [{"role": "user", "content": f"old-{i}"} for i in range(4)],
            )
            db.archive_and_compact(
                "cur-compacted",
                [
                    {"role": "assistant", "content": "summary"},
                    {"role": "user", "content": "live-0"},
                    {"role": "assistant", "content": "live-1"},
                ],
            )
        finally:
            db.close()

        newest = self._get(
            "cur-compacted", "limit=3&order=latest&include_compacted=true"
        ).json()
        older = self._get(
            "cur-compacted",
            f"limit=3&include_compacted=true&before={newest['next_cursor']}",
        ).json()
        oldest = self._get(
            "cur-compacted",
            f"limit=3&include_compacted=true&before={older['next_cursor']}",
        ).json()

        assert [m["content"] for m in newest["messages"]] == [
            "summary", "live-0", "live-1",
        ]
        assert [m["content"] for m in older["messages"]] == [
            "old-1", "old-2", "old-3",
        ]
        assert [m["content"] for m in oldest["messages"]] == ["old-0"]
        assert oldest["has_more"] is False and oldest["next_cursor"] is None

        # The active-only default view is unaffected.
        default_view = self._get("cur-compacted").json()
        assert [m["content"] for m in default_view["messages"]] == [
            "summary", "live-0", "live-1",
        ]

    def test_cursor_survives_a_compaction_that_re_sequenced_the_tail(self):
        """``archive_and_compact(watermark=...)`` re-inserts the concurrent tail
        under fresh ids. A cursor handed out BEFORE that compaction must still
        page correctly instead of skipping the carried-forward turns."""
        from hermes_state import SessionDB

        db = SessionDB()
        try:
            db.create_session(session_id="cur-reseq", source="cli")
            db.append_messages_batch(
                "cur-reseq",
                [{"role": "user", "content": f"old-{i}"} for i in range(3)],
            )
            watermark = db.get_active_message_watermark("cur-reseq")
            db.append_messages_batch(
                "cur-reseq",
                [{"role": "user", "content": f"tail-{i}"} for i in range(3)],
            )
        finally:
            db.close()

        # Cursor taken from the live transcript, BEFORE the compaction.
        page = self._get("cur-reseq", "limit=1&order=latest&include_compacted=true")
        stale_cursor = page.json()["next_cursor"]
        assert stale_cursor is not None

        db = SessionDB()
        try:
            db.archive_and_compact(
                "cur-reseq",
                [{"role": "assistant", "content": "summary"}],
                watermark=watermark,
            )
        finally:
            db.close()

        older = self._get(
            "cur-reseq", f"limit=10&include_compacted=true&before={stale_cursor}"
        ).json()
        contents = [m["content"] for m in older["messages"]]

        # tail-0/tail-1 were carried forward onto fresh, HIGHER ids; a naive
        # ``id < cursor`` slice would have dropped them silently.
        assert contents == [
            "old-0", "old-1", "old-2", "summary", "tail-0", "tail-1",
        ]

    # -- 6. has_more/next_cursor invariant ------------------------------

    def test_has_more_always_carries_a_usable_cursor(self):
        """``has_more: true`` + ``next_cursor: null`` is the one combination the
        client treats as a server contract violation. Walk the transcript in
        3-row pages and assert it never occurs."""
        self._seed("cur-invariant", n=17)

        page = self._get("cur-invariant", "limit=3&order=latest").json()
        seen = []
        for _ in range(50):
            assert page["cursor_version"] == 1
            if page["has_more"]:
                assert page["next_cursor"] is not None
                assert page["oldest_message_id"] is not None
            seen = [m["content"] for m in page["messages"]] + seen
            if not page["has_more"]:
                break
            page = self._get(
                "cur-invariant", f"limit=3&before={page['next_cursor']}"
            ).json()
        else:  # pragma: no cover - only reached if paging never terminates
            pytest.fail("cursor paging did not terminate")

        assert seen == [f"msg-{i}" for i in range(17)]

    def test_empty_page_never_claims_more(self):
        """A zero-row page has no anchor to hand back, so it must report
        ``has_more: false`` rather than an unreachable cursor."""
        self._seed("cur-empty", n=2)
        rows = self._get("cur-empty").json()["messages"]
        empty = self._get(
            "cur-empty", f"limit=5&before={encode_message_cursor(rows[0]['id'])}"
        ).json()

        assert empty["messages"] == []
        assert empty["has_more"] is False
        assert empty["next_cursor"] is None
        assert empty["oldest_message_id"] is None

        # limit=0 is the degenerate case of the same rule.
        zero = self._get("cur-empty", "limit=0").json()
        assert zero["messages"] == []
        assert zero["has_more"] is False
        assert zero["next_cursor"] is None
