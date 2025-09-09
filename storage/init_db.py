# storage/init_db.py
import sqlite3
from pathlib import Path

# --- Paths ---
ROOT = Path(__file__).resolve().parents[1]      # repo root: servline/
STORAGE = ROOT / "storage"
DB_PATH = STORAGE / "servline.db"
SCHEMA = STORAGE / "schema.sql"
SEED = STORAGE / "seed_dev.sql"

# Day 8: folders we always want present
DRAFTS_DIR = STORAGE / "drafts"                 # where we keep cleaned-up artifacts
UPLOADS_DIR = ROOT / "uploads"                  # where user files land
TRASH_DIR = UPLOADS_DIR / ".trash"              # soft-deleted uploads live here

def run_sql_path(conn: sqlite3.Connection, path: Path) -> None:
    """Executes an entire .sql file if it exists (idempotent schema + optional seed)."""
    if not path.exists():
        print(f"[ServLine] Skipping missing SQL file: {path}")
        return
    with path.open("r", encoding="utf-8") as f:
        conn.executescript(f.read())

def ensure_folders() -> None:
    """Create required folders (safe if they already exist)."""
    STORAGE.mkdir(parents=True, exist_ok=True)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)

def rebuild_db() -> None:
    """Blow away the dev DB and rebuild from schema (and seed if present)."""
    if DB_PATH.exists():
        print(f"[ServLine] Removing existing DB: {DB_PATH}")
        DB_PATH.unlink()

    print(f"[ServLine] Creating DB: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        # Enforce FKs for this connection (SQLite requires per-connection PRAGMA)
        conn.execute("PRAGMA foreign_keys = ON;")

        # Apply schema and seed (seed is optional)
        run_sql_path(conn, SCHEMA)
        run_sql_path(conn, SEED)

        conn.commit()
    finally:
        conn.close()

def main() -> None:
    ensure_folders()
    rebuild_db()

    print("[ServLine] DB ready. Seeded demo data if seed_dev.sql present.")
    print(f"[ServLine] Location: {DB_PATH}")
    print(f"[ServLine] Drafts:   {DRAFTS_DIR}")
    print(f"[ServLine] Uploads:  {UPLOADS_DIR}")
    print(f"[ServLine] Trash:    {TRASH_DIR}")

if __name__ == "__main__":
    main()
