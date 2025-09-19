# storage/drafts.py
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

# ------------------------------------------------------------
# Paths / DB
# ------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]   # project root
DB_PATH = ROOT / "storage" / "servline.db"

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

# ------------------------------------------------------------
# Schema (idempotent; safe with external schema.sql + migrate)
# ------------------------------------------------------------
def _ensure_schema() -> None:
    with db_connect() as conn:
        cur = conn.cursor()

        # drafts (includes source JSON + source_job_id link)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS drafts (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              title         TEXT,
              restaurant_id INTEGER,
              status        TEXT NOT NULL DEFAULT 'editing',
              source        TEXT,               -- JSON string (file, ocr_engine, etc)
              source_job_id INTEGER,            -- import_jobs.id if applicable
              created_at    TEXT NOT NULL,
              updated_at    TEXT NOT NULL,
              FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
            )
            """
        )

        # draft_items (includes confidence)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS draft_items (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              draft_id    INTEGER NOT NULL,
              name        TEXT NOT NULL,
              description TEXT,
              price_cents INTEGER NOT NULL DEFAULT 0,
              category    TEXT,
              position    INTEGER,
              confidence  INTEGER,              -- OCR confidence (nullable)
              created_at  TEXT NOT NULL,
              updated_at  TEXT NOT NULL,
              FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
            )
            """
        )

        # helpful indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_drafts_source_job ON drafts(source_job_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_items_draft ON draft_items(draft_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_items_cat ON draft_items(draft_id, category)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_items_pos ON draft_items(draft_id, position)")

        # in case existing DBs predate Day-14 columns, patch them
        def _col_exists(table: str, col: str) -> bool:
            return any(r[1].lower() == col for r in conn.execute(f"PRAGMA table_info({table});").fetchall())

        if not _col_exists("drafts", "source"):
            cur.execute("ALTER TABLE drafts ADD COLUMN source TEXT;")
        if not _col_exists("draft_items", "confidence"):
            cur.execute("ALTER TABLE draft_items ADD COLUMN confidence INTEGER;")

        conn.commit()

_ensure_schema()

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}

def _coerce_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default

def _coerce_opt_int(v: Any) -> Optional[int]:
    try:
        if v is None or str(v).strip() == "":
            return None
        return int(v)
    except Exception:
        return None

# ------------------------------------------------------------
# Public API consumed by portal/app.py
# ------------------------------------------------------------
def list_drafts(*, status: Optional[str] = None, restaurant_id: Optional[int] = None,
                limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
    qs = "SELECT * FROM drafts WHERE 1=1"
    args: List[Any] = []
    if status:
        qs += " AND status=?"
        args.append(status)
    if restaurant_id is not None:
        qs += " AND restaurant_id=?"
        args.append(int(restaurant_id))
    qs += " ORDER BY datetime(updated_at) DESC, id DESC LIMIT ? OFFSET ?"
    args += [int(limit), int(offset)]
    with db_connect() as conn:
        rows = conn.execute(qs, args).fetchall()
    return [_row_to_dict(r) for r in rows]

def get_draft(draft_id: int) -> Optional[Dict[str, Any]]:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM drafts WHERE id=?", (int(draft_id),)).fetchone()
        return _row_to_dict(row) if row else None

def get_draft_items(draft_id: int) -> List[Dict[str, Any]]:
    with db_connect() as conn:
        rows = conn.execute(
            # NULL positions go last (use a big integer sentinel)
            "SELECT * FROM draft_items WHERE draft_id=? ORDER BY COALESCE(position, 1000000000), id",
            (int(draft_id),)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

def save_draft_metadata(draft_id: int, *, title: Optional[str] = None,
                        restaurant_id: Optional[int] = None,
                        status: Optional[str] = None,
                        source: Optional[str] = None,
                        source_job_id: Optional[int] = None) -> None:
    sets: List[str] = []
    args: List[Any] = []

    if title is not None:
        sets.append("title=?"); args.append(title)
    if restaurant_id is not None:
        sets.append("restaurant_id=?"); args.append(int(restaurant_id))
    if status is not None:
        sets.append("status=?"); args.append(status)
    if source is not None:
        # store as-is (caller may pass JSON string)
        sets.append("source=?"); args.append(source)
    if source_job_id is not None:
        sets.append("source_job_id=?"); args.append(int(source_job_id))

    if not sets:
        return
    sets.append("updated_at=?"); args.append(_now())
    args.append(int(draft_id))

    with db_connect() as conn:
        conn.execute(f"UPDATE drafts SET {', '.join(sets)} WHERE id=?", args)
        conn.commit()

def submit_draft(draft_id: int) -> None:
    save_draft_metadata(int(draft_id), status="submitted")

def _insert_draft(*, title: Optional[str], restaurant_id: Optional[int],
                  status: str = "editing", source: Optional[str] = None,
                  source_job_id: Optional[int] = None) -> int:
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO drafts (title, restaurant_id, status, source, source_job_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (title, restaurant_id, status, source, source_job_id, _now(), _now())
        )
        conn.commit()
        return int(cur.lastrowid)

def _insert_items_bulk(draft_id: int, items: Iterable[Dict[str, Any]]) -> List[int]:
    ids: List[int] = []
    with db_connect() as conn:
        cur = conn.cursor()
        for it in items:
            name = (it.get("name") or "").strip()
            if not name:
                continue
            desc = (it.get("description") or "").strip()
            price_cents = _coerce_int(it.get("price_cents"), 0)
            category = (it.get("category") or None)
            position = _coerce_opt_int(it.get("position"))
            confidence = _coerce_opt_int(it.get("confidence"))

            cur.execute(
                """
                INSERT INTO draft_items (draft_id, name, description, price_cents, category, position, confidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (int(draft_id), name, desc, price_cents, category, position, confidence, _now(), _now())
            )
            ids.append(int(cur.lastrowid))
        conn.commit()
    return ids

def upsert_draft_items(draft_id: int, items: Iterable[Dict[str, Any]]) -> Dict[str, List[int]]:
    """
    Upsert by presence of 'id':
      - If id is a valid integer -> UPDATE that item
      - Else -> INSERT new item
    Returns: {"inserted_ids":[...], "updated_ids":[...]}
    """
    inserted, updated = [], []
    with db_connect() as conn:
        cur = conn.cursor()
        for it in items:
            raw_id = it.get("id")
            has_int_id = False
            try:
                if raw_id is not None and str(raw_id).isdigit():
                    item_id = int(raw_id)
                    has_int_id = True
                else:
                    item_id = None
            except Exception:
                item_id = None

            name = (it.get("name") or "").strip()
            if not name:
                # skip blanks
                continue

            desc = (it.get("description") or "").strip()
            price_cents = _coerce_int(it.get("price_cents"), 0)
            category = (it.get("category") or None)
            position = _coerce_opt_int(it.get("position"))
            confidence = _coerce_opt_int(it.get("confidence"))

            if has_int_id:
                cur.execute(
                    """
                    UPDATE draft_items
                    SET name=?, description=?, price_cents=?, category=?, position=?, confidence=?, updated_at=?
                    WHERE id=? AND draft_id=?
                    """,
                    (name, desc, price_cents, category, position, confidence, _now(), item_id, int(draft_id))
                )
                if cur.rowcount > 0:
                    updated.append(item_id)
                else:
                    # if the id doesn't belong to this draft, insert instead (safety)
                    cur.execute(
                        """
                        INSERT INTO draft_items (draft_id, name, description, price_cents, category, position, confidence, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (int(draft_id), name, desc, price_cents, category, position, confidence, _now(), _now())
                    )
                    inserted.append(int(cur.lastrowid))
            else:
                cur.execute(
                    """
                    INSERT INTO draft_items (draft_id, name, description, price_cents, category, position, confidence, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (int(draft_id), name, desc, price_cents, category, position, confidence, _now(), _now())
                )
                inserted.append(int(cur.lastrowid))

        conn.commit()

    return {"inserted_ids": inserted, "updated_ids": updated}

def delete_draft_items(draft_id: int, item_ids: Iterable[int]) -> int:
    ids = [int(i) for i in item_ids if str(i).isdigit()]
    if not ids:
        return 0
    with db_connect() as conn:
        cur = conn.cursor()
        qmarks = ",".join(["?"] * len(ids))
        cur.execute(
            f"DELETE FROM draft_items WHERE draft_id=? AND id IN ({qmarks})",
            (int(draft_id), *ids)
        )
        conn.commit()
        return int(cur.rowcount)

# ------------------------------------------------------------
# Import bridge (legacy JSON → DB-first draft)
# ------------------------------------------------------------
def find_draft_by_source_job(job_id: int) -> Optional[Dict[str, Any]]:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM drafts WHERE source_job_id=? ORDER BY id DESC LIMIT 1",
            (int(job_id),)
        ).fetchone()
        return _row_to_dict(row) if row else None

def create_draft_from_import(draft_json: Dict[str, Any], *, import_job_id: int) -> Dict[str, Any]:
    """
    Accepts JSON like:
    {
      "job_id": ...,
      "source": {"type":"upload","file":"...","ocr_engine":"..."},
      "categories": [
        {"name":"Pizzas","items":[
           {"name":"Cheese Pizza", "description":"", "sizes":[{"name":"Small","price":9.99}], "confidence": 68}
        ]}
      ]
    }
    Explodes sizes into individual rows; if no sizes, uses item.price or 0.
    """
    title = f"Imported {datetime.utcnow().date()}"

    # Persist full source JSON (stringified) so editor can display provenance
    source_blob = json.dumps(draft_json.get("source") or {}, ensure_ascii=False)

    draft_id = _insert_draft(
        title=title,
        restaurant_id=None,
        status="editing",
        source=source_blob,
        source_job_id=int(import_job_id)
    )

    flat_items: List[Dict[str, Any]] = []
    for cat in (draft_json.get("categories") or []):
        cat_name = (cat.get("name") or "").strip() or None
        for item in (cat.get("items") or []):
            base = (item.get("name") or "").strip()
            if not base:
                continue
            desc = (item.get("description") or "").strip()
            confidence = _coerce_opt_int(item.get("confidence"))

            sizes = item.get("sizes") or []
            if sizes:
                for s in sizes:
                    size_name = (s.get("name") or "").strip()
                    price = s.get("price", 0)
                    try:
                        cents = int(round(float(price) * 100))
                    except Exception:
                        cents = 0
                    name = f"{base} ({size_name})" if size_name else base
                    flat_items.append({
                        "name": name,
                        "description": desc,
                        "price_cents": cents,
                        "category": cat_name,
                        "position": None,
                        "confidence": confidence
                    })
            else:
                price = item.get("price", 0)
                try:
                    cents = int(round(float(price) * 100))
                except Exception:
                    cents = 0
                flat_items.append({
                    "name": base,
                    "description": desc,
                    "price_cents": cents,
                    "category": cat_name,
                    "position": None,
                    "confidence": confidence
                })

    _insert_items_bulk(draft_id, flat_items)
    return {"id": draft_id, "draft_id": draft_id}

# ------------------------------------------------------------
# Clone
# ------------------------------------------------------------
def clone_draft(draft_id: int) -> Dict[str, Any]:
    src = get_draft(int(draft_id))
    if not src:
        raise ValueError(f"Draft {draft_id} not found")

    # create new shell with "(copy)" in title, keep linkage to source_job_id but reset status
    new_title = ((src.get("title") or "").strip() or f"Draft {draft_id}") + " (copy)"
    new_id = _insert_draft(
        title=new_title,
        restaurant_id=src.get("restaurant_id"),
        status="editing",
        source=src.get("source"),
        source_job_id=src.get("source_job_id")
    )

    items = get_draft_items(int(draft_id))
    _insert_items_bulk(new_id, [{
        "name": it.get("name"),
        "description": it.get("description"),
        "price_cents": _coerce_int(it.get("price_cents"), 0),
        "category": it.get("category"),
        "position": it.get("position"),
        "confidence": _coerce_opt_int(it.get("confidence")),
    } for it in items])

    return {"id": new_id, "draft_id": new_id}
