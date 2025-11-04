# storage/drafts.py
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
import re

# ------------------------------------------------------------
# Optional ocr_utils import (shim if missing)
# ------------------------------------------------------------
try:
    # Real helpers (preferred) if you have storage/ocr_utils.py
    from . import ocr_utils  # type: ignore
except Exception:
    # Minimal, robust fallbacks so runtime & Pylance stay happy.
    class _OCRUtilsShim:
        # Sane menu price clamp in cents: $1.00–$99.99
        PRICE_MIN: int = 100
        PRICE_MAX: int = 9999

        # Loose price matcher: $12, 12.99, 8, 8.99, 899 (interpreted as 8.99)
        _price_rx = re.compile(
            r"""
            (?<!\d)                                  # no digit before
            (?:\$?\s*)                               # optional currency
            (?:
                (?P<dollars>\d{1,3})(?:\.(?P<cents>\d{1,2}))?  # 8 or 8.99 or 123.4
                |
                (?P<compact>\d{3,4})                # 899 or 1299
            )
            (?!\d)                                   # no digit after
            """,
            re.X,
        )

        def _to_cents(self, dollars: Optional[str], cents: Optional[str], compact: Optional[str]) -> Optional[int]:
            try:
                if compact:
                    # Interpret "899" as 8.99, "1299" as 12.99, etc.
                    if len(compact) == 3:
                        return int(compact)  # already cents (8.99 -> 899)
                    if len(compact) == 4:
                        return int(compact)  # 12.99 -> 1299
                    # Very large compact numbers are unlikely to be menu prices
                    return None
                if dollars is not None:
                    d = int(dollars)
                    c = int((cents or "0").ljust(2, "0")[:2])
                    return d * 100 + c
            except Exception:
                return None
            return None

        def find_price_candidates(self, text: str) -> List[int]:
            hits: List[int] = []
            for m in self._price_rx.finditer(text or ""):
                cents = self._to_cents(m.group("dollars"), m.group("cents"), m.group("compact"))
                if cents is None:
                    continue
                if self.PRICE_MIN <= cents <= self.PRICE_MAX:
                    hits.append(int(cents))
            return hits

        def is_garbage_line(self, s: str, *, price_hit: bool = False) -> bool:
            """
            Very conservative filter for nonsense OCR lines.
            Allows through short lines if we already have a valid price_hit.
            """
            if not s:
                return True
            t = s.strip()

            # If we already have a valid price for this line, be lenient.
            if price_hit:
                if len(t) <= 1:
                    return True
                # still drop blatantly non-alphabetic junk like '---' or '()[]'
                if not any(ch.isalpha() for ch in t) and not any(ch.isdigit() for ch in t):
                    return True
                return False

            # Without a price, require at least some letters and a minimum length.
            if len(t) < 3:
                return True
            letters = sum(ch.isalpha() for ch in t)
            digits = sum(ch.isdigit() for ch in t)
            if letters == 0 and digits == 0:
                return True

            # Too many symbols relative to letters → likely junk.
            symbols = sum(not (ch.isalnum() or ch.isspace()) for ch in t)
            if letters and symbols > letters * 2:
                return True

            # Heuristic: weird glyph soup (no spaces, many mixed-case flips) is often junk.
            if "  " in t and letters < 2:
                return True
            return False

    ocr_utils = _OCRUtilsShim()  # type: ignore

# ------------------------------------------------------------
# Paths / DB
# ------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]   # project root
DB_PATH = ROOT / "storage" / "servline.db"

# Sidecar debug storage for OCR Inspector
_DEBUG_BASE = ROOT / "storage" / ".debug" / "drafts"
_DEBUG_BASE.mkdir(parents=True, exist_ok=True)

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _debug_path(draft_id: int) -> Path:
    return _DEBUG_BASE / f"{int(draft_id)}.json"

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

def _to_cents(p: Any) -> int:
    try:
        return int(round(float(p) * 100))
    except Exception:
        return 0

def _clamp_price_cents(cents: Optional[int]) -> Optional[int]:
    """Clamp into sane menu range (default Day-22 guard: $1.00–$99.99)."""
    if cents is None:
        return None
    if cents < ocr_utils.PRICE_MIN or cents > ocr_utils.PRICE_MAX:
        return None
    return int(cents)

def _pick_price_from_ai_or_text(ai_price_candidates: List[Dict[str, Any]], name: str, desc: Optional[str]) -> Optional[int]:
    """
    Choose a main price (in cents).
    1) Prefer AI-provided candidates (value may be dollars as float).
    2) Else, extract from text (name + description) using OCR regex.
    """
    # 1) AI candidates (often {'value': 12.99}):
    for c in ai_price_candidates or []:
        try:
            v = c.get("value")
            if v is None:
                continue
            cents = int(round(float(v) * 100))
            cents = _clamp_price_cents(cents)
            if cents is not None:
                return cents
        except Exception:
            continue

    # 2) Text extraction (supports 8.99 / $12 / 899 etc.)
    text = f"{name} {(desc or '')}".strip()
    hits = ocr_utils.find_price_candidates(text)
    for c in hits:
        cents = _clamp_price_cents(int(c))
        if cents is not None:
            return cents
    return None

# ------------------------------------------------------------
# OCR Inspector debug sidecars
# ------------------------------------------------------------
def save_ocr_debug(draft_id: int, payload: Dict[str, Any]) -> None:
    """
    Persist a rich OCR debug payload to a sidecar JSON file:
      storage/.debug/drafts/<draft_id>.json
    This avoids DB migrations and keeps large blobs off the main table.
    """
    p = _debug_path(int(draft_id))
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload or {}, f, ensure_ascii=False, indent=2)

def load_ocr_debug(draft_id: int) -> Optional[Dict[str, Any]]:
    """
    Load the OCR debug payload if present. Returns None if missing or unreadable.
    """
    p = _debug_path(int(draft_id))
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
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

def approve_publish(draft_id: int) -> None:
    """Mark draft as published (used by /drafts/<id>/publish_now)."""
    save_draft_metadata(int(draft_id), status="published")

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
# Import bridge (legacy JSON → DB-first draft) + AI bridge
# ------------------------------------------------------------
def find_draft_by_source_job(job_id: int) -> Optional[Dict[str, Any]]:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM drafts WHERE source_job_id=? ORDER BY id DESC LIMIT 1",
            (int(job_id),)
        ).fetchone()
        return _row_to_dict(row) if row else None

def _flat_from_legacy_categories(draft_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Legacy path: explode categories/items/sizes to flat rows.
    """
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
                    cents = _to_cents(s.get("price", 0))
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
                cents = _to_cents(item.get("price", 0))
                flat_items.append({
                    "name": base,
                    "description": desc,
                    "price_cents": cents,
                    "category": cat_name,
                    "position": None,
                    "confidence": confidence
                })
    return flat_items

def _flat_from_ai_items(ai_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    AI path: map ai_ocr_helper.analyze_ocr_text(...) → flat rows.
    Hardening (Day 22):
      - Garbage guard: drop lines that fail is_garbage_line (unless valid price present).
      - Price normalization: prefer AI price candidates, else extract from text; clamp to sane range.
    """
    flat: List[Dict[str, Any]] = []

    for it in ai_items or []:
        name_raw = (it.get("name") or "").strip()
        if not name_raw:
            continue

        desc_raw = (it.get("description") or None)
        cat = (it.get("category") or None)
        pcs = it.get("price_candidates") or []

        # Pick a main price (in cents) with clamps
        cents_main = _pick_price_from_ai_or_text(pcs, name_raw, desc_raw)
        price_hit = cents_main is not None

        # Garbage guard on the NAME (allow leniency if a valid price is present)
        if ocr_utils.is_garbage_line(name_raw, price_hit=bool(price_hit)):
            # Skip nonsense like "von", symbol storms, etc.
            continue

        # Final price_cents (0 if missing)
        price_cents = int(cents_main) if cents_main is not None else 0

        # Confidence mapping (keep your original logic)
        conf_float = it.get("confidence")
        if isinstance(conf_float, float):
            if conf_float <= 1.0:
                confidence = int(round(conf_float * 100))
            else:
                confidence = int(round(conf_float))
        else:
            confidence = _coerce_opt_int(conf_float)

        flat.append({
            "name": name_raw,
            "description": (desc_raw or "").strip() or None,
            "price_cents": price_cents,
            "category": cat,
            "position": None,
            "confidence": confidence
        })

    return flat

def create_draft_from_import(draft_json: Dict[str, Any], *, import_job_id: int) -> Dict[str, Any]:
    """
    Accepts either:
      (A) Legacy JSON (categories/items/sizes)  -> builds rows from 'categories'
      (B) AI JSON:
          - {'ai_preview': {'items': [...]}}  or
          - {'preview': {'items': [...]}}     or
          - {'items': [...]}                  (direct)
          Each AI item should look like ai_ocr_helper.analyze_ocr_text(...) output.

    We choose AI when present; otherwise fallback to legacy.
    """
    title = f"Imported {datetime.utcnow().date()}"

    # Persist source sidecar (raw)
    source_blob = json.dumps(draft_json.get("source") or {}, ensure_ascii=False)

    # Create draft shell
    draft_id = _insert_draft(
        title=title,
        restaurant_id=None,
        status="editing",
        source=source_blob,
        source_job_id=int(import_job_id)
    )

    # ---------- Prefer AI items if provided ----------
    ai_items = None
    if isinstance(draft_json.get("ai_preview"), dict):
        ai_items = (draft_json["ai_preview"] or {}).get("items")
    if ai_items is None and isinstance(draft_json.get("preview"), dict):
        ai_items = (draft_json["preview"] or {}).get("items")
    if ai_items is None and isinstance(draft_json.get("items"), list):
        ai_items = draft_json.get("items")

    if ai_items:
        flat_items = _flat_from_ai_items(ai_items)
        _insert_items_bulk(draft_id, flat_items)
        # Save a rich debug sidecar so OCR Inspector / Dev tabs can render the AI provenance
        try:
            save_ocr_debug(draft_id, {
                "bridge": "ai",
                "import_job_id": import_job_id,
                "ai_items_count": len(ai_items or []),
                "ai_sample": (ai_items[:20] if isinstance(ai_items, list) else None),
                "source_meta": draft_json.get("source") or {},
                "guards": {
                    "dropped_by_is_garbage_line": True,
                    "price_clamp_range": [ocr_utils.PRICE_MIN, ocr_utils.PRICE_MAX],
                }
            })
        except Exception:
            pass
        return {"id": draft_id, "draft_id": draft_id}

    # ---------- Fallback: legacy categories path ----------
    flat_items = _flat_from_legacy_categories(draft_json)
    _insert_items_bulk(draft_id, flat_items)
    try:
        save_ocr_debug(draft_id, {
            "bridge": "legacy",
            "import_job_id": import_job_id,
            "legacy_categories_count": len(draft_json.get("categories") or []),
            "source_meta": draft_json.get("source") or {},
        })
    except Exception:
        pass
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
