"""Tenant-keying tests for ``tui_gateway.profile_router``.

The router used to key per-user children on ``user_id`` alone, with each
child's ``HERMES_HOME`` at ``<profiles_root>/wb-<uid>``. This module covers
the move to a composite ``(tenant_id, user_id)`` key, with child
``HERMES_HOME`` nested at
``<router HERMES_HOME>/tenants/<tenant_id>/profiles/wb-<user_id>``.

Follows the fixture style of ``tests/test_profile_router.py`` (fake-spawn
``make_manager`` helper, recording HTTP/WS stub children) and
``tests/gateway/test_profile_router_profile_param.py``.

``tui_gateway.tenant_migration`` is being written in parallel by another
workstream; we code against its documented contract
(``run_tenant_migration(hermes_root, supabase_url, supabase_key) ->
MigrationReport`` with ``migrated``/``orphaned``/``skipped``/``errors``
fields) and stub the module in ``sys.modules`` rather than importing the
real thing.
"""
from __future__ import annotations

import http.server
import json
import sys
import threading
import types
from dataclasses import dataclass, field

import pytest
from starlette.testclient import TestClient

from tui_gateway.profile_router import ChildManager, build_app


class FakeProc:
    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


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


class _RecordingChildHTTP(threading.Thread):
    """Loopback HTTP stub standing in for a per-user dashboard child."""

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


AUTH_HEADERS = {
    "X-Hermes-Session-Token": "router-secret",
    "X-Wheelbase-User-Id": "user-aaaa",
    "X-Wheelbase-Tenant-Id": "tenant-one",
}


# --- (a) child spawned under tenants/<tid>/profiles/wb-<uid> ---------------


def test_ensure_child_nests_under_tenant(tmp_path):
    mgr, spawned = make_manager(tmp_path)
    child = mgr.ensure_child("tenant-one", "user-aaaa")

    assert child.tenant_id == "tenant-one"
    assert child.user_id == "user-aaaa"
    assert child.profile_dir == (
        tmp_path / "tenants" / "tenant-one" / "profiles" / "wb-user-aaaa"
    )
    assert (child.profile_dir / "config.yaml").exists()
    # parent.name == "profiles" must hold for hermes_constants.py's walk-up.
    assert child.profile_dir.parent.name == "profiles"
    env = spawned[0]["env"]
    assert env["HERMES_HOME"] == str(child.profile_dir)


# --- (b) same user id under two tenants -> two distinct children/dirs ------


def test_same_user_id_different_tenants_get_distinct_children(tmp_path):
    mgr, spawned = make_manager(tmp_path)
    a = mgr.ensure_child("tenant-one", "user-aaaa")
    b = mgr.ensure_child("tenant-two", "user-aaaa")

    assert a is not b
    assert a.port != b.port
    assert a.token != b.token
    assert a.profile_dir != b.profile_dir
    assert a.profile_dir == tmp_path / "tenants" / "tenant-one" / "profiles" / "wb-user-aaaa"
    assert b.profile_dir == tmp_path / "tenants" / "tenant-two" / "profiles" / "wb-user-aaaa"
    assert len(spawned) == 2
    # Same (tenant, user) key returns the cached child, not a third spawn.
    assert mgr.ensure_child("tenant-one", "user-aaaa") is a
    assert len(spawned) == 2


def test_ensure_child_rejects_invalid_tenant_id(tmp_path):
    mgr, _ = make_manager(tmp_path)
    for bad in ("", "../evil", "a b", "x" * 65):
        with pytest.raises(ValueError):
            mgr.ensure_child(bad, "user-aaaa")


# --- (c) missing tenant header -> 403 ---------------------------------------


def test_rest_rejects_missing_tenant_header(router_client):
    client, stub, _ = router_client
    headers = {
        "X-Hermes-Session-Token": "router-secret",
        "X-Wheelbase-User-Id": "user-aaaa",
    }
    resp = client.get("/api/cron/list", headers=headers)
    assert resp.status_code == 403
    assert "Tenant" in resp.json()["error"]
    assert not stub.requests


# --- (d) invalid tenant header -> 403 ---------------------------------------


def test_rest_rejects_invalid_tenant_header(router_client):
    client, stub, _ = router_client
    headers = {
        "X-Hermes-Session-Token": "router-secret",
        "X-Wheelbase-User-Id": "user-aaaa",
        "X-Wheelbase-Tenant-Id": "../evil",
    }
    resp = client.get("/api/cron/list", headers=headers)
    assert resp.status_code == 403
    assert "Tenant" in resp.json()["error"]
    assert not stub.requests


def test_rest_proxies_with_valid_tenant_and_user(router_client):
    client, stub, mgr = router_client
    resp = client.post(
        "/api/cron/list?limit=5",
        content=b'{"x":1}',
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 201
    child = mgr.ensure_child("tenant-one", "user-aaaa")
    forwarded = stub.requests[0]
    assert forwarded["headers"]["X-Hermes-Session-Token"] == child.token
    assert forwarded["headers"]["X-Wheelbase-User-Id"] == "user-aaaa"
    assert forwarded["headers"]["X-Wheelbase-Tenant-Id"] == "tenant-one"


class _EchoChildWS(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.upgrades = []
        self.port = None
        self._server_started = threading.Event()
        self._loop = None
        self._stop_future = None

    def run(self):
        import asyncio

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


def test_ws_rejects_missing_or_invalid_tenant_header(ws_router):
    from starlette.websockets import WebSocketDisconnect

    client, stub, _ = ws_router
    for headers in (
        {"X-Wheelbase-User-Id": "user-aaaa"},
        {"X-Wheelbase-User-Id": "user-aaaa", "X-Wheelbase-Tenant-Id": "../evil"},
    ):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/ws?token=router-secret", headers=headers):
                pass
        assert exc.value.code == 4003
    assert not stub.upgrades


def test_ws_proxies_with_composite_key(ws_router):
    client, stub, mgr = ws_router
    headers = {
        "X-Wheelbase-User-Id": "user-aaaa",
        "X-Wheelbase-Tenant-Id": "tenant-one",
    }
    with client.websocket_connect("/api/ws?token=router-secret", headers=headers) as ws:
        ws.send_text("hi")
        assert ws.receive_text() == "echo:hi"

    child = mgr.ensure_child("tenant-one", "user-aaaa")
    upgrade = stub.upgrades[0]
    assert f"token={child.token}" in upgrade["path"]
    lowered = {key.lower(): value for key, value in upgrade["headers"].items()}
    assert lowered["x-wheelbase-tenant-id"] == "tenant-one"


# --- (e) boot reconcile discovers nested profiles, skips garbage -----------


def test_boot_reconcile_discovers_nested_profiles_and_skips_garbage(tmp_path):
    # Valid nested profiles for two tenants.
    (tmp_path / "tenants" / "tenant-one" / "profiles" / "wb-user-aaaa").mkdir(parents=True)
    (tmp_path / "tenants" / "tenant-two" / "profiles" / "wb-user-bbbb").mkdir(parents=True)
    # Garbage: invalid tenant dir name.
    (tmp_path / "tenants" / "../evil" / "profiles" / "wb-user-cccc").mkdir(parents=True)
    # Garbage: tenant dir with no profiles/ subdir.
    (tmp_path / "tenants" / "tenant-three").mkdir(parents=True)
    # Garbage: non-wb-* dir inside a valid tenant's profiles/.
    (tmp_path / "tenants" / "tenant-one" / "profiles" / "stray-dir").mkdir(parents=True)
    # Garbage: invalid user id under a valid tenant.
    (tmp_path / "tenants" / "tenant-one" / "profiles" / "wb-..evil..").mkdir(parents=True)
    # Stray file (not a dir) directly under tenants/.
    (tmp_path / "tenants" / "not-a-dir.txt").write_text("x")

    mgr, spawned = make_manager(tmp_path)
    started = mgr.reconcile_boot()

    assert {(c.tenant_id, c.user_id) for c in started} == {
        ("tenant-one", "user-aaaa"),
        ("tenant-two", "user-bbbb"),
    }
    assert len(spawned) == 2
    for child in started:
        assert child.profile_dir.parent.name == "profiles"


def test_boot_reconcile_no_tenants_dir_returns_empty(tmp_path):
    mgr, spawned = make_manager(tmp_path)
    started = mgr.reconcile_boot()
    assert started == []
    assert spawned == []


# --- (f) migration function invoked once at startup (stubbed) --------------


@dataclass
class _FakeMigrationReport:
    migrated: int = 3
    orphaned: int = 1
    skipped: int = 2
    errors: list = field(default_factory=list)


@pytest.fixture
def stub_tenant_migration(monkeypatch):
    """Install a fake ``tui_gateway.tenant_migration`` module.

    The real module (``run_tenant_migration(hermes_root, supabase_url,
    supabase_key) -> MigrationReport``) is being built in parallel by another
    workstream and does not exist yet in this checkout, so profile_router's
    startup import would fail without this stub.
    """
    calls = []

    def fake_run_tenant_migration(*, hermes_root, supabase_url, supabase_key):
        calls.append(
            {
                "hermes_root": hermes_root,
                "supabase_url": supabase_url,
                "supabase_key": supabase_key,
            }
        )
        return _FakeMigrationReport()

    fake_module = types.ModuleType("tui_gateway.tenant_migration")
    fake_module.run_tenant_migration = fake_run_tenant_migration
    monkeypatch.setitem(sys.modules, "tui_gateway.tenant_migration", fake_module)
    return calls


def test_main_invokes_tenant_migration_once_before_reconcile(
    tmp_path, monkeypatch, stub_tenant_migration
):
    import tui_gateway.profile_router as pr

    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "tok")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-key")
    monkeypatch.setattr(pr, "profiles_root", lambda: tmp_path / "flat")
    monkeypatch.setattr(pr, "hermes_home_root", lambda: tmp_path)

    def fake_serve(app, host, port):
        pass

    pr.main(serve=fake_serve)

    assert len(stub_tenant_migration) == 1
    call = stub_tenant_migration[0]
    assert call["hermes_root"] == tmp_path
    assert call["supabase_url"] == "https://example.supabase.co"
    assert call["supabase_key"] == "svc-key"


def test_main_logs_migration_summary(tmp_path, monkeypatch, stub_tenant_migration, caplog):
    import logging

    import tui_gateway.profile_router as pr

    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "tok")
    monkeypatch.setattr(pr, "profiles_root", lambda: tmp_path / "flat")
    monkeypatch.setattr(pr, "hermes_home_root", lambda: tmp_path)

    def fake_serve(app, host, port):
        pass

    with caplog.at_level(logging.INFO, logger="tui_gateway.profile_router"):
        pr.main(serve=fake_serve)

    assert any("tenant migration" in record.message for record in caplog.records)
    assert any("migrated=3" in record.message for record in caplog.records)
