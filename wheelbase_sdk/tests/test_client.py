import json

import httpx
import pytest

from wheelbase_sdk.client import WheelbaseClient
from wheelbase_sdk.errors import WheelbaseAuthError


def _env(monkeypatch, tmp_path, token="tok"):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://sb.example")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("WHEELBASE_GO_API_ORIGIN", "https://api.example")
    if token:
        (tmp_path / "wheelbase-session.json").write_text(
            json.dumps({"access_token": token})
        )


def test_raises_when_signed_out(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path, token=None)
    with pytest.raises(WheelbaseAuthError):
        WheelbaseClient()


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
