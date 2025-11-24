# storage/import_jobs.py
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "servline.db"


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_import_job(job_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetch a single import job entry by ID.
    Expected schema (Day 20+):
        id INTEGER PRIMARY KEY
        filename TEXT
        source_path TEXT (uploads/<file>)
        status TEXT
        created_at TEXT
        updated_at TEXT
        ... plus any OCR metadata columns
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


def list_import_jobs(limit: int = 200, offset: int = 0):
    """
    Optional helper if you ever need it.
    """
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM import_jobs ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]
