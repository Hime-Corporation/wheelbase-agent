"""DesktopRelayEnvironment — BaseEnvironment backed by the exec relay.

Mirrors DaytonaEnvironment's SDK-relay pattern (tools/environments/daytona.py):
_run_bash sends an ExecInbound `exec` frame and wraps the streamed
ExecOutbound frames in _ThreadedProcessHandle, so the inherited execute() /
_wait_for_process machinery (cwd markers, interrupt, timeout) works unchanged.
"""
from __future__ import annotations

import uuid
from typing import Any

from tools.environments.base import BaseEnvironment, _ThreadedProcessHandle

from .transport import ExecTransport, PreDispatchError


class DesktopRelayEnvironment(BaseEnvironment):
    # No stdin pipe over the relay — embed stdin as a heredoc like Daytona.
    _stdin_mode = "heredoc"

    def __init__(self, transport: ExecTransport, cwd: str, timeout: int,
                 env: dict | None = None, workspace_root: str = ""):
        super().__init__(cwd=cwd, timeout=timeout, env=env)
        self._transport = transport
        # Desktop workspace jail root (spec §7.2.1 open item: how the cloud
        # turn learns the intended root — carried in identity for now).
        self._workspace_root = workspace_root or cwd

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120, stdin_data: str | None = None):
        request_id = uuid.uuid4().hex
        transport = self._transport
        # Send synchronously so a pre-dispatch failure raises here (→ the
        # middleware's next_call fallback) rather than inside the worker thread.
        transport.send({
            "type": "exec", "request_id": request_id, "kind": "bash",
            "command": cmd_string, "cwd": self.cwd, "env": self.env or {},
            "workspace_root": self._workspace_root,
        })

        def cancel():
            # AFTER dispatch: interrupt the running command. Best-effort.
            try:
                transport.send({"type": "interrupt", "request_id": request_id})
            except Exception:
                pass

        def exec_fn() -> tuple[str, int]:
            chunks: list[str] = []
            while True:
                frame = transport.recv(request_id, timeout=timeout)
                ftype = frame.get("type")
                if ftype == "chunk":
                    chunks.append(frame.get("data") or "")
                elif ftype == "exit":
                    code = frame.get("exit_code")
                    return ("".join(chunks), int(code) if code is not None else 0)
                elif ftype == "result":
                    return (frame.get("data") or "".join(chunks), 0)
                elif ftype == "error":
                    return ("".join(chunks) + "\n" + str(frame.get("message") or "relay error"), 1)
                # unknown frame types are ignored (forward-compat)

        return _ThreadedProcessHandle(exec_fn, cancel_fn=cancel)

    def cleanup(self):
        try:
            self._transport.close()
        except Exception:
            pass
