from __future__ import annotations

import importlib
import json

import pytest

plug = importlib.import_module("plugins.wheelbase-desktop-exec")


def test_write_to_sensitive_path_blocked(monkeypatch):
    monkeypatch.setattr(
        "tools.file_tools._check_sensitive_path",
        lambda path, task_id="default": "Refusing to write to sensitive system path"
        if path.startswith("/etc") else None,
    )
    out = plug._safety_block("write_file", {"path": "/etc/passwd", "content": "x"}, "t", "s")
    assert out is not None
    assert "sensitive" in json.loads(out)["error"].lower()


def test_write_to_ok_path_returns_none(monkeypatch):
    monkeypatch.setattr(
        "tools.file_tools._check_sensitive_path", lambda path, task_id="default": None
    )
    assert plug._safety_block("write_file", {"path": "/work/a.txt", "content": "x"}, "t", "s") is None


def test_patch_uses_same_write_denylist(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "tools.file_tools._check_sensitive_path",
        lambda path, task_id="default": seen.setdefault("path", path) and None,
    )
    plug._safety_block("patch", {"path": "/work/a.txt"}, "t", "s")
    assert seen["path"] == "/work/a.txt"


def test_read_blocked_path(monkeypatch):
    monkeypatch.setattr(
        "agent.file_safety.get_read_block_error",
        lambda path: "blocked: credential store" if path.endswith(".env") else None,
    )
    out = plug._safety_block("read_file", {"path": "/work/.env"}, "t", "s")
    assert out is not None
    assert "blocked" in json.loads(out)["error"].lower()


def test_read_ok_path_returns_none(monkeypatch):
    monkeypatch.setattr("agent.file_safety.get_read_block_error", lambda path: None)
    assert plug._safety_block("read_file", {"path": "/work/a.txt"}, "t", "s") is None


def test_real_file_denylist_end_to_end():
    """No monkeypatch: exercise the plugin's REAL file-safety wiring.

    A write to a sensitive system path and a read of a secret-bearing .env
    file must both be blocked through the plugin's real _check_sensitive_path /
    get_read_block_error calls (matches the rigor of the un-mocked command
    hardline test)."""
    # Real write to a denylisted system path is blocked.
    wout = plug._safety_block("write_file", {"path": "/etc/passwd", "content": "x"}, "t", "s")
    assert wout is not None
    wparsed = json.loads(wout)
    assert wparsed["success"] is False
    assert "sensitive" in wparsed["error"].lower()

    # Real read of a secret-bearing .env file is blocked (.env basename match).
    rout = plug._safety_block("read_file", {"path": "/tmp/some-project/.env"}, "t", "s")
    assert rout is not None
    rparsed = json.loads(rout)
    assert rparsed["success"] is False
    assert "denied" in rparsed["error"].lower()
