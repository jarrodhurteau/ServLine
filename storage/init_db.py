import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # repo root (servline/)
STORAGE = ROOT / "storage"
DB_PATH = STORAGE / "servline.db"
SCHEMA = STORAGE / "schema.sql"
SEED = STORAGE / "seed_dev.sql"

def run_sql(conn, path):
    with open(path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())

def main():
    STORAGE.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        print(f"[ServLine] Removing existing DB: {DB_PATH}")
        DB_PATH.unlink()

    print(f"[ServLine] Creating DB: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        run_sql(conn, SCHEMA)
        run_sql(conn, SEED)
        conn.commit()
    finally:
        conn.close()

    print("[ServLine] DB ready. Seeded demo data.")
    print(f"[ServLine] Location: {DB_PATH}")

if __name__ == "__main__":
    main()
