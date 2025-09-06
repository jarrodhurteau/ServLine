from flask import Flask, jsonify, render_template, abort, request, redirect, url_for, session, send_from_directory
import sqlite3
from pathlib import Path
from functools import wraps
import uuid
import os
import threading
import time
import json
from datetime import datetime

# NEW: safer filename + big-file error handling
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

# NEW: OCR health imports
from importlib.util import find_spec
import pytesseract

app = Flask(__name__)

# --- Config (dev) ---
app.config["SECRET_KEY"] = "dev-secret-change-me"   # replace later with an env var
# Limit uploads to ~20 MB to avoid huge files (tweak as needed)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
DEV_USERNAME = "admin"
DEV_PASSWORD = "letmein"

# --- Paths ---
# ROOT is the project root (one level above /portal)
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "servline.db"

# Root-level uploads dir (kept out of git via .gitignore)
UPLOAD_FOLDER = ROOT / "uploads"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

# Draft menus dir (JSON drafts created by worker)
DRAFTS_FOLDER = ROOT / "storage" / "drafts"
DRAFTS_FOLDER.mkdir(parents=True, exist_ok=True)

# Allowed upload types for the menu importer MVP
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "pdf"}
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# --- DB bootstrap: ensure import_jobs table exists ---
def ensure_import_jobs_table():
    with db_connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS import_jobs (
            job_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)
        conn.commit()

ensure_import_jobs_table()

# --- Auth helper ---
def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapper

# ------------------------
# Health / DB / OCR Health
# ------------------------
@app.get("/health")
def health():
    return jsonify({"status": "ok"})

@app.get("/ocr/health")
def ocr_health():
    # Tesseract presence + version
    tess_cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "")
    tess_path_exists = bool(tess_cmd) and Path(tess_cmd).exists()
    try:
        tess_version = str(pytesseract.get_tesseract_version())
    except Exception:
        tess_version = None

    # Column-mode libs
    have_pandas = find_spec("pandas") is not None
    have_sklearn = find_spec("sklearn") is not None
    column_mode = "enabled" if (have_pandas and have_sklearn) else "fallback"

    return jsonify({
        "tesseract": {
            "cmd": tess_cmd,
            "found_on_disk": tess_path_exists,
            "version": tess_version,
        },
        "columns": {
            "pandas": have_pandas,
            "scikit_learn": have_sklearn,
            "mode": column_mode
        }
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
            "SELECT * FROM menus WHERE restaurant_id=? AND active=1", (rest_id,)
        ).fetchall()
        if not rows:
            abort(404, description="No menus found for that restaurant")
        return jsonify([dict(r) for r in rows])

@app.get("/api/menus/<int:menu_id>/items")
def get_menu_items(menu_id):
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM menu_items WHERE menu_id=? AND is_available=1", (menu_id,)
        ).fetchall()
        if not rows:
            abort(404, description="No items found for that menu")
        return jsonify([dict(r) for r in rows])

# ------------------------
# Import flow: Upload -> Job record -> Background worker -> Draft JSON
# ------------------------
def _now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def _update_job(job_id: str, status: str, error: str | None = None):
    with db_connect() as conn:
        conn.execute(
            "UPDATE import_jobs SET status=?, error=?, updated_at=? WHERE job_id=?",
            (status, error, _now_iso(), job_id),
        )
        conn.commit()

def _insert_job(job_id: str, filename: str):
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO import_jobs (job_id, filename, status, error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, filename, "saved", None, _now_iso(), _now_iso()),
        )
        conn.commit()

def _draft_path(job_id: str) -> Path:
    return DRAFTS_FOLDER / f"{job_id}.json"

def _process_import_job(job_id: str, saved_file_path: Path):
    """
    Background worker: for JPG/PNG -> OCR (Tesseract) -> draft JSON.
    PDFs still use the temporary stub for now.
    """
    try:
        suffix = saved_file_path.suffix.lower()

        if suffix in (".png", ".jpg", ".jpeg"):
            # ---- Real OCR path for images
            _update_job(job_id, "processing_ocr")
            try:
                from .ocr_worker import run_image_pipeline
            except ImportError:
                # Fallback: allow absolute import if user placed ocr_worker next to app.py
                from ocr_worker import run_image_pipeline

            raw_text, draft = run_image_pipeline(saved_file_path, job_id)

            # Save raw OCR text for debugging
            raw_dir = DRAFTS_FOLDER / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"{job_id}.txt").write_text(raw_text, encoding="utf-8")

        else:
            # ---- Temporary stub for PDFs/other types (we'll swap to pdfplumber next)
            _update_job(job_id, "processing_ocr")
            time.sleep(1.0)
            draft = {
                "job_id": job_id,
                "restaurant_id": None,
                "currency": "USD",
                "categories": [
                    {
                        "name": "Pizzas",
                        "items": [
                            {"name": "Cheese Pizza", "description": "", "sizes": [{"name": "Large", "price": 14.99}], "options": [], "tags": []},
                            {"name": "Pepperoni Pizza", "description": "", "sizes": [{"name": "Large", "price": 16.49}], "options": [], "tags": []}
                        ]
                    },
                    {
                        "name": "Drinks",
                        "items": [
                            {"name": "Soda", "description": "20oz bottle", "sizes": [{"name": "One Size", "price": 2.49}], "options": [], "tags": []}
                        ]
                    }
                ],
                "source": {"type": "upload", "file": saved_file_path.name, "ocr_engine": "stub", "confidence": 0.42},
                "created_at": _now_iso()
            }

        # Write the draft JSON
        with open(_draft_path(job_id), "w", encoding="utf-8") as f:
            json.dump(draft, f, indent=2)

        _update_job(job_id, "draft_created")

    except Exception as e:
        _update_job(job_id, "error", error=str(e))

# Upload route
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

        job_id = str(uuid.uuid4())

        base_name = secure_filename(file.filename) or "upload"
        safe_name = f"{job_id}_{base_name}"
        save_path = UPLOAD_FOLDER / safe_name

        file.save(str(save_path))

        _insert_job(job_id, safe_name)
        t = threading.Thread(target=_process_import_job, args=(job_id, save_path), daemon=True)
        t.start()

        return jsonify({"job_id": job_id, "status": "saved", "file": safe_name}), 200

    except RequestEntityTooLarge:
        return jsonify({"error": "File too large. Try a smaller image or raise MAX_CONTENT_LENGTH."}), 413
    except Exception as e:
        return jsonify({"error": f"Server error while saving upload: {e}"}), 500

# Check job status
@app.get("/api/menus/import/<job_id>/status")
@login_required
def import_status(job_id):
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM import_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            abort(404, description="Job not found")
        data = dict(row)
    data["draft_ready"] = _draft_path(job_id).exists()
    return jsonify(data)

# List drafts (MVP JSON)
@app.get("/api/menus/drafts")
@login_required
def list_drafts():
    drafts = []
    for p in DRAFTS_FOLDER.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                draft = json.load(f)
                drafts.append({
                    "job_id": draft.get("job_id"),
                    "file": draft.get("source", {}).get("file"),
                    "categories": [c.get("name") for c in draft.get("categories", [])]
                })
        except Exception:
            pass
    return jsonify({"drafts": drafts})

# ------------------------
# HTML Pages (Portal UI)
# ------------------------
@app.get("/")
def index():
    return render_template("index.html")

@app.get("/restaurants")
def restaurants_page():
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM restaurants WHERE active=1 ORDER BY id"
        ).fetchall()
    return render_template("restaurants.html", restaurants=rows)

@app.get("/restaurants/<int:rest_id>/menus")
def menus_page(rest_id):
    with db_connect() as conn:
        rest = conn.execute(
            "SELECT * FROM restaurants WHERE id=?", (rest_id,)
        ).fetchone()
        menus = conn.execute(
            "SELECT * FROM menus WHERE restaurant_id=? AND active=1 ORDER BY id", (rest_id,)
        ).fetchall()
    if not rest:
        abort(404)
    return render_template("menus.html", restaurant=rest, menus=menus)

@app.get("/menus/<int:menu_id>/items")
def items_page(menu_id):
    with db_connect() as conn:
        menu = conn.execute(
            "SELECT * FROM menus WHERE id=?", (menu_id,)
        ).fetchone()
        if not menu:
            abort(404)
        rest = conn.execute(
            "SELECT * FROM restaurants WHERE id=?", (menu["restaurant_id"],)
        ).fetchone()
        items = conn.execute(
            "SELECT * FROM menu_items WHERE menu_id=? AND is_available=1 ORDER BY id", (menu_id,)
        ).fetchall()
    return render_template("items.html", restaurant=rest, menu=menu, items=items)

# ------------------------
# Day 5: Admin Forms (Add Menu Items)
# ------------------------
@app.get("/menus/<int:menu_id>/items/new")
@login_required
def new_item_form(menu_id):
    with db_connect() as conn:
        menu = conn.execute("SELECT * FROM menus WHERE id=?", (menu_id,)).fetchone()
        if not menu:
            abort(404)
        # FIX: parameter tuple (trailing comma)
        rest = conn.execute(
            "SELECT * FROM restaurants WHERE id=?", (menu["restaurant_id"],)
        ).fetchone()
    return render_template("item_form.html", restaurant=rest, menu=menu)

@app.post("/menus/<int:menu_id>/items/new")
@login_required
def create_item(menu_id):
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    price_raw = (request.form.get("price") or "0").strip()

    if not name:
        abort(400, description="Name is required")

    try:
        price_cents = int(round(float(price_raw) * 100))
    except ValueError:
        abort(400, description="Price must be a number")

    with db_connect() as conn:
        conn.execute(
            "INSERT INTO menu_items (menu_id, name, description, price_cents) VALUES (?, ?, ?, ?)",
            (menu_id, name, description, price_cents),
        )
        conn.commit()

    return redirect(url_for("items_page", menu_id=menu_id))

# ------------------------
# Day 6: Auth (Login / Logout)
# ------------------------
@app.get("/login")
def login():
    return render_template("login.html", error=None, next=request.args.get("next"))

@app.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    nxt = request.form.get("next") or url_for("index")
    if username == DEV_USERNAME and password == DEV_PASSWORD:
        session["user"] = {"username": username}
        return redirect(nxt)
    return render_template("login.html", error="Invalid credentials", next=request.form.get("next"))

@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# ------------------------
# Dev helper page: simple upload form (uses your login session)
# ------------------------
@app.get("/dev/upload")
@login_required
def dev_upload_form():
    return """
    <!doctype html>
    <html>
      <head><meta charset="utf-8"><title>Dev Upload Test</title></head>
      <body style="font-family: sans-serif; padding: 2rem; max-width: 640px;">
        <h2>Dev Upload Test</h2>
        <p>Pick an image or PDF and submit to <code>/api/menus/import</code>.</p>
        <form action="/api/menus/import" method="post" enctype="multipart/form-data">
          <input type="file" name="file" accept="image/*,.pdf" />
          <button type="submit">Upload</button>
        </form>
      </body>
    </html>
    """

# ------------------------
# Drafts (HTML list page) + Upload preview
# ------------------------
@app.get("/drafts")
@login_required
def drafts_page():
    draft_rows = []
    statuses = {}
    with db_connect() as conn:
        for row in conn.execute("SELECT job_id, status, updated_at FROM import_jobs"):
            statuses[row["job_id"]] = {"status": row["status"], "updated_at": row["updated_at"]}

    for p in DRAFTS_FOLDER.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                draft = json.load(f)
            job_id = draft.get("job_id")
            source_file = (draft.get("source", {}) or {}).get("file")
            categories = [c.get("name") for c in draft.get("categories", [])]
            st = statuses.get(job_id, {"status": "unknown", "updated_at": "unknown"})
            draft_rows.append({
                "job_id": job_id,
                "source_file": source_file,
                "categories": categories,
                "status": st["status"],
                "updated_at": st["updated_at"],
            })
        except Exception:
            pass

    draft_rows.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
    return render_template("drafts.html", drafts=draft_rows)

@app.get("/uploads/<path:filename>")
@login_required
def serve_upload(filename):
    return send_from_directory(str(UPLOAD_FOLDER), filename, as_attachment=False)

# ---------- Helpers for review/publish ----------
def _load_draft(job_id: str):
    p = _draft_path(job_id)
    if not p.exists():
        abort(404, description="Draft not found")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def _is_image(name: str) -> bool:
    n = (name or "").lower()
    return n.endswith(".png") or n.endswith(".jpg") or n.endswith(".jpeg")

# ------------------------
# Draft Review page
# ------------------------
@app.get("/drafts/<job_id>")
@login_required
def draft_review_page(job_id):
    draft = _load_draft(job_id)
    with db_connect() as conn:
        restaurants = conn.execute(
            "SELECT id, name FROM restaurants WHERE active=1 ORDER BY id"
        ).fetchall()

    preview_url = None
    src_file = (draft.get("source", {}) or {}).get("file")
    if src_file and _is_image(src_file):
        preview_url = url_for("serve_upload", filename=src_file)

    return render_template("draft_review.html", draft=draft, restaurants=restaurants, preview_url=preview_url)

# ------------------------
# Publish draft -> create menu + items
# ------------------------
@app.post("/drafts/<job_id>/publish")
@login_required
def publish_draft(job_id):
    draft = _load_draft(job_id)
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
        _update_job(job_id, "published")
    except Exception:
        pass

    return redirect(url_for("items_page", menu_id=menu_id))

# ------------------------
# Raw OCR viewer (styled, copyable)
# ------------------------
@app.get("/drafts/<job_id>/raw")
@login_required
def view_raw(job_id):
    raw_file = DRAFTS_FOLDER / "raw" / f"{job_id}.txt"
    if not raw_file.exists():
        abort(404, description="Raw OCR not found for that job")
    raw_text = raw_file.read_text(encoding="utf-8", errors="ignore")
    return render_template("raw.html", raw_text=raw_text, job_id=job_id)

# ------------------------
# Import page (two simple forms: picture or PDF)
# ------------------------
@app.get("/import")
@login_required
def import_page():
    return render_template("import.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
