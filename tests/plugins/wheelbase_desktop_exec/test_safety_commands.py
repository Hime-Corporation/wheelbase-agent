"""Command-guard reproduction (spec §5.5): the bypass must re-run the guards."""
from __future__ import annotations

import importlib
import json

import pytest

plug = importlib.import_module("plugins.wheelbase-desktop-exec")


def test_hardline_command_is_blocked_not_relayed(monkeypatch):
    # rm -rf / is a hardline block regardless of session settings.
    out = plug._safety_block("terminal", {"command": "rm -rf /"}, "t1", "s1")
    assert out is not None
    parsed = json.loads(out)
    assert parsed["status"] in ("blocked", "error")
    assert parsed.get("exit_code", -1) != 0


def test_safe_command_returns_none(monkeypatch):
    assert plug._safety_block("terminal", {"command": "ls -la"}, "t1", "s1") is None


def test_guard_invoked_with_noncontainer_env_type(monkeypatch):
    seen = {}

    def fake_guard(command, env_type, approval_callback=None, has_host_access=False):
        seen["env_type"] = env_type
        seen["has_host_access"] = has_host_access
        return {"approved": True, "message": None}

    monkeypatch.setattr("tools.approval.check_all_command_guards", fake_guard)
    plug._safety_block("terminal", {"command": "echo hi"}, "t1", "s1")
    assert seen["env_type"] == "desktop-relay"          # non-container → guards fire
    assert seen["has_host_access"] is True


def test_process_routes_through_command_guard(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tools.approval.check_all_command_guards",
        lambda command, env_type, **k: calls.append((command, env_type))
        or {"approved": True, "message": None},
    )
    plug._safety_block("process", {"command": "sleep 1"}, "t", "s")
    assert calls == [("sleep 1", "desktop-relay")]


def test_execute_code_uses_code_guard_not_command_guard(monkeypatch):
    """Unit test of the DEDICATED execute_code guard helper.

    NOTE: execute_code no longer takes the local relay path — it routes to
    cloud (see test_routing.test_unmapped_tools_route_to_cloud_not_local), so
    this guard branch is dead for the local path. The helper is retained
    (harmless) and unit-tested here: if execute_code is ever re-added to the
    routed-local set, it MUST take the whole-script code guard, NEVER the shell
    command guard — a script that doesn't textually trip a shell regex would
    otherwise run on the user's real machine with no approval."""
    code_guard_calls = []
    command_guard_calls = []
    monkeypatch.setattr(
        "tools.approval.check_execute_code_guard",
        lambda code, env_type, **k: code_guard_calls.append((code, env_type, k))
        or {"approved": True, "message": None},
    )
    monkeypatch.setattr(
        "tools.approval.check_all_command_guards",
        lambda *a, **k: command_guard_calls.append((a, k))
        or {"approved": True, "message": None},
    )
    out = plug._safety_block(
        "execute_code", {"code": "print(1)", "language": "python"}, "t", "s"
    )
    assert out is None                                      # approved → relays
    assert len(code_guard_calls) == 1
    assert code_guard_calls[0][0] == "print(1)"            # the code BODY
    assert code_guard_calls[0][1] == "desktop-relay"       # non-container env
    assert code_guard_calls[0][2].get("has_host_access") is True
    assert command_guard_calls == []                       # NOT the shell guard


def test_execute_code_denied_returns_block_result(monkeypatch):
    """When the code guard denies, the plugin returns the built-in-shaped
    error block (status=error + load-bearing `error` key) and never relays."""
    monkeypatch.setattr(
        "tools.approval.check_execute_code_guard",
        lambda code, env_type, **k: {
            "approved": False,
            "message": "BLOCKED: execute_code denied by user. Do NOT retry.",
        },
    )
    out = plug._safety_block(
        "execute_code", {"code": "import os; os.system('rm -rf ~')"}, "t", "s"
    )
    assert out is not None
    parsed = json.loads(out)
    assert parsed["status"] == "error"
    assert "blocked" in parsed["error"].lower()
