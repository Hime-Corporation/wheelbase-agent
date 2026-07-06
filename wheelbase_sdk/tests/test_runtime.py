"""Tests for task-scoped credential resolution (runtime.py + load_session integration)."""
from __future__ import annotations

import json
import threading

import pytest

from wheelbase_sdk import runtime
from wheelbase_sdk.session import load_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_cred(path, token: str, expires_at: int | None = None) -> None:
    data: dict = {"access_token": token}
    if expires_at is not None:
        data["expires_at"] = expires_at
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixture: reset runtime state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_runtime():
    """Clear any lingering task identity after each test."""
    yield
    # Reset ContextVar for the current thread
    runtime._current.set(None)
    with runtime._lock:
        runtime._by_task.clear()


# ---------------------------------------------------------------------------
# Test 1: task identity resolves credential file
# ---------------------------------------------------------------------------


def test_task_identity_resolves_credential(tmp_path, monkeypatch):
    cred_file = tmp_path / "user-session.json"
    _write_cred(cred_file, "cloud-token-abc", 9999999999)

    runtime.set_task_identity("task-1", {"credential_path": str(cred_file)})

    session = load_session()
    assert session is not None
    assert session.access_token == "cloud-token-abc"
    assert session.expires_at == 9999999999


# ---------------------------------------------------------------------------
# Test 2: no identity → falls back to legacy HERMES_HOME singleton
# ---------------------------------------------------------------------------


def test_no_identity_falls_back_to_legacy(tmp_path, monkeypatch):
    # Ensure no task identity is set (autouse fixture handles this, but be explicit)
    runtime._current.set(None)

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    legacy_file = tmp_path / "wheelbase-session.json"
    _write_cred(legacy_file, "legacy-token-xyz", 1893456000)

    session = load_session()
    assert session is not None
    assert session.access_token == "legacy-token-xyz"
    assert session.expires_at == 1893456000


# ---------------------------------------------------------------------------
# Test 3: two threads each set their own identity — no cross-bleed
# ---------------------------------------------------------------------------


def test_two_threads_no_cross_bleed(tmp_path):
    cred_a = tmp_path / "user-a.json"
    cred_b = tmp_path / "user-b.json"
    _write_cred(cred_a, "token-user-A")
    _write_cred(cred_b, "token-user-B")

    results: dict[str, str | None] = {}

    def run_thread(task_id: str, cred_file, result_key: str) -> None:
        runtime.set_task_identity(task_id, {"credential_path": str(cred_file)})
        session = load_session()
        results[result_key] = session.access_token if session else None

    t_a = threading.Thread(target=run_thread, args=("task-a", cred_a, "a"))
    t_b = threading.Thread(target=run_thread, args=("task-b", cred_b, "b"))

    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    assert results["a"] == "token-user-A"
    assert results["b"] == "token-user-B"


# ---------------------------------------------------------------------------
# Test 4: identity set but credential file missing → falls back to legacy
# ---------------------------------------------------------------------------


def test_missing_credential_file_falls_back_to_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    legacy_file = tmp_path / "wheelbase-session.json"
    _write_cred(legacy_file, "fallback-token")

    # Point to a nonexistent credential file
    runtime.set_task_identity("task-missing", {"credential_path": str(tmp_path / "nonexistent.json")})

    session = load_session()
    assert session is not None
    assert session.access_token == "fallback-token"


# ---------------------------------------------------------------------------
# Bonus: clear_task removes the stored identity
# ---------------------------------------------------------------------------


def test_clear_task_removes_identity():
    runtime.set_task_identity("task-x", {"credential_path": "/some/path"})
    with runtime._lock:
        assert "task-x" in runtime._by_task

    runtime.clear_task("task-x")
    with runtime._lock:
        assert "task-x" not in runtime._by_task


# ---------------------------------------------------------------------------
# Bonus: activate_task re-binds context to stored identity
# ---------------------------------------------------------------------------


def test_activate_task_binds_context(tmp_path):
    cred = tmp_path / "act.json"
    _write_cred(cred, "activated-token")

    runtime.set_task_identity("task-act", {"credential_path": str(cred)})
    # Clear the ContextVar to simulate a fresh thread
    runtime._current.set(None)
    assert runtime.current_identity() is None

    runtime.activate_task("task-act")
    ident = runtime.current_identity()
    assert ident is not None
    assert ident["credential_path"] == str(cred)


# ---------------------------------------------------------------------------
# Fail-closed: reused pool threads must not inherit the previous turn's
# identity (spec §5.4 cross-user exposure regression).
# ---------------------------------------------------------------------------


def test_reused_thread_fails_closed_with_reset(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    cred_file = tmp_path / "user-a.json"
    _write_cred(cred_file, "user-a-token")

    def turn_with_identity():
        token = runtime.set_task_identity("task-a", {"credential_path": str(cred_file)})
        try:
            return runtime.current_identity()
        finally:
            runtime.reset_identity(token)

    def turn_without_identity():
        # A turn that never calls set_task_identity must see NO identity.
        return runtime.current_identity()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(turn_with_identity).result()
        second = pool.submit(turn_without_identity).result()

    assert first is not None
    assert first["credential_path"] == str(cred_file)
    assert second is None, "reused thread leaked previous turn's identity"


def test_reset_identity_tolerates_foreign_token(tmp_path):
    # Cross-thread token misuse must still clear the identity, never leave it.
    holder: dict = {}

    def set_in_thread():
        holder["token"] = runtime.set_task_identity("task-x", {"credential_path": "/x"})

    t = threading.Thread(target=set_in_thread)
    t.start()
    t.join()

    runtime.set_task_identity("task-y", {"credential_path": "/y"})
    runtime.reset_identity(holder["token"])  # foreign token -> falls back to clear
    assert runtime.current_identity() is None


def test_get_task_identity_returns_copy():
    runtime.set_task_identity("task-42", {"user_id": "u1", "shell_relay_url": "wss://x"})
    got = runtime.get_task_identity("task-42")
    assert got == {"user_id": "u1", "shell_relay_url": "wss://x"}
    got["user_id"] = "MUTATED"
    # mutating the returned copy must not corrupt the registry
    assert runtime.get_task_identity("task-42")["user_id"] == "u1"


def test_get_task_identity_empty_task_id_returns_none():
    assert runtime.get_task_identity("") is None
    assert runtime.get_task_identity(None) is None  # type: ignore[arg-type]


def test_get_task_identity_unknown_task_returns_none():
    assert runtime.get_task_identity("never-registered") is None
