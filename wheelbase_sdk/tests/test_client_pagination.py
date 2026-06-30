"""Tests for WheelbaseClient.postgrest_get_page — Content-Range pagination."""

import json

import httpx
import pytest

from wheelbase_sdk.client import WheelbaseClient


def _env(monkeypatch, tmp_path, token="tok"):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("SUPABASE_URL", "https://sb.example")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("WHEELBASE_GO_API_ORIGIN", "https://api.example")
    (tmp_path / "wheelbase-session.json").write_text(
        json.dumps({"access_token": token})
    )


def _make_client(monkeypatch, tmp_path, content_range, rows=None):
    """Build a WheelbaseClient whose HTTP layer returns the given Content-Range."""
    _env(monkeypatch, tmp_path)
    rows = rows or []

    def handler(req):
        return httpx.Response(
            200,
            json=rows,
            headers={"Content-Range": content_range},
        )

    return WheelbaseClient(transport=httpx.MockTransport(handler))


def test_next_offset_computed_from_content_range(tmp_path, monkeypatch):
    """Content-Range 0-49/200 → next_offset = 50."""
    c = _make_client(monkeypatch, tmp_path, "0-49/200")
    rows, next_offset = c.postgrest_get_page("inventory_car", {}, limit=50, offset=0)
    assert next_offset == 50


def test_next_offset_none_when_exhausted(tmp_path, monkeypatch):
    """Content-Range 150-199/200 → offset+limit(200)=total → next_offset = None."""
    c = _make_client(monkeypatch, tmp_path, "150-199/200")
    rows, next_offset = c.postgrest_get_page("inventory_car", {}, limit=50, offset=150)
    assert next_offset is None


def test_rows_returned_correctly(tmp_path, monkeypatch):
    """Rows from the response body are returned alongside next_offset."""
    data = [{"id": "c1"}, {"id": "c2"}]
    c = _make_client(monkeypatch, tmp_path, "0-1/100", rows=data)
    rows, next_offset = c.postgrest_get_page("inventory_car", {}, limit=2, offset=0)
    assert rows == data
    assert next_offset == 2


def test_prefer_count_exact_sent(tmp_path, monkeypatch):
    """Prefer: count=exact header must be included in the request."""
    _env(monkeypatch, tmp_path)
    captured = {}

    def handler(req):
        captured["prefer"] = req.headers.get("prefer")
        return httpx.Response(200, json=[], headers={"Content-Range": "0-0/0"})

    c = WheelbaseClient(transport=httpx.MockTransport(handler))
    c.postgrest_get_page("inventory_car", {}, limit=50)
    assert captured.get("prefer") == "count=exact"


def test_limit_and_offset_passed_as_query_params(tmp_path, monkeypatch):
    """limit and offset must appear in the query string."""
    _env(monkeypatch, tmp_path)
    captured = {}

    def handler(req):
        captured["params"] = dict(req.url.params)
        return httpx.Response(200, json=[], headers={"Content-Range": "50-99/200"})

    c = WheelbaseClient(transport=httpx.MockTransport(handler))
    c.postgrest_get_page("inventory_car", {"is_archived": "eq.false"}, limit=50, offset=50)
    assert captured["params"]["limit"] == "50"
    assert captured["params"]["offset"] == "50"


def test_no_content_range_header_returns_none_next_offset(tmp_path, monkeypatch):
    """When the server does not return Content-Range, next_offset is None."""
    _env(monkeypatch, tmp_path)

    def handler(req):
        return httpx.Response(200, json=[{"id": "x"}])

    c = WheelbaseClient(transport=httpx.MockTransport(handler))
    rows, next_offset = c.postgrest_get_page("inventory_car", {}, limit=50)
    assert next_offset is None
    assert rows == [{"id": "x"}]


def test_wildcard_total_returns_none_next_offset(tmp_path, monkeypatch):
    """Content-Range with '*' total (count not requested) → next_offset None."""
    c = _make_client(monkeypatch, tmp_path, "0-49/*")
    _, next_offset = c.postgrest_get_page("inventory_car", {}, limit=50)
    assert next_offset is None


def test_short_page_next_offset_derived_from_last_index(tmp_path, monkeypatch):
    """Short page: requested limit=50 but server returned only rows 0-19 of 200.

    next_offset must be 20 (last+1), not 50 (offset+limit), so no rows are skipped.
    """
    c = _make_client(monkeypatch, tmp_path, "0-19/200")
    _, next_offset = c.postgrest_get_page("inventory_car", {}, limit=50, offset=0)
    assert next_offset == 20
