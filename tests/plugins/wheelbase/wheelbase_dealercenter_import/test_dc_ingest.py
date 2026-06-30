"""Tests for wheelbase_dealercenter_import.tools.dc_ingest."""

import csv
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import wheelbase_dealercenter_import.tools.dc_ingest as ingest_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(result: str) -> dict:
    data = json.loads(result)
    assert "error" not in data, f"Unexpected error: {data}"
    return data


def _err(result: str) -> dict:
    data = json.loads(result)
    assert "error" in data, f"Expected error, got: {data}"
    return data


def _write_csv(path: Path, rows: list[dict]):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _sample_csv(tmp_path: Path, count: int = 5) -> Path:
    p = tmp_path / "export.csv"
    rows = [
        {
            "VIN": f"1HGCM82633A{i:06d}",
            "Stock #": f"S{i:03d}",
            "Year": "2020",
            "Make": "Honda",
            "Model": "Civic",
            "Purchase Price": "$10,000.00",
            "Sale Price": "$13,500.00",
            "Sold Date": "01/15/2023",
        }
        for i in range(count)
    ]
    _write_csv(p, rows)
    return p


# ---------------------------------------------------------------------------
# dry_run=True
# ---------------------------------------------------------------------------

class TestDcIngestDryRun:
    def test_returns_preview_without_calling_api(self, tmp_path):
        p = _sample_csv(tmp_path, count=15)
        mock_client_cls = MagicMock()
        with patch.object(ingest_mod, "WheelbaseClient", mock_client_cls):
            result = _ok(ingest_mod.dc_ingest({"path": str(p), "dryRun": True}))
        # WheelbaseClient should NOT have been instantiated during a dry run.
        mock_client_cls.assert_not_called()
        assert result["dryRun"] is True
        assert result["counts"]["rows"] == 15
        assert len(result["preview"]) == 10  # capped at 10

    def test_dry_run_default_is_true(self, tmp_path):
        p = _sample_csv(tmp_path, count=3)
        mock_client_cls = MagicMock()
        with patch.object(ingest_mod, "WheelbaseClient", mock_client_cls):
            result = _ok(ingest_mod.dc_ingest({"path": str(p)}))
        mock_client_cls.assert_not_called()
        assert result["dryRun"] is True

    def test_unmapped_headers_present(self, tmp_path):
        p = tmp_path / "export.csv"
        _write_csv(p, [
            {"VIN": "1HGCM82633A000001", "UnknownCol": "value", "Sold Date": "01/01/2023"},
        ])
        result = _ok(ingest_mod.dc_ingest({"path": str(p), "dryRun": True}))
        assert "unmappedHeaders" in result
        assert "UnknownCol" in result["unmappedHeaders"]

    def test_missing_path_returns_error(self):
        result = _err(ingest_mod.dc_ingest({}))
        assert "path" in result["error"]

    def test_empty_path_returns_error(self):
        result = _err(ingest_mod.dc_ingest({"path": ""}))
        assert "path" in result["error"]

    def test_nonexistent_file_returns_error(self):
        # _err already asserts "error" is present in the parsed dict.
        _err(ingest_mod.dc_ingest({"path": "/tmp/nonexistent_dc_file.csv", "dryRun": True}))


# ---------------------------------------------------------------------------
# dry_run=False — batching
# ---------------------------------------------------------------------------

class TestDcIngestCommit:
    def test_calls_go_api_with_rows(self, tmp_path):
        p = _sample_csv(tmp_path, count=5)
        mock_client = MagicMock()
        mock_client.go_api.return_value = {"created": 5, "updated": 0, "skipped": 0, "errors": []}
        mock_client_cls = MagicMock(return_value=mock_client)

        with patch.object(ingest_mod, "WheelbaseClient", mock_client_cls):
            result = _ok(ingest_mod.dc_ingest({"path": str(p), "dryRun": False}))

        mock_client.go_api.assert_called_once()
        call_args = mock_client.go_api.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == "/v1/inventory/import/historic"
        body = call_args[1]["body"]
        assert "rows" in body
        assert len(body["rows"]) == 5

        assert result["dryRun"] is False
        assert result["created"] == 5
        mock_client.close.assert_called_once()

    def test_batches_at_200(self, tmp_path):
        p = _sample_csv(tmp_path, count=450)
        call_row_counts = []

        def fake_go_api(method, path, *, body=None, params=None):
            call_row_counts.append(len(body["rows"]))
            return {"created": len(body["rows"]), "updated": 0, "skipped": 0, "errors": []}

        mock_client = MagicMock()
        mock_client.go_api.side_effect = fake_go_api
        mock_client_cls = MagicMock(return_value=mock_client)

        with patch.object(ingest_mod, "WheelbaseClient", mock_client_cls):
            result = _ok(ingest_mod.dc_ingest({"path": str(p), "dryRun": False}))

        # 450 rows -> batches of 200, 200, 50
        assert len(call_row_counts) == 3
        assert call_row_counts[0] == 200
        assert call_row_counts[1] == 200
        assert call_row_counts[2] == 50
        assert result["created"] == 450

    def test_aggregates_errors_across_batches(self, tmp_path):
        p = _sample_csv(tmp_path, count=250)
        call_n = [0]

        def fake_go_api(method, path, *, body=None, params=None):
            call_n[0] += 1
            return {
                "created": 0,
                "updated": 0,
                "skipped": 0,
                "errors": [{"vin": "X", "msg": f"batch {call_n[0]} error"}],
            }

        mock_client = MagicMock()
        mock_client.go_api.side_effect = fake_go_api
        mock_client_cls = MagicMock(return_value=mock_client)

        with patch.object(ingest_mod, "WheelbaseClient", mock_client_cls):
            result = _ok(ingest_mod.dc_ingest({"path": str(p), "dryRun": False}))

        assert len(result["errors"]) == 2  # one error per batch

    def test_auth_error_returns_signed_out(self, tmp_path):
        from wheelbase_sdk import WheelbaseAuthError
        p = _sample_csv(tmp_path)
        mock_client_cls = MagicMock(side_effect=WheelbaseAuthError("not signed in"))

        with patch.object(ingest_mod, "WheelbaseClient", mock_client_cls):
            raw = ingest_mod.dc_ingest({"path": str(p), "dryRun": False})

        data = json.loads(raw)
        assert "signedOut" in data or "error" in data  # signed_out_result shape

    def test_api_exception_returns_err(self, tmp_path):
        p = _sample_csv(tmp_path)
        mock_client = MagicMock()
        mock_client.go_api.side_effect = RuntimeError("network down")
        mock_client_cls = MagicMock(return_value=mock_client)

        with patch.object(ingest_mod, "WheelbaseClient", mock_client_cls):
            result = _err(ingest_mod.dc_ingest({"path": str(p), "dryRun": False}))

        assert "network down" in result["error"]
        mock_client.close.assert_called_once()
