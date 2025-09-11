# portal/app.py
from flask import (
    Flask, jsonify, render_template, abort, request, redirect, url_for,
    session, send_from_directory, flash
)
import sqlite3
from pathlib import Path
from functools import wraps
import uuid
import os
import threading
import time
import json
import shutil
from datetime import datetime
from typing import Optional, Iterable, Tuple

# safer filename + big-file error handling
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

# OCR health imports
from importlib.util import find_spec
import pytesseract

app = Flask(__name__)

# --- Config (dev) ---
app.config["SECRET_KEY"] = "dev-secret-change-me"          # replace later with env var
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024        # ~20 MB
DEV_USERNAME = "admin"
DEV_PASSWORD = "letmein"

# --- Paths ---
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "servline.db"

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
# Template globals
# ------------------------
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
            (upload_name,),
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
# Health / DB / OCR Health
# ------------------------
@app.get("/health")
def health():
    return jsonify({"status": "ok"})

@app.get("/ocr/health")
def ocr_health():
    tess_cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "")
    tess_path_exists = bool(tess_cmd) and Path(tess_cmd).exists()
    try:
        tess_version = str(pytesseract.get_tesseract_version())
    except Exception:
        tess_version = None

    have_pandas = find_spec("pandas") is not None
    have_sklearn = find_spec("sklearn") is not None
    column_mode = "enabled" if (have_pandas and have_sklearn) else "fallback"

    return jsonify({
        "tesseract": {"cmd": tess_cmd, "found_on_disk": tess_path_exists, "version": tess_version},
        "columns": {"pandas": have_pandas, "scikit_learn": have_sklearn, "mode": column_mode}
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

# ------------------------
# Import flow: Upload -> Job -> Worker -> Draft JSON
# ------------------------
def _abs_from_rel(rel_path: Optional[str]) -> Optional[Path]:
    if not rel_path:
        return None
    return (ROOT / rel_path).resolve()

def _save_draft_json(job_id: int, draft: dict) -> str:
    draft_name = f"draft_{job_id}_{uuid.uuid4().hex[:8]}.json"
    abs_path = DRAFTS_FOLDER / draft_name
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(draft, f, indent=2)
    return str(abs_path.relative_to(ROOT)).replace("\\", "/")

def run_ocr_and_make_draft(job_id: int, saved_file_path: Path):
    try:
        update_import_job(job_id, status="processing")

        # (Stubbed OCR for dev)
        time.sleep(0.8)
        if saved_file_path.suffix.lower() in (".png", ".jpg", ".jpeg"):
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

        # OPTIONAL raw dump for debugging
        try:
            (RAW_FOLDER / f"{job_id}.txt").write_text("stub raw OCR\n", encoding="utf-8")
        except Exception:
            pass

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
        tmp_name = f"{uuid.uuid4().hex[:8]}_{base_name}"
        save_path = UPLOAD_FOLDER / tmp_name
        file.save(str(save_path))

        job_id = create_import_job(filename=tmp_name, restaurant_id=None)

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
        abort(404)
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
        rows = conn.execute("SELECT * FROM restaurants WHERE active=1 ORDER BY id").fetchall()
    return render_template("restaurants.html", restaurants=rows)

@app.get("/restaurants/<int:rest_id>/menus")
def menus_page(rest_id):
    with db_connect() as conn:
        rest = conn.execute("SELECT * FROM restaurants WHERE id=?", (rest_id,)).fetchone()
        menus = conn.execute(
            "SELECT * FROM menus WHERE restaurant_id=? AND active=1 ORDER BY id", (rest_id,),
        ).fetchall()
    if not rest:
        abort(404)
    return render_template("menus.html", restaurant=rest, menus=menus)

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
        return render_template("items.html", restaurant=rest, menu=menu, items=items)

# ------------------------
# Day 6: Auth (Login / Logout) — PRG + flashes
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
        flash("Welcome back!", "success")
        return redirect(nxt)  # PRG
    flash("Invalid credentials", "error")
    # Redirect back to login to avoid re-POST on refresh (PRG)
    return redirect(url_for("login", next=request.form.get("next") or ""))

@app.post("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
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
# Imports pages (Day 8 behaviors)
# ------------------------
@app.get("/imports")
@login_required
def imports():
    jobs = list_import_jobs()  # hide status='deleted'
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

# --- Cleanup & per-job delete (soft delete via status) ---
@app.route("/imports/cleanup", methods=["GET", "POST"])
@login_required
def imports_cleanup():
    """Soft-delete any import_jobs whose original upload file is gone."""
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
    # any draft json that references this upload
    u = upload_name.lower()
    for p in _iter_draft_json_files():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            src_file = (((data or {}).get("source") or {}).get("file") or "").strip()
            if Path(src_file).name.lower() == u:
                _trash_draft_file(p, ts)
        except Exception:
            continue
    # raw dumps with filename hints
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

    moved: list[str] = []
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
    restored: list[Tuple[str, str]] = []
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
    # move loose raw files to their trash buckets
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
    return render_template("uploads.html", files=files)

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
    return render_template("uploads_trash.html", trashed=trashed, err_note=err_note)

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

# --- Buttons referenced by template ---
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

# --- Artifact Sweep button on bin page ---
@app.post("/admin/artifacts/sweep")
@login_required
def artifacts_sweep():
    report = _sweep_artifacts()
    return jsonify({"status": "ok", **report})

# ------------------------
# Draft Review & Publish
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
def draft_review_page(job_id):
    draft = _load_draft_json_by_job(job_id)
    with db_connect() as conn:
        restaurants = conn.execute(
            "SELECT id, name FROM restaurants WHERE active=1 ORDER BY id"
        ).fetchall()
    src_file = (draft.get("source", {}) or {}).get("file")
    preview_url = url_for("serve_upload", filename=src_file) if src_file and _is_image(src_file) else None
    return render_template("draft_review.html", draft=draft, restaurants=restaurants, preview_url=preview_url)

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

    flash(f"Published draft #{job_id} to menu #{menu_id}.", "success")
    return redirect(url_for("items_page", menu_id=menu_id))

# ------------------------
# Raw OCR viewer
# ------------------------
@app.get("/drafts/<int:job_id>/raw")
@login_required
def view_raw(job_id):
    candidates = [
        RAW_FOLDER / f"{job_id}.txt",
        LEGACY_RAW_FOLDER / f"{job_id}.txt",
    ]
    for raw_file in candidates:
        if raw_file.exists():
            raw_text = raw_file.read_text(encoding="utf-8", errors="ignore")
            return render_template("raw.html", raw_text=raw_text, job_id=job_id)
    abort(404, description="Raw OCR not found for that job")

# ------------------------
# Import landing page + HTML POST handler
# ------------------------
@app.get("/import")
@login_required
def import_page():
    return render_template("import.html")

@app.post("/import")
@login_required
def import_upload():
    """HTML handler: save upload, start OCR job, then refresh with a flash."""
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

        job_id = create_import_job(filename=tmp_name, restaurant_id=None)
        threading.Thread(
            target=run_ocr_and_make_draft, args=(job_id, save_path), daemon=True
        ).start()

        flash(f"Import started for {base_name} (job #{job_id}).", "success")
    except RequestEntityTooLarge:
        flash("File too large. Try a smaller file or raise MAX_CONTENT_LENGTH.", "error")
    except Exception as e:
        flash(f"Server error while saving upload: {e}", "error")
    return redirect(url_for("import_page"))

# ------------------------
# Error handlers (Day 10)
# ------------------------
@app.errorhandler(404)
def not_found(e):
    return render_template("errors/404.html"), 404

@app.errorhandler(500)
def server_error(e):
    # Do not leak error details; rely on logs for specifics in dev.
    return render_template("errors/500.html"), 500

# ------------------------
# Run
# ------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
