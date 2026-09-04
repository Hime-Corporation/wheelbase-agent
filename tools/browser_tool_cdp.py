"""User-supplied CDP endpoint resolution (browser.cdp_url / real-profile), dialog-policy config and the per-task CDP supervisor lifecycle.

Split out of ``tools/browser_tool.py``. Facade-owned state is read through ``_bt`` (``tools.browser_tool``, resolved per call) — no import cycle."""

import contextlib
import os
import threading
from typing import Tuple

from tools.browser_tool_origin import origin_module as _origin


_task_cdp_urls: dict[str, str] = {}
_task_cdp_lock = threading.Lock()
_task_cdp_registration_lock = threading.Lock()


def _resolve_cdp_override(cdp_url: str) -> str:
    """Normalize a user-supplied CDP endpoint into a concrete websocket URL.

    Full ``ws://.../devtools/browser/...`` endpoints pass through; HTTP discovery roots and bare ``ws://host:port``
    resolve via ``/json/version`` → ``webSocketDebuggerUrl``. Discovery failures return an empty string so a dead
    relay cannot remain truthy and silently reroute a task.
    """
    _bt = _origin()
    raw = (cdp_url or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if "/devtools/browser/" in lowered:
        return raw

    discovery_url = raw
    if lowered.startswith(("ws://", "wss://")):
        if not (raw.count(":") == 2 and raw.rstrip("/").rsplit(":", 1)[-1].isdigit() and "/" not in raw.split(":", 2)[-1]):
            return raw
        discovery_url = ("http://" if lowered.startswith("ws://") else "https://") + raw.split("://", 1)[1]
    path_part, sep, query_part = discovery_url.partition("?")
    version_url = path_part if path_part.lower().endswith("/json/version") else path_part.rstrip("/") + "/json/version"
    if sep:
        version_url += sep + query_part

    san = _bt._sanitize_url_for_logs
    try:
        import requests  # lazy — shared module object, test patches still apply
        response = requests.get(version_url, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        _bt.logger.warning("Failed to resolve CDP endpoint %s via %s: %s", san(raw), san(version_url), san(exc))
        return ""
    ws_url = str(payload.get("webSocketDebuggerUrl") or "").strip()
    if ws_url:
        _bt.logger.info("Resolved CDP endpoint %s -> %s", san(raw), san(ws_url))
        return ws_url
    _bt.logger.warning(
        "CDP discovery at %s did not return webSocketDebuggerUrl; treating endpoint as unusable",
        san(version_url),
    )
    return ""


def _get_cdp_override_raw(task_id: str = "default") -> str:
    """Return the *configured* CDP override without any network I/O.

    Precedence: ``BROWSER_CDP_URL`` env (live ``/browser connect``), then ``browser.cdp_url``. Is-it-configured
    gates (check_fns, ``_is_local_mode`` / ``_is_local_backend``, ``hermes doctor``) MUST use this, not
    :func:`_get_cdp_override`: its 10s HTTP discovery against a stale ``cdp_url`` would stall every startup's
    schema build with no error.
    """
    try:
        from wheelbase_sdk.runtime import get_task_identity

        identity = get_task_identity(task_id) or {}
        if identity.get("client") == "desktop" and identity.get("cdp_url"):
            return str(identity["cdp_url"]).strip()
    except ImportError:
        pass
    with _task_cdp_lock:
        task_override = str(_task_cdp_urls.get(task_id, "") or "").strip()
    if task_override:
        return task_override
    env_override = os.environ.get("BROWSER_CDP_URL", "").strip()
    return env_override or _origin()._browser_cfg("cdp_url", "", lambda v: str(v or "").strip(), "browser.cdp_url from config")


def _get_cdp_override(task_id: str = "default") -> str:
    """Resolved CDP URL override, or "" (skips cloud AND local launch).

    May perform HTTP ``/json/version`` discovery — only call on paths about to *connect*; pure gates must use
    :func:`_get_cdp_override_raw`.
    """
    _bt = _origin()
    return _resolve_cdp_override(raw) if (raw := _get_cdp_override_raw(task_id)) else ""


class DesktopUnavailableError(RuntimeError):
    """The session requires a desktop relay that is not currently usable."""

    code = "desktop_unavailable"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        try:
            from wheelbase_sdk.runtime import DESKTOP_UNAVAILABLE_MESSAGE

            message = DESKTOP_UNAVAILABLE_MESSAGE
        except ImportError:
            message = "The desktop browser relay is unavailable. Reconnect the Wheelbase desktop and retry."
        super().__init__(message)


def _desktop_requires_cdp(task_id: str) -> bool:
    try:
        from wheelbase_sdk.runtime import get_task_identity

        return (get_task_identity(task_id) or {}).get("client") == "desktop"
    except ImportError:
        return False


def _desktop_task_cdp_raw(task_id: str) -> str:
    try:
        from wheelbase_sdk.runtime import get_task_identity

        identity = get_task_identity(task_id) or {}
        if identity.get("client") == "desktop":
            identity_cdp = str(identity.get("cdp_url") or "").strip()
            if identity_cdp:
                return identity_cdp
    except ImportError:
        pass
    with _task_cdp_lock:
        return str(_task_cdp_urls.get(task_id, "") or "").strip()


def register_task_cdp_url(task_id: str, url: str) -> None:
    """Register the current task's CDP capability and recycle stale sessions on change."""
    task_id = str(task_id or "default")
    normalized = str(url or "").strip()
    with _task_cdp_registration_lock:
        with _task_cdp_lock:
            previous = _task_cdp_urls.get(task_id, "")
        _bt = _origin()
        with _bt._cleanup_lock:
            has_active_session = task_id in _bt._active_sessions
        if previous != normalized and has_active_session:
            try:
                _bt._lifecycle._cleanup_single_browser_session(task_id)
            except Exception:
                _bt.logger.debug("CDP session recycle failed for task=%s", task_id, exc_info=True)
        with _task_cdp_lock:
            if normalized:
                _task_cdp_urls[task_id] = normalized
            else:
                _task_cdp_urls.pop(task_id, None)


def _get_dialog_policy_config() -> Tuple[str, float]:
    """Read ``browser.dialog_policy`` + ``browser.dialog_timeout_s``; supervisor defaults when absent/invalid."""
    _bt = _origin()
    # Deferred so browser_tool imports in minimal environments.
    from tools.browser_supervisor_dialogs import DEFAULT_DIALOG_POLICY, DEFAULT_DIALOG_TIMEOUT_S, _VALID_POLICIES
    policy, timeout_s = DEFAULT_DIALOG_POLICY, DEFAULT_DIALOG_TIMEOUT_S
    try:
        from hermes_cli.config import read_raw_config
        cfg = read_raw_config()
        browser_cfg = cfg.get("browser", {}) if isinstance(cfg, dict) else {}
        if not isinstance(browser_cfg, dict):
            return policy, timeout_s
        candidate = str(browser_cfg.get("dialog_policy") or DEFAULT_DIALOG_POLICY)
        if candidate in _VALID_POLICIES:
            policy = candidate
        else:
            _bt.logger.debug("Invalid browser.dialog_policy=%r; using default", candidate)
        timeout_raw = browser_cfg.get("dialog_timeout_s")
        try:
            timeout_s = float(timeout_raw) if timeout_raw is not None else DEFAULT_DIALOG_TIMEOUT_S
            if timeout_s <= 0:
                timeout_s = DEFAULT_DIALOG_TIMEOUT_S
        except (TypeError, ValueError):
            timeout_s = DEFAULT_DIALOG_TIMEOUT_S
        return policy, timeout_s
    except Exception:
        return DEFAULT_DIALOG_POLICY, DEFAULT_DIALOG_TIMEOUT_S


def _ensure_cdp_supervisor(task_id: str) -> None:
    """Start a CDP supervisor for ``task_id`` if an endpoint is reachable.

    Idempotent (``get_or_start`` skips an existing ``(task_id, cdp_url)`` and restarts on URL change), so safe on
    every navigate / ``/browser connect``. URL precedence: the CDP override, then the session's own ``cdp_url``
    (cloud providers, e.g. Browserbase). Swallows all errors — a failed attach must not break the session;
    snapshots just lack ``pending_dialogs`` / ``frame_tree``.
    """
    _bt = _origin()
    cdp_url = _get_cdp_override(task_id)
    if not cdp_url:
        with _bt._cleanup_lock:
            session_info = _bt._active_sessions.get(task_id, {})
        maybe = str(session_info.get("cdp_url") or "")
        if maybe:
            cdp_url = _resolve_cdp_override(maybe)
    if not cdp_url:
        return
    try:
        from tools.browser_supervisor import SUPERVISOR_REGISTRY  # type: ignore[import-not-found]
        policy, timeout_s = _get_dialog_policy_config()
        SUPERVISOR_REGISTRY.get_or_start(task_id=task_id, cdp_url=cdp_url, dialog_policy=policy, dialog_timeout_s=timeout_s)
    except Exception as exc:
        _bt.logger.debug("CDP supervisor attach for task=%s failed (non-fatal): %s", task_id, exc)


def _stop_cdp_supervisor(task_id: str) -> None:
    """Stop the CDP supervisor for ``task_id`` if one exists. No-op otherwise."""
    try:
        from tools.browser_supervisor import SUPERVISOR_REGISTRY  # type: ignore[import-not-found]
        SUPERVISOR_REGISTRY.stop(task_id)
    except Exception as exc:
        _origin().logger.debug("CDP supervisor stop for task=%s failed (non-fatal): %s", task_id, exc)
