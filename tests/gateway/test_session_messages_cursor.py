"""Backward-cursor contract for the gateway's session-messages route.

``GET /api/sessions/{session_id}/messages?before=<cursor>`` is the aiohttp half
of the contract documented in
``wheelbase-app/docs/plans/2026-08-18-conversation-messages-cursor-contract.md``.
The FastAPI half lives in ``tests/hermes_cli/test_session_messages_cursor.py``
and asserts the same properties against the same
:meth:`SessionDB.get_messages` ``before_id`` primitive.
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_message_cursor import (
    InvalidMessageCursor,
    decode_message_cursor,
    encode_message_cursor,
)
from hermes_state import SessionDB


@pytest.fixture
def session_db(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        yield db
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()


@pytest.fixture
def adapter(session_db):
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    adapter._session_db = session_db
    return adapter


def _create_session_app(adapter: APIServerAdapter) -> web.Application:
    app = web.Application()
    app.router.add_get(
        "/api/sessions/{session_id}/messages", adapter._handle_session_messages
    )
    return app


def _seed(session_db, session_id: str, n: int = 10) -> str:
    sid = session_db.create_session(session_id, "api_server")
    session_db.replace_messages(
        sid, [{"role": "user", "content": f"msg-{i}"} for i in range(n)]
    )
    return sid


# ---------------------------------------------------------------------------
# Cursor codec
# ---------------------------------------------------------------------------


class TestCursorCodec:
    def test_round_trips_and_is_not_a_bare_integer(self):
        cursor = encode_message_cursor(4212)
        assert cursor != "4212"
        assert not cursor.isdigit()
        assert decode_message_cursor(cursor) == 4212

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "4212",                       # bare id — the thing we refuse to accept
            "djE6NDI=extra",              # trailing junk
            "djI6NDI",                    # v2 payload
            "djE6MDA",                    # leading zero body
            "djE6",                       # empty body
            "djE6YWJj",                   # v1:abc
            "!!!!",                       # outside the base64url alphabet
            "a" * 65,                     # over the length cap
        ],
    )
    def test_rejects_anything_that_is_not_a_v1_cursor(self, bad):
        with pytest.raises(InvalidMessageCursor):
            decode_message_cursor(bad)

    def test_rejection_message_is_constant(self):
        messages = set()
        for bad in ("djI6NDI", "!!!!", "djE6YWJj"):
            with pytest.raises(InvalidMessageCursor) as exc:
                decode_message_cursor(bad)
            messages.add(str(exc.value))
        assert messages == {"Invalid pagination cursor"}


# ---------------------------------------------------------------------------
# Route behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_page_is_newest_chronological_and_shape_is_unchanged(
    adapter, session_db
):
    """No ``before`` behaves exactly as it did before the cursor existed; the
    cursor fields are purely additive."""
    sid = _seed(session_db, "cursor-first-page", n=10)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.get(f"/api/sessions/{sid}/messages?limit=4")
        assert resp.status == 200
        payload = await resp.json()

    assert payload["object"] == "list"
    assert payload["session_id"] == sid
    assert payload["pagination"] == {
        "limit": 4,
        "offset": 0,
        "order": "oldest",
        "returned": 4,
    }
    assert [m["content"] for m in payload["data"]] == [
        "msg-0", "msg-1", "msg-2", "msg-3",
    ]

    latest = None
    async with TestClient(TestServer(_create_session_app(adapter))) as cli:
        resp = await cli.get(f"/api/sessions/{sid}/messages?limit=4&order=latest")
        latest = await resp.json()

    assert [m["content"] for m in latest["data"]] == [
        "msg-6", "msg-7", "msg-8", "msg-9",
    ]
    assert latest["cursor_version"] == 1
    assert latest["has_more"] is True
    assert latest["next_cursor"] == encode_message_cursor(latest["data"][0]["id"])
    assert latest["oldest_message_id"] == str(latest["data"][0]["id"])


@pytest.mark.asyncio
async def test_before_returns_strictly_older_rows_exclusive_of_the_cursor(
    adapter, session_db
):
    sid = _seed(session_db, "cursor-walk", n=10)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        page1 = await (await cli.get(f"/api/sessions/{sid}/messages?limit=4")).json()
        assert page1["pagination"]["order"] == "oldest"

        newest = await (
            await cli.get(f"/api/sessions/{sid}/messages?limit=4&order=latest")
        ).json()
        assert [m["content"] for m in newest["data"]] == [
            "msg-6", "msg-7", "msg-8", "msg-9",
        ]

        older = await (
            await cli.get(
                f"/api/sessions/{sid}/messages"
                f"?limit=4&before={newest['next_cursor']}"
            )
        ).json()
        oldest = await (
            await cli.get(
                f"/api/sessions/{sid}/messages"
                f"?limit=4&before={older['next_cursor']}"
            )
        ).json()
        exhausted = await (
            await cli.get(
                f"/api/sessions/{sid}/messages"
                f"?limit=4&before={encode_message_cursor(oldest['data'][0]['id'])}"
            )
        ).json()

    # Chronological ascending within each page, strictly older across pages,
    # and EXCLUSIVE: the cursor row is never repeated.
    assert [m["content"] for m in older["data"]] == [
        "msg-2", "msg-3", "msg-4", "msg-5",
    ]
    assert [m["content"] for m in oldest["data"]] == ["msg-0", "msg-1"]
    assert exhausted["data"] == []

    walked = [m["content"] for m in oldest["data"] + older["data"] + newest["data"]]
    assert walked == [f"msg-{i}" for i in range(10)]

    # ``before`` is inherently newest-anchored, so pagination echoes "latest".
    assert older["pagination"]["order"] == "latest"
    assert older["pagination"]["offset"] == 0
    assert older["pagination"]["returned"] == 4

    assert oldest["has_more"] is False
    assert oldest["next_cursor"] is None
    assert oldest["oldest_message_id"] == str(oldest["data"][0]["id"])

    assert exhausted["has_more"] is False
    assert exhausted["next_cursor"] is None
    assert exhausted["oldest_message_id"] is None


@pytest.mark.asyncio
async def test_concurrent_tail_append_does_not_shift_the_older_page(
    adapter, session_db
):
    """The property OFFSET paging fails and keyset paging fixes.

    Rows appended between two identical older-page reads must not slide the
    window; the same request must return the same rows, byte for byte.
    """
    sid = _seed(session_db, "cursor-concurrent", n=10)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        newest = await (
            await cli.get(f"/api/sessions/{sid}/messages?limit=4&order=latest")
        ).json()
        cursor = newest["next_cursor"]

        first = await (
            await cli.get(f"/api/sessions/{sid}/messages?limit=4&before={cursor}")
        ).json()

        # A live turn lands while the user is scrolling up.
        for i in range(3):
            session_db.append_message(sid, role="user", content=f"late-{i}")

        second = await (
            await cli.get(f"/api/sessions/{sid}/messages?limit=4&before={cursor}")
        ).json()

        # For contrast: the equivalent OFFSET read HAS slid by three rows.
        shifted = await (
            await cli.get(
                f"/api/sessions/{sid}/messages?limit=4&offset=4&order=latest"
            )
        ).json()

    assert first["data"] == second["data"]
    assert [m["content"] for m in second["data"]] == [
        "msg-2", "msg-3", "msg-4", "msg-5",
    ]
    assert second["next_cursor"] == first["next_cursor"]
    assert [m["content"] for m in shifted["data"]] == [
        "msg-5", "msg-6", "msg-7", "msg-8",
    ]


@pytest.mark.asyncio
async def test_malformed_cursor_is_a_typed_400_that_leaks_no_existence(
    adapter, session_db
):
    sid = _seed(session_db, "cursor-bad", n=3)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        real = await cli.get(f"/api/sessions/{sid}/messages?before=not-a-cursor")
        real_body = await real.json()
        # Same malformed cursor against a session that does NOT exist: the
        # response must be indistinguishable, or the 400/404 split becomes an
        # existence oracle.
        ghost = await cli.get(
            "/api/sessions/no-such-session-at-all/messages?before=not-a-cursor"
        )
        ghost_body = await ghost.json()

        v2 = await cli.get(f"/api/sessions/{sid}/messages?before=djI6NDI")
        v2_body = await v2.json()

        bare = await cli.get(f"/api/sessions/{sid}/messages?before=42")

    assert real.status == 400
    assert ghost.status == 400
    assert real_body == ghost_body
    assert real_body["error"]["code"] == "invalid_cursor"
    assert real_body["error"]["message"] == "Invalid pagination cursor"
    assert v2.status == 400 and v2_body == real_body
    # A bare row id is not a cursor.
    assert bare.status == 400


@pytest.mark.asyncio
async def test_foreign_cursor_is_scoped_to_the_requested_session(
    adapter, session_db
):
    """A well-formed cursor minted against another session is a pure ordering
    bound, not an error: 400-ing it would reveal that the other session's row
    exists. The page stays scoped to the requested session either way."""
    a = _seed(session_db, "cursor-scope-a", n=4)
    b = _seed(session_db, "cursor-scope-b", n=4)
    b_rows = session_db.get_messages(b)
    foreign = encode_message_cursor(b_rows[-1]["id"])
    # An id no row anywhere has ever used.
    never_existed = encode_message_cursor(b_rows[-1]["id"] + 10_000)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        with_foreign = await cli.get(
            f"/api/sessions/{a}/messages?limit=10&before={foreign}"
        )
        foreign_body = await with_foreign.json()
        with_unknown = await cli.get(
            f"/api/sessions/{a}/messages?limit=10&before={never_existed}"
        )
        unknown_body = await with_unknown.json()

    assert with_foreign.status == 200 and with_unknown.status == 200
    # Identical status AND identical payload — nothing about session B leaks.
    assert foreign_body == unknown_body
    assert [m["content"] for m in foreign_body["data"]] == [
        "msg-0", "msg-1", "msg-2", "msg-3",
    ]
    assert all(m["session_id"] == a for m in foreign_body["data"])


@pytest.mark.asyncio
async def test_has_more_always_carries_a_usable_cursor(adapter, session_db):
    """``has_more: true`` with a null ``next_cursor`` is the one combination the
    client treats as a broken server. Walk the whole transcript one row at a
    time and assert it never appears."""
    sid = _seed(session_db, "cursor-invariant", n=17)

    seen = []
    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        page = await (
            await cli.get(f"/api/sessions/{sid}/messages?limit=3&order=latest")
        ).json()
        for _ in range(50):
            assert page["cursor_version"] == 1
            if page["has_more"]:
                assert page["next_cursor"] is not None
                assert page["oldest_message_id"] is not None
            seen = [m["content"] for m in page["data"]] + seen
            if not page["has_more"]:
                break
            page = await (
                await cli.get(
                    f"/api/sessions/{sid}/messages"
                    f"?limit=3&before={page['next_cursor']}"
                )
            ).json()
        else:  # pragma: no cover - only reached if paging never terminates
            pytest.fail("cursor paging did not terminate")

    assert seen == [f"msg-{i}" for i in range(17)]


@pytest.mark.asyncio
async def test_before_rejects_offset(adapter, session_db):
    sid = _seed(session_db, "cursor-offset", n=4)
    cursor = encode_message_cursor(10**6)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.get(
            f"/api/sessions/{sid}/messages?limit=2&offset=1&before={cursor}"
        )
        body = await resp.json()

    assert resp.status == 400
    assert body["error"]["code"] == "invalid_pagination"


@pytest.mark.asyncio
async def test_cursor_pages_deduped_compacted_display_history(adapter, session_db):
    """``include_compacted=true`` + ``before`` returns correct deduped older
    pages rather than a typed refusal (see the DB-layer docstring for why the
    combination is supported here but not for ``after_id``)."""
    sid = session_db.create_session("cursor-compacted", "api_server")
    session_db.replace_messages(
        sid, [{"role": "user", "content": f"old-{i}"} for i in range(4)]
    )
    session_db.archive_and_compact(
        sid,
        [
            {"role": "assistant", "content": "summary"},
            {"role": "user", "content": "live-0"},
            {"role": "assistant", "content": "live-1"},
        ],
    )

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        newest = await (
            await cli.get(
                f"/api/sessions/{sid}/messages"
                "?limit=3&order=latest&include_compacted=true"
            )
        ).json()
        older = await (
            await cli.get(
                f"/api/sessions/{sid}/messages"
                f"?limit=3&include_compacted=true&before={newest['next_cursor']}"
            )
        ).json()
        oldest = await (
            await cli.get(
                f"/api/sessions/{sid}/messages"
                f"?limit=3&include_compacted=true&before={older['next_cursor']}"
            )
        ).json()

        # The default (active-only) view is untouched by any of this.
        default_view = await (
            await cli.get(f"/api/sessions/{sid}/messages")
        ).json()

    assert [m["content"] for m in newest["data"]] == [
        "summary", "live-0", "live-1",
    ]
    assert [m["content"] for m in older["data"]] == ["old-1", "old-2", "old-3"]
    assert [m["content"] for m in oldest["data"]] == ["old-0"]
    assert oldest["has_more"] is False and oldest["next_cursor"] is None
    assert [m["content"] for m in default_view["data"]] == [
        "summary", "live-0", "live-1",
    ]
