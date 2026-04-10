# storage/drafts.py
from __future__ import annotations
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
import re

from .import_jobs import (
    get_import_job,
    rebuild_structured_items_from_header_map,
)


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

        def _to_cents(
            self,
            dollars: Optional[str],
            cents: Optional[str],
            compact: Optional[str],
        ) -> Optional[int]:
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
                cents = self._to_cents(
                    m.group("dollars"),
                    m.group("cents"),
                    m.group("compact"),
                )
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
                if not any(ch.isalpha() for ch in t) and not any(
                    ch.isdigit() for ch in t
                ):
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
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              title           TEXT,
              restaurant_id   INTEGER,
              status          TEXT NOT NULL DEFAULT 'editing',
              source          TEXT,               -- JSON string (file, ocr_engine, etc)
              source_job_id   INTEGER,            -- import_jobs.id if applicable
              source_file_path TEXT,              -- original uploaded file path for debug OCR
              created_at      TEXT NOT NULL,
              updated_at      TEXT NOT NULL,
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

        # draft_item_variants (Phase 9 — structured variant storage)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS draft_item_variants (
              id                  INTEGER PRIMARY KEY AUTOINCREMENT,
              item_id             INTEGER NOT NULL,
              label               TEXT NOT NULL,
              price_cents         INTEGER NOT NULL DEFAULT 0,
              kind                TEXT DEFAULT 'size',
              position            INTEGER DEFAULT 0,
              modifier_group_id   INTEGER,
              created_at          TEXT NOT NULL,
              updated_at          TEXT NOT NULL,
              FOREIGN KEY (item_id) REFERENCES draft_items(id) ON DELETE CASCADE
            )
            """
        )

        # draft_export_history (Day 83 — export tracking)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS draft_export_history (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              draft_id      INTEGER NOT NULL,
              format        TEXT NOT NULL,
              item_count    INTEGER NOT NULL DEFAULT 0,
              variant_count INTEGER NOT NULL DEFAULT 0,
              warning_count INTEGER NOT NULL DEFAULT 0,
              exported_at   TEXT NOT NULL,
              FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
            )
            """
        )

        # api_keys (Day 84 — REST API authentication)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              key_hash        TEXT NOT NULL UNIQUE,
              restaurant_id   INTEGER,
              label           TEXT NOT NULL DEFAULT '',
              active          INTEGER NOT NULL DEFAULT 1,
              rate_limit_rpm  INTEGER NOT NULL DEFAULT 60,
              created_at      TEXT NOT NULL,
              FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
            )
            """
        )

        # webhooks (Day 85 — webhook notification support)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS webhooks (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              restaurant_id   INTEGER,
              url             TEXT NOT NULL,
              event_types     TEXT NOT NULL DEFAULT '',
              secret          TEXT NOT NULL,
              active          INTEGER NOT NULL DEFAULT 1,
              created_at      TEXT NOT NULL,
              updated_at      TEXT NOT NULL,
              FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
            )
            """
        )

        # pipeline_rejections (Day 105 — confidence gate rejection logging)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_rejections (
              id                  INTEGER PRIMARY KEY AUTOINCREMENT,
              restaurant_id       INTEGER,
              draft_id            INTEGER,
              image_path          TEXT,
              ocr_chars           INTEGER NOT NULL DEFAULT 0,
              item_count          INTEGER NOT NULL DEFAULT 0,
              gate_score          REAL NOT NULL,
              gate_reason         TEXT,
              pipeline_signals    TEXT,
              created_at          TEXT NOT NULL
            )
            """
        )

        # draft_modifier_groups (Day 110 — Phase 12.1 schema kickoff)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS draft_modifier_groups (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              item_id     INTEGER NOT NULL,
              name        TEXT NOT NULL,
              required    INTEGER DEFAULT 0,
              min_select  INTEGER DEFAULT 0,
              max_select  INTEGER DEFAULT 0,
              position    INTEGER DEFAULT 0,
              created_at  TEXT NOT NULL,
              updated_at  TEXT NOT NULL,
              FOREIGN KEY (item_id) REFERENCES draft_items(id) ON DELETE CASCADE
            )
            """
        )

        # draft_modifier_group_templates (Day 111 — reusable presets per restaurant)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS draft_modifier_group_templates (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              restaurant_id INTEGER,
              name          TEXT NOT NULL,
              required      INTEGER DEFAULT 0,
              min_select    INTEGER DEFAULT 0,
              max_select    INTEGER DEFAULT 0,
              position      INTEGER DEFAULT 0,
              modifiers     TEXT NOT NULL DEFAULT '[]',
              created_at    TEXT NOT NULL,
              updated_at    TEXT NOT NULL
            )
            """
        )

        # helpful indexes
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_drafts_source_job ON drafts(source_job_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_draft ON draft_items(draft_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_cat ON draft_items(draft_id, category)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_pos ON draft_items(draft_id, position)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_variants_item ON draft_item_variants(item_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_export_history_draft ON draft_export_history(draft_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_webhooks_restaurant ON webhooks(restaurant_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_webhooks_active ON webhooks(active)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_rejections_restaurant ON pipeline_rejections(restaurant_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_rejections_created ON pipeline_rejections(created_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_modifier_groups_item ON draft_modifier_groups(item_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_templates_restaurant "
            "ON draft_modifier_group_templates(restaurant_id)"
        )

        # in case existing DBs predate Day-14+ columns, patch them
        def _col_exists(table: str, col: str) -> bool:
            return any(
                r[1].lower() == col
                for r in conn.execute(f"PRAGMA table_info({table});").fetchall()
            )

        if not _col_exists("drafts", "source"):
            cur.execute("ALTER TABLE drafts ADD COLUMN source TEXT;")
        if not _col_exists("drafts", "source_job_id"):
            cur.execute("ALTER TABLE drafts ADD COLUMN source_job_id INTEGER;")
        if not _col_exists("drafts", "source_file_path"):
            cur.execute("ALTER TABLE drafts ADD COLUMN source_file_path TEXT;")
        if not _col_exists("draft_items", "confidence"):
            cur.execute("ALTER TABLE draft_items ADD COLUMN confidence INTEGER;")
        if not _col_exists("drafts", "menu_id"):
            cur.execute("ALTER TABLE drafts ADD COLUMN menu_id INTEGER;")
        if not _col_exists("draft_item_variants", "modifier_group_id"):
            cur.execute(
                "ALTER TABLE draft_item_variants ADD COLUMN modifier_group_id INTEGER;"
            )
        # idx_variants_group must come AFTER the ALTER that adds modifier_group_id
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_variants_group "
            "ON draft_item_variants(modifier_group_id)"
        )
        if not _col_exists("draft_items", "kitchen_name"):
            cur.execute(
                "ALTER TABLE draft_items ADD COLUMN kitchen_name TEXT;"
            )
        # Day 116 — category ordering per draft
        if not _col_exists("drafts", "category_order"):
            cur.execute(
                "ALTER TABLE drafts ADD COLUMN category_order TEXT;"
            )

        # Day 139 — bounding box coordinates for wizard highlighting
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS draft_item_coordinates (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              item_id     INTEGER NOT NULL,
              x_pct       REAL NOT NULL DEFAULT 0,
              y_pct       REAL NOT NULL DEFAULT 0,
              w_pct       REAL NOT NULL DEFAULT 0,
              h_pct       REAL NOT NULL DEFAULT 0,
              page        INTEGER NOT NULL DEFAULT 1,
              element_type TEXT DEFAULT 'item',
              created_at  TEXT NOT NULL,
              FOREIGN KEY (item_id) REFERENCES draft_items(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_coords_item "
            "ON draft_item_coordinates(item_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_coords_page "
            "ON draft_item_coordinates(page)"
        )

        # Day 139 — source_elements JSON on drafts (raw classified elements from Call 1)
        if not _col_exists("drafts", "source_elements"):
            cur.execute(
                "ALTER TABLE drafts ADD COLUMN source_elements TEXT;"
            )

        # Day 139.5 — gap_warnings JSON on drafts (Call 2 missed-region warnings)
        if not _col_exists("drafts", "gap_warnings"):
            cur.execute(
                "ALTER TABLE drafts ADD COLUMN gap_warnings TEXT;"
            )

        # Day 140 — subcategory grouping for modifiers/toppings
        if not _col_exists("draft_items", "subcategory"):
            cur.execute(
                "ALTER TABLE draft_items ADD COLUMN subcategory TEXT;"
            )

        # Day 137 — wizard category review tracking
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS draft_category_reviews (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              draft_id    INTEGER NOT NULL,
              category    TEXT NOT NULL,
              reviewed    INTEGER NOT NULL DEFAULT 0,
              reviewed_at TEXT,
              FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE,
              UNIQUE(draft_id, category)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_cat_reviews_draft "
            "ON draft_category_reviews(draft_id)"
        )

        # Day 137 — track whether wizard has been completed for a draft
        if not _col_exists("drafts", "wizard_completed"):
            cur.execute(
                "ALTER TABLE drafts ADD COLUMN wizard_completed INTEGER DEFAULT 0;"
            )

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


def _normalize_item_for_db(raw: Any) -> Optional[Dict[str, Any]]:
    """
    Defensive normalizer for draft_items payloads.

    Ensures:
      - name: non-empty string (required; rows without a name are dropped)
      - description: always a string (may be empty)
      - price_cents: int, never negative (bad / NaN -> 0)
      - category: normalized to a trimmed string or None
      - position: optional int or None
      - confidence: optional int in [0, 100] or None (accepts 0–1 floats as %)
    """
    if not isinstance(raw, dict):
        return None

    # Name (required)
    name_raw = raw.get("name")
    name = str(name_raw).strip() if name_raw is not None else ""
    if not name:
        return None  # Finalize / editor will silently drop nameless rows

    # Description (optional, always string)
    desc_raw = raw.get("description")
    description = "" if desc_raw is None else str(desc_raw).strip()

    # Price cents (never negative; we do NOT re-interpret dollars here)
    price_raw = raw.get("price_cents")
    try:
        price_cents = int(price_raw)
    except Exception:
        price_cents = 0
    if price_cents < 0:
        price_cents = 0

    # Category (normalized string or None)
    cat_raw = raw.get("category")
    if cat_raw is None:
        category: Optional[str] = None
    else:
        cat_str = str(cat_raw).strip()
        category = cat_str or None

    # Position: optional int
    position = _coerce_opt_int(raw.get("position"))

    # Confidence: optional int (0–100), accept 0–1 float as %
    conf_raw = raw.get("confidence")
    if conf_raw is None or str(conf_raw).strip() == "":
        confidence: Optional[int] = None
    else:
        try:
            cf = float(conf_raw)
            # Allow 0–1 as fractional confidence → convert to percentage
            if 0.0 <= cf <= 1.0:
                cf = cf * 100.0
            confidence = int(round(cf))
        except Exception:
            confidence = None

        if confidence is not None:
            if confidence < 0:
                confidence = 0
            elif confidence > 100:
                confidence = 100

    # Kitchen name (optional, nullable)
    kitchen_name_raw = raw.get("kitchen_name")
    if kitchen_name_raw is None:
        kitchen_name: Optional[str] = None
    else:
        kitchen_name = str(kitchen_name_raw).strip() or None

    # Subcategory (optional, nullable) — Day 140: modifier grouping
    subcat_raw = raw.get("subcategory")
    if subcat_raw is None:
        subcategory: Optional[str] = None
    else:
        subcategory = str(subcat_raw).strip() or None

    return {
        "name": name,
        "description": description,
        "price_cents": price_cents,
        "category": category,
        "subcategory": subcategory,
        "position": position,
        "confidence": confidence,
        "kitchen_name": kitchen_name,
    }


def _clamp_price_cents(cents: Optional[int]) -> Optional[int]:
    """Clamp into sane menu range (default Day-22 guard: $1.00–$99.99)."""
    if cents is None:
        return None
    if cents < ocr_utils.PRICE_MIN or cents > ocr_utils.PRICE_MAX:
        return None
    return int(cents)


def _pick_price_from_ai_or_text(
    ai_price_candidates: List[Dict[str, Any]],
    name: str,
    desc: Optional[str],
) -> Optional[int]:
    """
    Choose a main price (in cents) from price_candidates and/or text.
    New canonical rule:
      - Gather all candidate prices from ai_price_candidates (value/price_cents).
      - Clamp each to sane range.
      - Pick the lowest remaining as canonical price.
      - If none, extract from text (name + description) using OCR regex.
    """
    candidate_prices: List[int] = []

    # 1) AI/OCR candidates: price_cents or value (dollar float)
    for c in ai_price_candidates or []:
        # a) Direct cents from OCR pipeline / helpers
        try:
            if "price_cents" in c and c.get("price_cents") is not None:
                cents_raw = c.get("price_cents")
                cents = _clamp_price_cents(int(round(float(cents_raw))))
                if cents is not None:
                    candidate_prices.append(cents)
        except Exception:
            pass

        # b) Legacy AI helper style: {'value': 12.99}
        try:
            v = c.get("value")
            if v is None:
                continue
            cents = _clamp_price_cents(int(round(float(v) * 100)))
            if cents is not None:
                candidate_prices.append(cents)
        except Exception:
            continue

    if candidate_prices:
        return min(candidate_prices)

    # 2) Text extraction (supports 8.99 / $12 / 899 etc.)
    text = f"{name} {(desc or '')}".strip()
    hits = ocr_utils.find_price_candidates(text)
    for c in hits:
        cents = _clamp_price_cents(int(c))
        if cents is not None:
            return cents
    return None


def _canonical_price_cents_for_preview_item(it: Dict[str, Any]) -> Optional[int]:
    """
    Canonical price chooser for new OCR preview items.

    Rule:
      - Gather all prices from:
          * variants[*].price_cents
          * price_candidates[*].price_cents or .value (float dollars)
      - Clamp each via _clamp_price_cents.
      - Take the lowest remaining as the canonical price.
      - If nothing survives, fall back to text extraction on name+description.
    """
    name_raw = (it.get("name") or "").strip()
    desc_raw = it.get("description") or None
    variants = it.get("variants") or []
    pcs = it.get("price_candidates") or []

    candidate_prices: List[int] = []

    # Variant prices: already in cents
    if isinstance(variants, list):
        for v in variants:
            try:
                pc = v.get("price_cents")
            except AttributeError:
                continue
            if pc is None:
                continue
            try:
                cents = _clamp_price_cents(int(round(float(pc))))
            except Exception:
                continue
            if cents is not None:
                candidate_prices.append(cents)

    # price_candidates: can be cents or dollars
    if isinstance(pcs, list):
        for c in pcs:
            # direct cents
            try:
                if "price_cents" in c and c.get("price_cents") is not None:
                    cents_raw = c.get("price_cents")
                    cents = _clamp_price_cents(int(round(float(cents_raw))))
                    if cents is not None:
                        candidate_prices.append(cents)
                        continue
            except Exception:
                pass

            # dollar float "value"
            try:
                v = c.get("value")
                if v is None:
                    continue
                cents = _clamp_price_cents(int(round(float(v) * 100)))
                if cents is not None:
                    candidate_prices.append(cents)
            except Exception:
                continue

    if candidate_prices:
        return min(candidate_prices)

    # Fallback: text-based extraction
    return _pick_price_from_ai_or_text(pcs, name_raw, desc_raw)


# ------------------------------------------------------------
# OCR Inspector debug sidecars
# ------------------------------------------------------------
def save_ocr_debug(draft_id: int, payload: Dict[str, Any]) -> None:
    """
    Persist a rich OCR debug payload to a sidecar JSON file:
      storage/.debug/drafts/<draft_id>.json

    Defensive:
      - atomic write (tmp + replace) to avoid partial files
      - default=str to tolerate Path / numpy types / odd objects
    """
    p = _debug_path(int(draft_id))
    p.parent.mkdir(parents=True, exist_ok=True)

    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload or {}, f, ensure_ascii=False, indent=2, default=str)
    tmp.replace(p)


def load_ocr_debug(draft_id: int) -> Optional[Dict[str, Any]]:
    """
    Load the OCR debug payload if present.
    Returns None if missing/unreadable/non-dict.
    """
    p = _debug_path(int(draft_id))
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if not isinstance(obj, dict):
            return None
        return obj
    except Exception:
        return None


# ------------------------------------------------------------
# Public API consumed by portal/app.py
# ------------------------------------------------------------
def list_drafts(
    *,
    status: Optional[str] = None,
    restaurant_id: Optional[int] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[Dict[str, Any]]:
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
        row = conn.execute(
            "SELECT * FROM drafts WHERE id=?", (int(draft_id),)
        ).fetchone()
        return _row_to_dict(row) if row else None


def _get_draft_items_nested(draft_id: int) -> List[Dict[str, Any]]:
    """
    Internal helper: returns items with full modifier-group nesting.

    Each item dict contains:
      modifier_groups: list of groups, each with a 'modifiers' list
      ungrouped_variants: variants whose modifier_group_id IS NULL
    """
    with db_connect() as conn:
        # --- pass 1: items + modifier groups + grouped variants ---
        rows = conn.execute(
            """
            SELECT
                di.id            AS item_id,
                di.draft_id,
                di.name,
                di.description,
                di.price_cents,
                di.category,
                di.subcategory,
                di.position,
                di.confidence,
                di.kitchen_name,
                di.created_at,
                di.updated_at,
                mg.id            AS mg_id,
                mg.name          AS mg_name,
                mg.required      AS mg_required,
                mg.min_select    AS mg_min_select,
                mg.max_select    AS mg_max_select,
                mg.position      AS mg_position,
                v.id             AS v_id,
                v.label          AS v_label,
                v.price_cents    AS v_price_cents,
                v.kind           AS v_kind,
                v.position       AS v_position
            FROM draft_items di
            LEFT JOIN draft_modifier_groups mg ON mg.item_id = di.id
            LEFT JOIN draft_item_variants v
                ON v.item_id = di.id AND v.modifier_group_id = mg.id
            WHERE di.draft_id = ?
            ORDER BY
                COALESCE(di.position, 1000000000), di.id,
                COALESCE(mg.position, 1000000000), mg.id,
                COALESCE(v.position,  1000000000), v.id
            """,
            (draft_id,),
        ).fetchall()

        # --- pass 2: ungrouped variants ---
        ungrouped_rows = conn.execute(
            """
            SELECT v.id, v.item_id, v.label, v.price_cents, v.kind,
                   v.position, v.modifier_group_id,
                   v.created_at, v.updated_at
            FROM draft_item_variants v
            JOIN draft_items di ON di.id = v.item_id
            WHERE di.draft_id = ? AND v.modifier_group_id IS NULL
            ORDER BY COALESCE(v.position, 1000000000), v.id
            """,
            (draft_id,),
        ).fetchall()

    items_map: Dict[int, Dict[str, Any]] = {}
    ordered_ids: List[int] = []

    for row in rows:
        iid = row["item_id"]
        if iid not in items_map:
            items_map[iid] = {
                "id": iid,
                "draft_id": row["draft_id"],
                "name": row["name"],
                "description": row["description"],
                "price_cents": row["price_cents"],
                "category": row["category"],
                "subcategory": row["subcategory"],
                "position": row["position"],
                "confidence": row["confidence"],
                "kitchen_name": row["kitchen_name"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "modifier_groups": [],
                "ungrouped_variants": [],
                "_groups_map": {},
            }
            ordered_ids.append(iid)

        item = items_map[iid]

        if row["mg_id"] is not None:
            mg_id = row["mg_id"]
            if mg_id not in item["_groups_map"]:
                group: Dict[str, Any] = {
                    "id": mg_id,
                    "name": row["mg_name"],
                    "required": row["mg_required"],
                    "min_select": row["mg_min_select"],
                    "max_select": row["mg_max_select"],
                    "position": row["mg_position"],
                    "modifiers": [],
                }
                item["modifier_groups"].append(group)
                item["_groups_map"][mg_id] = group

            if row["v_id"] is not None:
                item["_groups_map"][mg_id]["modifiers"].append(
                    {
                        "id": row["v_id"],
                        "label": row["v_label"],
                        "price_cents": row["v_price_cents"],
                        "kind": row["v_kind"],
                        "position": row["v_position"],
                    }
                )

    # attach ungrouped variants
    for row in ungrouped_rows:
        iid = row["item_id"]
        if iid in items_map:
            items_map[iid]["ungrouped_variants"].append(
                {
                    "id": row["id"],
                    "item_id": iid,
                    "label": row["label"],
                    "price_cents": row["price_cents"],
                    "kind": row["kind"],
                    "position": row["position"],
                    "modifier_group_id": None,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )

    # strip internal map; alias ungrouped_variants → variants for template compat
    for item in items_map.values():
        del item["_groups_map"]
        item["variants"] = item["ungrouped_variants"]

    return [items_map[iid] for iid in ordered_ids]


def get_draft_items(
    draft_id: int,
    *,
    include_variants: bool = True,
    include_modifier_groups: bool = False,
) -> List[Dict[str, Any]]:
    """
    Fetch all items for a draft, ordered by position then id.

    include_variants=True (default): each item gets a flat 'variants' list.
    include_modifier_groups=True: each item gets 'modifier_groups' (nested
      structure: modifier_groups[].modifiers[]) + 'ungrouped_variants' for
      any variants not attached to a group.  Mutually exclusive with the
      flat 'variants' key.
    """
    if include_modifier_groups:
        return _get_draft_items_nested(int(draft_id))

    with db_connect() as conn:
        if not include_variants:
            rows = conn.execute(
                "SELECT * FROM draft_items WHERE draft_id=? "
                "ORDER BY COALESCE(position, 1000000000), id",
                (int(draft_id),),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

        # LEFT JOIN: one query to get items + their variants
        rows = conn.execute(
            """
            SELECT
                di.*,
                v.id                AS v_id,
                v.label             AS v_label,
                v.price_cents       AS v_price_cents,
                v.kind              AS v_kind,
                v.position          AS v_position,
                v.modifier_group_id AS v_modifier_group_id,
                v.created_at        AS v_created_at,
                v.updated_at        AS v_updated_at
            FROM draft_items di
            LEFT JOIN draft_item_variants v ON v.item_id = di.id
            WHERE di.draft_id = ?
            ORDER BY COALESCE(di.position, 1000000000), di.id,
                     COALESCE(v.position, 1000000000), v.id
            """,
            (int(draft_id),),
        ).fetchall()

    # Group by item id
    items_map: Dict[int, Dict[str, Any]] = {}
    ordered_ids: List[int] = []

    for row in rows:
        item_id = row["id"]
        if item_id not in items_map:
            item = {
                "id": row["id"],
                "draft_id": row["draft_id"],
                "name": row["name"],
                "description": row["description"],
                "price_cents": row["price_cents"],
                "category": row["category"],
                "subcategory": row["subcategory"],
                "position": row["position"],
                "confidence": row["confidence"],
                "kitchen_name": row["kitchen_name"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "variants": [],
            }
            items_map[item_id] = item
            ordered_ids.append(item_id)

        # Attach variant if present (LEFT JOIN may produce NULL v_id)
        if row["v_id"] is not None:
            items_map[item_id]["variants"].append(
                {
                    "id": row["v_id"],
                    "item_id": item_id,
                    "label": row["v_label"],
                    "price_cents": row["v_price_cents"],
                    "kind": row["v_kind"],
                    "position": row["v_position"],
                    "modifier_group_id": row["v_modifier_group_id"],
                    "created_at": row["v_created_at"],
                    "updated_at": row["v_updated_at"],
                }
            )

    return [items_map[iid] for iid in ordered_ids]


def save_draft_metadata(
    draft_id: int,
    *,
    title: Optional[str] = None,
    restaurant_id: Optional[int] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    source_job_id: Optional[int] = None,
    menu_id: Optional[int] = None,
) -> None:
    sets: List[str] = []
    args: List[Any] = []

    if title is not None:
        sets.append("title=?")
        args.append(title)
    if restaurant_id is not None:
        sets.append("restaurant_id=?")
        args.append(int(restaurant_id))
    if status is not None:
        sets.append("status=?")
        args.append(status)
    if source is not None:
        # store as-is (caller may pass JSON string)
        sets.append("source=?")
        args.append(source)
    if source_job_id is not None:
        sets.append("source_job_id=?")
        args.append(int(source_job_id))
    if menu_id is not None:
        sets.append("menu_id=?")
        args.append(int(menu_id))

    if not sets:
        return
    sets.append("updated_at=?")
    args.append(_now())
    args.append(int(draft_id))

    with db_connect() as conn:
        conn.execute(f"UPDATE drafts SET {', '.join(sets)} WHERE id=?", args)
        conn.commit()


def submit_draft(draft_id: int) -> None:
    save_draft_metadata(int(draft_id), status="submitted")


def approve_publish(draft_id: int) -> None:
    """Mark draft as published (used by /drafts/<id>/publish_now)."""
    save_draft_metadata(int(draft_id), status="published")


def approve_draft(draft_id: int) -> None:
    """Mark draft as approved (owner reviewed and approved for POS export)."""
    save_draft_metadata(int(draft_id), status="approved")


# ------------------------------------------------------------
# Category order (Day 116)
# ------------------------------------------------------------
def save_category_order(draft_id: int, categories: List[str]) -> None:
    """Persist the user-defined category display order for a draft."""
    import json as _json
    encoded = _json.dumps([str(c) for c in categories])
    with db_connect() as conn:
        conn.execute(
            "UPDATE drafts SET category_order=?, updated_at=? WHERE id=?",
            (encoded, _now(), int(draft_id)),
        )
        conn.commit()


def get_category_order(draft_id: int) -> List[str]:
    """Return the stored category order for a draft, or [] if none saved."""
    import json as _json
    with db_connect() as conn:
        row = conn.execute(
            "SELECT category_order FROM drafts WHERE id=?", (int(draft_id),)
        ).fetchone()
    if not row or not row["category_order"]:
        return []
    try:
        result = _json.loads(row["category_order"])
        return result if isinstance(result, list) else []
    except (ValueError, TypeError):
        return []


def record_export(
    draft_id: int,
    fmt: str,
    item_count: int = 0,
    variant_count: int = 0,
    warning_count: int = 0,
) -> int:
    """Record an export event in draft_export_history. Returns the new row id."""
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO draft_export_history "
            "(draft_id, format, item_count, variant_count, warning_count, exported_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (int(draft_id), fmt, int(item_count), int(variant_count),
             int(warning_count), _now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_export_history(draft_id: int) -> List[Dict[str, Any]]:
    """Return export history records for a draft, newest first."""
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT id, draft_id, format, item_count, variant_count, "
            "warning_count, exported_at "
            "FROM draft_export_history WHERE draft_id=? ORDER BY id DESC",
            (int(draft_id),),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ------------------------------------------------------------
# API Key Management (Day 84 — REST API authentication)
# ------------------------------------------------------------

def create_api_key(
    label: str = "",
    restaurant_id: Optional[int] = None,
    rate_limit_rpm: int = 60,
) -> Dict[str, Any]:
    """Create a new API key. Returns dict with 'raw_key' (only time visible)."""
    raw_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO api_keys "
            "(key_hash, restaurant_id, label, active, rate_limit_rpm, created_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (key_hash, restaurant_id, (label or "").strip(),
             int(rate_limit_rpm), _now()),
        )
        conn.commit()
        return {
            "id": int(cur.lastrowid),
            "raw_key": raw_key,
            "label": (label or "").strip(),
            "restaurant_id": restaurant_id,
            "rate_limit_rpm": int(rate_limit_rpm),
        }


def validate_api_key(raw_key: str) -> Optional[Dict[str, Any]]:
    """Hash the raw key and look it up. Returns key record dict or None."""
    if not raw_key or not isinstance(raw_key, str):
        return None
    key_hash = hashlib.sha256(raw_key.strip().encode()).hexdigest()
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash=?", (key_hash,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def revoke_api_key(key_id: int) -> bool:
    """Deactivate an API key. Returns True if a row was updated."""
    with db_connect() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET active=0 WHERE id=?", (int(key_id),)
        )
        conn.commit()
        return cur.rowcount > 0


# ------------------------------------------------------------
# Webhook Management (Day 85 — notification callbacks)
# ------------------------------------------------------------

VALID_WEBHOOK_EVENTS = {"draft.approved", "draft.exported"}


def register_webhook(
    url: str,
    event_types: List[str],
    restaurant_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Register a new webhook. Returns dict with 'secret' (only time visible)."""
    secret = secrets.token_urlsafe(32)
    valid = [e for e in event_types if e in VALID_WEBHOOK_EVENTS]
    if not valid:
        raise ValueError(
            f"No valid event types. Must be one of: {sorted(VALID_WEBHOOK_EVENTS)}"
        )
    event_str = ",".join(sorted(valid))
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO webhooks "
            "(restaurant_id, url, event_types, secret, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (restaurant_id, url.strip(), event_str, secret, _now(), _now()),
        )
        conn.commit()
        return {
            "id": int(cur.lastrowid),
            "url": url.strip(),
            "event_types": valid,
            "secret": secret,
            "restaurant_id": restaurant_id,
            "active": 1,
        }


def list_webhooks(restaurant_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """List all active webhooks, optionally filtered by restaurant_id."""
    with db_connect() as conn:
        if restaurant_id is not None:
            rows = conn.execute(
                "SELECT * FROM webhooks WHERE active=1 AND restaurant_id=? ORDER BY id",
                (int(restaurant_id),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM webhooks WHERE active=1 ORDER BY id"
            ).fetchall()
        result = []
        for r in rows:
            d = _row_to_dict(r)
            d["event_types"] = [
                e.strip() for e in (d.get("event_types") or "").split(",") if e.strip()
            ]
            d.pop("secret", None)
            result.append(d)
        return result


def get_webhook(webhook_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single webhook by ID. Returns dict or None."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM webhooks WHERE id=?", (int(webhook_id),)
        ).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        d["event_types"] = [
            e.strip() for e in (d.get("event_types") or "").split(",") if e.strip()
        ]
        return d


def delete_webhook(webhook_id: int) -> bool:
    """Hard-delete a webhook. Returns True if a row was deleted."""
    with db_connect() as conn:
        cur = conn.execute(
            "DELETE FROM webhooks WHERE id=?", (int(webhook_id),)
        )
        conn.commit()
        return cur.rowcount > 0


def get_webhooks_for_event(
    restaurant_id: Optional[int], event: str
) -> List[Dict[str, Any]]:
    """Get active webhooks matching a specific event for a restaurant.

    Returns webhooks where restaurant_id matches OR is NULL (global).
    """
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM webhooks WHERE active=1 ORDER BY id"
        ).fetchall()
        matched = []
        for r in rows:
            d = _row_to_dict(r)
            events = [
                e.strip() for e in (d.get("event_types") or "").split(",") if e.strip()
            ]
            if event not in events:
                continue
            wh_rid = d.get("restaurant_id")
            if wh_rid is None or (restaurant_id is not None and wh_rid == restaurant_id):
                d["event_types"] = events
                matched.append(d)
        return matched


def fire_webhooks(
    restaurant_id: Optional[int],
    event: str,
    payload: Dict[str, Any],
) -> int:
    """Fire webhooks for a given event. Returns count dispatched.

    Sends POST requests in daemon threads (fire-and-forget).
    Each request includes X-Webhook-Event and X-Webhook-Signature headers.
    """
    hooks = get_webhooks_for_event(restaurant_id, event)
    if not hooks:
        return 0

    body = json.dumps(payload, default=str)

    def _send(hook: Dict[str, Any]) -> None:
        try:
            secret = hook.get("secret", "")
            signature = hmac.new(
                secret.encode(), body.encode(), hashlib.sha256
            ).hexdigest()
            req = urllib.request.Request(
                hook["url"],
                data=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Event": event,
                    "X-Webhook-Signature": signature,
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=15)
        except Exception:
            pass  # fire-and-forget

    for hook in hooks:
        t = threading.Thread(target=_send, args=(hook,), daemon=True)
        t.start()

    return len(hooks)


def _insert_draft(
    *,
    title: Optional[str],
    restaurant_id: Optional[int],
    status: str = "editing",
    source: Optional[str] = None,
    source_job_id: Optional[int] = None,
    source_file_path: Optional[str] = None,
    menu_id: Optional[int] = None,
) -> int:
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO drafts (
                title,
                restaurant_id,
                status,
                source,
                source_job_id,
                source_file_path,
                menu_id,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                restaurant_id,
                status,
                source,
                source_job_id,
                source_file_path,
                menu_id,
                _now(),
                _now(),
            ),
        )

        conn.commit()
        return int(cur.lastrowid)


def _insert_modifier_groups_with_cursor(
    cur: Any,
    item_id: int,
    raw_groups: List[Dict[str, Any]],
    replace: bool = False,
) -> None:
    """Insert _modifier_groups for an item using an existing cursor.

    Each group dict must have: name, required, min_select, max_select,
    position, and _modifiers list.

    If replace=True, deletes existing groups+variants for the item first.
    Variants in each group are inserted into draft_item_variants with
    modifier_group_id set to the newly created group row.
    """
    if replace:
        # Nullify variants that reference our groups so cascade doesn't
        # remove them; then delete the groups themselves.
        cur.execute(
            "UPDATE draft_item_variants SET modifier_group_id=NULL "
            "WHERE item_id=? AND modifier_group_id IS NOT NULL",
            (item_id,),
        )
        cur.execute(
            "DELETE FROM draft_modifier_groups WHERE item_id=?",
            (item_id,),
        )

    for g in raw_groups:
        if not isinstance(g, dict):
            continue
        gname = (g.get("name") or "").strip()
        if not gname:
            continue
        try:
            required = 1 if g.get("required") else 0
            min_select = int(g.get("min_select") or 0)
            max_select = int(g.get("max_select") or 0)
            gpos = int(g.get("position") or 0)
        except (ValueError, TypeError):
            required, min_select, max_select, gpos = 0, 0, 0, 0

        cur.execute(
            """
            INSERT INTO draft_modifier_groups
                (item_id, name, required, min_select, max_select, position,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item_id, gname, required, min_select, max_select, gpos,
             _now(), _now()),
        )
        group_id = int(cur.lastrowid)

        for m in (g.get("_modifiers") or []):
            if not isinstance(m, dict):
                continue
            vnorm = _normalize_variant_for_db(m)
            if not vnorm:
                continue
            cur.execute(
                """
                INSERT INTO draft_item_variants
                    (item_id, label, price_cents, kind, position,
                     modifier_group_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    vnorm["label"],
                    vnorm["price_cents"],
                    vnorm["kind"],
                    vnorm["position"],
                    group_id,
                    _now(),
                    _now(),
                ),
            )


def _insert_items_bulk(
    draft_id: int, items: Iterable[Dict[str, Any]]
) -> List[int]:
    ids: List[int] = []
    with db_connect() as conn:
        cur = conn.cursor()
        for it in items:
            norm = _normalize_item_for_db(it)
            if not norm:
                # Skip rows with no valid name or totally malformed payloads
                continue

            name = norm["name"]
            desc = norm["description"]
            price_cents = norm["price_cents"]
            category = norm["category"]
            subcategory = norm["subcategory"]
            position = norm["position"]
            confidence = norm["confidence"]
            kitchen_name = norm["kitchen_name"]

            cur.execute(
                """
                INSERT INTO draft_items (
                    draft_id,
                    name,
                    description,
                    price_cents,
                    category,
                    subcategory,
                    position,
                    confidence,
                    kitchen_name,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(draft_id),
                    name,
                    desc,
                    price_cents,
                    category,
                    subcategory,
                    position,
                    confidence,
                    kitchen_name,
                    _now(),
                    _now(),
                ),
            )
            item_id = int(cur.lastrowid)
            ids.append(item_id)

            # Day 72: insert child variant rows if present
            raw_variants = it.get("_variants") or []
            for v in raw_variants:
                vnorm = _normalize_variant_for_db(v)
                if not vnorm:
                    continue
                cur.execute(
                    """
                    INSERT INTO draft_item_variants
                        (item_id, label, price_cents, kind, position,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        vnorm["label"],
                        vnorm["price_cents"],
                        vnorm["kind"],
                        vnorm["position"],
                        _now(),
                        _now(),
                    ),
                )

            # Day 112: insert modifier groups if present
            raw_groups = it.get("_modifier_groups") or []
            if raw_groups:
                _insert_modifier_groups_with_cursor(cur, item_id, raw_groups)

        conn.commit()
    return ids


def upsert_draft_items(
    draft_id: int, items: Iterable[Dict[str, Any]]
) -> Dict[str, List[int]]:
    """
    Upsert by presence of 'id':
      - If id is a valid integer -> UPDATE that item
      - Else -> INSERT new item
    Returns: {"inserted_ids":[...], "updated_ids":[...]}

    Day 72: If an item dict contains '_variants' (list of variant dicts),
    those are inserted as child rows in draft_item_variants.  For updates,
    existing variants are replaced (delete-all + re-insert).
    """
    inserted: List[int] = []
    updated: List[int] = []
    with db_connect() as conn:
        cur = conn.cursor()
        for it in items:
            # Defensive: non-dicts are ignored
            if not isinstance(it, dict):
                continue

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

            norm = _normalize_item_for_db(it)
            if not norm:
                # Skip nameless or completely invalid rows
                continue

            name = norm["name"]
            desc = norm["description"]
            price_cents = norm["price_cents"]
            category = norm["category"]
            subcategory = norm["subcategory"]
            position = norm["position"]
            confidence = norm["confidence"]
            kitchen_name = norm["kitchen_name"]

            effective_id: Optional[int] = None

            if has_int_id:
                # Use COALESCE so partial updates (e.g. wizard item save with no
                # position field) don't blow away existing values. Without this,
                # editing any item nulls its position and sends it to the bottom.
                cur.execute(
                    """
                    UPDATE draft_items
                    SET name=?,
                        description=?,
                        price_cents=?,
                        category=COALESCE(?, category),
                        subcategory=COALESCE(?, subcategory),
                        position=COALESCE(?, position),
                        confidence=COALESCE(?, confidence),
                        kitchen_name=COALESCE(?, kitchen_name),
                        updated_at=?
                    WHERE id=? AND draft_id=?
                    """,
                    (
                        name,
                        desc,
                        price_cents,
                        category,
                        subcategory,
                        position,
                        confidence,
                        kitchen_name,
                        _now(),
                        item_id,
                        int(draft_id),
                    ),
                )
                if cur.rowcount > 0:
                    updated.append(item_id)
                    effective_id = item_id
                else:
                    # if the id doesn't belong to this draft, insert instead (safety)
                    cur.execute(
                        """
                        INSERT INTO draft_items (
                            draft_id,
                            name,
                            description,
                            price_cents,
                            category,
                            subcategory,
                            position,
                            confidence,
                            kitchen_name,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(draft_id),
                            name,
                            desc,
                            price_cents,
                            category,
                            subcategory,
                            position,
                            confidence,
                            kitchen_name,
                            _now(),
                            _now(),
                        ),
                    )
                    effective_id = int(cur.lastrowid)
                    inserted.append(effective_id)
            else:
                cur.execute(
                    """
                    INSERT INTO draft_items (
                        draft_id,
                        name,
                        description,
                        price_cents,
                        category,
                        subcategory,
                        position,
                        confidence,
                        kitchen_name,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(draft_id),
                        name,
                        desc,
                        price_cents,
                        category,
                        subcategory,
                        position,
                        confidence,
                        kitchen_name,
                        _now(),
                        _now(),
                    ),
                )
                effective_id = int(cur.lastrowid)
                inserted.append(effective_id)

            # Day 72: insert child variant rows if present
            raw_variants = it.get("_variants") or []
            if raw_variants and effective_id is not None:
                # For updates, replace existing variants
                if has_int_id and item_id in updated:
                    cur.execute(
                        "DELETE FROM draft_item_variants WHERE item_id=?",
                        (effective_id,),
                    )
                for v in raw_variants:
                    vnorm = _normalize_variant_for_db(v)
                    if not vnorm:
                        continue
                    cur.execute(
                        """
                        INSERT INTO draft_item_variants
                            (item_id, label, price_cents, kind, position,
                             created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            effective_id,
                            vnorm["label"],
                            vnorm["price_cents"],
                            vnorm["kind"],
                            vnorm["position"],
                            _now(),
                            _now(),
                        ),
                    )

            # Day 112: insert/replace modifier groups if present
            raw_groups = it.get("_modifier_groups") or []
            if raw_groups and effective_id is not None:
                is_update = has_int_id and item_id in updated
                _insert_modifier_groups_with_cursor(
                    cur, effective_id, raw_groups, replace=is_update
                )

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
            (int(draft_id), *ids),
        )
        conn.commit()
        return int(cur.rowcount)


# ------------------------------------------------------------
# Variant CRUD (Phase 9 — structured variant storage)
# ------------------------------------------------------------
def _normalize_variant_for_db(raw: Any) -> Optional[Dict[str, Any]]:
    """
    Defensive normalizer for draft_item_variants payloads.

    Ensures:
      - label: non-empty string (required; rows without a label are dropped)
      - price_cents: int, never negative
      - kind: one of size/combo/flavor/style/other (default: size)
      - position: int (default: 0)
    """
    if not isinstance(raw, dict):
        return None

    label_raw = raw.get("label")
    label = str(label_raw).strip() if label_raw is not None else ""
    if not label:
        return None

    price_raw = raw.get("price_cents")
    try:
        price_cents = int(price_raw)
    except Exception:
        price_cents = 0
    if price_cents < 0:
        price_cents = 0

    _VALID_KINDS = {"size", "combo", "flavor", "style", "other"}
    kind_raw = raw.get("kind")
    kind = str(kind_raw).strip().lower() if kind_raw else "size"
    if kind not in _VALID_KINDS:
        kind = "other"

    pos_raw = raw.get("position")
    try:
        position = int(pos_raw) if pos_raw is not None else 0
    except Exception:
        position = 0

    return {
        "label": label,
        "price_cents": price_cents,
        "kind": kind,
        "position": position,
    }


def insert_variants(
    item_id: int, variants: Iterable[Dict[str, Any]]
) -> List[int]:
    """
    Bulk-insert variant rows for a given item.
    Returns list of inserted variant IDs.
    Silently skips invalid rows (no label).
    """
    ids: List[int] = []
    with db_connect() as conn:
        cur = conn.cursor()
        for v in variants:
            norm = _normalize_variant_for_db(v)
            if not norm:
                continue
            cur.execute(
                """
                INSERT INTO draft_item_variants
                    (item_id, label, price_cents, kind, position,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(item_id),
                    norm["label"],
                    norm["price_cents"],
                    norm["kind"],
                    norm["position"],
                    _now(),
                    _now(),
                ),
            )
            ids.append(int(cur.lastrowid))
        conn.commit()
    return ids


def update_variant(variant_id: int, data: Dict[str, Any]) -> bool:
    """
    Partial update of a single variant row.
    Only updates fields present in data (label, price_cents, kind, position).
    Returns True if a row was updated, False otherwise.
    """
    sets: List[str] = []
    args: List[Any] = []

    if "label" in data:
        label = str(data["label"]).strip()
        if not label:
            return False
        sets.append("label=?")
        args.append(label)

    if "price_cents" in data:
        try:
            pc = int(data["price_cents"])
        except Exception:
            pc = 0
        if pc < 0:
            pc = 0
        sets.append("price_cents=?")
        args.append(pc)

    if "kind" in data:
        _VALID_KINDS = {"size", "combo", "flavor", "style", "other"}
        k = str(data["kind"]).strip().lower()
        if k not in _VALID_KINDS:
            k = "other"
        sets.append("kind=?")
        args.append(k)

    if "position" in data:
        try:
            pos = int(data["position"])
        except Exception:
            pos = 0
        sets.append("position=?")
        args.append(pos)

    if not sets:
        return False

    sets.append("updated_at=?")
    args.append(_now())
    args.append(int(variant_id))

    with db_connect() as conn:
        cur = conn.execute(
            f"UPDATE draft_item_variants SET {', '.join(sets)} WHERE id=?",
            args,
        )
        conn.commit()
        return cur.rowcount > 0


def delete_variants(item_id: int, variant_ids: Iterable[int]) -> int:
    """
    Delete specific variant rows for a given item.
    Returns count of deleted rows.
    """
    ids = [int(i) for i in variant_ids if str(i).isdigit()]
    if not ids:
        return 0
    with db_connect() as conn:
        qmarks = ",".join(["?"] * len(ids))
        cur = conn.execute(
            f"DELETE FROM draft_item_variants WHERE item_id=? AND id IN ({qmarks})",
            (int(item_id), *ids),
        )
        conn.commit()
        return int(cur.rowcount)


def delete_variants_by_id(variant_ids: Iterable[int]) -> int:
    """Delete variant rows by their primary key IDs (no item_id needed)."""
    ids = [int(i) for i in variant_ids if str(i).isdigit()]
    if not ids:
        return 0
    with db_connect() as conn:
        qmarks = ",".join(["?"] * len(ids))
        cur = conn.execute(
            f"DELETE FROM draft_item_variants WHERE id IN ({qmarks})",
            ids,
        )
        conn.commit()
        return int(cur.rowcount)


def delete_all_variants_for_item(item_id: int) -> int:
    """Delete all variant rows for a given item. Returns count deleted."""
    with db_connect() as conn:
        cur = conn.execute(
            "DELETE FROM draft_item_variants WHERE item_id=?",
            (int(item_id),),
        )
        conn.commit()
        return int(cur.rowcount)


def get_item_variants(item_id: int) -> List[Dict[str, Any]]:
    """Fetch all variants for a single item, ordered by position then id."""
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM draft_item_variants WHERE item_id=? "
            "ORDER BY position, id",
            (int(item_id),),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ------------------------------------------------------------
# Item Coordinates (Day 139 — bounding boxes for wizard highlighting)
# ------------------------------------------------------------
def store_item_coordinates(
    item_id: int,
    x_pct: float,
    y_pct: float,
    w_pct: float,
    h_pct: float,
    page: int = 1,
    element_type: str = "item",
) -> int:
    """Store a bounding box coordinate for a draft item."""
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO draft_item_coordinates
                (item_id, x_pct, y_pct, w_pct, h_pct, page, element_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(item_id), x_pct, y_pct, w_pct, h_pct, page, element_type, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def store_item_coordinates_bulk(
    coords: List[Dict[str, Any]],
) -> int:
    """Bulk-insert bounding box coordinates.

    Each dict: {item_id, x_pct, y_pct, w_pct, h_pct, page?, element_type?}
    Returns number of rows inserted.
    """
    if not coords:
        return 0
    now = _now()
    with db_connect() as conn:
        cur = conn.cursor()
        rows = []
        for c in coords:
            item_id = c.get("item_id")
            if item_id is None:
                continue
            rows.append((
                int(item_id),
                float(c.get("x_pct", 0)),
                float(c.get("y_pct", 0)),
                float(c.get("w_pct", 0)),
                float(c.get("h_pct", 0)),
                int(c.get("page", 1)),
                c.get("element_type", "item"),
                now,
            ))
        if rows:
            cur.executemany(
                """
                INSERT INTO draft_item_coordinates
                    (item_id, x_pct, y_pct, w_pct, h_pct, page, element_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        return len(rows)


def get_item_coordinates(item_id: int) -> Optional[Dict[str, Any]]:
    """Get bounding box for a single item."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM draft_item_coordinates WHERE item_id=? LIMIT 1",
            (int(item_id),),
        ).fetchone()
        return _row_to_dict(row) if row else None


def get_draft_coordinates(draft_id: int) -> List[Dict[str, Any]]:
    """Get all bounding boxes for items in a draft."""
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT c.* FROM draft_item_coordinates c
            JOIN draft_items i ON c.item_id = i.id
            WHERE i.draft_id = ?
            ORDER BY c.page, c.y_pct
            """,
            (int(draft_id),),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def save_source_elements(draft_id: int, elements_json: str) -> None:
    """Store raw classified elements JSON on the draft."""
    with db_connect() as conn:
        conn.execute(
            "UPDATE drafts SET source_elements=?, updated_at=? WHERE id=?",
            (elements_json, _now(), int(draft_id)),
        )
        conn.commit()


def get_source_elements(draft_id: int) -> Optional[List[Dict[str, Any]]]:
    """Retrieve raw classified elements from the draft."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT source_elements FROM drafts WHERE id=?",
            (int(draft_id),),
        ).fetchone()
        if row and row["source_elements"]:
            try:
                return json.loads(row["source_elements"])
            except Exception:
                return None
        return None


def save_gap_warnings(draft_id: int, warnings_json: str) -> None:
    """Store Call 2 gap warnings JSON on the draft (Day 139.5)."""
    with db_connect() as conn:
        conn.execute(
            "UPDATE drafts SET gap_warnings=?, updated_at=? WHERE id=?",
            (warnings_json, _now(), int(draft_id)),
        )
        conn.commit()


def get_gap_warnings(draft_id: int) -> Optional[List[Dict[str, Any]]]:
    """Retrieve Call 2 gap warnings from the draft."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT gap_warnings FROM drafts WHERE id=?",
            (int(draft_id),),
        ).fetchone()
        if row and row.get("gap_warnings"):
            try:
                return json.loads(row["gap_warnings"])
            except Exception:
                return None
        return None


# ------------------------------------------------------------
# Import bridge (legacy JSON → DB-first draft) + AI bridge
# ------------------------------------------------------------
def find_draft_by_source_job(job_id: int) -> Optional[Dict[str, Any]]:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM drafts WHERE source_job_id=? "
            "ORDER BY id DESC LIMIT 1",
            (int(job_id),),
        ).fetchone()
        return _row_to_dict(row) if row else None


def _flat_from_legacy_categories(
    draft_json: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Legacy path: explode categories/items/sizes to flat rows.
    """
    flat_items: List[Dict[str, Any]] = []
    for cat in draft_json.get("categories") or []:
        cat_name = (cat.get("name") or "").strip() or None
        for item in cat.get("items") or []:
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
                    flat_items.append(
                        {
                            "name": name,
                            "description": desc,
                            "price_cents": cents,
                            "category": cat_name,
                            "position": None,
                            "confidence": confidence,
                        }
                    )
            else:
                cents = _to_cents(item.get("price", 0))
                flat_items.append(
                    {
                        "name": base,
                        "description": desc,
                        "price_cents": cents,
                        "category": cat_name,
                        "position": None,
                        "confidence": confidence,
                    }
                )
    return flat_items


def _flat_from_ai_items(ai_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    AI path: map ai_ocr_helper / OCR pipeline preview items → flat rows.

    New Day-32 behavior:
      - Canonical price:
            * Prefer variants[*].price_cents + price_candidates[*].(price_cents|value)
              -> clamp all, choose lowest as main price.
            * If none survives, fall back to text extraction via OCR regex.
      - Category:
            * If subcategory present → use as category column.
            * Else fall back to top-level category.
      - Confidence:
            * Map float 0–1 to 0–100; tolerate integer % as-is.
      - Garbage guard:
            * Drop items whose NAME fails is_garbage_line unless a valid price was found.

    Day 72: Each item may include a '_variants' key with structured variant
    data that _insert_items_bulk() / upsert_draft_items() will insert into
    draft_item_variants.
    """
    flat: List[Dict[str, Any]] = []

    for it in ai_items or []:
        name_raw = (it.get("name") or "").strip()
        if not name_raw:
            continue

        desc_raw = it.get("description") or None

        # Category: favor subcategory when present
        subcat = (it.get("subcategory") or "").strip() or None
        cat_top = (it.get("category") or "").strip() or None
        cat = subcat or cat_top

        # Canonical price
        cents_main = _canonical_price_cents_for_preview_item(it)
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

        # Build structured variants from AI preview variant data
        raw_variants = it.get("variants") or []
        variants: List[Dict[str, Any]] = []
        for vi, v in enumerate(raw_variants):
            if not isinstance(v, dict):
                continue
            lbl = (v.get("label") or v.get("normalized_size") or "").strip()
            vpc = v.get("price_cents")
            if vpc is None:
                continue
            try:
                vpc = int(vpc)
            except Exception:
                continue
            kind = (v.get("kind") or "size").strip().lower()
            if kind not in ("size", "combo", "flavor", "style", "other"):
                kind = "size"
            if lbl or vpc > 0:
                variants.append({
                    "label": lbl or f"Option {vi + 1}",
                    "price_cents": vpc,
                    "kind": kind,
                    "position": vi,
                })

        row: Dict[str, Any] = {
            "name": name_raw,
            "description": (desc_raw or "").strip() or None,
            "price_cents": price_cents,
            "category": cat,
            "position": None,
            "confidence": confidence,
        }
        if variants:
            row["_variants"] = variants
        flat.append(row)

    return flat


def create_draft_from_import(
    draft_json: Dict[str, Any], *, import_job_id: int
) -> Dict[str, Any]:
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

    # Look up the original upload so we can store a debug path
    job = get_import_job(import_job_id)
    source_file_path: Optional[str] = None
    if job:
        # Prefer source_path (full relative path in uploads) and fall back to filename
        source_file_path = job.get("source_path") or job.get("filename")

    # Create draft shell
    draft_id = _insert_draft(
        title=title,
        restaurant_id=None,
        status="editing",
        source=source_blob,
        source_job_id=int(import_job_id),
        source_file_path=source_file_path,
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
            save_ocr_debug(
                draft_id,
                {
                    "bridge": "ai",
                    "import_job_id": import_job_id,
                    "ai_items_count": len(ai_items or []),
                    "items": ai_items,  # full OCR preview payload for Phase 5+
                    "source_meta": draft_json.get("source") or {},
                    "guards": {
                        "dropped_by_is_garbage_line": True,
                        "price_clamp_range": [
                            ocr_utils.PRICE_MIN,
                            ocr_utils.PRICE_MAX,
                        ],
                        "canonical_price_rule": "min(variants + price_candidates) or first text hit",
                    },
                },
            )
        except Exception:
            pass
        return {"id": draft_id, "draft_id": draft_id}

        # ----------- Fallback: legacy categories path -----------
    flat_items = _flat_from_legacy_categories(draft_json)
    _insert_items_bulk(draft_id, flat_items)
    try:
        save_ocr_debug(
            draft_id,
            {
                # This describes the bridge mode, not the old engine
                "bridge": "one_brain_legacy_categories",
                "pipeline": "one_brain_v2",
                "import_job_id": import_job_id,
                "legacy_categories_count": len(
                    draft_json.get("categories") or []
                ),
                "source_meta": draft_json.get("source") or {},
            },
        )
    except Exception:
        pass
    return {"id": draft_id, "draft_id": draft_id}



# ------------------------------------------------------------
# Structured import → Drafts
# ------------------------------------------------------------

def _flat_items_from_structured_items(
    items: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Shared flattener for structured items (CSV/XLSX/JSON) into draft_items rows.

    Rules:
      - name: required, non-empty
      - description: optional string
      - category: prefer subcategory over category
      - price_cents: integer, clamped to >= 0 (no invention beyond provided value)
      - confidence: default 100 when missing (structured imports are high trust)
    """
    flat_items: List[Dict[str, Any]] = []

    for raw in items:
        if not isinstance(raw, dict):
            continue

        name = (raw.get("name") or "").strip()
        if not name:
            continue

        description = (raw.get("description") or "").strip()

        subcat = (raw.get("subcategory") or "").strip() or None
        cat = (raw.get("category") or "").strip() or None
        category = subcat or cat

        price_cents_raw = raw.get("price_cents")
        try:
            price_cents = int(price_cents_raw) if price_cents_raw is not None else 0
        except Exception:
            price_cents = 0
        if price_cents < 0:
            price_cents = 0

        confidence = raw.get("confidence")
        if confidence is None:
            confidence = 100  # structured import: assume high confidence

        flat_items.append(
            {
                "name": name,
                "description": description,
                "price_cents": price_cents,
                "category": category,
                "position": None,
                "confidence": confidence,
            }
        )

    return flat_items


def create_draft_from_structured_items(
    title: str,
    restaurant_id: Optional[int],
    items: Iterable[Dict[str, Any]],
    *,
    source_type: str = "structured_csv",
    source_meta: Optional[Dict[str, Any]] = None,
    source_job_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Create a new draft from structured CSV/JSON items, bypassing OCR.

    Assumptions about each item dict (from structured ingestion helpers):
      - name: required, non-empty
      - description: optional string
      - category: optional
      - subcategory: optional (we prefer this as DB category when present)
      - price_cents: optional integer cents
      - size_name, tags, sku, pos_code: currently ignored at DB level (Phase 6+)

    We:
      - Prefer subcategory over category for the DB 'category' column.
      - Default price_cents to 0 when absent, but NEVER invent prices.
      - Default confidence to 100 for structured imports (high trust).
      - Store a small 'source' JSON blob indicating that this is a structured import.
      - Optionally link to import_jobs via source_job_id.
    """
    # Persist a small source blob so we can future-debug where this draft came from.
    meta: Dict[str, Any] = dict(source_meta or {})
    if source_job_id is not None:
        # Keep the import job id both in the column and in the JSON blob.
        meta.setdefault("import_job_id", int(source_job_id))

    src_payload = {
        "kind": "structured_import",
        "source_type": source_type,
        "meta": meta,
    }
    source_blob = json.dumps(src_payload, ensure_ascii=False)

    draft_id = _insert_draft(
        title=title,
        restaurant_id=restaurant_id,
        status="editing",
        source=source_blob,
        source_job_id=source_job_id,
        source_file_path=None,
    )

    flat_items = _flat_items_from_structured_items(items)
    _insert_items_bulk(draft_id, flat_items)

    return {"id": draft_id, "draft_id": draft_id}


def rebuild_draft_from_mapping(
    job_id: int,
    header_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Recompute structured items for a structured import job using the latest
    column mapping, and replace the linked draft's items in-place.

    Flow:
      - Use storage.import_jobs.rebuild_structured_items_from_header_map(...) to
        rebuild clean structured items based on raw_rows + header_map.
      - Find the existing draft linked to this job via source_job_id.
      - Delete existing draft_items for that draft.
      - Reinsert items using the same structured → flat rules as initial import.

    Returns:
      {
        "draft_id": int,
        "job_id": int,
        "header_map": {...},   # final header map used
        "summary": {...},      # counts dict
        "errors": [...],       # row-level validation errors
        "sample_rows": [...],  # preview items
      }
    """
    # 1) Rebuild items from the mapping engine.
    (
        clean_items,
        errors,
        summary,
        final_header_map,
        sample_rows,
    ) = rebuild_structured_items_from_header_map(int(job_id), header_map=header_map)

    # 2) Locate an existing draft for this import job.
    draft = find_draft_by_source_job(int(job_id))
    if not draft:
        raise ValueError(
            f"No draft found for structured import job {job_id}; "
            "cannot apply column mapping."
        )

    draft_id = int(draft["id"])

    # 3) Clear existing items and reinsert.
    with db_connect() as conn:
        conn.execute("DELETE FROM draft_items WHERE draft_id=?", (draft_id,))
        conn.commit()

    flat_items = _flat_items_from_structured_items(clean_items)
    _insert_items_bulk(draft_id, flat_items)

    # 4) Ensure draft is in editing state after rebuild.
    save_draft_metadata(draft_id, status="editing")

    return {
        "draft_id": draft_id,
        "job_id": int(job_id),
        "header_map": final_header_map,
        "summary": summary,
        "errors": errors,
        "sample_rows": sample_rows,
    }



# ------------------------------------------------------------
# Backfill: parse "Name (Size)" patterns into variant rows
# ------------------------------------------------------------
_BACKFILL_PATTERN = re.compile(
    r"^(?P<base>.+?)\s*\((?P<size>[^)]+)\)\s*$"
)


def backfill_variants_from_names(draft_id: int) -> Dict[str, Any]:
    """
    Scan draft items for the legacy "Name (Size)" naming pattern and convert
    them into proper parent item + variant rows.

    Groups items that share the same base name (after stripping the (Size)
    suffix) and category.  For each group with 2+ items:
      - Keep the first item as the parent (rename to base name)
      - Create variant rows from all items in the group
      - Delete the extra flattened rows

    Returns summary: {groups_found, variants_created, items_deleted}
    """
    items = get_draft_items(int(draft_id), include_variants=True)

    # Group by (base_name, category)
    groups: Dict[Tuple[str, Optional[str]], List[Dict[str, Any]]] = {}
    for it in items:
        # Skip items that already have variants
        if it.get("variants"):
            continue
        name = it.get("name") or ""
        m = _BACKFILL_PATTERN.match(name)
        if not m:
            continue
        base = m.group("base").strip()
        size_label = m.group("size").strip()
        if not base or not size_label:
            continue
        key = (base.lower(), it.get("category"))
        if key not in groups:
            groups[key] = []
        groups[key].append({
            "item": it,
            "base": base,
            "size_label": size_label,
        })

    groups_found = 0
    variants_created = 0
    items_deleted = 0

    for key, members in groups.items():
        if len(members) < 2:
            continue
        groups_found += 1

        # Sort by price to get consistent ordering (cheapest first)
        members.sort(key=lambda m: m["item"].get("price_cents", 0))

        # First item becomes the parent
        parent = members[0]["item"]
        parent_id = parent["id"]

        # Rename parent to base name (strip size suffix)
        with db_connect() as conn:
            conn.execute(
                "UPDATE draft_items SET name=?, updated_at=? WHERE id=?",
                (members[0]["base"], _now(), parent_id),
            )
            conn.commit()

        # Create variant rows from all members
        variant_rows = []
        for vi, m in enumerate(members):
            variant_rows.append({
                "label": m["size_label"],
                "price_cents": m["item"].get("price_cents", 0),
                "kind": "size",
                "position": vi,
            })
        inserted_ids = insert_variants(parent_id, variant_rows)
        variants_created += len(inserted_ids)

        # Delete the extra flattened rows (all except parent)
        delete_ids = [m["item"]["id"] for m in members[1:]]
        if delete_ids:
            deleted = delete_draft_items(int(draft_id), delete_ids)
            items_deleted += deleted

    return {
        "groups_found": groups_found,
        "variants_created": variants_created,
        "items_deleted": items_deleted,
    }


# ------------------------------------------------------------
# Publish helpers (Phase 9 — variant-aware output)
# ------------------------------------------------------------
def get_publish_rows(draft_id: int) -> List[Dict[str, Any]]:
    """
    Return a flat list of publishable rows for a draft, expanding variants.

    For each draft item:
      - If the item has variants: emit one row per variant with
        name = "ItemName (VariantLabel)" and price = variant price_cents.
      - If the item has NO variants: emit a single row with the
        item's own price_cents.

    Each row dict has: name, description, price_cents, category.
    """
    items = get_draft_items(int(draft_id), include_variants=True)
    rows: List[Dict[str, Any]] = []

    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        desc = (it.get("description") or "").strip()
        category = it.get("category")
        variants = it.get("variants") or []

        if variants:
            for v in variants:
                label = (v.get("label") or "").strip()
                vname = f"{name} ({label})" if label else name
                rows.append({
                    "name": vname,
                    "description": desc,
                    "price_cents": v.get("price_cents", 0),
                    "category": category,
                })
        else:
            rows.append({
                "name": name,
                "description": desc,
                "price_cents": it.get("price_cents", 0),
                "category": category,
            })

    return rows


def ensure_parent_base_price(draft_id: int) -> int:
    """
    Scan items with variants and ensure each parent's price_cents equals
    the minimum variant price.  Returns count of items updated.

    This enforces the rule: price_cents on parent = base/lowest price.
    Items without variants are untouched.
    """
    items = get_draft_items(int(draft_id), include_variants=True)
    updated = 0

    with db_connect() as conn:
        for it in items:
            variants = it.get("variants") or []
            if not variants:
                continue
            min_price = min(v.get("price_cents", 0) for v in variants)
            current_price = it.get("price_cents", 0)
            if current_price != min_price:
                conn.execute(
                    "UPDATE draft_items SET price_cents=?, updated_at=? WHERE id=?",
                    (min_price, _now(), it["id"]),
                )
                updated += 1
        conn.commit()

    return updated


# ------------------------------------------------------------
# Clone
# ------------------------------------------------------------
def clone_draft(draft_id: int) -> Dict[str, Any]:
    src = get_draft(int(draft_id))
    if not src:
        raise ValueError(f"Draft {draft_id} not found")

    # create new shell with "(copy)" in title, keep linkage to source_job_id but reset status
    new_title = (
        (src.get("title") or "").strip() or f"Draft {draft_id}"
    ) + " (copy)"
    new_id = _insert_draft(
        title=new_title,
        restaurant_id=src.get("restaurant_id"),
        status="editing",
        source=src.get("source"),
        source_job_id=src.get("source_job_id"),
        source_file_path=src.get("source_file_path"),
    )

    items = get_draft_items(int(draft_id), include_variants=True)
    for it in items:
        # Insert item into new draft
        inserted_ids = _insert_items_bulk(
            new_id,
            [
                {
                    "name": it.get("name"),
                    "description": it.get("description"),
                    "price_cents": _coerce_int(it.get("price_cents"), 0),
                    "category": it.get("category"),
                    "position": it.get("position"),
                    "confidence": _coerce_opt_int(it.get("confidence")),
                }
            ],
        )
        # Clone variants if present
        variants = it.get("variants") or []
        if inserted_ids and variants:
            new_item_id = inserted_ids[0]
            insert_variants(
                new_item_id,
                [
                    {
                        "label": v.get("label"),
                        "price_cents": v.get("price_cents", 0),
                        "kind": v.get("kind", "size"),
                        "position": v.get("position", 0),
                    }
                    for v in variants
                ],
            )

    return {"id": new_id, "draft_id": new_id}


# ---------------------------------------------------------------------------
# Pipeline rejection logging (Day 105 — Sprint 11.3)
# ---------------------------------------------------------------------------

def log_pipeline_rejection(
    restaurant_id: Optional[int],
    draft_id: Optional[int],
    gate_score: float,
    gate_reason: str,
    *,
    image_path: str = "",
    ocr_chars: int = 0,
    item_count: int = 0,
    pipeline_signals: Optional[Dict[str, Any]] = None,
) -> int:
    """Record a pipeline rejection in the database.

    Called when evaluate_confidence_gate() returns passed=False.
    Stores all available pipeline signals for post-mortem analysis and
    future pipeline hardening.

    Args:
        restaurant_id:    Restaurant that uploaded the menu (may be None).
        draft_id:         Draft created for the upload (may be None if none).
        gate_score:       Aggregate gate score (0.0-1.0) from GateResult.
        gate_reason:      Technical reason string from GateResult.
        image_path:       Path or identifier of the uploaded menu image.
        ocr_chars:        Character count from the OCR step.
        item_count:       Number of items extracted before the gate.
        pipeline_signals: Full signals dict from GateResult for JSON storage.

    Returns:
        The newly-inserted rejection row id.
    """
    signals_json = json.dumps(pipeline_signals or {})
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO pipeline_rejections
              (restaurant_id, draft_id, image_path, ocr_chars, item_count,
               gate_score, gate_reason, pipeline_signals, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                restaurant_id,
                draft_id,
                image_path or "",
                ocr_chars,
                item_count,
                round(float(gate_score), 6),
                gate_reason,
                signals_json,
                _now(),
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_pipeline_rejections(
    restaurant_id: Optional[int] = None,
    *,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Retrieve recent pipeline rejections for analysis.

    Args:
        restaurant_id: Filter to a specific restaurant.  None = all.
        limit:         Maximum rows to return (default 50).

    Returns:
        List of rejection dicts ordered by most-recent first.
        Each dict has: id, restaurant_id, draft_id, image_path, ocr_chars,
        item_count, gate_score, gate_reason, pipeline_signals (dict), created_at.
    """
    with db_connect() as conn:
        cur = conn.cursor()
        if restaurant_id is not None:
            cur.execute(
                """
                SELECT id, restaurant_id, draft_id, image_path, ocr_chars,
                       item_count, gate_score, gate_reason, pipeline_signals,
                       created_at
                FROM pipeline_rejections
                WHERE restaurant_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (restaurant_id, limit),
            )
        else:
            cur.execute(
                """
                SELECT id, restaurant_id, draft_id, image_path, ocr_chars,
                       item_count, gate_score, gate_reason, pipeline_signals,
                       created_at
                FROM pipeline_rejections
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = cur.fetchall()

    results = []
    for row in rows:
        signals = {}
        try:
            signals = json.loads(row[8] or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        results.append({
            "id":                row[0],
            "restaurant_id":     row[1],
            "draft_id":          row[2],
            "image_path":        row[3],
            "ocr_chars":         row[4],
            "item_count":        row[5],
            "gate_score":        row[6],
            "gate_reason":       row[7],
            "pipeline_signals":  signals,
            "created_at":        row[9],
        })
    return results


# -------------------------------------------------------
# Modifier Group CRUD (Phase 12.1 — Day 110)
# -------------------------------------------------------

# kind → default group config for migration
_KIND_TO_GROUP_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "size":   {"name": "Size",    "required": True,  "max_select": 1},
    "combo":  {"name": "Add-ons", "required": False, "max_select": 0},
    "flavor": {"name": "Flavor",  "required": False, "max_select": 1},
    "style":  {"name": "Style",   "required": False, "max_select": 1},
    "other":  {"name": "Options", "required": False, "max_select": 0},
}


def insert_modifier_group(
    item_id: int,
    name: str,
    *,
    required: bool = False,
    min_select: int = 0,
    max_select: int = 0,
    position: int = 0,
) -> int:
    """Insert a modifier group for a draft item. Returns new group id."""
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO draft_modifier_groups
                (item_id, name, required, min_select, max_select, position,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(item_id),
                str(name).strip(),
                1 if required else 0,
                int(min_select),
                int(max_select),
                int(position),
                _now(),
                _now(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_modifier_group(group_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single modifier group by id. Returns None if not found."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM draft_modifier_groups WHERE id=?",
            (int(group_id),),
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_modifier_groups(item_id: int) -> List[Dict[str, Any]]:
    """Fetch all modifier groups for a draft item, ordered by position then id."""
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM draft_modifier_groups WHERE item_id=? "
            "ORDER BY COALESCE(position, 1000000000), id",
            (int(item_id),),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_modifier_group(group_id: int, **fields: Any) -> bool:
    """
    Update modifier group fields by keyword arguments.
    Allowed fields: name, required, min_select, max_select, position.
    Returns True if a row was updated, False if not found or no valid fields.
    """
    _ALLOWED = {"name", "required", "min_select", "max_select", "position"}
    updates = {k: v for k, v in fields.items() if k in _ALLOWED}
    if not updates:
        return False
    updates["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [int(group_id)]
    with db_connect() as conn:
        cur = conn.execute(
            f"UPDATE draft_modifier_groups SET {sets} WHERE id=?", vals
        )
        conn.commit()
        return cur.rowcount > 0


def delete_modifier_group(group_id: int) -> bool:
    """
    Delete a modifier group. Variants belonging to this group have their
    modifier_group_id set to NULL before the group is removed.
    Returns True if a group was deleted, False if not found.
    """
    group_id = int(group_id)
    with db_connect() as conn:
        conn.execute(
            "UPDATE draft_item_variants SET modifier_group_id=NULL "
            "WHERE modifier_group_id=?",
            (group_id,),
        )
        cur = conn.execute(
            "DELETE FROM draft_modifier_groups WHERE id=?", (group_id,)
        )
        conn.commit()
        return cur.rowcount > 0


def migrate_variants_to_modifier_groups(item_id: int) -> int:
    """
    Auto-group existing variants for an item into modifier groups by kind.

    Mapping:
      size   → ModifierGroup("Size",    required=True,  max_select=1)
      combo  → ModifierGroup("Add-ons", required=False, max_select=0)
      flavor → ModifierGroup("Flavor",  required=False, max_select=1)
      style  → ModifierGroup("Style",   required=False, max_select=1)
      other  → ModifierGroup("Options", required=False, max_select=0)

    Idempotent: if the item already has modifier groups, returns 0 immediately.
    Returns count of modifier groups created (0 if nothing to do).
    """
    item_id = int(item_id)

    with db_connect() as conn:
        existing_count = conn.execute(
            "SELECT COUNT(*) FROM draft_modifier_groups WHERE item_id=?",
            (item_id,),
        ).fetchone()[0]
        if existing_count > 0:
            return 0

        rows = conn.execute(
            "SELECT id, kind FROM draft_item_variants WHERE item_id=? "
            "ORDER BY COALESCE(position, 1000000000), id",
            (item_id,),
        ).fetchall()

    if not rows:
        return 0

    # Group variant ids by kind (preserving insertion order)
    kind_to_ids: Dict[str, List[int]] = {}
    for row in rows:
        kind = (row["kind"] or "other").strip() or "other"
        kind_to_ids.setdefault(kind, []).append(row["id"])

    created = 0
    for pos, (kind, variant_ids) in enumerate(kind_to_ids.items()):
        defaults = _KIND_TO_GROUP_DEFAULTS.get(kind, _KIND_TO_GROUP_DEFAULTS["other"])
        group_id = insert_modifier_group(
            item_id,
            defaults["name"],
            required=defaults["required"],
            max_select=defaults["max_select"],
            position=pos,
        )
        with db_connect() as conn:
            conn.executemany(
                "UPDATE draft_item_variants SET modifier_group_id=? WHERE id=?",
                [(group_id, vid) for vid in variant_ids],
            )
            conn.commit()
        created += 1

    return created


# ---------------------------------------------------------------------------
# Day 111 — Modifier Group Template Library
# ---------------------------------------------------------------------------

import json as _json

MODIFIER_TEMPLATE_PRESETS: Dict[str, Dict[str, Any]] = {
    "size_sml": {
        "name": "Size (S/M/L)",
        "required": True,
        "min_select": 1,
        "max_select": 1,
        "modifiers": [
            {"label": "Small", "price_cents": 0, "kind": "size"},
            {"label": "Medium", "price_cents": 100, "kind": "size"},
            {"label": "Large", "price_cents": 200, "kind": "size"},
        ],
    },
    "temperature": {
        "name": "Temperature",
        "required": True,
        "min_select": 1,
        "max_select": 1,
        "modifiers": [
            {"label": "Hot", "price_cents": 0, "kind": "style"},
            {"label": "Iced", "price_cents": 0, "kind": "style"},
        ],
    },
    "sauce_choice": {
        "name": "Sauce Choice",
        "required": False,
        "min_select": 0,
        "max_select": 1,
        "modifiers": [
            {"label": "BBQ", "price_cents": 0, "kind": "flavor"},
            {"label": "Ranch", "price_cents": 0, "kind": "flavor"},
            {"label": "Buffalo", "price_cents": 0, "kind": "flavor"},
        ],
    },
    "protein_add": {
        "name": "Add Protein",
        "required": False,
        "min_select": 0,
        "max_select": 0,
        "modifiers": [
            {"label": "Chicken", "price_cents": 300, "kind": "combo"},
            {"label": "Steak", "price_cents": 500, "kind": "combo"},
            {"label": "Tofu", "price_cents": 200, "kind": "combo"},
        ],
    },
}


def insert_modifier_template(
    restaurant_id: Optional[int],
    name: str,
    modifiers: Optional[List[Dict[str, Any]]] = None,
    *,
    required: bool = False,
    min_select: int = 0,
    max_select: int = 0,
    position: int = 0,
) -> int:
    """Insert a reusable modifier group template. Returns new id."""
    now = _now()
    mods_json = _json.dumps(modifiers or [])
    with db_connect() as conn:
        gid = conn.execute(
            """
            INSERT INTO draft_modifier_group_templates
                (restaurant_id, name, required, min_select, max_select,
                 position, modifiers, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                restaurant_id,
                name.strip(),
                1 if required else 0,
                int(min_select),
                int(max_select),
                int(position),
                mods_json,
                now,
                now,
            ),
        ).lastrowid
        conn.commit()
    return gid


def get_modifier_template(template_id: int) -> Optional[Dict[str, Any]]:
    """Return a single template dict (with modifiers decoded), or None."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM draft_modifier_group_templates WHERE id=?",
            (int(template_id),),
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["modifiers"] = _json.loads(d["modifiers"])
    return d


def list_modifier_templates(
    restaurant_id: Optional[int],
) -> List[Dict[str, Any]]:
    """
    Return all templates for a restaurant (including global templates where
    restaurant_id IS NULL), ordered by position then id.
    """
    with db_connect() as conn:
        if restaurant_id is None:
            rows = conn.execute(
                "SELECT * FROM draft_modifier_group_templates "
                "WHERE restaurant_id IS NULL "
                "ORDER BY COALESCE(position, 1000000000), id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM draft_modifier_group_templates "
                "WHERE restaurant_id = ? OR restaurant_id IS NULL "
                "ORDER BY COALESCE(position, 1000000000), id",
                (int(restaurant_id),),
            ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["modifiers"] = _json.loads(d["modifiers"])
        result.append(d)
    return result


def delete_modifier_template(template_id: int) -> bool:
    """Hard-delete a template. Returns True if a row was deleted."""
    with db_connect() as conn:
        deleted = conn.execute(
            "DELETE FROM draft_modifier_group_templates WHERE id=?",
            (int(template_id),),
        ).rowcount
        conn.commit()
    return deleted > 0


def seed_modifier_template_presets(restaurant_id: Optional[int]) -> int:
    """
    Insert all MODIFIER_TEMPLATE_PRESETS for a restaurant if none exist yet.
    Returns the number of templates inserted (0 if already seeded).
    """
    existing = list_modifier_templates(restaurant_id)
    existing_names = {t["name"] for t in existing}
    inserted = 0
    for pos, preset in enumerate(MODIFIER_TEMPLATE_PRESETS.values()):
        if preset["name"] not in existing_names:
            insert_modifier_template(
                restaurant_id,
                preset["name"],
                preset["modifiers"],
                required=bool(preset.get("required", False)),
                min_select=int(preset.get("min_select", 0)),
                max_select=int(preset.get("max_select", 0)),
                position=pos,
            )
            inserted += 1
    return inserted


def apply_modifier_template(item_id: int, template_id: int) -> Dict[str, Any]:
    """
    Create a modifier group on *item_id* from *template_id*.

    Returns {"group_id": int, "modifier_ids": List[int]}.

    Raises ValueError if either the item or template does not exist.
    This is intentionally non-idempotent: applying the same template twice
    creates two independent groups (useful for e.g. two separate sauce rounds).
    """
    template = get_modifier_template(int(template_id))
    if template is None:
        raise ValueError(f"Modifier template {template_id} not found")

    # Verify item exists by attempting a direct query
    with db_connect() as conn:
        item_row = conn.execute(
            "SELECT id FROM draft_items WHERE id=?", (int(item_id),)
        ).fetchone()
    if item_row is None:
        raise ValueError(f"Draft item {item_id} not found")

    # Create the modifier group from template attrs
    group_id = insert_modifier_group(
        int(item_id),
        template["name"],
        required=bool(template["required"]),
        min_select=int(template["min_select"]),
        max_select=int(template["max_select"]),
    )

    # Insert each modifier as a variant linked to the new group
    now = _now()
    modifier_ids: List[int] = []
    with db_connect() as conn:
        for pos, mod in enumerate(template["modifiers"]):
            vid = conn.execute(
                """
                INSERT INTO draft_item_variants
                    (item_id, label, price_cents, kind, position,
                     modifier_group_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(item_id),
                    mod.get("label", ""),
                    int(mod.get("price_cents", 0)),
                    mod.get("kind", "other"),
                    pos,
                    group_id,
                    now,
                    now,
                ),
            ).lastrowid
            modifier_ids.append(vid)
        conn.commit()

    return {"group_id": group_id, "modifier_ids": modifier_ids}


# ---------------------------------------------------------------------------
# Day 114 — Modifier Group Reorder + Bulk Migration
# ---------------------------------------------------------------------------


def _bulk_reorder_by_position(
    table: str,
    parent_col: str,
    parent_id: int,
    ordered_ids: List[int],
) -> int:
    """
    Bulk-update the *position* column for rows in *table* whose id appears in
    *ordered_ids* and whose *parent_col* equals *parent_id*.

    The index of each id in *ordered_ids* becomes its new position value.
    IDs that do not belong to *parent_id* are silently skipped.

    Returns the total number of rows updated.
    """
    if not ordered_ids:
        return 0
    now = _now()
    rows = [(pos, now, int(gid), int(parent_id)) for pos, gid in enumerate(ordered_ids, start=1)]
    with db_connect() as conn:
        cur = conn.executemany(
            f"UPDATE {table} SET position=?, updated_at=? WHERE id=? AND {parent_col}=?",
            rows,
        )
        conn.commit()
        return cur.rowcount


def reorder_modifier_groups(item_id: int, ordered_ids: List[int]) -> int:
    """
    Bulk-update position for modifier groups belonging to *item_id*.

    *ordered_ids* is the desired display order (index = new position).
    Only IDs that actually belong to *item_id* are updated; unknown IDs
    are silently skipped.

    Returns the number of rows updated.
    """
    return _bulk_reorder_by_position(
        "draft_modifier_groups", "item_id", int(item_id), ordered_ids
    )


def reorder_modifiers(group_id: int, ordered_ids: List[int]) -> int:
    """
    Bulk-update position for modifiers (variants) belonging to *group_id*.

    *ordered_ids* is the desired display order (index = new position).
    Only IDs whose modifier_group_id matches *group_id* are updated;
    unknown IDs are silently skipped.

    Returns the number of rows updated.
    """
    return _bulk_reorder_by_position(
        "draft_item_variants", "modifier_group_id", int(group_id), ordered_ids
    )


def reorder_items(draft_id: int, ordered_ids: List[int]) -> int:
    """
    Bulk-update position for draft items belonging to *draft_id*.

    *ordered_ids* is the desired display order (index = new position).
    Only IDs that actually belong to *draft_id* are updated; unknown IDs
    are silently skipped.

    Returns the number of rows updated.
    """
    return _bulk_reorder_by_position(
        "draft_items", "draft_id", int(draft_id), ordered_ids
    )


def upsert_group_modifiers(group_id: int, modifiers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Full-replace modifiers (draft_item_variants) for a modifier group.

    Deletes all existing variants for *group_id*, then inserts each entry
    from *modifiers*.  Each modifier dict should have:
        label       (str, required — blank entries are skipped)
        price_cents (int, optional, default 0)
        id          (int, optional — ignored; always re-inserted for simplicity)

    Returns {"inserted": N, "deleted": N}.
    """
    if not isinstance(modifiers, list):
        modifiers = []

    # Fetch item_id so newly inserted rows are consistent
    with db_connect() as conn:
        row = conn.execute(
            "SELECT item_id FROM draft_modifier_groups WHERE id=?",
            (int(group_id),),
        ).fetchone()
        if row is None:
            return {"inserted": 0, "deleted": 0}
        item_id = row[0]

        cur = conn.cursor()
        del_result = cur.execute(
            "DELETE FROM draft_item_variants WHERE modifier_group_id=?",
            (int(group_id),),
        )
        deleted = del_result.rowcount

        inserted = 0
        for idx, m in enumerate(modifiers):
            if not isinstance(m, dict):
                continue
            label = (m.get("label") or "").strip()
            if not label:
                continue
            try:
                price_cents = int(m.get("price_cents") or 0)
            except (ValueError, TypeError):
                price_cents = 0
            cur.execute(
                """
                INSERT INTO draft_item_variants
                    (item_id, label, price_cents, kind, position,
                     modifier_group_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (item_id, label, price_cents, "other", idx,
                 int(group_id), _now(), _now()),
            )
            inserted += 1

        conn.commit()
    return {"inserted": inserted, "deleted": deleted}


def migrate_draft_modifier_groups(draft_id: int) -> Dict[str, int]:
    """
    Batch-migrate all items in *draft_id* that have ungrouped variants.

    Calls migrate_variants_to_modifier_groups() per item (idempotent per
    item — items that already have groups are skipped).

    Returns {"item_count": int, "migrated_count": int} where:
      item_count    — total items in the draft
      migrated_count — number of items that had groups created
    """
    draft_id = int(draft_id)
    with db_connect() as conn:
        item_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM draft_items WHERE draft_id=? ORDER BY id",
                (draft_id,),
            ).fetchall()
        ]

    migrated = 0
    for iid in item_ids:
        created = migrate_variants_to_modifier_groups(iid)
        if created > 0:
            migrated += 1

    return {"item_count": len(item_ids), "migrated_count": migrated}


# ------------------------------------------------------------
# Wizard category review tracking (Day 137)
# ------------------------------------------------------------

def init_wizard_categories(draft_id: int) -> List[str]:
    """
    Initialize category review rows for all categories in the draft.
    Returns the list of category names in position order.
    Idempotent — only inserts categories not yet tracked.
    """
    items = get_draft_items(draft_id) or []
    categories = []
    seen = set()
    for it in items:
        cat = (it.get("category") or "Uncategorized").strip()
        if cat not in seen:
            seen.add(cat)
            categories.append(cat)

    now = datetime.utcnow().isoformat()
    with db_connect() as conn:
        for cat in categories:
            conn.execute(
                """INSERT OR IGNORE INTO draft_category_reviews
                   (draft_id, category, reviewed, reviewed_at)
                   VALUES (?, ?, 0, NULL)""",
                (draft_id, cat),
            )
        conn.commit()
    return categories


def get_wizard_progress(draft_id: int) -> Dict[str, Any]:
    """
    Get wizard review progress for a draft.
    Returns {categories: [{name, reviewed, reviewed_at}], total, reviewed_count, complete}.
    """
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT category, reviewed, reviewed_at
               FROM draft_category_reviews
               WHERE draft_id = ?
               ORDER BY id""",
            (draft_id,),
        ).fetchall()

    cats = [
        {"name": r["category"], "reviewed": bool(r["reviewed"]), "reviewed_at": r["reviewed_at"]}
        for r in rows
    ]
    total = len(cats)
    reviewed_count = sum(1 for c in cats if c["reviewed"])
    return {
        "categories": cats,
        "total": total,
        "reviewed_count": reviewed_count,
        "complete": total > 0 and reviewed_count == total,
    }


def mark_category_reviewed(draft_id: int, category: str) -> bool:
    """Mark a single category as reviewed. Returns True if updated."""
    now = datetime.utcnow().isoformat()
    with db_connect() as conn:
        cur = conn.execute(
            """UPDATE draft_category_reviews
               SET reviewed = 1, reviewed_at = ?
               WHERE draft_id = ? AND category = ?""",
            (now, draft_id, category),
        )
        conn.commit()
    return cur.rowcount > 0


def unmark_category_reviewed(draft_id: int, category: str) -> bool:
    """Unmark a category (allow re-review). Returns True if updated."""
    with db_connect() as conn:
        cur = conn.execute(
            """UPDATE draft_category_reviews
               SET reviewed = 0, reviewed_at = NULL
               WHERE draft_id = ? AND category = ?""",
            (draft_id, category),
        )
        conn.commit()
    return cur.rowcount > 0


def mark_wizard_completed(draft_id: int) -> None:
    """Flag the draft as having completed the wizard review."""
    now = datetime.utcnow().isoformat()
    with db_connect() as conn:
        conn.execute(
            "UPDATE drafts SET wizard_completed = 1, updated_at = ? WHERE id = ?",
            (now, draft_id),
        )
        conn.commit()
