"""Opaque backward-paging cursors for the session-messages HTTP routes.

Two independent servers expose ``GET /api/sessions/{session_id}/messages``:
the FastAPI dashboard router (:mod:`hermes_cli.web_routers.sessions`, which is
what the Wheelbase desktop actually calls) and the aiohttp gateway API
(:mod:`gateway.platforms.api_server`). Both page backwards through
``messages.id`` — the AUTOINCREMENT insertion-order column ``SessionDB``
already orders on, deliberately in preference to ``timestamp`` (see c03acca50
for the WSL2 clock-regression rationale) — so both need the identical wire
format. It lives here, in a dependency-free root module next to
:mod:`hermes_time`, because the dashboard router must not import the gateway
and the gateway must not import the dashboard.

The cursor is deliberately OPAQUE. A bare row id on the wire invites clients to
do arithmetic on it ("give me ``id - 50``"), which is silently wrong the moment
ids stop being contiguous — and they already aren't: ``rewind_to_message``
soft-deletes rows and ``archive_and_compact`` re-sequences the concurrent tail
onto fresh ids. Base64url of ``v1:{id}`` leaves a client nothing to compute
with, stays hand-debuggable (``base64 -d``), and carries an explicit version so
a future format change is a decode failure rather than a silent misread.

Encoding is total for any non-negative id; decoding is strict and rejects
anything that is not exactly ``base64url("v1:<digits>")``.
"""

from __future__ import annotations

import base64
import re

# Bumping this is a breaking wire change: clients key their capability probe
# on the ``cursor_version`` field both routes emit, so a v2 cursor must arrive
# with a v2 marker (and decode() below must keep rejecting v1-only garbage).
MESSAGE_CURSOR_VERSION = 1

_CURSOR_PREFIX = f"v{MESSAGE_CURSOR_VERSION}:"

# Anchored, no leading zeros, capped at 19 digits (SQLite rowids are signed
# 64-bit, so 19 digits is the widest legal id). Keeping the grammar this tight
# means a client that hand-rolls "v1:007" or "v1:1e5" gets a hard 400 instead
# of a page it will misinterpret.
_CURSOR_BODY_RE = re.compile(r"^v1:(0|[1-9][0-9]{0,18})$")

# A well-formed v1 cursor for the widest legal id is 30 characters. The cap is
# a cheap denial-of-service guard: it bounds the base64 decode of an attacker
# supplied query string before any allocation worth measuring.
_MAX_CURSOR_CHARS = 64

# Deliberately constant and content-free. The message is user-visible on both
# routes, and it must read the same whether the cursor was gibberish, a v2
# cursor, a cursor minted against a session the caller cannot see, or a cursor
# whose row has since been rewound away — a differing message would turn the
# error into an existence oracle.
_INVALID_CURSOR_MESSAGE = "Invalid pagination cursor"


class InvalidMessageCursor(ValueError):
    """A ``before`` cursor that is not a well-formed v1 message cursor.

    Subclasses ``ValueError`` so callers that only care that a query parameter
    failed to parse can keep catching ``ValueError``, while the two HTTP
    routes catch this specific type to map it onto their own typed 400.
    """


def encode_message_cursor(message_id: int) -> str:
    """Encode a ``messages.id`` as an opaque, client-safe cursor string.

    Padding is stripped so the cursor is safe to paste into a query string
    without percent-encoding; :func:`decode_message_cursor` restores it.
    """
    mid = int(message_id)
    if mid < 0:
        raise ValueError(f"message id must be non-negative, got {mid!r}")
    raw = f"{_CURSOR_PREFIX}{mid}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_message_cursor(cursor: str) -> int:
    """Decode an opaque cursor back into a ``messages.id``.

    Raises :class:`InvalidMessageCursor` for anything that is not exactly a v1
    cursor: wrong type, empty, over-long, non-base64url, non-ASCII payload,
    wrong version tag, or a body that is not a bare decimal id.

    The returned id is a pure ordering bound — it is NOT validated against any
    session. See the route docstrings for why: a cursor whose row lives in
    another session, or was soft-deleted by a rewind, is indistinguishable from
    one that never existed, and rejecting it would build exactly the existence
    oracle this module's constant error message exists to avoid.
    """
    if not isinstance(cursor, str):
        raise InvalidMessageCursor(_INVALID_CURSOR_MESSAGE)
    token = cursor.strip()
    if not token or len(token) > _MAX_CURSOR_CHARS:
        raise InvalidMessageCursor(_INVALID_CURSOR_MESSAGE)
    # Accept both padded and unpadded input; we emit unpadded.
    padded = token + "=" * (-len(token) % 4)
    try:
        # validate=True (rather than urlsafe_b64decode, which has no such
        # switch) so out-of-alphabet bytes are an error instead of being
        # silently discarded into a plausible-looking payload.
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError):
        raise InvalidMessageCursor(_INVALID_CURSOR_MESSAGE) from None
    try:
        decoded = raw.decode("ascii")
    except UnicodeDecodeError:
        raise InvalidMessageCursor(_INVALID_CURSOR_MESSAGE) from None
    match = _CURSOR_BODY_RE.match(decoded)
    if match is None:
        raise InvalidMessageCursor(_INVALID_CURSOR_MESSAGE)
    return int(match.group(1))
