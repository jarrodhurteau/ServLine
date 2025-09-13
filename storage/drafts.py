# storage/drafts.py
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "servline.db"

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

# ---------- Queries ----------

def list_drafts(*, status: Optional[str] = None, restaurant_id: Optional[int] = None,
                limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    sql = ["SELECT id, restaurant_id, title, status, created_at, updated_at FROM drafts"]
    args: List[Any] = []
    where = []
    if status:
        where.append("status = ?")
        args.append(status)
    if restaurant_id:
        where.append("restaurant_id = ?")
        args.append(int(restaurant_id))
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY datetime(updated_at) DESC, id DESC LIMIT ? OFFSET ?")
    args.extend([int(limit), int(offset)])
    with _connect() as conn:
        rows = conn.execute(" ".join(sql), args).fetchall()
        return [dict(r) for r in rows]

def get_draft(draft_id: int) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, restaurant_id, title, status, source, created_at, updated_at "
            "FROM drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        return dict(row) if row else None

def get_draft_items(draft_id: int) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, draft_id, name, description, price_cents, category, position "
            "FROM draft_items WHERE draft_id = ? "
            "ORDER BY COALESCE(position, 1<<30), id", (draft_id,)
        ).fetchall()
        return [dict(r) for r in rows]

# ---------- Mutations ----------

def create_draft(*, restaurant_id: Optional[int] = None, title: str = "Untitled Draft",
                 source: Optional[str] = None) -> int:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO drafts (restaurant_id, title, status, source) VALUES (?, ?, 'editing', ?)",
            (restaurant_id, title, source)
        )
        conn.commit()
        return int(cur.lastrowid)

def save_draft_metadata(draft_id: int, *, title: Optional[str] = None,
                        restaurant_id: Optional[int] = None) -> None:
    sets = []
    args: List[Any] = []
    if title is not None:
        sets.append("title = ?")
        args.append(title)
    if restaurant_id is not None:
        sets.append("restaurant_id = ?")
        args.append(restaurant_id)
    if not sets:
        return
    sets.append("updated_at = datetime('now')")
    with _connect() as conn:
        conn.execute(f"UPDATE drafts SET {', '.join(sets)} WHERE id = ?", (*args, draft_id))
        conn.commit()

def upsert_draft_items(draft_id: int, items: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    """
    Items schema expected from UI:
    { id?: int | 'tmp-...', name, description, price_cents, category, position }
    - Insert when id is missing/non-int
    - Update when id is an int
    """
    inserted_ids: List[int] = []
    updated_ids: List[int] = []
    with _connect() as conn:
        cur = conn.cursor()
        for it in items:
            name = (it.get("name") or "").strip()
            if not name:
                # skip totally blank rows
                continue
            desc = (it.get("description") or "").strip()
            category = (it.get("category") or "").strip() or None
            try:
                price_cents = int(it.get("price_cents") or 0)
            except Exception:
                price_cents = 0
            pos = it.get("position")
            try:
                pos = int(pos) if pos is not None and str(pos) != "" else None
            except Exception:
                pos = None

            raw_id = it.get("id")
            is_update = isinstance(raw_id, int) or (isinstance(raw_id, str) and raw_id.isdigit())
            if is_update:
                rid = int(raw_id)
                cur.execute(
                    "UPDATE draft_items "
                    "SET name=?, description=?, price_cents=?, category=?, position=?, updated_at=datetime('now') "
                    "WHERE id=? AND draft_id=?",
                    (name, desc, price_cents, category, pos, rid, draft_id)
                )
                if cur.rowcount:
                    updated_ids.append(rid)
            else:
                cur.execute(
                    "INSERT INTO draft_items (draft_id, name, description, price_cents, category, position) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (draft_id, name, desc, price_cents, category, pos)
                )
                inserted_ids.append(int(cur.lastrowid))
        conn.commit()
    return {"inserted_ids": inserted_ids, "updated_ids": updated_ids}

def delete_draft_items(draft_id: int, ids: List[int]) -> int:
    if not ids:
        return 0
    qmarks = ",".join("?" for _ in ids)
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM draft_items WHERE draft_id=? AND id IN ({qmarks})", (draft_id, *ids))
        conn.commit()
        return cur.rowcount

def submit_draft(draft_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE drafts SET status='submitted', updated_at=datetime('now') "
            "WHERE id=? AND status='editing'", (draft_id,)
        )
        conn.commit()

# ---------- Bridge helper for legacy import JSON ----------

def _to_cents(x: Any) -> int:
    try:
        return int(round(float(x) * 100))
    except Exception:
        return 0

def create_draft_from_import(draft_json: Dict[str, Any],
                             *,
                             import_job_id: Optional[int] = None,
                             restaurant_id: Optional[int] = None,
                             title: Optional[str] = None) -> Dict[str, Any]:
    """
    Promote a legacy import JSON payload into the DB-backed drafts tables.
    Expected JSON shape (examples):
      {
        "job_id": 123,
        "source": {"type":"upload","file":"abc.png","ocr_engine":"..."},
        "extracted_at": "2025-09-13T19:20:00Z",
        "categories": [
          {"name":"Pizzas","items":[
            {"name":"Cheese","description":"","sizes":[{"name":"Small","price":9.99},{"name":"Large","price":14.99}]},
            {"name":"Pepperoni","sizes":[{"name":"Large","price":16.49}]}
          ]},
          {"name":"Drinks","items":[{"name":"Soda","description":"20oz","sizes":[{"name":"One Size","price":2.49}]}]}
        ]
      }
    Returns: {"id": <draft_id>}
    """
    src_blob = None
    try:
        # store a minimal provenance string; DB column is TEXT
        src = draft_json.get("source") or {}
        src_blob = src.get("file") or src.get("type") or None
    except Exception:
        src_blob = None

    if title is None:
        title = f"Draft from import {import_job_id}" if import_job_id else "Imported Draft"

    # 1) create the draft row
    draft_id = create_draft(restaurant_id=restaurant_id, title=title, source=src_blob)

    # 2) flatten items and insert into draft_items
    items: List[Dict[str, Any]] = []
    categories = draft_json.get("categories") or []
    pos_counter = 0
    for cat in categories:
        cat_name = (cat.get("name") or "").strip() or None
        for it in (cat.get("items") or []):
            base = (it.get("name") or "").strip()
            if not base:
                continue
            desc = (it.get("description") or "").strip()
            sizes = it.get("sizes") or []
            if sizes:
                for s in sizes:
                    size_name = (s.get("name") or "").strip()
                    price = _to_cents(s.get("price", 0))
                    display = f"{base} ({size_name})" if size_name else base
                    items.append({
                        "name": display,
                        "description": desc,
                        "price_cents": price,
                        "category": cat_name,
                        "position": pos_counter
                    })
                    pos_counter += 1
            else:
                price = _to_cents(it.get("price", 0))
                items.append({
                    "name": base,
                    "description": desc,
                    "price_cents": price,
                    "category": cat_name,
                    "position": pos_counter
                })
                pos_counter += 1

    if items:
        upsert_draft_items(draft_id, items)

    return {"id": draft_id}
