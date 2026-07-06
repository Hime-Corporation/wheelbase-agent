"""Per-connection Wheelbase identity for the multi-user cloud gateway.

The Go backend chat broker injects identity headers on the /api/ws upgrade.
The gateway trusts them because it is reachable only on the private network
and the upgrade is dashboard-token authenticated. Single-user desktop/dev
connections carry no headers and get identity=None (legacy behavior).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

_log = logging.getLogger(__name__)

HEADER_USER = "x-wheelbase-user-id"
HEADER_TENANT = "x-wheelbase-tenant-id"
HEADER_DEALERSHIP = "x-wheelbase-dealership-id"
HEADER_JWT = "x-wheelbase-user-jwt"
HEADER_CDP = "x-wheelbase-cdp-url"
HEADER_SHELL_RELAY = "x-wheelbase-shell-relay-url"

# Supabase UUID format and safe identifiers: alphanumeric, underscores, hyphens, 1-64 chars.
_USER_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def is_valid_user_id(user_id: str) -> bool:
    """Return True when *user_id* is safe for profile and credential paths."""
    return bool(user_id) and bool(_USER_ID_RE.match(user_id))


@dataclass(frozen=True)
class WheelbaseIdentity:
    user_id: str
    tenant_id: str = ""
    dealership_id: str = ""
    jwt: str = ""
    cdp_url: str = ""
    shell_relay_url: str = ""


def identity_from_headers(headers: Mapping[str, str]) -> Optional[WheelbaseIdentity]:
    """Parse and validate Wheelbase identity from HTTP upgrade headers.

    Returns None for single-user desktop/dev connections (no user header) and
    for any connection with an invalid user_id — guards file-path and
    docker-volume-name construction downstream.
    """
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    user_id = lowered.get(HEADER_USER, "").strip()
    if not user_id:
        return None
    # Sanitize: reject anything that doesn't match the safe identifier pattern.
    # This prevents path traversal (../evil), spaces, null bytes, slashes, etc.
    if not is_valid_user_id(user_id):
        return None
    return WheelbaseIdentity(
        user_id=user_id,
        tenant_id=lowered.get(HEADER_TENANT, "").strip(),
        dealership_id=lowered.get(HEADER_DEALERSHIP, "").strip(),
        jwt=lowered.get(HEADER_JWT, "").strip(),
        cdp_url=lowered.get(HEADER_CDP, "").strip(),
        shell_relay_url=lowered.get(HEADER_SHELL_RELAY, "").strip(),
    )


_lock = threading.Lock()
_jwt_by_user: dict[str, str] = {}


def update_user_jwt(user_id: str, jwt: str) -> None:
    with _lock:
        _jwt_by_user[user_id] = jwt


def current_jwt(identity: WheelbaseIdentity) -> str:
    with _lock:
        return _jwt_by_user.get(identity.user_id, "") or identity.jwt


def credential_path(hermes_home: Path, user_id: str) -> Path:
    return hermes_home / "wheelbase-sessions" / f"{user_id}.json"


def write_credential_file(hermes_home: Path, identity: WheelbaseIdentity) -> Path:
    """Per-user session credential the Wheelbase SDK resolves (spec §5.1.1)."""
    path = credential_path(hermes_home, identity.user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"access_token": current_jwt(identity), "expires_at": int(time.time()) + 3600}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def _attach_identity_to_transport(ws: Any, transport: Any) -> None:
    """Capture Wheelbase identity headers from the WS upgrade and attach to transport.

    Sets ``transport.wheelbase_identity`` to a :class:`WheelbaseIdentity` instance
    when trusted identity headers are present, or ``None`` for legacy/desktop
    connections.  Always safe: exceptions are caught and produce ``identity=None``.
    """
    try:
        raw_headers = getattr(ws, "headers", None)
        identity = identity_from_headers(dict(raw_headers or {}))
    except Exception:
        identity = None
    transport.wheelbase_identity = identity
