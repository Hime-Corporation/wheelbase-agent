import json

import httpx
import pytest

from wheelbase_sdk.client import WheelbaseClient
from wheelbase_sdk.errors import WheelbaseAuthError, WheelbaseForbiddenError


def _env(monkeypatch, tmp_path, token="tok"):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_DESKTOP", "1")
    monkeypatch.setenv("SUPABASE_URL", "https://sb.example")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("WHEELBASE_GO_API_ORIGIN", "https://api.example")
    if token:
        (tmp_path / "wheelbase-session.json").write_text(
            json.dumps({"access_token": token, "expires_at": 9999999999})
        )
        (tmp_path / "wheelbase-session.json").chmod(0o600)


def test_raises_when_signed_out(tmp_path, monkeypatch, caplog):
    _env(monkeypatch, tmp_path, token=None)
    with pytest.raises(WheelbaseAuthError):
        WheelbaseClient()
    signal = next(
        record.message for record in caplog.records
        if "wheelbase_auth_lifecycle" in record.message
    )
    assert '"reason":"not_signed_in"' in signal
    assert '"source":"local"' in signal


def test_postgrest_get_sends_auth_headers(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    captured = {}

    def handler(req):
        captured["url"] = str(req.url)
        captured["headers"] = dict(req.headers)
        return httpx.Response(200, json=[{"id": "c1"}])

    c = WheelbaseClient(transport=httpx.MockTransport(handler))
    rows = c.postgrest_get("inventory_car", {"id": "eq.c1"})
    assert rows == [{"id": "c1"}]
    assert captured["url"].startswith("https://sb.example/rest/v1/inventory_car")
    assert captured["headers"]["authorization"] == "Bearer tok"
    assert captured["headers"]["apikey"] == "anon"


def test_postgrest_write_sends_prefer_and_body(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    captured = {}

    def handler(req):
        captured["method"] = req.method
        captured["prefer"] = req.headers.get("prefer")
        captured["body"] = json.loads(req.content) if req.content else None
        return httpx.Response(200, json=[{"id": "w1"}])

    c = WheelbaseClient(transport=httpx.MockTransport(handler))
    out = c.postgrest_write("POST", "work_order", body={"car_id": "c1"})
    assert out == [{"id": "w1"}]
    assert captured["method"] == "POST"
    assert captured["prefer"] == "return=representation"
    assert captured["body"] == {"car_id": "c1"}


def test_go_api_uses_origin_and_bearer(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    captured = {}

    def handler(req):
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    c = WheelbaseClient(transport=httpx.MockTransport(handler))
    assert c.go_api("GET", "/v1/ai/imx/demand-set") == {"ok": True}
    assert captured["url"] == "https://api.example/v1/ai/imx/demand-set"
    assert captured["auth"] == "Bearer tok"


@pytest.mark.parametrize("surface", ["postgrest", "go"])
def test_401_is_typed_and_403_is_preserved(surface, tmp_path, monkeypatch, caplog):
    _env(monkeypatch, tmp_path)
    statuses = iter((401, 403))
    calls = 0

    def respond(req):
        nonlocal calls
        calls += 1
        return httpx.Response(next(statuses), request=req)

    c = WheelbaseClient(transport=httpx.MockTransport(respond))
    call = (lambda: c.postgrest_get("cars", {})) if surface == "postgrest" else (lambda: c.go_api("GET", "/cars"))
    with pytest.raises(WheelbaseAuthError) as unauthorized:
        call()
    assert unauthorized.value.reason == "not_signed_in"
    with pytest.raises(WheelbaseForbiddenError) as forbidden:
        call()
    assert not isinstance(forbidden.value, WheelbaseAuthError)
    assert calls == 2
    signals = [
        record.message for record in caplog.records
        if "wheelbase_auth_lifecycle" in record.message
    ]
    assert any('"reason":"not_signed_in"' in signal and '"status":401' in signal for signal in signals)
    assert any('"reason":"forbidden"' in signal and '"status":403' in signal for signal in signals)
    assert all("tok" not in signal for signal in signals)


@pytest.mark.parametrize("surface", ["postgrest", "go"])
def test_403_never_retries_after_newer_revision(
    surface, tmp_path, monkeypatch
):
    from wheelbase_sdk import runtime

    credential = tmp_path / "jti.json"
    credential.write_text(json.dumps({
        "access_token": "old",
        "expires_at": 9999999999,
        "revision": 1,
        "source": "agent_session",
    }))
    credential.chmod(0o600)
    runtime.set_task_identity("task", {"credential_path": str(credential)})
    monkeypatch.setenv("SUPABASE_URL", "https://sb.example")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("WHEELBASE_GO_API_ORIGIN", "https://api.example")
    calls = 0

    def respond(req):
        nonlocal calls
        calls += 1
        credential.write_text(json.dumps({
            "access_token": "new",
            "expires_at": 9999999999,
            "revision": 2,
            "source": "agent_session",
        }))
        credential.chmod(0o600)
        return httpx.Response(403, request=req)

    client = WheelbaseClient(transport=httpx.MockTransport(respond))
    call = (
        (lambda: client.postgrest_get("cars", {}))
        if surface == "postgrest"
        else (lambda: client.go_api("GET", "/cars"))
    )
    with pytest.raises(WheelbaseForbiddenError):
        call()

    assert calls == 1


def test_safe_get_retries_once_only_for_newer_task_revision(tmp_path, monkeypatch):
    from wheelbase_sdk import runtime
    credential = tmp_path / "jti.json"
    credential.write_text(json.dumps({"access_token": "old", "expires_at": 9999999999, "revision": 1, "source": "agent_session"}))
    credential.chmod(0o600)
    runtime.set_task_identity("task", {"credential_path": str(credential)})
    monkeypatch.setenv("SUPABASE_URL", "https://sb.example")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    seen = []
    def handler(req):
        seen.append(req.headers["authorization"])
        if len(seen) == 1:
            credential.write_text(json.dumps({"access_token": "new", "expires_at": 9999999999, "revision": 2, "source": "agent_session"}))
            credential.chmod(0o600)
            return httpx.Response(401, request=req)
        return httpx.Response(200, json=[], request=req)
    assert WheelbaseClient(transport=httpx.MockTransport(handler)).postgrest_get("cars", {}) == []
    assert seen == ["Bearer old", "Bearer new"]


def test_unsafe_write_never_retries_after_rotation(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    calls = 0
    def handler(req):
        nonlocal calls
        calls += 1
        return httpx.Response(401, request=req)
    with pytest.raises(WheelbaseAuthError):
        WheelbaseClient(transport=httpx.MockTransport(handler)).postgrest_write("POST", "cars", body={})
    assert calls == 1
