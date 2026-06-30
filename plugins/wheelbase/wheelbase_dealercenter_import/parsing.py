"""DealerCenter export parsing utilities.

Handles CSV and Excel exports from DealerCenter.  Normalises column names,
converts money strings to integer cents, and infers vehicle disposition.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from datetime import datetime, date

# ---------------------------------------------------------------------------
# Column-name mapping
# ---------------------------------------------------------------------------

DC_COLUMN_MAP: dict[str, str] = {
    # Keys are lowercase-stripped versions for flexible matching.
    "vin": "vin",
    "stock": "stock_number",
    "stock #": "stock_number",
    "stock number": "stock_number",
    "year": "year",
    "make": "make",
    "model": "model",
    "trim": "trim",
    "mileage": "odometer",
    "miles": "odometer",
    "odometer": "odometer",
    "sold date": "sold_at",
    "date sold": "sold_at",
    "acquired date": "acquired_at",
    "purchase date": "acquired_at",
    "date acquired": "acquired_at",
    "purchase price": "purchase_price_cents",
    "cost": "purchase_price_cents",
    "sale price": "sale_price_cents",
    "selling price": "sale_price_cents",
    "freight": "freight_cents",
    "recon cost": "recon_actual_cents",
    "reconditioning": "recon_actual_cents",
    "disposition": "disposition",
    "status": "disposition",
    "source url": "source_url",
    "url": "source_url",
}

# Fields that store money values (will be converted to integer cents).
_CENTS_FIELDS = frozenset({
    "purchase_price_cents",
    "sale_price_cents",
    "freight_cents",
    "recon_actual_cents",
})

# Fields that store dates (will be normalised to RFC3339 UTC timestamp).
_DATE_FIELDS = frozenset({
    "sold_at",
    "acquired_at",
})

# Fields that must be emitted as integers (Go decodes them as int / *int).
_INT_FIELDS = frozenset({
    "year",
    "odometer",
})

# Common date formats found in DealerCenter exports.
_DATE_FORMATS = [
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y-%m-%d",
    "%m-%d-%Y",
    "%Y/%m/%d",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _money_to_cents(value: str) -> int | None:
    """Convert a money string like "$1,500.00" to integer cents (150000)."""
    if not value:
        return None
    cleaned = re.sub(r"[\$,\s]", "", value)
    try:
        return round(float(cleaned) * 100)
    except (ValueError, TypeError):
        return None


def _parse_date(value: str) -> str | None:
    """Return a full RFC3339 UTC timestamp or None if unparseable.

    DealerCenter exports contain date-only values; we append midnight UTC so
    the Go backend can decode them as ``*time.Time`` (which requires RFC3339).
    """
    if not value:
        return None
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat() + "T00:00:00Z"
        except ValueError:
            continue
    return None


def _to_int(value: str) -> int | None:
    """Convert a numeric string (possibly with commas) to int, or None."""
    if not value:
        return None
    cleaned = value.replace(",", "").strip()
    try:
        return int(float(cleaned))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_export(path: str) -> list[dict] | str:
    """Parse a CSV or Excel file.

    Returns a list of raw row dicts (column-name -> string value) on success,
    or an error string on failure.
    """
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}"

    suffix = p.suffix.lower()

    if suffix == ".csv":
        try:
            with p.open(newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
            return rows
        except Exception as exc:  # noqa: BLE001
            return f"Failed to read CSV: {exc}"

    if suffix in {".xlsx", ".xls"}:
        try:
            import openpyxl  # type: ignore[import]
        except ImportError:
            return "openpyxl is required to read Excel files: pip install openpyxl"
        try:
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            ws = wb.active
            rows_iter = iter(ws.iter_rows(values_only=True))
            try:
                headers = [str(c).strip() if c is not None else "" for c in next(rows_iter)]
            except StopIteration:
                return []
            rows = []
            for row in rows_iter:
                row_dict = {}
                for h, v in zip(headers, row):
                    row_dict[h] = str(v) if v is not None else ""
                rows.append(row_dict)
            wb.close()
            return rows
        except Exception as exc:  # noqa: BLE001
            return f"Failed to read Excel file: {exc}"

    return f"Unsupported file type '{suffix}'. Expected .csv, .xlsx, or .xls."


def normalize_rows(raw: list[dict]) -> tuple[list[dict], list[str]]:
    """Normalise raw rows to the backend contract shape.

    Returns ``(rows, unmapped_headers)`` where ``unmapped_headers`` is the
    list of original column names that had no entry in DC_COLUMN_MAP.

    Transformations applied per row:
    - Money strings (``$1,500.00``) -> integer cents.
    - Date strings -> ISO 8601 date string.
    - ``disposition`` inferred as ``"sold"`` when ``sold_at`` is non-null,
      otherwise ``"active"``, unless the raw export already sets it.
    """
    if not raw:
        return [], []

    # Determine unmapped headers from the first row.
    sample_headers = list(raw[0].keys())
    unmapped: list[str] = []
    seen_unmapped: set[str] = set()
    for h in sample_headers:
        key = h.strip().lower()
        if key not in DC_COLUMN_MAP and h not in seen_unmapped:
            unmapped.append(h)
            seen_unmapped.add(h)

    normalised: list[dict] = []
    for raw_row in raw:
        row: dict = {}
        for h, v in raw_row.items():
            key = h.strip().lower()
            dest = DC_COLUMN_MAP.get(key)
            if dest is None:
                continue
            v = (v or "").strip()
            if dest in _CENTS_FIELDS:
                row[dest] = _money_to_cents(v)
            elif dest in _DATE_FIELDS:
                row[dest] = _parse_date(v)
            elif dest in _INT_FIELDS:
                row[dest] = _to_int(v)
            else:
                row[dest] = v or None

        # Infer disposition when not already set by the export.
        if "disposition" not in row or not row["disposition"]:
            row["disposition"] = "sold" if row.get("sold_at") else "active"

        normalised.append(row)

    return normalised, unmapped
