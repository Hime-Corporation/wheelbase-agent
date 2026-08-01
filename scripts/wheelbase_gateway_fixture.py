#!/usr/bin/env python3
"""Run the production profile router and dashboard children for contract tests.

This is an integration-test executable, not a development gateway. It accepts
only loopback listeners, writes a mode-0600 readiness file, never logs identity
envelopes or tokens, and keeps production routing, dashboard RPC handlers, and
profile persistence intact. Deterministic prompt responses use Hermes' existing
loopback OpenAI-compatible inference; session, history, title, runtime probe,
signed-envelope, and profile selection behavior remain production code.

Secrets are inherited through ``WHEELBASE_GATEWAY_FIXTURE_ROUTER_TOKEN`` and
``WHEELBASE_GATEWAY_FIXTURE_IDENTITY_KEYS_JSON`` so they never appear in the
process command line. The readiness document contains only ``version`` and the
actual loopback ``base_url`` selected for ``--port 0``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from tui_gateway.profile_router import ChildManager, _default_spawn, build_app
from tui_gateway.wheelbase_identity import load_identity_envelope_keys


def _completion_text(messages: Any) -> str:
    """Return deterministic inference derived from the production request."""
    rows = messages if isinstance(messages, list) else []
    system = "\n".join(
        str(row.get("content") or "")
        for row in rows
        if isinstance(row, dict) and row.get("role") == "system"
    )
    user = next(
        (
            str(row.get("content") or "")
            for row in reversed(rows)
            if isinstance(row, dict) and row.get("role") == "user"
        ),
        "",
    )
    if "Return ONLY the title text" in system:
        conversation = user.partition("User:")[2].partition("\n\nAssistant:")[0]
        words = [word.strip(".,:;!?()[]{}\"'") for word in conversation.split()]
        words = [word for word in words if word][:5]
        return " ".join(word.capitalize() for word in words)
    return f"Completed production turn for: {user[:120]}"


class _InferenceHandler(BaseHTTPRequestHandler):
    server_version = "WheelbaseFixtureInference/1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: int, payload: object) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path.rstrip("/") == "/v1/models":
            self._json(
                200,
                {"object": "list", "data": [{"id": "wheelbase-fixture-model"}]},
            )
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._json(404, {"error": {"message": "not found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 2_000_000:
                raise ValueError("invalid request length")
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError("invalid request")
        except (ValueError, TypeError, json.JSONDecodeError):
            self._json(400, {"error": {"message": "invalid request"}})
            return

        model = str(request.get("model") or "wheelbase-fixture-model")
        content = _completion_text(request.get("messages"))
        created = int(time.time())
        if request.get("stream") is True:
            chunks = [
                {
                    "id": "chatcmpl-wheelbase-fixture",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": content},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "chatcmpl-wheelbase-fixture",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 8,
                        "completion_tokens": 8,
                        "total_tokens": 16,
                    },
                },
            ]
            body = "".join(
                f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n"
                for chunk in chunks
            ) + "data: [DONE]\n\n"
            raw = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        self._json(
            200,
            {
                "id": "chatcmpl-wheelbase-fixture",
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 8,
                    "total_tokens": 16,
                },
            },
        )


class _InferenceServer:
    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _InferenceHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
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

    inference = _InferenceServer()
    inference.start()

    def spawn_production_child(user_id: str, port: int, env: dict[str, str]):
        """Configure the supported custom-provider boundary, then spawn normally."""
        import yaml

        config_path = Path(env["HERMES_HOME"]) / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        config["model"] = {
            "default": "wheelbase-fixture-model",
            "provider": "custom",
            "base_url": inference.base_url,
            "api_key": "fixture-local-no-secret",
            "api_mode": "chat_completions",
        }
        config_path.write_text(
            yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
        )
        return _default_spawn(user_id, port, env)

    manager = ChildManager(
        profiles_root=args.hermes_home,
        spawn=spawn_production_child,
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
            timeout_graceful_shutdown=5,
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
    # ChildManager intentionally has no public global-shutdown operation: the
    # long-running production router supervises children forever. This bounded
    # executable owns the manager, so reap the real dashboard processes it
    # launched before removing readiness and exiting.
    with manager._lock:
        children = list(manager._children.values())
    for child in children:
        proc = child.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
    shutdown_deadline = time.monotonic() + 4
    for child in children:
        proc = child.proc
        if proc is None:
            continue
        try:
            proc.wait(timeout=max(0.05, shutdown_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for child in children:
        proc = child.proc
        if proc is not None and proc.poll() is None:
            proc.kill()
    for child in children:
        proc = child.proc
        if proc is not None:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    inference.stop()
    try:
        args.ready_file.unlink()
    except FileNotFoundError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
