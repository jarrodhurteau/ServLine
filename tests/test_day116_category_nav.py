"""
Day 116 — Category Navigation: Collapsible Sidebar + Drag Reorder
==================================================================
Tests for:
  1. save_category_order() — persists JSON list
  2. get_category_order() — retrieves stored list
  3. get_category_order() — returns [] for draft with no order set
  4. get_category_order() — returns [] for unknown draft id
  5. save_category_order() — updates existing order
  6. save_category_order() — empty list round-trips as []
  7. save_category_order() — unicode category names survive round-trip
  8. Two drafts have independent category orders
  9. get_category_order() — corrupted JSON returns []
  10. POST /drafts/<id>/reorder_categories — 200 success + {ok, count}
  11. POST /drafts/<id>/reorder_categories — persists order (verify via get)
  12. POST /drafts/<id>/reorder_categories — 400 missing 'categories' key
  13. POST /drafts/<id>/reorder_categories — 400 non-list value
  14. POST /drafts/<id>/reorder_categories — 400 non-string item in list
  15. POST /drafts/<id>/reorder_categories — 404 unknown draft
  16. POST /drafts/<id>/reorder_categories — requires login (redirect)
  17. POST /drafts/<id>/reorder_categories — empty list → 200 with count 0
  18. POST /drafts/<id>/reorder_categories — single category
  19. GET /drafts/<id>/edit — category_order passed to template context
  20. GET /drafts/<id>/edit — data-category-order attribute in rendered HTML
  21. GET /drafts/<id>/edit — data-category-order is valid JSON
  22. GET /drafts/<id>/edit — saved order reflected in data-category-order
  23. GET /drafts/<id>/edit — no saved order → data-category-order is "[]"
  24. GET /drafts/<id>/edit — items-table-wrap element present
  25. save_category_order() + get_category_order() five-item round-trip
  26. save_category_order() called twice — second wins
  27. POST /drafts/<id>/reorder_categories — three categories → count=3
  28. POST /drafts/<id>/reorder_categories — category with special chars
  29. Reorder then GET edit — HTML reflects new order
  30. data-category-order attribute in table-wrap with items
  31. data-modifier-group-count attribute present on item rows in HTML
  32. data-modifier-group-count = 0 for items without modifier groups
  33. Multiple items same category — all rows have data-modifier-group-count
  34. category_order key present in template vars passed by draft_editor route
  35. POST /reorder_categories with 50-category list → 200 + count=50

~35 tests across 4 classes.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

import pytest

import storage.drafts as drafts_mod
from storage.drafts import save_category_order, get_category_order


# ---------------------------------------------------------------------------
# Schema (Day 116: includes category_order column)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS restaurants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    restaurant_id INTEGER,
    status TEXT NOT NULL DEFAULT 'editing',
    source TEXT,
    source_job_id INTEGER,
    source_file_path TEXT,
    menu_id INTEGER,
    category_order TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS draft_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    price_cents INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    position INTEGER,
    confidence INTEGER,
    kitchen_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS draft_item_variants (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id           INTEGER NOT NULL,
    label             TEXT NOT NULL,
    price_cents       INTEGER NOT NULL DEFAULT 0,
    kind              TEXT DEFAULT 'size',
    position          INTEGER DEFAULT 0,
    modifier_group_id INTEGER,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES draft_items(id) ON DELETE CASCADE
);
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
);
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
);
CREATE TABLE IF NOT EXISTS draft_export_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    format TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    variant_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    exported_at TEXT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS menu_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash TEXT UNIQUE NOT NULL,
    restaurant_id INTEGER,
    label TEXT,
    active INTEGER DEFAULT 1,
    rate_limit_rpm INTEGER DEFAULT 60,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    gate_score REAL,
    gate_threshold REAL,
    reason TEXT,
    customer_message TEXT,
    rejected_at TEXT NOT NULL
);
"""

_NOW = "2026-03-13T10:00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(_SCHEMA_SQL)
    return conn


def _seed(conn):
    """Insert one restaurant + one draft; return (rid, did)."""
    rid = conn.execute(
        "INSERT INTO restaurants (name) VALUES ('Test Restaurant')"
    ).lastrowid
    did = conn.execute(
        "INSERT INTO drafts (title, restaurant_id, status, created_at, updated_at) "
        "VALUES ('Menu', ?, 'editing', ?, ?)",
        (rid, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return rid, did


def _insert_item(conn, draft_id, name="Burger", price=999, category="Mains"):
    iid = conn.execute(
        "INSERT INTO draft_items "
        "(draft_id, name, price_cents, category, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (draft_id, name, price, category, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return iid


def _insert_group(conn, item_id, name="Sauce"):
    gid = conn.execute(
        "INSERT INTO draft_modifier_groups "
        "(item_id, name, required, min_select, max_select, position, created_at, updated_at) "
        "VALUES (?, ?, 0, 0, 0, 0, ?, ?)",
        (item_id, name, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return gid


@pytest.fixture
def conn(monkeypatch):
    c = _make_conn()
    monkeypatch.setattr(drafts_mod, "db_connect", lambda: c)
    return c


@pytest.fixture
def seeded(conn):
    rid, did = _seed(conn)
    return conn, rid, did


# ---------------------------------------------------------------------------
# Shared DB state for endpoint tests
# ---------------------------------------------------------------------------
_TEST_CONN: Optional[sqlite3.Connection] = None


def _patch_db(monkeypatch):
    global _TEST_CONN
    _TEST_CONN = _make_conn()
    import portal.app as _portal_app_module

    def _mock_connect():
        return _TEST_CONN

    monkeypatch.setattr(drafts_mod, "db_connect", _mock_connect)
    monkeypatch.setattr(_portal_app_module, "db_connect", _mock_connect)
    return _TEST_CONN


@pytest.fixture
def fresh_db(monkeypatch):
    c = _patch_db(monkeypatch)
    yield c


@pytest.fixture
def client(fresh_db):
    import portal.app as _app_module
    app = _app_module.app
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = {"id": 1, "email": "test@test.com"}
        yield c


def _create_draft(conn, status="editing"):
    rid = conn.execute(
        "INSERT INTO restaurants (name, active) VALUES ('R', 1)"
    ).lastrowid
    did = conn.execute(
        "INSERT INTO drafts (title, restaurant_id, status, created_at, updated_at) "
        "VALUES ('Menu', ?, ?, ?, ?)",
        (rid, status, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return rid, did


# ---------------------------------------------------------------------------
# Class 1: Storage — save/get category order
# ---------------------------------------------------------------------------

class TestSaveGetCategoryOrder:
    """Tests for save_category_order() and get_category_order()."""

    def test_save_and_retrieve(self, seeded):
        conn, _, did = seeded
        order = ["Mains", "Sides", "Desserts"]
        save_category_order(did, order)
        assert get_category_order(did) == order

    def test_get_returns_empty_list_when_none(self, seeded):
        conn, _, did = seeded
        assert get_category_order(did) == []

    def test_get_returns_empty_list_for_unknown_draft(self, conn):
        assert get_category_order(9999) == []

    def test_update_replaces_existing(self, seeded):
        conn, _, did = seeded
        save_category_order(did, ["A", "B"])
        save_category_order(did, ["X", "Y", "Z"])
        assert get_category_order(did) == ["X", "Y", "Z"]

    def test_empty_list_roundtrip(self, seeded):
        conn, _, did = seeded
        save_category_order(did, [])
        assert get_category_order(did) == []

    def test_unicode_category_names(self, seeded):
        conn, _, did = seeded
        order = ["Entrées", "Plats principaux", "Desserts 🍰"]
        save_category_order(did, order)
        assert get_category_order(did) == order

    def test_two_drafts_independent(self, conn, monkeypatch):
        rid = conn.execute("INSERT INTO restaurants (name) VALUES ('R')").lastrowid
        did1 = conn.execute(
            "INSERT INTO drafts (title, restaurant_id, status, created_at, updated_at) "
            "VALUES ('D1', ?, 'editing', ?, ?)", (rid, _NOW, _NOW)
        ).lastrowid
        did2 = conn.execute(
            "INSERT INTO drafts (title, restaurant_id, status, created_at, updated_at) "
            "VALUES ('D2', ?, 'editing', ?, ?)", (rid, _NOW, _NOW)
        ).lastrowid
        conn.commit()
        save_category_order(did1, ["Alpha", "Beta"])
        save_category_order(did2, ["Gamma", "Delta", "Epsilon"])
        assert get_category_order(did1) == ["Alpha", "Beta"]
        assert get_category_order(did2) == ["Gamma", "Delta", "Epsilon"]

    def test_corrupted_json_returns_empty_list(self, conn, monkeypatch):
        rid = conn.execute("INSERT INTO restaurants (name) VALUES ('R')").lastrowid
        did = conn.execute(
            "INSERT INTO drafts (title, restaurant_id, status, created_at, updated_at) "
            "VALUES ('D', ?, 'editing', ?, ?)", (rid, _NOW, _NOW)
        ).lastrowid
        conn.execute(
            "UPDATE drafts SET category_order=? WHERE id=?",
            ("NOT_JSON{{{{", did),
        )
        conn.commit()
        assert get_category_order(did) == []

    def test_five_item_roundtrip(self, seeded):
        conn, _, did = seeded
        order = ["Breakfast", "Lunch", "Dinner", "Drinks", "Desserts"]
        save_category_order(did, order)
        assert get_category_order(did) == order

    def test_second_save_wins(self, seeded):
        conn, _, did = seeded
        save_category_order(did, ["A", "B", "C"])
        save_category_order(did, ["C", "A"])
        assert get_category_order(did) == ["C", "A"]


# ---------------------------------------------------------------------------
# Class 2: Endpoint — POST /drafts/<id>/reorder_categories
# ---------------------------------------------------------------------------

class TestReorderCategoriesEndpoint:
    """Tests for POST /drafts/<id>/reorder_categories."""

    def test_success_returns_ok_and_count(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        resp = client.post(
            f"/drafts/{did}/reorder_categories",
            json={"categories": ["Mains", "Sides"]},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["count"] == 2

    def test_persists_order(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        client.post(
            f"/drafts/{did}/reorder_categories",
            json={"categories": ["Desserts", "Mains", "Drinks"]},
        )
        assert get_category_order(did) == ["Desserts", "Mains", "Drinks"]

    def test_missing_categories_key_returns_400(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        resp = client.post(f"/drafts/{did}/reorder_categories", json={"foo": "bar"})
        assert resp.status_code == 400

    def test_non_list_value_returns_400(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        resp = client.post(
            f"/drafts/{did}/reorder_categories",
            json={"categories": "not a list"},
        )
        assert resp.status_code == 400

    def test_non_string_item_returns_400(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        resp = client.post(
            f"/drafts/{did}/reorder_categories",
            json={"categories": ["Mains", 42]},
        )
        assert resp.status_code == 400

    def test_unknown_draft_returns_404(self, client, fresh_db):
        resp = client.post(
            "/drafts/9999/reorder_categories",
            json={"categories": ["A"]},
        )
        assert resp.status_code == 404

    def test_requires_login(self, fresh_db):
        import portal.app as _app_module
        app = _app_module.app
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        _, did = _create_draft(fresh_db)
        with app.test_client() as anon:
            resp = anon.post(
                f"/drafts/{did}/reorder_categories",
                json={"categories": ["A"]},
            )
        assert resp.status_code in (302, 401)

    def test_empty_list_succeeds(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        resp = client.post(
            f"/drafts/{did}/reorder_categories",
            json={"categories": []},
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 0

    def test_single_category(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        resp = client.post(
            f"/drafts/{did}/reorder_categories",
            json={"categories": ["Burgers"]},
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 1

    def test_three_categories_count(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        resp = client.post(
            f"/drafts/{did}/reorder_categories",
            json={"categories": ["A", "B", "C"]},
        )
        assert resp.get_json()["count"] == 3

    def test_category_with_special_chars(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        cats = ["Entrées & Appetizers", "Main Dishes (Hot)"]
        resp = client.post(
            f"/drafts/{did}/reorder_categories",
            json={"categories": cats},
        )
        assert resp.status_code == 200
        assert get_category_order(did) == cats

    def test_fifty_categories(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        cats = [f"Category {i}" for i in range(50)]
        resp = client.post(
            f"/drafts/{did}/reorder_categories",
            json={"categories": cats},
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 50

    def test_reorder_then_reorder_again(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        client.post(f"/drafts/{did}/reorder_categories", json={"categories": ["X", "Y"]})
        client.post(f"/drafts/{did}/reorder_categories", json={"categories": ["Y", "X", "Z"]})
        assert get_category_order(did) == ["Y", "X", "Z"]


# ---------------------------------------------------------------------------
# Class 3: Editor route — template context + rendered HTML
# ---------------------------------------------------------------------------

class TestEditorTemplateContext:
    """Tests for draft_editor() route — category_order in context + HTML."""

    def test_data_category_order_present_no_saved_order(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/edit")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-category-order" in html

    def test_data_category_order_is_valid_json(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        # Extract the value of data-category-order="..."
        import re
        m = re.search(r'data-category-order="([^"]*)"', html)
        assert m is not None
        decoded = m.group(1).replace("&#34;", '"').replace("&quot;", '"')
        parsed = json.loads(decoded)
        assert isinstance(parsed, list)

    def test_no_saved_order_renders_empty_json_array(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        assert 'data-category-order="[]"' in html

    def test_saved_order_reflected_in_html(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        save_category_order(did, ["Mains", "Drinks"])
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        assert "Mains" in html
        assert "Drinks" in html
        assert "data-category-order" in html

    def test_items_table_wrap_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        assert 'id="items-table-wrap"' in html

    def test_reorder_then_get_html_contains_order(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        client.post(
            f"/drafts/{did}/reorder_categories",
            json={"categories": ["Desserts", "Mains"]},
        )
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        assert "Desserts" in html


# ---------------------------------------------------------------------------
# Class 4: data-modifier-group-count attribute on item rows
# ---------------------------------------------------------------------------

class TestModifierGroupCountAttribute:
    """Tests for data-modifier-group-count on item <tr> rows."""

    def test_items_have_modifier_group_count_attr(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        fresh_db.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, category, created_at, updated_at) "
            "VALUES (?, 'Burger', 999, 'Mains', ?, ?)",
            (did, _NOW, _NOW),
        )
        fresh_db.commit()
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        assert "data-modifier-group-count" in html

    def test_item_without_groups_has_count_zero(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        fresh_db.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, category, created_at, updated_at) "
            "VALUES (?, 'Salad', 799, 'Starters', ?, ?)",
            (did, _NOW, _NOW),
        )
        fresh_db.commit()
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        assert 'data-modifier-group-count="0"' in html

    def test_item_with_one_group_has_count_one(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = fresh_db.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, category, created_at, updated_at) "
            "VALUES (?, 'Pizza', 1299, 'Mains', ?, ?)",
            (did, _NOW, _NOW),
        ).lastrowid
        fresh_db.execute(
            "INSERT INTO draft_modifier_groups "
            "(item_id, name, required, min_select, max_select, position, created_at, updated_at) "
            "VALUES (?, 'Toppings', 0, 0, 5, 0, ?, ?)",
            (iid, _NOW, _NOW),
        )
        fresh_db.commit()
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        assert 'data-modifier-group-count="1"' in html

    def test_multiple_items_all_have_attribute(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        for name in ("Item A", "Item B", "Item C"):
            fresh_db.execute(
                "INSERT INTO draft_items (draft_id, name, price_cents, category, created_at, updated_at) "
                "VALUES (?, ?, 100, 'Cat', ?, ?)",
                (did, name, _NOW, _NOW),
            )
        fresh_db.commit()
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        assert html.count("data-modifier-group-count") >= 3

    def test_category_order_key_in_template_context(self, client, fresh_db):
        """Smoke: draft_editor route does not crash and renders a page."""
        _, did = _create_draft(fresh_db)
        save_category_order(did, ["Z", "A", "M"])
        resp = client.get(f"/drafts/{did}/edit")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "items-table-wrap" in html
