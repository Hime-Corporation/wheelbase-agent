"""Real WebSocket transport for the desktop exec relay (spec §5.3 B2).

Connects the gateway side of the relay to the Go backend's ``ExecHub``
(``wheelbase-backend/internal/handlers/agent_exec.go``, ``GatewayConnect``).
``relay_url`` is the FULL ``ws://<internal-addr>/internal/agent/exec/<userID>/ws
?token=<cap>`` URL the backend already built and injected as the
``X-Wheelbase-Shell-Relay-Url`` header (see ``agent_chat_handler.go``'s
``internalExecURL``) — nothing else to construct, no extra auth headers, the
capability token is already embedded in the query string.

The hub pumps frames verbatim in both directions and never parses them —
multiplexing by ``request_id`` is entirely this class's job, matching
``DesktopRelayEnvironment``'s request/response pattern: one ``send()`` per
request, followed by one or more ``recv(request_id)`` calls until a terminal
frame type (``exit``/``result``/``error``) arrives. A background reader thread
drains the socket continuously and buckets frames by ``request_id`` so
multiple in-flight requests on one connection (e.g. patch/search_files issuing
several internal exec calls) never lose or misdeliver a frame.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Mapping, Optional

from .transport import ExecTransport, PreDispatchError

logger = logging.getLogger(__name__)

# Matches the backend's DesktopConnect dial: ExecHub.GatewayConnect has no
# separate handshake step (auth is the ?token= already in relay_url), so this
# only bounds the TCP/TLS + WS upgrade round-trip itself.
_CONNECT_TIMEOUT_S = 10.0


class WebsocketExecTransport(ExecTransport):
    """One instance per relayed tool call. Not safe to reuse across calls
    (mirrors the desktop's "one gateway conn at a time" pairing: a later
    ``GatewayConnect`` from the SAME user replaces the previous one)."""

    def __init__(self, relay_url: str, identity: Optional[dict] = None):
        try:
            from websockets.sync.client import connect as ws_connect
        except ImportError as exc:  # pragma: no cover - websockets is a required install path
            raise PreDispatchError(f"websockets library unavailable: {exc}") from exc

        self._relay_url = relay_url
        self._identity = identity or {}
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._pending: dict[str, list[dict]] = {}
        self._recv_error: Optional[BaseException] = None
        self._closed = False

        try:
            self._ws = ws_connect(
                relay_url,
                open_timeout=_CONNECT_TIMEOUT_S,
                max_size=64 << 20,  # matches execMaxFrame on the Go side
            )
        except Exception as exc:
            # Upgrade rejected (desktop offline -> 503, bad/expired cap token
            # -> 401/403) or a plain connect failure — nothing has executed
            # anywhere yet, so this is always a pre-dispatch failure.
            raise PreDispatchError(
                f"desktop exec relay connect failed: {exc}"
            ) from exc

        self._reader = threading.Thread(
            target=self._read_loop, name="wb-desktop-exec-ws-reader", daemon=True,
        )
        self._reader.start()

    def _read_loop(self) -> None:
        while True:
            try:
                raw = self._ws.recv()
            except Exception as exc:
                with self._cond:
                    self._recv_error = exc
                    self._cond.notify_all()
                return
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                logger.debug("desktop exec relay: dropping non-JSON frame")
                continue
            if not isinstance(frame, dict):
                continue
            request_id = str(frame.get("request_id") or "")
            with self._cond:
                self._pending.setdefault(request_id, []).append(frame)
                self._cond.notify_all()

    def send(self, frame: Mapping[str, Any]) -> None:
        if self._closed:
            raise PreDispatchError("desktop exec relay is closed")
        try:
            with self._lock:
                self._ws.send(json.dumps(frame, ensure_ascii=False))
        except Exception as exc:
            # The only network op a fresh request performs before anything
            # can have executed on the desktop — always pre-dispatch.
            raise PreDispatchError(f"desktop exec relay send failed: {exc}") from exc

    def recv(self, request_id: str, timeout: Optional[float] = None) -> dict:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while True:
                bucket = self._pending.get(request_id)
                if bucket:
                    return bucket.pop(0)
                if self._recv_error is not None:
                    # Post-dispatch failure (the command may already have run
                    # on the desktop) — NEVER PreDispatchError; a caller must
                    # not re-dispatch this (spec §5.1 M4).
                    raise self._recv_error
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(
                        f"desktop exec relay timed out waiting for request {request_id}"
                    )
                self._cond.wait(timeout=remaining)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._ws.close()
        except Exception:
            pass
