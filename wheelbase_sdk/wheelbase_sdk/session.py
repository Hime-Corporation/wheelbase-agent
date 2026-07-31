"""Read the Supabase session that the Wheelbase desktop delivers into HERMES_HOME.

The Electron main process writes `$HERMES_HOME/wheelbase-session.json` on every
auth change (sign-in, token refresh) and removes it on sign-out. Plugins read it
PER REQUEST so a freshly-refreshed token is always picked up with no restart.
"""

from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WheelbaseSession:
    access_token: str
    expires_at: int | None = None
    revision: int = 0
    source: str = "local"
    credential_path: Path | None = None


AUTH_EXPIRY_SKEW_SECONDS = 30


def _task_session(path: Path) -> WheelbaseSession:
    from .errors import WheelbaseAuthError

    try:
        info = path.lstat()
    except OSError as exc:
        raise WheelbaseAuthError("refresh_pending", reason="refresh_pending") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise WheelbaseAuthError("refresh_pending", reason="refresh_pending")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise WheelbaseAuthError("refresh_pending", reason="refresh_pending")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WheelbaseAuthError("refresh_pending", reason="refresh_pending") from exc
    if not isinstance(data, dict):
        raise WheelbaseAuthError("refresh_pending", reason="refresh_pending")
    token = data.get("access_token")
    expiry = data.get("expires_at")
    revision = data.get("revision")
    source = data.get("source")
    if not isinstance(token, str) or not token.strip():
        raise WheelbaseAuthError("not_signed_in", reason="not_signed_in")
    if not isinstance(expiry, int) or isinstance(expiry, bool):
        raise WheelbaseAuthError("refresh_pending", reason="refresh_pending")
    if expiry <= int(time.time()):
        raise WheelbaseAuthError("expired", reason="expired")
    if expiry <= int(time.time()) + AUTH_EXPIRY_SKEW_SECONDS:
        raise WheelbaseAuthError("refresh_pending", reason="refresh_pending")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        raise WheelbaseAuthError("refresh_pending", reason="refresh_pending")
    if not isinstance(source, str) or not source.strip():
        raise WheelbaseAuthError("refresh_pending", reason="refresh_pending")
    return WheelbaseSession(token.strip(), expiry, revision, source.strip(), path)


def _session_path() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "wheelbase-session.json"


def load_session() -> WheelbaseSession | None:
    """Return the current session, or None when signed out / file absent / malformed.

    Resolution order:
    1. Task-scoped identity (cloud gateway: per-user credential file injected per turn).
    2. Legacy singleton $HERMES_HOME/wheelbase-session.json (desktop / dev mode).
    """
    # --- 1. Task-scoped identity (cloud gateway) ---
    try:
        from wheelbase_sdk import runtime as _runtime
        ident = _runtime.current_identity()
    except Exception:
        ident = None
    if ident is not None:
        raw_path = ident.get("credential_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            from .errors import WheelbaseAuthError
            raise WheelbaseAuthError("refresh_pending", reason="refresh_pending")
        return _task_session(Path(raw_path))

    # --- 2. Legacy singleton file ---
    try:
        raw = _session_path().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        return None
    exp = data.get("expires_at")
    return WheelbaseSession(
        access_token=token,
        expires_at=exp if isinstance(exp, int) else None,
        revision=data.get("revision") if isinstance(data.get("revision"), int) else 0,
        source=str(data.get("source") or "local"),
        credential_path=_session_path(),
    )
