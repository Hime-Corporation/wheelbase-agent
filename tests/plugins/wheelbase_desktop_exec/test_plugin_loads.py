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
