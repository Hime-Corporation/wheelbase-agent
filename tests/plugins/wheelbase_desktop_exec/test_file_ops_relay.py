"""Task 1: patch/search_files routed via _relay_file_ops (ShellFileOperations
over a DesktopRelayEnvironment), NOT the old _relay_file/_relay_command whose
arg extraction is wrong for these tools (the F1/F2 bug this replaces).

The fake transport here actually RUNS the exec frames' bash commands as real
subprocesses against a real tmp_path — the "desktop" in these tests IS the
local test machine — so ShellFileOperations' fuzzy-match/rg/grep logic is
exercised for real, not just mocked frame-shape assertions.
"""
from __future__ import annotations

import importlib
import json
import os
import queue
import subprocess

import pytest

plug = importlib.import_module("plugins.wheelbase-desktop-exec")
transport_mod = importlib.import_module("plugins.wheelbase-desktop-exec.transport")


class RealBashTransport(transport_mod.ExecTransport):
    """Runs `exec` frames as real bash subprocesses. `read`/`write` frames are
    not used by the file-ops relay path (it emits only `exec` frames via
    DesktopRelayEnvironment), so those are left unimplemented on purpose."""

    def __init__(self, connected=True):
        self.sent = []
        self._connected = connected
        self._q: dict[str, "queue.Queue[dict]"] = {}

    def send(self, frame):
        if not self._connected:
            raise transport_mod.PreDispatchError("no relay")
        self.sent.append(dict(frame))
        rid = frame["request_id"]
        q = self._q.setdefault(rid, queue.Queue())
        if frame["type"] == "exec":
            proc = subprocess.run(
                ["bash", "-c", frame["command"]],
                capture_output=True, text=True,
            )
            q.put({"type": "chunk", "data": (proc.stdout or "") + (proc.stderr or "")})
            q.put({"type": "exit", "exit_code": proc.returncode})
        # interrupt/cancel frames: no-op, nothing scripted needs cancellation.

    def recv(self, request_id, timeout=None):
        return self._q.setdefault(request_id, queue.Queue()).get(timeout=timeout or 10)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _ident(monkeypatch, tmp_path):
    # cwd MUST be a real, existing directory: DesktopRelayEnvironment's
    # wrapped script does `builtin cd -- <cwd> || exit 126` before every
    # command, so a fake path here (e.g. "/work") would make EVERY relayed
    # exec fail closed with exit 126 regardless of the (absolute) paths the
    # tool args pass — using the test's own tmp_path keeps `cd` real.
    from wheelbase_sdk import runtime
    runtime.set_task_identity("t-desk", {"user_id": "u", "shell_relay_url": "wss://relay",
                                         "workspace_root": str(tmp_path), "cwd": str(tmp_path)})
    yield
    runtime._current.set(None)
    with runtime._lock:
        runtime._by_task.clear()


def _no_next():
    def nc(args):
        raise AssertionError("next_call must NOT run — the desktop relay must have handled it")
    return nc


def _wire_real_transport(monkeypatch):
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: RealBashTransport())
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)


def test_routed_tools_include_patch_and_search_files(plug=plug):
    assert "patch" in plug.ROUTED_TOOLS
    assert "search_files" in plug.ROUTED_TOOLS


def test_patch_replace_mode_edits_file_on_disk(monkeypatch, tmp_path):
    f = tmp_path / "a.py"
    f.write_text("def foo():\n    return 1\n")
    _wire_real_transport(monkeypatch)

    out = plug.route_or_passthrough(
        tool_name="patch",
        args={"mode": "replace", "path": str(f),
              "old_string": "return 1", "new_string": "return 2"},
        next_call=_no_next(), task_id="t-desk", tool_call_id="p1",
    )
    parsed = json.loads(out)
    assert parsed["success"] is True
    assert "diff" in parsed
    assert parsed["files_modified"] == [str(f)]
    assert f.read_text() == "def foo():\n    return 2\n"


def test_patch_v4a_mode_applies(monkeypatch, tmp_path):
    f = tmp_path / "b.txt"
    f.write_text("line1\nline2\nline3\n")
    _wire_real_transport(monkeypatch)

    patch_content = (
        "*** Begin Patch\n"
        f"*** Update File: {f}\n"
        "@@\n"
        " line1\n"
        "-line2\n"
        "+line2-changed\n"
        " line3\n"
        "*** End Patch"
    )
    out = plug.route_or_passthrough(
        tool_name="patch", args={"mode": "patch", "patch": patch_content},
        next_call=_no_next(), task_id="t-desk", tool_call_id="p2",
    )
    parsed = json.loads(out)
    assert parsed["success"] is True, parsed
    assert f.read_text() == "line1\nline2-changed\nline3\n"


def test_patch_content_with_special_chars_round_trips(monkeypatch, tmp_path):
    # $ / backticks / quotes / embedded newlines must survive the heredoc
    # write path unchanged (production-proven Daytona heredoc pattern).
    f = tmp_path / "c.sh"
    original = 'echo "hi $USER"\n'
    f.write_text(original)
    _wire_real_transport(monkeypatch)

    new_content = 'echo "bye `whoami` $HOME"\nprintf "%s\\n" "line with \'quotes\'"\n'
    out = plug.route_or_passthrough(
        tool_name="patch",
        args={"mode": "replace", "path": str(f),
              "old_string": original, "new_string": new_content},
        next_call=_no_next(), task_id="t-desk", tool_call_id="p3",
    )
    parsed = json.loads(out)
    assert parsed["success"] is True, parsed
    assert f.read_text() == new_content


def test_patch_missing_args_returns_tool_error_shape(monkeypatch, tmp_path):
    _wire_real_transport(monkeypatch)
    out = plug.route_or_passthrough(
        tool_name="patch", args={"mode": "replace"},  # no path
        next_call=_no_next(), task_id="t-desk", tool_call_id="p4",
    )
    parsed = json.loads(out)
    assert parsed == {"error": "path required"}


def test_search_files_content_mode(monkeypatch, tmp_path):
    (tmp_path / "one.py").write_text("def alpha():\n    pass\n")
    (tmp_path / "two.py").write_text("def beta():\n    pass\n")
    _wire_real_transport(monkeypatch)

    out = plug.route_or_passthrough(
        tool_name="search_files",
        args={"pattern": "def ", "path": str(tmp_path), "target": "content"},
        next_call=_no_next(), task_id="t-desk", tool_call_id="s1",
    )
    parsed = json.loads(out)
    assert parsed["total_count"] == 2
    assert "matches" in parsed or "matches_text" in parsed


def test_search_files_files_mode(monkeypatch, tmp_path):
    (tmp_path / "one.py").write_text("x = 1\n")
    (tmp_path / "two.txt").write_text("y = 2\n")
    _wire_real_transport(monkeypatch)

    out = plug.route_or_passthrough(
        tool_name="search_files",
        args={"pattern": "*.py", "path": str(tmp_path), "target": "files"},
        next_call=_no_next(), task_id="t-desk", tool_call_id="s2",
    )
    parsed = json.loads(out)
    assert any(p.endswith("one.py") for p in parsed.get("files", []))
    assert all(not p.endswith("two.txt") for p in parsed.get("files", []))


def test_search_files_count_output_mode(monkeypatch, tmp_path):
    (tmp_path / "one.py").write_text("def a():\n    pass\ndef b():\n    pass\n")
    _wire_real_transport(monkeypatch)

    out = plug.route_or_passthrough(
        tool_name="search_files",
        args={"pattern": "def ", "path": str(tmp_path), "target": "content",
              "output_mode": "count"},
        next_call=_no_next(), task_id="t-desk", tool_call_id="s3",
    )
    parsed = json.loads(out)
    assert "counts" in parsed
    assert parsed["total_count"] == 2


def test_patch_without_relay_url_falls_back_to_next_call(monkeypatch):
    from wheelbase_sdk import runtime
    runtime.set_task_identity("t-mobile", {"user_id": "u", "shell_relay_url": ""})
    calls = {"n": 0}

    def nc(args):
        calls["n"] += 1
        return "CLOUD"

    monkeypatch.setattr(plug, "_make_transport",
                        lambda *a, **k: pytest.fail("must not build a transport"))
    out = plug.route_or_passthrough(
        tool_name="patch",
        args={"mode": "replace", "path": "/x", "old_string": "a", "new_string": "b"},
        next_call=nc, task_id="t-mobile", tool_call_id="p5",
    )
    assert out == "CLOUD"
    assert calls["n"] == 1


def test_search_files_without_relay_url_falls_back_to_next_call(monkeypatch):
    from wheelbase_sdk import runtime
    runtime.set_task_identity("t-mobile2", {"user_id": "u", "shell_relay_url": ""})
    calls = {"n": 0}

    def nc(args):
        calls["n"] += 1
        return "CLOUD"

    monkeypatch.setattr(plug, "_make_transport",
                        lambda *a, **k: pytest.fail("must not build a transport"))
    out = plug.route_or_passthrough(
        tool_name="search_files", args={"pattern": "x"},
        next_call=nc, task_id="t-mobile2", tool_call_id="s4",
    )
    assert out == "CLOUD"
    assert calls["n"] == 1


# --- V4A (mode="patch") safety-check bypass fix -----------------------------
#
# _file_path(args) only reads args["path"]; a V4A patch carries no such arg —
# its target(s) live INSIDE args["patch"] under "*** Update/Add/Delete/Move
# File:" headers. Without a dedicated guard, _safety_block_files would see
# path == "" and neither _check_sensitive_path nor traversal rejection would
# ever run for a V4A patch, unlike "replace" mode (whose explicit path=
# argument IS checked). These tests exercise the REAL _safety_block (NOT
# stubbed, unlike the tests above) so the fix's wiring into the actual
# safety-chain seam is proven, not just the helper in isolation.

def test_v4a_patch_targeting_hermes_config_is_blocked(monkeypatch, tmp_path):
    # Must be blocked BEFORE any transport is built (fail-closed, never
    # relays to the desktop) and must never call next_call either.
    #
    # Use hermes_cli.config.get_config_path() rather than the literal
    # "~/.hermes/config.yaml" string: tests/conftest.py redirects HERMES_HOME
    # to a per-test tempdir (hermetic-test invariant), so the guard's real
    # comparison target is THAT path, not the developer's actual home dir.
    from hermes_cli.config import get_config_path
    hermes_config_path = get_config_path()

    monkeypatch.setattr(plug, "_make_transport",
                        lambda *a, **k: pytest.fail("must not relay a blocked V4A patch"))
    patch_content = (
        "*** Begin Patch\n"
        f"*** Update File: {hermes_config_path}\n"
        "@@\n"
        "-approvals:\n"
        "+approvals: disabled\n"
        "*** End Patch"
    )
    out = plug.route_or_passthrough(
        tool_name="patch", args={"mode": "patch", "patch": patch_content},
        next_call=_no_next(), task_id="t-desk", tool_call_id="v1",
    )
    parsed = json.loads(out)
    assert parsed.get("status") == "error"
    assert parsed.get("success") is False
    # _check_sensitive_path checks system-path prefixes before the
    # Hermes-config-specific message; under the per-test HERMES_HOME
    # redirect the tempdir itself can also fall under a sensitive prefix
    # (e.g. macOS's /private/var/...) — either message proves the SAME
    # thing: the V4A header path reached _check_sensitive_path at all,
    # which it never did before this fix.
    err = (parsed.get("error") or "").lower()
    assert "hermes config" in err or "sensitive system path" in err, parsed


def test_v4a_patch_move_file_traversal_escape_is_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(plug, "_make_transport",
                        lambda *a, **k: pytest.fail("must not relay a blocked V4A patch"))
    patch_content = (
        "*** Begin Patch\n"
        "*** Move File: a.txt -> ../../../etc/passwd\n"
        "*** End Patch"
    )
    out = plug.route_or_passthrough(
        tool_name="patch", args={"mode": "patch", "patch": patch_content},
        next_call=_no_next(), task_id="t-desk", tool_call_id="v2",
    )
    parsed = json.loads(out)
    assert parsed.get("status") == "error"
    assert "traversal" in (parsed.get("error") or "").lower()


def test_v4a_patch_within_workspace_still_applies_through_real_safety_chain(monkeypatch):
    # Same shape as test_patch_v4a_mode_applies, but does NOT stub
    # _safety_block — proves the new V4A header guard does not false-positive
    # block a benign in-workspace patch.
    #
    # Deliberately NOT pytest's tmp_path fixture: on macOS it resolves under
    # /private/var/folders/..., which _check_sensitive_path's own
    # _SENSITIVE_PATH_PREFIXES treats as a system path (unrelated to this
    # fix — the very reason test_patch_v4a_mode_applies above stubs
    # _safety_block out entirely). Use a workspace under the real home dir
    # instead so this collision doesn't mask what's actually being tested.
    import shutil
    import tempfile
    from pathlib import Path

    workdir = tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix=".wb-v4a-test-")
    try:
        f = Path(workdir) / "d.txt"
        f.write_text("line1\nline2\nline3\n")
        monkeypatch.setattr(plug, "_make_transport", lambda url, ident: RealBashTransport())

        patch_content = (
            "*** Begin Patch\n"
            f"*** Update File: {f}\n"
            "@@\n"
            " line1\n"
            "-line2\n"
            "+line2-changed\n"
            " line3\n"
            "*** End Patch"
        )
        out = plug.route_or_passthrough(
            tool_name="patch", args={"mode": "patch", "patch": patch_content},
            next_call=_no_next(), task_id="t-desk", tool_call_id="v3",
        )
        parsed = json.loads(out)
        assert parsed["success"] is True, parsed
        assert f.read_text() == "line1\nline2-changed\nline3\n"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
