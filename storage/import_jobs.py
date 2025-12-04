from __future__ import annotations
import csv
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "servline.db"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_import_jobs_columns() -> Set[str]:
    """
    Introspect the import_jobs table columns so we can insert flexibly.

    This lets us support new columns like:
      - source_type
      - restaurant_id
      - summary_json
      - payload_json

    without breaking older DBs that don't have them yet.
    """
    with db_connect() as conn:
        rows = conn.execute("PRAGMA table_info(import_jobs)").fetchall()
        return {str(r["name"]) for r in rows}


# ---------------------------------------------------------------------------
# Public read helpers
# ---------------------------------------------------------------------------

def get_import_job(job_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetch a single import job entry by ID.

    Expected base schema (Day 20+):
        id INTEGER PRIMARY KEY
        filename TEXT
        source_path TEXT (uploads/<file>)
        status TEXT
        created_at TEXT
        updated_at TEXT
        ... plus any OCR / structured metadata columns, e.g.:
            source_type TEXT
            restaurant_id INTEGER
            summary_json TEXT
            payload_json TEXT

    Returns dict or None.
    """
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM import_jobs WHERE id = ?",
            (int(job_id),)
        ).fetchone()

        if not row:
            return None

        return {k: row[k] for k in row.keys()}


def list_import_jobs(limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
    """
    Optional helper if you ever need it.
    """
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM import_jobs ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]


# ---------------------------------------------------------------------------
# Structured ingestion: CSV helpers
# ---------------------------------------------------------------------------

try:
    # One Brain contracts for structured ingestion (CSV / JSON)
    from . import contracts as structured_contracts  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - defensive; should exist in modern env
    structured_contracts = None  # type: ignore[assignment]


# Canonical field names for structured menu items.
CANONICAL_FIELDS = {
    "name",
    "description",
    "category",
    "subcategory",
    "price",
    "price_cents",
    "size",
    "sku",
}


HEADER_ALIASES: Dict[str, Set[str]] = {
    "name": {
        "name",
        "item",
        "itemname",
        "item_name",
        "itemtitle",
        "title",
        "menuitem",
    },
    "description": {
        "description",
        "desc",
        "details",
        "detail",
        "itemdescription",
    },
    "category": {
        "category",
        "cat",
        "section",
        "group",
        "menu_section",
        "menu_group",
    },
    "subcategory": {
        "subcategory",
        "subcat",
        "sub_category",
        "subsection",
        "sub_section",
    },
    "price": {
        "price",
        "cost",
        "amount",
        "baseprice",
        "base_price",
        "listprice",
    },
    "price_cents": {
        "pricecents",
        "price_cents",
    },
    "size": {
        "size",
        "portion",
        "variant",
        "serving",
    },
    "sku": {
        "sku",
        "code",
        "plu",
        "itemcode",
        "item_code",
    },
}


def _normalize_header(h: str) -> str:
    """
    Normalize a CSV header to a simple token for alias matching.
    """
    return "".join(ch for ch in h.lower() if ch.isalnum())


def _detect_header_mapping(headers: List[str]) -> Dict[str, str]:
    """
    Detect which CSV headers correspond to canonical structured fields.

    Returns mapping:
        { canonical_field: csv_header }
    """
    mapping: Dict[str, str] = {}

    if not headers:
        return mapping

    normalized: Dict[str, str] = {h: _normalize_header(h) for h in headers}

    for canonical, aliases in HEADER_ALIASES.items():
        for header, norm in normalized.items():
            if norm in aliases:
                mapping[canonical] = header
                break

    return mapping


def _csv_row_to_structured_item(
    raw_row: Dict[str, Any],
    header_map: Dict[str, str],
) -> Dict[str, Any]:
    """
    Convert a raw CSV row (DictReader output) into a canonical structured item dict.
    """
    item: Dict[str, Any] = {}

    for canonical in CANONICAL_FIELDS:
        csv_header = header_map.get(canonical)
        if csv_header is not None:
            item[canonical] = raw_row.get(csv_header)
        else:
            # some fields simply won't be mapped; that's OK
            pass

    # Collect leftover columns as meta
    meta: Dict[str, Any] = {}
    mapped_headers = set(header_map.values())
    for col_name, value in raw_row.items():
        if col_name not in mapped_headers:
            if value not in (None, "", " "):
                meta[col_name] = value

    if meta:
        item["meta"] = meta

    return item


def parse_structured_csv(
    csv_path: Path,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int], Dict[str, str]]:
    """
    Parse a CSV file into structured menu items and run validation via One Brain contracts.

    Returns:
      clean_items:  list of normalized items ready for draft creation
      errors:       list of row-level validation error dicts
      summary:      summary dict (total_rows / valid_rows / error_rows)
      header_map:   mapping of canonical field -> CSV header name
    """
    if structured_contracts is None:
        raise RuntimeError(
            "storage.contracts module is required for structured CSV parsing; "
            "make sure storage/contracts.py exists."
        )

    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        header_map = _detect_header_mapping(headers)

        raw_items: List[Dict[str, Any]] = []
        for row in reader:
            raw_items.append(_csv_row_to_structured_item(row, header_map))

    clean_items, errors, summary = structured_contracts.validate_structured_items(raw_items)
    return clean_items, errors, summary, header_map


# ---------------------------------------------------------------------------
# Structured import job creation (CSV + generic)
# ---------------------------------------------------------------------------

def create_structured_import_job(
    source_type: str,
    *,
    filename: Optional[str] = None,
    source_path: Optional[str] = None,
    restaurant_id: Optional[int] = None,
    summary: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    status: str = "parsed",
) -> int:
    """
    Create an import_jobs row for a structured import (CSV / JSON).

    This function is resilient to schema differences by introspecting columns.

    Used by:
      - create_csv_import_job_from_file (...)
      - (later) JSON-based structured ingest.

    Returns:
      job_id (int)
    """
    columns = _get_import_jobs_columns()
    insert_cols: List[str] = []
    values: List[Any] = []

    if "filename" in columns and filename is not None:
        insert_cols.append("filename")
        values.append(filename)

    if "source_path" in columns and source_path is not None:
        insert_cols.append("source_path")
        values.append(source_path)

    if "source_type" in columns:
        insert_cols.append("source_type")
        values.append(source_type)

    if "restaurant_id" in columns and restaurant_id is not None:
        insert_cols.append("restaurant_id")
        values.append(int(restaurant_id))

    if "status" in columns:
        insert_cols.append("status")
        values.append(status)

    if "summary_json" in columns and summary is not None:
        insert_cols.append("summary_json")
        values.append(json.dumps(summary))

    if "payload_json" in columns and payload is not None:
        insert_cols.append("payload_json")
        values.append(json.dumps(payload))

    if not insert_cols:
        raise RuntimeError("import_jobs table has no usable columns for INSERT")

    placeholders = ", ".join(["?"] * len(insert_cols))
    col_list = ", ".join(insert_cols)
    sql = f"INSERT INTO import_jobs ({col_list}) VALUES ({placeholders})"

    with db_connect() as conn:
        cur = conn.execute(sql, values)
        job_id = cur.lastrowid
        conn.commit()

    return int(job_id)


def create_csv_import_job_from_file(
    csv_path: Path,
    restaurant_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    High-level helper for Phase 6 pt.1:

    - Parse a CSV into structured items
    - Validate via One Brain contracts
    - Create an import_jobs row recording the results
    - Return details for draft creation + UI summary

    Returns dict:
      {
        "job_id": int,
        "items": [... clean items ...],
        "errors": [... error dicts ...],
        "summary": {... counts ...},
        "header_map": {... canonical -> csv header ...},
        "job_summary": {... stored in summary_json (if column exists) ...},
      }
    """
    csv_path = Path(csv_path)
    clean_items, errors, summary, header_map = parse_structured_csv(csv_path)

    job_summary: Dict[str, Any] = {
        "ingest_mode": "structured_csv",
        "header_map": header_map,
        "summary": summary,
        "error_rows": errors,
    }

    status = "parsed_with_errors" if errors else "parsed"

    job_id = create_structured_import_job(
        source_type="structured_csv",
        filename=csv_path.name,
        source_path=str(csv_path),
        restaurant_id=restaurant_id,
        summary=job_summary,
        payload=None,
        status=status,
    )

    return {
        "job_id": job_id,
        "items": clean_items,
        "errors": errors,
        "summary": summary,
        "header_map": header_map,
        "job_summary": job_summary,
    }
