"""Task 2: execute_code routed via terminal-cache env injection.

Unlike every other routed tool, execute_code is NOT dispatched through
_relay/_safety_block: the plugin injects a DesktopRelayEnvironment into the
shared tools.terminal_tool._active_environments cache (under the SAME key
_resolve_container_task_id computes — verified against
tools/code_execution_tool.py:_get_or_create_env) and delegates to the
built-in handler via next_call, which guards (check_execute_code_guard) and
post-processes itself. These tests use a fake `next_call` that stands in for
the built-in handler: it looks up the injected env from the shared cache
exactly like _execute_remote -> _get_or_create_env does, and calls
env.execute() on it.
"""
from __future__ import annotations

import importlib

import pytest

plug = importlib.import_module("plugins.wheelbase-desktop-exec")
transport_mod = importlib.import_module("plugins.wheelbase-desktop-exec.transport")
relay_env_mod = importlib.import_module("plugins.wheelbase-desktop-exec.relay_env")


class FakeTransport(transport_mod.ExecTransport):
    def __init__(self, connected=True):
        self.sent = []
        self._connected = connected
        self.closed = False

    def send(self, frame):
        if not self._connected:
            raise transport_mod.PreDispatchError("no relay")
        self.sent.append(dict(frame))

    def recv(self, request_id, timeout=None):  # pragma: no cover - unused here
        raise AssertionError("execute_code test stubs DesktopRelayEnvironment.execute directly")

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _ident():
    from wheelbase_sdk import runtime
    runtime.set_task_identity("t-desk", {"user_id": "u", "shell_relay_url": "wss://relay",
                                         "workspace_root": "/work"})
    yield
    runtime._current.set(None)
    with runtime._lock:
        runtime._by_task.clear()


@pytest.fixture(autouse=True)
def _clear_env_cache():
    from tools.terminal_tool import _active_environments, _last_activity, _env_lock
    yield
    with _env_lock:
        _active_environments.clear()
        _last_activity.clear()


def _stub_execute(monkeypatch, output="hi\n", returncode=0):
    monkeypatch.setattr(relay_env_mod.DesktopRelayEnvironment, "execute",
                        lambda self, *a, **k: {"output": output, "returncode": returncode})


def _builtin_lookup_next_call(task_id, seen):
    """Simulates the built-in execute_code -> _execute_remote ->
    _get_or_create_env: looks up _active_environments under
    _resolve_container_task_id(task_id) and calls env.execute()."""
    from tools.terminal_tool import _active_environments, _resolve_container_task_id

    def nc(args):
        key = _resolve_container_task_id(task_id)
        env = _active_environments.get(key)
        seen["env"] = env
        if env is None:
            return "NO_ENV_FOUND"
        result = env.execute("echo hi")
        return f"ran: {result.get('output', '')}"

    return nc


def test_execute_code_injects_desktop_relay_env_under_the_looked_up_key(monkeypatch):
    ft = FakeTransport()
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    _stub_execute(monkeypatch)

    seen = {}
    nc = _builtin_lookup_next_call("t-desk", seen)

    out = plug.route_or_passthrough(
        tool_name="execute_code", args={"code": "print(1)"}, next_call=nc,
        task_id="t-desk", tool_call_id="e1",
    )

    assert out == "ran: hi\n"
    assert isinstance(seen["env"], relay_env_mod.DesktopRelayEnvironment)
    assert seen["env"]._always_on is True


def test_execute_code_cache_entry_removed_after_call_when_no_prior_entry(monkeypatch):
    from tools.terminal_tool import _active_environments, _resolve_container_task_id
    key = _resolve_container_task_id("t-desk")

    ft = FakeTransport()
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    _stub_execute(monkeypatch)

    def nc(args):
        # Mid-call: the env IS in the cache under the looked-up key.
        assert key in _active_environments
        return "ok"

    plug.route_or_passthrough(
        tool_name="execute_code", args={"code": "1"}, next_call=nc,
        task_id="t-desk", tool_call_id="e2",
    )
    assert key not in _active_environments


def test_execute_code_restores_previous_cache_entry_after_call(monkeypatch):
    from tools.terminal_tool import _active_environments, _resolve_container_task_id
    key = _resolve_container_task_id("t-desk")
    sentinel = object()
    _active_environments[key] = sentinel

    ft = FakeTransport()
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    _stub_execute(monkeypatch)

    def nc(args):
        return "ok"

    plug.route_or_passthrough(
        tool_name="execute_code", args={"code": "1"}, next_call=nc,
        task_id="t-desk", tool_call_id="e3",
    )
    assert _active_environments[key] is sentinel


def test_execute_code_cleanup_closes_transport(monkeypatch):
    ft = FakeTransport()
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    _stub_execute(monkeypatch)

    def nc(args):
        return "ok"

    plug.route_or_passthrough(
        tool_name="execute_code", args={"code": "1"}, next_call=nc,
        task_id="t-desk", tool_call_id="e4",
    )
    assert ft.closed is True


def test_execute_code_cleanup_runs_even_if_next_call_raises(monkeypatch):
    from tools.terminal_tool import _active_environments, _resolve_container_task_id
    key = _resolve_container_task_id("t-desk")

    ft = FakeTransport()
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    _stub_execute(monkeypatch)

    def nc(args):
        raise RuntimeError("built-in execute_code blew up")

    with pytest.raises(RuntimeError):
        plug.route_or_passthrough(
            tool_name="execute_code", args={"code": "1"}, next_call=nc,
            task_id="t-desk", tool_call_id="e5",
        )
    assert ft.closed is True
    assert key not in _active_environments


def test_execute_code_transport_build_failure_falls_back_with_no_injection(monkeypatch):
    from tools.terminal_tool import _active_environments, _resolve_container_task_id
    key = _resolve_container_task_id("t-desk")

    def boom(url, ident):
        raise RuntimeError("no relay available")

    monkeypatch.setattr(plug, "_make_transport", boom)
    calls = {"n": 0}

    def nc(args):
        calls["n"] += 1
        assert key not in _active_environments  # nothing was injected
        return "CLOUD"

    out = plug.route_or_passthrough(
        tool_name="execute_code", args={"code": "1"}, next_call=nc,
        task_id="t-desk", tool_call_id="e6",
    )
    assert out == "CLOUD"
    assert calls["n"] == 1
    assert key not in _active_environments


def test_execute_code_predispatch_error_falls_back_with_no_injection(monkeypatch):
    from tools.terminal_tool import _active_environments, _resolve_container_task_id
    key = _resolve_container_task_id("t-desk")

    def boom(url, ident):
        raise transport_mod.PreDispatchError("not connected")

    monkeypatch.setattr(plug, "_make_transport", boom)
    calls = {"n": 0}

    def nc(args):
        calls["n"] += 1
        return "CLOUD"

    out = plug.route_or_passthrough(
        tool_name="execute_code", args={"code": "1"}, next_call=nc,
        task_id="t-desk", tool_call_id="e7",
    )
    assert out == "CLOUD"
    assert calls["n"] == 1
    assert key not in _active_environments


def test_execute_code_not_pre_guarded_by_the_plugin(monkeypatch):
    # The built-in execute_code handler (behind next_call) runs
    # check_execute_code_guard itself; the plugin must NOT also call
    # _safety_block_execute_code on this path (that would double-prompt).
    ft = FakeTransport()
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    _stub_execute(monkeypatch)

    def _fail_guard(*a, **k):
        pytest.fail("_safety_block_execute_code must not run on the execute_code relay path")

    monkeypatch.setattr(plug, "_safety_block_execute_code", _fail_guard)
    monkeypatch.setattr(plug, "_safety_block",
                        lambda *a, **k: pytest.fail("generic _safety_block must not run either"))

    def nc(args):
        return "ok"

    out = plug.route_or_passthrough(
        tool_name="execute_code", args={"code": "1"}, next_call=nc,
        task_id="t-desk", tool_call_id="e8",
    )
    assert out == "ok"


def test_execute_code_not_double_post_processed(monkeypatch):
    # The built-in dispatch (reached through next_call) already runs
    # post_tool_call/transform_tool_result on its way out; the plugin must
    # NOT re-fire _post_process_relayed_result for execute_code.
    ft = FakeTransport()
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    _stub_execute(monkeypatch)

    def _fail_post(*a, **k):
        pytest.fail("_post_process_relayed_result must not re-fire for execute_code")

    monkeypatch.setattr(plug, "_post_process_relayed_result", _fail_post)

    def nc(args):
        return "ok"

    out = plug.route_or_passthrough(
        tool_name="execute_code", args={"code": "1"}, next_call=nc,
        task_id="t-desk", tool_call_id="e9",
    )
    assert out == "ok"


def test_execute_code_routed_but_not_via_shell_family():
    assert "execute_code" in plug.ROUTED_TOOLS
    assert "execute_code" not in plug._SHELL_FAMILY


# --- Idempotency + safe env-cache restore -----------------------------------
#
# The concurrent executor double-wraps the middleware, so the SAME
# (task_id, tool_call_id) can reach _relay_execute_code twice for one logical
# call. _relay already has an idempotency cache for this (spec §5.1 M2);
# execute_code lacked it entirely, so a repeat invocation would re-inject a
# fresh env and RE-RUN the arbitrary Python script a second time.

@pytest.fixture(autouse=True)
def _clear_result_cache():
    yield
    with plug._result_cache_lock:
        plug._result_cache.clear()


def test_execute_code_repeated_tool_call_id_returns_cached_result_no_second_run(monkeypatch):
    ft = FakeTransport()
    transport_calls = {"n": 0}

    def _counted_transport(url, ident):
        transport_calls["n"] += 1
        return ft

    monkeypatch.setattr(plug, "_make_transport", _counted_transport)
    _stub_execute(monkeypatch)

    run_count = {"n": 0}
    seen = {}
    nc = _builtin_lookup_next_call("t-desk", seen)

    def counted_nc(args):
        run_count["n"] += 1
        return nc(args)

    out1 = plug.route_or_passthrough(
        tool_name="execute_code", args={"code": "print(1)"}, next_call=counted_nc,
        task_id="t-desk", tool_call_id="dup-exec",
    )
    out2 = plug.route_or_passthrough(
        tool_name="execute_code", args={"code": "print(1)"}, next_call=counted_nc,
        task_id="t-desk", tool_call_id="dup-exec",
    )

    assert out1 == out2
    assert run_count["n"] == 1          # script body executed exactly once
    assert transport_calls["n"] == 1    # second call never even built a transport


def test_execute_code_active_environments_never_holds_a_dead_env_after_call(monkeypatch):
    # After a call completes, the shared cache must either be empty for this
    # key or hold a live env — never the closed/dead env this call itself
    # cleaned up.
    from tools.terminal_tool import _active_environments, _resolve_container_task_id

    key = _resolve_container_task_id("t-desk")
    ft = FakeTransport()
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    _stub_execute(monkeypatch)

    def nc(args):
        return "ok"

    plug.route_or_passthrough(
        tool_name="execute_code", args={"code": "1"}, next_call=nc,
        task_id="t-desk", tool_call_id="dead-env-1",
    )
    # No prior entry -> popped clean; the closed-transport env is not cached.
    assert key not in _active_environments
    assert ft.closed is True

    # Repeat with a real prior entry: it must come back untouched (still
    # live), not get clobbered by our (now-closed) injected env.
    class _PriorEnv:
        pass

    prior_env = _PriorEnv()
    _active_environments[key] = prior_env
    try:
        plug.route_or_passthrough(
            tool_name="execute_code", args={"code": "1"}, next_call=nc,
            task_id="t-desk", tool_call_id="dead-env-2",
        )
        assert _active_environments[key] is prior_env  # untouched, still live
        assert ft.closed is True
    finally:
        _active_environments.pop(key, None)
