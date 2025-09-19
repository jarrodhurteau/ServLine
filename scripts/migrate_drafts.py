#!/usr/bin/env python3
"""
Run with:
  python scripts/migrate_drafts.py

This is an idempotent migration helper for ServLine's SQLite DB.
It adds any missing columns/tables used by the Day 12–14 features.
"""

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "servline.db"


def col_exists(conn, table, col):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(r[1].lower() == col.lower() for r in cur.fetchall())


def table_exists(conn, table):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def add_col(conn, table, ddl):
    # ddl: e.g. "source_job_id INTEGER"
    col = ddl.split()[0]
    if not col_exists(conn, table, col):
        print(f"[+] {table}: adding column {ddl}")
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        conn.commit()
    else:
        print(f"[=] {table}: column {col} already present")


def create_table(conn, name, create_sql):
    if not table_exists(conn, name):
        print(f"[+] creating table {name}")
        conn.execute(create_sql)
        conn.commit()
    else:
        print(f"[=] table {name} already present")


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    # --- core POS/portal tables (no-op if they already exist) ---
    create_table(conn, "restaurants", """
        CREATE TABLE restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
    """)

    create_table(conn, "menus", """
        CREATE TABLE menus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE CASCADE
        );
    """)

    create_table(conn, "menu_items", """
        CREATE TABLE menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            price_cents INTEGER NOT NULL DEFAULT 0,
            is_available INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE
        );
    """)

    # --- import jobs ---
    create_table(conn, "import_jobs", """
        CREATE TABLE import_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER,
            filename TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            draft_path TEXT,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
        );
    """)

    # optional linkage (not strictly required by app.py, but useful to have)
    add_col(conn, "import_jobs", "draft_id INTEGER")

    # --- drafts (DB-backed) ---
    create_table(conn, "drafts", """
        CREATE TABLE drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            status TEXT NOT NULL DEFAULT 'editing',
            restaurant_id INTEGER,
            source TEXT,                 -- e.g. 'import'
            source_job_id INTEGER,       -- import_jobs.id
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
        );
    """)

    # ensure columns exist if table already present
    add_col(conn, "drafts", "status TEXT")
    add_col(conn, "drafts", "restaurant_id INTEGER")
    add_col(conn, "drafts", "source TEXT")
    add_col(conn, "drafts", "source_job_id INTEGER")

    # --- draft_items ---
    create_table(conn, "draft_items", """
        CREATE TABLE draft_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            price_cents INTEGER NOT NULL DEFAULT 0,
            category TEXT,
            position INTEGER,
            confidence INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
        );
    """)

    # ensure columns exist if table already present
    add_col(conn, "draft_items", "position INTEGER")
    add_col(conn, "draft_items", "confidence INTEGER")

    print("\n[OK] Migration complete.")
    conn.close()


if __name__ == "__main__":
    main()
