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


def _credential_session(
    path: Path,
    *,
    lifecycle_source: str,
    require_task_metadata: bool,
) -> WheelbaseSession:
    from .errors import WheelbaseAuthError, log_auth_lifecycle

    def auth_error(reason: str, data: object = None) -> WheelbaseAuthError:
        payload = data if isinstance(data, dict) else {}
        log_auth_lifecycle(
            reason,
            source=lifecycle_source,
            revision=payload.get("revision") if require_task_metadata else None,
            expires_at=payload.get("expires_at"),
        )
        return WheelbaseAuthError(reason, reason=reason)

    try:
        info = path.lstat()
    except OSError as exc:
        raise auth_error("refresh_pending") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise auth_error("refresh_pending")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise auth_error("refresh_pending")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise auth_error("refresh_pending") from exc
    if not isinstance(data, dict):
        raise auth_error("refresh_pending")
    token = data.get("access_token")
    expiry = data.get("expires_at")
    revision = data.get("revision")
    source = data.get("source")
    if not isinstance(token, str) or not token.strip():
        raise auth_error("not_signed_in", data)
    if not isinstance(expiry, int) or isinstance(expiry, bool):
        raise auth_error("refresh_pending", data)
    now = int(time.time())
    if expiry <= now:
        raise auth_error("expired", data)
    if expiry <= now + AUTH_EXPIRY_SKEW_SECONDS:
        raise auth_error("refresh_pending", data)
    if require_task_metadata:
        if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
            raise auth_error("refresh_pending", data)
        if not isinstance(source, str) or not source.strip():
            raise auth_error("refresh_pending", data)
        return WheelbaseSession(token.strip(), expiry, revision, source.strip(), path)
    return WheelbaseSession(token.strip(), expiry, 0, "local", path)


def _task_session(path: Path) -> WheelbaseSession:
    return _credential_session(
        path,
        lifecycle_source="task",
        require_task_metadata=True,
    )


def _desktop_singleton_session(path: Path) -> WheelbaseSession:
    return _credential_session(
        path,
        lifecycle_source="local",
        require_task_metadata=False,
    )


def _session_path() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "wheelbase-session.json"


def load_session() -> WheelbaseSession | None:
    """Return the current session, or None when no permitted credential exists.

    Resolution order:
    1. Task-scoped identity (cloud gateway: per-user credential file injected per turn).
    2. Singleton $HERMES_HOME/wheelbase-session.json (explicit desktop mode only).
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
            from .errors import WheelbaseAuthError, log_auth_lifecycle
            log_auth_lifecycle("refresh_pending", source="task")
            raise WheelbaseAuthError("refresh_pending", reason="refresh_pending")
        return _task_session(Path(raw_path))

    # --- 2. Desktop singleton file ---
    if os.environ.get("HERMES_DESKTOP") != "1":
        return None

    path = _session_path()
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        # Let the credential reader emit the bounded lifecycle reason.
        pass
    return _desktop_singleton_session(path)
