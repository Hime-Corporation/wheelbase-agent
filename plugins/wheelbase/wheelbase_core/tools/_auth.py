"""One auth lifecycle boundary shared by Wheelbase tool handlers."""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from wheelbase_sdk import WheelbaseAuthError, signed_out_result

_F = TypeVar("_F", bound=Callable[..., str])


class _AuthAbort(BaseException):
    """Bypass tool-local ``except Exception`` blocks after credential loss."""


class _ClientProxy:
    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._client, name)
        if not callable(value):
            return value

        @wraps(value)
        def call(*args: Any, **kwargs: Any) -> Any:
            try:
                return value(*args, **kwargs)
            except WheelbaseAuthError as exc:
                raise _AuthAbort() from exc

        return call


def authenticated_client(factory: Callable[[], Any]) -> Any:
    try:
        return _ClientProxy(factory())
    except WheelbaseAuthError as exc:
        raise _AuthAbort() from exc


def auth_result(handler: _F) -> _F:
    """Keep the stable signed-out tool contract across construction and calls."""
    @wraps(handler)
    def wrapped(*args: Any, **kwargs: Any) -> str:
        try:
            return handler(*args, **kwargs)
        except (WheelbaseAuthError, _AuthAbort):
            return signed_out_result()

    return wrapped  # type: ignore[return-value]
