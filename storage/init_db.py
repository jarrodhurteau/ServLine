# storage/init_db.py
import sqlite3
from pathlib import Path
from typing import List

# --- Paths ---
ROOT = Path(__file__).resolve().parents[1]      # repo root: servline/
STORAGE = ROOT / "storage"
DB_PATH = STORAGE / "servline.db"
SCHEMA = STORAGE / "schema.sql"
SEED = STORAGE / "seed_dev.sql"
MIGRATIONS_DIR = STORAGE / "migrations"

# Day 8: folders we always want present
DRAFTS_DIR = STORAGE / "drafts"                 # where we keep cleaned-up artifacts
UPLOADS_DIR = ROOT / "uploads"                  # where user files land
TRASH_DIR = UPLOADS_DIR / ".trash"              # soft-deleted uploads live here

# ----------------------------
# Utilities
# ----------------------------

def connect_db() -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys enforced."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def run_sql_path(conn: sqlite3.Connection, path: Path) -> None:
    """Executes an entire .sql file if it exists (idempotent schema + optional seed)."""
    if not path.exists():
        print(f"[ServLine] Skipping missing SQL file: {path}")
        return
    with path.open("r", encoding="utf-8") as f:
        sql = f.read()
    if not sql.strip():
        print(f"[ServLine] Empty SQL file: {path}")
        return
    conn.executescript(sql)

def ensure_folders() -> None:
    """Create required folders (safe if they already exist)."""
    STORAGE.mkdir(parents=True, exist_ok=True)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)

# ----------------------------
# Migration runner
# ----------------------------

def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
          filename   TEXT PRIMARY KEY,
          applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

def list_migration_files() -> List[Path]:
    if not MIGRATIONS_DIR.exists():
        return []
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))

def get_applied_migrations(conn: sqlite3.Connection) -> set:
    ensure_migrations_table(conn)
    cur = conn.execute("SELECT filename FROM schema_migrations")
    return {row[0] for row in cur.fetchall()}

def apply_migration(conn: sqlite3.Connection, path: Path) -> None:
    print(f"[ServLine] Applying migration: {path.name}")
    run_sql_path(conn, path)
    conn.execute("INSERT OR IGNORE INTO schema_migrations(filename) VALUES (?)", (path.name,))

def run_pending_migrations(conn: sqlite3.Connection) -> None:
    ensure_migrations_table(conn)
    applied = get_applied_migrations(conn)
    pending = [p for p in list_migration_files() if p.name not in applied]
    if not pending:
        print("[ServLine] No pending migrations.")
        return
    for path in pending:
        apply_migration(conn, path)
    print(f"[ServLine] Applied {len(pending)} migration(s).")

# ----------------------------
# Build / Migrate
# ----------------------------

def build_fresh_db() -> None:
    """Create a brand-new DB from schema (and seed if present), then run migrations."""
    print(f"[ServLine] Creating DB: {DB_PATH}")
    conn = connect_db()
    try:
        run_sql_path(conn, SCHEMA)   # base schema (idempotent)
        run_sql_path(conn, SEED)     # optional seed (idempotent)
        run_pending_migrations(conn) # apply any migrations on top
        conn.commit()
    finally:
        conn.close()

def migrate_existing_db() -> None:
    """Run any pending migrations against an existing DB."""
    print(f"[ServLine] Migrating existing DB: {DB_PATH}")
    conn = connect_db()
    try:
        # Ensure base schema exists in case of partial environments
        run_sql_path(conn, SCHEMA)
        run_pending_migrations(conn)
        conn.commit()
    finally:
        conn.close()

# ----------------------------
# CLI entry
# ----------------------------

def main() -> None:
    ensure_folders()

    if not DB_PATH.exists():
        build_fresh_db()
    else:
        migrate_existing_db()

    print("[ServLine] DB ready. Seeded demo data if seed_dev.sql present.")
    print(f"[ServLine] Location: {DB_PATH}")
    print(f"[ServLine] Drafts:   {DRAFTS_DIR}")
    print(f"[ServLine] Uploads:  {UPLOADS_DIR}")
    print(f"[ServLine] Trash:    {TRASH_DIR}")
    print(f"[ServLine] Migrations dir: {MIGRATIONS_DIR}")

if __name__ == "__main__":
    main()
