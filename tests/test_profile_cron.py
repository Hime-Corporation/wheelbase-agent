"""Tests for the per-user background cron sweeper (Workstream B)."""
from __future__ import annotations

import sys
from pathlib import Path

from tui_gateway.profile_cron import CronSweeper, _default_run_tick


def _make_profile_with_jobs(root: Path, name: str) -> Path:
    d = root / name
    (d / "cron").mkdir(parents=True)
    (d / "cron" / "jobs.json").write_text("[]")
    return d


class FakeProc:
    def __init__(self, events, idx):
        self._events = events
        self._idx = idx

    def wait(self):
        self._events.append(("wait", self._idx))
        return 0


def test_profiles_with_jobs_filters(tmp_path):
    a = _make_profile_with_jobs(tmp_path, "wb-user-aaaa")
    (tmp_path / "wb-user-bbbb").mkdir()                # no cron/jobs.json -> skip
    _make_profile_with_jobs(tmp_path, "notwb-cccc")    # wrong prefix -> skip

    sweeper = CronSweeper(tmp_path)
    assert sweeper.profiles_with_jobs() == [a]


def test_sweep_once_launches_tick_per_profile(tmp_path):
    a = _make_profile_with_jobs(tmp_path, "wb-user-aaaa")
    b = _make_profile_with_jobs(tmp_path, "wb-user-bbbb")
    calls = []

    def fake_run_tick(profile_dir):
        calls.append(profile_dir)
        return FakeProc([], 0)

    sweeper = CronSweeper(tmp_path, run_tick=fake_run_tick)
    launched = sweeper.sweep_once()

    assert launched == 2
    assert sorted(calls) == [a, b]


def test_sweep_once_respects_max_concurrent(tmp_path):
    _make_profile_with_jobs(tmp_path, "wb-user-aaaa")
    _make_profile_with_jobs(tmp_path, "wb-user-bbbb")
    _make_profile_with_jobs(tmp_path, "wb-user-cccc")
    events = []
    counter = {"n": 0}

    def fake_run_tick(profile_dir):
        events.append(("launch", counter["n"]))
        proc = FakeProc(events, counter["n"])
        counter["n"] += 1
        return proc

    sweeper = CronSweeper(tmp_path, run_tick=fake_run_tick, max_concurrent=1)
    sweeper.sweep_once()

    # cap=1 => each launch is waited before the next launch.
    assert events == [
        ("launch", 0), ("wait", 0),
        ("launch", 1), ("wait", 1),
        ("launch", 2), ("wait", 2),
    ]


def test_default_run_tick_builds_cmd(tmp_path, monkeypatch):
    captured = {}

    class P:
        pass

    def fake_popen(cmd, *, env, stdin):
        captured["cmd"] = cmd
        captured["env"] = env
        return P()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    _default_run_tick(tmp_path / "wb-user-aaaa")

    assert captured["cmd"] == [sys.executable, "-m", "hermes_cli.main", "cron", "tick"]
    assert captured["env"]["HERMES_HOME"] == str(tmp_path / "wb-user-aaaa")


def test_main_once_empty(tmp_path, monkeypatch):
    import tui_gateway.profile_cron as pc

    monkeypatch.setattr("tui_gateway.profile_router.profiles_root", lambda: tmp_path)
    assert pc.main_once() == 0
