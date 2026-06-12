"""Resolve the agent's active workspace directory.

The Wheelbase desktop writes per-conversation marker files
(`.wheelbase-<mode>-active`) and context files (`DEALERSHIP.md`, `TEAM.md`) into
the agent's working directory. Hermes exports that directory as `TERMINAL_CWD`
(the same variable its terminal/file tools resolve relative paths against); fall
back to the process cwd when it is unset.
"""

from __future__ import annotations

import os
from pathlib import Path


def workspace_dir() -> Path:
    return Path(os.environ.get("TERMINAL_CWD") or os.getcwd())
