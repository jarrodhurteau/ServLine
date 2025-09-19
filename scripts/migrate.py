#!/usr/bin/env python3
"""
SQLite migration helper for ServLine.

- Adds `source` column to drafts if missing
- Adds `confidence` column to draft_items if missing
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "storage" / "servline.db"


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table});")
    return any(row[1].lower() == column.lower() for row in cur.fetchall())


def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # drafts.source
        if not column_exists(conn, "drafts", "source"):
            print("Adding column drafts.source ...")
            conn.execute("ALTER TABLE drafts ADD COLUMN source TEXT;")
        else:
            print("Column drafts.source already exists.")

        # draft_items.confidence
        if not column_exists(conn, "draft_items", "confidence"):
            print("Adding column draft_items.confidence ...")
            conn.execute("ALTER TABLE draft_items ADD COLUMN confidence INTEGER;")
        else:
            print("Column draft_items.confidence already exists.")

        conn.commit()
        print("Migration complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
