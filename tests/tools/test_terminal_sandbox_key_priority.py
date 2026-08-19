"""Regression tests for the post-merge container-key/config resolution in
``tools/terminal_tool.py`` (upstream Hermes merge, 2026-08-18).

These cover gaps NOT already exercised by ``test_docker_session_isolation.py``
(alias/persistence keying), ``tests/test_wheelbase_inject.py`` (sandbox_key
per-user derivation), ``test_terminal_error_redaction.py``, or
``test_terminal_heredoc_background_guard.py``:

* ``docker_volumes`` alone (no ``sandbox_key``) is now part of
  ``_ISOLATION_OVERRIDE_KEYS`` and must trigger per-task isolation on its own,
  independent of the docker-session-isolation env toggle.
* ``_container_config_from_config``'s new ``overrides`` parameter: per-task
  volumes append (never replace) global volumes, and per-task env values win
  over global env values.
* Both call sites — the lazy ``ensure_task_env`` bring-up and the normal
  ``terminal_tool`` get-or-create path — must thread the resolved per-task
  overrides into ``_container_config_from_config``. Before this merge,
  ``ensure_task_env`` called it with globals only; that regression is pinned
  here directly against the call site, not just the helper.
* Daytona ``always_on`` flows from config through
  ``_container_config_from_config`` and is honored by the idle reaper
  (``_cleanup_inactive_envs``), which must refresh rather than tear down an
  always-on environment.
"""

import time

import tools.terminal_tool as terminal_tool


def _minimal_config(**overrides):
    base = {
        "env_type": "docker",
        "docker_image": "global:latest",
        "cwd": "/root",
        "timeout": 60,
        "lifetime_seconds": 3600,
        "docker_volumes": ["global-vol:/data"],
        "docker_env": {"GLOBAL_ONLY": "1", "SHARED": "global"},
        "docker_mount_cwd_to_workspace": False,
        "host_cwd": None,
        "container_cpu": 1,
        "container_memory": 5120,
        "container_disk": 51200,
        "container_persistent": True,
        "daytona_always_on": False,
        "modal_mode": "auto",
        "vercel_runtime": "",
        "docker_forward_env": [],
        "docker_run_as_host_user": False,
        "docker_extra_args": [],
        "docker_shm_size": "1g",
        "docker_network": True,
        "docker_persist_across_processes": True,
        "docker_orphan_reaper": True,
    }
    base.update(overrides)
    return base


class TestDockerVolumesOverrideIsolation:
    """``docker_volumes`` alone must isolate, matching upstream's image/env_type keys."""

    def test_volumes_only_override_gets_own_task_id(self, monkeypatch):
        # Explicitly disable the unrelated docker-session-isolation toggle so
        # the isolation decision can only be coming from the override check.
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setenv("TERMINAL_CONTAINER_PERSISTENT", "true")
        monkeypatch.setattr(terminal_tool, "_terminal_config_bridge_attempted", True)
        task_id = "bench-volumes-only"
        monkeypatch.setattr(
            terminal_tool, "_task_env_overrides", {task_id: {"docker_volumes": ["v:/x"]}}
        )
        assert terminal_tool._resolve_container_task_id(task_id) == task_id

    def test_docker_volumes_is_in_module_level_isolation_keys(self):
        assert "docker_volumes" in terminal_tool._ISOLATION_OVERRIDE_KEYS

    def test_bare_cwd_override_stays_non_isolating(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setenv("TERMINAL_CONTAINER_PERSISTENT", "true")
        monkeypatch.setattr(terminal_tool, "_terminal_config_bridge_attempted", True)
        task_id = "acp-cwd-only"
        monkeypatch.setattr(
            terminal_tool, "_task_env_overrides", {task_id: {"cwd": "/workspace/foo"}}
        )
        assert terminal_tool._resolve_container_task_id(task_id) == "default"


class TestContainerConfigFromConfigOverrides:
    def test_no_overrides_matches_globals(self):
        config = _minimal_config()
        cc = terminal_tool._container_config_from_config(config)
        assert cc["docker_volumes"] == ["global-vol:/data"]
        assert cc["docker_env"] == {"GLOBAL_ONLY": "1", "SHARED": "global"}

    def test_task_volumes_append_to_global_volumes(self):
        config = _minimal_config()
        cc = terminal_tool._container_config_from_config(
            config, {"docker_volumes": ["task-vol:/task"]}
        )
        assert cc["docker_volumes"] == ["global-vol:/data", "task-vol:/task"]

    def test_task_env_values_win_over_global(self):
        config = _minimal_config()
        cc = terminal_tool._container_config_from_config(
            config, {"docker_env": {"SHARED": "task", "TASK_ONLY": "2"}}
        )
        assert cc["docker_env"] == {
            "GLOBAL_ONLY": "1",
            "SHARED": "task",
            "TASK_ONLY": "2",
        }

    def test_daytona_always_on_passed_through(self):
        config = _minimal_config(daytona_always_on=True)
        cc = terminal_tool._container_config_from_config(config)
        assert cc["daytona_always_on"] is True


class TestEnsureTaskEnvThreadsOverrides:
    """Pin the exact regression: ensure_task_env must not build container_config
    from globals only — the lazy bring-up path must see the same per-task
    overrides as the normal terminal_tool get-or-create path."""

    def test_ensure_task_env_passes_overrides_into_container_config(self, monkeypatch):
        captured = {}

        def fake_create_environment(**kwargs):
            captured.update(kwargs)
            return object()

        task_id = "ensure-task-env-overrides"
        monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_config())
        monkeypatch.setattr(terminal_tool, "_create_environment", fake_create_environment)
        monkeypatch.setattr(
            terminal_tool,
            "_task_env_overrides",
            {
                task_id: {
                    "docker_volumes": ["task-vol:/task"],
                    "docker_env": {"SHARED": "task", "TASK_ONLY": "2"},
                }
            },
        )
        monkeypatch.setattr(terminal_tool, "_active_environments", {})
        monkeypatch.setattr(terminal_tool, "_last_activity", {})
        monkeypatch.setattr(terminal_tool, "_creation_locks", {})

        result = terminal_tool.ensure_task_env(task_id)

        assert result is not None
        cc = captured["container_config"]
        assert cc["docker_volumes"] == ["global-vol:/data", "task-vol:/task"]
        assert cc["docker_env"] == {
            "GLOBAL_ONLY": "1",
            "SHARED": "task",
            "TASK_ONLY": "2",
        }


class TestDaytonaAlwaysOnIdleReaper:
    def test_always_on_env_is_refreshed_not_reaped(self, monkeypatch):
        class FakeAlwaysOnEnv:
            _always_on = True

        task_id = "daytona-always-on-user"
        stale_time = time.time() - 10_000
        monkeypatch.setattr(
            terminal_tool, "_active_environments", {task_id: FakeAlwaysOnEnv()}
        )
        monkeypatch.setattr(terminal_tool, "_last_activity", {task_id: stale_time})
        monkeypatch.setattr(terminal_tool, "_creation_locks", {})

        terminal_tool._cleanup_inactive_envs(lifetime_seconds=1)

        assert task_id in terminal_tool._active_environments
        assert terminal_tool._last_activity[task_id] > stale_time

    def test_non_always_on_env_is_reaped(self, monkeypatch):
        class FakeEnv:
            _always_on = False

        task_id = "daytona-not-always-on"
        stale_time = time.time() - 10_000
        monkeypatch.setattr(terminal_tool, "_active_environments", {task_id: FakeEnv()})
        monkeypatch.setattr(terminal_tool, "_last_activity", {task_id: stale_time})
        monkeypatch.setattr(terminal_tool, "_creation_locks", {})

        terminal_tool._cleanup_inactive_envs(lifetime_seconds=1)

        assert task_id not in terminal_tool._active_environments
        assert task_id not in terminal_tool._last_activity
