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
        root: Path,
        *,
        run_tick: Callable[[Path], subprocess.Popen] = _default_run_tick,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        # ``root`` is the router's HERMES_HOME. Profiles are discovered under
        # the tenant-nested layout (tenants/<tid>/profiles/wb-<uid>), the
        # legacy flat layout (profiles/wb-<uid> — kept live because a deferred
        # tenant migration leaves profiles there until the next boot), and
        # ``root`` itself when it directly contains wb-* dirs.
        self.root = root
        self._run_tick = run_tick
        self._max_concurrent = max(1, max_concurrent)

    def _profile_parent_dirs(self) -> list[Path]:
        from tui_gateway.wheelbase_identity import is_valid_user_id

        parents: list[Path] = [self.root]
        legacy = self.root / "profiles"
        if legacy.is_dir():
            parents.append(legacy)
        tenants = self.root / "tenants"
        if tenants.is_dir():
            for tenant_dir in sorted(tenants.iterdir()):
                if not tenant_dir.is_dir() or not is_valid_user_id(tenant_dir.name):
                    continue
                nested = tenant_dir / "profiles"
                if nested.is_dir():
                    parents.append(nested)
        return parents

    def profiles_with_jobs(self) -> list[Path]:
        from tui_gateway.profile_router import PROFILE_PREFIX
        from tui_gateway.wheelbase_identity import is_valid_user_id

        result: list[Path] = []
        for parent in self._profile_parent_dirs():
            if not parent.is_dir():
                continue
            for entry in sorted(parent.iterdir()):
                if not entry.is_dir() or not entry.name.startswith(PROFILE_PREFIX):
                    continue
                user_id = entry.name[len(PROFILE_PREFIX):]
                if not is_valid_user_id(user_id):
                    continue
                if (entry / "cron" / "jobs.json").exists():
                    result.append(entry)
        return result

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


def main_once() -> int:
    """One sweep over the live hermes root — entrypoint for host/k8s cron."""
    from tui_gateway.profile_router import hermes_home_root

    logging.basicConfig(level=logging.INFO)
    return CronSweeper(hermes_home_root()).sweep_once()


if __name__ == "__main__":
    main_once()
