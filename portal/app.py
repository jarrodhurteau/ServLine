# portal/app.py 
from flask import (
    Flask, jsonify, render_template, abort, request, redirect, url_for,
    session, send_from_directory, flash, make_response, send_file        # ← added send_file
)

# --- Standard libs & typing ---
import sqlite3
from pathlib import Path
from functools import wraps
import uuid
import os
import threading
import json
import shutil
from datetime import datetime
from typing import Optional, Iterable, Tuple, List, Dict, Any
import hashlib  # <-- added for cat_hue filter
import time     # <-- NEW: for gentle polling after upload

# --- Paths ---
ROOT = Path(__file__).resolve().parents[1]

# Make project root importable so we can import storage.*
import sys
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from storage import import_jobs as import_jobs_store  # <-- NEW: structured import helpers
# segment_document import removed — facade provides layout data; no need for duplicate call


# --- Forward decls for type checkers (real implementations appear later) ---
def _ocr_image_to_text(img_path: Path) -> str: ...
def _pdf_to_text(pdf_path: Path) -> str: ...


# stdlib for exports
import io
import csv
import re  # <-- for OCR parsing

# NEW: optional Excel export dependency
openpyxl = None  # type: ignore[assignment]
try:
    import openpyxl as _openpyxl  # type: ignore[import]
    openpyxl = _openpyxl  # type: ignore[assignment]
except Exception:
    openpyxl = None  # type: ignore[assignment]


# safer filename + big-file error handling
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

# OCR health imports
import pytesseract

# OCR health (single source of truth)
ocr_health_lib = None  # type: ignore[assignment]

try:
    from storage.ocr_facade import health as _ocr_health_lib  # type: ignore
    ocr_health_lib = _ocr_health_lib
except Exception as e:
    _ocr_health_import_error = repr(e)

    def _fallback_ocr_health_lib():
        return {
            "engine": "error",
            "error": f"ocr_facade health import failed: {_ocr_health_import_error}",
        }

    ocr_health_lib = _fallback_ocr_health_lib




# ✅ Try to import the contract validator (with safe fallback if file not added yet)
try:
    from portal.contracts import validate_draft_payload  # type: ignore
except Exception:
    def validate_draft_payload(_payload):  # type: ignore
        # No-op validator so app still runs if contracts.py isn't present yet.
        return True, ""


# ✅ Make sure the OCR worker is imported at app startup
#    (this triggers the version banner inside ocr_worker.py)
from portal import ocr_worker
print("[App] Imported portal.ocr_worker")  # optional confirmation from app.py


# ------------------------
# App & Config
# ------------------------
app = Flask(__name__)

# --- Config (dev) ---
app.config["SECRET_KEY"] = "dev-secret-change-me"          # replace later with env var
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024        # ~20 MB
# Dev QoL: auto-reload templates and disable static caching when iterating on UI
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

DEV_USERNAME = "admin"
DEV_PASSWORD = "letmein"

# --- Debug OCR routes ---
from portal.routes_debug_preocr import debug_preocr
app.register_blueprint(debug_preocr)

# --- Paths ---
DB_PATH = ROOT / "storage" / "servline.db"

# --- Load .env if available (so TESSERACT_CMD / POPPLER_PATH work even without PATH) ---
try:
    from dotenv import load_dotenv  # optional; ok if not installed
    load_dotenv(ROOT / ".env")
except Exception:
    pass

# --- OCR paths from env ---
TESSERACT_CMD = os.getenv("TESSERACT_CMD")
POPPLER_PATH = os.getenv("POPPLER_PATH") or None
# Allow tuning Tesseract without code changes
TESSERACT_LANG = os.getenv("TESSERACT_LANG") or "eng"
TESSERACT_CONFIG = os.getenv("TESSERACT_CONFIG") or "--oem 1 --psm 3"

# Day 20 — Canonical taxonomy seed (editable)
TAXONOMY_SEED = [
    "pizzas", "calzones", "salads", "wings", "appetizers", "burgers", "sandwiches", "subs",
    "pasta", "steaks", "seafood", "tacos", "burritos", "sides", "desserts", "beverages",
    "breakfast", "lunch specials", "dinner specials", "kids menu"
]

# Windows-friendly Tesseract discovery:
# 1) Respect explicit env var if it exists
# 2) Else use PATH
# 3) Else try common install locations
if TESSERACT_CMD and Path(TESSERACT_CMD).exists():
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
else:
    _which = shutil.which("tesseract") or shutil.which("tesseract.exe")
    if _which:
        pytesseract.pytesseract.tesseract_cmd = _which
    else:
        _common = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        for p in _common:
            if Path(p).exists():
                pytesseract.pytesseract.tesseract_cmd = p
                break

# storage layer for drafts (DB-first, Day 12+)
try:
    from storage import drafts as drafts_store
except Exception:
    drafts_store = None  # guarded below

# OCR engine (Day-21 revamp / One Brain façade)
try:
    from storage.ocr_facade import build_structured_menu
    extract_items_from_path = build_structured_menu
    print("[APP] Loaded OCR facade OK")
except Exception as e:
    print("[APP] OCR facade failed:", e)

    extract_items_from_path = None
    _ocr_facade_error = repr(e)

# AI OCR Heuristics (Day 20)
try:
    from storage.ai_ocr_helper import analyze_ocr_text  # Phase A (heuristics-only)
except Exception:
    analyze_ocr_text = None

# uploads (kept out of git via .gitignore)
UPLOAD_FOLDER = ROOT / "uploads"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
TRASH_FOLDER = UPLOAD_FOLDER / ".trash"
TRASH_FOLDER.mkdir(parents=True, exist_ok=True)

# drafts + raw artifacts
DRAFTS_FOLDER = ROOT / "storage" / "drafts"
DRAFTS_FOLDER.mkdir(parents=True, exist_ok=True)

# Primary raw folder (newer layout) and legacy raw folder support
RAW_FOLDER = DRAFTS_FOLDER / "raw"
RAW_FOLDER.mkdir(parents=True, exist_ok=True)
LEGACY_RAW_FOLDER = ROOT / "storage" / "raw"
LEGACY_RAW_FOLDER.mkdir(parents=True, exist_ok=True)

# trash bins for artifacts
TRASH_DRAFTS = DRAFTS_FOLDER / ".trash"
TRASH_DRAFTS.mkdir(parents=True, exist_ok=True)
TRASH_RAW = RAW_FOLDER / ".trash"
TRASH_RAW.mkdir(parents=True, exist_ok=True)
LEGACY_TRASH_RAW = LEGACY_RAW_FOLDER / ".trash"
LEGACY_TRASH_RAW.mkdir(parents=True, exist_ok=True)

# Allowed upload types
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "pdf"}
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

# ------------------------
# Safe render helper (prevents template-caused 500 loops)
# ------------------------
def _safe_render(template_name: str, **ctx):
    # Try to print the exact template file being used
    try:
        if app and app.jinja_loader:
            try:
                # get_source returns (source, filename, uptodate)
                _src, _filename, _ = app.jinja_loader.get_source(app.jinja_env, template_name)
                print(f"[TEMPLATE DEBUG] → {template_name} from {_filename}")
            except Exception as e:
                print(f"[TEMPLATE DEBUG] (could not resolve path for {template_name}): {e}")
    except Exception:
        # If printing fails for any reason, we still render normally
        pass

    try:
        return render_template(template_name, **ctx)
    except Exception:
        # Minimal inline fallback to expose the real traceback
        import html, traceback
        tb = html.escape(traceback.format_exc())
        body = f"<h1>{html.escape(template_name)} missing or failed</h1><pre>{tb}</pre>"
        return body, 200, {"Content-Type": "text/html; charset=utf-8"}

# ------------------------
# Template globals + filters
# ------------------------
def _cat_hue(value: Optional[str]) -> int:
    """
    Deterministic hue (0–359) from a category string.
    Usage in templates: style="--hue: {{ category|cat_hue }};"
    """
    s = (value or "").strip().lower()
    if not s:
        return 210  # default-ish blue
    h = int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16) % 360
    return h

@app.template_filter("cat_hue")
def jinja_cat_hue(value: Optional[str]) -> int:
    return _cat_hue(value)

# ------------------------
# Confidence → CSS class mapping
# ------------------------
@app.template_filter("confidence_class")
def confidence_class(conf) -> str:
    """
    Map numeric confidence (0–100) to a CSS class.
    """
    try:
        c = int(conf or 0)
    except (TypeError, ValueError):
        c = 0

    if c >= 80:
        return "conf-high"
    elif c >= 50:
        return "conf-med"
    elif c > 0:
        return "conf-low"
    else:
        return "conf-unknown"


def score_item_quality(item: Dict[str, Any]) -> Tuple[int, bool]:
    """
    Compute a 0–100 quality score for a cleaned draft item and whether it's low-confidence.

    Inputs (post-AI-cleanup fields, if present):
      - item["confidence"]: OCR/parse confidence (0–100)
      - item["price_cents"]: int cents (0 if missing/invalid)
      - item["category"]: string (may be 'Uncategorized')
      - item["name"]: cleaned name
      - item["description"]: cleaned description

    Returns:
      (quality_score, is_low_confidence)
    """
    name = (item.get("name") or "").strip()
    desc = (item.get("description") or "").strip()
    category = (item.get("category") or "").strip() or "Uncategorized"

    # Confidence as integer baseline
    conf_raw = item.get("confidence")
    try:
        conf = int(conf_raw) if conf_raw is not None else 0
    except Exception:
        conf = 0

    # Price in cents
    price_raw = item.get("price_cents")
    try:
        price_cents = int(price_raw) if price_raw is not None else 0
    except Exception:
        price_cents = 0

    # Start from OCR confidence (already 0–100)
    score = conf

    # --- Price validity ---
    if price_cents <= 0:
        # Missing/zero price is a strong negative
        score -= 15
    else:
        # Very low price (likely sides) is a small nudge
        if price_cents < 300:      # <$3
            score -= 3
        # Very high price (probably parse issue)
        if price_cents > 6000:     # >$60
            score -= 8

    # --- Category quality ---
    if category.lower() in {"uncategorized", "other", "misc"}:
        score -= 10
    else:
        score += 3

    # --- Name length sanity ---
    nlen = len(name)
    if nlen == 0:
        score -= 25
    elif nlen < 6:
        score -= 10
    elif nlen > 120:
        score -= 15
    elif nlen > 80:
        score -= 10

    # --- Junk-symbol density (proxy for OCR/cleanup difficulty) ---
    if nlen:
        clean_chars = sum(
            1 for c in name
            if (c.isalnum() or c.isspace() or c in "&()/+'-.$")
        )
        junk_ratio = 1.0 - (clean_chars / max(nlen, 1))
        if junk_ratio > 0.45:
            score -= 25
        elif junk_ratio > 0.30:
            score -= 15
        elif junk_ratio > 0.20:
            score -= 8
    else:
        junk_ratio = 0.0  # not used directly, kept for clarity

    # --- Description sanity (tiny nudge) ---
    if not desc and nlen > 40:
        score -= 3

    # Clamp to [0, 100]
    if score < 0:
        score = 0
    if score > 100:
        score = 100

    low_conf = score < 65
    return score, low_conf


@app.context_processor
def inject_globals():
    """Provide `now` and `show_admin` to all templates."""
    return {
        "now": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "show_admin": bool(session.get("user")),
    }

# ------------------------
# DB helpers (align with schema.sql: status only)
# ------------------------
def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def create_import_job(filename: str, restaurant_id: Optional[int] = None) -> int:
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO import_jobs (restaurant_id, filename, status, created_at, updated_at)
            VALUES (?, ?, 'pending', datetime('now'), datetime('now'))
            """,
            (restaurant_id, filename),
        )
        job_id = cur.lastrowid
        conn.commit()
        return int(job_id)

def update_import_job(job_id: int, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields.keys())
    values = list(fields.values())
    sets = f"{sets}, updated_at=datetime('now')"
    with db_connect() as conn:
        conn.execute(f"UPDATE import_jobs SET {sets} WHERE id=?", (*values, job_id))
        conn.commit()

def get_import_job(job_id: int):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM import_jobs WHERE id=?", (job_id,)).fetchone()

def list_import_jobs(limit: int = 100, include_deleted: bool = False):
    with db_connect() as conn:
        if include_deleted:
            sql = """
                SELECT * FROM import_jobs
                ORDER BY datetime(created_at) DESC
                LIMIT ?
            """
            args = (limit,)
        else:
            sql = """
                SELECT * FROM import_jobs
                WHERE COALESCE(status,'') != 'deleted'
                ORDER BY datetime(created_at) DESC
                LIMIT ?
            """
            args = (limit,)
        return conn.execute(sql, args).fetchall()

def _jobs_for_upload_filename(upload_name: str):
    with db_connect() as conn:
        return conn.execute(
            "SELECT id, draft_path FROM import_jobs WHERE filename=?",
            (upload_name, ),
        ).fetchall()

# ------------------------
# Auth helper
# ------------------------
def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapper

# ------------------------
# Role/restaurant helpers (ADMIN vs CUSTOMER scoping)
# ------------------------
def _resolve_restaurant_id_for_action(job_row: sqlite3.Row) -> Optional[int]:
    """
    Determine the restaurant context for an action:
      - customer side: session['user']['restaurant_id']
      - admin side: use job_row.restaurant_id (must be chosen)
    """
    u = (session.get("user") or {})
    if u.get("role") == "customer" and u.get("restaurant_id"):
        try:
            return int(u["restaurant_id"])
        except Exception:
            return None
    rid = job_row["restaurant_id"]
    try:
        return int(rid) if rid is not None else None
    except Exception:
        return None

def _resolve_restaurant_id_from_request() -> Optional[int]:
    """
    Used at upload time:
      - If customer: force their own restaurant_id.
      - Else (admin): try form['restaurant_id'] if provided.
    """
    u = (session.get("user") or {})
    if u.get("role") == "customer" and u.get("restaurant_id"):
        try:
            return int(u["restaurant_id"])
        except Exception:
            return None
    rid = request.form.get("restaurant_id")
    if rid is None or str(rid).strip() == "":
        return None
    try:
        return int(rid)
    except Exception:
        return None

def _find_or_create_menu_for_restaurant(conn: sqlite3.Connection, restaurant_id: int) -> int:
    """Return an existing active menu for the restaurant, or create a new one."""
    cur = conn.cursor()
    row = cur.execute(
        "SELECT id FROM menus WHERE restaurant_id=? AND active=1 ORDER BY id LIMIT 1",
        (int(restaurant_id),)
    ).fetchone()
    if row:
        return int(row["id"] if isinstance(row, sqlite3.Row) else row[0])
    name = f"Imported {datetime.utcnow().date()}"
    cur.execute(
        "INSERT INTO menus (restaurant_id, name, active) VALUES (?, ?, 1)",
        (int(restaurant_id), name)
    )
    return int(cur.lastrowid)

# ------------------------
# Small helpers for Day 13 flow
# ------------------------
def _require_drafts_storage():
    if drafts_store is None:
        abort(500, description="Drafts storage layer not available. Ensure storage/drafts.py exists and is importable.")

def _abs_from_rel(rel_path: Optional[str]) -> Optional[Path]:
    if not rel_path:
        return None
    return (ROOT / rel_path).resolve()

def _price_to_cents(v) -> int:
    """Accept floats/strings like 12.5, '12.50', '$12.5', '12 50' and return cents."""
    if v is None:
        return 0
    try:
        if isinstance(v, (int, float)):
            return int(round(float(v) * 100))
        s = str(v)
        s = s.replace("$", "").replace(",", " ").strip()
        # handle '12 50' -> '12.50'
        parts = [p for p in s.split() if p]
        if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) in (1, 2):
            s = parts[0] + "." + parts[1].rjust(2, "0")
        return int(round(float(s) * 100))
    except Exception:
        return 0
def _find_or_create_menu_for_job(conn: sqlite3.Connection, job_row: sqlite3.Row) -> int:
    """(Legacy) Pick an existing active menu for the job's restaurant, else create one."""
    rest_id = job_row["restaurant_id"]
    cur = conn.cursor()
    if rest_id:
        m = cur.execute(
            "SELECT id FROM menus WHERE restaurant_id=? AND active=1 ORDER  BY id LIMIT 1",
            (rest_id,),
        ).fetchone()
        if m:
            return int(m["id"])
    # create a new menu
    name = f"Imported {datetime.utcnow().date()}"
    cur.execute(
        "INSERT INTO menus (restaurant_id, name, active) VALUES (?, ?, 1)",
        (rest_id, name),
    )
    return int(cur.lastrowid)

def _find_draft_for_job(job_id: int) -> Optional[int]:
    """
    Read-only lookup: return an existing DB-backed draft_id linked via source_job_id.
    Does NOT create drafts from preview/legacy JSON.
    """
    _require_drafts_storage()

    if hasattr(drafts_store, "find_draft_by_source_job"):
        existing = drafts_store.find_draft_by_source_job(job_id)
        if existing and (existing.get("id") or existing.get("draft_id")):
            try:
                return int(existing.get("id") or existing.get("draft_id"))
            except Exception:
                return None

    return None


def _get_or_create_draft_for_job(job_id: int, allow_create: bool = False) -> Optional[int]:
    """
    Return a draft_id for this import job.

    If allow_create=False (default): read-only behavior, returns existing draft_id or None.
    If allow_create=True: will create a draft from the best available JSON source.

    Priority (when allow_create=True):
      1. Reuse existing DB-backed draft linked via source_job_id.
      2. For OCR/AI jobs: use ai_preview_path / preview_path JSON if present.
      3. For structured JSON jobs: use embedded payload_json when present.
      4. Fallback: legacy JSON at draft_path.
    """
    _require_drafts_storage()
    row = get_import_job(job_id)
    if not row:
        return None

    # -----------------------------
    # 1) Existing DB-backed draft?
    # -----------------------------
    draft_id = _find_draft_for_job(job_id)
    if draft_id:
        # Sync restaurant_id if the job has one and draft is missing it
        try:
            keys = set(row.keys()) if hasattr(row, "keys") else set()
            if "restaurant_id" in keys and row["restaurant_id"]:
                existing = drafts_store.get_draft(draft_id) if hasattr(drafts_store, "get_draft") else {}
                if isinstance(existing, dict) and not existing.get("restaurant_id"):
                    drafts_store.save_draft_metadata(
                        draft_id,
                        restaurant_id=int(row["restaurant_id"]),
                    )
        except Exception:
            pass
        return draft_id

    # If we are not allowed to create drafts, stop here (GET-safe default).
    if not allow_create:
        return None

    # We’ll build draft_json from the best available source.
    draft_json: Optional[Dict[str, Any]] = None
    keys = set(row.keys()) if hasattr(row, "keys") else set()

    # -----------------------------
    # 2) Prefer file-based AI preview JSON (OCR jobs)
    #    Try ai_preview_path → preview_path → draft_path
    # -----------------------------
    abs_path: Optional[Path] = None
    used_source_label: str = ""
    for key in ("ai_preview_path", "preview_path", "draft_path"):
        raw_path = row[key] if key in keys else None
        if raw_path:
            candidate = _abs_from_rel(raw_path)
            if candidate and candidate.exists():
                abs_path = candidate
                used_source_label = key
                break

    if abs_path and abs_path.exists():
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                draft_json = loaded
        except Exception:
            draft_json = None

    # -----------------------------
    # 3) Fallback: embedded payload_json (structured JSON jobs)
    # -----------------------------
    if draft_json is None and "payload_json" in keys:
        payload_raw = row["payload_json"]
        if payload_raw:
            try:
                candidate = json.loads(payload_raw)
                if isinstance(candidate, dict):
                    draft_json = candidate
                    used_source_label = "payload_json"
            except Exception:
                draft_json = None

    if draft_json is None:
        return None

    # -----------------------------
    # 4) Create new draft from chosen JSON
    # -----------------------------
    draft_id = None

    create_fn = getattr(drafts_store, "create_draft_from_import", None)
    if callable(create_fn):
        try:
            created = create_fn(draft_json, import_job_id=job_id)
        except Exception:
            created = None

        if isinstance(created, dict):
            new_id_raw = created.get("id") or created.get("draft_id")
            if new_id_raw:
                try:
                    draft_id = int(new_id_raw)
                except Exception:
                    draft_id = None

    # If create_draft_from_import didn’t produce a usable draft id, fall back to structured-items path.
    if draft_id is None:
        create_structured = getattr(drafts_store, "create_draft_from_structured_items", None)
        if callable(create_structured):
            flat = _draft_items_from_draft_json(draft_json)

            structured_items: List[Dict[str, Any]] = []
            for it in flat:
                name = (it.get("name") or "").strip()
                if not name:
                    continue
                desc = (it.get("description") or "").strip()
                category = (it.get("category") or "Uncategorized").strip() or "Uncategorized"
                confidence = it.get("confidence")
                position = it.get("position")

                price_val = it.get("price")
                price_cents = 0
                if price_val is not None:
                    try:
                        price_cents = int(round(float(price_val) * 100.0))
                    except Exception:
                        price_cents = 0

                structured_items.append(
                    {
                        "name": name,
                        "description": desc,
                        "category": category,
                        "price_cents": int(price_cents),
                        "confidence": confidence,
                        "position": position,
                    }
                )

            restaurant_id = None
            if "restaurant_id" in keys and row["restaurant_id"]:
                try:
                    restaurant_id = int(row["restaurant_id"])
                except Exception:
                    restaurant_id = None

            title = f"Imported OCR {datetime.utcnow().date()}"
            try:
                src = draft_json.get("source") if isinstance(draft_json, dict) else None
                if isinstance(src, dict) and src.get("file"):
                    title = f"{src.get('file')}"
            except Exception:
                title = f"Imported OCR {datetime.utcnow().date()}"

            if structured_items:
                try:
                    created2 = create_structured(
                        title=title,
                        restaurant_id=restaurant_id,
                        items=structured_items,
                        source_type="ocr_legacy_categories",
                        source_job_id=int(job_id),
                        source_meta={
                            "import_job_id": int(job_id),
                            "source": used_source_label or "unknown",
                            "draft_json_kind": "categories",
                        },
                    )
                except Exception:
                    created2 = None

                if isinstance(created2, dict):
                    new_id_raw2 = created2.get("id") or created2.get("draft_id")
                    if new_id_raw2:
                        try:
                            draft_id = int(new_id_raw2)
                        except Exception:
                            draft_id = None

    if draft_id is None:
        return None

    # -----------------------------
    # 5) Sync restaurant_id
    # -----------------------------
    if "restaurant_id" in keys and row["restaurant_id"]:
        try:
            drafts_store.save_draft_metadata(
                draft_id,
                restaurant_id=int(row["restaurant_id"]),
            )
        except Exception:
            pass

    # Best-effort: link back to import_jobs.draft_id if the column exists.
    try:
        update_import_job(job_id, draft_id=int(draft_id))
    except Exception:
        pass

    return int(draft_id)



# ---------- MISSING HELPERS (wired to /imports actions) ----------
def _dedupe_exists(conn: sqlite3.Connection, menu_id: int, name: str, price_cents: int) -> bool:
    cur = conn.cursor()
    row = cur.execute(
        """
        SELECT 1 FROM menu_items
        WHERE menu_id=? AND lower(trim(name))=lower(trim(?)) AND price_cents=?
        LIMIT 1
        """,
        (menu_id, name, price_cents),
    ).fetchone()
    return bool(row)


def approve_draft_to_menu(job_id: int) -> Tuple[int, int]:
    """
    Commit draft rows for job -> menu_items with simple dedupe.
    Requires a restaurant context:
      - customer side: session['user']['restaurant_id']
      - admin side: job_row.restaurant_id (must be chosen)
    Returns (menu_id, inserted_count).
    """
    _require_drafts_storage()
    job = get_import_job(job_id)
    if not job:
        abort(404, description="Job not found")

    # Determine restaurant context
    restaurant_id = _resolve_restaurant_id_for_action(job)
    if not restaurant_id:
        abort(400, description="No restaurant selected. Choose a restaurant for this import before approving.")

    # Explicit user action (POST) -> allow creating draft from preview/legacy JSON if needed
    draft_id = _get_or_create_draft_for_job(job_id, allow_create=True)
    if not draft_id:
        abort(400, description="No draft available to approve")

    items = drafts_store.get_draft_items(draft_id) or []

    with db_connect() as conn:
        menu_id = _find_or_create_menu_for_restaurant(conn, int(restaurant_id))
        cur = conn.cursor()

        inserted = 0
        for it in items:
            name = (it.get("name") or "").strip()
            if not name:
                continue

            price_cents = it.get("price_cents")
            if price_cents is None:
                price_cents = _price_to_cents(it.get("price") or it.get("price_text"))

            desc = (it.get("description") or "").strip()

            if not _dedupe_exists(conn, menu_id, name, int(price_cents)):
                cur.execute(
                    "INSERT INTO menu_items (menu_id, name, description, price_cents, is_available) VALUES (?, ?, ?, ?, 1)",
                    (menu_id, name, desc, int(price_cents)),
                )
                inserted += 1

        conn.commit()

    # Mark job approved and sync restaurant linkage
    try:
        update_import_job(job_id, status="approved", restaurant_id=int(restaurant_id))
    except Exception:
        pass
    try:
        drafts_store.save_draft_metadata(draft_id, restaurant_id=int(restaurant_id))
    except Exception:
        pass

    return menu_id, inserted


def discard_draft_for_job(job_id: int) -> int:
    """
    Delete all draft items (keep the draft shell so editor can be used later).
    Returns deleted count.
    """
    _require_drafts_storage()

    # Explicit user action (POST) -> allow creating draft from preview/legacy JSON if needed
    draft_id = _get_or_create_draft_for_job(job_id, allow_create=True)
    if not draft_id:
        return 0

    items = drafts_store.get_draft_items(draft_id) or []
    ids = []
    for it in items:
        if it.get("id") is not None:
            ids.append(it.get("id"))

    deleted = 0
    if ids:
        deleted = drafts_store.delete_draft_items(draft_id, ids)

    try:
        update_import_job(job_id, status="discarded")
    except Exception:
        pass

    return int(deleted)
# ---------- /MISSING HELPERS ----------


# ------------------------
# Health / DB / OCR Health
# ------------------------
@app.get("/ocr/health")
def ocr_health_route():
    # Prefer explicit cmd if set; otherwise look up via PATH
    explicit_cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "") or ""
    which_cmd = shutil.which("tesseract") or shutil.which("tesseract.exe") or ""
    display_cmd = explicit_cmd or which_cmd
    tess_path_exists = (bool(explicit_cmd) and Path(explicit_cmd).exists()) or bool(which_cmd)

    try:
        tess_version = str(pytesseract.get_tesseract_version())
    except Exception:
        tess_version = None

    # App (Flask) context libs — likely False on 3.13
    from importlib.util import find_spec as _find_spec
    have_pandas_app = _find_spec("pandas") is not None
    have_sklearn_app = _find_spec("sklearn") is not None

    # Probe the OCR worker's interpreter (.venv311) to get true Column Mode status
    worker_probe = {
        "active": False,
        "env_ok": False,
        "pandas": False,
        "scikit_learn": False,
        "python": None,
        "path": None,
        "error": None,
    }
    try:
        worker_py = str(ROOT / ".venv311" / "Scripts" / "python.exe")
        if Path(worker_py).exists():
            worker_probe["path"] = worker_py
            import subprocess
            code = (
                "import json,sys\n"
                "res={'python':sys.executable}\n"
                "try:\n"
                " import pandas; res['pandas']=pandas.__version__\n"
                "except Exception:\n"
                " res['pandas']=False\n"
                "try:\n"
                " import sklearn; res['scikit_learn']=sklearn.__version__\n"
                "except Exception:\n"
                " res['scikit_learn']=False\n"
                "print(json.dumps(res))\n"
            )
            out = subprocess.check_output([worker_py, "-c", code], text=True)
            data = json.loads(out.strip())
            worker_probe.update(data)
            worker_probe["env_ok"] = bool(data.get("pandas")) and bool(data.get("scikit_learn"))
            worker_probe["active"] = bool(worker_probe["env_ok"])
    except Exception as e:
        worker_probe["error"] = str(e)

    column_mode = "active" if worker_probe.get("env_ok") else "fallback"

    # Surface OCR worker version string so you can confirm live-reload worked
    worker_version = getattr(ocr_worker, "OCR_WORKER_VERSION", None)

    return jsonify({
        "tesseract": {
            "cmd": display_cmd,
            "found_on_disk": bool(tess_path_exists),
            "version": tess_version
        },
        "poppler": {
            "poppler_path_env": os.getenv("POPPLER_PATH") or "",
            "poppler_bin_present": bool((os.getenv("POPPLER_PATH") or "") and Path(os.getenv("POPPLER_PATH")).exists())
        },
        # Show both app and worker context (useful for debugging)
        "columns": {
            "mode": column_mode,
            "pandas": worker_probe.get("pandas", False),
            "scikit_learn": worker_probe.get("scikit_learn", False),
            "app_context": {"pandas": have_pandas_app, "scikit_learn": have_sklearn_app},
            "worker_python": worker_probe.get("python"),
            "worker_path": worker_probe.get("path"),
            "probe_error": worker_probe.get("error"),
        },
        "ocr_worker_version": worker_version,
        "ocr_lib_health": (ocr_health_lib() if ocr_health_lib else None),

    })

@app.get("/db/health")
def db_health():
    try:
        with db_connect() as conn:
            cur = conn.cursor()
            def count(table):
                cur.execute(f"SELECT COUNT(*) AS c FROM {table}")
                return cur.fetchone()["c"]
            data = {
                "db_path": str(DB_PATH),
                "restaurants": count("restaurants"),
                "menus": count("menus"),
                "menu_items": count("menu_items"),
                "import_jobs": count("import_jobs"),
            }
        return jsonify({"status": "ok", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

# ------------------------
# JSON API (existing)
# ------------------------
@app.get("/api/restaurants")
def get_restaurants():
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM restaurants WHERE active=1").fetchall()
        return jsonify([dict(r) for r in rows])

@app.get("/api/restaurants/<int:rest_id>/menus")
def get_menus(rest_id):
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM menus WHERE restaurant_id=? AND active=1", (rest_id,),
        ).fetchall()
        if not rows:
            abort(404, description="No menus found for that restaurant")
        return jsonify([dict(r) for r in rows])

@app.get("/api/menus/<int:menu_id>/items")
def get_menu_items(menu_id):
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM menu_items WHERE menu_id=? AND is_available=1", (menu_id,),
        ).fetchall()
        if not rows:
            abort(404, description="No items found for that menu")
        return jsonify([dict(r) for r in rows])

# =========================================================
#  NEW: Manual rotate preview (working copy) helpers/routes
# =========================================================

def _work_image_path(job_id: int) -> Path:
    """JPEG working copy (user-rotatable) tied to job id."""
    return RAW_FOLDER / f"job_{job_id}_work.jpg"

def _ensure_work_image(job_id: int, src_path: Path) -> Optional[Path]:
    """
    Ensure a JPEG preview exists for the job:
    - Images: convert/copy to RGB JPEG
    - PDFs: rasterize first page
    
    IMPORTANT: Apply orientation normalization so preview matches OCR input.
    """
    try:
        p = _work_image_path(job_id)
        if p.exists():
            return p

        # Import orientation normalizer from One Brain pipeline
        from storage.ocr_utils import normalize_orientation

        suffix = src_path.suffix.lower()
        if suffix in (".png", ".jpg", ".jpeg"):
            from PIL import Image
            with Image.open(src_path) as im:
                if im.mode != "RGB":
                    im = im.convert("RGB")
                # Apply orientation correction
                im, deg = normalize_orientation(im)
                if deg != 0:
                    print(f"[Preview] job={job_id} orientation corrected by {deg}° for preview")
                p.parent.mkdir(parents=True, exist_ok=True)
                im.save(p, "JPEG", quality=92, optimize=True)
            return p

        if suffix == ".pdf":
            try:
                from pdf2image import convert_from_path
            except Exception:
                return None
            pages = convert_from_path(str(src_path), dpi=200, poppler_path=POPPLER_PATH)
            if not pages:
                return None
            im = pages[0]
            if im.mode != "RGB":
                im = im.convert("RGB")
            # Apply orientation correction
            im, deg = normalize_orientation(im)
            if deg != 0:
                print(f"[Preview] job={job_id} orientation corrected by {deg}° for preview (PDF)")
            p.parent.mkdir(parents=True, exist_ok=True)
            im.save(p, "JPEG", quality=92, optimize=True)
            return p
    except Exception:
        return None
    return None

def _get_work_image_if_any(job_id: int) -> Optional[Path]:
    p = _work_image_path(job_id)
    return p if p.exists() else None

def _path_for_ocr(job_id: int, original: Path) -> Tuple[Path, str]:
    """
    Return (path, type_tag). If the user has rotated a working copy,
    use it and tag as 'image'. Otherwise return the original path.
    """
    p = _get_work_image_if_any(job_id)
    if p and p.exists():
        return p, "image"
    return original, ("image" if original.suffix.lower() in (".jpg", ".jpeg", ".png") else "pdf")

def _score_ocr_text_for_orientation(text: str) -> int:
    """
    Heuristic score: prefer outputs that look like real menu text.
    Higher is better.
    """
    t = (text or "").strip()
    if not t:
        return 0

    total = len(t)

    letters = 0
    digits = 0
    spaces = 0
    other = 0
    for ch in t:
        if ch.isalpha():
            letters += 1
        elif ch.isdigit():
            digits += 1
        elif ch.isspace():
            spaces += 1
        else:
            other += 1

    # Ratio of "good" characters
    good = letters + digits + spaces
    good_ratio = good / max(total, 1)

    # Junk penalty (punctuation/symbol soup tends to spike on wrong rotations)
    junk_ratio = other / max(total, 1)

    # Word count (rough)
    words = [w for w in t.split() if w.strip()]
    word_count = len(words)

    # Simple "menu-ish" signal: presence of prices ($ or digit+dot patterns)
    has_dollar = "$" in t
    has_decimal = False
    for i in range(0, len(t) - 2):
        if t[i].isdigit() and t[i + 1] == "." and t[i + 2].isdigit():
            has_decimal = True
            break

    score = 0

    # Length helps, but cap it
    score += min(total, 2500)

    # Favor readable text, punish junk
    score += int(good_ratio * 2000)
    score -= int(junk_ratio * 1500)

    # Word count helps (cap)
    score += min(word_count, 300) * 10

    # Tiny boosts if it looks like menu pricing
    if has_dollar:
        score += 250
    if has_decimal:
        score += 250

    return int(max(score, 0))


def _auto_rotate_work_image_if_needed(job_id: int, original_image: Path) -> Optional[Path]:
    """
    Ensure a working preview exists, then attempt auto-rotation (0/90/180/270)
    by OCR-scoring each orientation. If a better orientation is found, rotate
    the working JPEG in-place.

    Returns the work image Path (rotated if needed) or None on failure.
    """
    try:
        wp = _ensure_work_image(job_id, original_image) or _get_work_image_if_any(job_id)
        if not wp or not wp.exists():
            return None

        from PIL import Image

        # Load the working JPEG once
        with Image.open(wp) as im0:
            if im0.mode != "RGB":
                im0 = im0.convert("RGB")

            candidates = [
                ("0", 0, im0),
                ("90", 90, im0.rotate(90, expand=True)),
                ("180", 180, im0.rotate(180, expand=True)),
                ("270", 270, im0.rotate(270, expand=True)),
            ]

            best_tag = "0"
            best_angle = 0
            best_score = -1

            for tag, angle, im in candidates:
                try:
                    # OCR directly from PIL image
                    txt = pytesseract.image_to_string(im)
                except Exception:
                    txt = ""
                s = _score_ocr_text_for_orientation(txt)
                if s > best_score:
                    best_score = s
                    best_tag = tag
                    best_angle = angle

            # If best is not 0, rotate the work image in-place
            if best_angle != 0:
                best_im = im0.rotate(best_angle, expand=True)
                if best_im.mode != "RGB":
                    best_im = best_im.convert("RGB")
                best_im.save(wp, "JPEG", quality=92, optimize=True)

        return wp
    except Exception:
        return None


@app.get("/imports/<int:job_id>/preview.jpg")
@login_required
def imports_preview_image(job_id: int):
    """
    Serve/create the rotatable working preview JPEG for this job.
    """
    row = get_import_job(job_id)
    if not row:
        abort(404)
    src = (UPLOAD_FOLDER / (row["filename"] or "")).resolve()
    if not src.exists():
        abort(404)
    p = _ensure_work_image(job_id, src) or _get_work_image_if_any(job_id)
    if not p or not p.exists():
        # As last resort, stream the original if it's already an image
        if src.suffix.lower() in (".jpg", ".jpeg", ".png"):
            return send_file(str(src), mimetype="image/jpeg")
        abort(404)
    return send_file(str(p), mimetype="image/jpeg")

@app.route("/imports/<int:job_id>/rotate", methods=["POST", "GET"])
@login_required
def imports_rotate_image(job_id: int):
    """
    Rotate working preview: dir=left|right OR angle=±90/180.
    - GET/POST both supported.
    - If form/redirect requested, flash+redirect back to import view.
    - Else returns JSON.
    """
    row = get_import_job(job_id)
    if not row:
        abort(404)
    src = (UPLOAD_FOLDER / (row["filename"] or "")).resolve()
    if not src.exists():
        abort(404)

    # Ensure working copy exists first
    wp = _ensure_work_image(job_id, src) or _get_work_image_if_any(job_id)
    if not wp or not wp.exists():
        return jsonify({"ok": False, "error": "preview not available"}), 400

    # Parse direction/angle
    direction = (request.values.get("dir") or "").strip().lower()
    angle_param = request.values.get("angle")
    angle = 0
    if angle_param:
        try:
            angle = int(angle_param)
        except Exception:
            angle = 0
    elif direction in ("left", "counterclockwise", "ccw"):
        angle = 90
    elif direction in ("right", "clockwise", "cw"):
        angle = -90
    else:
        angle = 90  # default: left

    from PIL import Image
    try:
        with Image.open(wp) as im:
            im = im.rotate(angle, expand=True)  # PIL is CCW-positive
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.save(wp, "JPEG", quality=92, optimize=True)
    except Exception as e:
        wants_redirect = request.args.get("redirect") == "1" or request.method == "POST"
        if wants_redirect:
            flash(f"Rotate failed: {e}", "error")
            return redirect(url_for("imports_detail", job_id=job_id))
        return jsonify({"ok": False, "error": str(e)}), 500

    wants_redirect = request.args.get("redirect") == "1" or request.method == "POST"
    if wants_redirect:
        flash("Image rotated.", "success")
        return redirect(url_for("imports_detail", job_id=job_id))
    return jsonify({"ok": True, "angle": angle})


# ------------------------
# Column mapping / preview (Phase 6 pt.9)
# ------------------------
@app.get("/imports/<int:job_id>/mapping")
@login_required
def imports_mapping(job_id: int):
    """
    Read-only column mapping preview for structured imports (CSV/XLSX).

    We rebuild the preview directly from the original source file using the
    One Brain parsers in storage.import_jobs:

      - parse_structured_csv(path)
      - parse_structured_xlsx(path)

    The template expects:
      mapping = {
        "header_map":   { original_header -> canonical_field },
        "sample_rows":  [ {original_header: value, ...}, ... ],
        "column_names": [ "Header 1", "Header 2", ... ],
        "is_structured": bool,
        "file_ext": "csv" | "xlsx",
        "source_type": "structured_csv" | "structured_xlsx" | ...
      }
    """
    row = get_import_job(job_id)
    if not row:
        abort(404, description="Import job not found")

    # sqlite3.Row supports .keys() and item access
    col_names = set(row.keys()) if hasattr(row, "keys") else set()

    filename = (row["filename"] if "filename" in col_names else "") or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    src_type_raw = row["source_type"] if "source_type" in col_names else ""
    src_type = (src_type_raw or "").lower()

    is_structured_ext = ext in ("csv", "xlsx")
    is_structured_type = src_type.startswith("structured_")
    is_structured = is_structured_ext or is_structured_type

    # For now we only support CSV/XLSX in the mapping preview.
    if not is_structured or ext not in ("csv", "xlsx"):
        flash("Column mapping is currently only available for structured CSV/XLSX imports.", "error")
        return redirect(url_for("imports_detail", job_id=job_id))

    source_path_str = row["source_path"] if "source_path" in col_names else ""
    if not source_path_str:
        flash("This structured import does not have a source_path recorded.", "error")
        return redirect(url_for("imports_detail", job_id=job_id))

    src_path = Path(source_path_str)
    if not src_path.exists():
        flash("The original structured import file could not be found on disk.", "error")
        return redirect(url_for("imports_detail", job_id=job_id))

    header_map_canon_to_header: Dict[str, str] = {}
    raw_rows: List[Dict[str, Any]] = []

    try:
        # Use the One Brain helpers to parse the file and recover raw rows
        if ext == "csv" or src_type == "structured_csv":
            _, _, _, header_map_canon_to_header, raw_rows = import_jobs_store.parse_structured_csv(src_path)
        elif ext == "xlsx" or src_type == "structured_xlsx":
            _, _, _, header_map_canon_to_header, raw_rows = import_jobs_store.parse_structured_xlsx(src_path)
        else:
            header_map_canon_to_header = {}
            raw_rows = []
    except Exception as exc:
        app.logger.exception("Failed to build column mapping preview for import job %s", job_id)
        flash(f"Could not build column mapping preview: {exc}", "error")
        return redirect(url_for("imports_detail", job_id=job_id))

    # raw_rows are the original tabular rows keyed by the file's headers.
    column_names: List[str] = []
    sample_rows: List[Dict[str, Any]] = []

    if raw_rows and isinstance(raw_rows, list) and isinstance(raw_rows[0], dict):
        first_row = raw_rows[0]
        column_names = list(first_row.keys())
        sample_rows = raw_rows[:5]

    # Invert canonical -> header mapping into header -> canonical for the UI
    header_map_original_to_canonical: Dict[str, str] = {}
    for canonical, original in (header_map_canon_to_header or {}).items():
        if original:
            header_map_original_to_canonical[original] = canonical

    mapping_ctx = {
        "header_map": header_map_original_to_canonical,
        "sample_rows": sample_rows,
        "column_names": column_names,
        "is_structured": True,
        "file_ext": ext,
        "source_type": src_type,
    }

    return _safe_render(
        "import_mapping.html",
        job=row,
        mapping=mapping_ctx,
    )



# ------------------------
# Import flow: Upload -> Job -> Worker -> Draft JSON (OCR)
# ------------------------
def _save_draft_json(job_id: int, draft: dict) -> str:
    draft_name = f"draft_{job_id}_{uuid.uuid4().hex[:8]}.json"
    abs_path = DRAFTS_FOLDER / draft_name
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(draft, f, indent=2)
    return str(abs_path.relative_to(ROOT)).replace("\\", "/")

# --- OCR helpers: image/PDF → text, then text → draft -----------------
def _ocr_image_to_text(img_path: Path) -> str:
    try:
        from PIL import Image, ImageOps, ImageFilter
        img = Image.open(str(img_path)).convert("L")
        img = ImageOps.autocontrast(img)
        img = img.filter(ImageFilter.SHARPEN)
        return pytesseract.image_to_string(
            img,
            lang=TESSERACT_LANG,
            config=TESSERACT_CONFIG
        ) or ""
    except Exception:
        return ""

def _pdf_to_text(pdf_path: Path) -> str:
    """
    Try to rasterize each PDF page with pdf2image (+poppler) and OCR it.
    Falls back gracefully if pdf2image/poppler are not available.
    """
    try:
        from pdf2image import convert_from_path
        from PIL import ImageOps, ImageFilter
    except Exception:
        return ""

    poppler_path = POPPLER_PATH
    try:
        pages = convert_from_path(str(pdf_path), dpi=300, poppler_path=poppler_path)
    except Exception:
        return ""

    buf = []
    for pg in pages:
        try:
            img = pg.convert("L")
            img = ImageOps.autocontrast(img)
            img = img.filter(ImageFilter.SHARPEN)
            txt = pytesseract.image_to_string(
                img,
                lang=TESSERACT_LANG,
                config=TESSERACT_CONFIG
            )
            if txt:
                buf.append(txt)
        except Exception:
            continue
    return "\n".join(buf).strip()

_price_rx = re.compile(r"""
    (?P<name>.+?)                         # item name
    [\s\-\–\—·:]*                         # optional separators (dashes, middots, colon)
    (?P<price>                            # price at end
        [\$€£]?\s*
        \d{1,3}(?:[.,]\d{3})*             # thousands with . or ,
        (?:[.,]\d{1,2})?                  # optional decimals
        |\$?\d+(?:[.,]\d{1,2})?           # or simple number with decimals
    )\s*$
""", re.X)

def _text_to_draft(text: str, job_id: int, src_file: str, engine_label: str) -> dict:
    """
    Heuristic parser:
      - Default category until a new one detected
      - New category when 'Category: Foo' or an ALL-CAPS line
      - Item line if it ends in a price; otherwise, append to previous item's description
    """
    categories = []
    current_cat = {"name": "Uncategorized", "items": []}
    categories.append(current_cat)

    def new_cat(name: str):
        nonlocal current_cat
        name = (name or "Misc").strip()
        current_cat = {"name": name, "items": []}
        categories.append(current_cat)

    prev_item = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        # Category cues
        if line.lower().startswith("category:"):
            new_cat(line.split(":", 1)[1].strip() or "Misc")
            prev_item = None
            continue
        if len(line) <= 40 and line.isupper() and any(c.isalpha() for c in line):
            new_cat(line.title())
            prev_item = None
            continue

        # Item with trailing price
        m = _price_rx.match(line)
        if m:
            name = m.group("name").strip(" -·:—–")
            price = (m.group("price") or "").strip()
            price_norm = price.replace("$", "").replace("€", "").replace("£", "").replace(" ", "")
            if ("," in price_norm) and ("." not in price_norm):
                price_norm = price_norm.replace(",", ".")
            try:
                p = float(re.sub(r"[^\d.]", "", price_norm))
            except Exception:
                p = 0.0
            current_cat["items"].append({
                "name": name,
                "description": "",
                "sizes": [{"name": "One Size", "price": round(p, 2)}] if p else [],
            })
            prev_item = current_cat["items"][-1]
            continue

        # Otherwise, treat as description line for the last item
        if prev_item:
            desc = (prev_item.get("description") or "").strip()
            prev_item["description"] = (desc + " " + line).strip()
        else:
            current_cat["items"].append({"name": line, "description": "", "sizes": []})
            prev_item = current_cat["items"][-1]

    # Drop empty leading category if it has no items
    categories = [c for c in categories if c.get("items")]

    return {
        "job_id": job_id,
        "source": {"type": "upload", "file": src_file, "ocr_engine": engine_label},
        "extracted_at": _now_iso(),
        "categories": categories or [{"name": "Uncategorized", "items": []}],
    }

# ----- Day 14: helper-backed draft builder (facade-aware) -----
def _build_draft_from_helper(job_id: int, saved_file_path: Path):
    """
    Use storage/ocr_facade.extract_menu_from_pdf to build a draft JSON.

    New façade shape (preferred):

      extract_items_from_path(path) ->

        • (categories_dict, debug_payload)  OR
        • categories_dict

      where categories_dict looks like:
        {
          "categories": [
            {
              "name": "Pizza",
              "items": [
                {
                  "name": "...",
                  "description": "...",
                  "sizes": [
                    {"label": "L", "price": 12.99},
                    {"label": "XL", "price_cents": 1899},
                    ...
                  ],
                  "confidence": 92,
                },
                ...
              ]
            },
            ...
          ],
          "extracted_at": "...Z",
          "source": { "type": "upload", "file": "...", "ocr_engine": "ocr_helper+tesseract" }
        }

    For backward compatibility, we still support the old shape:
      • dict[str, list[items]]  (category_name -> items)
    """
    if extract_items_from_path is None:
        raise RuntimeError("ocr_facade not available")

    debug_payload = None
    cats_raw = extract_items_from_path(str(saved_file_path)) or {}

    # Unpack (categories_dict, debug_payload) vs just categories_dict
    if isinstance(cats_raw, tuple) and len(cats_raw) == 2:
        cats_dict, debug_payload = cats_raw
    else:
        cats_dict, debug_payload = cats_raw, None

    categories: List[Dict[str, Any]] = []
    source_meta: Dict[str, Any] = {}
    extracted_at: Optional[str] = None

    # -------- New façade shape: {"categories": [...], "source": {...}, "extracted_at": "..."} --------
    if isinstance(cats_dict, dict) and "categories" in cats_dict:
        extracted_at = cats_dict.get("extracted_at")
        source_meta = (cats_dict.get("source") or {}) if isinstance(cats_dict.get("source"), dict) else {}

        for cat_obj in (cats_dict.get("categories") or []):
            if not isinstance(cat_obj, dict):
                continue
            cat_name = (cat_obj.get("name") or "Uncategorized").strip() or "Uncategorized"

            out_items: List[Dict[str, Any]] = []
            for it in (cat_obj.get("items") or []):
                if not isinstance(it, dict):
                    continue

                name = (it.get("name") or "").strip() or "Untitled"
                desc = (it.get("description") or "").strip()

                # Normalize sizes: support {"label"/"name", "price"} or {"price_cents"}
                sizes_out: List[Dict[str, Any]] = []
                for s in (it.get("sizes") or []):
                    if not isinstance(s, dict):
                        continue
                    label = (s.get("label") or s.get("name") or "").strip()

                    raw_price = s.get("price", None)
                    if raw_price is None and s.get("price_cents") is not None:
                        try:
                            raw_price = float(s.get("price_cents"))
                            raw_price = raw_price / 100.0
                        except Exception:
                            raw_price = 0.0
                    try:
                        pr = float(raw_price or 0.0)
                    except Exception:
                        pr = 0.0

                    if pr > 0:
                        sizes_out.append({"name": label, "price": round(pr, 2)})

                out_items.append(
                    {
                        "name": name,
                        "description": desc,
                        "sizes": sizes_out,
                        "category": cat_name,
                        "confidence": it.get("confidence"),
                    }
                )

            if out_items:
                categories.append({"name": cat_name, "items": out_items})

    # -------- Backward compat: old dict[category_name] -> [items...] shape --------
    elif isinstance(cats_dict, dict):
        for cat_name, items in (cats_dict or {}).items():
            out_items: List[Dict[str, Any]] = []
            for it in (items or []):
                if not isinstance(it, dict):
                    continue
                name = (it.get("name") or "").strip()
                desc = (it.get("description") or "").strip()
                price = it.get("price")

                if isinstance(price, str):
                    try:
                        price_val = float(price.replace("$", "").strip())
                    except Exception:
                        price_val = 0.0
                elif isinstance(price, (int, float)):
                    # historical behavior: ints were sometimes cents
                    price_val = float(price) / 100.0 if isinstance(price, int) and price >= 100 else float(price)
                else:
                    price_val = 0.0

                sizes = [{"name": "One Size", "price": round(float(price_val), 2)}] if price_val else []

                out_items.append(
                    {
                        "name": name or "Untitled",
                        "description": desc,
                        "sizes": sizes,
                        "category": cat_name,
                        "confidence": it.get("confidence"),
                        "raw": it.get("raw"),
                    }
                )
            if out_items:
                categories.append({"name": cat_name, "items": out_items})

    # -------- Fallback if everything came back empty --------
    if not categories:
        categories = [
            {
                "name": "Uncategorized",
                "items": [
                    {
                        "name": "No items recognized",
                        "description": "OCR returned no items.",
                        "sizes": [],
                    }
                ],
            }
        ]

    # Source + engine metadata (prefer façade's own source block if present)
    engine = (source_meta.get("ocr_engine") if isinstance(source_meta, dict) else None) or "ocr_helper+tesseract"
    source = {
        "type": (source_meta.get("type") if isinstance(source_meta, dict) else None) or "upload",
        "file": (source_meta.get("file") if isinstance(source_meta, dict) else None) or saved_file_path.name,
        "ocr_engine": engine,
    }

    draft_dict = {
        "job_id": job_id,
        "source": source,
        "extracted_at": extracted_at or _now_iso(),
        "categories": categories,
    }
    return draft_dict, debug_payload




def _draft_items_from_draft_json(draft: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert saved draft JSON format:
      {"categories":[{"name": "...", "items":[{...}]}]}
    into a flat list of DB draft_items rows expected by drafts_store.upsert_draft_items.

    Each item may include a '_variants' key with structured variant data
    that upsert_draft_items() will insert into draft_item_variants.
    """
    out: List[Dict[str, Any]] = []

    categories = draft.get("categories") or []
    if not isinstance(categories, list):
        return out

    pos = 1
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        cat_name = (cat.get("name") or "Uncategorized").strip() or "Uncategorized"

        items = cat.get("items") or []
        if not isinstance(items, list):
            continue

        for it in items:
            if not isinstance(it, dict):
                continue

            name = (it.get("name") or "").strip()
            desc = (it.get("description") or "").strip()

            # Build structured variants from sizes array
            sizes = it.get("sizes")
            variants: list = []
            if isinstance(sizes, list) and sizes:
                for vi, s in enumerate(sizes):
                    if not isinstance(s, dict):
                        continue
                    lbl = (s.get("name") or s.get("label") or "").strip()
                    try:
                        pr = float(s.get("price", 0))
                    except Exception:
                        pr = 0.0
                    pr_cents = int(round(pr * 100))
                    if lbl or pr_cents > 0:
                        variants.append({
                            "label": lbl or f"Size {vi + 1}",
                            "price_cents": pr_cents,
                            "kind": "size",
                            "position": vi,
                        })

            # Base price: first variant price or item-level price
            price_val = None
            if variants:
                price_val = variants[0]["price_cents"] / 100.0
            if price_val is None:
                raw_price = it.get("price")
                try:
                    price_val = float(raw_price) if raw_price is not None and str(raw_price).strip() != "" else None
                except Exception:
                    price_val = None

            if not name and not desc and price_val is None:
                continue

            row: Dict[str, Any] = {
                "name": name or "Untitled",
                "description": desc,
                "price": price_val,
                "category": cat_name,
                "position": pos,
            }
            if variants:
                row["_variants"] = variants
            out.append(row)
            pos += 1

    return out


def _draft_items_from_ai_preview(ai_items: list) -> list:
    """Convert analyze_ocr_text() items directly to draft DB rows.

    Bypasses the triple-transformation chain (group->helper->json) that loses
    prices, confidence, and categories.  Same logic as imports_ai_commit()
    but reusable for background imports.

    Each item may include a '_variants' key with structured variant data
    that upsert_draft_items() will insert into draft_item_variants.
    """
    def _to_cents(v) -> int:
        try:
            return int(round(float(v) * 100))
        except Exception:
            return 0

    out: List[Dict[str, Any]] = []
    pos = 1
    for it in ai_items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        desc = (it.get("description") or "").strip() or None
        cat = it.get("category") or None
        conf = it.get("confidence")
        pcs = it.get("price_candidates") or []

        price_cents = 0
        if pcs:
            try:
                price_cents = _to_cents(pcs[0].get("value"))
            except Exception:
                pass

        # Build structured variants from AI preview data
        raw_variants = it.get("variants") or []
        variants: list = []
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

        # Use lowest variant price as base if no price_candidates
        if price_cents == 0 and variants:
            price_cents = min(v["price_cents"] for v in variants)

        row: Dict[str, Any] = {
            "name": name,
            "description": desc,
            "price_cents": int(price_cents),
            "category": cat,
            "position": pos,
            "confidence": int(round(conf * 100)) if isinstance(conf, float) else conf,
        }
        if variants:
            row["_variants"] = variants
        out.append(row)
        pos += 1
    return out


def _build_draft_from_worker(job_id: int, saved_file_path: Path, worker_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert the ocr_worker draft object into the on-disk draft JSON format expected by the rest of the app.

    Critical: preserve categories/items so /imports/raw/<job_id> is not empty and Draft Editor has items.
    """
    from datetime import datetime, timezone

    categories = worker_obj.get("categories")
    if not isinstance(categories, list):
        categories = []

    source = worker_obj.get("source")
    if not isinstance(source, dict):
        source = {}

    # Ensure source has the basics
    source.setdefault("type", "upload")
    source.setdefault("file", saved_file_path.name)
    source.setdefault("ocr_engine", "ocr_worker")

    extracted_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    draft: Dict[str, Any] = {
        "job_id": int(job_id),
        "restaurant_id": worker_obj.get("restaurant_id"),
        "currency": worker_obj.get("currency") or "USD",
        "categories": categories,
        "source": source,
        "extracted_at": extracted_at,
    }
    return draft


def run_ocr_and_make_draft(job_id: int, saved_file_path: Path):
    draft: Any = None
    debug_payload: Any = None
    engine = ""
    helper_error: Optional[str] = None

    try:
        update_import_job(job_id, status="processing")

        # Always create a user-rotatable working preview up-front
        try:
            _ensure_work_image(job_id, saved_file_path)
        except Exception:
            pass

        # Prefer working image (respects later user rotation too if we rerun)
        src_for_ocr, type_tag = _path_for_ocr(job_id, saved_file_path)

        # 0) RETIRED: portal/ocr_worker is legacy and must not be the preferred path.
        # Images should flow through the One Brain pipeline via storage.ocr_facade/build_structured_menu.
        # If facade fails, we will fall back later.
        if type_tag == "image" and False:
            try:
                raw_text, worker_obj = ocr_worker.run_image_pipeline(
                    Path(src_for_ocr),
                    job_id=str(job_id),
                )
                draft = _build_draft_from_worker(
                    job_id,
                    saved_file_path,
                    worker_obj or {},
                )
                engine = "ocr_worker"

                if isinstance(worker_obj, dict):
                    dbg_obj = worker_obj.get("debug")
                    if isinstance(dbg_obj, dict):
                        debug_payload = dbg_obj

            except Exception as e:
                helper_error = f"ocr_worker_failed: {e}"
                draft = None

        # 1) Helper path (typically for PDFs), fallback if image path failed
        if draft is None and extract_items_from_path is not None:
            try:
                draft, debug_payload = _build_draft_from_helper(job_id, saved_file_path)
                if isinstance(draft, dict):
                    engine = (draft.get("source") or {}).get("ocr_engine") or "ocr_helper+tesseract"
                else:
                    engine = "ocr_helper+tesseract"

                # NOTE: segment_document was previously called here for layout_debug
                # but it re-runs the full OCR pipeline (~3min), doubling processing time.
                # The facade already provides text_blocks/preview_blocks which is sufficient.


            except Exception as e:
                helper_error = str(e)
                draft = None
                debug_payload = None

        # 2) Legacy fallback to direct Tesseract text OCR + heuristics
        if draft is None:
            text = ""
            if type_tag == "image":
                engine = "tesseract"
                text = _ocr_image_to_text(src_for_ocr)
            else:
                engine = "tesseract+pdf2image"
                text = _pdf_to_text(saved_file_path)

            if text:
                draft = _text_to_draft(
                    text,
                    job_id,
                    saved_file_path.name,
                    engine or "tesseract",
                )
                debug_payload = {
                    "notes": [f"fallback_engine={engine}", f"len_text={len(text)}"],
                    "items": [],
                    "lines": text.splitlines()[:500],
                }
            else:
                draft = {
                    "job_id": job_id,
                    "source": {
                        "type": "upload",
                        "file": saved_file_path.name,
                        "ocr_engine": engine or "unavailable",
                    },
                    "extracted_at": _now_iso(),
                    "categories": [
                        {
                            "name": "Uncategorized",
                            "items": [
                                {
                                    "name": "OCR not configured",
                                    "description": "Install Tesseract; for PDFs also install pdf2image + Poppler.",
                                    "sizes": [],
                                }
                            ],
                        }
                    ],
                }
                debug_payload = {"notes": ["no_text_extracted"]}

        # ✅ Normalize draft["source"] if it was produced as a JSON string (prevents weird UI/source rendering)
        if isinstance(draft, dict):
            src = draft.get("source")
            if isinstance(src, str):
                try:
                    draft["source"] = json.loads(src)
                except Exception:
                    draft["source"] = {"raw": src}

        rel_draft_path = _save_draft_json(job_id, draft)

        # Save draft_path to DB NOW so _get_or_create_draft_for_job can find it.
        # status="done" is set later, after items are in the DB.
        update_import_job(job_id, draft_path=rel_draft_path)

        try:
            raw_dump = f"(engine={engine})\n"
            if helper_error:
                raw_dump += f"[helper_error] {helper_error}\n"
            (RAW_FOLDER / f"{job_id}.txt").write_text(raw_dump, encoding="utf-8")
        except Exception:
            pass

        # =====================================================================
        # THREE-STRATEGY ITEM EXTRACTION
        # Get clean OCR text (same path as /ai/preview), then try:
        #   1. Claude API extraction (best quality)
        #   2. Heuristic AI (analyze_ocr_text — same as /ai/preview)
        #   3. Legacy draft JSON parsing (last resort)
        # =====================================================================
        items = []
        extraction_strategy = "none"

        # Get clean OCR text via simple Tesseract (same path as /ai/preview)
        clean_ocr_text = ""
        try:
            _suffix = saved_file_path.suffix.lower()
            if _suffix == ".pdf":
                clean_ocr_text = _pdf_to_text(saved_file_path)
            elif _suffix in (".png", ".jpg", ".jpeg"):
                clean_ocr_text = _ocr_image_to_text(src_for_ocr)
            print(f"[Draft] Clean OCR text: {len(clean_ocr_text)} chars")
        except Exception as _ocr_err:
            print(f"[Draft] Clean OCR failed: {_ocr_err}")

        # Strategy 1: Claude API extraction
        if clean_ocr_text and not items:
            try:
                from storage.ai_menu_extract import extract_menu_items_via_claude, claude_items_to_draft_rows
                claude_items = extract_menu_items_via_claude(clean_ocr_text)
                if claude_items:
                    items = claude_items_to_draft_rows(claude_items)
                    extraction_strategy = "claude_api"
                    print(f"[Draft] Strategy 1 (Claude API): {len(items)} items")
            except Exception as _claude_err:
                print(f"[Draft] Strategy 1 (Claude API) failed: {_claude_err}")

        # Strategy 2: Heuristic AI (same as /ai/preview endpoint)
        if clean_ocr_text and not items:
            try:
                doc = analyze_ocr_text(clean_ocr_text, layout=None, taxonomy=None, restaurant_profile=None)
                ai_items = (doc or {}).get("items") or []
                if ai_items:
                    items = _draft_items_from_ai_preview(ai_items)
                    extraction_strategy = "heuristic_ai"
                    print(f"[Draft] Strategy 2 (Heuristic AI): {len(items)} items")
            except Exception as _ai_err:
                print(f"[Draft] Strategy 2 (Heuristic AI) failed: {_ai_err}")

        # Strategy 3: Legacy draft JSON parsing (last resort)
        if not items:
            items = _draft_items_from_draft_json(draft if isinstance(draft, dict) else {})
            extraction_strategy = "legacy_draft_json"
            print(f"[Draft] Strategy 3 (Legacy JSON): {len(items)} items")

        # ✅ CRITICAL (SUCCESS PATH): hydrate DB-backed draft items + save OCR debug payload
        # status="done" is set AFTER items are in the DB so auto-redirect
        # lands on a populated editor.
        try:
            if drafts_store is not None:
                draft_id = _get_or_create_draft_for_job(job_id, allow_create=True)

                if draft_id and hasattr(drafts_store, "upsert_draft_items"):
                    if items:
                        drafts_store.upsert_draft_items(draft_id, items)

                if draft_id and hasattr(drafts_store, "save_ocr_debug"):
                    payload = debug_payload if isinstance(debug_payload, dict) else {}
                    payload.setdefault("import_job_id", int(job_id))
                    payload.setdefault("pipeline", engine or "unknown")
                    payload.setdefault("bridge", "run_ocr_and_make_draft")
                    payload["extraction_strategy"] = extraction_strategy
                    payload["clean_ocr_chars"] = len(clean_ocr_text)
                    drafts_store.save_ocr_debug(draft_id, payload)
        except Exception as _draft_err:
            print(f"[Draft] ERROR creating draft items: {_draft_err}")
            import traceback; traceback.print_exc()

        # Mark done AFTER items are in DB so auto-redirect shows populated editor
        update_import_job(job_id, status="done")


    except Exception as e:
        update_import_job(job_id, status="failed", error=str(e))


# Upload route (JSON API) — returns job id
@app.post("/api/menus/import")
@login_required
def import_menu():
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file field 'file' provided"}), 400
        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "Empty filename"}), 400
        if not allowed_file(file.filename):
            return jsonify({"error": "Unsupported file type. Allowed: jpg, jpeg, png, pdf"}), 400

        base_name = secure_filename(file.filename) or "upload"
        tmp_name = f"{uuid.uuid4().hex[:8]}_{base_name}"
        save_path = UPLOAD_FOLDER / tmp_name
        file.save(str(save_path))

        restaurant_id = _resolve_restaurant_id_from_request()
        job_id = create_import_job(filename=tmp_name, restaurant_id=restaurant_id)

        t = threading.Thread(target=run_ocr_and_make_draft, args=(job_id, save_path), daemon=True)
        t.start()

        return jsonify({"job_id": job_id, "status": "pending", "file": tmp_name, "restaurant_id": restaurant_id}), 200

    except RequestEntityTooLarge:
        return jsonify({"error": "File too large. Try a smaller image or raise MAX_CONTENT_LENGTH."}), 413
    except Exception as e:
        return jsonify({"error": f"Server error while saving upload: {e}"}), 500

# Check job status
@app.get("/api/menus/import/<int:job_id>/status")
@login_required
def import_status(job_id):
    row = get_import_job(job_id)
    if not row:
        abort(404)
    data = dict(row)
    abs_draft = _abs_from_rel(row["draft_path"])
    data["draft_ready"] = bool(abs_draft and abs_draft.exists())
    return jsonify(data)

# ------------------------
# HTML Pages (Portal UI)
# ------------------------
@app.get("/restaurants")
def restaurants_page():
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM restaurants WHERE active=1 ORDER BY id").fetchall()
    return _safe_render("restaurants.html", restaurants=rows)

@app.get("/restaurants/<int:rest_id>/menus")
def menus_page(rest_id):
    with db_connect() as conn:
        rest = conn.execute("SELECT * FROM restaurants WHERE id=?", (rest_id,)).fetchone()
        menus = conn.execute(
            "SELECT * FROM menus WHERE restaurant_id=? AND active=1 ORDER BY id", (rest_id,),
        ).fetchall()
    if not rest:
        abort(404)
    return _safe_render("menus.html", restaurant=rest, menus=menus)

@app.get("/menus/<int:menu_id>/items")
def items_page(menu_id):
    with db_connect() as conn:
        menu = conn.execute("SELECT * FROM menus WHERE id=?", (menu_id,)).fetchone()
        if not menu:
            abort(404)
        rest = conn.execute("SELECT * FROM restaurants WHERE id=?", (menu["restaurant_id"],)).fetchone()
        items = conn.execute(
            "SELECT * FROM menu_items WHERE menu_id=? AND is_available=1 ORDER BY id", (menu_id,),
        ).fetchall()
        return _safe_render("items.html", restaurant=rest, menu=menu, items=items)

# ------------------------
# Day 6: Auth (Login / Logout)
# ------------------------
@app.get("/login")
def login():
    return _safe_render("login.html", error=None, next=request.args.get("next"))

@app.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    nxt = request.form.get("next") or url_for("index")
    if username == DEV_USERNAME and password == DEV_PASSWORD:
        session["user"] = {"username": username, "role": "admin"}
        flash("Welcome back!", "success")
        return redirect(nxt)
    flash("Invalid credentials", "error")
    return redirect(url_for("login", next=request.form.get("next") or ""))

@app.post("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))

# ------------------------
# Dev helper page: simple upload form
# ------------------------
@app.get("/dev/upload")
@login_required
def dev_upload_form():
    return """
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <title>Dev Upload Test</title>
        <style>
          :root { --bg:#0b1220; --panel:#111a2f; --ink:#e8eefc; --muted:#9fb0d1; --line:#1f2a44; --brand:#7aa2ff; --brandH:#5a86f7; }
          * { box-sizing: border-box; }
          body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 0; background: var(--bg); color: var(--ink); }
          .wrap { max-width: 640px; margin: 32px auto; padding: 0 16px; }
          .card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 18px; box-shadow: 0 8px 24px rgba(0,0,0,.25); }
          h2 { margin: 0 0 8px; }
          p { margin: 0 0 12px; color: var(--muted); }
          .btn {
            display: inline-block; padding: 8px 14px; border-radius: 12px;
            border: 1px solid var(--brand); background: var(--brand);
            color: #000; font-weight: 600; cursor: pointer; text-decoration: none;
            transition: background .2s, border-color .2s;
          }
          .btn:hover { background: var(--brandH); border-color: var(--brandH); }
          .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
          .mt-3{ margin-top: .75rem; } .mt-4{ margin-top: 1rem; }
          input[type="file"] { color: #000; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 8px; }
          code { color: #b7cdfb; }
        </style>
      </head>
      <body>
        <div class="wrap">
          <div class="card">
            <h2>Dev Upload Test</h2>
            <p>Pick an image or PDF and submit to <code>/api/menus/import</code>.</p>
            <form class="mt-3" action="/api/menus/import" method="post" enctype="multipart/form-data">
              <input type="file" name="file" accept="image/*,.pdf" required />
              <div class="mt-3 row">
                <button type="submit" class="btn">Upload</button>
                <a href="/import" class="btn">Back to Import</a>
              </div>
            </form>
            <p class="mt-4">Max file size: 20&nbsp;MB. Allowed: PNG, JPG, PDF.</p>
          </div>
        </div>
      </body>
    </html>
    """


@app.get("/test_json_form")
@login_required
def test_json_form():
    """
    Dev-only helper to POST a structured JSON file into /import/json.
    """
    return """
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <title>Dev JSON Import Test</title>
        <style>
          :root { --bg:#0b1220; --panel:#111a2f; --ink:#e8eefc; --muted:#9fb0d1; --line:#1f2a44; --brand:#7aa2ff; --brandH:#5a86f7; }
          * { box-sizing: border-box; }
          body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 0; background: var(--bg); color: var(--ink); }
          .wrap { max-width: 640px; margin: 32px auto; padding: 0 16px; }
          .card { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 18px; box-shadow: 0 8px 24px rgba(0,0,0,.25); }
          h2 { margin: 0 0 8px; }
          p { margin: 0 0 12px; color: var(--muted); }
          .btn {
            display: inline-block; padding: 8px 14px; border-radius: 12px;
            border: 1px solid var(--brand); background: var(--brand);
            color: #000; font-weight: 600; cursor: pointer; text-decoration: none;
            transition: background .2s, border-color .2s;
          }
          .btn:hover { background: var(--brandH); border-color: var(--brandH); }
          .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
          .mt-3{ margin-top: .75rem; } .mt-4{ margin-top: 1rem; }
          input[type="file"] { color: #000; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 8px; }
          code { color: #b7cdfb; }
        </style>
      </head>
      <body>
        <div class="wrap">
          <div class="card">
            <h2>Dev JSON Import Test</h2>
            <p>Upload a structured JSON payload and send it to <code>/import/json</code>.</p>
            <form class="mt-3" action="/import/json" method="post" enctype="multipart/form-data">
              <input type="file" name="json_file" accept="application/json" required />
              <div class="mt-3 row">
                <button type="submit" class="btn">Upload JSON</button>
                <a href="/import" class="btn">Back to Import</a>
              </div>
            </form>
            <p class="mt-4">Expected: JSON that passes the structured item contract.</p>
          </div>
        </div>
      </body>
    </html>
    """


# ------------------------
# **NEW** Import landing page + HTML POST handler
# ------------------------
@app.route("/import", methods=["GET", "POST"], strict_slashes=False)
@login_required
def import_upload():
    """
    Handles uploaded menu files (images or PDFs) and launches the OCR import job.

    GET  -> render the import upload page.
    POST -> save the file, launch OCR job, then redirect to Import Preview.
    """
    # Handle landing-page GET so /import from navbar doesn't 405
    if request.method == "GET":
        return _safe_render("import.html")

    # POST: actual upload handler
    try:
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Please choose a file to upload.", "error")
            return redirect(url_for("import_upload"))

        if not allowed_file(file.filename):
            flash("Unsupported file type. Allowed: JPG, JPEG, PNG, PDF.", "error")
            return redirect(url_for("import_upload"))

        base_name = secure_filename(file.filename) or "upload"
        tmp_name = f"{uuid.uuid4().hex[:8]}_{base_name}"
        save_path = UPLOAD_FOLDER / tmp_name
        file.save(str(save_path))

        restaurant_id = _resolve_restaurant_id_from_request()
        job_id = create_import_job(filename=tmp_name, restaurant_id=restaurant_id)

        # Run OCR asynchronously
        threading.Thread(
            target=run_ocr_and_make_draft, args=(job_id, save_path), daemon=True
        ).start()

        # Flash success with optional restaurant info
        if restaurant_id:
            flash(
                f"Import started for {base_name} (job #{job_id}) — linked to restaurant #{restaurant_id}.",
                "success",
            )
        else:
            flash(
                f"Import started for {base_name} (job #{job_id}). "
                "Tip: assign a restaurant on the import page before approving.",
                "success",
            )

        # ✅ Redirect straight to Import Preview instead of waiting for Draft Editor
        flash("Import complete — review preview below and rotate if needed.", "success")
        return redirect(url_for("imports_view", job_id=job_id))

    except RequestEntityTooLarge:
        flash("File too large. Try a smaller file or raise MAX_CONTENT_LENGTH.", "error")

    except Exception as e:
        flash(f"Server error while saving upload: {e}", "error")

    # On error, bounce back to the same import page
    return redirect(url_for("import_upload"))



# ------------------------
# NEW: Structured CSV import (Phase 6 pt.1)
# ------------------------
@app.post("/import/csv")
@login_required
def import_csv():
    """
    Structured ingestion for CSV menus (bypasses OCR).

    Expected form fields:
      - csv_file: uploaded .csv file
      - restaurant_id: optional (uses session restaurant for customers)

    Flow:
      - Save CSV into uploads/
      - Call storage.import_jobs.create_csv_import_job_from_file(...)
      - Create a DB-backed draft via drafts_store.create_draft_from_structured_items
      - Redirect to Draft Editor (if draft exists) or the import detail page.
    """
    _require_drafts_storage()

    # Ensure storage/import_jobs helpers are present
    if not hasattr(import_jobs_store, "create_csv_import_job_from_file"):
        flash("CSV import helpers are not available yet (storage.import_jobs.create_csv_import_job_from_file missing).", "error")
        return redirect(url_for("import_upload"))

    try:
        # Accept either 'csv_file' (preferred) or fallback to 'file'
        file = request.files.get("csv_file") or request.files.get("file")
        if not file or file.filename == "":
            flash("Please choose a CSV file to upload.", "error")
            return redirect(url_for("import_upload"))

        base_name = secure_filename(file.filename) or "upload.csv"
        if not base_name.lower().endswith(".csv"):
            flash("Structured CSV import currently only accepts .csv files.", "error")
            return redirect(url_for("import_upload"))

        tmp_name = f"{uuid.uuid4().hex[:8]}_{base_name}"
        save_path = UPLOAD_FOLDER / tmp_name
        file.save(str(save_path))

        restaurant_id = _resolve_restaurant_id_from_request()

        # Let the storage layer parse + validate the CSV
        result = import_jobs_store.create_csv_import_job_from_file(
            save_path,
            restaurant_id=restaurant_id,
        )

        job_id = int(result.get("job_id"))
        items = result.get("items") or []
        summary = result.get("summary") or {}
        errors = result.get("errors") or []

        # Build a draft from the structured items using the shared drafts storage
        title = summary.get("title") or f"Imported CSV {datetime.utcnow().date()}"
        create_structured = getattr(
            drafts_store, "create_draft_from_structured_items", None
        )
        draft_id = None
        if callable(create_structured) and items:
            draft = create_structured(
                title=title,
                restaurant_id=restaurant_id,
                items=items,
                source_type="structured_csv",
                # 🔑 link this draft back to import_jobs.id
                source_job_id=job_id,
                source_meta={
                    "filename": base_name,
                    "row_count": summary.get("row_count"),
                    "valid_rows": summary.get("valid_rows"),
                    "invalid_rows": summary.get("invalid_rows"),
                    "job_id": job_id,
                },
            )
            draft_id = int(draft.get("id") or draft.get("draft_id"))



        # Flash a concise summary
        row_count = summary.get("row_count", len(items))
        valid_rows = summary.get("valid_rows", len(items))
        invalid_rows = summary.get("invalid_rows", len(errors))
        msg = f"CSV import created job #{job_id}: {valid_rows} item(s) imported"
        if row_count is not None:
            msg += f" out of {row_count} row(s)"
        if invalid_rows:
            msg += f" ({invalid_rows} row(s) skipped)."
            level = "warning"
        else:
            msg += "."
            level = "success"
        flash(msg, level)

        if errors:
            # Keep the message short; detailed surfacing can be added in the template later.
            flash("Some CSV rows could not be imported. Check your CSV headers and formats.", "warning")

        # Prefer jumping straight into the draft editor if we have a draft id
        if draft_id:
            return redirect(url_for("draft_editor", draft_id=draft_id))

        # Fallback: show the structured job detail
        return redirect(url_for("imports_detail", job_id=job_id))

    except RequestEntityTooLarge:
        flash("CSV file too large. Try a smaller file or raise MAX_CONTENT_LENGTH.", "error")
        return redirect(url_for("import_upload"))
    except Exception as e:
        flash(f"CSV import failed: {e}", "error")
        return redirect(url_for("import_upload"))




# ------------------------
# NEW: Structured XLSX import (Phase 6 pt.3)
# ------------------------
@app.post("/import/xlsx")
@login_required
def import_xlsx():
    """
    Structured ingestion for Excel menus (XLSX, bypasses OCR).

    Expected form fields:
      - xlsx_file: uploaded .xlsx file (preferred)
      - file:      fallback field name
      - restaurant_id: optional (uses session restaurant for customers)

    Flow:
      - Save XLSX into uploads/
      - Call storage.import_jobs.create_xlsx_import_job_from_file(...)
      - Create a DB-backed draft via drafts_store.create_draft_from_structured_items
      - Redirect to Draft Editor (if draft exists) or the import detail page.
    """
    _require_drafts_storage()

    # Ensure storage/import_jobs helpers are present
    if not hasattr(import_jobs_store, "create_xlsx_import_job_from_file"):
        flash(
            "XLSX import helpers are not available yet (storage.import_jobs.create_xlsx_import_job_from_file missing).",
            "error",
        )
        return redirect(url_for("import_upload"))

    try:
        # Accept either 'xlsx_file' (preferred) or fallback to 'file'
        file = request.files.get("xlsx_file") or request.files.get("file")
        if not file or file.filename == "":
            flash("Please choose an XLSX file to upload.", "error")
            return redirect(url_for("import_upload"))

        base_name = secure_filename(file.filename) or "upload.xlsx"
        if not base_name.lower().endswith(".xlsx"):
            flash("Structured Excel import currently only accepts .xlsx files.", "error")
            return redirect(url_for("import_upload"))

        tmp_name = f"{uuid.uuid4().hex[:8]}_{base_name}"
        save_path = UPLOAD_FOLDER / tmp_name
        file.save(str(save_path))

        restaurant_id = _resolve_restaurant_id_from_request()

        # Let the storage layer parse + validate the XLSX
        result = import_jobs_store.create_xlsx_import_job_from_file(
            save_path,
            restaurant_id=restaurant_id,
        )

        job_id = int(result.get("job_id"))
        items = result.get("items") or []
        summary = result.get("summary") or {}
        errors = result.get("errors") or []

        # Build a draft from the structured items using the shared drafts storage
        title = summary.get("title") or f"Imported XLSX {datetime.utcnow().date()}"
        create_structured = getattr(
            drafts_store, "create_draft_from_structured_items", None
        )
        draft_id = None
        if callable(create_structured) and items:
            draft = create_structured(
                title=title,
                restaurant_id=restaurant_id,
                items=items,
                source_type="structured_xlsx",
                # 🔗 link draft → import job using the canonical kwarg
                source_job_id=job_id,
                source_meta={
                    "filename": base_name,
                    "row_count": summary.get("row_count"),
                    "valid_rows": summary.get("valid_rows"),
                    "invalid_rows": summary.get("invalid_rows"),
                    "job_id": job_id,
                },
            )
            draft_id = int(draft.get("id") or draft.get("draft_id"))

        # Flash a concise summary
        row_count = summary.get("row_count", len(items))
        valid_rows = summary.get("valid_rows", len(items))
        invalid_rows = summary.get("invalid_rows", len(errors))
        msg = f"XLSX import created job #{job_id}: {valid_rows} item(s) imported"
        if row_count is not None:
            msg += f" out of {row_count} row(s)"
        if invalid_rows:
            msg += f" ({invalid_rows} row(s) skipped)."
            level = "warning"
        else:
            msg += "."
            level = "success"
        flash(msg, level)

        if errors:
            flash(
                "Some XLSX rows could not be imported. Check your column headers and formats.",
                "warning",
            )

        # Prefer jumping straight into the draft editor if we have a draft id
        if draft_id:
            return redirect(url_for("draft_editor", draft_id=draft_id))

        # Fallback: show the structured job detail
        return redirect(url_for("imports_detail", job_id=job_id))

    except RequestEntityTooLarge:
        flash("XLSX file too large. Try a smaller file or raise MAX_CONTENT_LENGTH.", "error")
        return redirect(url_for("import_upload"))
    except Exception as e:
        flash(f"XLSX import failed: {e}", "error")
        return redirect(url_for("import_upload"))



# ------------------------
# NEW: Structured JSON import (Phase 6 pt.7)
# ------------------------
@app.post("/import/json")
@login_required
def import_json():
    """
    Structured ingestion for JSON menus (bypasses OCR).

    Expected form fields:
      - json_file: uploaded .json file (preferred)
      - file:      fallback field name
      - restaurant_id: optional (uses session restaurant for customers)

    Flow:
      - Save JSON into uploads/
      - Call storage.import_jobs.create_json_import_job_from_file(...)
      - Create a DB-backed draft via drafts_store.create_draft_from_structured_items
      - Redirect to Draft Editor (if draft exists) or the import detail page.
    """
    _require_drafts_storage()

    # Ensure storage/import_jobs helpers are present
    if not hasattr(import_jobs_store, "create_json_import_job_from_file"):
        flash(
            "JSON import helpers are not available yet (storage.import_jobs.create_json_import_job_from_file missing).",
            "error",
        )
        return redirect(url_for("import_upload"))

    try:
        # Accept either 'json_file' (preferred) or fallback to 'file'
        file = request.files.get("json_file") or request.files.get("file")
        if not file or file.filename == "":
            flash("Please choose a JSON file to upload.", "error")
            return redirect(url_for("import_upload"))

        base_name = secure_filename(file.filename) or "upload.json"
        if not base_name.lower().endswith(".json"):
            flash("Structured JSON import currently only accepts .json files.", "error")
            return redirect(url_for("import_upload"))

        tmp_name = f"{uuid.uuid4().hex[:8]}_{base_name}"
        save_path = UPLOAD_FOLDER / tmp_name
        file.save(str(save_path))

        restaurant_id = _resolve_restaurant_id_from_request()

        # Let the storage layer parse + validate the JSON
        result = import_jobs_store.create_json_import_job_from_file(
            save_path,
            restaurant_id=restaurant_id,
        )

        job_id = int(result.get("job_id"))
        items = result.get("items") or []
        summary = result.get("summary") or {}
        errors = result.get("errors") or []

        # Build a draft from the structured items using the shared drafts storage
        title = summary.get("title") or f"Imported JSON {datetime.utcnow().date()}"
        create_structured = getattr(drafts_store, "create_draft_from_structured_items", None)
        draft_id = None
        if callable(create_structured) and items:
            draft = create_structured(
                title=title,
                restaurant_id=restaurant_id,
                items=items,
                source_type="structured_json",
                # 🔑 link this draft back to import_jobs.id (matches CSV/XLSX behavior)
                source_job_id=job_id,
                source_meta={
                    "filename": base_name,
                    "row_count": summary.get("row_count"),
                    "valid_rows": summary.get("valid_rows"),
                    "invalid_rows": summary.get("invalid_rows"),
                    "job_id": job_id,
                },
            )

            raw_id = (draft.get("id") or draft.get("draft_id") or 0)
            draft_id = int(raw_id) if raw_id else None


        # Flash a concise summary
        row_count = summary.get("row_count", len(items))
        valid_rows = summary.get("valid_rows", len(items))
        invalid_rows = summary.get("invalid_rows", len(errors))
        msg = f"JSON import created job #{job_id}: {valid_rows} item(s) imported"
        if row_count is not None:
            msg += f" out of {row_count} row(s)"
        if invalid_rows:
            msg += f" ({invalid_rows} row(s) skipped)."
            level = "warning"
        else:
            msg += "."
            level = "success"
        flash(msg, level)

        if errors:
            flash("Some JSON rows could not be imported. Check your JSON structure and field names.", "warning")

        # Prefer jumping straight into the draft editor if we have a draft id
        if draft_id:
            return redirect(url_for("draft_editor", draft_id=draft_id))

        # Fallback: show the structured job detail
        return redirect(url_for("imports_detail", job_id=job_id))

    except RequestEntityTooLarge:
        flash("JSON file too large. Try a smaller file or raise MAX_CONTENT_LENGTH.", "error")
        return redirect(url_for("import_upload"))
    except Exception as e:
        flash(f"JSON import failed: {e}", "error")
        return redirect(url_for("import_upload"))



# ------------------------
# Imports pages
# ------------------------
@app.get("/imports")
@login_required
def imports():
    jobs = list_import_jobs()
    return _safe_render("imports.html", jobs=jobs)

@app.get("/imports/<int:job_id>")
@login_required
def imports_detail(job_id):
    """Job detail page with actions."""
    row = get_import_job(job_id)
    if not row:
        abort(404)
    draft = None
    if row["draft_path"]:
        abs_path = _abs_from_rel(row["draft_path"])
        if abs_path and abs_path.exists():
            with open(abs_path, "r", encoding="utf-8") as f:
                draft = json.load(f)
    with db_connect() as conn:
        restaurants = conn.execute(
            "SELECT id, name FROM restaurants WHERE active=1 ORDER BY name"
        ).fetchall()

    # NEW: surface preview + rotate endpoints
    preview_url = url_for("imports_preview_image", job_id=job_id)
    rotate_url = url_for("imports_rotate_image", job_id=job_id)

    return _safe_render("import_view.html", job=row, draft=draft, restaurants=restaurants,
                        preview_img_url=preview_url, rotate_action_url=rotate_url)

# === NEW ===
# Visual OCR Blocks Debugger page (renders debug_blocks.html)
@app.get("/debug/blocks/<int:job_id>")
@login_required
def debug_blocks_page(job_id: int):
    """
    Render the overlay debugger for a given import job.
    Template expects:
      - preview_img_url: image to overlay boxes on
      - blocks_json_url: JSON feed with 'preview_blocks' / 'text_blocks'
      - rotate_action_url: rotate handler so users can fix orientation
      - back_url: link back to the import detail page
    """
    row = get_import_job(job_id)
    if not row:
        abort(404)

    # Best-effort: ensure a preview exists
    try:
        src = (UPLOAD_FOLDER / (row["filename"] or "")).resolve()
        if src.exists():
            _ensure_work_image(job_id, src)
    except Exception:
        pass

    return _safe_render(
        "debug_blocks.html",
        job=row,
        preview_img_url=url_for("imports_preview_image", job_id=job_id),
        blocks_json_url=url_for("imports_blocks", job_id=job_id),
        rotate_action_url=url_for("imports_rotate_image", job_id=job_id),
        back_url=url_for("imports_detail", job_id=job_id),
    )


@app.get("/imports/raw/<int:job_id>")
@login_required
def imports_raw(job_id: int):
    """
    DB-first "raw" view for an import job.
    Returns the draft payload synthesized from the drafts storage layer
    (the same source the Draft Editor uses), NOT the legacy draft_path file.
    """
    row = get_import_job(job_id)
    if not row:
        abort(404)

    _require_drafts_storage()
    draft_id = _get_or_create_draft_for_job(job_id)
    if not draft_id:
        return jsonify({"ok": False, "job_id": job_id, "message": "No draft available for this job yet"}), 200

    draft_meta = drafts_store.get_draft(draft_id) if hasattr(drafts_store, "get_draft") else {"id": draft_id}
    items = drafts_store.get_draft_items(draft_id) if hasattr(drafts_store, "get_draft_items") else []

    categories_map: Dict[str, List[Dict[str, Any]]] = {}
    for it in (items or []):
        cat = (it.get("category") or "Uncategorized").strip() or "Uncategorized"
        categories_map.setdefault(cat, []).append(
            {
                "id": it.get("id"),
                "name": it.get("name") or "",
                "description": it.get("description") or "",
                "price_cents": it.get("price_cents") or 0,
                "price": (float(it.get("price_cents") or 0) / 100.0) if it.get("price_cents") else 0.0,
                "confidence": it.get("confidence"),
                "position": it.get("position"),
            }
        )

    categories = [{"name": k, "items": v} for k, v in categories_map.items()]

    payload = {
        "job_id": int(job_id),
        "draft_id": int(draft_id),
        "source": (draft_meta.get("source") if isinstance(draft_meta, dict) else None) or {"type": "db"},
        "extracted_at": (draft_meta.get("created_at") if isinstance(draft_meta, dict) else None) or _now_iso(),
        "categories": categories,
    }
    return jsonify(payload)


# ---- NEW: Segmentation preview bridges (JSON) ----
def _load_debug_for_draft(draft_id: int) -> Dict[str, Any]:
    """Helper to fetch OCR debug payload saved by worker/helper."""
    _require_drafts_storage()
    load_fn = getattr(drafts_store, "load_ocr_debug", None)
    if not load_fn:
        return {}
    dbg = load_fn(draft_id) or {}
    if not isinstance(dbg, dict):
        return {}
    return dbg


def _load_layout_debug_for_draft(draft_id: int) -> Dict[str, Any]:
    """
    Phase 7 — layout/geometry debug payload.
    Expected keys (optional, experimental):
      - blocks
      - proto_sections
      - block_labels
      - geometry_stats
    """
    dbg = _load_debug_for_draft(draft_id)
    layout = dbg.get("layout_debug") or {}
    return layout if isinstance(layout, dict) else {}


@app.get("/drafts/<int:draft_id>/blocks")
@login_required
def drafts_blocks(draft_id: int):
    """
    Returns segmentation overlays for the draft editor:
    {
      preview_blocks: [ {bbox:[x1,y1,x2,y2], block_type, merged_text, lines:[...]}, ... ],
      text_blocks:    [ raw text-blocks if available ],
    }
    """
    dbg = _load_debug_for_draft(draft_id)

    preview_blocks = dbg.get("preview_blocks") or []
    if not isinstance(preview_blocks, list):
        preview_blocks = []

    text_blocks = dbg.get("text_blocks") or dbg.get("blocks") or []
    if not isinstance(text_blocks, list):
        text_blocks = []

    # Defensive: only allow dict blocks (prevents jsonify from choking on weird objects)
    preview_blocks = [b for b in preview_blocks if isinstance(b, dict)]
    text_blocks = [b for b in text_blocks if isinstance(b, dict)]

    return jsonify({
        "ok": True,
        "draft_id": draft_id,
        "preview_blocks": preview_blocks,
        "text_blocks": text_blocks,
    })


@app.get("/imports/<int:job_id>/blocks")
@login_required
def imports_blocks(job_id: int):
    """
    Convenience bridge: look up draft for import job and delegate to /drafts/<id>/blocks.
    """
    _require_drafts_storage()
    @app.get("/imports/<int:job_id>/blocks")
    @login_required
    def imports_blocks(job_id: int):
        """
        Read-only bridge: serve blocks only if a DB draft already exists.
        Must NOT create drafts or trigger OCR.
        """
        _require_drafts_storage()

        row = get_import_job(job_id)
        if not row:
            abort(404)

        data = dict(row)
        draft_id = data.get("draft_id") or data.get("draftId") or data.get("draft")
        try:
            draft_id = int(draft_id) if draft_id is not None else None
        except Exception:
            draft_id = None

        if not draft_id:
            return jsonify({"ok": False, "error": "Draft not ready"}), 404

        return drafts_blocks(draft_id)



# Bridge to Draft Editor (DB-first)
@app.get("/imports/<int:job_id>/draft")
@login_required
def imports_draft(job_id: int):
    """
    Bridge to Draft Editor.

    User intent is explicit here ("Open Draft Editor"), so it is OK to:
      - ensure a DB-backed draft exists for this import job, and
      - link it back onto import_jobs.draft_id when possible.

    This does NOT trigger OCR. It only ensures the editor has a draft.
    """
    row = get_import_job(job_id)
    if not row:
        abort(404)

    data = dict(row)

    # Prefer DB-backed draft id if present on the job row.
    draft_id = data.get("draft_id") or data.get("draftId") or data.get("draft")
    try:
        draft_id = int(draft_id) if draft_id is not None else None
    except Exception:
        draft_id = None

    if draft_id:
        return redirect(url_for("draft_editor", draft_id=draft_id))

    # NEW: On-demand DB draft creation/linking (no OCR)
    try:
        draft_id = _ensure_draft_for_job(job_id, row=row)
    except Exception:
        draft_id = None

    if draft_id:
        return redirect(url_for("draft_editor", draft_id=int(draft_id)))

    # Fallback: legacy file draft (still read-only)
    abs_draft = _abs_from_rel(data.get("draft_path")) if data.get("draft_path") else None
    if abs_draft and abs_draft.exists():
        flash("Legacy draft file is ready, but no DB draft id is linked yet.", "info")
        return redirect(url_for("imports_detail", job_id=job_id))

    flash("Draft not ready yet for the editor. Try Clone Draft, or re-run Finalize.", "error")
    return redirect(url_for("imports_detail", job_id=job_id))




@app.post("/imports/<int:job_id>/set_restaurant")
@login_required
def imports_set_restaurant(job_id: int):
    rid = request.form.get("restaurant_id")
    if not rid:
        flash("Please choose a restaurant.", "error")
        return redirect(url_for("imports_detail", job_id=job_id))
    try:
        restaurant_id = int(rid)
    except Exception:
        flash("Invalid restaurant id.", "error")
        return redirect(url_for("imports_detail", job_id=job_id))

    try:
        update_import_job(job_id, restaurant_id=restaurant_id)
        try:
            _require_drafts_storage()
            draft_id = _get_or_create_draft_for_job(job_id)
            if draft_id:
                drafts_store.save_draft_metadata(draft_id, restaurant_id=restaurant_id)
        except Exception:
            pass
        flash("Linked import to restaurant.", "success")
    except Exception as e:
        flash(f"Failed to link restaurant: {e}", "error")
    return redirect(url_for("imports_detail", job_id=job_id))

@app.post("/imports/<int:job_id>/approve")
@login_required
def imports_approve(job_id: int):
    try:
        menu_id, inserted = approve_draft_to_menu(job_id)
        flash(f"Approved: inserted {inserted} item(s) into menu #{menu_id}.", "success")
        return redirect(url_for("items_page", menu_id=menu_id))
    except Exception as e:
        flash(f"Approve failed: {e}", "error")
        return redirect(url_for("imports_detail", job_id=job_id))

@app.post("/imports/<int:job_id>/discard")
@login_required
def imports_discard(job_id: int):
    try:
        deleted = discard_draft_for_job(job_id)
        flash(f"Discarded draft items ({deleted} removed).", "success")
    except Exception as e:
        flash(f"Discard failed: {e}", "error")
    return redirect(url_for("imports_detail", job_id=job_id))

@app.post("/imports/<int:job_id>/clone")
@login_required
def imports_clone(job_id: int):
    try:
        _require_drafts_storage()
        draft_id = _get_or_create_draft_for_job(job_id)
        if not draft_id:
            flash("No draft available to clone for this import.", "error")
            return redirect(url_for("imports_detail", job_id=job_id))
        if hasattr(drafts_store, "clone_draft"):
            clone = drafts_store.clone_draft(draft_id)
            new_id = int(clone.get("id") or clone.get("draft_id"))
            flash(f"Cloned draft #{draft_id} → #{new_id}.", "success")
            return redirect(url_for("draft_editor", draft_id=new_id))
        else:
            flash("Clone operation is not supported by the drafts storage layer.", "error")
            return redirect(url_for("draft_editor", draft_id=draft_id))
    except Exception as e:
        flash(f"Clone failed: {e}", "error")
        return redirect(url_for("imports_detail", job_id=job_id))

@app.get("/imports/view/<int:job_id>")
@login_required
def imports_view(job_id):
    # Pylance-safe redirect instead of direct function reference
    return redirect(url_for("imports_detail", job_id=job_id))

@app.route("/imports/cleanup", methods=["GET", "POST"])
@login_required
def imports_cleanup():
    with db_connect() as conn:
        rows = conn.execute("SELECT id, filename, COALESCE(status,'') AS st FROM import_jobs").fetchall()
        to_delete = [
            r["id"]
            for r in rows
            if not (UPLOAD_FOLDER / r["filename"]).exists() and r["st"] != "deleted"
        ]
        if to_delete:
            conn.executemany(
                "UPDATE import_jobs SET status='deleted', updated_at=datetime('now') WHERE id=?",
                [(jid,) for jid in to_delete],
            )
            conn.commit()
    flash("Imports cleanup completed.", "success")
    return redirect(url_for("imports"))

@app.post("/imports/<int:job_id>/delete")
@login_required
def imports_delete_job(job_id):
    with db_connect() as conn:
        conn.execute(
            "UPDATE import_jobs SET status='deleted', updated_at=datetime('now') WHERE id=?",
            (job_id,),
        )
        conn.commit()
    flash(f"Job #{job_id} moved to deleted.", "success")
    return redirect(url_for("imports"))


# ------------------------------------------------------------
# Phase 6 pt.1 — Structured CSV import → Drafts
# ------------------------------------------------------------

def _canonical_field_for_header(header: str) -> Optional[str]:
    """
    Map a CSV header to a canonical field name using storage.import_jobs
    HEADER_ALIASES / CANONICAL_FIELDS if available.

    Returns one of:
      - "name", "description", "category", "subcategory",
        "price", "price_cents", "size", "sku"
      - or None if we don't recognize the header.
    """
    h = (header or "").strip().lower()
    if not h:
        return None

    aliases = getattr(import_jobs_store, "HEADER_ALIASES", {}) or {}
    for field, names in aliases.items():
        try:
            if h in {n.lower() for n in names}:
                return field
        except Exception:
            continue

    canon_fields = set(getattr(import_jobs_store, "CANONICAL_FIELDS", []) or [])
    if h in canon_fields:
        return h

    return None


def _price_to_cents_loose(value: Any) -> int:
    """
    Loose parser for price columns in CSV (dollars → cents).

    Accepts things like:
      - "12.99"
      - "$12.99"
      - " 12 "
    Returns 0 on failure and clamps negatives to 0.
    """
    if value is None:
        return 0
    try:
        txt = str(value).strip().replace("$", "")
        cents = int(round(float(txt) * 100))
    except Exception:
        return 0
    return cents if cents > 0 else 0


def _csv_to_structured_items(file_storage) -> List[Dict[str, Any]]:
    """
    Read an uploaded CSV file and normalize rows into structured menu items
    suitable for drafts.create_draft_from_structured_items(...).

    Supported canonical fields (columns can use any alias defined in
    storage.import_jobs.HEADER_ALIASES):

      name (required)
      description
      category
      subcategory
      price          (dollars; we convert via _price_to_cents_loose)
      price_cents    (integer cents)
      size
      sku
    """
    raw = file_storage.read()
    # Basic charset handling; utf-8-sig for common BOM'd exports, else latin-1 fallback
    try:
        text = raw.decode("utf-8-sig")
    except Exception:
        text = raw.decode("latin-1", errors="ignore")

    f = io.StringIO(text)
    reader = csv.DictReader(f)

    if not reader.fieldnames:
        return []

    # Build header → canonical mapping once
    header_map: Dict[str, Optional[str]] = {}
    for col in reader.fieldnames:
        header_map[col] = _canonical_field_for_header(col)

    items: List[Dict[str, Any]] = []

    for row in reader:
        if not isinstance(row, dict):
            continue

        normalized: Dict[str, Any] = {}

        for orig_col, value in row.items():
            canon = header_map.get(orig_col)
            if not canon:
                continue
            normalized[canon] = value

        name = (normalized.get("name") or "").strip()
        if not name:
            # No name → skip this row entirely
            continue

        description = (normalized.get("description") or "").strip()

        subcat = (normalized.get("subcategory") or "").strip() or None
        cat = (normalized.get("category") or "").strip() or None
        category = subcat or cat

        # Price handling: prefer explicit cents column, else parse dollars
        price_cents: int = 0
        if normalized.get("price_cents") not in (None, ""):
            try:
                price_cents = int(str(normalized["price_cents"]).strip())
            except Exception:
                price_cents = 0
        elif normalized.get("price") not in (None, ""):
            price_cents = _price_to_cents_loose(normalized.get("price"))

        # Never allow negative
        if price_cents < 0:
            price_cents = 0

        item: Dict[str, Any] = {
            "name": name,
            "description": description,
            "category": category,
            "subcategory": subcat,
            "price_cents": price_cents,
        }

        # Optional extras we might care about later (ignored by drafts for now)
        size = (normalized.get("size") or "").strip()
        if size:
            item["size_name"] = size

        sku = (normalized.get("sku") or "").strip()
        if sku:
            item["sku"] = sku

        items.append(item)

    return items


@app.post("/api/drafts/import_structured")
@login_required
def import_structured_draft():
    """
    Phase 6 pt.2 — Structured CSV import route (One Brain-backed).

    Flow:
      1) Accept CSV upload via multipart/form-data.
      2) Save CSV to disk (under UPLOAD_FOLDER).
      3) Use storage.import_jobs.create_csv_import_job_from_file(...) to:
         - parse + validate rows via One Brain contracts
         - create an import_jobs row (source_type=structured_csv)
      4) Create a DB-backed draft via storage.drafts.create_draft_from_structured_items.
      5) Return JSON with job_id, draft_id, summary, and redirect_url.

    Request (multipart/form-data):
      - file: CSV file
      - restaurant_id: optional (used to pre-link the draft)
      - title: optional draft title

    Response (JSON on success):
      {
        "ok": true,
        "job_id": 42,
        "draft_id": 123,
        "summary": {...},
        "errors": [...],
        "redirect_url": "/drafts/123/edit"
      }
    """
    try:
        file = request.files.get("file")
        if not file or file.filename == "":
            return jsonify({"ok": False, "error": "No file uploaded."}), 400

        if not file.filename.lower().endswith(".csv"):
            return jsonify(
                {
                    "ok": False,
                    "error": "Only CSV structured imports are supported right now.",
                }
            ), 400

        # Ensure drafts storage is available and supports structured creation
        _require_drafts_storage()
        create_fn = getattr(drafts_store, "create_draft_from_structured_items", None)
        if not callable(create_fn):
            return jsonify(
                {
                    "ok": False,
                    "error": "Structured draft creation is not available in this environment.",
                }
            ), 500

        # Resolve restaurant + title
        restaurant_id = _resolve_restaurant_id_from_request()
        title = (
            (request.form.get("title") or "").strip()
            or f"Structured Import {datetime.utcnow().date()}"
        )

        # --- Phase 6 pt.2: save CSV to disk and create a One Brain import_job ---
        safe_name = secure_filename(file.filename) or "structured.csv"
        unique_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
        csv_path = (UPLOAD_FOLDER / unique_name).resolve()
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        file.save(str(csv_path))

        # Use the One Brain helper to parse + create import_jobs row
        job_result = import_jobs_store.create_csv_import_job_from_file(
            csv_path, restaurant_id=restaurant_id
        )


        job_id = int(job_result.get("job_id") or 0)
        items = job_result.get("items") or []
        errors = job_result.get("errors") or []
        summary = job_result.get("summary") or {}
        job_summary = job_result.get("job_summary") or {}

        if job_id <= 0:
            return jsonify(
                {"ok": False, "error": "Failed to create structured import job."}
            ), 500

        if not items:
            # We DID create a job row, but there were no valid items.
            return jsonify(
                {
                    "ok": False,
                    "job_id": job_id,
                    "error": "CSV parsed but produced no valid structured items.",
                    "summary": summary,
                    "errors": errors,
                }
            ), 400

        # --- Create the DB-backed draft from structured items ---
        source_meta = {
            "filename": file.filename,
            "csv_path": str(csv_path),
            "job_id": job_id,
            "summary": job_summary,
        }

        draft = create_fn(
            title=title,
            restaurant_id=restaurant_id,
            items=items,
            source_type="structured_csv",
            # 🔑 link draft back to this import job
            source_job_id=job_id,
            source_meta=source_meta,
        )
        draft_id = int(draft.get("id") or draft.get("draft_id") or 0)

        if not draft_id:
            return jsonify(
                {
                    "ok": False,
                    "job_id": job_id,
                    "error": "Structured draft creation did not return a draft id.",
                }
            ), 500

        return jsonify(
            {
                "ok": True,
                "job_id": job_id,
                "draft_id": draft_id,
                "summary": summary,
                "errors": errors,
                "redirect_url": url_for("draft_editor", draft_id=draft_id),
            }
        ), 200

    except Exception as e:
        # Log for dev; keep JSON response stable for the UI
        app.logger.exception("Structured import failed")
        return jsonify({"ok": False, "error": f"Structured import failed: {e}"}), 500



# ------------------------
# Serving uploads (secure; block .trash)
# ------------------------
@app.get("/uploads/<path:filename>")
@login_required
def serve_upload(filename):
    requested = (UPLOAD_FOLDER / filename).resolve()

    # Strong containment check: requested must be inside UPLOAD_FOLDER
    try:
        requested.relative_to(UPLOAD_FOLDER.resolve())
    except Exception:
        abort(403)

    # Block anything inside .trash
    try:
        requested.relative_to(TRASH_FOLDER.resolve())
        abort(403)
    except Exception:
        pass

    return send_from_directory(str(UPLOAD_FOLDER), str(requested.relative_to(UPLOAD_FOLDER.resolve())), as_attachment=False)



# ------------------------
# Upload Management (Recycle Bin) + Artifact cleanup
# ------------------------
def _safe_in_uploads(path: Path) -> bool:
    try:
        path.resolve().relative_to(UPLOAD_FOLDER.resolve())
        return True
    except Exception:
        return False


def _is_direct_child_file(p: Path) -> bool:
    return p.parent.resolve() == UPLOAD_FOLDER.resolve() and p.is_file()

def _list_uploads_files():
    files = []
    for p in UPLOAD_FOLDER.iterdir():
        if p.name == ".trash":
            continue
        if p.is_file():
            stat = p.stat()
            files.append({
                "name": p.name,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime),
            })
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files

def _list_trash_files():
    results = []
    try:
        trash_root = TRASH_FOLDER.resolve()
        if not trash_root.exists():
            return results

        for root, _, filenames in os.walk(trash_root, topdown=True):
            try:
                root_path = Path(root).resolve()
            except Exception:
                continue

            for fname in filenames:
                try:
                    p = (root_path / fname).resolve()
                    if not str(p).startswith(str(trash_root)):
                        continue
                    try:
                        st = p.stat()
                        mtime = datetime.fromtimestamp(st.st_mtime)
                        size = st.st_size
                    except FileNotFoundError:
                        continue
                    except Exception:
                        continue

                    try:
                        rel = p.relative_to(trash_root)
                    except Exception:
                        rel = p.name

                    results.append({
                        "trash_path": str(rel).replace("\\", "/"),
                        "name": p.name,
                        "size": size,
                        "modified": mtime,
                        "modified_iso": mtime.isoformat(timespec="seconds"),
                    })
                except Exception:
                    continue
    except Exception:
        return []

    results.sort(key=lambda x: x.get("modified", datetime.min), reverse=True)
    return results

def _batch_trash_dir() -> Path:
    return TRASH_FOLDER / datetime.utcnow().strftime("%Y%m%d_%H%M%S")

def _mark_jobs_for_upload(upload_name: str, new_status: str):
    with db_connect() as conn:
        conn.execute(
            "UPDATE import_jobs SET status=?, updated_at=datetime('now') WHERE filename=?",
            (new_status, upload_name),
        )
        conn.commit()

def _iter_draft_json_files():
    for p in DRAFTS_FOLDER.iterdir():
        if p.name in (".trash", "raw"):
            continue
        if p.is_file() and p.suffix.lower() == ".json":
            yield p

def _trash_draft_file(p: Path, ts: str):
    dest_dir = TRASH_DRAFTS / ts
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(p), str(dest_dir / p.name))
    except Exception:
        pass

def _trash_job_artifacts_for_upload(upload_name: str):
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    u = upload_name.lower()
    for p in _iter_draft_json_files():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            src_file = (((data or {}).get("source") or {}).get("file") or "").strip()
            if Path(src_file).name.lower() == u:
                _trash_draft_file(p, ts)
        except Exception:
            continue
    def _sweep_raw_dir(src_root: Path, trash_root: Path):
        if not src_root.exists():
            return
        dest_dir = trash_root / ts
        dest_dir.mkdir(parents=True, exist_ok=True)
        for p in list(src_root.iterdir()):
            if p.name == ".trash":
                continue
            try:
                name_lower = p.name.lower()
                if u in name_lower:
                    shutil.move(str(p), str(dest_dir / p.name))
            except Exception:
                continue
    _sweep_raw_dir(RAW_FOLDER, TRASH_RAW)
    _sweep_raw_dir(LEGACY_RAW_FOLDER, LEGACY_TRASH_RAW)

def _sweep_all_raw_to_trash() -> int:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    total = 0
    for src_root, trash_root in [(RAW_FOLDER, TRASH_RAW), (LEGACY_RAW_FOLDER, LEGACY_TRASH_RAW)]:
        if not src_root.exists():
            continue
        dest_dir = trash_root / ts
        dest_dir.mkdir(parents=True, exist_ok=True)
        for p in list(src_root.iterdir()):
            if p.name == ".trash":
                continue
            try:
                shutil.move(str(p), str(dest_dir / p.name))
                total += 1
            except Exception:
                continue
    return total

def _sweep_all_drafts_to_trash() -> int:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    total = 0
    for p in list(_iter_draft_json_files()):
        try:
            _trash_draft_file(p, ts)
            total += 1
        except Exception:
            continue
    return total

def _move_to_trash(names: Iterable[str]) -> Iterable[str]:
    """Move given upload filenames into /uploads/.trash/<batch>/ and mark related jobs deleted."""
    batch_dir = _batch_trash_dir()
    batch_dir.mkdir(parents=True, exist_ok=True)

    moved: List[str] = []
    for raw in names:
        name = secure_filename(Path(raw).name)
        if not name:
            continue
        src = (UPLOAD_FOLDER / name).resolve()
        if not _safe_in_uploads(src) or not _is_direct_child_file(src) or not src.exists():
            continue
        dest = batch_dir / name
        try:
            shutil.move(str(src), str(dest))
            moved.append(name)
            _mark_jobs_for_upload(name, "deleted")
            _trash_job_artifacts_for_upload(name)
        except Exception:
            continue
    return moved

def _restore_from_trash(trash_paths: Iterable[str]) -> Iterable[Tuple[str, str]]:
    """Restore files from /uploads/.trash by relative trash paths and mark jobs restored."""
    restored: List[Tuple[str, str]] = []
    for rel in trash_paths:
        rel_path = (TRASH_FOLDER / rel).resolve()
        if not str(rel_path).startswith(str(TRASH_FOLDER.resolve())):
            continue
        if not rel_path.exists() or not rel_path.is_file():
            continue

        original_name = rel_path.name
        dest = UPLOAD_FOLDER / original_name
        if dest.exists():
            base = dest.stem
            ext = dest.suffix
            idx = 1
            while True:
                candidate = UPLOAD_FOLDER / f"{base} (restored {idx}){ext}"
                if not candidate.exists():
                    dest = candidate
                    break
                idx += 1
        try:
            shutil.move(str(rel_path), str(dest))
            _mark_jobs_for_upload(original_name, "restored")
            restored.append((original_name, dest.name))
        except Exception:
            continue
    return restored

def _empty_dir_tree(path: Path) -> int:
    deleted = 0
    if not path.exists():
        return 0
    for child in list(path.iterdir()):
        try:
            if child.is_file():
                child.unlink()
                deleted += 1
            elif child.is_dir():
                count = 0
                for _, _, files in os.walk(child):
                    count += len(files)
                shutil.rmtree(child, ignore_errors=True)
                deleted += count
        except Exception:
            continue
    return deleted

def _sweep_artifacts() -> dict:
    moved = []
    def _move(pattern: str):
        for p in ROOT.glob(f"storage/{pattern}"):
            if p.is_file():
                dest = DRAFTS_FOLDER / p.name
                try:
                    shutil.move(str(p), str(dest))
                    moved.append(str(dest.relative_to(ROOT)).replace("\\", "/"))
                except Exception:
                    pass
    _move("*.jsonl")
    _move("*.tmp")
    _move("*.raw.json")
    _move("*.ocr.txt")
    def _sweep_raw_dir(src_root: Path, trash_root: Path):
        if not src_root.exists():
            return
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        dest_dir = trash_root / ts
        dest_dir.mkdir(parents=True, exist_ok=True)
        for p in list(src_root.iterdir()):
            if p.name == ".trash":
                continue
            try:
                shutil.move(str(p), str(dest_dir / p.name))
                moved.append(str((dest_dir / p.name).relative_to(ROOT)).replace("\\", "/"))
            except Exception:
                pass
    _sweep_raw_dir(RAW_FOLDER, TRASH_RAW)
    _sweep_raw_dir(LEGACY_RAW_FOLDER, LEGACY_TRASH_RAW)
    return {"moved": moved, "count": len(moved)}

@app.get("/uploads")
@login_required
def uploads_page():
    files = _list_uploads_files()
    return _safe_render("uploads.html", files=files)

@app.post("/uploads/delete")
@login_required
def uploads_delete():
    names = request.form.getlist("names")
    if not names:
        abort(400, description="No files selected")
    moved = list(_move_to_trash(names))
    flash(f"Moved {len(moved)} file(s) to Recycle Bin.", "success")
    return redirect(url_for("uploads_page"))


@app.get("/uploads/trash")
@login_required
def uploads_trash_page():
    err_note = ""
    trashed = []
    try:
        trashed = _list_trash_files()
        if not isinstance(trashed, list):
            trashed = []
    except Exception as e:
        err_note = f"Note: failed to enumerate recycle bin ({e.__class__.__name__}). Showing empty list."
        trashed = []
    return _safe_render("uploads_trash.html", trashed=trashed, err_note=err_note)

@app.post("/uploads/restore")
@login_required
def uploads_restore():
    paths = request.form.getlist("trash_paths")
    if not paths:
        abort(400, description="No trash items selected")
    restored = list(_restore_from_trash(paths))
    flash(f"Restored {len(restored)} file(s).", "success")
    return redirect(url_for("uploads_trash_page"))

@app.post("/uploads/empty_trash")
@login_required
def uploads_empty_trash():
    total = 0
    for p in (TRASH_FOLDER, TRASH_DRAFTS, TRASH_RAW, LEGACY_TRASH_RAW):
        total += _empty_dir_tree(p)
    flash(f"Permanently removed {total} file(s) from trash.", "success")
    return redirect(url_for("uploads_trash_page"))

@app.post("/uploads/clean_raw")
@login_required
def uploads_clean_raw():
    count = _sweep_all_raw_to_trash()
    flash(f"Moved {count} raw artifact file(s) to trash.", "success")
    return redirect(url_for("uploads_trash_page"))

@app.post("/uploads/clean_drafts")
@login_required
def uploads_clean_drafts():
    count = _sweep_all_drafts_to_trash()
    flash(f"Moved {count} draft file(s) to trash.", "success")
    return redirect(url_for("uploads_trash_page"))

@app.post("/admin/artifacts/sweep")
@login_required
def artifacts_sweep():
    report = _sweep_artifacts()
    return jsonify({"status": "ok", **report})

# ------------------------
# Draft Review (legacy JSON-file flow) & Publish (kept)
# ------------------------
def _load_draft_json_by_job(job_id: int):
    row = get_import_job(job_id)
    if not row or not row["draft_path"]:
        abort(404, description="Draft not found")
    abs_path = _abs_from_rel(row["draft_path"])
    if not abs_path or not abs_path.exists():
        abort(404, description="Draft file missing on disk")
    with open(abs_path, "r", encoding="utf-8") as f:
        return json.load(f)

def _is_image(name: str) -> bool:
    n = (name or "").lower()
    return n.endswith(".png") or n.endswith(".jpg") or n.endswith(".jpeg")

@app.get("/drafts/<int:job_id>")
@login_required
def draft_review_page(job_id: int):
    draft = _load_draft_json_by_job(job_id)
    with db_connect() as conn:
        restaurants = conn.execute(
            "SELECT id, name FROM restaurants WHERE active=1 ORDER BY name"
        ).fetchall()
    src_file = (draft.get("source", {}) or {}).get("file")
    preview_url = url_for("serve_upload", filename=src_file) if src_file and _is_image(src_file) else None
    return _safe_render("draft_review.html", draft=draft, restaurants=restaurants, preview_url=preview_url)

@app.post("/drafts/<int:job_id>/publish")
@login_required
def publish_draft(job_id: int):
    draft = _load_draft_json_by_job(job_id)
    restaurant_id = request.form.get("restaurant_id")
    menu_name = (request.form.get("menu_name") or "").strip() or f"Imported {datetime.utcnow().date()}"

    if not restaurant_id:
        abort(400, description="restaurant_id is required")

    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO menus (restaurant_id, name, active) VALUES (?, ?, 1)",
            (int(restaurant_id), menu_name),
        )
        menu_id = cur.lastrowid

        for cat in draft.get("categories", []):
            for item in (cat.get("items") or []):
                base_name = (item.get("name") or "").strip() or "Untitled"
                desc = (item.get("description") or "").strip()
                sizes = item.get("sizes") or []

                if sizes:
                    for s in sizes:
                        size_name = (s.get("name") or "").strip()
                        price_val = s.get("price", 0)
                        try:
                            price_cents = int(round(float(price_val) * 100))
                        except Exception:
                            price_cents = 0
                        display_name = f"{base_name} ({size_name})" if size_name else base_name
                        cur.execute(
                            "INSERT INTO menu_items (menu_id, name, description, price_cents, is_available) VALUES (?, ?, ?, ?, 1)",
                            (menu_id, display_name, desc, price_cents),
                        )
                else:
                    price_val = item.get("price", 0)
                    try:
                        price_cents = int(round(float(price_val) * 100))
                    except Exception:
                        price_cents = 0
                    cur.execute(
                        "INSERT INTO menu_items (menu_id, name, description, price_cents, is_available) VALUES (?, ?, ?, ?, 1)",
                        (menu_id, base_name, desc, price_cents),
                    )
        conn.commit()

    try:
        update_import_job(job_id, status="published")
    except Exception:
        pass

    flash(f"Published draft #{job_id} to menu #{menu_id}.", "success")
    return redirect(url_for("items_page", menu_id=menu_id))

# ======================================================================
# Drafts (DB-first): List + Editor + Save + Submit
# ======================================================================

QUALITY_LOW_THRESHOLD = 65  # items below this are considered "low confidence"

def _compute_item_quality(it: dict) -> tuple[int, bool]:
    """
    Compute a 0–100 quality score for a draft item based on:
      - OCR confidence (0–100)
      - Valid price vs missing/zero
      - Category presence/quality
      - Name length sanity
      - Junk-symbol density in the name

    Returns (score, is_low_confidence).
    """
    name = (it.get("name") or "").strip()
    desc = (it.get("description") or "").strip()
    cat = (it.get("category") or "").strip()
    price_cents = it.get("price_cents")
    try:
        conf = int(it.get("confidence")) if it.get("confidence") is not None else None
    except Exception:
        conf = None

    # ---------- Base score ----------
    score = 100

    # --- Confidence component ---
    if conf is None:
        score -= 10
    else:
        if conf < 30:
            score -= 35
        elif conf < 50:
            score -= 25
        elif conf < 70:
            score -= 15
        elif conf < 85:
            score -= 5
        # 85+ → no penalty

    # --- Price component ---
    if price_cents is None:
        # Try to infer from loose fields if present
        from_price = 0
        for key in ("price", "price_text"):
            if it.get(key) is not None:
                try:
                    from_price = int(round(float(str(it[key]).replace("$", "").strip()) * 100))
                    break
                except Exception:
                    continue
        price_cents = from_price

    if not price_cents or price_cents <= 0:
        score -= 20

    # --- Category component ---
    if not cat:
        score -= 15
    else:
        cl = cat.lower()
        if cl in ("uncategorized", "misc", "other"):
            score -= 8

    # --- Name length sanity ---
    nlen = len(name)
    if nlen == 0:
        score -= 40
    elif nlen < 3:
        score -= 25
    elif nlen < 8:
        score -= 5
    elif nlen > 120:
        score -= 30
    elif nlen > 80:
        score -= 15

    # --- Junk-symbol density in name ---
    if name:
        bad_chars = 0
        for ch in name:
            if not (ch.isalnum() or ch.isspace() or ch in "$.,&()/+'-"):
                bad_chars += 1
        junk_ratio = bad_chars / max(len(name), 1)
        if junk_ratio > 0.40:
            score -= 25
        elif junk_ratio > 0.25:
            score -= 15
        elif junk_ratio > 0.15:
            score -= 5

    # Clamp
    if score < 0:
        score = 0
    if score > 100:
        score = 100

    is_low = score < QUALITY_LOW_THRESHOLD
    return int(score), bool(is_low)


@app.get("/drafts")
@login_required
def drafts_list():
    """List drafts (optionally filter by status or restaurant)."""
    _require_drafts_storage()
    status = request.args.get("status") or None
    try:
        restaurant_id = int(request.args.get("restaurant_id")) if request.args.get("restaurant_id") else None
    except Exception:
        restaurant_id = None

    drafts = drafts_store.list_drafts(status=status, restaurant_id=restaurant_id, limit=200, offset=0)
    return _safe_render("drafts.html", drafts=drafts, status=status, restaurant_id=restaurant_id)

@app.get("/drafts/<int:draft_id>/edit")
@login_required
def draft_editor(draft_id: int):
    """Render the Draft Editor UI."""
    _require_drafts_storage()
    draft = drafts_store.get_draft(draft_id)
    if not draft:
        abort(404, description=f"Draft {draft_id} not found")

    items = drafts_store.get_draft_items(draft_id) or []

    # ---- NEW: compute per-item quality + low-confidence flag ----
    low_conf_items = []
    for it in items:
        try:
            score, is_low = _compute_item_quality(it)
        except Exception:
            # Extremely defensive: if anything blows up, fall back to neutral score.
            score, is_low = 70, False
        it["quality"] = score
        it["low_confidence"] = bool(is_low)
        if is_low:
            low_conf_items.append(it)

    categories = sorted({
        (it.get("category") or "").strip()
        for it in items
        if (it.get("category") or "").strip()
    })

    with db_connect() as conn:
        restaurants = conn.execute(
            "SELECT id, name FROM restaurants WHERE active=1 ORDER BY name"
        ).fetchall()

    # ---- NEW: Phase 4 pt.8 structured grouping ----
    category_tree = {}
    flat_groups = {}

    for it in items:
        cat = (it.get("category") or "Uncategorized").strip()
        sub = (it.get("subcategory") or "").strip()  # may be empty if not inferred

        # fallback: no subcategories → treat everything as "" group
        if cat not in category_tree:
            category_tree[cat] = {}

        if not sub:
            sub = ""  # root-level bucket for this category

        category_tree[cat].setdefault(sub, []).append(it)

        # also populate flat (non-nested) grouping
        flat_groups.setdefault(cat, []).append(it)
    return _safe_render(
        "draft_editor.html",
        draft=draft,
        items=items,
        categories=categories,
        restaurants=restaurants,
        low_conf_items=low_conf_items,
        quality_threshold=QUALITY_LOW_THRESHOLD,

        # NEW pt.8 context
        category_tree=category_tree,
        flat_groups=flat_groups,
    )


# --- NEW: Draft status probe for polling ---
@app.get("/drafts/<int:draft_id>/status")
@login_required
def draft_status(draft_id: int):
    _require_drafts_storage()
    d = drafts_store.get_draft(draft_id) or {}
    status = (d.get("status") or "editing")
    return jsonify({"ok": True, "status": status, "draft_id": draft_id})

# --- AI Cleanup route (now supports AJAX JSON or redirect, with status flips) ---
@app.post("/drafts/<int:draft_id>/cleanup")
@login_required
def cleanup_draft(draft_id: int):
    from storage.ai_cleanup import apply_ai_cleanup
    _require_drafts_storage()

    # Detect redirect vs JSON
    ct = (request.headers.get("Content-Type") or "").lower()
    is_form_post = ct.startswith("application/x-www-form-urlencoded") or ct.startswith("multipart/form-data")
    wants_redirect = (
        request.args.get("redirect") == "1"
        or (request.form.get("redirect") == "1" if is_form_post else False)
        or is_form_post
        or (request.headers.get("X-Requested-With") == "fetch" and "text/html" in (request.headers.get("Accept") or ""))
    )

    # Flip to processing for polling UIs
    try:
        drafts_store.save_draft_metadata(int(draft_id), status="processing")
    except Exception:
        pass

    try:
        updated = apply_ai_cleanup(int(draft_id))
        try:
            drafts_store.save_draft_metadata(int(draft_id), status="finalized")
        except Exception:
            pass

        if wants_redirect:
            flash(f"AI cleanup complete: {updated} item(s) updated.", "success")
            return redirect(url_for("draft_editor", draft_id=int(draft_id)))
        return jsonify({"ok": True, "updated": int(updated), "status": "finalized"}), 200

    except Exception as e:
        app.logger.exception("AI cleanup failed")
        try:
            drafts_store.save_draft_metadata(int(draft_id), status="editing")
        except Exception:
            pass
        if wants_redirect:
            flash(f"AI cleanup failed: {e}", "error")
            return redirect(url_for("draft_editor", draft_id=int(draft_id)))
        return jsonify({"ok": False, "error": str(e), "status": "editing"}), 500

@app.post("/drafts/<int:draft_id>/save")
@login_required
def draft_save(draft_id: int):
    """Save title and bulk upsert/delete items. Expects JSON payload."""
    _require_drafts_storage()
    if not request.is_json:
        return jsonify({"ok": False, "error": "Expected JSON payload"}), 400
    payload = request.get_json(silent=True) or {}

    if payload.get("autosave_ping"):
        return jsonify({"ok": True, "saved_at": _now_iso(), "ping": True}), 200

    # 🔒 Validate payload contract (prevents UI/AI drift)
    probe = {
        "draft_id": draft_id,
        "items": payload.get("items") or [],
        # extra fields tolerated by the validator (ignored if present)
        "title": payload.get("title"),
        "restaurant_id": payload.get("restaurant_id"),
        "status": payload.get("status"),
    }
    ok, err = validate_draft_payload(probe)
    if not ok:
        return jsonify({"ok": False, "error": f"schema: {err}"}), 400

    title = (payload.get("title") or "").strip() or None
    items = payload.get("items") or []
    deleted_ids = payload.get("deleted_item_ids") or []
    deleted_variant_ids = payload.get("deleted_variant_ids") or []

    try:
        if title is not None:
            drafts_store.save_draft_metadata(draft_id, title=title)
        upsert_result = drafts_store.upsert_draft_items(draft_id, items)
        deleted_count = 0
        if deleted_ids:
            del_ints = []
            for x in deleted_ids:
                try:
                    del_ints.append(int(x))
                except Exception:
                    continue
            if del_ints:
                deleted_count = drafts_store.delete_draft_items(draft_id, del_ints)
        # Delete orphaned variant rows
        if deleted_variant_ids:
            for vid in deleted_variant_ids:
                try:
                    vid_int = int(vid)
                    drafts_store.delete_variants_by_id([vid_int])
                except Exception:
                    continue
        saved = {
            "ok": True,
            "saved_at": _now_iso(),
            "inserted_ids": upsert_result.get("inserted_ids", []),
            "updated_ids": upsert_result.get("updated_ids", []),
            "deleted_count": deleted_count,
        }
        return jsonify(saved), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.post("/drafts/<int:draft_id>/submit")
@login_required
def draft_submit(draft_id: int):
    """Mark draft as submitted."""
    _require_drafts_storage()
    try:
        drafts_store.submit_draft(draft_id)
        flash(f"Draft #{draft_id} submitted for review.", "success")
        return redirect(url_for("drafts_list"))
    except Exception as e:
        flash(f"Submit failed: {e}", "error")
        return redirect(url_for("draft_editor", draft_id=draft_id))

@app.post("/drafts/<int:draft_id>/publish_now")
@login_required
def draft_publish_now(draft_id: int):
    """
    Approve & publish from the Draft Editor.
    Requires restaurant_id to be assigned (in metadata) or provided in form/json.
    """
    _require_drafts_storage()
    try:
        draft = drafts_store.get_draft(draft_id)
        if not draft:
            flash("Draft not found.", "error")
            return redirect(url_for("drafts_list"))

        rid = draft.get("restaurant_id")
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            rid = payload.get("restaurant_id", rid)
        else:
            rid = request.form.get("restaurant_id", rid)

        try:
            restaurant_id = int(rid) if rid is not None else None
        except Exception:
            restaurant_id = None

        if not restaurant_id:
            flash("Assign a restaurant before publishing.", "error")
            return redirect(url_for("draft_editor", draft_id=draft_id))

        # Day 73: variant-aware publish — expand variants into flat rows
        publish_rows = drafts_store.get_publish_rows(draft_id)
        with db_connect() as conn:
            menu_id = _find_or_create_menu_for_restaurant(conn, int(restaurant_id))
            cur = conn.cursor()
            inserted = 0
            for row in publish_rows:
                name = row.get("name", "")
                desc = row.get("description", "")
                price_cents = row.get("price_cents", 0)
                if not _dedupe_exists(conn, menu_id, name, int(price_cents)):
                    cur.execute(
                        "INSERT INTO menu_items (menu_id, name, description, price_cents, is_available) VALUES (?, ?, ?, ?, 1)",
                        (menu_id, name, desc, int(price_cents)),
                    )
                    inserted += 1
            conn.commit()

        try:
            if hasattr(drafts_store, "approve_publish"):
                drafts_store.approve_publish(draft_id)
            else:
                drafts_store.save_draft_metadata(draft_id, status="published")
        except Exception:
            pass

        flash(f"Published draft #{draft_id} to menu #{menu_id} ({inserted} items).", "success")
        return redirect(url_for("items_page", menu_id=menu_id))
    except Exception as e:
        flash(f"Publish failed: {e}", "error")
        return redirect(url_for("draft_editor", draft_id=draft_id))

@app.post("/drafts/<int:draft_id>/backfill_variants")
@login_required
def draft_backfill_variants(draft_id: int):
    """Merge legacy 'Name (Size)' items into structured parent + variant rows."""
    _require_drafts_storage()
    try:
        draft = drafts_store.get_draft(draft_id)
        if not draft:
            return jsonify({"ok": False, "error": "Draft not found"}), 404
        if draft.get("status") != "editing":
            return jsonify({"ok": False, "error": "Draft is not in editing state"}), 400
        result = drafts_store.backfill_variants_from_names(draft_id)
        return jsonify({
            "ok": True,
            "groups_found": result.get("groups_found", 0),
            "variants_created": result.get("variants_created", 0),
            "items_deleted": result.get("items_deleted", 0),
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/drafts/<int:draft_id>/assign_restaurant")
@login_required
def draft_assign_restaurant(draft_id: int):
    _require_drafts_storage()
    rid = request.form.get("restaurant_id")
    if not rid:
        flash("Please choose a restaurant.", "error")
        return redirect(url_for("draft_editor", draft_id=draft_id))
    try:
        restaurant_id = int(rid)
    except Exception:
        flash("Invalid restaurant id.", "error")
        return redirect(url_for("draft_editor", draft_id=draft_id))
    try:
        drafts_store.save_draft_metadata(draft_id, restaurant_id=restaurant_id)
        flash("Restaurant assigned to draft.", "success")
    except Exception as e:
        flash(f"Failed to assign restaurant: {e}", "error")
    return redirect(url_for("draft_editor", draft_id=draft_id))

# OCR Inspector debug endpoints
@app.get("/drafts/<int:draft_id>/ocr-debug.json")
@login_required
def draft_ocr_debug_json(draft_id: int):
    _require_drafts_storage()
    if not hasattr(drafts_store, "load_ocr_debug"):
        return jsonify({"error": "OCR debug storage not available"}), 404
    dbg = drafts_store.load_ocr_debug(draft_id)
    if not dbg:
        return jsonify({"error": "No OCR debug payload found for this draft"}), 404
    return jsonify(dbg)


@app.get("/drafts/<int:draft_id>/ocr-debug.csv")
@login_required
def draft_ocr_debug_csv(draft_id: int):
    _require_drafts_storage()
    load_fn = getattr(drafts_store, "load_ocr_debug", None)
    if load_fn is None:
        return make_response("OCR debug storage not available", 404)
    dbg = load_fn(draft_id)
    if not dbg:
        return make_response("No OCR debug payload found for this draft", 404)

    rows = []
    items = dbg.get("items") or []
    if items:
        for it in items:
            src = it.get("source") or {}
            rows.append({
                "id": it.get("id"),
                "name": it.get("name"),
                "description": it.get("desc") or it.get("description"),
                "price": it.get("price"),
                "category": it.get("category"),
                "confidence": it.get("confidence"),
                "page": src.get("page"),
                "line_idx": src.get("line_idx"),
                "bbox": json.dumps(src.get("bbox")) if src.get("bbox") is not None else "",
                "matched_rule": src.get("matched_rule"),
            })
    else:
        for a in (dbg.get("assignments") or [])[:200]:
            rows.append({
                "id": "",
                "name": a.get("name"),
                "description": "",
                "price": "",
                "category": a.get("category"),
                "confidence": a.get("score"),
                "page": "",
                "line_idx": "",
                "bbox": "",
                "matched_rule": a.get("reason"),
            })

    buf = io.StringIO()
    fieldnames = ["id", "name", "description", "price", "category", "confidence", "page", "line_idx", "bbox", "matched_rule"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})
    data = buf.getvalue().encode("utf-8-sig")
    resp = make_response(data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="draft_{draft_id}_ocr_debug.csv"'
    return resp

# ------------------------------------------------------------
# NEW: Layout Debug JSON (job -> draft -> stored payload)
# ------------------------------------------------------------

@app.get("/drafts/<int:draft_id>/layout-debug.json")
@login_required
def draft_layout_debug_json(draft_id: int):
    _require_drafts_storage()

    layout = _load_layout_debug_for_draft(draft_id)

    if not layout:
        # Back-compat fallback: older keys may have been stored at root
        dbg = _load_debug_for_draft(draft_id) or {}
        if not isinstance(dbg, dict):
            dbg = {}

        payload = (
            dbg.get("layout_debug")
            or dbg.get("layout")
            or dbg.get("debug_layout")
            or dbg.get("blocks_layout")
            or None
        )
        if isinstance(payload, dict):
            layout = payload


    if not layout:
        return jsonify({
            "ok": True,
            "draft_id": draft_id,
            "note": "No layout_debug payload present yet",
        }), 200

    # If present, meta.orientation should carry your per-page rotation audit trail
    meta = layout.get("meta") if isinstance(layout, dict) else None
    if not isinstance(meta, dict):
        meta = {}

    # Always provide the orientation key so clients can rely on it
    if "orientation" not in meta or not isinstance(meta.get("orientation"), dict):
        meta["orientation"] = {}

    return jsonify({
        "ok": True,
        "draft_id": draft_id,
        "layout_debug": layout,
        "meta": meta,
    }), 200




@app.get("/imports/<int:job_id>/layout-debug.json")
@login_required
def imports_layout_debug_json(job_id: int):
    _require_drafts_storage()
    draft_id = _get_or_create_draft_for_job(job_id)
    if not draft_id:
        return jsonify({"ok": False, "job_id": job_id, "error": "No draft found for job"}), 404
    return draft_layout_debug_json(int(draft_id))


@app.get("/ocr/layout-debug/<int:job_id>.json")
@login_required
def ocr_layout_debug_alias(job_id: int):
    return imports_layout_debug_json(job_id)


# Exporters
@app.get("/drafts/<int:draft_id>/export.csv")
@login_required
def draft_export_csv(draft_id: int):
    _require_drafts_storage()
    draft = drafts_store.get_draft(draft_id) or {}
    items = drafts_store.get_draft_items(draft_id) or []
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["id", "name", "description", "price_cents", "category", "position"])
    writer.writeheader()
    for it in items:
        writer.writerow({
            "id": it.get("id"),
            "name": it.get("name", ""),
            "description": it.get("description", ""),
            "price_cents": it.get("price_cents", 0),
            "category": it.get("category") or "",
            "position": it.get("position") if it.get("position") is not None else ""
        })
    csv_data = buf.getvalue().encode("utf-8-sig")
    resp = make_response(csv_data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="draft_{draft_id}.csv"'
    return resp

@app.get("/drafts/<int:draft_id>/export_variants.csv")
@login_required
def draft_export_csv_variants(draft_id: int):
    """CSV export with variant sub-rows under each parent item."""
    _require_drafts_storage()
    items = drafts_store.get_draft_items(draft_id, include_variants=True) or []
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["type", "id", "name", "description", "price_cents", "category", "kind", "label"])
    for it in items:
        writer.writerow([
            "item",
            it.get("id", ""),
            it.get("name", ""),
            it.get("description", ""),
            it.get("price_cents", 0),
            it.get("category") or "",
            "",
            "",
        ])
        for v in (it.get("variants") or []):
            writer.writerow([
                "variant",
                "",
                "",
                "",
                v.get("price_cents", 0),
                "",
                v.get("kind", "size"),
                v.get("label", ""),
            ])
    csv_data = buf.getvalue().encode("utf-8-sig")
    resp = make_response(csv_data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="draft_{draft_id}_variants.csv"'
    return resp


@app.get("/drafts/<int:draft_id>/export_wide.csv")
@login_required
def draft_export_csv_wide(draft_id: int):
    """CSV export with variant prices as extra columns (one row per item)."""
    _require_drafts_storage()
    items = drafts_store.get_draft_items(draft_id, include_variants=True) or []

    # Collect all unique variant labels across the draft (in order of first appearance)
    seen_labels: dict = {}  # label -> insertion order
    for it in items:
        for v in (it.get("variants") or []):
            lbl = (v.get("label") or "").strip()
            if lbl and lbl not in seen_labels:
                seen_labels[lbl] = len(seen_labels)
    label_order = sorted(seen_labels.keys(), key=lambda x: seen_labels[x])

    buf = io.StringIO()
    writer = csv.writer(buf)
    base_headers = ["id", "name", "description", "price_cents", "category"]
    writer.writerow(base_headers + [f"price_{lbl}" for lbl in label_order])

    for it in items:
        row = [
            it.get("id", ""),
            it.get("name", ""),
            it.get("description", ""),
            it.get("price_cents", 0),
            it.get("category") or "",
        ]
        # Build a label -> price mapping for this item's variants
        vpmap = {}
        for v in (it.get("variants") or []):
            lbl = (v.get("label") or "").strip()
            if lbl:
                vpmap[lbl] = v.get("price_cents", 0)
        # Append variant price columns
        for lbl in label_order:
            row.append(vpmap.get(lbl, ""))
        writer.writerow(row)

    csv_data = buf.getvalue().encode("utf-8-sig")
    resp = make_response(csv_data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="draft_{draft_id}_wide.csv"'
    return resp


@app.get("/drafts/<int:draft_id>/export.json")
@login_required
def draft_export_json(draft_id: int):
    _require_drafts_storage()
    draft = drafts_store.get_draft(draft_id) or {}
    items = drafts_store.get_draft_items(draft_id, include_variants=True) or []

    # Clean items for export: include nested variants array per item
    export_items = []
    for it in items:
        eitem = {
            "id": it.get("id"),
            "name": it.get("name", ""),
            "description": it.get("description", ""),
            "price_cents": it.get("price_cents", 0),
            "category": it.get("category") or "",
            "position": it.get("position"),
        }
        variants = it.get("variants") or []
        if variants:
            eitem["variants"] = [
                {
                    "label": v.get("label", ""),
                    "price_cents": v.get("price_cents", 0),
                    "kind": v.get("kind", "size"),
                }
                for v in variants
            ]
        else:
            eitem["variants"] = []
        export_items.append(eitem)

    payload = {
        "draft_id": draft_id,
        "title": draft.get("title"),
        "restaurant_id": draft.get("restaurant_id"),
        "status": draft.get("status"),
        "items": export_items,
        "exported_at": _now_iso(),
    }

    resp = make_response(json.dumps(payload, indent=2))
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="draft_{draft_id}.json"'
    return resp

@app.get("/drafts/<int:draft_id>/export.xlsx")
@login_required
def draft_export_xlsx(draft_id: int):
    """Excel export with variant sub-rows, formatting, and auto-generated columns."""
    _require_drafts_storage()

    try:
        import openpyxl as xl  # type: ignore[import]
        from openpyxl.styles import Font, PatternFill, Alignment  # type: ignore[import]
    except Exception:
        xl = None

    if xl is None:
        return make_response("openpyxl not installed. pip install openpyxl", 500)

    draft = drafts_store.get_draft(draft_id) or {}
    items = drafts_store.get_draft_items(draft_id, include_variants=True) or []

    # Collect all unique variant labels in first-appearance order
    seen_labels: dict = {}
    for it in items:
        for v in (it.get("variants") or []):
            lbl = (v.get("label") or "").strip()
            if lbl and lbl not in seen_labels:
                seen_labels[lbl] = len(seen_labels)
    label_order = sorted(seen_labels.keys(), key=lambda x: seen_labels[x])

    wb = xl.Workbook()
    ws = wb.active
    ws.title = (draft.get("title") or f"Draft {draft_id}")[:31]

    # -- Styles --
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1a2236", end_color="1a2236", fill_type="solid")
    parent_font = Font(bold=True)
    variant_font = Font(color="666666")
    variant_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")

    # -- Headers --
    base_headers = ["name", "description", "price_cents", "category"]
    variant_headers = [f"price_{lbl}" for lbl in label_order]
    all_headers = base_headers + variant_headers
    ws.append(all_headers)
    for ci, _ in enumerate(all_headers, start=1):
        cell = ws.cell(row=1, column=ci)
        cell.font = header_font
        cell.fill = header_fill

    # -- Data rows --
    for it in items:
        variants = it.get("variants") or []
        vpmap = {}
        for v in variants:
            lbl = (v.get("label") or "").strip()
            if lbl:
                vpmap[lbl] = v.get("price_cents", 0)

        row_data = [
            it.get("name", ""),
            it.get("description", ""),
            it.get("price_cents", 0),
            it.get("category") or "",
        ]
        for lbl in label_order:
            row_data.append(vpmap.get(lbl, ""))
        ws.append(row_data)
        row_num = ws.max_row
        for ci in range(1, len(all_headers) + 1):
            ws.cell(row=row_num, column=ci).font = parent_font

        # Variant sub-rows (indented)
        for v in variants:
            vrow = [
                "  " + (v.get("label") or ""),  # indented label in name column
                v.get("kind", "size"),            # kind in description column
                v.get("price_cents", 0),          # price in price column
                "",                               # no category
            ]
            for _ in label_order:
                vrow.append("")
            ws.append(vrow)
            vrow_num = ws.max_row
            for ci in range(1, len(all_headers) + 1):
                cell = ws.cell(row=vrow_num, column=ci)
                cell.font = variant_font
                cell.fill = variant_fill

    # Auto-width columns
    for ci, _ in enumerate(all_headers, start=1):
        ws.column_dimensions[xl.utils.get_column_letter(ci)].width = 18

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    resp = make_response(out.read())
    resp.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    resp.headers["Content-Disposition"] = f'attachment; filename="draft_{draft_id}.xlsx"'
    return resp


@app.get("/drafts/<int:draft_id>/export_by_category.xlsx")
@login_required
def draft_export_xlsx_by_category(draft_id: int):
    """Excel export with one sheet per category.  Each sheet has variant sub-rows."""
    _require_drafts_storage()

    try:
        import openpyxl as xl  # type: ignore[import]
        from openpyxl.styles import Font, PatternFill  # type: ignore[import]
    except Exception:
        xl = None

    if xl is None:
        return make_response("openpyxl not installed. pip install openpyxl", 500)

    draft = drafts_store.get_draft(draft_id) or {}
    items = drafts_store.get_draft_items(draft_id, include_variants=True) or []

    # Group items by category
    cat_map: dict = {}  # category -> list of items
    for it in items:
        cat = (it.get("category") or "Uncategorized").strip()
        cat_map.setdefault(cat, []).append(it)

    # Styles
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1a2236", end_color="1a2236", fill_type="solid")
    parent_font = Font(bold=True)
    variant_font = Font(color="666666")
    variant_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")

    wb = xl.Workbook()
    wb.remove(wb.active)  # remove default empty sheet

    for cat_name in sorted(cat_map.keys()):
        cat_items = cat_map[cat_name]
        sheet_title = cat_name[:31] or "Uncategorized"
        ws = wb.create_sheet(title=sheet_title)

        # Collect variant labels for this category
        seen_labels: dict = {}
        for it in cat_items:
            for v in (it.get("variants") or []):
                lbl = (v.get("label") or "").strip()
                if lbl and lbl not in seen_labels:
                    seen_labels[lbl] = len(seen_labels)
        label_order = sorted(seen_labels.keys(), key=lambda x: seen_labels[x])

        base_headers = ["name", "description", "price_cents"]
        variant_headers = [f"price_{lbl}" for lbl in label_order]
        all_headers = base_headers + variant_headers

        ws.append(all_headers)
        for ci, _ in enumerate(all_headers, start=1):
            cell = ws.cell(row=1, column=ci)
            cell.font = header_font
            cell.fill = header_fill

        for it in cat_items:
            variants = it.get("variants") or []
            vpmap = {}
            for v in variants:
                lbl = (v.get("label") or "").strip()
                if lbl:
                    vpmap[lbl] = v.get("price_cents", 0)

            row_data = [
                it.get("name", ""),
                it.get("description", ""),
                it.get("price_cents", 0),
            ]
            for lbl in label_order:
                row_data.append(vpmap.get(lbl, ""))
            ws.append(row_data)
            row_num = ws.max_row
            for ci in range(1, len(all_headers) + 1):
                ws.cell(row=row_num, column=ci).font = parent_font

            for v in variants:
                vrow = [
                    "  " + (v.get("label") or ""),
                    v.get("kind", "size"),
                    v.get("price_cents", 0),
                ]
                for _ in label_order:
                    vrow.append("")
                ws.append(vrow)
                vrow_num = ws.max_row
                for ci in range(1, len(all_headers) + 1):
                    cell = ws.cell(row=vrow_num, column=ci)
                    cell.font = variant_font
                    cell.fill = variant_fill

        for ci, _ in enumerate(all_headers, start=1):
            ws.column_dimensions[xl.utils.get_column_letter(ci)].width = 18

    # If no categories at all, create a placeholder sheet
    if not cat_map:
        ws = wb.create_sheet(title="Empty")
        ws.append(["No items"])

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    resp = make_response(out.read())
    resp.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    resp.headers["Content-Disposition"] = f'attachment; filename="draft_{draft_id}_by_category.xlsx"'
    return resp


# ---------------------------------------------------------------------------
# POS Export Templates (Day 80)
# ---------------------------------------------------------------------------

def _validate_draft_for_export(items):
    """Pre-export validation: returns list of warning dicts."""
    warnings = []
    for it in items:
        item_id = it.get("id")
        name = it.get("name", "")
        price = it.get("price_cents", 0)
        cat = it.get("category") or ""
        variants = it.get("variants") or []

        if not price and not variants:
            warnings.append({
                "item_id": item_id,
                "name": name,
                "type": "missing_price",
                "message": f"Item '{name}' has no price and no variants",
            })
        if not cat:
            warnings.append({
                "item_id": item_id,
                "name": name,
                "type": "missing_category",
                "message": f"Item '{name}' has no category",
            })
        if not name.strip():
            warnings.append({
                "item_id": item_id,
                "name": name,
                "type": "missing_name",
                "message": "Item has no name",
            })
        for v in variants:
            if not v.get("price_cents"):
                warnings.append({
                    "item_id": item_id,
                    "name": name,
                    "type": "variant_missing_price",
                    "message": f"Variant '{v.get('label','')}' on '{name}' has no price",
                })
    return warnings


def _format_price_dollars(cents):
    """Convert cents to dollar string: 1299 -> '12.99'."""
    if not cents:
        return "0.00"
    return f"{int(cents) / 100:.2f}"


@app.get("/drafts/<int:draft_id>/export/validate")
@login_required
def draft_export_validate(draft_id: int):
    """Pre-export validation: returns warnings for items missing data."""
    _require_drafts_storage()
    items = drafts_store.get_draft_items(draft_id, include_variants=True) or []
    warnings = _validate_draft_for_export(items)
    return jsonify({
        "draft_id": draft_id,
        "item_count": len(items),
        "variant_count": sum(len(it.get("variants") or []) for it in items),
        "warnings": warnings,
        "warning_count": len(warnings),
    })


@app.get("/drafts/<int:draft_id>/export/preview")
@login_required
def draft_export_preview(draft_id: int):
    """Export preview: returns formatted output as JSON for pre-download review."""
    _require_drafts_storage()
    fmt = request.args.get("format", "generic_pos")
    items = drafts_store.get_draft_items(draft_id, include_variants=True) or []
    draft = drafts_store.get_draft(draft_id) or {}
    warnings = _validate_draft_for_export(items)

    if fmt == "square":
        rows = _build_square_rows(items)
        # Return first 50 rows as preview
        preview_lines = []
        headers = ["Token", "Item Name", "Description", "Category",
                    "Price", "Modifier Set Name", "Modifier Name", "Modifier Price"]
        preview_lines.append(",".join(headers))
        for r in rows[:50]:
            preview_lines.append(",".join(str(c) for c in r))
        content = "\n".join(preview_lines)
    elif fmt == "toast":
        rows = _build_toast_rows(items)
        headers = ["Menu Group", "Menu Item", "Base Price",
                    "Option Group", "Option", "Option Price"]
        preview_lines = [",".join(headers)]
        for r in rows[:50]:
            preview_lines.append(",".join(str(c) for c in r))
        content = "\n".join(preview_lines)
    else:
        payload = _build_generic_pos_json(items, draft)
        content = json.dumps(payload, indent=2)

    return jsonify({
        "format": fmt,
        "content": content,
        "item_count": len(items),
        "warnings": warnings,
        "truncated": fmt != "generic_pos" and len(items) > 50,
    })


def _build_square_rows(items):
    """Build Square CSV rows: items + modifier groups from variants.

    Square import format:
      Token, Item Name, Description, Category, Price,
      Modifier Set Name, Modifier Name, Modifier Price

    Items without variants: single row with base price.
    Items with variants: parent row (price=base), then modifier rows
    grouped by kind (e.g., "Size", "Combo Add-on").
    """
    rows = []
    for it in items:
        name = it.get("name", "")
        desc = it.get("description") or ""
        cat = it.get("category") or ""
        price = _format_price_dollars(it.get("price_cents", 0))
        variants = it.get("variants") or []

        if not variants:
            rows.append([
                "item", name, desc, cat, price, "", "", "",
            ])
        else:
            # Parent row with base price
            rows.append([
                "item", name, desc, cat, price, "", "", "",
            ])
            # Group variants by kind for modifier sets
            kind_groups: dict = {}
            for v in variants:
                k = v.get("kind", "size")
                kind_groups.setdefault(k, []).append(v)

            kind_labels = {
                "size": "Size",
                "combo": "Combo Add-on",
                "flavor": "Flavor",
                "style": "Style",
                "other": "Option",
            }
            for kind, vlist in kind_groups.items():
                set_name = kind_labels.get(kind, "Option")
                for v in vlist:
                    mod_price = _format_price_dollars(v.get("price_cents", 0))
                    rows.append([
                        "modifier", name, "", "", "",
                        set_name, v.get("label", ""), mod_price,
                    ])
    return rows


def _build_toast_rows(items):
    """Build Toast CSV rows: menu group/item/option hierarchy.

    Toast import format:
      Menu Group, Menu Item, Base Price,
      Option Group, Option, Option Price

    Items map to Menu Items under their category (Menu Group).
    Variants map to Options under Option Groups (by kind).
    """
    rows = []
    for it in items:
        name = it.get("name", "")
        cat = it.get("category") or "Uncategorized"
        price = _format_price_dollars(it.get("price_cents", 0))
        variants = it.get("variants") or []

        if not variants:
            rows.append([cat, name, price, "", "", ""])
        else:
            # Parent row
            rows.append([cat, name, price, "", "", ""])
            # Group by kind
            kind_groups: dict = {}
            for v in variants:
                k = v.get("kind", "size")
                kind_groups.setdefault(k, []).append(v)

            kind_labels = {
                "size": "Size",
                "combo": "Combo Add-on",
                "flavor": "Flavor",
                "style": "Style",
                "other": "Option",
            }
            for kind, vlist in kind_groups.items():
                group_name = kind_labels.get(kind, "Option")
                for v in vlist:
                    opt_price = _format_price_dollars(v.get("price_cents", 0))
                    rows.append([
                        "", "", "", group_name,
                        v.get("label", ""), opt_price,
                    ])
    return rows


def _build_generic_pos_json(items, draft=None):
    """Build Generic POS JSON: universal item/variant/modifier schema.

    Structure:
      { menu: { id, title, categories: [
          { name, items: [
              { name, description, base_price, modifiers: [
                  { group, name, price }
              ]}
          ]}
      ]}, metadata: { ... } }
    """
    draft = draft or {}
    cat_map: dict = {}
    for it in items:
        cat = it.get("category") or "Uncategorized"
        cat_map.setdefault(cat, []).append(it)

    categories = []
    for cat_name in sorted(cat_map.keys()):
        cat_items = []
        for it in cat_map[cat_name]:
            item_entry = {
                "name": it.get("name", ""),
                "description": it.get("description") or "",
                "base_price": _format_price_dollars(it.get("price_cents", 0)),
            }
            variants = it.get("variants") or []
            if variants:
                modifiers = []
                for v in variants:
                    kind_labels = {
                        "size": "Size",
                        "combo": "Combo Add-on",
                        "flavor": "Flavor",
                        "style": "Style",
                        "other": "Option",
                    }
                    modifiers.append({
                        "group": kind_labels.get(v.get("kind", "size"), "Option"),
                        "name": v.get("label", ""),
                        "price": _format_price_dollars(v.get("price_cents", 0)),
                    })
                item_entry["modifiers"] = modifiers
            else:
                item_entry["modifiers"] = []
            cat_items.append(item_entry)
        categories.append({"name": cat_name, "items": cat_items})

    return {
        "menu": {
            "id": draft.get("id"),
            "title": draft.get("title") or "",
            "categories": categories,
        },
        "metadata": {
            "exported_at": _now_iso(),
            "format": "generic_pos",
            "version": "1.0",
            "item_count": len(items),
            "category_count": len(categories),
        },
    }


@app.get("/drafts/<int:draft_id>/export_square.csv")
@login_required
def draft_export_square_csv(draft_id: int):
    """Square POS CSV export: items + modifier groups."""
    _require_drafts_storage()
    items = drafts_store.get_draft_items(draft_id, include_variants=True) or []
    rows = _build_square_rows(items)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Token", "Item Name", "Description", "Category",
                      "Price", "Modifier Set Name", "Modifier Name", "Modifier Price"])
    for r in rows:
        writer.writerow(r)

    csv_data = buf.getvalue().encode("utf-8-sig")
    resp = make_response(csv_data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="draft_{draft_id}_square.csv"'
    return resp


@app.get("/drafts/<int:draft_id>/export_toast.csv")
@login_required
def draft_export_toast_csv(draft_id: int):
    """Toast POS CSV export: menu group / item / option hierarchy."""
    _require_drafts_storage()
    items = drafts_store.get_draft_items(draft_id, include_variants=True) or []
    rows = _build_toast_rows(items)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Menu Group", "Menu Item", "Base Price",
                      "Option Group", "Option", "Option Price"])
    for r in rows:
        writer.writerow(r)

    csv_data = buf.getvalue().encode("utf-8-sig")
    resp = make_response(csv_data)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="draft_{draft_id}_toast.csv"'
    return resp


@app.get("/drafts/<int:draft_id>/export_pos.json")
@login_required
def draft_export_pos_json(draft_id: int):
    """Generic POS JSON export: universal item/variant/modifier schema."""
    _require_drafts_storage()
    draft = drafts_store.get_draft(draft_id) or {}
    items = drafts_store.get_draft_items(draft_id, include_variants=True) or []
    payload = _build_generic_pos_json(items, draft)

    resp = make_response(json.dumps(payload, indent=2))
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="draft_{draft_id}_pos.json"'
    return resp


# ------------------------
# NEW: Fix descriptions endpoint used by the editor button
# ------------------------
def _clean_desc(name: str, desc: Optional[str]) -> Optional[str]:
    """
    Heuristics:
      - Trim, collapse whitespace.
      - Drop leading repeats of the name: "Pepperoni: pepperoni slices..." -> "pepperoni slices..."
      - Remove surrounding quotes and stray punctuation at ends.
      - If desc becomes empty, return None.
    """
    nm = (name or "").strip()
    d = (desc or "").strip()

    if not d:
        return None

    # If description begins with the name (case-insensitive), remove that prefix + common separators
    lowered = d.lower()
    nm_low = nm.lower()
    prefixes = [f"{nm_low} - ", f"{nm_low} — ", f"{nm_low} – ", f"{nm_low}: ", f"{nm_low} —", f"{nm_low} –", f"{nm_low}:"]
    for pre in prefixes:
        if lowered.startswith(pre):
            d = d[len(pre):].lstrip()
            lowered = d.lower()
            break

    # Strip wrapping quotes/parens
    d = d.strip(" '\"\t\r\n")
    # Fix bad spacing around punctuation
    d = re.sub(r"\s+([,.;:!?])", r"\1", d)
    d = re.sub(r"\s{2,}", " ", d).strip()

    return d or None

def _split_name_into_desc_if_needed(name: str, desc: Optional[str]) -> Tuple[str, Optional[str]]:
    """
    If description is empty but name contains " - " or " — ", split once and move the tail to description.
    Keep conservative: only when the left part isn't too long (title-like) and right part has letters.
    """
    nm = (name or "").strip()
    if (desc or "").strip():
        return nm, (desc or None)

    for sep in [" - ", " — ", " – ", ":", " · "]:
        if sep in nm:
            left, right = nm.split(sep, 1)
            left, right = left.strip(), right.strip()
            if left and right and any(c.isalpha() for c in right) and len(left) <= 80:
                return left, right
    return nm, (desc or None)

@app.route("/drafts/<int:draft_id>/fix-descriptions", methods=["POST", "GET"])
@login_required
def fix_descriptions_for_draft(draft_id: int):
    """
    Clean up funky descriptions in-place. Safe, idempotent heuristics.

    Behavior:
      - GET  → redirect back to editor (no mutation).
      - POST (AJAX/JSON) → returns JSON {ok, updated_count} (editor JS can reload).
      - POST (regular form OR ?redirect=1) → flash + redirect back to editor (no JSON blank page).
    """
    _require_drafts_storage()

    if request.method == "GET":
        return redirect(url_for("draft_editor", draft_id=draft_id))

    # --- detect if caller wants redirect instead of JSON ---
    ct = (request.headers.get("Content-Type") or "").lower()
    is_form_post = ct.startswith("application/x-www-form-urlencoded") or ct.startswith("multipart/form-data")
    wants_redirect = (
        request.args.get("redirect") == "1"
        or (request.form.get("redirect") == "1" if is_form_post else False)
        or is_form_post
    )

    items = drafts_store.get_draft_items(draft_id) or []
    updates = []

    for it in items:
        _id = it.get("id")
        if _id is None:
            continue
        name = (it.get("name") or "").strip()
        desc = it.get("description")
        # Optionally split long names into name+desc if desc is empty
        new_name, maybe_desc = _split_name_into_desc_if_needed(name, desc)
        # Always run the desc cleaner
        new_desc = _clean_desc(new_name, maybe_desc)

        # Only push updates when something has changed
        changed = False
        upd = {"id": int(_id)}

        if new_name != name:
            upd["name"] = new_name
            changed = True
        # Normalize empty -> None; DB layer should treat None as NULL
        norm_desc = new_desc if (new_desc and new_desc.strip()) else None
        # Only update if different from current (normalize current too)
        cur_norm = (desc or None)
        if (norm_desc or None) != (cur_norm or None):
            upd["description"] = norm_desc
            changed = True

        if changed:
            updates.append(upd)

    updated_count = 0
    if updates:
        try:
            res = drafts_store.upsert_draft_items(draft_id, updates)
            updated_count = len(res.get("updated_ids", [])) + len(res.get("inserted_ids", []))
            # bump updated_at
            try:
                ds = drafts_store.get_draft(draft_id) or {}
                drafts_store.save_draft_metadata(draft_id, title=ds.get("title"))
            except Exception:
                pass
        except Exception as e:
            if wants_redirect:
                flash(f"Description cleanup failed: {e}", "error")
                return redirect(url_for("draft_editor", draft_id=draft_id))
            return jsonify({"ok": False, "error": f"update failed: {e}"}), 500

    if wants_redirect:
        flash(f"Cleaned descriptions — {int(updated_count)} item(s) updated.", "success")
        return redirect(url_for("draft_editor", draft_id=draft_id))

    return jsonify({"ok": True, "updated_count": int(updated_count)}), 200

def _ensure_draft_for_job(job_id: int, row=None) -> Optional[int]:
    """
    Ensure there is a DB-backed draft for an import job.

    Strategy:
      1) Use existing helper _get_or_create_draft_for_job(job_id)
      2) If legacy JSON exists (import_jobs.draft_path), upgrade it into a DB draft
         using drafts_store.create_draft_from_structured_items(...)
      3) Otherwise attempt to create via drafts_store if it exposes
         create_draft_from_import / create_draft_from_import_job.

    This function does NOT run OCR.
    """
    try:
        _require_drafts_storage()
    except Exception:
        return None

    # First: try the canonical helper
    try:
        did = _get_or_create_draft_for_job(job_id)
        if did:
            did_int = int(did)
            try:
                update_import_job(job_id, draft_id=did_int)
            except Exception:
                pass
            return did_int
    except Exception:
        pass

    filename = ""
    source_type = ""
    restaurant_id = None
    legacy_draft_path = None

    try:
        if row is not None:
            filename = (row["filename"] or "").strip()
            try:
                source_type = (row.get("source_type") or "").strip()
            except Exception:
                source_type = ""
            try:
                legacy_draft_path = (row.get("draft_path") or "").strip() or None
            except Exception:
                legacy_draft_path = None
            rid = None
            try:
                rid = row.get("restaurant_id")
            except Exception:
                rid = None
            try:
                restaurant_id = int(rid) if rid is not None else None
            except Exception:
                restaurant_id = None
    except Exception:
        filename = ""
        source_type = ""
        restaurant_id = None
        legacy_draft_path = None

    title = (Path(filename).stem if filename else f"Import {job_id}").strip() or f"Import {job_id}"

    # ---------------------------------------------------------------------
    # NEW: If the job only has a legacy JSON draft_path, upgrade it to DB.
    # ---------------------------------------------------------------------
    try:
        if legacy_draft_path:
            abs_legacy = _abs_from_rel(legacy_draft_path)
            if abs_legacy and abs_legacy.exists():
                legacy = json.loads(abs_legacy.read_text(encoding="utf-8"))

                items: List[Dict[str, Any]] = []
                cats = legacy.get("categories") or []
                for cat_obj in cats:
                    cat_name = (cat_obj.get("name") or "").strip() or None
                    for it in (cat_obj.get("items") or []):
                        base_name = (it.get("name") or "").strip()
                        if not base_name:
                            continue
                        desc = (it.get("description") or "").strip() or None

                        sizes = it.get("sizes") or []
                        if sizes:
                            for s in sizes:
                                size_name = (s.get("name") or "").strip()
                                price_val = s.get("price", 0)
                                try:
                                    price_cents = int(round(float(price_val) * 100))
                                except Exception:
                                    price_cents = 0
                                display_name = f"{base_name} ({size_name})" if size_name else base_name
                                items.append(
                                    {
                                        "name": display_name,
                                        "description": desc,
                                        "category": cat_name,
                                        "subcategory": None,
                                        "price_cents": max(int(price_cents), 0),
                                    }
                                )
                        else:
                            price_val = it.get("price", 0)
                            try:
                                price_cents = int(round(float(price_val) * 100))
                            except Exception:
                                price_cents = 0
                            items.append(
                                {
                                    "name": base_name,
                                    "description": desc,
                                    "category": cat_name,
                                    "subcategory": None,
                                    "price_cents": max(int(price_cents), 0),
                                }
                            )

                create_structured = getattr(drafts_store, "create_draft_from_structured_items", None)
                if callable(create_structured) and items:
                    source_meta = {
                        "job_id": int(job_id),
                        "filename": filename,
                        "source_type": source_type or "legacy_json",
                        "legacy_draft_path": legacy_draft_path,
                    }
                    draft = create_structured(
                        title=title,
                        restaurant_id=restaurant_id,
                        items=items,
                        source_type="legacy_json",
                        source_job_id=int(job_id),
                        source_meta=source_meta,
                    )
                    did_int = int(draft.get("id") or draft.get("draft_id") or 0)

                    if did_int > 0:
                        try:
                            update_import_job(job_id, draft_id=did_int)
                        except Exception:
                            pass
                        return did_int
    except Exception:
        pass

    # Second: attempt explicit creation via drafts_store (if supported)
    create_fn = getattr(drafts_store, "create_draft_from_import", None)
    if not callable(create_fn):
        create_fn = getattr(drafts_store, "create_draft_from_import_job", None)

    if not callable(create_fn):
        return None

    source_file_path = None
    try:
        if filename:
            p = (UPLOAD_FOLDER / filename).resolve()
            if p.exists():
                source_file_path = str(p)
    except Exception:
        source_file_path = None

    source_meta = {
        "job_id": int(job_id),
        "filename": filename,
        "source_type": source_type,
    }

    draft = None

    # Try rich kwargs first
    try:
        draft = create_fn(
            title=title,
            restaurant_id=restaurant_id,
            source_type=source_type or "ocr",
            source_job_id=int(job_id),
            source_file_path=source_file_path,
            source_meta=source_meta,
        )
    except TypeError:
        draft = None
    except Exception:
        draft = None

    # Try minimal kwargs
    if draft is None:
        try:
            draft = create_fn(
                title=title,
                restaurant_id=restaurant_id,
                source_job_id=int(job_id),
            )
        except TypeError:
            draft = None
        except Exception:
            draft = None

    # Try positional job_id only
    if draft is None:
        try:
            draft = create_fn(int(job_id))
        except Exception:
            draft = None

    did_int = 0
    try:
        if isinstance(draft, dict):
            did_int = int(draft.get("id") or draft.get("draft_id") or 0)
        elif draft is not None:
            did_int = int(draft)
    except Exception:
        did_int = 0

    if did_int > 0:
        try:
            update_import_job(job_id, draft_id=did_int)
        except Exception:
            pass
        return did_int

    return None



# ------------------------
# AI Heuristics Preview (Day 20 Phase A)
# ------------------------
@app.get("/imports/<int:job_id>/ai/preview")
@login_required
def imports_ai_preview(job_id: int):
    """
    Day 20 (Phase A): Heuristics-only AI preview.
    Re-OCRs the original upload for this job, runs analyze_ocr_text(), and returns JSON.
    No draft/db writes occur here — it's a read-only preview.

    NOW prefers the user-rotated working image if present.

    NOTE: For structured CSV/JSON imports we do NOT re-OCR; preview is only
    available for image/PDF OCR jobs.
    """
    row = get_import_job(job_id)
    if not row:
        abort(404)

    # Detect structured imports (CSV/JSON/XLSX) and bail out early
    try:
        source_type = (row["source_type"] or "").lower()
    except Exception:
        source_type = ""
    src_name = (row["filename"] or "").strip()
    suffix = Path(src_name).suffix.lower() if src_name else ""
    is_structured = (
        source_type.startswith("structured")
        or suffix in (".csv", ".json", ".xlsx", ".xls")
    )

    if is_structured:
        return jsonify({
            "ok": False,
            "error": "AI preview is only available for image/PDF OCR imports (this job is a structured CSV/JSON import)."
        }), 400

    if analyze_ocr_text is None:
        return jsonify({"ok": False, "error": "AI helper not available"}), 501

    if not src_name:
        return jsonify({"ok": False, "error": "No source filename on job"}), 400

    src_path = (UPLOAD_FOLDER / src_name).resolve()
    if not src_path.exists():
        return jsonify({"ok": False, "error": "Upload file not found on disk"}), 404

    # Prefer working image if available (and auto-rotate if needed for images)
    work = _get_work_image_if_any(job_id)
    try:
        if work and work.exists():
            raw_text = _ocr_image_to_text(work)
        else:
            suffix = src_path.suffix.lower()
            if suffix in (".png", ".jpg", ".jpeg"):
                # NEW: auto-rotate by creating/rotating a working copy
                wp = _auto_rotate_work_image_if_needed(job_id, src_path)
                if wp and wp.exists():
                    raw_text = _ocr_image_to_text(wp)
                else:
                    raw_text = _ocr_image_to_text(src_path)
            elif suffix == ".pdf":
                raw_text = _pdf_to_text(src_path)
            else:
                return jsonify({"ok": False, "error": f"Unsupported file type: {suffix}"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"OCR error: {e}"}), 500


    if not raw_text:
        return jsonify({"ok": False, "error": "Could not extract text for preview"}), 500

    # Run heuristics analysis (Phase A)
    doc = analyze_ocr_text(raw_text, layout=None, taxonomy=TAXONOMY_SEED, restaurant_profile=None)

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "filename": src_name,
        "extracted_chars": len(raw_text),
        "preview": doc
    }), 200


def _build_price_text(ai_item: dict) -> Optional[str]:
    """
    Build a human-readable price_text from AI preview data.

    Priority:
      1) Use variants with explicit price_cents, e.g. "S: $9.95 / L: $13.99"
      2) Fallback to distinct price_candidates, e.g. "$9.95 / $13.99 / $19.95"
    """
    pcs = ai_item.get("price_candidates") or []
    variants = ai_item.get("variants") or []
    parts: List[str] = []

    # Prefer variants if they have explicit prices
    for v in variants:
        price_cents = v.get("price_cents")
        if price_cents is None:
            continue
        try:
            price_cents = int(price_cents)
        except Exception:
            continue
        label = (v.get("label") or "").strip()
        dollars = price_cents / 100.0
        if label:
            parts.append(f"{label}: ${dollars:0.2f}")
        else:
            parts.append(f"${dollars:0.2f}")

    # Fallback: use price_candidates if we didn't get anything from variants
    if not parts:
        seen = set()
        for pc in pcs:
            val = pc.get("value")
            if val is None:
                continue
            try:
                dollars = float(val)
            except Exception:
                continue
            key = round(dollars, 2)
            if key in seen:
                continue
            seen.add(key)
            parts.append(f"${dollars:0.2f}")

    return " / ".join(parts) if parts else None

# ------------------------
# AI Heuristics → Commit into Draft (with redirect-friendly behavior)
# ------------------------
@app.post("/imports/<int:job_id>/ai/commit")
@login_required
def imports_ai_commit(job_id: int):
    """
    For OCR/image/PDF imports:
      - Re-OCR the original upload (same as /ai/preview),
      - run analyze_ocr_text(),
      - replace the draft items for this job with the cleaned items,
      - run AI cleanup.

    For structured CSV/JSON imports:
      - Skip OCR entirely,
      - run AI cleanup on the existing draft items,
      - mark the draft as finalized.

    Behavior:
      - JSON/AJAX: returns JSON.
      - Regular form post or ?redirect=1: flashes + redirects back to Draft Editor.

    NOW prefers the user-rotated working image if present for OCR jobs.
    """
    from storage.ai_cleanup import apply_ai_cleanup

    # detect redirect vs JSON (matches fix-descriptions pattern)
    ct = (request.headers.get("Content-Type") or "").lower()
    is_form_post = ct.startswith("application/x-www-form-urlencoded") or ct.startswith("multipart/form-data")
    wants_redirect = (
        request.args.get("redirect") == "1"
        or (request.form.get("redirect") == "1" if is_form_post else False)
        or is_form_post
    )

    row = get_import_job(job_id)
    if not row:
        if wants_redirect:
            flash("Import job not found.", "error")
            return redirect(url_for("imports"))
        abort(404)

    # Detect structured imports vs OCR-style imports
    try:
        source_type = (row["source_type"] or "").lower()
    except Exception:
        source_type = ""
    src_name = (row["filename"] or "").strip()
    suffix = Path(src_name).suffix.lower() if src_name else ""
    is_structured = (
        source_type.startswith("structured")
        or suffix in (".csv", ".json", ".xlsx", ".xls")
    )

    # ---------------- Structured path: no OCR, just AI cleanup on existing draft ----------------
    if is_structured:
        _require_drafts_storage()
        draft_id = _ensure_draft_for_job(job_id, row=row)
        if not draft_id:
            if wants_redirect:
                flash("No draft available for this job.", "error")
                return redirect(url_for("imports_detail", job_id=job_id))
            return jsonify({"ok": False, "error": "No draft available for this job"}), 400

        draft_id = int(draft_id)

        # Flip status while we run cleanup
        try:
            drafts_store.save_draft_metadata(draft_id, status="processing")
        except Exception:
            pass

        try:
            cleaned = apply_ai_cleanup(draft_id)
            try:
                drafts_store.save_draft_metadata(draft_id, status="finalized")
            except Exception:
                pass

            if wants_redirect:
                flash(
                    f"Finalize complete — AI cleanup updated {int(cleaned)} item(s).",
                    "success",
                )
                return redirect(url_for("draft_editor", draft_id=draft_id))

            return jsonify(
                {
                    "ok": True,
                    "job_id": job_id,
                    "draft_id": draft_id,
                    "inserted_count": 0,
                    "updated_count": 0,
                    "cleaned_count": int(cleaned),
                    "status": "finalized",
                }
            ), 200

        except Exception as e:
            app.logger.exception("AI cleanup during structured imports_ai_commit failed")
            try:
                drafts_store.save_draft_metadata(int(draft_id), status="editing")
            except Exception:
                pass

            if wants_redirect:
                flash(f"AI cleanup failed: {e}", "error")
                return redirect(url_for("draft_editor", draft_id=int(draft_id)))

            return jsonify({"ok": False, "error": str(e), "status": "editing"}), 500

    # ---------------- OCR path: original behavior ----------------
    if analyze_ocr_text is None:
        if wants_redirect:
            flash("AI helper not available.", "error")
            return redirect(url_for("imports_detail", job_id=job_id))
        return jsonify({"ok": False, "error": "AI helper not available"}), 501

    if not src_name:
        if wants_redirect:
            flash("No source filename on job.", "error")
            return redirect(url_for("imports_detail", job_id=job_id))
        return jsonify({"ok": False, "error": "No source filename on job"}), 400

    src_path = (UPLOAD_FOLDER / src_name).resolve()
    if not src_path.exists():
        if wants_redirect:
            flash("Upload file not found on disk.", "error")
            return redirect(url_for("imports_detail", job_id=job_id))
        return jsonify({"ok": False, "error": "Upload file not found on disk"}), 404

    # Extract raw text (prefers working copy; auto-rotate images if needed)
    try:
        work = _get_work_image_if_any(job_id)
        if work and work.exists():
            raw_text = _ocr_image_to_text(work)
        else:
            suffix = src_path.suffix.lower()
            if suffix in (".png", ".jpg", ".jpeg"):
                # NEW: auto-rotate by creating/rotating a working copy
                wp = _auto_rotate_work_image_if_needed(job_id, src_path)
                if wp and wp.exists():
                    raw_text = _ocr_image_to_text(wp)
                else:
                    raw_text = _ocr_image_to_text(src_path)
            elif suffix == ".pdf":
                raw_text = _pdf_to_text(src_path)
            else:
                if wants_redirect:
                    flash(f"Unsupported file type: {suffix}", "error")
                    return redirect(url_for("imports_detail", job_id=job_id))
                return jsonify({"ok": False, "error": f"Unsupported file type: {suffix}"}), 400
    except Exception as e:
        if wants_redirect:
            flash(f"OCR error: {e}", "error")
            return redirect(url_for("imports_detail", job_id=job_id))
        return jsonify({"ok": False, "error": f"OCR error: {e}"}), 500


    if not raw_text:
        if wants_redirect:
            flash("Could not extract text for commit.", "error")
            return redirect(url_for("imports_detail", job_id=job_id))
        return jsonify({"ok": False, "error": "Could not extract text for commit"}), 500

    # Run heuristics
    doc = analyze_ocr_text(raw_text, layout=None, taxonomy=TAXONOMY_SEED, restaurant_profile=None)
    items_ai = (doc or {}).get("items") or []
    new_items = _draft_items_from_ai_preview(items_ai)

    # Replace items in the draft for this job
    _require_drafts_storage()
    draft_id = _ensure_draft_for_job(job_id, row=row)

    if not draft_id:
        if wants_redirect:
            flash("No draft available for this job.", "error")
            return redirect(url_for("imports_detail", job_id=job_id))
        return jsonify({"ok": False, "error": "No draft available for this job"}), 400
 
    draft_id = int(draft_id)

    existing = drafts_store.get_draft_items(draft_id) or []
    existing_ids = [it.get("id") for it in existing if it.get("id") is not None]
    if existing_ids:
        try:
            drafts_store.delete_draft_items(draft_id, existing_ids)
        except Exception as e:
            if wants_redirect:
                flash(f"Failed to clear existing items: {e}", "error")
                return redirect(url_for("draft_editor", draft_id=draft_id))
            return jsonify({"ok": False, "error": f"Failed to clear existing items: {e}"}), 500

    ins = drafts_store.upsert_draft_items(draft_id, new_items)

    # Run AI cleanup on the freshly-committed draft
    try:
        cleaned = apply_ai_cleanup(int(draft_id))
    except Exception as e:
        cleaned = 0
        app.logger.exception("AI cleanup during imports_ai_commit failed: %s", e)

    # Nudge updated_at + status
    try:
        drafts_store.save_draft_metadata(
            draft_id,
            title=(drafts_store.get_draft(draft_id) or {}).get("title"),
            status="finalized",
        )
    except TypeError:
        try:
            drafts_store.save_draft_metadata(
                draft_id,
                title=(drafts_store.get_draft(draft_id) or {}).get("title"),
            )
        except Exception:
            pass
    except Exception:
        pass

    inserted_count = len(ins.get("inserted_ids", []))
    updated_count = len(ins.get("updated_ids", []))

    if wants_redirect:
        flash(
            f"Finalize complete — {inserted_count} item(s) inserted, {int(cleaned)} cleaned.",
            "success",
        )
        return redirect(url_for("draft_editor", draft_id=draft_id))

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "draft_id": draft_id,
        "inserted_count": inserted_count,
        "updated_count": updated_count,
        "cleaned_count": int(cleaned),
        "status": "finalized",
    }), 200



@app.post("/imports/<int:job_id>/ai/finalize")
@login_required
def imports_ai_finalize(job_id: int):
    """
    One-click "Finalize with AI Cleanup" flow for an import job.

    For OCR/image/PDF imports:
      - delegates to /imports/<job_id>/ai/commit to re-OCR + regenerate items
        and run AI cleanup.

    For structured CSV/JSON imports:
      - delegates to /imports/<job_id>/ai/commit which skips OCR and simply
        runs AI cleanup on the existing draft items.

    In both cases, we end in the Draft Editor for the associated draft.

    IMPORTANT:
    imports_ai_commit may return a 302 redirect on both success and failure
    (flash + redirect). We must interpret redirects correctly so failures
    don't look like success.
    """
    _require_drafts_storage()

    # --- Step 0: ensure a DB draft exists for this job (upgrades legacy JSON draft_path -> DB) ---
    row = get_import_job(job_id)
    if not row:
        flash("Import job not found.", "error")
        return redirect(url_for("imports"))

    ensured_draft_id = _ensure_draft_for_job(job_id, row=row)
    if not ensured_draft_id:
        flash("No draft available for this job.", "error")
        return redirect(url_for("imports_detail", job_id=job_id))


    # --- Step 1: run AI commit (side effects only; inspect its Response) ---
    resp = None
    try:
        resp = imports_ai_commit(job_id)
    except Exception as e:
        flash(f"AI finalize failed during commit: {e}", "error")
        return redirect(url_for("imports_detail", job_id=job_id))

    # imports_ai_commit may return:
    #   - a Flask Response
    #   - a (Response, status_code) tuple
    resp_obj = resp[0] if isinstance(resp, tuple) and len(resp) >= 1 else resp
    status_code = None

    if isinstance(resp, tuple) and len(resp) >= 2:
        try:
            status_code = int(resp[1])
        except Exception:
            status_code = None
    elif hasattr(resp_obj, "status_code"):
        try:
            status_code = int(resp_obj.status_code)
        except Exception:
            status_code = None

    imports_url = url_for("imports_detail", job_id=job_id)

    # If commit returned a redirect, interpret it:
    # - Redirect to Draft Editor => success; just follow it.
    # - Redirect back to imports_detail => failure (commit likely flashed an error).
    try:
        location = None
        if hasattr(resp_obj, "headers"):
            location = resp_obj.headers.get("Location")

        if location and status_code in (301, 302, 303, 307, 308):
            loc = str(location)
            if "/drafts/" in loc:
                return resp_obj
            if loc.startswith(imports_url):
                flash("AI finalize failed during commit.", "error")
                return redirect(url_for("imports_detail", job_id=job_id))
    except Exception:
        pass

    if status_code is not None and status_code >= 400:
        flash("AI finalize failed during commit.", "error")
        return redirect(url_for("imports_detail", job_id=job_id))

    # If commit returned JSON, prefer draft_id from the payload.
    try:
        commit_json = None
        if hasattr(resp_obj, "get_json"):
            commit_json = resp_obj.get_json(silent=True)
        if isinstance(commit_json, dict):
            if commit_json.get("ok") is False:
                flash("AI finalize failed during commit.", "error")
                return redirect(url_for("imports_detail", job_id=job_id))
            draft_id_from_commit = commit_json.get("draft_id")
            if draft_id_from_commit:
                flash("AI finalize complete.", "success")
                return redirect(url_for("draft_editor", draft_id=int(draft_id_from_commit)))
    except Exception:
        pass

    # --- Step 2: locate draft for this job ---
    draft_id = _get_or_create_draft_for_job(job_id)
    if not draft_id:
        flash("No draft available for this job after AI finalize.", "error")
        return redirect(url_for("imports_detail", job_id=job_id))

    # --- Step 3: send user into the Draft Editor ---
    flash("AI finalize complete.", "success")
    return redirect(url_for("draft_editor", draft_id=int(draft_id)))


# ------------------------
# Diagnostics
# ------------------------
@app.get("/__ping")
def __ping():

    return jsonify({"ok": True, "time": _now_iso()})

@app.get("/__routes")
def __routes():
    """
    Debug helper: list all registered routes.

    Made defensive so that if any weird rule object blows up during
    stringification or sorting, we surface the error instead of a bare 500.
    """
    try:
        routes = []
        for r in app.url_map.iter_rules():
            try:
                routes.append(str(r.rule))
            except Exception as inner:
                # Fallback so a single bad rule doesn't kill the whole endpoint
                routes.append(f"<unprintable rule: {inner.__class__.__name__}>")

        routes.sort()
        return jsonify({"ok": True, "count": len(routes), "routes": routes})
    except Exception as e:
        # With FLASK_DEBUG=1 and the dev errorhandler, this will also log the traceback.
        app.logger.exception("__routes failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/__boom")
def __boom():
    raise RuntimeError("Intentional test error")

@app.get("/__ocrtxt/<int:job_id>")
def __ocrtxt(job_id: int):
    """
    Debug: return AI helper items + structured categories + superimport bundle
    for the given import job.
    """
    from storage.ocr_facade import extract_menu_from_pdf
    from storage.drafts import find_draft_by_source_job

    draft = find_draft_by_source_job(job_id)
    if not draft:
        return jsonify({"ok": False, "error": "draft not found"}), 404

    # Original upload path
    path = draft.get("source_file_path")
    if not path:
        return jsonify({"ok": False, "error": "no source_file_path"}), 400

    structured, debug = extract_menu_from_pdf(path)

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "structured": structured,
        "superimport": debug.get("superimport"),
        "ai_items": debug.get("ai_preview", {}).get("items"),
        "hierarchy": debug.get("ai_preview", {}).get("hierarchy"),
    })

# ------------------------
# Blueprint registration (core)
# ------------------------
try:
    from .routes.core import core_bp  # type: ignore
except Exception:
    from routes.core import core_bp  # fallback if relative import fails

app.register_blueprint(core_bp)


# --------------------------------------------------------
# TEMPORARY TEST ROUTE — bypass browser validation
# --------------------------------------------------------
@app.get("/test_csv_form")
def test_csv_form():
    return """
    <form action="/api/drafts/import_structured" method="post" enctype="multipart/form-data">
        <input type="file" name="file">

        <button type="submit">Test CSV Upload</button>
    </form>
    """


# ------------------------
# Run
# ------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

# === DEBUG APPEND (auto-added) ===
import os as _os
if _os.environ.get("FLASK_DEBUG") == "1":
    @app.errorhandler(500)
    def _dev_rethrow(e):
        import traceback
        traceback.print_exc()
        raise e

    @app.get("/__debug/imports/<int:job_id>/draft")
    @login_required
    def imports_draft_debug(job_id: int):
        try:
            draft_id = _get_or_create_draft_for_job(job_id)
            if draft_id:
                return redirect(url_for("draft_editor", draft_id=draft_id))
            flash("Draft not ready for editor yet. Showing legacy import view.", "info")
            return redirect(url_for("imports_detail", job_id=job_id))
        except Exception:
            import traceback, html
            tb = traceback.format_exc()
            return f"<pre>{html.escape(tb)}</pre>", 500
# === /DEBUG APPEND ===
