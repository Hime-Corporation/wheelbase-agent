"""wheelbase-desktop-exec — route tool execution to a desktop over the exec relay.

Standalone Hermes plugin (spec §5.1). Registers a ``tool_execution`` middleware
that, for a desktop user who is online (identity carries shell_relay_url), runs
the built-in per-tool safety chain (spec §5.5) then relays the operation to the
user's machine. Mobile/offline users (no relay url) and any ambiguous identity
fall back to the sandboxed cloud path via next_call. Zero upstream-core edits.

All 7 built-in tools route to the desktop when a relay url is present:
  * terminal/process        → ``_relay_command`` (bash `exec` frame).
  * read_file/write_file    → ``_relay_file`` (`read`/`write` frames).
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

    # 3. No relay url → mobile / offline → sandboxed cloud path (spec §5.1.3).
    relay_url = (identity.get("shell_relay_url") or "").strip()
    if not relay_url:
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
        if tool_name in ("patch", "search_files"):
            # NOT _relay_file/_relay_command: their arg extraction (path/data,
            # command/cmd) does not match patch's mode/old_string/new_string/
            # patch args or search_files' pattern/target/output_mode args.
            result = _relay_file_ops(tool_name, args, transport, identity,
                                     task_id=task_id)
        elif tool_name in _SHELL_FAMILY:
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
    else:  # write_file
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
        cwd=identity.get("cwd") or identity.get("workspace_root") or "/workspace",
        timeout=int(args.get("timeout") or 120),
        workspace_root=identity.get("workspace_root") or "",
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

    Fail-closed: any failure building the transport/environment happens
    BEFORE anything is injected into the shared cache, so next_call(args)
    (cloud fallback) is always safe to call in that case — nothing has run
    on the desktop yet.

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

    try:
        transport = _make_transport(relay_url, identity)
    except PreDispatchError:
        return next_call(args)
    except Exception as exc:
        logger.warning("execute_code relay transport build failed: %s", exc)
        return next_call(args)

    try:
        from .relay_env import DesktopRelayEnvironment
        env = DesktopRelayEnvironment(
            transport=transport,
            cwd=identity.get("cwd") or identity.get("workspace_root") or "/workspace",
            timeout=int(args.get("timeout") or 120),
            workspace_root=identity.get("workspace_root") or "",
        )
    except Exception as exc:
        logger.warning("execute_code relay environment build failed: %s", exc)
        return next_call(args)

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
        try:
            env.cleanup()
        except Exception:
            pass

    if cache_key:
        with _result_cache_lock:
            if len(_result_cache) >= _MAX_CACHE:
                _result_cache.clear()
            _result_cache[cache_key] = result
    return result
