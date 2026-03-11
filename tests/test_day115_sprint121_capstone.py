"""
Day 115 — Sprint 12.1 Capstone: POS Export Upgrade + Modifier Group CRUD Endpoints
=====================================================================================
Tests for:
  1. _build_generic_pos_json() — POS-native modifier_groups output
  2. _build_generic_pos_json() — kitchen_name included when set
  3. _build_generic_pos_json() — legacy flat fallback (no modifier groups)
  4. _build_generic_pos_json() — mixed: some items with groups, some without
  5. _build_generic_pos_json() — metadata version bump to 1.1
  6. _build_generic_pos_json() — ungrouped variants alongside modifier groups
  7. POST /drafts/<id>/items/<iid>/modifier_groups — add modifier group
  8. POST /drafts/<id>/items/<iid>/modifier_groups — missing name → 400
  9. POST /drafts/<id>/items/<iid>/modifier_groups — bad integer field → 400
  10. PATCH /drafts/<id>/modifier_groups/<gid> — update name + required
  11. PATCH /drafts/<id>/modifier_groups/<gid> — update min/max/position
  12. PATCH /drafts/<id>/modifier_groups/<gid> — not found → 404
  13. PATCH /drafts/<id>/modifier_groups/<gid> — empty name → 400
  14. PATCH /drafts/<id>/modifier_groups/<gid> — no valid fields → 400
  15. Full round-trip: save with modifier groups → load nested → export POS JSON
  16. GET /drafts/<id>/export_pos.json — returns modifier_groups in JSON

~36 tests across 5 classes.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

import pytest

import storage.drafts as drafts_mod
from storage.drafts import (
    insert_modifier_group,
    update_modifier_group,
    get_modifier_group,
    get_modifier_groups,
    get_draft_items,
    upsert_draft_items,
)


# ---------------------------------------------------------------------------
# Schema (Day 115: full Sprint 12.1 schema)
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
"""

_NOW = "2026-03-11T10:00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(_SCHEMA_SQL)
    return conn


def _seed(conn):
    """Return (restaurant_id, draft_id)."""
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


def _insert_item(conn, draft_id, name="Burger", price=999, category="Mains", kitchen=None):
    iid = conn.execute(
        "INSERT INTO draft_items "
        "(draft_id, name, price_cents, category, kitchen_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (draft_id, name, price, category, kitchen, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return iid


def _insert_variant(conn, item_id, label="Small", price=0, kind="size", group_id=None):
    vid = conn.execute(
        "INSERT INTO draft_item_variants "
        "(item_id, label, price_cents, kind, position, modifier_group_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
        (item_id, label, price, kind, group_id, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return vid


def _insert_group(conn, item_id, name="Size", required=0, min_s=0, max_s=0, pos=0):
    gid = conn.execute(
        "INSERT INTO draft_modifier_groups "
        "(item_id, name, required, min_select, max_select, position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, name, required, min_s, max_s, pos, _NOW, _NOW),
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
# DB shared state for endpoint tests
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


def _create_draft(conn):
    rid = conn.execute(
        "INSERT INTO restaurants (name, active) VALUES ('R', 1)"
    ).lastrowid
    did = conn.execute(
        "INSERT INTO drafts (title, restaurant_id, status, created_at, updated_at) "
        "VALUES ('Menu', ?, 'editing', ?, ?)",
        (rid, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return rid, did


# ---------------------------------------------------------------------------
# Class 1: _build_generic_pos_json() — POS-native modifier groups
# ---------------------------------------------------------------------------

class TestBuildGenericPosJson:
    """Tests for the upgraded _build_generic_pos_json() helper."""

    def _fn(self):
        from portal.app import _build_generic_pos_json
        return _build_generic_pos_json

    def test_no_items_returns_empty_categories(self):
        fn = self._fn()
        out = fn([], {"id": 1, "title": "Test"})
        assert out["menu"]["categories"] == []
        assert out["metadata"]["item_count"] == 0

    def test_metadata_version_is_1_1(self):
        fn = self._fn()
        out = fn([], {})
        assert out["metadata"]["version"] == "1.1"
        assert out["metadata"]["format"] == "generic_pos"

    def test_item_no_modifier_groups_no_variants_empty_modifiers(self):
        fn = self._fn()
        items = [{"name": "Fries", "price_cents": 350, "category": "Sides"}]
        out = fn(items, {})
        cat_items = out["menu"]["categories"][0]["items"]
        assert len(cat_items) == 1
        assert cat_items[0]["modifier_groups"] == []
        assert cat_items[0]["modifiers"] == []

    def test_item_with_ungrouped_variants_uses_flat_format(self):
        fn = self._fn()
        items = [{
            "name": "Soda",
            "price_cents": 200,
            "category": "Drinks",
            "variants": [
                {"label": "Small", "price_cents": 150, "kind": "size"},
                {"label": "Large", "price_cents": 250, "kind": "size"},
            ],
        }]
        out = fn(items, {})
        item = out["menu"]["categories"][0]["items"][0]
        assert item["modifier_groups"] == []
        assert len(item["modifiers"]) == 2
        assert item["modifiers"][0]["group"] == "Size"
        assert item["modifiers"][0]["name"] == "Small"

    def test_item_with_modifier_groups_uses_nested_format(self):
        fn = self._fn()
        items = [{
            "name": "Burger",
            "price_cents": 999,
            "category": "Mains",
            "modifier_groups": [{
                "name": "Add-ons",
                "required": False,
                "min_select": 0,
                "max_select": 3,
                "modifiers": [
                    {"label": "Bacon", "price_cents": 150},
                    {"label": "Cheese", "price_cents": 100},
                ],
            }],
            "ungrouped_variants": [],
        }]
        out = fn(items, {})
        item = out["menu"]["categories"][0]["items"][0]
        assert len(item["modifier_groups"]) == 1
        grp = item["modifier_groups"][0]
        assert grp["name"] == "Add-ons"
        assert grp["required"] is False
        assert grp["min_select"] == 0
        assert grp["max_select"] == 3
        assert len(grp["modifiers"]) == 2
        assert grp["modifiers"][0]["name"] == "Bacon"
        assert grp["modifiers"][0]["price"] == "1.50"

    def test_modifier_groups_also_populate_flat_modifiers(self):
        fn = self._fn()
        items = [{
            "name": "Pizza",
            "price_cents": 1200,
            "category": "Mains",
            "modifier_groups": [{
                "name": "Size",
                "required": True,
                "min_select": 1,
                "max_select": 1,
                "modifiers": [
                    {"label": "Small", "price_cents": 1000},
                    {"label": "Large", "price_cents": 1500},
                ],
            }],
            "ungrouped_variants": [],
        }]
        out = fn(items, {})
        item = out["menu"]["categories"][0]["items"][0]
        assert len(item["modifiers"]) == 2
        assert item["modifiers"][0]["group"] == "Size"
        assert item["modifiers"][1]["name"] == "Large"

    def test_required_true_preserved_in_pos_output(self):
        fn = self._fn()
        items = [{
            "name": "Wrap",
            "price_cents": 850,
            "category": "Mains",
            "modifier_groups": [{
                "name": "Protein",
                "required": True,
                "min_select": 1,
                "max_select": 1,
                "modifiers": [{"label": "Chicken", "price_cents": 0}],
            }],
            "ungrouped_variants": [],
        }]
        out = fn(items, {})
        grp = out["menu"]["categories"][0]["items"][0]["modifier_groups"][0]
        assert grp["required"] is True
        assert grp["min_select"] == 1

    def test_kitchen_name_included_when_set(self):
        fn = self._fn()
        items = [{
            "name": "Grilled Salmon",
            "kitchen_name": "SALMON",
            "price_cents": 2200,
            "category": "Entrees",
        }]
        out = fn(items, {})
        item = out["menu"]["categories"][0]["items"][0]
        assert item.get("kitchen_name") == "SALMON"

    def test_kitchen_name_omitted_when_empty(self):
        fn = self._fn()
        items = [{
            "name": "Fries",
            "kitchen_name": "",
            "price_cents": 350,
            "category": "Sides",
        }]
        out = fn(items, {})
        item = out["menu"]["categories"][0]["items"][0]
        assert "kitchen_name" not in item

    def test_kitchen_name_omitted_when_none(self):
        fn = self._fn()
        items = [{"name": "Fries", "price_cents": 350, "category": "Sides"}]
        out = fn(items, {})
        item = out["menu"]["categories"][0]["items"][0]
        assert "kitchen_name" not in item

    def test_multiple_modifier_groups_per_item(self):
        fn = self._fn()
        items = [{
            "name": "Bowl",
            "price_cents": 1100,
            "category": "Bowls",
            "modifier_groups": [
                {
                    "name": "Base",
                    "required": True,
                    "min_select": 1,
                    "max_select": 1,
                    "modifiers": [{"label": "Rice", "price_cents": 0}],
                },
                {
                    "name": "Protein",
                    "required": True,
                    "min_select": 1,
                    "max_select": 1,
                    "modifiers": [{"label": "Chicken", "price_cents": 200}],
                },
            ],
            "ungrouped_variants": [],
        }]
        out = fn(items, {})
        item = out["menu"]["categories"][0]["items"][0]
        assert len(item["modifier_groups"]) == 2
        assert item["modifier_groups"][0]["name"] == "Base"
        assert item["modifier_groups"][1]["name"] == "Protein"
        # flat modifiers = 2 total (one per group)
        assert len(item["modifiers"]) == 2

    def test_ungrouped_variants_alongside_modifier_groups(self):
        """Items with both modifier_groups and ungrouped_variants."""
        fn = self._fn()
        items = [{
            "name": "Tacos",
            "price_cents": 800,
            "category": "Mains",
            "modifier_groups": [{
                "name": "Protein",
                "required": True,
                "min_select": 1,
                "max_select": 1,
                "modifiers": [{"label": "Beef", "price_cents": 0}],
            }],
            "ungrouped_variants": [
                {"label": "Extra Salsa", "price_cents": 50, "kind": "other"},
            ],
        }]
        out = fn(items, {})
        item = out["menu"]["categories"][0]["items"][0]
        # modifier_groups has 1 group
        assert len(item["modifier_groups"]) == 1
        # flat modifiers = 1 (from group) + 1 (ungrouped) = 2
        assert len(item["modifiers"]) == 2
        assert item["modifiers"][1]["group"] == "Option"
        assert item["modifiers"][1]["name"] == "Extra Salsa"

    def test_mixed_items_some_with_groups_some_without(self):
        fn = self._fn()
        items = [
            {
                "name": "Burger",
                "price_cents": 999,
                "category": "Mains",
                "modifier_groups": [{
                    "name": "Add-ons",
                    "required": False,
                    "min_select": 0,
                    "max_select": 2,
                    "modifiers": [{"label": "Bacon", "price_cents": 150}],
                }],
                "ungrouped_variants": [],
            },
            {
                "name": "Fries",
                "price_cents": 350,
                "category": "Mains",
                "variants": [
                    {"label": "Small", "price_cents": 299, "kind": "size"},
                ],
            },
        ]
        out = fn(items, {})
        cat_items = out["menu"]["categories"][0]["items"]
        # Sorted by name alphabetically (Burger < Fries)
        burger = next(i for i in cat_items if i["name"] == "Burger")
        fries = next(i for i in cat_items if i["name"] == "Fries")
        assert len(burger["modifier_groups"]) == 1
        assert burger["modifier_groups"][0]["name"] == "Add-ons"
        assert fries["modifier_groups"] == []
        assert len(fries["modifiers"]) == 1

    def test_categories_sorted_alphabetically(self):
        fn = self._fn()
        items = [
            {"name": "A", "price_cents": 100, "category": "Zebra"},
            {"name": "B", "price_cents": 200, "category": "Apple"},
        ]
        out = fn(items, {})
        cats = [c["name"] for c in out["menu"]["categories"]]
        assert cats == ["Apple", "Zebra"]

    def test_base_price_formatted_as_dollars(self):
        fn = self._fn()
        items = [{"name": "Steak", "price_cents": 2599, "category": "Mains"}]
        out = fn(items, {})
        item = out["menu"]["categories"][0]["items"][0]
        assert item["base_price"] == "25.99"

    def test_modifier_price_formatted_as_dollars(self):
        fn = self._fn()
        items = [{
            "name": "Latte",
            "price_cents": 450,
            "category": "Coffee",
            "modifier_groups": [{
                "name": "Size",
                "required": True,
                "min_select": 1,
                "max_select": 1,
                "modifiers": [{"label": "Large", "price_cents": 575}],
            }],
            "ungrouped_variants": [],
        }]
        out = fn(items, {})
        mod = out["menu"]["categories"][0]["items"][0]["modifier_groups"][0]["modifiers"][0]
        assert mod["price"] == "5.75"


# ---------------------------------------------------------------------------
# Class 2: insert_modifier_group() + update_modifier_group() (storage)
# ---------------------------------------------------------------------------

class TestStorageModifierGroupCrud:
    """Storage-layer CRUD for individual modifier groups."""

    def test_insert_modifier_group_returns_id(self, seeded):
        conn, rid, did = seeded
        iid = _insert_item(conn, did)
        gid = insert_modifier_group(iid, "Toppings")
        assert isinstance(gid, int)
        assert gid > 0

    def test_insert_modifier_group_defaults(self, seeded):
        conn, rid, did = seeded
        iid = _insert_item(conn, did)
        gid = insert_modifier_group(iid, "Extras")
        grp = get_modifier_group(gid)
        assert grp["name"] == "Extras"
        assert grp["required"] == 0
        assert grp["min_select"] == 0
        assert grp["max_select"] == 0
        assert grp["position"] == 0

    def test_insert_modifier_group_with_all_fields(self, seeded):
        conn, rid, did = seeded
        iid = _insert_item(conn, did)
        gid = insert_modifier_group(
            iid, "Protein",
            required=True, min_select=1, max_select=2, position=3
        )
        grp = get_modifier_group(gid)
        assert grp["required"] == 1
        assert grp["min_select"] == 1
        assert grp["max_select"] == 2
        assert grp["position"] == 3

    def test_update_modifier_group_name(self, seeded):
        conn, rid, did = seeded
        iid = _insert_item(conn, did)
        gid = insert_modifier_group(iid, "Old Name")
        updated = update_modifier_group(gid, name="New Name")
        assert updated is True
        assert get_modifier_group(gid)["name"] == "New Name"

    def test_update_modifier_group_required(self, seeded):
        conn, rid, did = seeded
        iid = _insert_item(conn, did)
        gid = insert_modifier_group(iid, "Sauce")
        update_modifier_group(gid, required=True)
        assert get_modifier_group(gid)["required"] == 1

    def test_update_modifier_group_min_max(self, seeded):
        conn, rid, did = seeded
        iid = _insert_item(conn, did)
        gid = insert_modifier_group(iid, "Sides")
        update_modifier_group(gid, min_select=1, max_select=3)
        grp = get_modifier_group(gid)
        assert grp["min_select"] == 1
        assert grp["max_select"] == 3

    def test_update_modifier_group_not_found_returns_false(self, seeded):
        conn, rid, did = seeded
        result = update_modifier_group(99999, name="Ghost")
        assert result is False

    def test_update_modifier_group_no_fields_returns_false(self, seeded):
        conn, rid, did = seeded
        iid = _insert_item(conn, did)
        gid = insert_modifier_group(iid, "Empty")
        result = update_modifier_group(gid)
        assert result is False

    def test_update_modifier_group_position(self, seeded):
        conn, rid, did = seeded
        iid = _insert_item(conn, did)
        gid = insert_modifier_group(iid, "Side", position=0)
        update_modifier_group(gid, position=5)
        assert get_modifier_group(gid)["position"] == 5


# ---------------------------------------------------------------------------
# Class 3: POST /drafts/<id>/items/<iid>/modifier_groups endpoint
# ---------------------------------------------------------------------------

class TestAddModifierGroupEndpoint:
    """POST /drafts/<id>/items/<iid>/modifier_groups"""

    def _setup(self, db):
        rid, did = _create_draft(db)
        iid = db.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
            "VALUES (?, 'Burger', 999, ?, ?)",
            (did, _NOW, _NOW),
        ).lastrowid
        db.commit()
        return rid, did, iid

    def test_add_group_returns_201_with_group_id(self, client, fresh_db):
        rid, did, iid = self._setup(fresh_db)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/modifier_groups",
            json={"name": "Add-ons"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["ok"] is True
        assert isinstance(data["group_id"], int)

    def test_add_group_persisted_in_db(self, client, fresh_db):
        rid, did, iid = self._setup(fresh_db)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/modifier_groups",
            json={"name": "Protein", "required": True, "min_select": 1, "max_select": 1},
        )
        gid = resp.get_json()["group_id"]
        grp = fresh_db.execute(
            "SELECT * FROM draft_modifier_groups WHERE id=?", (gid,)
        ).fetchone()
        assert grp is not None
        assert grp["name"] == "Protein"
        assert grp["required"] == 1
        assert grp["min_select"] == 1

    def test_add_group_missing_name_returns_400(self, client, fresh_db):
        rid, did, iid = self._setup(fresh_db)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/modifier_groups",
            json={"required": True},
        )
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_add_group_empty_name_returns_400(self, client, fresh_db):
        rid, did, iid = self._setup(fresh_db)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/modifier_groups",
            json={"name": "   "},
        )
        assert resp.status_code == 400

    def test_add_group_bad_min_select_returns_400(self, client, fresh_db):
        rid, did, iid = self._setup(fresh_db)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/modifier_groups",
            json={"name": "Toppings", "min_select": "bad"},
        )
        assert resp.status_code == 400

    def test_add_group_position_stored(self, client, fresh_db):
        rid, did, iid = self._setup(fresh_db)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/modifier_groups",
            json={"name": "Extras", "position": 5},
        )
        gid = resp.get_json()["group_id"]
        grp = fresh_db.execute(
            "SELECT position FROM draft_modifier_groups WHERE id=?", (gid,)
        ).fetchone()
        assert grp["position"] == 5


# ---------------------------------------------------------------------------
# Class 4: PATCH /drafts/<id>/modifier_groups/<gid> endpoint
# ---------------------------------------------------------------------------

class TestUpdateModifierGroupEndpoint:
    """PATCH /drafts/<id>/modifier_groups/<gid>"""

    def _setup(self, db):
        rid, did = _create_draft(db)
        iid = db.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
            "VALUES (?, 'Pizza', 1200, ?, ?)",
            (did, _NOW, _NOW),
        ).lastrowid
        gid = db.execute(
            "INSERT INTO draft_modifier_groups "
            "(item_id, name, required, min_select, max_select, position, created_at, updated_at) "
            "VALUES (?, 'Size', 0, 0, 0, 0, ?, ?)",
            (iid, _NOW, _NOW),
        ).lastrowid
        db.commit()
        return rid, did, iid, gid

    def test_patch_name_returns_200(self, client, fresh_db):
        rid, did, iid, gid = self._setup(fresh_db)
        resp = client.patch(
            f"/drafts/{did}/modifier_groups/{gid}",
            json={"name": "Crust"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_patch_name_persisted(self, client, fresh_db):
        rid, did, iid, gid = self._setup(fresh_db)
        client.patch(f"/drafts/{did}/modifier_groups/{gid}", json={"name": "Crust"})
        row = fresh_db.execute(
            "SELECT name FROM draft_modifier_groups WHERE id=?", (gid,)
        ).fetchone()
        assert row["name"] == "Crust"

    def test_patch_required_to_true(self, client, fresh_db):
        rid, did, iid, gid = self._setup(fresh_db)
        client.patch(f"/drafts/{did}/modifier_groups/{gid}", json={"required": True})
        row = fresh_db.execute(
            "SELECT required FROM draft_modifier_groups WHERE id=?", (gid,)
        ).fetchone()
        assert row["required"] == 1

    def test_patch_min_max_select(self, client, fresh_db):
        rid, did, iid, gid = self._setup(fresh_db)
        client.patch(
            f"/drafts/{did}/modifier_groups/{gid}",
            json={"min_select": 1, "max_select": 3},
        )
        row = fresh_db.execute(
            "SELECT min_select, max_select FROM draft_modifier_groups WHERE id=?", (gid,)
        ).fetchone()
        assert row["min_select"] == 1
        assert row["max_select"] == 3

    def test_patch_not_found_returns_404(self, client, fresh_db):
        rid, did, iid, gid = self._setup(fresh_db)
        resp = client.patch(
            f"/drafts/{did}/modifier_groups/99999",
            json={"name": "Ghost"},
        )
        assert resp.status_code == 404
        assert resp.get_json()["ok"] is False

    def test_patch_empty_name_returns_400(self, client, fresh_db):
        rid, did, iid, gid = self._setup(fresh_db)
        resp = client.patch(
            f"/drafts/{did}/modifier_groups/{gid}",
            json={"name": ""},
        )
        assert resp.status_code == 400

    def test_patch_no_valid_fields_returns_400(self, client, fresh_db):
        rid, did, iid, gid = self._setup(fresh_db)
        resp = client.patch(
            f"/drafts/{did}/modifier_groups/{gid}",
            json={"garbage_field": "value"},
        )
        assert resp.status_code == 400

    def test_patch_bad_integer_field_returns_400(self, client, fresh_db):
        rid, did, iid, gid = self._setup(fresh_db)
        resp = client.patch(
            f"/drafts/{did}/modifier_groups/{gid}",
            json={"min_select": "not_a_number"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Class 5: Full round-trip + export_pos.json endpoint
# ---------------------------------------------------------------------------

class TestRoundTripAndExport:
    """Full round-trip: upsert with modifier groups → load nested → POS export."""

    def test_upsert_and_load_nested(self, seeded):
        conn, rid, did = seeded
        items = [{
            "name": "Tacos",
            "price_cents": 800,
            "category": "Mains",
            "_modifier_groups": [{
                "name": "Protein",
                "required": True,
                "min_select": 1,
                "max_select": 1,
                "position": 0,
                "_modifiers": [
                    {"label": "Beef", "price_cents": 0},
                    {"label": "Chicken", "price_cents": 50},
                ],
            }],
        }]
        upsert_draft_items(did, items)
        loaded = get_draft_items(did, include_modifier_groups=True)
        assert len(loaded) == 1
        item = loaded[0]
        assert len(item["modifier_groups"]) == 1
        grp = item["modifier_groups"][0]
        assert grp["name"] == "Protein"
        assert grp["required"] == 1
        assert len(grp["modifiers"]) == 2

    def test_pos_json_from_nested_load(self, seeded):
        """POS JSON export correctly maps modifier groups from get_draft_items."""
        from portal.app import _build_generic_pos_json
        conn, rid, did = seeded
        items = [{
            "name": "Burrito",
            "price_cents": 1100,
            "category": "Mains",
            "_modifier_groups": [{
                "name": "Size",
                "required": True,
                "min_select": 1,
                "max_select": 1,
                "position": 0,
                "_modifiers": [
                    {"label": "Regular", "price_cents": 0},
                    {"label": "Large", "price_cents": 200},
                ],
            }],
        }]
        upsert_draft_items(did, items)
        loaded = get_draft_items(did, include_modifier_groups=True)
        out = _build_generic_pos_json(loaded, {"id": did, "title": "Menu"})
        item = out["menu"]["categories"][0]["items"][0]
        assert len(item["modifier_groups"]) == 1
        assert item["modifier_groups"][0]["name"] == "Size"
        assert item["modifier_groups"][0]["required"] is True
        assert len(item["modifier_groups"][0]["modifiers"]) == 2
        # flat modifiers also present
        assert len(item["modifiers"]) == 2

    def test_export_pos_json_endpoint(self, client, fresh_db):
        """GET /drafts/<id>/export_pos.json returns valid JSON with modifier_groups."""
        rid, did = _create_draft(fresh_db)
        # Insert item with a modifier group
        iid = fresh_db.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, category, created_at, updated_at) "
            "VALUES (?, 'Latte', 450, 'Coffee', ?, ?)",
            (did, _NOW, _NOW),
        ).lastrowid
        gid = fresh_db.execute(
            "INSERT INTO draft_modifier_groups "
            "(item_id, name, required, min_select, max_select, position, created_at, updated_at) "
            "VALUES (?, 'Size', 1, 1, 1, 0, ?, ?)",
            (iid, _NOW, _NOW),
        ).lastrowid
        fresh_db.execute(
            "INSERT INTO draft_item_variants "
            "(item_id, label, price_cents, kind, position, modifier_group_id, created_at, updated_at) "
            "VALUES (?, 'Large', 575, 'size', 0, ?, ?, ?)",
            (iid, gid, _NOW, _NOW),
        )
        fresh_db.commit()

        resp = client.get(f"/drafts/{did}/export_pos.json")
        assert resp.status_code == 200
        payload = json.loads(resp.data)
        assert payload["metadata"]["version"] == "1.1"
        cats = payload["menu"]["categories"]
        assert len(cats) == 1
        item = cats[0]["items"][0]
        assert item["name"] == "Latte"
        assert len(item["modifier_groups"]) == 1
        assert item["modifier_groups"][0]["name"] == "Size"
        assert item["modifier_groups"][0]["required"] is True
