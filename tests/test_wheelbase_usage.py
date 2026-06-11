"""Tests for post-turn usage reporting (B6, spec §9)."""
from __future__ import annotations

import json
import time
import types
import urllib.request
from types import SimpleNamespace

import pytest

from tui_gateway.wheelbase_usage import report_session_usage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_identity(user_id="u1", tenant_id="t1", dealership_id="d1"):
    return SimpleNamespace(user_id=user_id, tenant_id=tenant_id, dealership_id=dealership_id)


def _make_db(row=None):
    """Return a minimal fake SessionDB with get_session."""
    db = SimpleNamespace()
    db.get_session = lambda session_key: row
    return db


_SAMPLE_ROW = {
    "id": "sess-abc",
    "model": "claude-opus-4",
    "input_tokens": 100,
    "output_tokens": 200,
    "cache_read_tokens": 10,
    "cache_write_tokens": 5,
    "reasoning_tokens": 0,
    "estimated_cost_usd": 0.0042,
    "message_count": 3,
}


# ---------------------------------------------------------------------------
# Helper to wait for the background thread to finish
# ---------------------------------------------------------------------------

def _wait_for_usage_thread(timeout=2.0):
    """Poll briefly until the wb-usage-report daemon thread has exited."""
    import threading
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = any(t.name == "wb-usage-report" for t in threading.enumerate())
        if not found:
            return
        time.sleep(0.02)
    # Give it one final tiny sleep so the thread can finish even if it was
    # still running right at the deadline
    time.sleep(0.05)


# ---------------------------------------------------------------------------
# Main happy-path test
# ---------------------------------------------------------------------------

def test_report_sends_correct_payload(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        resp = types.SimpleNamespace(read=lambda: b"ok")
        return resp

    monkeypatch.setenv("WHEELBASE_INTERNAL_API", "http://backend:8091")
    monkeypatch.setenv("WHEELBASE_GATEWAY_TOKEN", "secret-tok")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    identity = _make_identity()
    db = _make_db(_SAMPLE_ROW)

    report_session_usage(db, "sess-abc", identity)
    _wait_for_usage_thread()

    assert "req" in captured, "urlopen was not called"
    req: urllib.request.Request = captured["req"]

    # URL
    assert req.full_url == "http://backend:8091/internal/agent/usage"

    # Auth header
    assert req.get_header("X-gateway-token") == "secret-tok"

    # Payload
    payload = json.loads(req.data)
    assert payload["session_id"] == "sess-abc"
    assert payload["user_id"] == "u1"
    assert payload["tenant_id"] == "t1"
    assert payload["dealership_id"] == "d1"
    assert payload["model"] == "claude-opus-4"
    assert payload["input_tokens"] == 100
    assert payload["output_tokens"] == 200
    assert payload["cache_read_tokens"] == 10
    assert payload["cache_write_tokens"] == 5
    assert payload["reasoning_tokens"] == 0
    assert payload["cost_usd"] == pytest.approx(0.0042)
    assert payload["message_count"] == 3


# ---------------------------------------------------------------------------
# No-op cases — urlopen must never be called
# ---------------------------------------------------------------------------

def test_noop_when_api_unset(monkeypatch):
    called = []

    monkeypatch.delenv("WHEELBASE_INTERNAL_API", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: called.append(1))

    report_session_usage(_make_db(_SAMPLE_ROW), "sess-abc", _make_identity())
    _wait_for_usage_thread()

    assert not called, "urlopen should not be called when WHEELBASE_INTERNAL_API is unset"


def test_noop_when_identity_none(monkeypatch):
    called = []

    monkeypatch.setenv("WHEELBASE_INTERNAL_API", "http://backend:8091")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: called.append(1))

    report_session_usage(_make_db(_SAMPLE_ROW), "sess-abc", None)
    _wait_for_usage_thread()

    assert not called, "urlopen should not be called when identity is None"


def test_noop_when_db_none(monkeypatch):
    called = []

    monkeypatch.setenv("WHEELBASE_INTERNAL_API", "http://backend:8091")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: called.append(1))

    report_session_usage(None, "sess-abc", _make_identity())
    _wait_for_usage_thread()

    assert not called, "urlopen should not be called when db is None"


def test_noop_when_row_missing(monkeypatch):
    called = []

    monkeypatch.setenv("WHEELBASE_INTERNAL_API", "http://backend:8091")
    monkeypatch.setenv("WHEELBASE_GATEWAY_TOKEN", "tok")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: called.append(1))

    report_session_usage(_make_db(None), "sess-missing", _make_identity())
    _wait_for_usage_thread()

    assert not called, "urlopen should not be called when get_session returns None"


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------

def test_urlopen_exception_does_not_raise(monkeypatch):
    """A failing HTTP call must not propagate — fire-and-forget."""

    def boom(req, timeout=None):
        raise OSError("network unreachable")

    monkeypatch.setenv("WHEELBASE_INTERNAL_API", "http://backend:8091")
    monkeypatch.setenv("WHEELBASE_GATEWAY_TOKEN", "tok")
    monkeypatch.setattr(urllib.request, "urlopen", boom)

    # Must not raise
    report_session_usage(_make_db(_SAMPLE_ROW), "sess-abc", _make_identity())
    _wait_for_usage_thread()


# ---------------------------------------------------------------------------
# Nullable tenant_id / dealership_id
# ---------------------------------------------------------------------------

def test_nullable_tenant_dealership(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return types.SimpleNamespace(read=lambda: b"ok")

    monkeypatch.setenv("WHEELBASE_INTERNAL_API", "http://backend:8091")
    monkeypatch.setenv("WHEELBASE_GATEWAY_TOKEN", "tok")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    identity = _make_identity(tenant_id=None, dealership_id=None)
    report_session_usage(_make_db(_SAMPLE_ROW), "sess-abc", identity)
    _wait_for_usage_thread()

    payload = json.loads(captured["req"].data)
    assert payload["tenant_id"] is None
    assert payload["dealership_id"] is None
