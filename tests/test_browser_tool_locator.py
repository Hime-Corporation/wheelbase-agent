"""Tests for the AGENT_BROWSER_CLI override in _find_agent_browser.

Bundled deployments (the Wheelbase Electron app) point the agent at an absolute
agent-browser binary via the AGENT_BROWSER_CLI env var so discovery never
depends on system PATH/npx.
"""

import os

import tools.browser_tool as bt


def _reset_cache():
    bt._agent_browser_resolved = False
    bt._cached_agent_browser = None


def test_env_override_takes_precedence(tmp_path, monkeypatch):
    fake = tmp_path / "agent-browser"
    fake.write_text("#!/bin/sh\necho ok\n")
    fake.chmod(0o755)
    monkeypatch.setenv("AGENT_BROWSER_CLI", str(fake))
    _reset_cache()
    assert bt._find_agent_browser() == str(fake)


def test_missing_override_file_falls_through(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BROWSER_CLI", str(tmp_path / "does-not-exist"))
    _reset_cache()
    # Should not return the bogus path; either resolves a real CLI or raises.
    try:
        result = bt._find_agent_browser()
    except FileNotFoundError:
        result = None
    assert result != str(tmp_path / "does-not-exist")


def test_non_executable_override_falls_through(tmp_path, monkeypatch):
    plain = tmp_path / "agent-browser"
    plain.write_text("not executable\n")
    plain.chmod(0o644)
    monkeypatch.setenv("AGENT_BROWSER_CLI", str(plain))
    _reset_cache()
    try:
        result = bt._find_agent_browser()
    except FileNotFoundError:
        result = None
    assert result != str(plain)


def test_no_override_does_not_short_circuit(monkeypatch):
    monkeypatch.delenv("AGENT_BROWSER_CLI", raising=False)
    _reset_cache()
    # Without the override the function proceeds to its normal search ladder;
    # we only assert it does not crash importing/branching on the override.
    try:
        bt._find_agent_browser()
    except FileNotFoundError:
        pass
