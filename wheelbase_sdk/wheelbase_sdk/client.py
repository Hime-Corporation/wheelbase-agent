"""WheelbaseClient — authenticated access to Supabase PostgREST + the Go API.

Reads the per-request session token (HERMES_HOME/wheelbase-session.json) and the
SUPABASE_URL / SUPABASE_ANON_KEY / WHEELBASE_GO_API_ORIGIN env vars (injected into
the Hermes child by the desktop supervisor). Row scoping is handled entirely by
Supabase RLS on the user JWT — callers never pass an org/tenant id.

This is the FROZEN public interface the four Wheelbase plugins build against.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .errors import WheelbaseAuthError
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
            raise WheelbaseAuthError("not signed in")
        self._token = session.access_token
        self._supabase_url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self._anon = os.environ.get("SUPABASE_ANON_KEY") or ""
        self._go_origin = (os.environ.get("WHEELBASE_GO_API_ORIGIN") or "").rstrip("/")
        self._http = httpx.Client(transport=transport, timeout=timeout)

    # ── Supabase PostgREST ────────────────────────────────────────────────────
    def _pg_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "apikey": self._anon,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def postgrest_get(self, table: str, params: dict[str, str]) -> list[dict]:
        r = self._http.get(
            f"{self._supabase_url}/rest/v1/{table}",
            params=params,
            headers=self._pg_headers(),
        )
        r.raise_for_status()
        return r.json()

    def postgrest_write(
        self,
        method: str,
        table: str,
        *,
        body: Any = None,
        params: dict[str, str] | None = None,
        prefer: str = "return=representation",
    ) -> Any:
        headers = {**self._pg_headers(), "Prefer": prefer}
        r = self._http.request(
            method,
            f"{self._supabase_url}/rest/v1/{table}",
            params=params or {},
            json=body,
            headers=headers,
        )
        r.raise_for_status()
        return r.json() if r.content else None

    # ── Go API ────────────────────────────────────────────────────────────────
    def go_api(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        r = self._http.request(
            method,
            f"{self._go_origin}{path}",
            params=params or {},
            json=body,
            headers=headers,
        )
        r.raise_for_status()
        return r.json() if r.content else None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "WheelbaseClient":
        return self

    def __exit__(self, *_a: object) -> None:
        self.close()
