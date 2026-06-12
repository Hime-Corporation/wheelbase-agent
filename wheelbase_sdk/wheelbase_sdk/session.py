"""Read the Supabase session that the Wheelbase desktop delivers into HERMES_HOME.

The Electron main process writes `$HERMES_HOME/wheelbase-session.json` on every
auth change (sign-in, token refresh) and removes it on sign-out. Plugins read it
PER REQUEST so a freshly-refreshed token is always picked up with no restart.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WheelbaseSession:
    access_token: str
    expires_at: int | None = None


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
    if ident and ident.get("credential_path"):
        path = Path(ident["credential_path"])
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = None
            if isinstance(data, dict):
                token = data.get("access_token")
                if isinstance(token, str) and token:
                    exp = data.get("expires_at")
                    return WheelbaseSession(
                        access_token=token,
                        expires_at=exp if isinstance(exp, int) else None,
                    )
        # credential_path set but file missing/malformed → fall through to legacy

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
    )
