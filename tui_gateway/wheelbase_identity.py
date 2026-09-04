"""Per-connection Wheelbase identity for the multi-user cloud gateway.

The authenticated Go chat broker injects one signed identity envelope on the
``/api/ws`` upgrade. The gateway verifies its signature, issuer, audience,
purpose, lifetime, and replay nonce and rejects independent identity headers.
Single-user desktop/dev connections carry no envelope and retain the legacy
``identity=None`` behavior.
"""
from __future__ import annotations

import json
import base64
import binascii
import hashlib
import hmac
import logging
import os
import re
import threading
import time
import uuid
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
HEADER_CLIENT = "x-wheelbase-client"
HEADER_DEVICE = "x-wheelbase-device-id"
HEADER_ENVELOPE = "x-wheelbase-identity-envelope"
_INDEPENDENT_HEADERS = frozenset({
    HEADER_USER, HEADER_TENANT, HEADER_DEALERSHIP, HEADER_JWT, HEADER_CDP,
    HEADER_SHELL_RELAY, HEADER_CLIENT, HEADER_DEVICE,
    "x-wheelbase-session-jti-hash", "x-wheelbase-credential-revision",
    "x-wheelbase-credential-expires-at",
})
_JTI_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_ISSUER = "wheelbase-api"
_EXPECTED_AUDIENCE = "wheelbase-agent-gateway"
_EXPECTED_KIND = "agent_gateway_identity"
_MAX_ENVELOPE_TTL_SECONDS = 30
_nonce_expiries: dict[str, int] = {}
_nonce_lock = threading.Lock()

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
    client: str = ""
    device_id: str = ""
    session_jti_hash: str = ""
    credential_revision: int = 0
    credential_expires_at: int = 0
    credential_source: str = "agent_session"


def _signal_value(identity: WheelbaseIdentity | Mapping[str, Any], field: str) -> Any:
    if isinstance(identity, Mapping):
        return identity.get(field)
    return getattr(identity, field, None)


def _signal_fingerprint(value: Any) -> str:
    raw = str(value or "").strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:12] if raw else "none"


def identity_signal_fields(
    identity: WheelbaseIdentity | Mapping[str, Any],
    *,
    connection_id: str = "",
    now: int | None = None,
) -> dict[str, Any]:
    """Return bounded lifecycle fields with sensitive scope values hashed."""
    current = int(time.time()) if now is None else int(now)
    expiry = _signal_value(identity, "credential_expires_at")
    expiry = expiry if isinstance(expiry, int) and not isinstance(expiry, bool) else 0
    revision = _signal_value(identity, "credential_revision")
    revision = revision if isinstance(revision, int) and not isinstance(revision, bool) else 0
    client = str(_signal_value(identity, "client") or "").strip().lower()
    source = str(_signal_value(identity, "credential_source") or "").strip().lower()
    return {
        "user_fp": _signal_fingerprint(_signal_value(identity, "user_id")),
        "tenant_fp": _signal_fingerprint(_signal_value(identity, "tenant_id")),
        "device_fp": _signal_fingerprint(_signal_value(identity, "device_id")),
        "jti_fp": _signal_fingerprint(_signal_value(identity, "session_jti_hash")),
        "connection_fp": _signal_fingerprint(connection_id),
        "client": client if client in {"desktop", "mobile"} else "unknown",
        "source": source
        if source in {"agent_session", "agent_gateway_identity", "local"}
        else "unknown",
        "revision": revision,
        "expiry_age_s": current - expiry if expiry else None,
        "expiry_skew_s": expiry - current if expiry else None,
    }


def log_identity_lifecycle(
    event: str,
    identity: WheelbaseIdentity | Mapping[str, Any],
    *,
    reason: str = "",
    connection_id: str = "",
    attempted_revision: Any = None,
    attempted_expires_at: Any = None,
    active_task_count: int | None = None,
    action: str = "",
) -> None:
    """Emit one token-safe structured broker credential lifecycle signal."""
    payload = {
        "event": str(event or "unknown"),
        **identity_signal_fields(identity, connection_id=connection_id),
    }
    if reason:
        payload["reason"] = str(reason)
    if isinstance(attempted_revision, int) and not isinstance(attempted_revision, bool):
        payload["attempted_revision"] = attempted_revision
    if isinstance(attempted_expires_at, int) and not isinstance(attempted_expires_at, bool):
        now = int(time.time())
        payload["attempted_expiry_age_s"] = now - attempted_expires_at
        payload["attempted_expiry_skew_s"] = attempted_expires_at - now
    if active_task_count is not None:
        payload["active_task_count"] = max(0, int(active_task_count))
    if action:
        payload["action"] = str(action)
    level = logging.WARNING if event.endswith("dropped") else logging.INFO
    _log.log(
        level,
        "wheelbase_identity_lifecycle %s",
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
    )


def _decode_segment(segment: str) -> bytes:
    if not segment or "=" in segment:
        raise ValueError("invalid identity envelope encoding")
    try:
        decoded = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise ValueError("invalid identity envelope encoding") from exc
    if canonical != segment:
        raise ValueError("invalid identity envelope encoding")
    return decoded


def load_identity_envelope_keys() -> dict[str, bytes]:
    raw = os.environ.get("AGENT_GATEWAY_IDENTITY_KEYS", "").strip()
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("AGENT_GATEWAY_IDENTITY_KEYS must be a JSON key map") from exc
    if not isinstance(parsed, dict) or not parsed or len(parsed) > 8:
        raise ValueError("identity envelope key ring must contain 1-8 keys")
    keys: dict[str, bytes] = {}
    for kid, encoded in parsed.items():
        if not isinstance(kid, str) or not kid or not isinstance(encoded, str):
            raise ValueError("identity envelope key ring contains an invalid entry")
        try:
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("identity envelope key must be base64") from exc
        if len(key) != 32:
            raise ValueError("identity envelope keys must decode to 32 bytes")
        keys[kid] = key
    return keys


def _one_header(headers: Any, name: str) -> str:
    values = headers.getlist(name) if hasattr(headers, "getlist") else None
    if values is None:
        values = [value for key, value in headers.items() if str(key).lower() == name]
    if len(values) > 1:
        raise ValueError("duplicate identity envelope")
    return str(values[0]).strip() if values else ""


def identity_from_headers(headers: Mapping[str, str], *, now: int | None = None) -> Optional[WheelbaseIdentity]:
    """Verify one atomic broker envelope; reject all independent trust headers."""
    lowered_names = {str(k).lower() for k, _v in headers.items()}
    if lowered_names & _INDEPENDENT_HEADERS:
        raise ValueError("independent Wheelbase identity headers are forbidden")
    token = _one_header(headers, HEADER_ENVELOPE)
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed identity envelope")
    try:
        header = json.loads(_decode_segment(parts[0]))
        payload = json.loads(_decode_segment(parts[1]))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("malformed identity envelope") from exc
    if not isinstance(header, dict) or set(header) != {"alg", "typ", "kid"}:
        raise ValueError("invalid identity envelope header")
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise ValueError("invalid identity envelope algorithm")
    keys = load_identity_envelope_keys()
    key = keys.get(header.get("kid"))
    if key is None:
        raise ValueError("unknown identity envelope key")
    expected = hmac.new(key, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _decode_segment(parts[2])):
        raise ValueError("invalid identity envelope signature")
    current = int(time.time()) if now is None else now
    if not isinstance(payload, dict):
        raise ValueError("invalid identity envelope payload")
    if payload.get("iss") != _EXPECTED_ISSUER or payload.get("aud") != _EXPECTED_AUDIENCE or payload.get("kind") != _EXPECTED_KIND or payload.get("ver") != 2:
        raise ValueError("invalid identity envelope purpose")
    iat, exp = payload.get("iat"), payload.get("exp")
    if not isinstance(iat, int) or isinstance(iat, bool) or not isinstance(exp, int) or isinstance(exp, bool):
        raise ValueError("invalid identity envelope lifetime")
    if iat > current + 5 or exp <= current or exp <= iat or exp - iat > _MAX_ENVELOPE_TTL_SECONDS:
        raise ValueError("expired or overlong identity envelope")
    nonce = payload.get("nonce")
    try:
        parsed_nonce = uuid.UUID(str(nonce))
    except (ValueError, AttributeError) as exc:
        raise ValueError("invalid identity envelope nonce") from exc
    if str(parsed_nonce) != str(nonce).lower():
        raise ValueError("invalid identity envelope nonce")
    with _nonce_lock:
        for seen, seen_exp in tuple(_nonce_expiries.items()):
            if seen_exp <= current:
                _nonce_expiries.pop(seen, None)
        if nonce in _nonce_expiries:
            raise ValueError("replayed identity envelope")
        _nonce_expiries[nonce] = exp
    bundle = payload.get("bundle")
    if not isinstance(bundle, dict):
        raise ValueError("invalid identity envelope bundle")
    user_id = bundle.get("user_id")
    tenant_id = bundle.get("tenant_id")
    client = bundle.get("client")
    device_id = bundle.get("device_id") or ""
    jti_hash = bundle.get("session_jti_hash")
    revision = bundle.get("credential_revision")
    credential_expiry = bundle.get("credential_expires_at")
    access_token = bundle.get("access_token")
    if not isinstance(user_id, str) or not is_valid_user_id(user_id) or not isinstance(tenant_id, str) or not is_valid_user_id(tenant_id):
        raise ValueError("invalid identity scope")
    if client not in {"desktop", "mobile"}:
        raise ValueError("invalid identity client")
    if client == "desktop":
        try:
            if str(uuid.UUID(device_id)) != device_id.lower():
                raise ValueError
        except (ValueError, AttributeError) as exc:
            raise ValueError("desktop identity requires a UUID device") from exc
    elif device_id:
        raise ValueError("mobile identity cannot carry a device")
    if not isinstance(jti_hash, str) or not _JTI_HASH_RE.fullmatch(jti_hash):
        raise ValueError("invalid session JTI hash")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("invalid credential revision")
    if not isinstance(credential_expiry, int) or isinstance(credential_expiry, bool) or credential_expiry <= current:
        raise ValueError("invalid credential expiry")
    if not isinstance(access_token, str) or not access_token.strip():
        raise ValueError("missing access token")
    for optional in ("dealership_id", "cdp_url", "shell_relay_url"):
        if optional in bundle and not isinstance(bundle[optional], str):
            raise ValueError(f"invalid {optional}")
    return WheelbaseIdentity(
        user_id=user_id,
        tenant_id=tenant_id,
        dealership_id=bundle.get("dealership_id", "").strip(),
        jwt=access_token.strip(),
        cdp_url=bundle.get("cdp_url", "").strip(),
        shell_relay_url=bundle.get("shell_relay_url", "").strip(),
        client=client,
        device_id=device_id,
        session_jti_hash=jti_hash,
        credential_revision=revision,
        credential_expires_at=credential_expiry,
        credential_source="agent_gateway_identity",
    )


_lock = threading.Lock()


def credential_path(hermes_home: Path, session_jti_hash: str) -> Path:
    if not is_valid_user_id(session_jti_hash):
        raise ValueError("invalid session JTI hash")
    return hermes_home / "wheelbase-sessions" / f"{session_jti_hash}.json"


def write_credential_file(hermes_home: Path, identity: WheelbaseIdentity) -> Path:
    """Atomically persist one revisioned, JTI-scoped broker credential."""
    if not identity.jwt or identity.credential_expires_at <= 0:
        raise ValueError("credential token and authoritative expiry are required")
    if identity.credential_revision <= 0:
        raise ValueError("positive credential revision is required")
    path = credential_path(hermes_home, identity.session_jti_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": identity.jwt,
        "expires_at": identity.credential_expires_at,
        "revision": identity.credential_revision,
        "source": identity.credential_source or "agent_session",
    }
    with _lock:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            current_revision = current.get("revision", 0) if isinstance(current, dict) else 0
        except (OSError, ValueError):
            current_revision = 0
        if isinstance(current_revision, int) and current_revision >= identity.credential_revision:
            return path
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            os.chmod(path, 0o600)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
    return path


def remove_credential_file(hermes_home: Path, session_jti_hash: str) -> bool:
    """Remove exactly one connection credential; never glob by user."""
    path = credential_path(hermes_home, session_jti_hash)
    with _lock:
        try:
            path.unlink()
        except FileNotFoundError:
            return False
    return True


def _attach_identity_to_transport(ws: Any, transport: Any) -> None:
    """Capture Wheelbase identity from the WS upgrade and attach it to *transport*.

    Sets ``transport.wheelbase_identity`` to a :class:`WheelbaseIdentity` when a
    valid broker envelope is present, or ``None`` for legacy/desktop connections
    with no envelope. Does not catch: a malformed envelope or independent
    identity headers raise, and the WS accept fails closed.
    """
    raw_headers = getattr(ws, "headers", None) or {}
    identity = identity_from_headers(raw_headers)
    transport.wheelbase_identity = identity
