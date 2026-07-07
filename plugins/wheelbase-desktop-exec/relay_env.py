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

    @staticmethod
    def _embed_stdin_heredoc(command: str, stdin_data: str) -> str:
        """Embed ``stdin_data`` so it reaches the command that actually reads
        stdin, even when that command is NOT the last statement in a
        ``;``-joined multi-statement script.

        The base implementation (``BaseEnvironment._embed_stdin_heredoc``)
        blindly appends ``<< DELIM`` to the END of ``command``. A heredoc
        redirect attaches to whichever statement is syntactically LAST, so
        for a single bare command that's correct — but
        ``ShellFileOperations._atomic_write`` (tools/file_operations.py) ships
        a multi-statement script whose real stdin consumer (``cat >
        "$tmp"``) is followed by ``mv`` and a trailing ``trap - EXIT``. Naive
        appending attaches the heredoc to that trailing ``trap`` (a no-op
        that ignores stdin), so ``cat`` gets immediate EOF and every
        patch/write over this relay silently produces a TRUNCATED
        (zero-byte) file — confirmed by direct repro against the unmodified
        base implementation before this override existed.

        Wrapping the whole script in a ``{ ...; }`` group before appending
        the heredoc fixes this: the group shares one stdin (the heredoc
        body) across every statement inside it, so whichever bare
        (unredirected) command actually reads stdin gets the real data
        regardless of its position — while commands with their own redirect
        (``mv``, ``trap``) are unaffected. Exit-status propagation
        (``__hermes_ec=$?`` in ``_wrap_command``) is unchanged: a ``{ }``
        group (unlike a ``( )`` subshell) still reports the exit status of
        whichever statement inside it last ran (or triggered ``set -e``),
        exactly as the unwrapped script would have.

        Separately: the base implementation unconditionally inserts a ``\n``
        between ``stdin_data`` and the delimiter line. A heredoc terminator
        line MUST start on its own fresh line, so that separator is required
        when ``stdin_data`` does not already end in ``\n`` — but when it DOES
        (as virtually every real text file's content does), the unconditional
        insert adds a REDUNDANT second newline, so the on-disk file ends up
        with one extra trailing blank line versus the intended content. That
        single-byte discrepancy alone is enough to trip
        ``ShellFileOperations.patch_replace``'s own post-write byte-for-byte
        verification (tools/file_operations.py) for the common case of
        content ending in ``\n``, turning nearly every successful edit into a
        reported "Post-write verification failed" error. Only inserting the
        separator when it's actually needed (``stdin_data`` empty or already
        ``\n``-terminated) makes the byte count exact for that common case;
        content that does NOT end in ``\n`` still picks up one extra trailing
        newline — an narrower, inherent heredoc limitation (you cannot
        terminate a heredoc without the terminator starting a fresh line) —
        matching the existing accepted quirk, not a regression.
        """
        delimiter = f"HERMES_STDIN_{uuid.uuid4().hex[:12]}"
        separator = "" if (not stdin_data or stdin_data.endswith("\n")) else "\n"
        return f"{{ {command}\n}} << '{delimiter}'\n{stdin_data}{separator}{delimiter}"

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
