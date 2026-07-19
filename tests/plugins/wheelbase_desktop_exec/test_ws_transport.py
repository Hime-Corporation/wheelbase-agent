from __future__ import annotations

import contextlib
import importlib
import json
import threading
import time

import pytest
from websockets.sync.server import serve

transport_mod = importlib.import_module("plugins.wheelbase-desktop-exec.transport")
ws_transport_mod = importlib.import_module("plugins.wheelbase-desktop-exec.ws_transport")

PreDispatchError = transport_mod.PreDispatchError
WebsocketExecTransport = ws_transport_mod.WebsocketExecTransport


@contextlib.contextmanager
def _mock_hub(handler, *, process_request=None):
    """Start a local sync WS server standing in for ExecHub.GatewayConnect."""
    server = serve(handler, "127.0.0.1", 0, process_request=process_request)
    port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"ws://127.0.0.1:{port}/internal/agent/exec/u1/ws?token=cap"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_send_recv_round_trip():
    def handler(ws):
        raw = ws.recv()
        frame = json.loads(raw)
        assert frame["type"] == "exec"
        ws.send(json.dumps({"type": "chunk", "request_id": frame["request_id"], "data": "hi\n"}))
        ws.send(json.dumps({"type": "exit", "request_id": frame["request_id"], "exit_code": 0}))

    with _mock_hub(handler) as url:
        t = WebsocketExecTransport(url, {"user_id": "u1"})
        try:
            t.send({"type": "exec", "request_id": "r1", "command": "echo hi"})
            f1 = t.recv("r1", timeout=5)
            f2 = t.recv("r1", timeout=5)
            assert f1["type"] == "chunk" and f1["data"] == "hi\n"
            assert f2["type"] == "exit" and f2["exit_code"] == 0
        finally:
            t.close()


def test_interleaved_request_ids_demultiplex_correctly():
    """Two logical requests' frames arrive interleaved on one connection —
    recv(request_id) must only ever return frames for its own id."""

    def handler(ws):
        first = json.loads(ws.recv())
        second = json.loads(ws.recv())
        # Interleave: r2's frame, then r1's, then r2's terminal, then r1's.
        ws.send(json.dumps({"type": "chunk", "request_id": second["request_id"], "data": "B-chunk"}))
        ws.send(json.dumps({"type": "chunk", "request_id": first["request_id"], "data": "A-chunk"}))
        ws.send(json.dumps({"type": "exit", "request_id": second["request_id"], "exit_code": 0}))
        ws.send(json.dumps({"type": "exit", "request_id": first["request_id"], "exit_code": 0}))

    with _mock_hub(handler) as url:
        t = WebsocketExecTransport(url, {"user_id": "u1"})
        try:
            t.send({"type": "exec", "request_id": "A", "command": "a"})
            t.send({"type": "exec", "request_id": "B", "command": "b"})

            a_chunk = t.recv("A", timeout=5)
            a_exit = t.recv("A", timeout=5)
            b_chunk = t.recv("B", timeout=5)
            b_exit = t.recv("B", timeout=5)

            assert a_chunk["data"] == "A-chunk" and a_exit["exit_code"] == 0
            assert b_chunk["data"] == "B-chunk" and b_exit["exit_code"] == 0
        finally:
            t.close()


def test_connect_refused_raises_pre_dispatch_error():
    # Nothing listening on this port -> connection refused, matching what a
    # desktop-offline (503) or bad-capability-token (401/403) upgrade
    # rejection looks like from the caller's side: the WS never comes up.
    with pytest.raises(PreDispatchError):
        WebsocketExecTransport("ws://127.0.0.1:1/internal/agent/exec/u1/ws?token=bad", {})


def test_handshake_rejection_raises_pre_dispatch_error():
    def reject(connection, request):
        from websockets.http11 import Response
        return Response(503, "Service Unavailable", {}, b"desktop exec offline")

    def handler(ws):  # pragma: no cover - never reached, handshake is rejected
        raise AssertionError("handler must not run when the handshake is rejected")

    with _mock_hub(handler, process_request=reject) as url:
        with pytest.raises(PreDispatchError):
            WebsocketExecTransport(url, {})


def test_send_after_close_raises_pre_dispatch_error():
    def handler(ws):
        ws.recv()  # keep the connection open briefly, then let the `with` close it

    with _mock_hub(handler) as url:
        t = WebsocketExecTransport(url, {})
        t.close()
        with pytest.raises(PreDispatchError):
            t.send({"type": "exec", "request_id": "r1"})


def test_close_is_idempotent():
    def handler(ws):
        ws.recv()

    with _mock_hub(handler) as url:
        t = WebsocketExecTransport(url, {})
        t.close()
        t.close()  # must not raise


def test_mid_stream_drop_is_not_a_pre_dispatch_error():
    """A command that already dispatched (send() succeeded) but whose
    connection dies before the terminal frame arrives must surface as a
    plain exception from recv() — NEVER PreDispatchError, since re-dispatching
    would risk re-running a command that may already have executed on the
    desktop (spec §5.1 M4)."""

    def handler(ws):
        raw = ws.recv()
        frame = json.loads(raw)
        ws.send(json.dumps({"type": "chunk", "request_id": frame["request_id"], "data": "partial"}))
        # Drop the connection without ever sending a terminal frame.

    with _mock_hub(handler) as url:
        t = WebsocketExecTransport(url, {})
        try:
            t.send({"type": "exec", "request_id": "r1", "command": "x"})
            first = t.recv("r1", timeout=5)
            assert first["data"] == "partial"
            with pytest.raises(Exception) as exc_info:
                t.recv("r1", timeout=5)
            assert not isinstance(exc_info.value, PreDispatchError)
        finally:
            t.close()


def test_recv_times_out_when_no_frame_arrives():
    def handler(ws):
        ws.recv()
        time.sleep(5)  # outlast the short client-side timeout below

    with _mock_hub(handler) as url:
        t = WebsocketExecTransport(url, {})
        try:
            t.send({"type": "exec", "request_id": "r1", "command": "x"})
            with pytest.raises(TimeoutError):
                t.recv("r1", timeout=0.2)
        finally:
            t.close()
