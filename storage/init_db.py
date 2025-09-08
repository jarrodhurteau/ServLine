import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # repo root (servline/)
STORAGE = ROOT / "storage"
DB_PATH = STORAGE / "servline.db"
SCHEMA = STORAGE / "schema.sql"
SEED = STORAGE / "seed_dev.sql"

# Day 8: ensure drafts dir (and uploads dir for convenience)
DRAFTS_DIR = STORAGE / "drafts"
UPLOADS_DIR = ROOT / "uploads"

def run_sql(conn, path: Path):
    if not path.exists():
        print(f"[ServLine] Skipping missing SQL file: {path}")
        return
    with open(path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())

def main():
    STORAGE.mkdir(parents=True, exist_ok=True)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    if DB_PATH.exists():
        print(f"[ServLine] Removing existing DB: {DB_PATH}")
        DB_PATH.unlink()

    print(f"[ServLine] Creating DB: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        # Ensure FK constraints are enforced for this connection
        conn.execute("PRAGMA foreign_keys = ON;")

        run_sql(conn, SCHEMA)
        run_sql(conn, SEED)

        conn.commit()
    finally:
        conn.close()

    print("[ServLine] DB ready. Seeded demo data (if seed_dev.sql present).")
    print(f"[ServLine] Location: {DB_PATH}")
    print(f"[ServLine] Drafts folder: {DRAFTS_DIR}")
    print(f"[ServLine] Uploads folder: {UPLOADS_DIR}")

if __name__ == "__main__":
    main()
