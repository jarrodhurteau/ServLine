# portal/routes/core.py
from flask import Blueprint, jsonify, render_template
from datetime import datetime

core_bp = Blueprint("core", __name__)

@core_bp.get("/")
def index():
    # keep behavior identical to previous index()
    try:
        return render_template("index.html")
    except Exception:
        # minimal fallback so a missing template never crashes the app
        return "<h1>Index</h1><p>Template missing.</p>", 200

@core_bp.get("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat(timespec="seconds") + "Z"})
