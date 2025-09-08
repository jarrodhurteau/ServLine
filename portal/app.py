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
    # ensure FK enforcement on this connection
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

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

def create_import_job(filename: str, restaurant_id: int | None = None) -> int:
    with db_connect() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO import_jobs (restaurant_id, filename, status, created_at, updated_at)
            VALUES (?, ?, 'pending', datetime('now'), datetime('now'))
        """, (restaurant_id, filename))
        job_id = cur.lastrowid
        conn.commit()
        return int(job_id)

def update_import_job(job_id: int, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields.keys())
    values = list(fields.values())
    # always touch updated_at
    sets = f"{sets}, updated_at=datetime('now')"
    with db_connect() as conn:
        conn.execute(f"UPDATE import_jobs SET {sets} WHERE id=?", (*values, job_id))
        conn.commit()

def get_import_job(job_id: int):
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM import_jobs WHERE id=?", (job_id,)).fetchone()
        return row

def list_import_jobs(limit: int = 100):
    with db_connect() as conn:
        return conn.execute("""
            SELECT * FROM import_jobs
            ORDER BY datetime(created_at) DESC
            LIMIT ?
        """, (limit,)).fetchall()

def _abs_from_rel(rel_path: str | None) -> Path | None:
    if not rel_path:
        return None
    p = ROOT / rel_path
    return p

def _save_draft_json(job_id: int, draft: dict) -> str:
    """Write draft JSON to storage/drafts and return RELATIVE path stored in DB."""
    draft_name = f"draft_{job_id}_{uuid.uuid4().hex[:8]}.json"
    abs_path = DRAFTS_FOLDER / draft_name
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(draft, f, indent=2)
    # store relative to repo root
    rel_path = str(abs_path.relative_to(ROOT))
    return rel_path

def run_ocr_and_make_draft(job_id: int, saved_file_path: Path):
    """Background OCR stub: mark processing, emit a simple draft, mark done."""
    try:
        update_import_job(job_id, status="processing")

        suffix = saved_file_path.suffix.lower()
        if suffix in (".png", ".jpg", ".jpeg"):
            # Placeholder "OCR" path; wire your real OCR later
            time.sleep(0.8)
            draft = {
                "job_id": job_id,
                "source": {"type": "upload", "file": saved_file_path.name, "ocr_engine": "tesseract_stub"},
                "extracted_at": _now_iso(),
                "categories": [
                    {"name": "Pizzas", "items": [
                        {"name": "Cheese Pizza", "description": "", "sizes": [{"name": "Small", "price": 9.99}, {"name": "Large", "price": 14.99}]},
                        {"name": "Pepperoni Pizza", "description": "", "sizes": [{"name": "Small", "price": 10.99}, {"name": "Large", "price": 16.49}]}
                    ]}
                ]
            }
        else:
            # PDF or other: stub for now
            time.sleep(0.8)
            draft = {
                "job_id": job_id,
                "source": {"type": "upload", "file": saved_file_path.name, "ocr_engine": "pdf_stub"},
                "extracted_at": _now_iso(),
                "categories": [
                    {"name": "Pizzas", "items": [
                        {"name": "Cheese Pizza", "sizes": [{"name": "Large", "price": 14.99}]},
                        {"name": "Pepperoni Pizza", "sizes": [{"name": "Large", "price": 16.49}]}
                    ]},
                    {"name": "Drinks", "items": [
                        {"name": "Soda", "description": "20oz bottle", "sizes": [{"name": "One Size", "price": 2.49}]}
                    ]}
                ]
            }

        rel_draft_path = _save_draft_json(job_id, draft)
        update_import_job(job_id, status="done", draft_path=rel_draft_path)

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
        # Save first with the original name; job id is created after
        tmp_name = f"{uuid.uuid4().hex[:8]}_{base_name}"
        save_path = UPLOAD_FOLDER / tmp_name
        file.save(str(save_path))

        # create job using the saved filename (we keep the stored name)
        job_id = create_import_job(tmp_name, restaurant_id=None)

        # kick off OCR (background)
        t = threading.Thread(target=run_ocr_and_make_draft, args=(job_id, save_path), daemon=True)
        t.start()

        return jsonify({"job_id": job_id, "status": "pending", "file": tmp_name}), 200

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
        abort(404, description="Job not found")
    data = dict(row)
    abs_draft = _abs_from_rel(row["draft_path"])
    data["draft_ready"] = bool(abs_draft and abs_draft.exists())
    return jsonify(data)

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
# Imports pages (Day 8)
# ------------------------
@app.get("/imports")
@login_required
def imports():
    jobs = list_import_jobs()
    return render_template("imports.html", jobs=jobs)

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

@app.get("/imports/view/<int:job_id>")
@login_required
def imports_view(job_id):
    row = get_import_job(job_id)
    if not row:
        abort(404)
    draft = None
    if row["draft_path"]:
        abs_path = _abs_from_rel(row["draft_path"])
        if abs_path and abs_path.exists():
            with open(abs_path, "r", encoding="utf-8") as f:
                draft = json.load(f)
    return render_template("import_view.html", job=row, draft=draft)

# ------------------------
# Upload serving
# ------------------------
@app.get("/uploads/<path:filename>")
@login_required
def serve_upload(filename):
    return send_from_directory(str(UPLOAD_FOLDER), filename, as_attachment=False)

# ---------- Helpers for review/publish ----------
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

# ------------------------
# Draft Review page (by job id)
# ------------------------
@app.get("/drafts/<int:job_id>")
@login_required
def draft_review_page(job_id):
    draft = _load_draft_json_by_job(job_id)
    with db_connect() as conn:
        restaurants = conn.execute(
            "SELECT id, name FROM restaurants WHERE active=1 ORDER BY id"
        ).fetchall()

    # try to preview original uploaded image if available
    src_file = (draft.get("source", {}) or {}).get("file")
    preview_url = url_for("serve_upload", filename=src_file) if src_file and _is_image(src_file) else None

    return render_template("draft_review.html", draft=draft, restaurants=restaurants, preview_url=preview_url)

# ------------------------
# Publish draft -> create menu + items
# ------------------------
@app.post("/drafts/<int:job_id>/publish")
@login_required
def publish_draft(job_id):
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

    return redirect(url_for("items_page", menu_id=menu_id))

# ------------------------
# Raw OCR viewer (styled, copyable) — optional if you save raw text
# ------------------------
@app.get("/drafts/<int:job_id>/raw")
@login_required
def view_raw(job_id):
    # If you save raw OCR text, it can live under storage/drafts/raw/{job_id}.txt
    raw_file = DRAFTS_FOLDER / "raw" / f"{job_id}.txt"
    if not raw_file.exists():
        abort(404, description="Raw OCR not found for that job")
    raw_text = raw_file.read_text(encoding="utf-8", errors="ignore")
    return render_template("raw.html", raw_text=raw_text, job_id=job_id)

# ------------------------
# Import page (simple forms)
# ------------------------
@app.get("/import")
@login_required
def import_page():
    return render_template("import.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
