from __future__ import annotations

import json
import stat

from tui_gateway.fixture_trace import emit_fixture_trace


def test_fixture_trace_is_opt_in_and_token_safe(tmp_path, monkeypatch):
    trace = tmp_path / "trace.jsonl"
    monkeypatch.delenv("WHEELBASE_GATEWAY_FIXTURE_TRACE_FILE", raising=False)
    emit_fixture_trace("probe_start", client="desktop")
    assert not trace.exists()

    monkeypatch.setenv("WHEELBASE_GATEWAY_FIXTURE_TRACE_FILE", str(trace))
    emit_fixture_trace(
        "probe_start",
        client="desktop",
        cdp_capability=True,
        ignored_payload={"access_token": "must-not-be-serialized"},
    )

    assert stat.S_IMODE(trace.stat().st_mode) == 0o600
    record = json.loads(trace.read_text(encoding="utf-8"))
    assert record["event"] == "probe_start"
    assert record["client"] == "desktop"
    assert record["cdp_capability"] is True
    assert "ignored_payload" not in record
    assert "access_token" not in trace.read_text(encoding="utf-8")


def test_fixture_trace_rejects_relative_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WHEELBASE_GATEWAY_FIXTURE_TRACE_FILE", "trace.jsonl")
    emit_fixture_trace("probe_start")
    assert not (tmp_path / "trace.jsonl").exists()
