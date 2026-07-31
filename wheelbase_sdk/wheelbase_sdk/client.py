"""WheelbaseClient — authenticated access to Supabase PostgREST + the Go API.

Reads the per-request session token (HERMES_HOME/wheelbase-session.json) and the
SUPABASE_URL / SUPABASE_ANON_KEY / WHEELBASE_GO_API_ORIGIN env vars (injected into
the Hermes child by the desktop supervisor). Row scoping is handled entirely by
Supabase RLS on the user JWT — callers never pass an org/tenant id.

This is the FROZEN public interface the four Wheelbase plugins build against.
"""

from __future__ import annotations

import os
from typing import Any, Callable

import httpx

from .errors import WheelbaseAuthError, WheelbaseForbiddenError, log_auth_lifecycle
from .session import load_session


class WheelbaseClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        session = load_session()
        if session is None:
            log_auth_lifecycle("not_signed_in", source="local")
            raise WheelbaseAuthError("not signed in")
        self._session = session
        self._token = session.access_token
        self._supabase_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self._anon = os.environ.get("SUPABASE_ANON_KEY") or ""
        self._go_origin = (os.environ.get("WHEELBASE_GO_API_ORIGIN") or "").rstrip("/")
        self._http = httpx.Client(transport=transport, timeout=timeout)

    def _reload(self) -> None:
        session = load_session()
        if session is None:
            log_auth_lifecycle("not_signed_in", source="request")
            raise WheelbaseAuthError("not signed in", reason="not_signed_in")
        self._session = session
        self._token = session.access_token

    def _send(
        self,
        build: Callable[[], httpx.Response],
        *,
        method: str,
        idempotent_rpc: bool = False,
    ) -> httpx.Response:
        """Reload credentials before I/O and retry one safe request on rotation."""
        self._reload()
        original_revision = self._session.revision
        response = build()
        if response.status_code == 401:
            safe = method.upper() in {"GET", "HEAD"} or idempotent_rpc
            if safe:
                replacement = load_session()
                if replacement is not None and replacement.revision > original_revision:
                    self._session = replacement
                    self._token = replacement.access_token
                    response = build()
            if response.status_code == 401:
                log_auth_lifecycle(
                    "not_signed_in",
                    source="request",
                    revision=self._session.revision,
                    expires_at=self._session.expires_at,
                    status=401,
                )
                raise WheelbaseAuthError("not signed in", reason="not_signed_in")
        if response.status_code == 403:
            log_auth_lifecycle(
                "forbidden",
                source="request",
                revision=self._session.revision,
                expires_at=self._session.expires_at,
                status=403,
            )
            raise WheelbaseForbiddenError("forbidden")
        response.raise_for_status()
        return response

    # ── Supabase PostgREST ────────────────────────────────────────────────────
    def _pg_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "apikey": self._anon,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def postgrest_get(self, table: str, params: dict[str, str]) -> list[dict]:
        r = self._send(
            lambda: self._http.get(
                f"{self._supabase_url}/rest/v1/{table}",
                params=params,
                headers=self._pg_headers(),
            ),
            method="GET",
        )
        return r.json()

    def postgrest_write(
        self,
        method: str,
        table: str,
        *,
        body: Any = None,
        params: dict[str, str] | None = None,
        prefer: str = "return=representation",
        idempotent_rpc: bool = False,
    ) -> Any:
        r = self._send(
            lambda: self._http.request(
                method,
                f"{self._supabase_url}/rest/v1/{table}",
                params=params or {},
                json=body,
                headers={**self._pg_headers(), "Prefer": prefer},
            ),
            method=method,
            idempotent_rpc=idempotent_rpc,
        )
        return r.json() if r.content else None

    def postgrest_get_page(
        self,
        table: str,
        params: dict[str, str],
        *,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[dict], int | None]:
        """Paginated PostgREST GET with exact count via Content-Range header.

        Adds ``limit`` / ``offset`` query params and ``Prefer: count=exact`` so
        PostgREST returns the total row count in the ``Content-Range`` response
        header (format: ``<first>-<last>/<total>`` e.g. ``0-49/200``).

        Returns ``(rows, next_offset)`` where ``next_offset`` is ``None`` when
        the current page is the last one (or the header is absent/unparseable).
        Leaves the caller-supplied ``params`` dict unchanged.
        """
        merged = {**params, "limit": str(limit), "offset": str(offset)}
        headers = {**self._pg_headers(), "Prefer": "count=exact"}
        r = self._send(
            lambda: self._http.get(
                f"{self._supabase_url}/rest/v1/{table}",
                params=merged,
                headers={**self._pg_headers(), "Prefer": "count=exact"},
            ),
            method="GET",
        )
        rows: list[dict] = r.json()

        next_offset: int | None = None
        content_range = r.headers.get("Content-Range") or r.headers.get("content-range")
        if content_range:
            # Format: "<first>-<last>/<total>" (e.g. "0-49/200") or "<first>-<last>/*"
            try:
                _range, total_str = content_range.split("/", 1)
                if total_str != "*":
                    total = int(total_str)
                    # Derive next offset from the last index in the page rather than
                    # offset+limit, so a short page (server max-rows < limit) doesn't
                    # skip the rows between last+1 and offset+limit.
                    _, last_str = _range.split("-", 1)
                    last = int(last_str)
                    candidate = last + 1
                    if candidate < total:
                        next_offset = candidate
            except (ValueError, AttributeError):
                pass

        return rows, next_offset

    # ── Go API ────────────────────────────────────────────────────────────────
    def go_api(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: dict[str, str] | None = None,
        idempotent_rpc: bool = False,
    ) -> Any:
        r = self._send(
            lambda: self._http.request(
                method,
                f"{self._go_origin}{path}",
                params=params or {},
                json=body,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
            ),
            method=method,
            idempotent_rpc=idempotent_rpc,
        )
        return r.json() if r.content else None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "WheelbaseClient":
        return self

    def __exit__(self, *_a: object) -> None:
        self.close()
