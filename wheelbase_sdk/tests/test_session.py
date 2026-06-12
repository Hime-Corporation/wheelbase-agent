import json

from wheelbase_sdk.session import load_session


def test_load_session_reads_token(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "wheelbase-session.json").write_text(
        json.dumps({"access_token": "tok-1", "expires_at": 1893456000})
    )
    s = load_session()
    assert s is not None
    assert s.access_token == "tok-1"
    assert s.expires_at == 1893456000


def test_load_session_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert load_session() is None


def test_load_session_empty_token_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "wheelbase-session.json").write_text(json.dumps({"access_token": ""}))
    assert load_session() is None


def test_load_session_malformed_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "wheelbase-session.json").write_text("{not json")
    assert load_session() is None
