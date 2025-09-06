from flask import Flask, jsonify, render_template, abort, request, redirect, url_for
import sqlite3
from pathlib import Path

app = Flask(__name__)

# --- DB path ---
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "storage" / "servline.db"

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ------------------------
# Health / DB Health
# ------------------------
@app.get("/health")
def health():
    return jsonify({"status": "ok"})

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
# JSON API
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
            "SELECT * FROM menus WHERE restaurant_id=? AND active=1 ORDER BY id",
            (rest_id,),
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
            "SELECT * FROM menu_items WHERE menu_id=? AND is_available=1 ORDER BY id",
            (menu_id,),
        ).fetchall()
    return render_template("items.html", restaurant=rest, menu=menu, items=items)

# ------------------------
# Day 5: Admin Forms (Add Menu Items)
# ------------------------
@app.get("/menus/<int:menu_id>/items/new")
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
def create_item(menu_id):
    # Basic validation & parsing
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    price_raw = (request.form.get("price") or "0").strip()

    if not name:
        abort(400, description="Name is required")

    try:
        # Accept 7.99 style input; store as integer cents
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
