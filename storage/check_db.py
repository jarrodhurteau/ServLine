# storage/check_db.py
import sqlite3
from pathlib import Path
from typing import Iterable

DB_PATH = Path(__file__).resolve().parent / "servline.db"

OK = "[OK]"
MISS = "[MISSING]"

def cents_to_dollars(cents: int) -> str:
    return f"${cents/100:.2f}"

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

# ----------------------------
# Low-level checks
# ----------------------------

def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
    )
    return cur.fetchone() is not None

def index_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=? LIMIT 1", (name,)
    )
    return cur.fetchone() is not None

def trigger_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=? LIMIT 1", (name,)
    )
    return cur.fetchone() is not None

def count_rows(conn: sqlite3.Connection, table: str) -> int:
    cur = conn.execute(f"SELECT COUNT(1) FROM {table}")
    return cur.fetchone()[0]

# ----------------------------
# Pretty printers
# ----------------------------

def print_header(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)

def print_kv(status: str, thing: str) -> None:
    print(f"{status:<10} {thing}")

def preview_rows(conn: sqlite3.Connection, table: str, limit: int = 5, cols: Iterable[str] | None = None) -> None:
    if not table_exists(conn, table):
        return
    sel = ", ".join(cols) if cols else "*"
    cur = conn.execute(f"SELECT {sel} FROM {table} LIMIT {limit}")
    rows = cur.fetchall()
    if not rows:
        print(f"(no rows in {table})")
        return
    for row in rows:
        d = dict(row)
        # Pretty-print price if present
        if "price_cents" in d and "price" not in d:
            d["price"] = cents_to_dollars(d.pop("price_cents"))
        print(d)

# ----------------------------
# High-level validations
# ----------------------------

def check_core(conn: sqlite3.Connection) -> None:
    print_header("Core Tables")
    for t in ("restaurants", "menus", "menu_items", "import_jobs"):
        print_kv(OK if table_exists(conn, t) else MISS, f"table {t}")

    print_header("Core Foreign Keys & Indexes (basic presence)")
    # We just check presence of expected indexes that improve UX
    idx_checks = [
        "idx_import_jobs_status",
        "idx_import_jobs_lifecycle",
        "idx_import_jobs_created",
    ]
    for idx in idx_checks:
        print_kv(OK if index_exists(conn, idx) else MISS, f"index {idx}")

def check_drafts(conn: sqlite3.Connection) -> None:
    print_header("Drafts Schema (Day 12)")
    print_kv(OK if table_exists(conn, "drafts") else MISS, "table drafts")
    print_kv(OK if table_exists(conn, "draft_items") else MISS, "table draft_items")

    print_header("Drafts Indexes")
    for idx in ("idx_drafts_status", "idx_drafts_restaurant", "idx_draft_items_draft"):
        print_kv(OK if index_exists(conn, idx) else MISS, f"index {idx}")

    print_header("Drafts Triggers")
    for trg in ("trg_drafts_updated", "trg_draft_items_updated"):
        print_kv(OK if trigger_exists(conn, trg) else MISS, f"trigger {trg}")

def check_counts(conn: sqlite3.Connection) -> None:
    print_header("Row Counts")
    for t in ("restaurants", "menus", "menu_items", "import_jobs", "drafts", "draft_items"):
        if table_exists(conn, t):
            print_kv(OK, f"{t}: {count_rows(conn, t)}")
        else:
            print_kv(MISS, f"{t}: (table missing)")

def sample_data(conn: sqlite3.Connection) -> None:
    print_header("Sample Data: restaurants")
    preview_rows(conn, "restaurants")

    print_header("Sample Data: menus")
    preview_rows(conn, "menus")

    print_header("Sample Data: menu_items (id, menu_id, name, description, price_cents, is_available)")
    preview_rows(conn, "menu_items", cols=("id","menu_id","name","description","price_cents","is_available"))

    print_header("Sample Data: drafts")
    preview_rows(conn, "drafts")

    print_header("Sample Data: draft_items")
    preview_rows(conn, "draft_items")

# ----------------------------
# Entrypoint
# ----------------------------

def main() -> None:
    if not DB_PATH.exists():
        print(f"[ERROR] DB not found at: {DB_PATH}")
        print("Run `python -m storage.init_db` first.")
        return

    conn = connect()
    try:
        check_core(conn)
        check_drafts(conn)
        check_counts(conn)
        sample_data(conn)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
