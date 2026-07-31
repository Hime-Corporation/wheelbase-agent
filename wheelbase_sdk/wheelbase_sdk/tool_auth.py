"""Shared auth/authorization boundary for every Wheelbase tool handler."""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from .errors import (
    WheelbaseAuthError,
    WheelbaseForbiddenError,
    forbidden_result,
    signed_out_result,
)

_F = TypeVar("_F", bound=Callable[..., str])


class _AuthAbort(BaseException):
    """Bypass tool-local ``except Exception`` blocks after credential loss."""


class _ForbiddenAbort(BaseException):
    """Bypass broad handlers while preserving authenticated denial."""


def _abort_for(exc: Exception) -> BaseException:
    if isinstance(exc, WheelbaseForbiddenError):
        return _ForbiddenAbort()
    return _AuthAbort()


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
            except (WheelbaseAuthError, WheelbaseForbiddenError) as exc:
                raise _abort_for(exc) from exc

        return call


def authenticated_client(factory: Callable[[], Any]) -> Any:
    try:
        return _ClientProxy(factory())
    except (WheelbaseAuthError, WheelbaseForbiddenError) as exc:
        raise _abort_for(exc) from exc


def auth_result(handler: _F) -> _F:
    """Map authentication and authorization failures to stable tool results."""
    @wraps(handler)
    def wrapped(*args: Any, **kwargs: Any) -> str:
        try:
            return handler(*args, **kwargs)
        except (WheelbaseForbiddenError, _ForbiddenAbort):
            return forbidden_result()
        except (WheelbaseAuthError, _AuthAbort):
            return signed_out_result()

    return wrapped  # type: ignore[return-value]
