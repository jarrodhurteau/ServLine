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
from storage import drafts

# --- Forward decls for type checkers (real implementations appear later) ---
def _ocr_image_to_text(img_path: Path) -> str: ...
def _pdf_to_text(pdf_path: Path) -> str: ...

# stdlib for exports
import io
import csv
import re  # <-- for OCR parsing

# NEW: optional Excel export dependency
try:
    from openpyxl import Workbook
except Exception:
    Workbook = None

# safer filename + big-file error handling
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

# OCR health imports
from importlib.util import find_spec
import pytesseract

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
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "servline.db"

# Make project root importable so we can import storage.*
import sys
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

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
TESSERACT_CONFIG = os.getenv("TESSERACT_CONFIG") or "--oem 1 --psm 6"

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

# OCR engine (Day 21 revamp)
try:
    from storage.ocr_facade import extract_menu_from_pdf as extract_items_from_path
    from storage.ocr_facade import health as ocr_health_lib
except Exception as e:
    extract_items_from_path = None
    ocr_health_lib = lambda: {"engine": "error", "error": repr(e)}

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

def _get_or_create_draft_for_job(job_id: int) -> Optional[int]:
    """Return a draft_id for this import job, creating from legacy JSON if needed."""
    _require_drafts_storage()
    row = get_import_job(job_id)
    if not row:
        return None

    # Existing DB-backed draft?
    if hasattr(drafts_store, "find_draft_by_source_job"):
        existing = drafts_store.find_draft_by_source_job(job_id)
        if existing and (existing.get("id") or existing.get("draft_id")):
            draft_id = int(existing.get("id") or existing.get("draft_id"))
            # NEW: sync restaurant_id if the job has one and draft is missing it
            try:
                if row["restaurant_id"] and not (existing.get("restaurant_id")):
                    drafts_store.save_draft_metadata(draft_id, restaurant_id=int(row["restaurant_id"]))
            except Exception:
                pass
            return draft_id

    # Legacy JSON → new draft
    abs_path = _abs_from_rel(row["draft_path"]) if row["draft_path"] else None
    if abs_path and abs_path.exists() and hasattr(drafts_store, "create_draft_from_import"):
        with open(abs_path, "r", encoding="utf-8") as f:
            draft_json = json.load(f)
        created = drafts_store.create_draft_from_import(draft_json, import_job_id=job_id)
        draft_id = int(created.get("id") or created.get("draft_id"))
        # NEW: set draft.restaurant_id from the job if available
        try:
            if row["restaurant_id"]:
                drafts_store.save_draft_metadata(draft_id, restaurant_id=int(row["restaurant_id"]))
        except Exception:
            pass
        return draft_id

    return None

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

    draft_id = _get_or_create_draft_for_job(job_id)
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
    """Delete all draft items (keep the draft shell so editor can be used later). Returns deleted count."""
    _require_drafts_storage()
    draft_id = _get_or_create_draft_for_job(job_id)
    if not draft_id:
        return 0

    # fetch items, delete them
    items = drafts_store.get_draft_items(draft_id) or []
    ids = [it.get("id") for it in items if it.get("id") is not None]
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
        "ocr_lib_health": (ocr_health_lib() if callable(ocr_health_lib) else None),
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
    """
    try:
        p = _work_image_path(job_id)
        if p.exists():
            return p

        suffix = src_path.suffix.lower()
        if suffix in (".png", ".jpg", ".jpeg"):
            from PIL import Image
            with Image.open(src_path) as im:
                if im.mode != "RGB":
                    im = im.convert("RGB")
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
        return pytesseract.image_to_string(
            str(img_path),
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

# ----- Day 14: helper-backed draft builder -----
def _build_draft_from_helper(job_id: int, saved_file_path: Path):
    """
    Use storage/ocr_helper.extract_items_from_path to build a draft JSON.

    NOTE (Day 17): extract_items_from_path may return either:
      • dict[str, list[items]]  -> just categories
      • (dict[str, list[items]], debug: dict) -> categories + debug payload
    """
    if extract_items_from_path is None:
        raise RuntimeError("ocr_helper not available")

    debug_payload = None
    cats_raw = extract_items_from_path(str(saved_file_path)) or {}

    if isinstance(cats_raw, tuple) and len(cats_raw) == 2:
        cats, debug_payload = cats_raw
    else:
        cats, debug_payload = cats_raw, None

    categories = []
    for cat_name, items in (cats or {}).items():
        out_items = []
        for it in items or []:
            name = (it.get("name") or "").strip()
            desc = (it.get("description") or "").strip()
            price = it.get("price")
            if isinstance(price, str):
                try:
                    price_val = float(price.replace("$", "").strip())
                except Exception:
                    price_val = 0.0
            elif isinstance(price, (int, float)):
                price_val = float(price) / 100.0 if isinstance(price, int) and price >= 100 else float(price)
            else:
                price_val = 0.0
            sizes = [{"name": "One Size", "price": round(float(price_val), 2)}] if price_val else []

            out_items.append({
                "name": name or "Untitled",
                "description": desc,
                "sizes": sizes,
                "category": cat_name,
                "confidence": it.get("confidence"),
                "raw": it.get("raw"),
            })
        if out_items:
            categories.append({"name": cat_name, "items": out_items})

    if not categories:
        categories = [{"name": "Uncategorized", "items": [
            {"name": "No items recognized", "description": "OCR returned no items.", "sizes": []}
        ]}]

    engine = "ocr_helper+tesseract"
    draft_dict = {
        "job_id": job_id,
        "source": {"type": "upload", "file": saved_file_path.name, "ocr_engine": engine},
        "extracted_at": _now_iso(),
        "categories": categories,
    }
    return draft_dict, debug_payload
# ---------- NEW: wrap ocr_worker result into draft schema ----------
def _build_draft_from_worker(job_id: int, saved_file_path: Path, worker_obj: dict) -> dict:
    """
    The worker returns either a category-like block (with 'items') or a single item.
    We normalize into our draft schema with minimal transformation.
    """
    # Determine category name and item list
    if isinstance(worker_obj, dict) and "items" in worker_obj:
        cat_name = (worker_obj.get("category") or worker_obj.get("name") or "Uncategorized").strip() or "Uncategorized"
        items = worker_obj.get("items") or []
    else:
        cat_name = (worker_obj.get("category") if isinstance(worker_obj, dict) else None) or "Uncategorized"
        items = [worker_obj] if isinstance(worker_obj, dict) else []

    # Map items into our expected fields (name, description, sizes)
    norm_items = []
    for it in items:
        if not isinstance(it, dict):
            continue
        nm = (it.get("name") or "").strip()
        if not nm:
            continue
        desc = (it.get("description") or "").strip()
        sizes_in = it.get("sizes") or []
        sizes_out = []
        for s in sizes_in:
            if not isinstance(s, dict):
                continue
            sn = (s.get("name") or "").strip()
            try:
                sp = float(s.get("price")) if s.get("price") is not None else 0.0
            except Exception:
                sp = 0.0
            if sn or sp:
                sizes_out.append({"name": sn, "price": round(sp, 2)})
        norm_items.append({
            "name": nm,
            "description": desc,
            "sizes": sizes_out
        })

    categories = [{"name": cat_name, "items": norm_items}] if norm_items else [{"name": "Uncategorized", "items": []}]
    return {
        "job_id": job_id,
        "source": {"type": "upload", "file": saved_file_path.name, "ocr_engine": "ocr_worker"},
        "extracted_at": _now_iso(),
        "categories": categories,
    }

def run_ocr_and_make_draft(job_id: int, saved_file_path: Path):
    try:
        update_import_job(job_id, status="processing")

        # Always create a user-rotatable working preview up-front
        try:
            _ensure_work_image(job_id, saved_file_path)
        except Exception:
            pass

        draft = None
        debug_payload = None
        engine = ""
        helper_error = None

        # Prefer working image (respects later user rotation too if we rerun)
        src_for_ocr, type_tag = _path_for_ocr(job_id, saved_file_path)

        # 0) **Preferred when we have an image**: use ocr_worker
        if type_tag == "image":
            try:
                raw_text, worker_obj = ocr_worker.run_image_pipeline(Path(src_for_ocr), job_id=str(job_id))
                draft = _build_draft_from_worker(job_id, saved_file_path, worker_obj or {})
                engine = "ocr_worker"
                # If the worker exposed segmentation, store it for debug overlays
                if drafts_store is not None and hasattr(drafts_store, "save_ocr_debug"):
                    try:
                        dbg_obj = worker_obj.get("debug") if isinstance(worker_obj, dict) else None
                        if dbg_obj:
                            drafts_store.save_ocr_debug(_get_or_create_draft_for_job(job_id) or 0, dbg_obj)
                    except Exception:
                        pass
            except Exception as e:
                helper_error = f"ocr_worker_failed: {e}"
                draft = None

        # 1) Helper path (typically for PDFs), fallback if image path failed
        if draft is None and extract_items_from_path is not None:
            try:
                draft, debug_payload = _build_draft_from_helper(job_id, saved_file_path)
                engine = (draft.get("source") or {}).get("ocr_engine") or "ocr_helper+tesseract"
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
                draft = _text_to_draft(text, job_id, saved_file_path.name, engine or "tesseract")
                debug_payload = {"notes": [f"fallback_engine={engine}", f"len_text={len(text)}"],
                                 "items": [], "lines": text.splitlines()[:500]}
            else:
                draft = {
                    "job_id": job_id,
                    "source": {"type": "upload", "file": saved_file_path.name, "ocr_engine": engine or "unavailable"},
                    "extracted_at": _now_iso(),
                    "categories": [
                        {"name": "Uncategorized", "items": [
                            {"name": "OCR not configured", "description": "Install Tesseract; for PDFs also install pdf2image + Poppler.", "sizes": []}
                        ]}]
                }
                debug_payload = {"notes": ["no_text_extracted"]}

        rel_draft_path = _save_draft_json(job_id, draft)

        try:
            raw_dump = f"(engine={engine})\n"
            if helper_error:
                raw_dump += f"[helper_error] {helper_error}\n"
            (RAW_FOLDER / f"{job_id}.txt").write_text(raw_dump, encoding="utf-8")
        except Exception:
            pass

        update_import_job(job_id, status="done", draft_path=rel_draft_path)

        try:
            if drafts_store is not None:
                draft_id = _get_or_create_draft_for_job(job_id)
                if draft_id and debug_payload and hasattr(drafts_store, "save_ocr_debug"):
                    try:
                        drafts_store.save_ocr_debug(draft_id, debug_payload)
                    except Exception:
                        pass
        except Exception:
            pass

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
# ------------------------
# **NEW** Import landing page + HTML POST handler
# ------------------------
@app.route("/import", methods=["GET"], strict_slashes=False)
@login_required
def import_page():
    with db_connect() as conn:
        restaurants = conn.execute(
            "SELECT id, name FROM restaurants WHERE active=1 ORDER BY name"
        ).fetchall()
    return _safe_render("import.html", restaurants=restaurants)

@app.route("/import", methods=["POST"], strict_slashes=False)
@login_required
def import_upload():
    """
    Handles uploaded menu files (images or PDFs) and launches the OCR import job.

    After saving and processing the file, this route redirects to the Import Preview
    page where you can review the parsed output, rotate the image if needed, and confirm
    before editing the generated draft.
    """
    try:
        file = request.files.get("file")
        if not file or file.filename == "":
            flash("Please choose a file to upload.", "error")
            return redirect(url_for("import_page"))

        if not allowed_file(file.filename):
            flash("Unsupported file type. Allowed: JPG, JPEG, PNG, PDF.", "error")
            return redirect(url_for("import_page"))

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

        # ✅ NEW: Redirect straight to Import Preview instead of waiting for Draft Editor
        flash("Import complete — review preview below and rotate if needed.", "success")
        return redirect(url_for("imports_view", job_id=job_id))

    except RequestEntityTooLarge:
        flash("File too large. Try a smaller file or raise MAX_CONTENT_LENGTH.", "error")

    except Exception as e:
        flash(f"Server error while saving upload: {e}", "error")

    return redirect(url_for("import_page"))


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
def imports_raw(job_id):
    row = get_import_job(job_id)
    if not row:
        abort(404)
    draft_path = row["draft_path"]
    if not draft_path:
        return jsonify({"job_id": job_id, "status": row["status"], "message": "No draft file yet"}), 200
    abs_path = _abs_from_rel(draft_path)
    if not abs_path or not abs_path.exists():
        return jsonify({"error": "Draft path missing on disk", "draft_path": draft_path}), 500
    with open(abs_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)

# ---- NEW: Segmentation preview bridges (JSON) ----
def _load_debug_for_draft(draft_id: int) -> Dict[str, Any]:
    """Helper to fetch OCR debug payload saved by worker/helper."""
    _require_drafts_storage()
    load_fn = getattr(drafts_store, "load_ocr_debug", None)
    if not load_fn:
        return {}
    dbg = load_fn(draft_id) or {}
    # Normalize possible shapes
    if not isinstance(dbg, dict):
        return {}
    return dbg
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
    text_blocks = dbg.get("text_blocks") or dbg.get("blocks") or []
    return jsonify({"ok": True, "draft_id": draft_id, "preview_blocks": preview_blocks, "text_blocks": text_blocks})

@app.get("/imports/<int:job_id>/blocks")
@login_required
def imports_blocks(job_id: int):
    """
    Convenience bridge: look up draft for import job and delegate to /drafts/<id>/blocks.
    """
    _require_drafts_storage()
    draft_id = _get_or_create_draft_for_job(job_id)
    if not draft_id:
        return jsonify({"ok": False, "error": "No draft found for job"}), 404
    return drafts_blocks(draft_id)  # returns a Response

# Bridge to Draft Editor (DB-first)
@app.get("/imports/<int:job_id>/draft")
@login_required
def imports_draft(job_id: int):
    draft_id = _get_or_create_draft_for_job(job_id)
    if draft_id:
        return redirect(url_for("draft_editor", draft_id=draft_id))
    flash("Draft not ready for editor yet. Showing legacy import view.", "info")
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
# ------------------------
# Serving uploads (secure; block .trash)
# ------------------------
@app.get("/uploads/<path:filename>")
@login_required
def serve_upload(filename):
    requested = (UPLOAD_FOLDER / filename).resolve()
    if not str(requested).startswith(str(UPLOAD_FOLDER.resolve())):
        abort(403)
    if TRASH_FOLDER.resolve() in requested.parents or requested == TRASH_FOLDER.resolve():
        abort(403)
    return send_from_directory(str(UPLOAD_FOLDER), filename, as_attachment=False)

# ------------------------
# Upload Management (Recycle Bin) + Artifact cleanup
# ------------------------
def _safe_in_uploads(path: Path) -> bool:
    try:
        return str(path.resolve()).startswith(str(UPLOAD_FOLDER.resolve()))
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
    _move_to_trash(names)
    flash(f"Moved {len(names)} file(s) to Recycle Bin.", "success")
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

    categories = sorted({
        (it.get("category") or "").strip()
        for it in items
        if (it.get("category") or "").strip()
    })

    with db_connect() as conn:
        restaurants = conn.execute(
            "SELECT id, name FROM restaurants WHERE active=1 ORDER BY name"
        ).fetchall()

    return _safe_render(
        "draft_editor.html",
        draft=draft,
        items=items,
        categories=categories,
        restaurants=restaurants,
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

        items = drafts_store.get_draft_items(draft_id) or []
        with db_connect() as conn:
            menu_id = _find_or_create_menu_for_restaurant(conn, int(restaurant_id))
            cur = conn.cursor()
            inserted = 0
            for it in items:
                name = (it.get("name") or "").strip()
                if not name:
                    continue
                desc = (it.get("description") or "").strip()
                price_cents = it.get("price_cents")
                if price_cents is None:
                    price_cents = _price_to_cents(it.get("price") or it.get("price_text"))
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

@app.get("/drafts/<int:draft_id>/export.json")
@login_required
def draft_export_json(draft_id: int):
    _require_drafts_storage()
    draft = drafts_store.get_draft(draft_id) or {}
    items = drafts_store.get_draft_items(draft_id) or []
    payload = {
        "draft_id": draft_id,
        "title": draft.get("title"),
        "restaurant_id": draft.get("restaurant_id"),
        "status": draft.get("status"),
        "items": items,
        "exported_at": _now_iso(),
    }

    # 🔒 Validate contract on the way out as well
    ok, err = validate_draft_payload(payload)
    if not ok:
        return make_response(json.dumps({"error": f"schema: {err}"}, indent=2), 500)

    resp = make_response(json.dumps(payload, indent=2))
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="draft_{draft_id}.json"'
    return resp

@app.get("/drafts/<int:draft_id>/export.xlsx")
@login_required
def draft_export_xlsx(draft_id: int):
    _require_drafts_storage()
    if Workbook is None:
        return make_response("openpyxl not installed. pip install openpyxl", 500)

    draft = drafts_store.get_draft(draft_id) or {}
    items = drafts_store.get_draft_items(draft_id) or []

    wb = Workbook()
    ws = wb.active
    ws.title = (draft.get("title") or f"Draft {draft_id}")[:31]

    headers = ["id", "name", "description", "price_cents", "category", "position"]
    ws.append(headers)
    for it in items:
        ws.append([
            it.get("id"),
            it.get("name", ""),
            it.get("description", ""),
            it.get("price_cents", 0),
            it.get("category") or "",
            "" if it.get("position") is None else it.get("position"),
        ])

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)

    resp = make_response(out.read())
    resp.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    resp.headers["Content-Disposition"] = f'attachment; filename="draft_{draft_id}.xlsx"'
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
    """
    if analyze_ocr_text is None:
        return jsonify({"ok": False, "error": "AI helper not available"}), 501

    row = get_import_job(job_id)
    if not row:
        abort(404)

    src_name = (row["filename"] or "").strip()
    if not src_name:
        return jsonify({"ok": False, "error": "No source filename on job"}), 400

    src_path = (UPLOAD_FOLDER / src_name).resolve()
    if not src_path.exists():
        return jsonify({"ok": False, "error": "Upload file not found on disk"}), 404

    # Prefer working image if available
    work = _get_work_image_if_any(job_id)
    try:
        if work and work.exists():
            raw_text = _ocr_image_to_text(work)
        else:
            suffix = src_path.suffix.lower()
            if suffix in (".png", ".jpg", ".jpeg"):
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

# ------------------------
# AI Heuristics → Commit into Draft (with redirect-friendly behavior)
# ------------------------
@app.post("/imports/<int:job_id>/ai/commit")
@login_required
def imports_ai_commit(job_id: int):
    """
    Re-OCR the original upload (same as /ai/preview), run analyze_ocr_text(),
    then replace the draft items for this job with the cleaned items.

    Behavior:
      - JSON/AJAX: returns JSON.
      - Regular form post or ?redirect=1: flashes + redirects back to Draft Editor.

    NOW prefers the user-rotated working image if present.
    """
    # detect redirect vs JSON (matches fix-descriptions pattern)
    ct = (request.headers.get("Content-Type") or "").lower()
    is_form_post = ct.startswith("application/x-www-form-urlencoded") or ct.startswith("multipart/form-data")
    wants_redirect = (
        request.args.get("redirect") == "1"
        or (request.form.get("redirect") == "1" if is_form_post else False)
        or is_form_post
    )

    if analyze_ocr_text is None:
        if wants_redirect:
            flash("AI helper not available.", "error")
            return redirect(url_for("imports_detail", job_id=job_id))
        return jsonify({"ok": False, "error": "AI helper not available"}), 501

    row = get_import_job(job_id)
    if not row:
        if wants_redirect:
            flash("Import job not found.", "error")
            return redirect(url_for("imports"))
        abort(404)

    src_name = (row["filename"] or "").strip()
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

    # Extract raw text (prefers working copy)
    try:
        work = _get_work_image_if_any(job_id)
        if work and work.exists():
            raw_text = _ocr_image_to_text(work)
        else:
            suffix = src_path.suffix.lower()
            if suffix in (".png", ".jpg", ".jpeg"):
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

    # Map AI preview items -> draft_items schema (name, description, price_cents, category, confidence)
    def _to_cents(v) -> int:
        try:
            return int(round(float(v) * 100))
        except Exception:
            return 0

    new_items = []
    for it in items_ai:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        desc = (it.get("description") or "") or ""
        cat = (it.get("category") or "") or None
        conf = it.get("confidence")
        pcs = it.get("price_candidates") or []
        price_cents = 0
        if pcs:
            try:
                price_cents = _to_cents(pcs[0].get("value"))
            except Exception:
                price_cents = 0
        new_items.append({
            "name": name,
            "description": desc.strip() or None,
            "price_cents": int(price_cents),
            "category": cat,
            "position": None,
            "confidence": int(round(conf * 100)) if isinstance(conf, float) else conf
        })

    # Replace items in the draft for this job
    _require_drafts_storage()
    draft_id = _get_or_create_draft_for_job(job_id)
    if not draft_id:
        if wants_redirect:
            flash("No draft available for this job.", "error")
            return redirect(url_for("imports_detail", job_id=job_id))
        return jsonify({"ok": False, "error": "No draft available for this job"}), 400

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
    # Nudge updated_at so the draft bubbles in /drafts
    try:
        drafts_store.save_draft_metadata(draft_id, title=(drafts_store.get_draft(draft_id) or {}).get("title"))
    except Exception:
        pass

    if wants_redirect:
        flash(f"AI commit complete — {len(ins.get('inserted_ids', []))} item(s) inserted.", "success")
        return redirect(url_for("draft_editor", draft_id=draft_id))

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "draft_id": draft_id,
        "inserted_count": len(ins.get("inserted_ids", [])),
        "updated_count": len(ins.get("updated_ids", [])),
    }), 200

# ------------------------
# Diagnostics
# ------------------------
@app.get("/__ping")
def __ping():
    return jsonify({"ok": True, "time": _now_iso()})

@app.get("/__routes")
def __routes():
    return jsonify(sorted([r.rule for r in app.url_map.iter_rules()]))

@app.get("/__boom")
def __boom():
    raise RuntimeError("Intentional test error")

# ------------------------
# Blueprint registration (core)
# ------------------------
try:
    from .routes.core import core_bp  # type: ignore
except Exception:
    from routes.core import core_bp  # fallback if relative import fails

app.register_blueprint(core_bp)
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
