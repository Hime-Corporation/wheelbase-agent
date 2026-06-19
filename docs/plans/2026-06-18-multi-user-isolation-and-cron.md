# Multi-User Isolation + Per-User Background Cron — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Disable cross-profile reads (`session_search`) for non-admin `wb-<uid>` profiles via provisioning config, and make per-user background cron jobs actually fire (today they never do).

**Architecture:** Two independent workstreams in `tui_gateway`. (A) inject/back-fill `agent.disabled_toolsets: [session_search]` into per-user `config.yaml` from `provision_profile`; admin runs as the Hermes root and is untouched. (B) a new `CronSweeper` driven by a thread in the router `main()` (plus a host-cron entrypoint) that runs `hermes cron tick` per profile on a schedule, independent of whether the user's child process is alive.

**Tech Stack:** Python 3.11, `pytest`, `pyyaml`, FastAPI/uvicorn (router), `subprocess`.

**Spec:** `docs/design/2026-06-18-multi-user-isolation-and-cron.md`

## Global Constraints

- **Enforcement is config-only** — no in-child code guard on `session_search` this plan (spec §2a.2). Do NOT add per-tool runtime guards here.
- **Cron uses the full toolset** — no tool restriction (spec §2a.3). Unattended-hang safety is a deploy-time `HERMES_CRON_TIMEOUT` knob (Task B5), not code.
- **`cron tick` is authoritative for due-evaluation and locking.** The sweeper never parses `jobs.json` schedules; it runs `cron tick`, which evaluates due jobs and holds the per-home `cron/.tick.lock`. Running it redundantly (router sweep + host cron) is therefore safe.
- **Run tests from the `wheelbase-agent` repo root:** `pytest tests/<file> -v`.
- **Every commit message ends with these two trailers** (shown once here; append to every commit in this plan):
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01QAvfcmCc6keQ6zjTJbdDsS
  ```
- Branch: `feat/multi-user-isolation-cron` (already created; the spec is committed there).

## File Structure

| File | Responsibility |
|---|---|
| `tui_gateway/profile_router.py` (modify) | Add `PROFILE_DISABLED_TOOLSETS` const; inject it into the first-time `config.yaml`; add `_ensure_session_search_disabled` back-fill; start the cron-sweep thread in `main()`. |
| `tui_gateway/profile_cron.py` (create) | `CronSweeper` (profile discovery, `sweep_once`, `sweep_forever`), `_default_run_tick`, and `main_once()` host-cron entrypoint. Isolated from the router so it stays focused and testable. |
| `tests/test_profile_router.py` (modify) | Tests for the disabled-toolset injection/back-fill and the router→sweeper wiring. |
| `tests/test_profile_cron.py` (create) | Tests for `CronSweeper` discovery, `sweep_once`, concurrency cap, and `_default_run_tick`. |

---

## Task A1: Inject `disabled_toolsets` into new profile configs

**Files:**
- Modify: `tui_gateway/profile_router.py` (const after `PROFILE_PLUGINS` ~`:31`; config dict ~`:144-154`)
- Test: `tests/test_profile_router.py`

**Interfaces:**
- Produces: module constant `PROFILE_DISABLED_TOOLSETS: tuple[str, ...] = ("session_search",)`; new profiles' `config.yaml` contains `agent.disabled_toolsets == list(PROFILE_DISABLED_TOOLSETS)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_profile_router.py`:

```python
def test_provision_writes_disabled_toolsets(tmp_path):
    from tui_gateway.profile_router import PROFILE_DISABLED_TOOLSETS

    profile_dir = tmp_path / "wb-user-aaaa"
    provision_profile(profile_dir, seed_skills=lambda p: None)

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text())
    assert cfg["agent"]["disabled_toolsets"] == list(PROFILE_DISABLED_TOOLSETS)
    assert "session_search" in cfg["agent"]["disabled_toolsets"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_router.py::test_provision_writes_disabled_toolsets -v`
Expected: FAIL — `ImportError: cannot import name 'PROFILE_DISABLED_TOOLSETS'`.

- [ ] **Step 3: Add the constant**

In `tui_gateway/profile_router.py`, immediately after the `PROFILE_PLUGINS = (...)` tuple (~`:31`), add:

```python
# Toolsets removed from every per-user (wb-<uid>) profile. session_search can
# open ANY profile's state.db by path (in-process, bypasses the Daytona
# sandbox), so it must never be exposed to a non-admin user. The admin runs as
# the Hermes root profile, which never goes through provision_profile and so
# keeps session_search.
PROFILE_DISABLED_TOOLSETS = ("session_search",)
```

- [ ] **Step 4: Inject it into the first-time config dict**

In `provision_profile`, the `config = {...}` literal written when `config.yaml` is absent (~`:144`), add the `agent` key right after `plugins`:

```python
        config = {
            "model": os.environ.get("WHEELBASE_PROFILE_MODEL", "minimax/minimax-m3"),
            "provider": os.environ.get("WHEELBASE_PROFILE_PROVIDER", "openrouter"),
            "skin": os.environ.get("WHEELBASE_PROFILE_SKIN", "wheelbase"),
            "plugins": {"enabled": list(PROFILE_PLUGINS)},
            "agent": {"disabled_toolsets": list(PROFILE_DISABLED_TOOLSETS)},
            "platform_toolsets": {
                "cli": {
                    "tools": ["todo"],
                },
            },
        }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_profile_router.py::test_provision_writes_disabled_toolsets -v`
Expected: PASS.

- [ ] **Step 6: Run the existing provision tests (no regressions)**

Run: `pytest tests/test_profile_router.py -k provision -v`
Expected: all PASS (the idempotency test still holds — the new key is part of the first-time write, so re-provision is still byte-for-byte identical).

- [ ] **Step 7: Commit**

```bash
git add tui_gateway/profile_router.py tests/test_profile_router.py
git commit  # message: "feat(router): disable session_search in new wb-<uid> profile configs" + standard trailers
```

---

## Task A2: Back-fill `disabled_toolsets` into existing profiles

**Files:**
- Modify: `tui_gateway/profile_router.py` (new `_ensure_session_search_disabled`; call it in the `else` branch ~`:156-157`)
- Test: `tests/test_profile_router.py`

**Interfaces:**
- Consumes: `PROFILE_DISABLED_TOOLSETS` (Task A1).
- Produces: `_ensure_session_search_disabled(config_path: Path) -> bool` (mirrors `_ensure_profile_plugins_enabled`; returns `True` iff it rewrote the file). Called for already-existing profiles so the guard reaches pre-cutover/stale homes on next provision.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_profile_router.py`:

```python
def test_provision_backfills_disabled_toolsets_for_existing_profile(tmp_path):
    from tui_gateway.profile_router import PROFILE_DISABLED_TOOLSETS

    profile_dir = tmp_path / "wb-user-bbbb"
    profile_dir.mkdir(parents=True)
    # Pre-guard profile: model only, no agent block at all.
    (profile_dir / "config.yaml").write_text("model: custom/model\n")

    provision_profile(profile_dir, seed_skills=lambda p: None)

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text())
    assert cfg["model"] == "custom/model"
    assert cfg["agent"]["disabled_toolsets"] == list(PROFILE_DISABLED_TOOLSETS)


def test_provision_preserves_other_disabled_toolsets(tmp_path):
    profile_dir = tmp_path / "wb-user-cccc"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        yaml.safe_dump({"agent": {"disabled_toolsets": ["web"]}}, sort_keys=False),
        encoding="utf-8",
    )

    provision_profile(profile_dir, seed_skills=lambda p: None)

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text())
    # Order-preserving union: existing disable kept, session_search appended once.
    assert cfg["agent"]["disabled_toolsets"] == ["web", "session_search"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_profile_router.py -k "disabled_toolsets" -v`
Expected: FAIL — existing profiles get no `agent.disabled_toolsets` (the `else` branch only back-fills plugins today).

- [ ] **Step 3: Add the back-fill function**

In `tui_gateway/profile_router.py`, directly after `_ensure_profile_plugins_enabled` (~`:124`), add:

```python
def _ensure_session_search_disabled(config_path: Path) -> bool:
    """Back-fill PROFILE_DISABLED_TOOLSETS into an existing profile config.yaml.

    User (wb-<uid>) profiles must not expose cross-profile read tools such as
    session_search. Profiles created before this guard (or migrated from the
    shared store) carry no ``agent.disabled_toolsets`` list. Merge the required
    disables in (order-preserving union) without disturbing other edits. Returns
    ``True`` if the file was rewritten.
    """
    import yaml

    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("could not read profile config for toolset back-fill: %s", config_path)
        return False
    if not isinstance(config, dict):
        return False

    agent = config.get("agent")
    if not isinstance(agent, dict):
        agent = {}
    disabled = agent.get("disabled_toolsets")
    if not isinstance(disabled, list):
        disabled = []

    missing = [t for t in PROFILE_DISABLED_TOOLSETS if t not in disabled]
    if not missing:
        return False

    agent["disabled_toolsets"] = list(disabled) + missing
    config["agent"] = agent
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    logger.info("disabled cross-profile toolsets in %s: added %s", config_path, missing)
    return True
```

- [ ] **Step 4: Call it for existing profiles**

In `provision_profile`, the `else` branch (~`:156-157`) currently calls only the plugin back-fill. Change it to:

```python
    else:
        _ensure_profile_plugins_enabled(config_path)
        _ensure_session_search_disabled(config_path)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_profile_router.py -k "disabled_toolsets" -v`
Expected: both PASS.

- [ ] **Step 6: Full module regression**

Run: `pytest tests/test_profile_router.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add tui_gateway/profile_router.py tests/test_profile_router.py
git commit  # "feat(router): back-fill session_search disable into existing profiles" + standard trailers
```

---

## Task B1: `CronSweeper` profile discovery

**Files:**
- Create: `tui_gateway/profile_cron.py`
- Test: `tests/test_profile_cron.py`

**Interfaces:**
- Produces: `class CronSweeper(profiles_root: Path, *, run_tick=..., max_concurrent=8)`; `CronSweeper.profiles_with_jobs() -> list[Path]` returns sorted `wb-<valid-uid>` dirs that have a `cron/jobs.json` file.

- [ ] **Step 1: Write the failing test**

Create `tests/test_profile_cron.py`:

```python
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


def test_profiles_with_jobs_filters(tmp_path):
    a = _make_profile_with_jobs(tmp_path, "wb-user-aaaa")
    (tmp_path / "wb-user-bbbb").mkdir()                # no cron/jobs.json -> skip
    _make_profile_with_jobs(tmp_path, "notwb-cccc")    # wrong prefix -> skip

    sweeper = CronSweeper(tmp_path)
    assert sweeper.profiles_with_jobs() == [a]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_cron.py::test_profiles_with_jobs_filters -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tui_gateway.profile_cron'`.

- [ ] **Step 3: Create the module with discovery only**

Create `tui_gateway/profile_cron.py`:

```python
"""Per-user background cron sweep for the Wheelbase profile router.

Per-user children are dashboard processes that do not run the cron ticker, so a
wb-<uid> profile's cron/jobs.json never fires on its own. This module drives
``hermes cron tick`` per profile on a schedule, independent of whether the
user's child is running. ``cron tick`` itself evaluates which jobs are due and
holds a per-home file lock, so running it redundantly (router sweep + host cron)
is safe.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT = 8


def _default_run_tick(profile_dir: Path) -> subprocess.Popen:
    env = {**os.environ, "HERMES_HOME": str(profile_dir)}
    return subprocess.Popen(
        [sys.executable, "-m", "hermes_cli.main", "cron", "tick"],
        env=env,
        stdin=subprocess.DEVNULL,
    )


class CronSweeper:
    def __init__(
        self,
        profiles_root: Path,
        *,
        run_tick: Callable[[Path], subprocess.Popen] = _default_run_tick,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self.profiles_root = profiles_root
        self._run_tick = run_tick
        self._max_concurrent = max(1, max_concurrent)

    def profiles_with_jobs(self) -> list[Path]:
        from tui_gateway.profile_router import PROFILE_PREFIX
        from tui_gateway.wheelbase_identity import is_valid_user_id

        if not self.profiles_root.is_dir():
            return []
        result: list[Path] = []
        for entry in sorted(self.profiles_root.iterdir()):
            if not entry.is_dir() or not entry.name.startswith(PROFILE_PREFIX):
                continue
            user_id = entry.name[len(PROFILE_PREFIX):]
            if not is_valid_user_id(user_id):
                continue
            if (entry / "cron" / "jobs.json").exists():
                result.append(entry)
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_profile_cron.py::test_profiles_with_jobs_filters -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tui_gateway/profile_cron.py tests/test_profile_cron.py
git commit  # "feat(cron): add CronSweeper profile discovery" + standard trailers
```

---

## Task B2: `sweep_once` + `_default_run_tick`

**Files:**
- Modify: `tui_gateway/profile_cron.py`
- Test: `tests/test_profile_cron.py`

**Interfaces:**
- Consumes: `CronSweeper.profiles_with_jobs`, `_default_run_tick` (Task B1).
- Produces: `CronSweeper.sweep_once() -> int` — launches `run_tick` for each profile-with-jobs, in batches of `max_concurrent`, waiting for each batch before the next; returns the number launched.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_profile_cron.py`:

```python
class FakeProc:
    def __init__(self, events, idx):
        self._events = events
        self._idx = idx

    def wait(self):
        self._events.append(("wait", self._idx))
        return 0


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_profile_cron.py -k "sweep_once or default_run_tick" -v`
Expected: FAIL — `AttributeError: 'CronSweeper' object has no attribute 'sweep_once'`.

- [ ] **Step 3: Implement `sweep_once`**

Add to `CronSweeper` in `tui_gateway/profile_cron.py`:

```python
    def sweep_once(self) -> int:
        launched = 0
        profiles = self.profiles_with_jobs()
        for i in range(0, len(profiles), self._max_concurrent):
            batch = profiles[i:i + self._max_concurrent]
            procs = []
            for profile_dir in batch:
                try:
                    procs.append(self._run_tick(profile_dir))
                    launched += 1
                except Exception:
                    logger.exception("cron tick launch failed for %s", profile_dir)
            for proc in procs:
                try:
                    proc.wait()
                except Exception:
                    logger.exception("cron tick wait failed")
        return launched
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_profile_cron.py -k "sweep_once or default_run_tick" -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tui_gateway/profile_cron.py tests/test_profile_cron.py
git commit  # "feat(cron): CronSweeper.sweep_once with concurrency cap" + standard trailers
```

---

## Task B3: `sweep_forever` + host-cron entrypoint

**Files:**
- Modify: `tui_gateway/profile_cron.py`
- Test: `tests/test_profile_cron.py`

**Interfaces:**
- Consumes: `CronSweeper.sweep_once` (Task B2).
- Produces: `CronSweeper.sweep_forever(interval: float = 60.0, sleep=time.sleep) -> None` (loop, swallow per-pass errors); `main_once() -> int` host/k8s-cron entrypoint that sweeps the live `profiles_root()` once; `python -m tui_gateway.profile_cron` runs `main_once()`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_profile_cron.py`:

```python
def test_main_once_empty(tmp_path, monkeypatch):
    import tui_gateway.profile_cron as pc

    monkeypatch.setattr("tui_gateway.profile_router.profiles_root", lambda: tmp_path)
    assert pc.main_once() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_cron.py::test_main_once_empty -v`
Expected: FAIL — `AttributeError: module 'tui_gateway.profile_cron' has no attribute 'main_once'`.

- [ ] **Step 3: Implement `sweep_forever`, `main_once`, and `__main__`**

Append to `tui_gateway/profile_cron.py` (the `sweep_forever` method goes inside `CronSweeper`, after `sweep_once`):

```python
    def sweep_forever(
        self,
        interval: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        while True:
            try:
                self.sweep_once()
            except Exception:
                logger.exception("profile cron sweep pass failed")
            sleep(interval)
```

And at module scope (end of file):

```python
def main_once() -> int:
    """One sweep over the live profiles root — entrypoint for host/k8s cron."""
    from tui_gateway.profile_router import profiles_root

    logging.basicConfig(level=logging.INFO)
    return CronSweeper(profiles_root()).sweep_once()


if __name__ == "__main__":
    main_once()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_profile_cron.py::test_main_once_empty -v`
Expected: PASS (empty root → 0 launches).

- [ ] **Step 5: Full module run**

Run: `pytest tests/test_profile_cron.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tui_gateway/profile_cron.py tests/test_profile_cron.py
git commit  # "feat(cron): sweep_forever loop + main_once host-cron entrypoint" + standard trailers
```

---

## Task B4: Wire the sweep thread into the router

**Files:**
- Modify: `tui_gateway/profile_router.py` (`main()` ~`:540-544`)
- Test: `tests/test_profile_router.py`

**Interfaces:**
- Consumes: `CronSweeper` (Task B3).
- Produces: `tui_gateway.profile_router.main()` starts a daemon thread named `profile-router-cron-sweep` running `CronSweeper(profiles_root()).sweep_forever`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_profile_router.py`:

```python
def test_main_starts_cron_sweep_thread(tmp_path, monkeypatch):
    import threading
    import tui_gateway.profile_router as pr

    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "tok")
    monkeypatch.setattr(pr, "profiles_root", lambda: tmp_path)

    captured = {}

    def fake_serve(app, host, port):
        captured["thread_names"] = {t.name for t in threading.enumerate()}

    pr.main(serve=fake_serve)

    assert "profile-router-cron-sweep" in captured["thread_names"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_router.py::test_main_starts_cron_sweep_thread -v`
Expected: FAIL — no thread named `profile-router-cron-sweep`.

- [ ] **Step 3: Start the thread in `main()`**

In `tui_gateway/profile_router.py`, in `main()`, immediately after the existing
`profile-router-supervisor` thread `.start()` block (~`:544`) and before the
`port = int(...)` line, add:

```python
    from tui_gateway.profile_cron import CronSweeper

    threading.Thread(
        target=CronSweeper(profiles_root()).sweep_forever,
        name="profile-router-cron-sweep",
        daemon=True,
    ).start()
```

(The import is local to `main()` to avoid a circular import: `profile_cron`
imports `PROFILE_PREFIX`/`profiles_root` from `profile_router`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_profile_router.py::test_main_starts_cron_sweep_thread -v`
Expected: PASS.

- [ ] **Step 5: Full suite for both modules**

Run: `pytest tests/test_profile_router.py tests/test_profile_cron.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tui_gateway/profile_router.py tests/test_profile_router.py
git commit  # "feat(router): run per-user cron sweep thread in main()" + standard trailers
```

---

## Task B5: Deploy wiring — host-cron fallback + unattended timeout

**Files:**
- Modify: `docs/design/2026-06-18-multi-user-isolation-and-cron.md` (add a "Deploy" subsection capturing the exact entries) — no application code.

**Interfaces:** none (ops/deploy artifacts).

- [ ] **Step 1: Verify the host-cron entrypoint runs**

Run (safe on an empty/non-prod root):
```bash
HERMES_HOME=/tmp/hermes-empty python -m tui_gateway.profile_cron
```
Expected: exits 0, logs a sweep with 0 launches (no `wb-*` profiles under that root).

- [ ] **Step 2: Document the host-cron fallback entry**

Add to the spec's deploy notes the crontab line that runs the same sweep every
minute as a restart-safety fallback (per-home `cron/.tick.lock` makes the
double-run with the router thread safe):

```cron
* * * * * cd /app && HERMES_HOME=/data/hermes /usr/bin/env python -m tui_gateway.profile_cron >> /var/log/hermes-cron-sweep.log 2>&1
```

- [ ] **Step 3: Document the unattended-run timeout**

Record that the tenant container sets a bounded `HERMES_CRON_TIMEOUT` (e.g.
`HERMES_CRON_TIMEOUT=600`) so a scheduled job whose Daytona sandbox is
stuck/archived fails closed instead of hanging (spec §5.4). This is an env var
on the container, not code.

- [ ] **Step 4: Commit**

```bash
git add docs/design/2026-06-18-multi-user-isolation-and-cron.md
git commit  # "docs(deploy): host-cron fallback + HERMES_CRON_TIMEOUT for per-user cron" + standard trailers
```

---

## Final Verification

- [ ] **Run the full affected suite**

Run: `pytest tests/test_profile_router.py tests/test_profile_cron.py -v`
Expected: all PASS.

- [ ] **Sanity import**

Run: `python -c "import tui_gateway.profile_cron, tui_gateway.profile_router"`
Expected: no error (no circular import).

---

## Self-Review (completed)

- **Spec coverage:** §4.1 → A1/A2; §4.4 invariants → enforced by config (A1/A2) + Global Constraints + B5 (`config.yaml 0444`, pinned backend documented in spec, not re-implemented here); §5.2/§5.5 → B1–B4; §5.4 timeout + delivery fallback → B5. §4.2/§4.3 are explicitly out of scope (decisions §2a) — no tasks, correct.
- **Placeholder scan:** none — every code/test step shows full content.
- **Type consistency:** `PROFILE_DISABLED_TOOLSETS` (tuple) used identically in A1/A2; `CronSweeper(profiles_root, *, run_tick, max_concurrent)`, `profiles_with_jobs()->list[Path]`, `sweep_once()->int`, `sweep_forever(interval, sleep)`, `_default_run_tick(profile_dir)->Popen`, `main_once()->int` consistent across B1–B4 and tests.
