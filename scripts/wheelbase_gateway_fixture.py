#!/usr/bin/env python3
"""Run a production profile router with deterministic RPC fixture children.

This is an integration-test executable, not a development gateway. It accepts
only loopback listeners, writes a mode-0600 readiness file, never logs identity
envelopes or tokens, and keeps production routing/profile isolation intact.

Secrets are inherited through ``WHEELBASE_GATEWAY_FIXTURE_ROUTER_TOKEN`` and
``WHEELBASE_GATEWAY_FIXTURE_IDENTITY_KEYS_JSON`` so they never appear in the
process command line. The readiness document contains only ``version`` and the
actual loopback ``base_url`` selected for ``--port 0``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import signal
import socket
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tui_gateway.profile_router import ChildManager, build_app
from tui_gateway.wheelbase_identity import load_identity_envelope_keys


def _decode_envelope(raw: str) -> dict[str, object]:
    """Decode the already router-verified envelope forwarded to the child."""
    parts = raw.split(".")
    if len(parts) != 3:
        raise ValueError("invalid forwarded envelope")
    payload = json.loads(
        base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
    )
    bundle = payload.get("bundle") if isinstance(payload, dict) else None
    if not isinstance(bundle, dict):
        raise ValueError("invalid forwarded envelope")
    return bundle


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:12]


class _FixtureChild:
    def __init__(
        self,
        tenant_id: str,
        user_id: str,
        port: int,
        token: str,
        profile: Path,
    ):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.port = port
        self.token = token
        self.profile = profile
        self.sessions: dict[str, dict[str, object]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None
        self._started = threading.Event()
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._started.wait(10):
            raise RuntimeError("fixture child did not start")

    def _run(self) -> None:
        async def serve() -> None:
            import websockets

            async def handler(ws) -> None:
                request = getattr(ws, "request", None)
                path = getattr(request, "path", "")
                query = parse_qs(urlsplit(path).query)
                if query.get("token") != [self.token]:
                    await ws.close(code=4003, reason="unauthorized")
                    return
                headers = getattr(request, "headers", {}) or {}
                envelope = headers.get("X-Wheelbase-Identity-Envelope", "")
                try:
                    identity = _decode_envelope(str(envelope))
                except (ValueError, json.JSONDecodeError, TypeError):
                    await ws.close(code=4003, reason="invalid")
                    return
                if (
                    str(identity.get("tenant_id") or "") != self.tenant_id
                    or str(identity.get("user_id") or "") != self.user_id
                ):
                    await ws.close(code=4003, reason="scope_mismatch")
                    return
                async for raw in ws:
                    if not isinstance(raw, str):
                        continue
                    try:
                        frame = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(frame, dict):
                        continue
                    method = frame.get("method")
                    params = frame.get("params") if isinstance(frame.get("params"), dict) else {}
                    request_id = frame.get("id")
                    if method == "identity.update":
                        try:
                            identity = _decode_envelope(str(params.get("identity_envelope") or ""))
                        except (ValueError, json.JSONDecodeError, TypeError):
                            await ws.close(code=4003, reason="invalid")
                        continue
                    response, events = self._rpc(identity, request_id, str(method or ""), params)
                    await ws.send(json.dumps(response, separators=(",", ":")))
                    for event in events:
                        await ws.send(json.dumps(event, separators=(",", ":")))

            self._server = await websockets.serve(
                handler,
                "127.0.0.1",
                self.port,
                max_size=None,
            )
            self._started.set()
            await self._server.wait_closed()

        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(serve())
        finally:
            self._loop.close()

    @staticmethod
    def _ok(request_id, result: object) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id, code: int, message: str) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def _event(kind: str, session_id: str, payload: dict[str, object]) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": kind, "session_id": session_id, "payload": payload},
        }

    def _rpc(
        self,
        identity: dict[str, object],
        request_id: object,
        method: str,
        params: dict[str, object],
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        if "profile" in params:
            return self._error(request_id, -32602, "profile override not permitted"), []
        if method == "wheelbase.runtime.probe":
            client = str(identity.get("client") or "")
            available = bool(identity.get("cdp_url") or identity.get("shell_relay_url"))
            if client == "mobile":
                attempted, error_code = False, "desktop_identity_required"
            elif available:
                attempted, error_code = False, "desktop_available"
            else:
                attempted, error_code = True, "desktop_unavailable"
            result = {
                "instance_fingerprint": _fingerprint(self.tenant_id, self.user_id, "instance"),
                "profile_fingerprint": _fingerprint(str(self.profile), "profile"),
                "profile_scope_match": (
                    identity.get("tenant_id") == self.tenant_id
                    and identity.get("user_id") == self.user_id
                ),
                "desktop_probe": {
                    "attempted": attempted,
                    "error_code": error_code,
                    "fallback_invocations": 0,
                },
            }
            return self._ok(request_id, result), []
        if method == "session.list":
            rows = [
                {"id": session_id, "title": session["title"]}
                for session_id, session in self.sessions.items()
            ]
            return self._ok(request_id, {"sessions": rows}), []
        if method == "session.create":
            session_id = str(uuid.uuid4())
            self.sessions[session_id] = {"title": "", "messages": [], "closed": False}
            return self._ok(
                request_id,
                {"session_id": session_id, "stored_session_id": session_id},
            ), []
        session_id = str(params.get("session_id") or "")
        session = self.sessions.get(session_id)
        if method in {"session.resume", "session.history", "session.title"}:
            if session is None:
                return self._error(request_id, 4007, "session unavailable"), []
            if method == "session.title":
                return self._ok(request_id, {"session_id": session_id, "title": session["title"]}), []
            return self._ok(
                request_id,
                {"session_id": session_id, "messages": list(session["messages"])},
            ), []
        if method == "prompt.submit":
            if session is None:
                return self._error(request_id, 4007, "session unavailable"), []
            prompt = str(params.get("text") or "")
            title = "Contract smoke acknowledgement"
            session["title"] = title
            session["messages"] = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "Acknowledged."},
            ]
            events = [
                self._event("message.complete", session_id, {"session_id": session_id}),
                self._event(
                    "session.title",
                    session_id,
                    {"session_id": session_id, "title": title},
                ),
            ]
            return self._ok(request_id, {"ok": True, "session_id": session_id}), events
        if method == "session.close":
            if session is None:
                return self._error(request_id, 4007, "session unavailable"), []
            session["closed"] = True
            return self._ok(request_id, {"closed": session_id}), []
        if method == "session.delete":
            if session is None:
                return self._error(request_id, 4007, "session unavailable"), []
            self.sessions.pop(session_id, None)
            return self._ok(request_id, {"deleted": session_id}), []
        return self._error(request_id, -32601, "method not found"), []

    def poll(self):
        return 0 if self._stopped else None

    def terminate(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._loop is not None and self._server is not None:
            self._loop.call_soon_threadsafe(self._server.close)
        self._thread.join(timeout=5)


def _write_ready(path: Path, base_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "base_url": base_url}, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--hermes-home", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    router_token = os.environ.get("WHEELBASE_GATEWAY_FIXTURE_ROUTER_TOKEN", "")
    identity_keys_json = os.environ.get(
        "WHEELBASE_GATEWAY_FIXTURE_IDENTITY_KEYS_JSON", ""
    )
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("fixture host must be loopback")
    if not 0 <= args.port <= 65535 or not router_token or not identity_keys_json:
        raise SystemExit("invalid fixture listener or token")
    if not args.hermes_home.is_absolute() or not args.ready_file.is_absolute():
        raise SystemExit("fixture paths must be absolute")
    args.hermes_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.hermes_home, 0o700)
    os.environ["HERMES_HOME"] = str(args.hermes_home)
    os.environ["HERMES_DASHBOARD_SESSION_TOKEN"] = router_token
    os.environ["AGENT_GATEWAY_IDENTITY_KEYS"] = identity_keys_json
    load_identity_envelope_keys()

    children: list[_FixtureChild] = []

    def spawn(user_id: str, port: int, env: dict[str, str]):
        profile = Path(env["HERMES_HOME"])
        tenant_id = profile.parents[1].name
        child = _FixtureChild(
            tenant_id,
            user_id,
            port,
            env["HERMES_DASHBOARD_SESSION_TOKEN"],
            profile,
        )
        children.append(child)
        return child

    manager = ChildManager(
        profiles_root=args.hermes_home,
        spawn=spawn,
        wait_ready=lambda _port, _token: None,
        seed_skills=lambda _path: None,
    )

    def allocate_child_port() -> int:
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            return int(candidate.getsockname()[1])

    manager._alloc_port = allocate_child_port
    app = build_app(manager)

    listener = socket.socket(socket.AF_INET6 if args.host == "::1" else socket.AF_INET)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.host, args.port))
    listener.listen(128)
    actual_port = int(listener.getsockname()[1])

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
    thread.start()
    deadline = time.time() + 15
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise SystemExit("profile router did not start")
    _write_ready(args.ready_file, f"http://{args.host}:{actual_port}")

    def stop(_signum=None, _frame=None):
        server.should_exit = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    thread.join()
    for child in children:
        child.terminate()
    try:
        args.ready_file.unlink()
    except FileNotFoundError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
