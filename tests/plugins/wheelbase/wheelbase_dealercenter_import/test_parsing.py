"""Tests for wheelbase_dealercenter_import.parsing."""

import csv
import json

import pytest

import wheelbase_dealercenter_import.parsing as parsing_mod
from wheelbase_dealercenter_import.parsing import parse_export, normalize_rows, DC_COLUMN_MAP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(path, rows: list[dict]):
    """Write a list of dicts as a CSV file."""
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# parse_export — CSV
# ---------------------------------------------------------------------------

class TestParseExportCsv:
    def test_reads_csv_rows(self, tmp_path):
        p = tmp_path / "export.csv"
        _write_csv(p, [
            {"VIN": "1HGCM82633A123456", "Stock #": "A101", "Year": "2020"},
        ])
        result = parse_export(str(p))
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["VIN"] == "1HGCM82633A123456"

    def test_file_not_found(self, tmp_path):
        result = parse_export(str(tmp_path / "missing.csv"))
        assert isinstance(result, str)
        assert "not found" in result.lower()

    def test_unsupported_extension(self, tmp_path):
        p = tmp_path / "export.pdf"
        p.write_text("irrelevant")
        result = parse_export(str(p))
        assert isinstance(result, str)
        assert "Unsupported" in result

    def test_empty_csv(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("", encoding="utf-8")
        result = parse_export(str(p))
        assert isinstance(result, list)
        assert result == []


# ---------------------------------------------------------------------------
# normalize_rows — full pipeline
# ---------------------------------------------------------------------------

class TestNormalizeRows:
    def _sample_row(self, **overrides):
        defaults = {
            "VIN": "1HGCM82633A123456",
            "Stock #": "A101",
            "Year": "2019",
            "Make": "Honda",
            "Model": "Civic",
            "Trim": "EX",
            "Mileage": "45000",
            "Purchase Price": "$12,500.00",
            "Sale Price": "$15,750.50",
            "Freight": "$350.00",
            "Recon Cost": "$1,500.00",
            "Sold Date": "03/15/2022",
            "Acquired Date": "01/10/2022",
            "Source URL": "https://example.com/car",
        }
        defaults.update(overrides)
        return defaults

    def test_money_to_cents(self, tmp_path):
        p = tmp_path / "export.csv"
        _write_csv(p, [self._sample_row()])
        raw = parse_export(str(p))
        rows, unmapped = normalize_rows(raw)
        assert rows[0]["purchase_price_cents"] == 1_250_000
        assert rows[0]["sale_price_cents"] == 1_575_050
        assert rows[0]["freight_cents"] == 35_000
        assert rows[0]["recon_actual_cents"] == 150_000

    def test_date_to_iso(self, tmp_path):
        p = tmp_path / "export.csv"
        _write_csv(p, [self._sample_row()])
        raw = parse_export(str(p))
        rows, _ = normalize_rows(raw)
        assert rows[0]["sold_at"] == "2022-03-15"
        assert rows[0]["acquired_at"] == "2022-01-10"

    def test_disposition_sold_when_sold_date_present(self, tmp_path):
        p = tmp_path / "export.csv"
        _write_csv(p, [self._sample_row()])
        raw = parse_export(str(p))
        rows, _ = normalize_rows(raw)
        assert rows[0]["disposition"] == "sold"

    def test_disposition_active_when_no_sold_date(self, tmp_path):
        p = tmp_path / "export.csv"
        row = self._sample_row()
        row["Sold Date"] = ""
        _write_csv(p, [row])
        raw = parse_export(str(p))
        rows, _ = normalize_rows(raw)
        assert rows[0]["disposition"] == "active"

    def test_unmapped_headers_collected(self, tmp_path):
        p = tmp_path / "export.csv"
        row = self._sample_row()
        row["InternalNotes"] = "some note"
        row["DealerTag"] = "X9"
        _write_csv(p, [row])
        raw = parse_export(str(p))
        _, unmapped = normalize_rows(raw)
        assert "InternalNotes" in unmapped
        assert "DealerTag" in unmapped

    def test_standard_headers_not_in_unmapped(self, tmp_path):
        p = tmp_path / "export.csv"
        _write_csv(p, [self._sample_row()])
        raw = parse_export(str(p))
        _, unmapped = normalize_rows(raw)
        # None of the mapped column names should appear in unmapped.
        mapped_originals = {"VIN", "Stock #", "Year", "Make", "Model", "Trim",
                            "Mileage", "Purchase Price", "Sale Price",
                            "Freight", "Recon Cost", "Sold Date",
                            "Acquired Date", "Source URL"}
        for h in mapped_originals:
            assert h not in unmapped, f"Mapped header '{h}' unexpectedly in unmapped list"

    def test_zero_money_value(self, tmp_path):
        p = tmp_path / "export.csv"
        _write_csv(p, [self._sample_row(**{"Purchase Price": "$0.00"})])
        raw = parse_export(str(p))
        rows, _ = normalize_rows(raw)
        assert rows[0]["purchase_price_cents"] == 0

    def test_empty_rows_returns_empty(self):
        rows, unmapped = normalize_rows([])
        assert rows == []
        assert unmapped == []

    def test_vin_and_stock_mapped(self, tmp_path):
        p = tmp_path / "export.csv"
        _write_csv(p, [self._sample_row()])
        raw = parse_export(str(p))
        rows, _ = normalize_rows(raw)
        assert rows[0]["vin"] == "1HGCM82633A123456"
        assert rows[0]["stock_number"] == "A101"

    def test_alternate_money_column_names(self, tmp_path):
        """'Cost' and 'Selling Price' are alternate names in DC_COLUMN_MAP."""
        p = tmp_path / "export.csv"
        row = {
            "VIN": "2T1BURHE0JC012345",
            "Cost": "$8,000.00",
            "Selling Price": "$10,500.00",
            "Sold Date": "06/01/2023",
        }
        _write_csv(p, [row])
        raw = parse_export(str(p))
        rows, _ = normalize_rows(raw)
        assert rows[0]["purchase_price_cents"] == 800_000
        assert rows[0]["sale_price_cents"] == 1_050_000
