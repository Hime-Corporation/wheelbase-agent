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
    """One sweep over the live profiles root — entrypoint for host/k8s cron."""
    from tui_gateway.profile_router import profiles_root

    logging.basicConfig(level=logging.INFO)
    return CronSweeper(profiles_root()).sweep_once()


if __name__ == "__main__":
    main_once()
