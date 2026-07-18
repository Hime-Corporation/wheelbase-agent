"""Tests for the ``profile`` REST query-param guard in tui_gateway.profile_router.

The dashboard child honors ``?profile=<name>`` on many endpoints to redirect
reads/writes into another profile's directory under the shared profiles root.
The router must strip redundant same-profile values and reject cross-user
ones outright (403), rather than forwarding them verbatim to the child. See
``tests/test_profile_router.py`` for the broader router test suite this
mirrors (recording HTTP stub child, ``make_manager`` fake-spawn pattern).
"""
from __future__ import annotations

import http.server
import json
import threading

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
}
OWN_PROFILE = "wb-user-aaaa"


def test_no_profile_param_forwarded_unchanged(router_client):
    client, stub, _ = router_client
    resp = client.get("/api/cron/list?limit=5", headers=AUTH_HEADERS)
    assert resp.status_code == 201
    assert stub.requests[0]["path"] == "/api/cron/list?limit=5"


def test_own_profile_param_stripped(router_client):
    client, stub, _ = router_client
    resp = client.get(
        f"/api/cron/list?profile={OWN_PROFILE}", headers=AUTH_HEADERS
    )
    assert resp.status_code == 201
    forwarded_path = stub.requests[0]["path"]
    assert "profile" not in forwarded_path
    assert forwarded_path == "/api/cron/list"


def test_current_profile_param_stripped(router_client):
    client, stub, _ = router_client
    resp = client.get("/api/cron/list?profile=current", headers=AUTH_HEADERS)
    assert resp.status_code == 201
    forwarded_path = stub.requests[0]["path"]
    assert "profile" not in forwarded_path
    assert forwarded_path == "/api/cron/list"


def test_empty_profile_param_stripped(router_client):
    client, stub, _ = router_client
    resp = client.get("/api/cron/list?profile=", headers=AUTH_HEADERS)
    assert resp.status_code == 201
    forwarded_path = stub.requests[0]["path"]
    assert "profile" not in forwarded_path


def test_other_user_profile_param_rejected(router_client):
    client, stub, _ = router_client
    resp = client.get(
        "/api/cron/list?profile=wb-user-victim", headers=AUTH_HEADERS
    )
    assert resp.status_code == 403
    assert resp.json() == {"error": "profile parameter not permitted"}
    assert not stub.requests


def test_garbage_profile_param_rejected(router_client):
    client, stub, _ = router_client
    resp = client.get(
        "/api/cron/list?profile=../../etc/passwd", headers=AUTH_HEADERS
    )
    assert resp.status_code == 403
    assert resp.json() == {"error": "profile parameter not permitted"}
    assert not stub.requests


def test_other_params_survive_profile_stripping(router_client):
    client, stub, _ = router_client
    resp = client.get(
        f"/api/cron/list?limit=5&profile={OWN_PROFILE}&foo=bar",
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 201
    forwarded_path = stub.requests[0]["path"]
    assert "profile" not in forwarded_path
    assert "limit=5" in forwarded_path
    assert "foo=bar" in forwarded_path
