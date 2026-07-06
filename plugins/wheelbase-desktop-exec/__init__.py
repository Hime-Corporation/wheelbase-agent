"""wheelbase-desktop-exec — route tool execution to a desktop over the exec relay.

Standalone Hermes plugin (spec §5.1). Registers a ``tool_execution`` middleware
that, for a desktop user who is online (identity carries shell_relay_url), runs
the built-in per-tool safety chain (spec §5.5) then relays the operation to the
user's machine. Mobile/offline users (no relay url) and any ambiguous identity
fall back to the sandboxed cloud path via next_call. Zero upstream-core edits.

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
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Tools whose execution we route to the desktop over the local relay. These are
# the ones that map CLEANLY onto the sidecar's primitives: bash-command tools
# (terminal/process → command/cmd) and full-content file read/write
# (read_file/write_file → path/data).
#
# patch/execute_code/search_files route to cloud pending (a) proper
# sidecar-primitive mapping and (b) workspace_root delivery (spec §7.2.1). Their
# args do NOT map to the current relay frames — `patch` carries
# mode/old_string/new_string/patch (not content), `execute_code` carries `code`
# (Python, not bash), and `search_files` carries `pattern`. Shipping them over
# the command/file frames would send empty/wrong frames, so they deliberately
# fall through to next_call (cloud Daytona) — functional and fail-closed —
# until both open items above are resolved.
ROUTED_TOOLS = frozenset({
    "terminal", "process", "read_file", "write_file",
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

    # 3. No relay url → mobile / offline → sandboxed cloud path (spec §5.1.3).
    relay_url = (identity.get("shell_relay_url") or "").strip()
    if not relay_url:
        return next_call(args)

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

    # 5. Relay. Pre-dispatch failure → next_call (cloud fallback). Post-dispatch
    #    failure → tool error, NEVER re-dispatch (spec §5.1 M4).
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


def _safety_block_files(tool_name, args, task_id):
    path = _file_path(args)
    if tool_name in ("write_file", "patch"):
        from tools.file_tools import _check_sensitive_path
        err = _check_sensitive_path(path, task_id)
        if err:
            return _file_block_result(err)
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

_SHELL_FAMILY = frozenset({"terminal", "process", "execute_code", "search_files"})
_FILE_FAMILY = frozenset({"read_file", "write_file", "patch"})


def _make_transport(relay_url: str, identity: dict):
    """Build the ExecTransport for this call. Production wiring (WS → Go
    ExecHub) replaces this seam; it is monkeypatched to a fake in tests."""
    from .ws_transport import WebsocketExecTransport  # not imported until wired
    return WebsocketExecTransport(relay_url, identity)


def _tool_error(message: str) -> str:
    return json.dumps({"output": "", "returncode": 1, "exit_code": 1,
                       "status": "error", "error": message}, ensure_ascii=False)


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
        from hermes_cli.plugins import has_hook, invoke_hook
        from model_tools import _tool_result_observer_fields
        if has_hook("transform_tool_result"):
            status, error_type, error_message = _tool_result_observer_fields(result)
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

    try:
        transport = _make_transport(relay_url, identity)
    except PreDispatchError:
        return next_call(args)          # connection never came up → cloud fallback
    except Exception as exc:
        logger.warning("relay transport build failed: %s", exc)
        return next_call(args)

    relay_ok = False
    try:
        if tool_name in _SHELL_FAMILY:
            result = _relay_command(tool_name, args, transport, identity)
        else:
            result = _relay_file(tool_name, args, transport, identity)
        relay_ok = True
    except PreDispatchError:
        # Nothing executed on the desktop → safe to fall back.
        return next_call(args)
    except Exception as exc:
        # Post-dispatch failure: return a tool error, NEVER re-dispatch (M4).
        logger.warning("relay post-dispatch failure for %s: %s", tool_name, exc)
        result = _tool_error(f"desktop exec failed: {exc}")

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


def _relay_command(tool_name, args, transport, identity) -> str:
    from .relay_env import DesktopRelayEnvironment
    env = DesktopRelayEnvironment(
        transport=transport,
        cwd=identity.get("cwd") or identity.get("workspace_root") or "/workspace",
        timeout=int(args.get("timeout") or 120),
        workspace_root=identity.get("workspace_root") or "",
    )
    env._snapshot_ready = True  # desktop shell is already a login shell
    command = _extract_command(tool_name, args)
    res = env.execute(command)
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
    else:  # write_file / patch (patch resolves to a final write on the desktop)
        data = args.get("content")
        if data is None:
            data = args.get("data") or ""
        transport.send({"type": "write", "request_id": request_id, "path": path,
                        "data": data, "workspace_root": workspace_root})
    frame = transport.recv(request_id, timeout=int(args.get("timeout") or 120))
    if frame.get("type") == "error":
        return _tool_error(str(frame.get("message") or "file relay error"))
    return json.dumps({"status": "success", "success": True,
                       "data": frame.get("data", ""), "path": path},
                      ensure_ascii=False)
