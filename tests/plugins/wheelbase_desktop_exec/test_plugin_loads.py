from __future__ import annotations

import importlib


def test_register_wires_tool_execution_middleware():
    plug = importlib.import_module("plugins.wheelbase-desktop-exec")

    registered = {}

    class Ctx:
        def register_middleware(self, kind, cb):
            registered[kind] = cb

    plug.register(Ctx())
    assert "tool_execution" in registered
    assert callable(registered["tool_execution"])


def test_relay_env_imports_after_environment_split():
    """First terminal/process call lazy-imports relay_env. After the Sep 2026
    Hermes split, ``_ThreadedProcessHandle`` lives in
    ``tools.environments.base_output``; importing it from ``base`` becomes
    ``desktop_unavailable: cannot import _ThreadedProcessHandle`` at runtime
    while the plugin itself still loads.
    """
    relay_env = importlib.import_module("plugins.wheelbase-desktop-exec.relay_env")
    from tools.environments.base import BaseEnvironment
    from tools.environments.base_output import _ThreadedProcessHandle

    assert issubclass(relay_env.DesktopRelayEnvironment, BaseEnvironment)
    assert relay_env._ThreadedProcessHandle is _ThreadedProcessHandle
