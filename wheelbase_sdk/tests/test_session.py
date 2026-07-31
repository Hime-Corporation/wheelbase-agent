import json

import pytest

from wheelbase_sdk.errors import WheelbaseAuthError
from wheelbase_sdk.session import load_session


def _write_session(path, **overrides):
    payload = {
        "access_token": "tok-1",
        "expires_at": 1_893_456_000,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def _enable_desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_DESKTOP", "1")


def test_load_session_reads_valid_desktop_singleton(tmp_path, monkeypatch):
    _enable_desktop(monkeypatch, tmp_path)
    path = tmp_path / "wheelbase-session.json"
    _write_session(path)

    session = load_session()

    assert session is not None
    assert session.access_token == "tok-1"
    assert session.expires_at == 1_893_456_000
    assert session.revision == 0
    assert session.source == "local"
    assert session.credential_path == path


def test_load_session_ignores_singleton_outside_desktop_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_DESKTOP", raising=False)
    _write_session(tmp_path / "wheelbase-session.json")

    assert load_session() is None


def test_load_session_absent_returns_none_in_desktop_mode(tmp_path, monkeypatch):
    _enable_desktop(monkeypatch, tmp_path)
    assert load_session() is None


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("directory", "refresh_pending"),
        ("symlink", "refresh_pending"),
        ("wrong_mode", "refresh_pending"),
        ("malformed", "refresh_pending"),
        ("non_object", "refresh_pending"),
        ("empty_token", "not_signed_in"),
        ("missing_expiry", "refresh_pending"),
        ("invalid_expiry", "refresh_pending"),
    ],
)
def test_desktop_singleton_validation_fails_closed(kind, reason, tmp_path, monkeypatch):
    _enable_desktop(monkeypatch, tmp_path)
    path = tmp_path / "wheelbase-session.json"
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        target = tmp_path / "target.json"
        _write_session(target)
        path.symlink_to(target)
    elif kind == "wrong_mode":
        _write_session(path)
        path.chmod(0o644)
    elif kind == "malformed":
        path.write_text("{not json", encoding="utf-8")
        path.chmod(0o600)
    elif kind == "non_object":
        path.write_text("[]", encoding="utf-8")
        path.chmod(0o600)
    elif kind == "empty_token":
        _write_session(path, access_token="")
    elif kind == "missing_expiry":
        _write_session(path, expires_at=None)
    elif kind == "invalid_expiry":
        _write_session(path, expires_at=True)
    else:
        raise AssertionError(f"unhandled test kind: {kind}")

    with pytest.raises(WheelbaseAuthError) as raised:
        load_session()

    assert raised.value.reason == reason


def test_desktop_singleton_ignores_untrusted_task_metadata(tmp_path, monkeypatch):
    _enable_desktop(monkeypatch, tmp_path)
    _write_session(
        tmp_path / "wheelbase-session.json",
        revision=99,
        source="agent_session",
    )

    session = load_session()

    assert session is not None
    assert session.revision == 0
    assert session.source == "local"


@pytest.mark.parametrize(
    ("expires_at", "reason"),
    [(999, "expired"), (1_030, "refresh_pending")],
)
def test_desktop_singleton_rejects_expired_and_within_skew(
    expires_at, reason, tmp_path, monkeypatch
):
    import wheelbase_sdk.session as session_module

    _enable_desktop(monkeypatch, tmp_path)
    monkeypatch.setattr(session_module.time, "time", lambda: 1_000)
    _write_session(tmp_path / "wheelbase-session.json", expires_at=expires_at)

    with pytest.raises(WheelbaseAuthError) as raised:
        load_session()

    assert raised.value.reason == reason


def test_expired_desktop_singleton_emits_safe_reason_signal(tmp_path, monkeypatch, caplog):
    import wheelbase_sdk.session as session_module

    _enable_desktop(monkeypatch, tmp_path)
    monkeypatch.setattr(session_module.time, "time", lambda: 1_000)
    path = tmp_path / "wheelbase-session.json"
    secret = "sensitive-access-token"
    _write_session(path, access_token=secret, expires_at=999)

    with pytest.raises(WheelbaseAuthError):
        load_session()

    signal = next(
        record.message for record in caplog.records
        if "wheelbase_auth_lifecycle" in record.message
    )
    assert '"reason":"expired"' in signal
    assert '"source":"local"' in signal
    assert secret not in signal
    assert str(path) not in signal
