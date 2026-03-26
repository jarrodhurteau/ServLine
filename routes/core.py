# portal/routes/core.py
from flask import Blueprint, jsonify, render_template, session, redirect, url_for
from datetime import datetime

core_bp = Blueprint("core", __name__)

@core_bp.get("/")
def index():
    # Logged-in customers go to dashboard, visitors see landing page
    if session.get("user") and session.get("role") == "customer":
        return redirect("/dashboard")
    try:
        return render_template("index.html")
    except Exception:
        return "<h1>Index</h1><p>Template missing.</p>", 200

@core_bp.get("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat(timespec="seconds") + "Z"})
