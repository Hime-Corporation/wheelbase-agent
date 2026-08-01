from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _envelope(key: bytes, *, user: str, tenant: str, client: str, device: str = "", revision: int = 1, relay: bool = True) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT", "kid": "fixture-key"}
    payload = {
        "iss": "wheelbase-api",
        "aud": "wheelbase-agent-gateway",
        "kind": "agent_gateway_identity",
        "ver": 2,
        "iat": now,
        "exp": now + 30,
        "nonce": str(uuid.uuid4()),
        "bundle": {
            "user_id": user,
            "tenant_id": tenant,
            "dealership_id": "dealer-a",
            "client": client,
            "device_id": device,
            "session_jti_hash": hashlib.sha256(f"{user}\0{client}\0{device}".encode()).hexdigest(),
            "credential_revision": revision,
            "credential_expires_at": now + 600,
            "access_token": f"secret-{user}-{client}-{revision}",
            "cdp_url": "wss://relay.invalid/cdp" if relay else "",
            "shell_relay_url": "wss://relay.invalid/shell" if relay else "",
        },
    }
    signing = ".".join(
        (
            _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode()),
            _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()),
        )
    )
    return f"{signing}.{_b64url(hmac.new(key, signing.encode(), hashlib.sha256).digest())}"


def _relay_status(*, client: str, device_id: str = "", available: bool = True) -> dict:
    if client == "mobile":
        return {
            "version": 2,
            "client": "mobile",
            "cdp_relay_challenge": "not_applicable",
            "shell_relay_challenge": "not_applicable",
        }
    challenge = "passed" if available else "failed"
    return {
        "version": 2,
        "client": "desktop",
        "device_id": device_id,
        "cdp_relay_challenge": challenge,
        "shell_relay_challenge": challenge,
    }


async def _rpc_flow(
    base_url: str,
    router_token: str,
    key: bytes,
    hermes_home: Path,
) -> None:
    import websockets

    device_a = str(uuid.uuid4())
    device_b = str(uuid.uuid4())
    ws_url = base_url.replace("http://", "ws://") + f"/api/ws?token={router_token}"
    async def connect(*, user: str, client: str, device: str = ""):
        # Mint immediately before each potentially slow production-child boot;
        # signed envelopes intentionally have a bounded 30-second lifetime.
        envelope = _envelope(
            key, user=user, tenant="tenant-a", client=client, device=device
        )
        return await websockets.connect(
            ws_url,
            additional_headers={"X-Wheelbase-Identity-Envelope": envelope},
            open_timeout=60,
        )

    sockets = {
        "a": await connect(user="user-a", client="desktop", device=device_a),
        "mobile": await connect(user="user-a", client="mobile"),
        "b": await connect(user="user-b", client="desktop", device=device_b),
    }
    next_id = 0

    async def request(name: str, method: str, params=None):
        nonlocal next_id
        next_id += 1
        request_id = f"r{next_id}"
        await sockets[name].send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}))
        events = []
        while True:
            frame = json.loads(await asyncio.wait_for(sockets[name].recv(), 5))
            if frame.get("id") == request_id:
                return frame, events
            events.append(frame)

    try:
        probe_a, _ = await request(
            "a",
            "wheelbase.runtime.probe",
            {"relay_status_v2": _relay_status(client="desktop", device_id=device_a)},
        )
        probe_mobile, _ = await request(
            "mobile",
            "wheelbase.runtime.probe",
            {"relay_status_v2": _relay_status(client="mobile")},
        )
        probe_b, _ = await request(
            "b",
            "wheelbase.runtime.probe",
            {
                "relay_status_v2": _relay_status(
                    client="desktop",
                    device_id=device_b,
                )
            },
        )
        assert probe_a["result"]["version"] == 2
        assert probe_a["result"]["relay_challenge"] == {
            "client": "desktop",
            "scope_match": True,
            "cdp_relay_challenge": "passed",
            "shell_relay_challenge": "passed",
        }
        assert probe_a["result"]["desktop_policies"] == {
            surface: {
                "attempted": False,
                "error_code": "challenge_passed",
                "fallback_invocations": 0,
            }
            for surface in ("cdp", "shell")
        }
        assert probe_mobile["result"]["desktop_policies"] == {
            surface: {
                "attempted": False,
                "error_code": "desktop_identity_required",
                "fallback_invocations": 0,
            }
            for surface in ("cdp", "shell")
        }
        assert probe_a["result"]["instance_fingerprint"] == probe_mobile["result"]["instance_fingerprint"]
        assert probe_a["result"]["instance_fingerprint"] != probe_b["result"]["instance_fingerprint"]

        created, _ = await request("a", "session.create")
        session_id = created["result"]["session_id"]
        submitted, events = await request(
            "a",
            "prompt.submit",
            {"session_id": session_id, "text": "production persistence check"},
        )
        assert submitted["result"]["status"] == "streaming"
        event_types = {frame.get("params", {}).get("type") for frame in events}
        generated_title = next(
            (
                str(frame.get("params", {}).get("payload", {}).get("title") or "")
                for frame in events
                if frame.get("params", {}).get("type") == "session.title"
            ),
            "",
        )
        while "message.complete" not in event_types:
            frame = json.loads(await asyncio.wait_for(sockets["a"].recv(), 5))
            event_types.add(frame.get("params", {}).get("type"))
            if frame.get("params", {}).get("type") == "session.title":
                generated_title = str(
                    frame.get("params", {}).get("payload", {}).get("title") or ""
                )
        while not generated_title:
            frame = json.loads(await asyncio.wait_for(sockets["a"].recv(), 10))
            if frame.get("params", {}).get("type") == "session.title":
                generated_title = str(
                    frame.get("params", {}).get("payload", {}).get("title") or ""
                )
        assert 3 <= len(generated_title.split()) <= 7

        profile_dir = hermes_home / "tenants" / "tenant-a" / "profiles" / "wb-user-a"
        state_db = profile_dir / "state.db"
        assert state_db.is_file()
        with sqlite3.connect(state_db) as db:
            row = db.execute(
                "SELECT user_id, title FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        assert row == ("user-a", generated_title)

        resumed, _ = await request("mobile", "session.resume", {"session_id": session_id})
        assert resumed["result"]["session_id"] == session_id
        history, _ = await request(
            "mobile", "session.history", {"session_id": session_id}
        )
        assert history["result"]["count"] >= 2
        roles = [message["role"] for message in history["result"]["messages"]]
        assert "user" in roles and "assistant" in roles
        read_title, _ = await request(
            "mobile", "session.title", {"session_id": session_id}
        )
        assert read_title["result"]["title"] == generated_title
        foreign, _ = await request("b", "session.resume", {"session_id": session_id})
        assert foreign["error"]["code"] == 4007
        override, _ = await request("a", "session.list", {"profile": "../../forbidden"})
        assert override["error"]["code"] == -32602

        update = _envelope(
            key,
            user="user-a",
            tenant="tenant-a",
            client="desktop",
            device=device_a,
            revision=2,
            relay=False,
        )
        await sockets["a"].send(json.dumps({"method": "identity.update", "params": {"identity_envelope": update}}))
        unavailable, _ = await request(
            "a",
            "wheelbase.runtime.probe",
            {
                "relay_status_v2": _relay_status(
                    client="desktop", device_id=device_a, available=False
                )
            },
        )
        assert unavailable["result"]["desktop_policies"] == {
            surface: {
                "attempted": True,
                "error_code": "desktop_unavailable",
                "fallback_invocations": 0,
            }
            for surface in ("cdp", "shell")
        }
        closed, _ = await request("a", "session.close", {"session_id": session_id})
        assert closed["result"]["closed"] is True
        deleted, _ = await request("a", "session.delete", {"session_id": session_id})
        assert deleted["result"]["deleted"] == session_id
    finally:
        await asyncio.gather(*(socket.close() for socket in sockets.values()))


def test_executable_gateway_fixture_contract(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "wheelbase_gateway_fixture.py"
    ready_file = tmp_path / "ready.json"
    hermes_home = tmp_path / "hermes-home"
    key = b"f" * 32
    router_token = "fixture-router-secret"
    process = subprocess.Popen(
        [
            sys.executable,
            str(script),
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--hermes-home",
            str(hermes_home),
            "--ready-file",
            str(ready_file),
        ],
        cwd=repo,
        env={
            **os.environ,
            "PYTHONPATH": str(repo),
            "WHEELBASE_GATEWAY_FIXTURE_ROUTER_TOKEN": router_token,
            "WHEELBASE_GATEWAY_FIXTURE_IDENTITY_KEYS_JSON": json.dumps(
                {"fixture-key": base64.b64encode(key).decode()}
            ),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        command_line = " ".join(str(part) for part in process.args)
        assert router_token not in command_line
        assert base64.b64encode(key).decode() not in command_line
        deadline = time.time() + 15
        while time.time() < deadline and not ready_file.exists() and process.poll() is None:
            time.sleep(0.05)
        assert process.poll() is None, process.stderr.read()
        assert stat.S_IMODE(ready_file.stat().st_mode) == 0o600
        ready = json.loads(ready_file.read_text())
        assert set(ready) == {"version", "base_url"}
        assert ready["version"] == 1
        assert ready["base_url"].startswith("http://127.0.0.1:")
        asyncio.run(_rpc_flow(ready["base_url"], router_token, key, hermes_home))
    finally:
        process.terminate()
        assert process.wait(timeout=10) == 0
        assert not ready_file.exists()
        stderr = process.stderr.read()
        assert router_token not in stderr
        assert base64.b64encode(key).decode() not in stderr
