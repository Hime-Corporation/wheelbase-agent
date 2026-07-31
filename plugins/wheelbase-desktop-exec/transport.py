"""Transport abstraction for the exec relay (spec §5.1).

The concrete WS transport that reaches the desktop via the Go ExecHub is a
SEPARATE plan. Everything here is tested against FakeTransport; the plugin's
tested paths never import a real socket.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping, Optional


class PreDispatchError(Exception):
    """Raised when the relay fails BEFORE the desktop starts executing.

    The middleware maps this to ``desktop_unavailable``. Desktop-origin calls
    never fall back or replay, regardless of whether dispatch began.
    """
class ExecTransport(ABC):
    @abstractmethod
    def send(self, frame: Mapping[str, Any]) -> None:
        """Transmit one ExecInbound frame. Raise PreDispatchError if the
        connection is not yet established (nothing has executed)."""

    @abstractmethod
    def recv(self, request_id: str, timeout: Optional[float] = None) -> dict:
        """Return the next ExecOutbound frame for request_id."""

    def close(self) -> None:  # pragma: no cover - optional
        pass
