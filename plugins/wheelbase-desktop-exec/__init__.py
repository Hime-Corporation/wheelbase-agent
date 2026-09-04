"""wheelbase-desktop-exec — route tool execution to a desktop over the exec relay.

Standalone Hermes plugin (spec §5.1). Registers a ``tool_execution`` middleware
that, for a desktop user who is online (identity carries shell_relay_url), runs
the built-in per-tool safety chain (spec §5.5) then relays the operation to the
user's machine. Mobile-origin users (no relay URL) use the sandboxed cloud
path; desktop-origin users with a missing/dead relay fail closed. Zero
upstream-core edits.

All calls for a given (task_id, relay_url) share ONE transport, cached at
module scope (see the cache above ``_make_transport``) — not a fresh dial
per call. A stale/closed entry rebuilds transparently on the next call, and
a send that fails against a cached entry (never reached the desktop) is
retried once against a freshly built one.

All 7 built-in tools route to the desktop when a relay url is present:
  * terminal/process        → ``_relay_command`` (bash `exec` frame).
  * write_file, and read_file for ordinary text → ``_relay_file`` (`read`/
    `write` frames). read_file for a structured document (.ipynb/.docx/.xlsx
    — ``tools/read_extract.py``'s EXTRACTABLE_EXTENSIONS) goes through
    ``_relay_read_extract`` instead: the raw `read` frame does a UTF-8
    decode on the sidecar, which turns a ZIP-based document into mojibake.
  * patch/search_files       → ``_relay_file_ops``: wraps a
    ``DesktopRelayEnvironment`` in the same ``ShellFileOperations`` the
    built-in tools use, so the fuzzy-match/rg/grep logic runs unchanged and
    just emits `exec` frames over the relay. Results are serialized with the
    SAME envelope shape as ``tools/file_tools.py``'s ``patch_tool``/
    ``search_tool`` so the model reads identical output whether local or
    cloud.
  * execute_code             → ``_relay_execute_code``: injects the
    ``DesktopRelayEnvironment`` into the shared terminal-tool env cache
    (``tools.terminal_tool._active_environments``) under the SAME key the
    built-in ``execute_code`` → ``_execute_remote`` → ``_get_or_create_env``
    looks up (``_resolve_container_task_id(task_id)``), then delegates to the
    built-in tool via ``next_call`` so it runs its own guard
    (check_execute_code_guard) and post-processing against the injected env.
    The plugin does NOT guard or post-process this path itself — see
    ``_relay_execute_code`` for why double-guarding/double-post-processing
    would be wrong.

Open items (handed to the Go ExecHub / Bun-sidecar plan):
  * _make_transport / ws_transport.py: authenticated WS dial to
    backend /v1/agent/exec carrying the signed capability token (spec §5.3 B2).
  * workspace_root delivery: currently read from identity["workspace_root"];
    the cloud turn must populate it per conversation (spec §7.2.1 open item).
  * approval round-trip (spec §7.1 M1): check_all_command_guards resolves the
    gateway approval callback internally via get_current_session_key /
    _gateway_notify_cbs — must be validated E2E before shipping destructive
    local exec (dormant-under-daytona path, first activated here).
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Tools whose execution we route to the desktop over the local relay — all 7
# built-in tools. terminal/process/read_file/write_file map directly onto the
# sidecar's bash/read/write primitives (_relay_command / _relay_file).
# patch/search_files route through _relay_file_ops (ShellFileOperations over a
# DesktopRelayEnvironment — same fuzzy-match/rg/grep logic as the built-in
# tools, emitting `exec` frames). execute_code routes through
# _relay_execute_code (terminal-tool env-cache injection + next_call
# delegation to the built-in guard/dispatch) — see module docstring.
ROUTED_TOOLS = frozenset({
    "terminal", "process", "read_file", "write_file",
    "patch", "search_files", "execute_code",
})


def register(ctx) -> None:
    ctx.register_middleware("tool_execution", _middleware_entry)


def _middleware_entry(**kwargs: Any) -> Any:
    """Adapt the framework's kwargs bag to route_or_passthrough."""
    return route_or_passthrough(
        tool_name=kwargs.get("tool_name") or "",
        args=kwargs.get("args") or {},
        next_call=kwargs["next_call"],
        task_id=kwargs.get("task_id") or "",
        session_id=kwargs.get("session_id") or "",
        tool_call_id=kwargs.get("tool_call_id") or "",
        turn_id=kwargs.get("turn_id") or "",
        api_request_id=kwargs.get("api_request_id") or "",
    )


def route_or_passthrough(
    *,
    tool_name: str,
    args: dict,
    next_call: Callable[[dict], Any],
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    turn_id: str = "",
    api_request_id: str = "",
) -> Any:
    # 1. Not a routed tool → passthrough.
    if tool_name not in ROUTED_TOOLS:
        return next_call(args)

    # 2. Fail closed on empty task_id or missing identity (spec §5.1.2, N3).
    if not task_id:
        return next_call(args)
    from wheelbase_sdk.runtime import get_task_identity
    identity = get_task_identity(task_id)
    if not identity:
        return next_call(args)

    # 3. Origin policy is immutable for the session. Mobile starts in cloud;
    # desktop must remain on its exact originating peer even if the advertised
    # relay disappears between mint and dispatch.
    relay_url = (identity.get("shell_relay_url") or "").strip()
    if not relay_url:
        if identity.get("client") == "desktop":
            return _desktop_unavailable()
        return next_call(args)

    # 3b. execute_code is a DEDICATED branch, ahead of the generic safety/relay
    # steps below: it must NOT be pre-guarded here (the built-in execute_code
    # handler runs check_execute_code_guard itself — guarding twice would
    # double-prompt) and it dispatches via next_call (delegating to the
    # built-in handler against an injected env), never via _relay.
    if tool_name == "execute_code":
        return _relay_execute_code(args, relay_url, identity, next_call,
                                   task_id=task_id, tool_call_id=tool_call_id)

    # 4. Reproduce the built-in safety chain (spec §5.5). A block returns the
    #    block result and neither relays nor calls next_call. If the guard
    #    itself RAISES (rather than returning a block dict), fail CLOSED with a
    #    tool-error deny — never let the exception propagate, which would let the
    #    framework auto-run the tool on cloud Daytona instead of the spec's DENY.
    try:
        block = _safety_block(tool_name, args, task_id, session_id)
    except Exception as exc:
        logger.warning("safety guard raised for %s; failing closed: %s",
                       tool_name, exc)
        return _tool_error(f"desktop exec denied: safety guard error: {exc}")
    if block is not None:
        return block

    # 5. Relay. Every relay failure returns stable desktop_unavailable. A
    #    desktop-origin operation is never replayed through next_call.
    return _relay(tool_name, args, relay_url, identity, next_call,
                  task_id=task_id, session_id=session_id, tool_call_id=tool_call_id,
                  turn_id=turn_id, api_request_id=api_request_id)


# terminal/process go through the shell command guards. execute_code does NOT
# (it runs arbitrary Python that never passes through the shell
# DANGEROUS_PATTERNS) — it takes the dedicated whole-script code guard instead;
# see _safety_block_execute_code.
_SHELL_TOOLS = frozenset({"terminal", "process"})


def _extract_command(tool_name: str, args: dict) -> str:
    """Best-effort command text for shell-command guard evaluation."""
    return str(args.get("command") or args.get("cmd") or "")


def _blocked_result(decision: dict, command: str) -> str:
    """Terminal-tool-shaped block result (mirrors terminal_tool.py:2290-2337)."""
    if decision.get("status") == "pending_approval":
        return json.dumps({
            "output": "", "exit_code": -1, "error": "",
            "status": "pending_approval", "approval_pending": True,
            "command": decision.get("command", command),
            "description": decision.get("description", "command flagged"),
            "pattern_key": decision.get("pattern_key", ""),
        }, ensure_ascii=False)
    desc = decision.get("description", "command flagged")
    return json.dumps({
        "output": "", "exit_code": -1,
        "error": decision.get("message") or f"Command denied: {desc}.",
        "status": "blocked",
    }, ensure_ascii=False)


def _safety_block(tool_name, args, task_id, session_id):
    if tool_name == "execute_code":
        # execute_code needs its DEDICATED guard, not the shell command guards.
        return _safety_block_execute_code(args)
    if tool_name in _SHELL_TOOLS:
        from tools.approval import check_all_command_guards
        command = _extract_command(tool_name, args)
        # env_type="desktop-relay" is deliberately NON-container so the guard
        # does not short-circuit (spec §5.5 N1). has_host_access=True keeps the
        # normal flow. The gateway approval callback is resolved internally by
        # check_all_command_guards via get_current_session_key /
        # _gateway_notify_cbs (tools/approval.py:2447); fail-closed if absent.
        decision = check_all_command_guards(
            command, env_type="desktop-relay", has_host_access=True,
        )
        if not decision.get("approved"):
            return _blocked_result(decision, command)
        return None
    # file family handled in Task 6
    return _safety_block_files(tool_name, args, task_id)


def _execute_code_blocked_result(guard: dict) -> str:
    """Block/pending result shaped exactly like the built-in execute_code
    handler (tools/code_execution_tool.py:1160-1166) so the model reads the
    same load-bearing ``error`` key. check_execute_code_guard collapses its
    pending/blocked/denied states into ``approved: False`` + ``message``; the
    built-in serializes that message under ``status: error`` — mirror it here.
    """
    return json.dumps({
        "status": "error",
        "error": guard.get("message") or "execute_code blocked by approval guard.",
        "tool_calls_made": 0,
        "duration_seconds": 0,
    }, ensure_ascii=False)


def _safety_block_execute_code(args):
    """Guard an execute_code script with the DEDICATED code guard.

    execute_code runs arbitrary local Python — the script can call subprocess,
    os.system, ctypes, etc. directly, none of which pass through terminal() /
    the shell DANGEROUS_PATTERNS. Routing it through check_all_command_guards
    (which only scans the code TEXT as a shell string) would let any script
    that doesn't textually trip a shell regex run on the user's real machine
    with NO approval. The built-in handler uses check_execute_code_guard, whose
    primary protection is a whole-script approval prompt; mirror it exactly
    (tools/code_execution_tool.py:1155-1166).
    """
    from tools.approval import check_execute_code_guard
    # execute_code's code arg is named "code" (tools/code_execution_tool.py).
    code = str(args.get("code") or "")
    # env_type="desktop-relay" is deliberately NON-container so the guard does
    # not short-circuit via the container fast-path. has_host_access=True keeps
    # the normal flow. Like the shell branch, the gateway approval callback is
    # resolved internally by check_execute_code_guard via get_current_session_key
    # / _gateway_notify_cbs (tools/approval.py:2682,2728); fail-closed if absent.
    guard = check_execute_code_guard(
        code, "desktop-relay", has_host_access=True,
    )
    if not guard.get("approved", False):
        return _execute_code_blocked_result(guard)
    return None


def _file_path(args: dict) -> str:
    return str(args.get("path") or args.get("file_path") or args.get("root") or "")


def _file_block_result(error: str) -> str:
    return json.dumps({"status": "error", "error": error, "success": False},
                      ensure_ascii=False)


def _safety_block_v4a_patch(patch_content: str, task_id: str) -> str | None:
    """Guard every path referenced by a V4A (``mode="patch"``) patch call.

    ``_file_path`` only reads ``args["path"]`` — a V4A patch carries no such
    arg; its targets live INSIDE ``args["patch"]`` under
    ``*** Update/Add/Delete/Move File:`` headers. Without this, a V4A patch
    call reaches ``_safety_block_files`` with ``path == ""`` and neither
    ``_check_sensitive_path`` nor traversal rejection ever sees the real
    target(s) — a `patch` targeting ``~/.hermes/config.yaml`` or escaping the
    workspace via a ``../../..`` header would relay straight to the desktop.

    Mirrors the built-in ``patch_tool``'s V4A path-extraction + guards
    EXACTLY — same header regexes, same order (traversal check, then
    sensitive-path check via the SAME ``has_traversal_component`` /
    ``_check_sensitive_path`` helpers it calls) — so anything the built-in
    would block locally is blocked here too, before dispatch to the desktop
    (tools/file_tools.py ``patch_tool`` ~1575-1623).
    """
    import re

    from tools.file_tools import _check_sensitive_path
    from tools.path_security import has_traversal_component

    v4a_paths: list[str] = []
    for m in re.finditer(r'^\*\*\*\s*(?:Update|Add|Delete)\s+File:\s*(.+)$',
                         patch_content, re.MULTILINE):
        v4a_paths.append(m.group(1).strip())
    for m in re.finditer(r'^\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)$',
                         patch_content, re.MULTILINE):
        v4a_paths.append(m.group(1).strip())
        v4a_paths.append(m.group(2).strip())

    for v4a_path in v4a_paths:
        if has_traversal_component(v4a_path):
            return _file_block_result(
                f"V4A patch header contains '..' traversal: {v4a_path!r}. "
                "Use the agent's cwd-relative path (no '..') or an absolute "
                "path in '*** Update File:' / '*** Add File:' / "
                "'*** Delete File:' / '*** Move File:' headers."
            )
        err = _check_sensitive_path(v4a_path, task_id)
        if err:
            return _file_block_result(err)
    return None


def _safety_block_files(tool_name, args, task_id):
    path = _file_path(args)
    if tool_name in ("write_file", "patch"):
        from tools.file_tools import _check_sensitive_path
        err = _check_sensitive_path(path, task_id)
        if err:
            return _file_block_result(err)
        if tool_name == "patch" and args.get("mode") == "patch":
            # V4A patch: path lives in args["patch"], not args["path"] — see
            # _safety_block_v4a_patch for why this is a SEPARATE check.
            v4a_block = _safety_block_v4a_patch(args.get("patch") or "", task_id)
            if v4a_block:
                return v4a_block
        return None
    if tool_name in ("read_file", "search_files"):
        # NOTE: the read-block runs on the RAW arg path. The built-in
        # pre-resolves a relative path against the task's terminal cwd
        # (TERMINAL_CWD) before checking the denylist; that cwd lives on the
        # desktop and is not knowable cloud-side, so a relative input like
        # ".env" resolved against a different cwd could miss here. Full
        # read-path parity is an accepted cloud-side limitation — the true
        # enforcement is defense-in-depth in the desktop sidecar, which
        # re-checks against the real resolved path before any read.
        from agent.file_safety import get_read_block_error
        err = get_read_block_error(path)
        if err:
            return _file_block_result(err)
        return None
    return None


import threading

# Idempotency cache keyed by (task_id, tool_call_id) (spec §5.1 M2). The
# concurrent executor double-wraps the middleware; a second invocation for the
# same call must return the first result, never re-dispatch. The key MUST
# include task_id: the shared-dashboard fallback runs multiple users in one
# process, so a reused tool_call_id across tasks must NOT serve user A's output
# to user B.
_result_cache: dict[str, Any] = {}
_result_cache_lock = threading.Lock()
_MAX_CACHE = 256

# Bash-command tools relayed via _relay_command. execute_code is NOT here —
# it never reaches _relay at all (dedicated route_or_passthrough branch,
# step 3b). search_files is NOT here either — it goes through
# _relay_file_ops (ShellFileOperations), not the raw command frame.
_SHELL_FAMILY = frozenset({"terminal", "process"})
# Whole-content read/write tools relayed via _relay_file. patch is NOT here —
# it goes through _relay_file_ops (ShellFileOperations), whose fuzzy-match
# arg shape (old_string/new_string/patch) does not map onto _relay_file's
# path/data frame.
_FILE_FAMILY = frozenset({"read_file", "write_file"})


def _make_transport(relay_url: str, identity: dict):
    """Build the ExecTransport for this call. Production wiring (WS → Go
    ExecHub) replaces this seam; it is monkeypatched to a fake in tests."""
    from .ws_transport import WebsocketExecTransport  # not imported until wired
    return WebsocketExecTransport(relay_url, identity)


# Shared relay-transport cache: one WebSocket per (task_id, relay_url)
# instead of one per relayed tool call.
#
# Before this, _relay and _relay_execute_code each called _make_transport()
# on EVERY call. Two defects followed: (a) _relay had no `finally` on its
# success path, so a relayed terminal/read_file/write_file/patch/search_files
# call that succeeded never closed the socket it opened — every one leaked a
# WebSocket plus its ws_transport.py reader thread. (b) the Go ExecHub's
# GatewayConnect allows exactly ONE gateway connection per desktop and evicts
# whatever was there before with a bare Close() and no close handshake
# (agent_exec.go GatewayConnect) the instant a second dial arrives for the
# same desktop — so two back-to-back relayed calls for the same task raced
# their own transports into existence, and the loser's connection died
# mid-flight with "no close frame was received or sent" (surfaced at
# ws_transport.py's send() as "desktop exec relay send failed: ..."). This
# was a real reported production symptom, not a theoretical one.
#
# WebsocketExecTransport was already built to multiplex — its reader thread
# buckets frames by request_id (see ws_transport.py) — specifically so more
# than one in-flight request can share a connection. The fix is to actually
# do that: hand out the SAME transport to every call for a given
# (task_id, relay_url) instead of dialing a new one each time.
#
# No task-teardown hook exists to close a task's transport the moment its
# desktop session ends — checked wheelbase_sdk.runtime (set_task_identity /
# release_task / clear_task) and hermes_cli.plugins.VALID_HOOKS; nothing
# fires per-task-id on release that a plugin can register against. Rather
# than invent one, eviction here is bounded instead: an evict-oldest cap
# closes the least-recently-used connection once the process is juggling
# _MAX_TRANSPORT_CACHE of them, the same shape as the _result_cache above.
_transport_cache: "OrderedDict[tuple[str, str], Any]" = OrderedDict()
_transport_cache_lock = threading.Lock()
_MAX_TRANSPORT_CACHE = 64


def _acquire_transport(task_id: str, relay_url: str, identity: dict) -> tuple[Any, bool]:
    """Return (transport, from_cache) for (task_id, relay_url).

    Reuses a live cached transport when one exists. "Live" means its
    ``_closed`` flag is not set — WebsocketExecTransport sets that in
    close(); FakeTransport in tests has no such attribute, so a missing flag
    is treated as live via getattr's default. A closed entry is evicted here
    so the fresh build below replaces it under the same key rather than
    piling up a second entry.
    """
    key = (task_id, relay_url)
    with _transport_cache_lock:
        cached = _transport_cache.get(key)
        if cached is not None:
            if not getattr(cached, "_closed", False):
                _transport_cache.move_to_end(key)
                return cached, True
            del _transport_cache[key]
    transport = _make_transport(relay_url, identity)
    _store_transport(task_id, relay_url, transport)
    return transport, False


def _store_transport(task_id: str, relay_url: str, transport: Any) -> None:
    """Cache *transport* under (task_id, relay_url), evicting (and closing)
    the least-recently-used entry once _MAX_TRANSPORT_CACHE is exceeded —
    see the module cache's docstring above for why eviction is size-bounded
    rather than hung off a task-teardown event that does not exist."""
    key = (task_id, relay_url)
    evicted: list[Any] = []
    with _transport_cache_lock:
        _transport_cache[key] = transport
        _transport_cache.move_to_end(key)
        while len(_transport_cache) > _MAX_TRANSPORT_CACHE:
            _, old = _transport_cache.popitem(last=False)
            evicted.append(old)
    for old in evicted:
        try:
            old.close()
        except Exception:
            pass


def _evict_transport(task_id: str, relay_url: str) -> None:
    """Drop and close the cached transport for (task_id, relay_url), if any.

    Called when a send against a CACHED transport raises PreDispatchError:
    the connection is dead (evicted server-side, or dropped without us
    noticing) and must never be handed to a later call under this key."""
    key = (task_id, relay_url)
    with _transport_cache_lock:
        transport = _transport_cache.pop(key, None)
    if transport is not None:
        try:
            transport.close()
        except Exception:
            pass


class _SendCountingTransport:
    """Wrap a transport to record whether ANY frame reached the wire.

    The retry below is only sound when NOTHING was dispatched. For a
    single-frame tool (terminal/read_file/write_file) a PreDispatchError is
    proof of that on its own. It is NOT for patch/search_files: those run
    through ShellFileOperations, which issues several exec frames over one
    transport, so a failure on the third send says nothing about the two that
    already executed on the user's machine. Re-running the whole operation
    there would re-execute real side effects, which is exactly the M4
    guarantee this plugin is built around.

    Counting AFTER the inner send returns is deliberate: a send that raised
    never reached the desktop and must not be counted.
    """

    def __init__(self, inner):
        self._inner = inner
        self.sends_completed = 0

    def send(self, frame):
        self._inner.send(frame)
        self.sends_completed += 1

    def recv(self, request_id, timeout=None):
        return self._inner.recv(request_id, timeout=timeout)

    def close(self):
        self._inner.close()

    def __getattr__(self, name):
        # Anything else (notably the _closed liveness flag) reads through to
        # the real transport.
        return getattr(self._inner, name)


def _tool_error(message: str) -> str:
    return json.dumps({"output": "", "returncode": 1, "exit_code": 1,
                       "status": "error", "error": message}, ensure_ascii=False)


def _desktop_unavailable(detail: str = "") -> str:
    from wheelbase_sdk.runtime import desktop_unavailable_result

    return json.dumps(desktop_unavailable_result(detail=detail), ensure_ascii=False)


def _post_process_relayed_result(tool_name, args, result, *,
                                 task_id, session_id, tool_call_id,
                                 turn_id, api_request_id, duration_ms=0):
    """Re-fire the post-processing that ``handle_function_call`` runs after a
    normal dispatch (model_tools.py:1178 + :1201). Because this plugin relays at
    the OUTER tool_execution wrapper WITHOUT calling next_call, that built-in
    post-processing is otherwise skipped — including the shipped
    security-guidance plugin's ``transform_tool_result`` content-scan on local
    writes. Mirror both calls exactly (same functions, same argument shape). A
    hook/transform that raises is logged and swallowed — it must never fail the
    tool. Returns the (possibly transformed) result.
    """
    # 1. post_tool_call observer (model_tools.py:1178).
    try:
        from model_tools import _emit_post_tool_call_hook
        _emit_post_tool_call_hook(
            function_name=tool_name,
            function_args=args,
            result=result,
            task_id=task_id,
            session_id=session_id,
            tool_call_id=tool_call_id,
            turn_id=turn_id,
            api_request_id=api_request_id,
            duration_ms=duration_ms,
            middleware_trace=[],
        )
    except Exception as exc:
        logger.debug("post_tool_call re-fire error: %s", exc)

    # 2. transform_tool_result canonicalization seam (model_tools.py:1201).
    #    First valid string return wins; non-string returns ignored; fail-open.
    try:
        # Use the lifecycle facade, which includes first-party observers and
        # the active plugin manager on current Hermes runtimes.
        from hermes_cli.lifecycle import has_hook, invoke_hook
        from model_tools import _tool_result_observer_fields
        if has_hook("transform_tool_result"):
            status, error_type, error_message = _tool_result_observer_fields(
                tool_name, result
            )
            hook_results = invoke_hook(
                "transform_tool_result",
                tool_name=tool_name,
                args=args,
                result=result,
                task_id=task_id or "",
                session_id=session_id or "",
                tool_call_id=tool_call_id or "",
                turn_id=turn_id or "",
                api_request_id=api_request_id or "",
                duration_ms=duration_ms,
                status=status,
                error_type=error_type,
                error_message=error_message,
            )
            for hook_result in hook_results:
                if isinstance(hook_result, str):
                    result = hook_result
                    break
    except Exception as exc:
        logger.debug("transform_tool_result re-fire error: %s", exc)

    return result


def _relay(tool_name, args, relay_url, identity, next_call, *,
           task_id, session_id="", tool_call_id, turn_id="", api_request_id=""):
    # Idempotency: serve a cached result for a repeated (task_id, tool_call_id).
    # The key includes task_id so a reused tool_call_id in the shared-dashboard
    # multi-user process can never serve one user's output to another (F4).
    cache_key = f"{task_id}:{tool_call_id}" if tool_call_id else ""
    if cache_key:
        with _result_cache_lock:
            if cache_key in _result_cache:
                return _result_cache[cache_key]

    from .transport import PreDispatchError

    def _dispatch(transport) -> str:
        if tool_name in ("patch", "search_files"):
            # NOT _relay_file/_relay_command: their arg extraction (path/data,
            # command/cmd) does not match patch's mode/old_string/new_string/
            # patch args or search_files' pattern/target/output_mode args.
            return _relay_file_ops(tool_name, args, transport, identity,
                                   task_id=task_id)
        if tool_name in _SHELL_FAMILY:
            return _relay_command(tool_name, args, transport, identity)
        if tool_name == "read_file" and _is_extractable_read(args):
            # .ipynb/.docx/.xlsx: the raw `read` frame below does a UTF-8
            # decode on the sidecar, which turns a ZIP-based document into
            # mojibake. Route these through the same ShellFileOperations
            # machinery patch/search_files use instead, whose
            # read_file_bytes speaks base64-over-exec-frame. A None return
            # means the extraction attempt hit one of read_file_tool's own
            # non-actionable fallthrough cases (see _relay_read_extract) —
            # fall back to the raw frame exactly like the built-in falls
            # through to its normal read path in the same situation.
            extracted = _relay_read_extract(args, transport, identity)
            if extracted is not None:
                return extracted
        return _relay_file(tool_name, args, transport, identity)

    try:
        transport, from_cache = _acquire_transport(task_id, relay_url, identity)
    except PreDispatchError as exc:
        return _desktop_unavailable(str(exc))
    except Exception as exc:
        logger.warning("relay transport build failed: %s", exc)
        return _desktop_unavailable(str(exc))

    relay_ok = False
    counting = _SendCountingTransport(transport)
    try:
        result = _dispatch(counting)
        relay_ok = True
    except PreDispatchError as exc:
        if from_cache and counting.sends_completed == 0:
            # Nothing reached the desktop on this attempt, so rebuilding and
            # re-sending exactly once is safe. This is the retry _relay never
            # had before the shared cache existed: per-call transports could
            # never go stale mid-task, but a transport now outlives many calls,
            # and the Go ExecHub can evict it from under us at any time (see
            # the cache's module docstring above _make_transport).
            #
            # The sends_completed guard is what makes that claim true for
            # patch/search_files, which issue SEVERAL frames over one transport
            # -- see _SendCountingTransport. Contrast with a POST-dispatch
            # failure (the `except Exception` below): that command may already
            # have run on the desktop, so it is NEVER retried (M4).
            _evict_transport(task_id, relay_url)
            try:
                transport = _make_transport(relay_url, identity)
                _store_transport(task_id, relay_url, transport)
                result = _dispatch(transport)
                relay_ok = True
            except PreDispatchError as exc2:
                return _desktop_unavailable(str(exc2))
            except Exception as exc2:
                logger.warning("relay post-dispatch failure for %s: %s", tool_name, exc2)
                result = _desktop_unavailable(str(exc2))
        else:
            return _desktop_unavailable(str(exc))
    except Exception as exc:
        # Post-dispatch failure: return a tool error, NEVER re-dispatch (M4).
        logger.warning("relay post-dispatch failure for %s: %s", tool_name, exc)
        result = _desktop_unavailable(str(exc))

    # On a SUCCESSFUL relay, re-fire the built-in post-processing that the outer
    # relay path bypassed (post_tool_call + transform_tool_result) so security
    # transforms still run on local results (F3). Skipped for the error result
    # above so we do not double-report a failure the built-in never produced.
    if relay_ok:
        result = _post_process_relayed_result(
            tool_name, args, result,
            task_id=task_id, session_id=session_id, tool_call_id=tool_call_id,
            turn_id=turn_id, api_request_id=api_request_id,
        )

    if cache_key:
        with _result_cache_lock:
            if len(_result_cache) >= _MAX_CACHE:
                _result_cache.clear()
            _result_cache[cache_key] = result
    return result


# "/workspace" is a CLOUD sandbox path. On the desktop relay the jail root is
# the user's ~/Wheelbase, and exec-sidecar/jail.ts rejects any absolute path
# outside it ("path escapes workspace: /workspace") — so that default turned
# every relayed shell command into a failure while file ops, which pass
# jail-relative paths, kept working.
#
# "." is the only meaningful default here: the sidecar's Dispatcher overrides
# workspace_root with the trusted EXEC_WORKSPACE_ROOT the desktop supervisor
# handed it and resolves cwd against that, so "." IS the desktop's jail root and
# the gateway never has to know the path.
def _relay_cwd(identity: dict) -> str:
    return identity.get("cwd") or identity.get("workspace_root") or "."


def _relay_command(tool_name, args, transport, identity) -> str:
    from .relay_env import DesktopRelayEnvironment, DESKTOP_UNAVAILABLE_EXIT_CODE
    env = DesktopRelayEnvironment(
        transport=transport,
        cwd=_relay_cwd(identity),
        timeout=int(args.get("timeout") or 120),
        workspace_root=identity.get("workspace_root") or "",
        # transport is the module-level cache's shared connection, not this
        # call's own — this short-lived env wrapper must never close it
        # (see DesktopRelayEnvironment.__init__'s _owns_transport comment).
        owns_transport=False,
    )
    env._snapshot_ready = True  # desktop shell is already a login shell
    command = _extract_command(tool_name, args)
    res = env.execute(command)
    if res.get("returncode") == DESKTOP_UNAVAILABLE_EXIT_CODE:
        return _desktop_unavailable(str(res.get("output") or "desktop relay failed"))
    return json.dumps({
        "output": res.get("output", ""),
        "exit_code": res.get("returncode", 0),
        "returncode": res.get("returncode", 0),
        "status": "success" if res.get("returncode", 0) == 0 else "error",
    }, ensure_ascii=False)


def _relay_file(tool_name, args, transport, identity) -> str:
    import uuid
    request_id = uuid.uuid4().hex
    workspace_root = identity.get("workspace_root") or ""
    path = _file_path(args)
    if tool_name == "read_file":
        transport.send({"type": "read", "request_id": request_id,
                        "path": path, "workspace_root": workspace_root})
    else:  # write_file
        data = args.get("content")
        if data is None:
            data = args.get("data") or ""
        transport.send({"type": "write", "request_id": request_id, "path": path,
                        "data": data, "workspace_root": workspace_root})
    frame = transport.recv(request_id, timeout=int(args.get("timeout") or 120))
    if frame.get("type") == "error":
        return _desktop_unavailable(str(frame.get("message") or "file relay error"))
    return json.dumps({"status": "success", "success": True,
                       "data": frame.get("data", ""), "path": path},
                      ensure_ascii=False)


def _is_extractable_read(args: dict) -> bool:
    """True when a read_file call's path is one of read_extract.py's
    EXTRACTABLE_EXTENSIONS (.ipynb/.docx/.xlsx)."""
    from pathlib import Path

    from tools.read_extract import EXTRACTABLE_EXTENSIONS

    return Path(_file_path(args)).suffix.lower() in EXTRACTABLE_EXTENSIONS


def _relay_read_extract(args: dict, transport, identity: dict) -> str | None:
    """Route an extractable-document read_file call through the SAME
    ShellFileOperations machinery patch/search_files use (_relay_file_ops),
    mirroring read_file_tool's own extraction stage exactly
    (tools/file_tools.py read_file_tool, the "Structured-document
    extraction" block ~1661-1758).

    _relay_file's raw `read` frame asks the sidecar for ``Bun.file(path)
    .text()`` — a UTF-8 decode. A .docx/.xlsx is a ZIP container, so that
    decode turns it into mojibake instead of readable text (confirmed
    user-facing bug). ``ShellFileOperations.read_file_bytes`` already speaks
    base64-over-exec-frame (the same machinery _relay_file_ops uses for
    patch/search_files), and ``tools.read_extract.extract_document_bytes``
    is pure stdlib zipfile/ElementTree with no environment dependency at
    all — so the only new plumbing this needs is fetching the bytes over the
    relay; the extraction/pagination/truncation logic itself is copied
    verbatim from the built-in so the model reads identical output whether
    the read ran locally or through the desktop relay.

    Returns ``None`` when extraction hit one of read_file_tool's own
    non-actionable fallthrough cases (a ValueError/binascii transport
    hiccup, a generic non-actionable ExtractionError, or a malformed
    .ipynb) — the built-in falls through to its normal raw-read path in
    exactly those cases (Python's ``try/except`` with no matching ``return``
    inside the ``except`` continues past the whole block), so the caller
    here does the same by falling back to ``_relay_file``.
    """
    import base64 as _b64
    from pathlib import Path

    from .relay_env import DesktopRelayEnvironment
    from tools.file_operations import ShellFileOperations, normalize_read_pagination
    from tools.file_tools import _get_max_read_chars, _truncate_to_char_budget
    from tools.read_extract import (
        ANYDOC_EXTENSIONS, EXTRACTABLE_EXTENSIONS, MAX_DOCUMENT_BYTES,
        ExtractionError, extract_document_bytes,
    )
    from agent.redact import redact_sensitive_text

    path = _file_path(args)
    offset, limit = normalize_read_pagination(args.get("offset", 1), args.get("limit", 500))

    env = DesktopRelayEnvironment(
        transport=transport,
        cwd=_relay_cwd(identity),
        timeout=int(args.get("timeout") or 120),
        workspace_root=identity.get("workspace_root") or "",
        owns_transport=False,  # shared cache owns it — see _relay_command
    )
    env._snapshot_ready = True  # desktop shell is already a login shell
    fileops = ShellFileOperations(env)

    binary = fileops.read_file_bytes(path, max_bytes=MAX_DOCUMENT_BYTES)
    try:
        if binary.error or binary.base64_content is None:
            raise ExtractionError(binary.error or "Document bytes unavailable")
        document_bytes = _b64.b64decode(binary.base64_content, validate=True)
        extracted_text = extract_document_bytes(document_bytes, path)
    except (ExtractionError, ValueError, _b64.binascii.Error) as exc:
        doc_ext = Path(path).suffix.lower()
        binary_doc = doc_ext in ANYDOC_EXTENSIONS or (
            doc_ext in EXTRACTABLE_EXTENSIONS and doc_ext != ".ipynb"
        )
        if (
            binary_doc
            and isinstance(exc, ExtractionError)
            and not str(exc).startswith("Unsupported document type")
        ):
            return _patch_tool_error(
                f"Cannot read '{path}' ({doc_ext}): document extraction "
                f"failed — {exc}. Use terminal utilities to inspect or "
                "convert the file."
            )
        return None

    lines = extracted_text.splitlines()
    total_lines = len(lines)
    end_line = offset + limit - 1
    page_text = "\n".join(lines[offset - 1:end_line])
    result_dict = {
        "content": fileops._add_line_numbers(page_text, offset) if page_text else "",
        "total_lines": total_lines,
        "file_size": binary.file_size,
        "truncated": total_lines > end_line,
        "extracted_document": True,
    }
    if result_dict["truncated"]:
        result_dict["hint"] = (
            f"Use offset={end_line + 1} to continue reading "
            f"(showing {offset}-{min(end_line, total_lines)} of {total_lines} lines)"
        )
    content_len = len(result_dict["content"])
    max_chars = _get_max_read_chars()
    if content_len > max_chars:
        trimmed, lines_kept, _ = _truncate_to_char_budget(result_dict["content"], max_chars)
        next_offset = offset + lines_kept
        shown_end = offset + lines_kept - 1
        result_dict["content"] = trimmed
        result_dict["truncated"] = True
        result_dict["truncated_by"] = "bytes"
        result_dict["next_offset"] = next_offset
        result_dict["hint"] = (
            f"Output truncated at the {max_chars:,}-char read budget "
            f"after {lines_kept} line(s) (showing lines {offset}-"
            f"{shown_end} of {total_lines}). Use offset={next_offset} "
            "to continue."
        )
        if len(trimmed.split("\n", 1)[0]) >= max_chars:
            result_dict["hint"] += (
                " Note: the first line alone exceeded the budget and "
                "was clamped mid-line; its remainder is not "
                "retrievable via offset."
            )
    if result_dict["content"]:
        result_dict["content"] = redact_sensitive_text(result_dict["content"], file_read=True)
    return json.dumps(result_dict, ensure_ascii=False)


def _patch_tool_error(message: str) -> str:
    """Same envelope shape as ``tools.registry.tool_error`` (single ``error``
    key, no ``success``/``status``) — matches ``patch_tool``'s validation-error
    returns (tools/file_tools.py ~1675-1690) exactly."""
    return json.dumps({"error": message}, ensure_ascii=False)


def _relay_file_ops(tool_name, args, transport, identity, *, task_id="") -> str:
    """Route patch/search_files through the SAME ShellFileOperations the
    built-in tools use (tools/file_operations.py), wrapping a
    DesktopRelayEnvironment so its fuzzy-match/rg/grep logic runs unchanged
    and simply emits `exec` frames over the relay instead of a local
    subprocess. NOT _relay_file/_relay_command — patch's
    mode/old_string/new_string/patch args and search_files' pattern/target/
    output_mode args do not map onto those helpers' path/data or
    command/cmd extraction (the F1/F2 bug this replaces).
    """
    from .relay_env import DesktopRelayEnvironment
    from tools.file_operations import ShellFileOperations

    env = DesktopRelayEnvironment(
        transport=transport,
        cwd=_relay_cwd(identity),
        timeout=int(args.get("timeout") or 120),
        workspace_root=identity.get("workspace_root") or "",
        owns_transport=False,  # shared cache owns it — see _relay_command
    )
    env._snapshot_ready = True  # desktop shell is already a login shell
    fileops = ShellFileOperations(env)

    if tool_name == "search_files":
        return _relay_search(fileops, args, task_id=task_id)
    return _relay_patch(fileops, args)


def _relay_search(fileops, args, *, task_id="") -> str:
    """Mirror ``search_tool``'s JSON envelope exactly (tools/file_tools.py
    ~1759-1841) so the model reads identical output whether the search ran
    locally (desktop) or in the cloud sandbox. Deliberately does NOT
    replicate the consecutive-repeated-search loop counter — that is a
    cross-call anti-loop nudge, not part of the result envelope shape.
    """
    from tools.file_operations import normalize_search_pagination

    offset, limit = normalize_search_pagination(
        args.get("offset"), args.get("limit"))

    result = fileops.search(
        pattern=args.get("pattern") or "",
        path=args.get("path") or ".",
        target=args.get("target") or "content",
        file_glob=args.get("file_glob"),
        limit=limit,
        offset=offset,
        output_mode=args.get("output_mode") or "content",
        context=int(args.get("context") or 0),
    )

    # Same credential/secret-path filtering + redaction the built-in applies
    # to search results before the model ever sees them.
    from tools.file_tools import _filter_read_blocked_search_results
    omitted = _filter_read_blocked_search_results(result, task_id or "default")
    if hasattr(result, "matches"):
        from agent.redact import redact_sensitive_text
        for m in result.matches:
            if getattr(m, "content", None):
                m.content = redact_sensitive_text(m.content, file_read=True)

    result_dict = result.to_dict(densify=True)
    if omitted:
        result_dict["_omitted"] = (
            f"{omitted} result(s) omitted because they target credential, "
            "token, cache, or secret-bearing environment files."
        )

    result_json = json.dumps(result_dict, ensure_ascii=False)
    if result_dict.get("truncated"):
        next_offset = offset + limit
        result_json += (
            f"\n\n[Hint: Results truncated. Use offset={next_offset} to see "
            "more, or narrow with a more specific pattern or file_glob.]"
        )
    return result_json


def _relay_patch(fileops, args) -> str:
    """Mirror ``patch_tool``'s JSON envelope exactly (tools/file_tools.py
    ~1565-1754) so the model reads identical output whether the patch ran
    locally (desktop) or in the cloud sandbox. ``PatchResult.to_dict()``
    (both patch_replace and patch_v4a/apply_v4a_operations return one) already
    carries success/diff/files_modified/files_created/files_deleted/lint/
    lsp_diagnostics/error — the same fields the built-in tool serializes.
    Deliberately does NOT replicate the cross-agent staleness-warning /
    per-task consecutive-failure-counter machinery (file_state, stale
    detection) — that machinery assumes the cloud sandbox filesystem is the
    same one other tasks/agents observe, which does not hold for a user's own
    desktop filesystem reached only through this one relay.
    """
    mode = args.get("mode") or "replace"
    if mode == "replace":
        path = args.get("path")
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        replace_all = bool(args.get("replace_all") or False)
        if not path:
            return _patch_tool_error("path required")
        if old_string is None or new_string is None:
            return _patch_tool_error("old_string and new_string required")
        result = fileops.patch_replace(path, old_string, new_string, replace_all)
    elif mode == "patch":
        patch_content = args.get("patch")
        if not patch_content:
            return _patch_tool_error("patch content required")
        result = fileops.patch_v4a(patch_content)
    else:
        return _patch_tool_error(f"Unknown mode: {mode}")

    result_dict = result.to_dict()
    error = result_dict.get("error")
    if error and "Could not find" in str(error):
        # Same hint the built-in attaches — saves iterations where the agent
        # retries with stale content instead of re-reading the file. Skipped
        # when patch_replace already attached its own richer "Did you mean?"
        # snippet (tools/fuzzy_match.py:format_no_match_hint).
        if "Did you mean one of these sections?" not in str(error):
            result_dict["_hint"] = (
                "old_string not found. Use read_file to verify the current "
                "content, or search_files to locate the text."
            )
    return json.dumps(result_dict, ensure_ascii=False)


def _relay_execute_code(args, relay_url, identity, next_call, *, task_id, tool_call_id=""):
    """Route execute_code by injecting a DesktopRelayEnvironment into the
    shared terminal-tool env cache, then delegating to the built-in
    execute_code handler via next_call.

    Unlike every other routed tool, execute_code is NOT dispatched via
    _relay/_safety_block:
      * The built-in execute_code handler (tools/code_execution_tool.py)
        guards itself via check_execute_code_guard. Calling
        _safety_block_execute_code here too would run the SAME whole-script
        approval guard twice — at best a double-prompt, at worst a confusing
        double-approval race.
      * The built-in dispatch (reached through next_call → the framework's
        normal handle_function_call path) already runs post_tool_call /
        transform_tool_result on its way out. Re-firing
        _post_process_relayed_result here would double-post-process.

    Fail-closed: any failure building the transport/environment returns the
    stable ``desktop_unavailable`` result. Desktop-origin code is never replayed
    in a cloud environment, including failures before dispatch.

    Idempotency (spec §5.1 M2, same cache _relay uses): the concurrent
    executor double-wraps the middleware, so the SAME (task_id, tool_call_id)
    can reach this function twice for one logical call. The second
    invocation must return the FIRST result verbatim and must NEVER build a
    second transport/environment or touch _active_environments again —
    re-injecting would re-run the script a second time (arbitrary Python,
    not idempotent) and would risk clobbering whatever the first call has
    already restored into the shared cache. Checking the cache before doing
    anything else (mirroring _relay's ordering exactly) means a cache hit
    short-circuits BEFORE any env is created, so it can never poison
    _active_environments. Genuine concurrency beyond this (two DIFFERENT
    tool_call_ids for the same task truly running in parallel threads) is
    out of scope here: a single agent turn dispatches its tool calls
    sequentially, so that scenario does not arise in practice; the
    surviving concern this fixes is the double-wrap re-dispatch of ONE call.
    """
    cache_key = f"{task_id}:{tool_call_id}" if tool_call_id else ""
    if cache_key:
        with _result_cache_lock:
            if cache_key in _result_cache:
                return _result_cache[cache_key]

    from .transport import PreDispatchError

    # Shared transport (same cache _relay uses — see the module docstring
    # above _make_transport). Unlike _relay, a mid-call send failure here
    # cannot be retried-once against a fresh transport: the built-in
    # execute_code handler (tools/code_execution_tool.py's _execute_remote)
    # already wraps its own env.execute() calls in a blanket
    # `except Exception` that converts ANY failure, including a
    # PreDispatchError raised from a stale send, into a normal
    # {"status": "error", ...} return — so by the time control comes back to
    # next_call() below, there is nothing left here to catch and retry
    # without editing that upstream handler (which this plugin's design
    # forbids — see the module docstring's "Zero upstream-core edits"). The
    # `_closed`-flag check in _acquire_transport still protects against
    # handing out a transport that was explicitly closed; a transport that
    # died silently (send still "succeeds" until the OS notices) self-heals
    # on the NEXT relayed call for this (task_id, relay_url) — any tool, not
    # just execute_code — via _relay's retry-and-evict path above.
    try:
        transport, _from_cache = _acquire_transport(task_id, relay_url, identity)
    except PreDispatchError as exc:
        return _desktop_unavailable(str(exc))
    except Exception as exc:
        logger.warning("execute_code relay transport build failed: %s", exc)
        return _desktop_unavailable(str(exc))

    try:
        from .relay_env import DesktopRelayEnvironment
        env = DesktopRelayEnvironment(
            transport=transport,
            cwd=_relay_cwd(identity),
            timeout=int(args.get("timeout") or 120),
            workspace_root=identity.get("workspace_root") or "",
            # Shared/cached transport, not this call's own — see
            # DesktopRelayEnvironment's _owns_transport comment. cleanup()
            # below must not close a connection other calls still need.
            owns_transport=False,
        )
    except Exception as exc:
        # Pure Python object construction — this failure has nothing to do
        # with the transport's health, so the shared cache is left alone for
        # the next call to keep using it.
        logger.warning("execute_code relay environment build failed: %s", exc)
        return _desktop_unavailable(str(exc))

    env._snapshot_ready = True  # desktop shell is already a login shell
    # Never idle-reaped mid-call by the terminal_tool cleanup thread (spec:
    # terminal_tool.py:1584 skips teardown for always-on envs, refreshing
    # their _last_activity instead).
    env._always_on = True

    from tools.terminal_tool import (
        _active_environments, _env_lock, _last_activity,
        _resolve_container_task_id,
    )
    import time

    # SAME key the built-in execute_code path computes: _execute_remote (~934)
    # calls _get_or_create_env(effective_task_id), which resolves
    # _resolve_container_task_id(task_id) and looks up _active_environments
    # under that key (tools/code_execution_tool.py:641,645; verified against
    # tools/terminal_tool.py:_resolve_container_task_id).
    key = _resolve_container_task_id(task_id)
    with _env_lock:
        prev = _active_environments.get(key)
        _active_environments[key] = env
        _last_activity[key] = time.time()
    try:
        result = next_call(args)  # built-in execute_code -> _execute_remote against injected env
    finally:
        with _env_lock:
            # Only touch the cache entry if it is still OURS — some other
            # caller may already have replaced it (e.g. a later injection
            # for this key, or a genuinely concurrent different tool_call_id
            # for this task — out of scope in practice, see the docstring).
            # `prev` here is whatever was cached under this key BEFORE we
            # injected — e.g. a real terminal_tool environment for this task,
            # not something this plugin ever cleanup()-ed. The idempotency
            # short-circuit above is what keeps `prev` from ever being a
            # dead env this same call already tore down: a repeated
            # (task_id, tool_call_id) never reaches this injection code a
            # second time (it returns the cached result up top instead), so
            # this function injects+restores under a given key AT MOST ONCE
            # per logical call — `prev` can never be an env WE already
            # cleaned up.
            if _active_environments.get(key) is env:
                if prev is not None:
                    _active_environments[key] = prev
                else:
                    _active_environments.pop(key, None)
        # No-op now that owns_transport=False (the transport is cache-owned
        # and other calls for this task may still need it) — kept for any
        # non-transport per-call teardown BaseEnvironment subclasses might
        # someday add.
        try:
            env.cleanup()
        except Exception:
            pass

    # Relay loss can be surfaced by BaseEnvironment as an ordinary non-zero
    # execution result. Normalize the reserved transport exit code so callers
    # still receive the stable machine-readable failure contract.
    try:
        parsed_result = json.loads(result) if isinstance(result, str) else result
        if isinstance(parsed_result, dict) and parsed_result.get("returncode") == 252:
            result = _desktop_unavailable(str(parsed_result.get("output") or ""))
    except (TypeError, ValueError):
        pass

    if cache_key:
        with _result_cache_lock:
            if len(_result_cache) >= _MAX_CACHE:
                _result_cache.clear()
            _result_cache[cache_key] = result
    return result
