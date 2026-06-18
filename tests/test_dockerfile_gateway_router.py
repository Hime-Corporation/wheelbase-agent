"""Dockerfile.gateway must boot the profile router."""
from __future__ import annotations

from pathlib import Path

import pytest

from tui_gateway import profile_router

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile.gateway"
GATEWAY_ENTRYPOINT = (
    Path(__file__).resolve().parents[1] / "scripts" / "gateway-entrypoint.sh"
)


def test_gateway_cmd_is_profile_router():
    text = DOCKERFILE.read_text()
    cmd_section = text.split("CMD")[-1]
    # The container boots via the entrypoint wrapper (which also supervises the
    # gateway.run API server), whose primary/critical process is the profile
    # router — see scripts/gateway-entrypoint.sh.
    assert 'CMD ["scripts/gateway-entrypoint.sh"]' in text
    assert "python -m tui_gateway.profile_router" in GATEWAY_ENTRYPOINT.read_text()
    # The container root must never be a bare per-profile dashboard.
    assert '"hermes_cli.main", "dashboard"' not in cmd_section
    assert "EXPOSE 9320" in text


def test_main_builds_app_and_reconciles(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "router-secret")
    monkeypatch.setenv("WHEELBASE_PROFILES_ROOT", str(tmp_path))
    (tmp_path / "wb-user-aaaa").mkdir()

    spawned = []
    captured = {}

    class Proc:
        def poll(self):
            return None

    def fake_spawn(user_id, port, env):
        spawned.append((user_id, port, env))
        return Proc()

    def fake_serve(app, host, port):
        captured.update(app=app, host=host, port=port)

    monkeypatch.setattr(profile_router, "_default_spawn", fake_spawn)
    monkeypatch.setattr(profile_router, "_default_wait_ready", lambda port, token: None)

    profile_router.main(serve=fake_serve, seed_skills=lambda p: None)

    assert [item[0] for item in spawned] == ["user-aaaa"]
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9320


def test_main_refuses_to_start_without_token(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)
    monkeypatch.setenv("WHEELBASE_PROFILES_ROOT", str(tmp_path))
    with pytest.raises(SystemExit):
        profile_router.main(serve=lambda *args, **kwargs: None, seed_skills=lambda p: None)
