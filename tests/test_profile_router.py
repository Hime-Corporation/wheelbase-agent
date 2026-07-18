"""Profile router tests for Workstream B.

Children are faked at the process seam. HTTP and WS proxy paths use local
loopback stubs so token/header forwarding is covered without starting Hermes.
"""
from __future__ import annotations

import asyncio
import http.server
import json
import threading

import pytest
import yaml
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tui_gateway.profile_router import (
    PORT_RANGE,
    PROFILE_PLUGINS,
    ChildManager,
    build_app,
    provision_profile,
)
from tui_gateway.wheelbase_identity import is_valid_user_id


def test_is_valid_user_id():
    assert is_valid_user_id("4f1c2d3e-aaaa-bbbb-cccc-001122334455")
    assert is_valid_user_id("user_A-1")
    assert not is_valid_user_id("")
    assert not is_valid_user_id("../evil")
    assert not is_valid_user_id("a/b")
    assert not is_valid_user_id("x" * 65)


def test_provision_writes_config_with_plugins(tmp_path):
    seeded = []
    profile_dir = tmp_path / "wb-user-aaaa"
    provision_profile(profile_dir, seed_skills=seeded.append)

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text())
    assert cfg["model"] == "minimax/minimax-m3"
    assert cfg["provider"] == "openrouter"
    assert cfg["skin"] == "wheelbase"
    assert cfg["plugins"]["enabled"] == list(PROFILE_PLUGINS)
    assert "dealership" in (profile_dir / "SOUL.md").read_text().lower()
    for sub in ("skills", "cron", "memories", "sessions", "workspace", "home"):
        assert (profile_dir / sub).is_dir()
    assert seeded == [profile_dir]


def test_main_starts_cron_sweep_thread(tmp_path, monkeypatch):
    import threading

    import tui_gateway.profile_router as pr

    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "tok")
    monkeypatch.setattr(pr, "profiles_root", lambda: tmp_path)

    captured = {}

    def fake_serve(app, host, port):
        captured["thread_names"] = {t.name for t in threading.enumerate()}

    pr.main(serve=fake_serve)

    assert "profile-router-cron-sweep" in captured["thread_names"]


def test_provision_writes_disabled_toolsets(tmp_path):
    from tui_gateway.profile_router import PROFILE_DISABLED_TOOLSETS

    profile_dir = tmp_path / "wb-user-aaaa"
    provision_profile(profile_dir, seed_skills=lambda p: None)

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text())
    assert cfg["agent"]["disabled_toolsets"] == list(PROFILE_DISABLED_TOOLSETS)
    assert "session_search" in cfg["agent"]["disabled_toolsets"]


def test_provision_backfills_disabled_toolsets_for_existing_profile(tmp_path):
    from tui_gateway.profile_router import PROFILE_DISABLED_TOOLSETS

    profile_dir = tmp_path / "wb-user-bbbb"
    profile_dir.mkdir(parents=True)
    # Pre-guard profile: model only, no agent block at all.
    (profile_dir / "config.yaml").write_text("model: custom/model\n")

    provision_profile(profile_dir, seed_skills=lambda p: None)

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text())
    assert cfg["model"] == "custom/model"
    assert cfg["agent"]["disabled_toolsets"] == list(PROFILE_DISABLED_TOOLSETS)


def test_provision_preserves_other_disabled_toolsets(tmp_path):
    profile_dir = tmp_path / "wb-user-ccc1"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        yaml.safe_dump({"agent": {"disabled_toolsets": ["web"]}}, sort_keys=False),
        encoding="utf-8",
    )

    provision_profile(profile_dir, seed_skills=lambda p: None)

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text())
    # Order-preserving union: existing disable kept, session_search appended once.
    assert cfg["agent"]["disabled_toolsets"] == ["web", "session_search"]


def test_provision_preserves_user_edits_but_backfills_plugins(tmp_path):
    profile_dir = tmp_path / "wb-user-aaaa"
    provision_profile(profile_dir, seed_skills=lambda p: None)
    # User customizes model + SOUL; the config (e.g. a pre-cutover profile)
    # carries no plugins.enabled list at all.
    (profile_dir / "config.yaml").write_text("model: custom/model\n")
    (profile_dir / "SOUL.md").write_text("MY SOUL")

    provision_profile(profile_dir, seed_skills=lambda p: None)

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text())
    # Non-plugin user edits are preserved ...
    assert cfg["model"] == "custom/model"
    assert (profile_dir / "SOUL.md").read_text() == "MY SOUL"
    # ... and the mandatory Wheelbase plugins are back-filled so the child
    # actually loads its tools.
    assert cfg["plugins"]["enabled"] == list(PROFILE_PLUGINS)


def test_provision_idempotent_when_config_complete(tmp_path):
    profile_dir = tmp_path / "wb-user-cccc"
    provision_profile(profile_dir, seed_skills=lambda p: None)
    first = (profile_dir / "config.yaml").read_text()

    provision_profile(profile_dir, seed_skills=lambda p: None)

    # A complete config is left byte-for-byte untouched (no needless rewrite).
    assert (profile_dir / "config.yaml").read_text() == first


def test_provision_backfills_partial_plugins_without_duplicates(tmp_path):
    profile_dir = tmp_path / "wb-user-dddd"
    profile_dir.mkdir(parents=True)
    # Stale profile enabled only the original core plugin plus a user plugin.
    (profile_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {"plugins": {"enabled": ["wheelbase-core", "my-custom-plugin"]}},
            sort_keys=False,
        )
    )

    provision_profile(profile_dir, seed_skills=lambda p: None)

    enabled = yaml.safe_load(
        (profile_dir / "config.yaml").read_text()
    )["plugins"]["enabled"]
    # User entry preserved, no duplicate of the already-enabled core plugin,
    # and every required Wheelbase plugin now present.
    assert enabled.count("wheelbase-core") == 1
    assert "my-custom-plugin" in enabled
    assert set(PROFILE_PLUGINS).issubset(set(enabled))


def test_provision_model_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("WHEELBASE_PROFILE_MODEL", "anthropic/claude-x")
    monkeypatch.setenv("WHEELBASE_PROFILE_PROVIDER", "anthropic")
    profile_dir = tmp_path / "wb-user-bbbb"

    provision_profile(profile_dir, seed_skills=lambda p: None)

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text())
    assert cfg["model"] == "anthropic/claude-x"
    assert cfg["provider"] == "anthropic"


def test_default_spawn_passes_isolated_flag(monkeypatch):
    """_default_spawn must pass --isolated so hermes_cli's unified-profile
    re-exec is suppressed and the child keeps its per-profile HERMES_HOME
    instead of being re-execed into the shared machine dashboard. Regression
    guard for the "per-user child has no Wheelbase tools" bug: without
    --isolated the child reads the root config.yaml, where the Wheelbase
    plugins are never enabled.
    """
    captured: list[list[str]] = []

    class _Proc:
        def poll(self):
            return None

    def fake_popen(cmd, *, env, stdin):
        captured.append(list(cmd))
        return _Proc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    from tui_gateway.profile_router import _default_spawn

    _default_spawn("user-aaaa", 9400, {})

    assert len(captured) == 1
    cmd = captured[0]
    assert "--isolated" in cmd
    assert "dashboard" in cmd
    assert "--skip-build" in cmd
    assert "--port" in cmd and "9400" in cmd


class FakeProc:
    def __init__(self):
        self.exit_code = None
        self.killed = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.killed = True


def make_manager(tmp_path, **kw):
    spawned = []

    def spawn(user_id, port, env):
        spawned.append({"user_id": user_id, "port": port, "env": env, "proc": FakeProc()})
        return spawned[-1]["proc"]

    options = {
        "spawn": spawn,
        "wait_ready": lambda port, token: None,
        "seed_skills": lambda p: None,
        **kw,
    }
    mgr = ChildManager(profiles_root=tmp_path, **options)
    return mgr, spawned


TENANT = "tenant-1111"


def test_two_users_route_to_two_different_ports(tmp_path):
    mgr, spawned = make_manager(tmp_path)
    a = mgr.ensure_child(TENANT, "user-aaaa")
    b = mgr.ensure_child(TENANT, "user-bbbb")
    assert a.port != b.port
    assert PORT_RANGE[0] <= a.port <= PORT_RANGE[1]
    assert a.token != b.token and len(a.token) >= 32
    assert mgr.ensure_child(TENANT, "user-aaaa") is a
    assert len(spawned) == 2


def test_ensure_child_provisions_profile_and_env(tmp_path):
    mgr, spawned = make_manager(tmp_path)
    child = mgr.ensure_child(TENANT, "user-aaaa")
    expected_dir = tmp_path / "tenants" / TENANT / "profiles" / "wb-user-aaaa"
    assert child.profile_dir == expected_dir
    assert (child.profile_dir / "config.yaml").exists()
    env = spawned[0]["env"]
    assert env["HERMES_HOME"] == str(expected_dir)
    assert env["HERMES_DASHBOARD_SESSION_TOKEN"] == child.token
    assert env.get("PATH")


def test_ensure_child_rejects_invalid_user_id(tmp_path):
    mgr, _ = make_manager(tmp_path)
    for bad in ("", "../evil", "a b", "x" * 65):
        with pytest.raises(ValueError):
            mgr.ensure_child(TENANT, bad)


def test_ensure_child_thread_safe_single_spawn(tmp_path):
    mgr, spawned = make_manager(tmp_path)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(mgr.ensure_child(TENANT, "user-aaaa")))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(spawned) == 1
    assert all(result.port == results[0].port for result in results)


def test_wait_ready_failure_does_not_cache_unready_child(tmp_path):
    calls = 0

    def wait_ready(port, token):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("not ready")

    mgr, spawned = make_manager(tmp_path, wait_ready=wait_ready)
    with pytest.raises(RuntimeError, match="not ready"):
        mgr.ensure_child(TENANT, "user-aaaa")

    assert spawned[0]["proc"].killed is True
    child = mgr.ensure_child(TENANT, "user-aaaa")
    assert child.proc is spawned[1]["proc"]
    assert len(spawned) == 2


def test_restart_on_crash_with_capped_backoff(tmp_path):
    sleeps = []
    mgr, spawned = make_manager(tmp_path, sleep=sleeps.append)
    child = mgr.ensure_child(TENANT, "user-aaaa")

    for i in range(8):
        child.proc.exit_code = 1
        mgr.check_children_once()
        assert len(spawned) == 2 + i
        assert child.proc.poll() is None

    assert sleeps[0] == pytest.approx(1.0)
    assert sleeps[1] == pytest.approx(2.0)
    assert max(sleeps) <= 60.0
    assert sleeps[-1] == pytest.approx(60.0)


def test_healthy_children_not_restarted(tmp_path):
    mgr, spawned = make_manager(tmp_path)
    mgr.ensure_child(TENANT, "user-aaaa")
    mgr.check_children_once()
    mgr.check_children_once()
    assert len(spawned) == 1


def test_boot_reconcile_starts_existing_profiles(tmp_path):
    (tmp_path / "tenants" / TENANT / "profiles" / "wb-user-aaaa").mkdir(parents=True)
    (tmp_path / "tenants" / "tenant-2222" / "profiles" / "wb-user-bbbb").mkdir(parents=True)
    (tmp_path / "tenants" / TENANT / "profiles" / "stray-dir").mkdir(parents=True)
    (tmp_path / "tenants" / TENANT / "profiles" / "wb-..evil..").mkdir(parents=True)
    (tmp_path / "tenants" / "..evil-tenant" / "profiles" / "wb-user-cccc").mkdir(parents=True)

    mgr, spawned = make_manager(tmp_path)
    started = mgr.reconcile_boot()

    assert {child.user_id for child in started} == {"user-aaaa", "user-bbbb"}
    assert {item["user_id"] for item in spawned} == {"user-aaaa", "user-bbbb"}


class _RecordingChildHTTP(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _handle(self):
                content_length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(content_length) if content_length else b""
                outer.requests.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers),
                        "method": self.command,
                        "body": body,
                    }
                )
                response = json.dumps({"child": "ok", "method": self.command}).encode()
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Child", "yes")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            do_GET = do_POST = _handle

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def router_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "router-secret")
    stub = _RecordingChildHTTP()
    stub.start()
    mgr, _ = make_manager(tmp_path)
    mgr._alloc_port = lambda: stub.port
    client = TestClient(build_app(mgr))
    try:
        yield client, stub, mgr
    finally:
        stub.stop()


def test_rest_rejects_missing_or_bad_token(router_client):
    client, stub, _ = router_client
    assert client.get("/api/cron/list").status_code == 403
    assert client.get(
        "/api/cron/list", headers={"X-Hermes-Session-Token": "wrong"}
    ).status_code == 403
    assert not stub.requests


def test_rest_rejects_missing_or_invalid_user_id(router_client):
    client, stub, _ = router_client
    good = {"X-Hermes-Session-Token": "router-secret"}
    assert client.get("/api/cron/list", headers=good).status_code == 403
    assert client.get(
        "/api/cron/list", headers={**good, "X-Wheelbase-User-Id": "../evil"}
    ).status_code == 403
    assert not stub.requests


def test_rest_proxies_with_child_token_swapped(router_client):
    client, stub, mgr = router_client
    resp = client.post(
        "/api/cron/list?limit=5",
        content=b'{"x":1}',
        headers={
            "Content-Type": "application/json",
            "X-Hermes-Session-Token": "router-secret",
            "X-Wheelbase-User-Id": "user-aaaa",
            "X-Wheelbase-Tenant-Id": TENANT,
        },
    )
    assert resp.status_code == 201
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["x-child"] == "yes"
    assert resp.json() == {"child": "ok", "method": "POST"}
    child = mgr.ensure_child(TENANT, "user-aaaa")
    forwarded = stub.requests[0]
    assert forwarded["path"] == "/api/cron/list?limit=5"
    assert forwarded["body"] == b'{"x":1}'
    assert forwarded["headers"]["X-Hermes-Session-Token"] == child.token
    assert forwarded["headers"]["X-Wheelbase-User-Id"] == "user-aaaa"


class _EchoChildWS(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.upgrades = []
        self.port = None
        self._server_started = threading.Event()
        self._loop = None
        self._stop_future = None

    def run(self):
        async def main():
            import websockets

            async def handler(ws):
                request = getattr(ws, "request", None)
                path = getattr(request, "path", "")
                headers = dict(getattr(request, "headers", {}) or {})
                self.upgrades.append({"path": path, "headers": headers})
                async for msg in ws:
                    await ws.send(f"echo:{msg}")

            async with websockets.serve(handler, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                self._stop_future = asyncio.get_running_loop().create_future()
                self._server_started.set()
                await self._stop_future

        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(main())
        finally:
            self._loop.close()

    def start_and_wait(self):
        self.start()
        assert self._server_started.wait(10)
        assert self.port is not None

    def stop(self):
        if self._loop is not None and self._stop_future is not None:
            self._loop.call_soon_threadsafe(self._stop_future.set_result, None)
        self.join(timeout=5)


@pytest.fixture
def ws_router(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "router-secret")
    stub = _EchoChildWS()
    stub.start_and_wait()
    mgr, _ = make_manager(tmp_path)
    mgr._alloc_port = lambda: stub.port
    try:
        yield TestClient(build_app(mgr)), stub, mgr
    finally:
        stub.stop()


def test_ws_rejects_bad_or_absent_token(ws_router):
    client, stub, _ = ws_router
    for url in ("/api/ws", "/api/ws?token=wrong"):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(url, headers={"X-Wheelbase-User-Id": "user-aaaa"}):
                pass
        assert exc.value.code == 4003
    assert not stub.upgrades


def test_ws_rejects_missing_or_invalid_user_id(ws_router):
    client, stub, _ = ws_router
    for headers in ({}, {"X-Wheelbase-User-Id": "../evil"}):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/ws?token=router-secret", headers=headers):
                pass
        assert exc.value.code == 4003
    assert not stub.upgrades


def test_ws_proxies_frames_and_forwards_identity_headers(ws_router):
    client, stub, mgr = ws_router
    headers = {
        "X-Wheelbase-User-Id": "user-aaaa",
        "X-Wheelbase-Tenant-Id": "t1",
        "X-Wheelbase-Dealership-Id": "d1",
        "X-Wheelbase-User-Jwt": "jwt-a",
        "X-Wheelbase-Cdp-Url": "http://cdp/user-aaaa",
    }
    with client.websocket_connect("/api/ws?token=router-secret", headers=headers) as ws:
        ws.send_text('{"jsonrpc":"2.0","method":"ping","id":1}')
        assert ws.receive_text() == 'echo:{"jsonrpc":"2.0","method":"ping","id":1}'

    child = mgr.ensure_child("t1", "user-aaaa")
    upgrade = stub.upgrades[0]
    assert f"token={child.token}" in upgrade["path"]
    lowered = {key.lower(): value for key, value in upgrade["headers"].items()}
    assert lowered["x-wheelbase-user-id"] == "user-aaaa"
    assert lowered["x-wheelbase-user-jwt"] == "jwt-a"
    assert lowered["x-wheelbase-cdp-url"] == "http://cdp/user-aaaa"
