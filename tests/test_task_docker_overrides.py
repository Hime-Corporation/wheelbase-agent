"""Tests for per-task docker_volumes / docker_env overrides (B4 — multi-user
cloud gateway). All tests are pure logic — no docker daemon required."""

import pytest

from tools import terminal_tool


# ---------------------------------------------------------------------------
# Fixture: clean up _task_env_overrides before/after each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_overrides():
    before = dict(terminal_tool._task_env_overrides)
    terminal_tool._task_env_overrides.clear()
    yield
    terminal_tool._task_env_overrides.clear()
    terminal_tool._task_env_overrides.update(before)


# ---------------------------------------------------------------------------
# Helpers: compute merged docker kwargs the same way terminal_tool does
# ---------------------------------------------------------------------------


def _compute_merged_docker_kwargs(task_id, global_volumes=None, global_docker_env=None):
    """Replicate the merge logic from terminal_tool's container_config block.

    Returns (merged_volumes, merged_docker_env) for the given task.
    """
    overrides = (
        terminal_tool._task_env_overrides.get(task_id)
        or terminal_tool._task_env_overrides.get(
            terminal_tool._resolve_container_task_id(task_id), {}
        )
    )
    global_volumes = global_volumes or []
    global_docker_env = global_docker_env or {}

    task_volumes = overrides.get("docker_volumes") or []
    task_docker_env = overrides.get("docker_env") or {}
    merged_volumes = list(global_volumes) + list(task_volumes)
    merged_docker_env = {**global_docker_env, **task_docker_env}
    return merged_volumes, merged_docker_env


# ---------------------------------------------------------------------------
# Test 1: docker_volumes and docker_env land in resolved kwargs;
#         t1 gets per-task isolation, t2 does not.
# ---------------------------------------------------------------------------


def test_volumes_and_env_in_resolved_kwargs():
    terminal_tool.register_task_env_overrides(
        "t1",
        {
            "docker_volumes": ["wb-ws-u1:/workspace"],
            "docker_env": {"WHEELBASE_USER_ID": "u1"},
        },
    )

    # t1 must be isolated (docker_volumes in ISOLATION_KEYS)
    assert terminal_tool._resolve_container_task_id("t1") == "t1"

    # t2 (no overrides) collapses to "default"
    assert terminal_tool._resolve_container_task_id("t2") == "default"

    # Volumes and env land in merged config for t1
    volumes, docker_env = _compute_merged_docker_kwargs("t1")
    assert "wb-ws-u1:/workspace" in volumes
    assert docker_env.get("WHEELBASE_USER_ID") == "u1"

    # t2 gets nothing extra
    volumes_t2, env_t2 = _compute_merged_docker_kwargs("t2")
    assert volumes_t2 == []
    assert env_t2 == {}


# ---------------------------------------------------------------------------
# Test 2: repeat-registration merges (does not replace)
# ---------------------------------------------------------------------------


def test_repeat_registration_merges():
    # First call: docker_volumes from the cloud injector
    terminal_tool.register_task_env_overrides(
        "sess-1",
        {"docker_volumes": ["wb-ws-u2:/workspace"], "docker_env": {"USER_ID": "u2"}},
    )

    # Second call: cwd-only update from the dashboard server
    terminal_tool.register_task_env_overrides("sess-1", {"cwd": "/workspace/project"})

    overrides = terminal_tool._task_env_overrides["sess-1"]
    # Both slices coexist
    assert overrides.get("docker_volumes") == ["wb-ws-u2:/workspace"]
    assert overrides.get("docker_env") == {"USER_ID": "u2"}
    assert overrides.get("cwd") == "/workspace/project"


# ---------------------------------------------------------------------------
# Test 3: two tasks with different volumes resolve independently (no cross-bleed)
# ---------------------------------------------------------------------------


def test_two_tasks_independent_volumes():
    terminal_tool.register_task_env_overrides(
        "user-a",
        {"docker_volumes": ["wb-ws-a:/workspace"], "docker_env": {"UID": "a"}},
    )
    terminal_tool.register_task_env_overrides(
        "user-b",
        {"docker_volumes": ["wb-ws-b:/workspace"], "docker_env": {"UID": "b"}},
    )

    vol_a, env_a = _compute_merged_docker_kwargs("user-a")
    vol_b, env_b = _compute_merged_docker_kwargs("user-b")

    assert "wb-ws-a:/workspace" in vol_a
    assert "wb-ws-b:/workspace" not in vol_a
    assert env_a["UID"] == "a"

    assert "wb-ws-b:/workspace" in vol_b
    assert "wb-ws-a:/workspace" not in vol_b
    assert env_b["UID"] == "b"


# ---------------------------------------------------------------------------
# Test 4: global docker_volumes are preserved; task values appended / win
# ---------------------------------------------------------------------------


def test_global_and_task_volumes_merged():
    terminal_tool.register_task_env_overrides(
        "t1",
        {
            "docker_volumes": ["wb-ws-u1:/workspace"],
            "docker_env": {"FROM_TASK": "yes", "SHARED": "task"},
        },
    )

    global_vols = ["shared-cache:/cache"]
    global_env = {"GLOBAL": "1", "SHARED": "global"}

    merged_vols, merged_env = _compute_merged_docker_kwargs(
        "t1",
        global_volumes=global_vols,
        global_docker_env=global_env,
    )

    # Global volume preserved, task volume appended
    assert "shared-cache:/cache" in merged_vols
    assert "wb-ws-u1:/workspace" in merged_vols

    # Task env wins on conflict
    assert merged_env["SHARED"] == "task"
    assert merged_env["GLOBAL"] == "1"
    assert merged_env["FROM_TASK"] == "yes"
