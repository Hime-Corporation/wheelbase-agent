"""Per-user profile router for the Wheelbase cloud dealership gateway.

The router keeps the backend-facing dashboard auth contract on port 9320, then
routes each authenticated Wheelbase user to a private child dashboard bound to
127.0.0.1 on an allocated port.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from starlette.websockets import WebSocketDisconnect

from tui_gateway.wheelbase_identity import is_valid_user_id

logger = logging.getLogger(__name__)

PROFILE_PREFIX = "wb-"
PROFILE_PLUGINS = (
    "wheelbase-core",
    "wheelbase-onboarding",
    "wheelbase-auction-browser",
    "wheelbase-demand-matrix",
    "wheelbase-inspection",
    "wheelbase-dealercenter-import",
)
# Toolsets removed from every per-user (wb-<uid>) profile. session_search can
# open ANY profile's state.db by path (in-process, bypasses the Daytona
# sandbox), so it must never be exposed to a non-admin user. The admin runs as
# the Hermes root profile, which never goes through provision_profile and so
# keeps session_search.
PROFILE_DISABLED_TOOLSETS = ("session_search",)
PORT_RANGE = (9400, 9899)

DEFAULT_SOUL = """\
# Wheelbase Dealership Agent

You are the Wheelbase agent for a car dealership. Help dealership staff manage
inventory, source vehicles at auction, analyze market demand, and run daily
operations. Use Wheelbase tools whenever they apply. Be concise, accurate, and
concrete.
"""

_PROXY_TIMEOUT_S = 300.0
_HOP_REQUEST_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "transfer-encoding",
        "keep-alive",
        "upgrade",
        "x-hermes-session-token",
    }
)
_HOP_RESPONSE_HEADERS = frozenset(
    {"content-length", "transfer-encoding", "connection"}
)
_WHEELBASE_HEADER_CANONICAL = {
    "x-wheelbase-user-id": "X-Wheelbase-User-Id",
    "x-wheelbase-tenant-id": "X-Wheelbase-Tenant-Id",
    "x-wheelbase-dealership-id": "X-Wheelbase-Dealership-Id",
    "x-wheelbase-user-jwt": "X-Wheelbase-User-Jwt",
    "x-wheelbase-cdp-url": "X-Wheelbase-Cdp-Url",
}


def profiles_root() -> Path:
    override = os.environ.get("WHEELBASE_PROFILES_ROOT", "").strip()
    if override:
        return Path(override)
    home = os.environ.get("HERMES_HOME", "/data/hermes").strip() or "/data/hermes"
    return Path(home) / "profiles"


def _default_seed_skills(profile_dir: Path) -> None:
    from hermes_cli.profiles import seed_profile_skills

    seed_profile_skills(profile_dir, quiet=True)


def _ensure_profile_plugins_enabled(config_path: Path) -> bool:
    """Back-fill PROFILE_PLUGINS into an existing profile ``config.yaml``.

    The original provision step wrote ``config.yaml`` only when absent, so a
    profile created before a plugin was added to ``PROFILE_PLUGINS`` (or one
    migrated from the pre-cutover shared store) keeps a stale or empty
    ``plugins.enabled`` list. Bundled Wheelbase plugins are ``standalone`` kind,
    so they load *only* when explicitly enabled — leaving such a profile with no
    Wheelbase tools and no self-healing path. Merge the required plugins in
    (order-preserving union) without disturbing any other user edits. Returns
    ``True`` if the file was rewritten.
    """
    import yaml

    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("could not read profile config for plugin back-fill: %s", config_path)
        return False
    if not isinstance(config, dict):
        return False

    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
    enabled = plugins.get("enabled")
    if not isinstance(enabled, list):
        enabled = []

    missing = [p for p in PROFILE_PLUGINS if p not in enabled]
    if not missing:
        return False

    plugins["enabled"] = list(enabled) + missing
    config["plugins"] = plugins
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    logger.info("back-filled wheelbase plugins into %s: added %s", config_path, missing)
    return True


def _ensure_session_search_disabled(config_path: Path) -> bool:
    """Back-fill PROFILE_DISABLED_TOOLSETS into an existing profile config.yaml.

    User (wb-<uid>) profiles must not expose cross-profile read tools such as
    session_search. Profiles created before this guard (or migrated from the
    shared store) carry no ``agent.disabled_toolsets`` list. Merge the required
    disables in (order-preserving union) without disturbing other edits. Returns
    ``True`` if the file was rewritten.
    """
    import yaml

    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("could not read profile config for toolset back-fill: %s", config_path)
        return False
    if not isinstance(config, dict):
        return False

    agent = config.get("agent")
    if not isinstance(agent, dict):
        agent = {}
    disabled = agent.get("disabled_toolsets")
    if not isinstance(disabled, list):
        disabled = []

    missing = [t for t in PROFILE_DISABLED_TOOLSETS if t not in disabled]
    if not missing:
        return False

    agent["disabled_toolsets"] = list(disabled) + missing
    config["agent"] = agent
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    logger.info("disabled cross-profile toolsets in %s: added %s", config_path, missing)
    return True


def provision_profile(
    profile_dir: Path,
    *,
    seed_skills: Optional[Callable[[Path], None]] = None,
) -> Path:
    """Seed a profile directory, preserving user edits but back-filling plugins."""
    import yaml

    from hermes_cli.profiles import _PROFILE_DIRS

    first_time = not profile_dir.exists()
    profile_dir.mkdir(parents=True, exist_ok=True)
    for subdir in _PROFILE_DIRS:
        (profile_dir / subdir).mkdir(parents=True, exist_ok=True)

    config_path = profile_dir / "config.yaml"
    if not config_path.exists():
        config = {
            "model": os.environ.get("WHEELBASE_PROFILE_MODEL", "minimax/minimax-m3"),
            "provider": os.environ.get("WHEELBASE_PROFILE_PROVIDER", "openrouter"),
            "skin": os.environ.get("WHEELBASE_PROFILE_SKIN", "wheelbase"),
            "plugins": {"enabled": list(PROFILE_PLUGINS)},
            "agent": {"disabled_toolsets": list(PROFILE_DISABLED_TOOLSETS)},
            "platform_toolsets": {
                "cli": {
                    "tools": ["todo"],
                },
            },
        }
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    else:
        _ensure_profile_plugins_enabled(config_path)
        _ensure_session_search_disabled(config_path)

    soul_path = profile_dir / "SOUL.md"
    if not soul_path.exists():
        soul_path.write_text(DEFAULT_SOUL, encoding="utf-8")

    # Seed bundled skills on first creation, or when the skills/ dir exists but
    # is empty (e.g. profiles migrated from the shared store before skill
    # seeding ran). seed_profile_skills() is idempotent: its sync manifest never
    # overwrites user-edited skills nor resurrects user-deleted ones, and the
    # .no-bundled-skills opt-out marker is honored inside the call.
    skills_dir = profile_dir / "skills"
    skills_empty = not any(skills_dir.rglob("SKILL.md")) if skills_dir.is_dir() else True
    if first_time or skills_empty:
        (seed_skills or _default_seed_skills)(profile_dir)
    return profile_dir


@dataclass
class Child:
    user_id: str
    profile_dir: Path
    port: int
    token: str
    proc: Any = None
    restarts: int = 0
    last_spawn: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


def _default_spawn(user_id: str, port: int, env: dict[str, str]) -> Any:
    cmd = [
        sys.executable,
        "-m",
        "hermes_cli.main",
        "dashboard",
        "--no-open",
        "--insecure",
        "--skip-build",
        # Keep this child scoped to its own profile dir. Without --isolated,
        # hermes_cli's "unified profile launch" re-execs the child into the
        # SHARED machine dashboard (HERMES_HOME reset to the volume root), so
        # plugin discovery reads the root config.yaml instead of this profile's
        # — and the per-profile Wheelbase plugins never load (no tools).
        "--isolated",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    logger.info("spawning profile child user=%s port=%d", user_id, port)
    return subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL)


def _default_wait_ready(port: int, token: str, timeout: float = 60.0) -> None:
    import httpx

    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    headers = {"X-Hermes-Session-Token": token}
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                f"http://127.0.0.1:{port}/api/status",
                headers=headers,
                timeout=2.0,
            )
            if response.status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001 - readiness polling
            last_exc = exc
        time.sleep(0.25)
    raise RuntimeError(f"child on port {port} not ready after {timeout}s: {last_exc}")


class ChildManager:
    _BACKOFF_RESET_S = 120.0

    def __init__(
        self,
        profiles_root: Path,
        *,
        spawn: Optional[Callable[[str, int, dict[str, str]], Any]] = None,
        wait_ready: Optional[Callable[[int, str], None]] = None,
        seed_skills: Optional[Callable[[Path], None]] = None,
        sleep: Callable[[float], None] = time.sleep,
        backoff_base: float = 1.0,
        backoff_cap: float = 60.0,
    ) -> None:
        self.profiles_root = profiles_root
        self._spawn = spawn or _default_spawn
        self._wait_ready = wait_ready or _default_wait_ready
        self._seed_skills = seed_skills
        self._sleep = sleep
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._children: dict[str, Child] = {}
        self._lock = threading.Lock()

    def _alloc_port(self) -> int:
        used = {child.port for child in self._children.values()}
        for port in range(PORT_RANGE[0], PORT_RANGE[1] + 1):
            if port not in used:
                return port
        raise RuntimeError("profile router port range exhausted")

    def _child_env(self, child: Child) -> dict[str, str]:
        return {
            **os.environ,
            "HERMES_HOME": str(child.profile_dir),
            "HERMES_DASHBOARD_SESSION_TOKEN": child.token,
        }

    def _spawn_child(self, child: Child) -> None:
        proc = self._spawn(child.user_id, child.port, self._child_env(child))
        try:
            self._wait_ready(child.port, child.token)
        except Exception:
            terminate = getattr(proc, "terminate", None)
            if callable(terminate):
                try:
                    terminate()
                except Exception:
                    logger.debug("failed to terminate unready child", exc_info=True)
            child.proc = None
            raise
        child.proc = proc
        child.last_spawn = time.monotonic()

    def ensure_child(self, user_id: str) -> Child:
        if not is_valid_user_id(user_id):
            raise ValueError(f"invalid user id: {user_id!r}")
        with self._lock:
            child = self._children.get(user_id)
            if child is None:
                child = Child(
                    user_id=user_id,
                    profile_dir=self.profiles_root / f"{PROFILE_PREFIX}{user_id}",
                    port=self._alloc_port(),
                    token=secrets.token_urlsafe(32),
                )
                self._children[user_id] = child
        with child.lock:
            if child.proc is None:
                provision_profile(child.profile_dir, seed_skills=self._seed_skills)
                self._spawn_child(child)
        return child

    def check_children_once(self) -> int:
        respawned = 0
        for child in list(self._children.values()):
            with child.lock:
                proc = child.proc
                if proc is None or proc.poll() is None:
                    continue
                healthy_for = time.monotonic() - child.last_spawn
                if healthy_for > self._BACKOFF_RESET_S:
                    child.restarts = 0
                backoff = min(
                    self._backoff_cap,
                    self._backoff_base * (2 ** child.restarts),
                )
                logger.warning(
                    "profile child crashed user=%s port=%d code=%s restart=%d backoff=%.1f",
                    child.user_id,
                    child.port,
                    proc.poll(),
                    child.restarts + 1,
                    backoff,
                )
                self._sleep(backoff)
                child.restarts += 1
                self._spawn_child(child)
                respawned += 1
        return respawned

    def supervise_forever(self, interval: float = 2.0) -> None:
        while True:
            try:
                self.check_children_once()
            except Exception:
                logger.exception("profile child supervision pass failed")
            time.sleep(interval)

    def reconcile_boot(self) -> list[Child]:
        started: list[Child] = []
        if not self.profiles_root.is_dir():
            return started
        for entry in sorted(self.profiles_root.iterdir()):
            if not entry.is_dir() or not entry.name.startswith(PROFILE_PREFIX):
                continue
            user_id = entry.name[len(PROFILE_PREFIX):]
            if not is_valid_user_id(user_id):
                logger.warning("skipping invalid Wheelbase profile dir: %s", entry.name)
                continue
            try:
                started.append(self.ensure_child(user_id))
            except Exception:
                logger.exception("boot reconcile failed for profile %s", entry.name)
        return started


def _router_token() -> str:
    return os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN", "")


def _token_ok(presented: str) -> bool:
    expected = _router_token()
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


def _identity_headers(headers: Any) -> list[tuple[str, str]]:
    return [
        (key, value)
        for key, value in headers.items()
        if key.lower().startswith("x-wheelbase-")
    ]


def _rest_proxy_headers(headers: Any, child_token: str) -> dict[str, str]:
    forwarded: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in _HOP_REQUEST_HEADERS or lower.startswith("x-wheelbase-"):
            continue
        forwarded[key] = value
    for key, value in headers.items():
        lower = key.lower()
        if lower.startswith("x-wheelbase-"):
            forwarded[_WHEELBASE_HEADER_CANONICAL.get(lower, key)] = value
    forwarded["X-Hermes-Session-Token"] = child_token
    return forwarded


def build_app(manager: ChildManager) -> FastAPI:
    app = FastAPI(title="Wheelbase Profile Router")

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def rest_proxy(request: Request, path: str):
        if not _token_ok(request.headers.get("X-Hermes-Session-Token", "")):
            return JSONResponse({"error": "unauthorized"}, status_code=403)
        user_id = request.headers.get("X-Wheelbase-User-Id", "")
        if not is_valid_user_id(user_id):
            return JSONResponse(
                {"error": "missing or invalid X-Wheelbase-User-Id"},
                status_code=403,
            )

        try:
            child = await asyncio.to_thread(manager.ensure_child, user_id)
        except Exception:
            logger.exception("failed to ensure child for REST user=%s", user_id)
            return JSONResponse({"error": "child unavailable"}, status_code=502)

        headers = _rest_proxy_headers(request.headers, child.token)
        url = f"http://127.0.0.1:{child.port}/api/{path}"
        body = await request.body()
        import httpx

        async with httpx.AsyncClient(timeout=_PROXY_TIMEOUT_S) as client:
            upstream = await client.request(
                request.method,
                url,
                params=request.url.query,
                content=body,
                headers=headers,
            )
        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in _HOP_RESPONSE_HEADERS
        }
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
        )

    @app.websocket("/api/ws")
    async def ws_proxy(ws: WebSocket):
        if not _token_ok(ws.query_params.get("token", "")):
            await ws.close(code=4003)
            return
        user_id = ws.headers.get("x-wheelbase-user-id", "")
        if not is_valid_user_id(user_id):
            await ws.close(code=4003)
            return

        try:
            child = await asyncio.to_thread(manager.ensure_child, user_id)
        except Exception:
            logger.exception("failed to ensure child for WS user=%s", user_id)
            await ws.close(code=1011)
            return

        import websockets

        upstream = None
        try:
            upstream = await websockets.connect(
                f"ws://127.0.0.1:{child.port}/api/ws?token={child.token}",
                additional_headers=_identity_headers(ws.headers),
                max_size=None,
            )
        except Exception:
            logger.exception("upstream WS dial failed user=%s port=%s", user_id, child.port)
            await ws.close(code=1011)
            return

        await ws.accept()

        async def client_to_child() -> None:
            try:
                while True:
                    msg = await ws.receive_text()
                    await upstream.send(msg)
            except WebSocketDisconnect:
                logger.debug("client_to_child: client disconnected")

        async def child_to_client() -> None:
            try:
                async for msg in upstream:
                    if isinstance(msg, str):
                        await ws.send_text(msg)
                    else:
                        await ws.send_text(msg.decode("utf-8"))
            except websockets.exceptions.ConnectionClosed:
                logger.debug("child_to_client: upstream connection closed")

        pumps = [
            asyncio.create_task(client_to_child()),
            asyncio.create_task(child_to_client()),
        ]
        try:
            _done, pending = await asyncio.wait(
                pumps,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
        finally:
            for task in pumps:
                if not task.done():
                    task.cancel()
            try:
                await upstream.close()
            except Exception:
                pass
            try:
                await ws.close()
            except Exception:
                pass

    return app


def _uvicorn_serve(app: FastAPI, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


def main(
    serve: Callable[[FastAPI, str, int], None] = _uvicorn_serve,
    *,
    seed_skills: Optional[Callable[[Path], None]] = None,
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not _router_token():
        logger.error("HERMES_DASHBOARD_SESSION_TOKEN must be set for profile router")
        raise SystemExit(2)

    manager = ChildManager(profiles_root(), seed_skills=seed_skills)
    started = manager.reconcile_boot()
    logger.info("boot reconcile started %d profile child process(es)", len(started))
    threading.Thread(
        target=manager.supervise_forever,
        name="profile-router-supervisor",
        daemon=True,
    ).start()

    # Per-user children are dashboard processes that never tick cron; this
    # sweep fires each profile's due jobs regardless of whether its child is
    # alive. Local import avoids a circular dependency (profile_cron imports
    # PROFILE_PREFIX/profiles_root from this module).
    from tui_gateway.profile_cron import CronSweeper

    threading.Thread(
        target=CronSweeper(profiles_root()).sweep_forever,
        name="profile-router-cron-sweep",
        daemon=True,
    ).start()

    port = int(os.environ.get("PORT", "9320"))
    serve(build_app(manager), "0.0.0.0", port)


if __name__ == "__main__":
    main()
