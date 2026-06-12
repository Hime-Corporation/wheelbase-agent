import os
from pathlib import Path

from wheelbase_sdk.workspace import workspace_dir


def test_workspace_dir_uses_terminal_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    assert workspace_dir() == tmp_path


def test_workspace_dir_falls_back_to_cwd(monkeypatch):
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    assert workspace_dir() == Path(os.getcwd())
