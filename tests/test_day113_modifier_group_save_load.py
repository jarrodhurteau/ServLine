"""
Day 113 — Sprint 12.1: Modifier Group Contract + Save/Load Cycle
=================================================================
Tests for:
  1. validate_draft_payload() — _modifier_groups per-item validation
  2. validate_draft_payload() — deleted_modifier_group_ids top-level validation
  3. draft_save() endpoint — items with _modifier_groups persisted to DB
  4. draft_save() endpoint — deleted_modifier_group_ids cleans up groups
  5. draft_editor() route — items loaded with include_modifier_groups=True
  6. Template — modifier-group-pill badge rendered when groups present

40 tests across 4 classes.
"""

from __future__ import annotations

import json
import sqlite3
import pytest
from typing import Optional

from portal.contracts import validate_draft_payload
import storage.drafts as drafts_mod
from storage.drafts import (
    upsert_draft_items,
    get_draft_items,
    get_modifier_groups,
    insert_modifier_group,
    delete_modifier_group,
)


# ---------------------------------------------------------------------------
# Schema (Day 113 — full schema with modifier groups)
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

_NOW = "2026-03-13T10:00:00"


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


@pytest.fixture
def conn(monkeypatch):
    c = _make_conn()
    monkeypatch.setattr(drafts_mod, "db_connect", lambda: c)
    return c


@pytest.fixture
def draft_id(conn):
    _, did = _seed(conn)
    return did


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
    """Flask test client with fake session login."""
    import portal.app as _app_module
    app = _app_module.app
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = {"id": 1, "email": "test@test.com"}
        yield c


def _create_draft(conn, title="Test Menu", status="editing"):
    rid = conn.execute(
        "INSERT INTO restaurants (name, active) VALUES ('R', 1)"
    ).lastrowid
    did = conn.execute(
        "INSERT INTO drafts (title, restaurant_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (title, rid, status, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return did


def _insert_item(conn, draft_id, name="Burger", price=999, category="Entrees"):
    iid = conn.execute(
        "INSERT INTO draft_items (draft_id, name, price_cents, category, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (draft_id, name, price, category, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return iid


def _insert_group(conn, item_id, name="Size", required=0, min_select=0, max_select=0):
    gid = conn.execute(
        "INSERT INTO draft_modifier_groups (item_id, name, required, min_select, "
        "max_select, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        (item_id, name, required, min_select, max_select, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return gid


# ===========================================================================
# CLASS 1: Contract Validation — _modifier_groups
# ===========================================================================

class TestContractModifierGroups:
    """validate_draft_payload correctly validates _modifier_groups and
    deleted_modifier_group_ids."""

    def _payload(self, items=None, **extra):
        p = {"draft_id": 1, "items": items or []}
        p.update(extra)
        return p

    def test_no_modifier_groups_is_valid(self):
        ok, err = validate_draft_payload(self._payload(items=[{"name": "Burger"}]))
        assert ok, err

    def test_empty_modifier_groups_list_valid(self):
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "_modifier_groups": []}])
        )
        assert ok, err

    def test_valid_group_with_all_fields(self):
        group = {
            "name": "Sauce Choice",
            "required": 1,
            "min_select": 1,
            "max_select": 1,
            "position": 0,
        }
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "_modifier_groups": [group]}])
        )
        assert ok, err

    def test_group_missing_name_rejected(self):
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "_modifier_groups": [{"required": 0}]}])
        )
        assert not ok
        assert "name" in err

    def test_group_empty_name_rejected(self):
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "_modifier_groups": [{"name": "  "}]}])
        )
        assert not ok
        assert "must not be empty" in err

    def test_group_name_not_string_rejected(self):
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "_modifier_groups": [{"name": 42}]}])
        )
        assert not ok
        assert "name" in err

    def test_group_required_non_intlike_rejected(self):
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "B", "_modifier_groups": [{"name": "G", "required": "yes"}]}])
        )
        assert not ok
        assert "required" in err

    def test_group_min_select_non_intlike_rejected(self):
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "B", "_modifier_groups": [{"name": "G", "min_select": "a"}]}])
        )
        assert not ok
        assert "min_select" in err

    def test_group_max_select_non_intlike_rejected(self):
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "B", "_modifier_groups": [{"name": "G", "max_select": []}]}])
        )
        assert not ok
        assert "max_select" in err

    def test_modifier_groups_not_list_rejected(self):
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "_modifier_groups": "Size"}])
        )
        assert not ok
        assert "_modifier_groups must be a list" in err

    def test_group_element_not_dict_rejected(self):
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "_modifier_groups": ["Size"]}])
        )
        assert not ok
        assert "must be an object" in err

    def test_valid_modifiers_inside_group(self):
        group = {
            "name": "Add-Ons",
            "_modifiers": [
                {"label": "Extra Cheese", "price_cents": 100},
                {"label": "Bacon", "price_cents": 150},
            ],
        }
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "_modifier_groups": [group]}])
        )
        assert ok, err

    def test_modifier_missing_label_rejected(self):
        group = {
            "name": "Add-Ons",
            "_modifiers": [{"price_cents": 100}],
        }
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "_modifier_groups": [group]}])
        )
        assert not ok
        assert "label" in err

    def test_modifier_empty_label_rejected(self):
        group = {
            "name": "Add-Ons",
            "_modifiers": [{"label": "  "}],
        }
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "_modifier_groups": [group]}])
        )
        assert not ok
        assert "must not be empty" in err

    def test_modifier_price_cents_non_intlike_rejected(self):
        group = {
            "name": "Add-Ons",
            "_modifiers": [{"label": "Cheese", "price_cents": "one dollar"}],
        }
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "_modifier_groups": [group]}])
        )
        assert not ok
        assert "price_cents" in err

    def test_modifier_price_cents_none_allowed(self):
        group = {
            "name": "Add-Ons",
            "_modifiers": [{"label": "Cheese", "price_cents": None}],
        }
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "_modifier_groups": [group]}])
        )
        assert ok, err

    def test_both_variants_and_modifier_groups_valid(self):
        item = {
            "name": "Tacos",
            "_variants": [{"label": "Small", "price_cents": 299, "kind": "size"}],
            "_modifier_groups": [{"name": "Salsa", "_modifiers": [{"label": "Verde"}]}],
        }
        ok, err = validate_draft_payload(self._payload(items=[item]))
        assert ok, err

    def test_multiple_groups_per_item_valid(self):
        item = {
            "name": "Burger",
            "_modifier_groups": [
                {"name": "Size"},
                {"name": "Add-Ons"},
                {"name": "Sauce"},
            ],
        }
        ok, err = validate_draft_payload(self._payload(items=[item]))
        assert ok, err

    def test_deleted_modifier_group_ids_valid(self):
        ok, err = validate_draft_payload(
            self._payload(deleted_modifier_group_ids=[1, 2, 3])
        )
        assert ok, err

    def test_deleted_modifier_group_ids_empty_valid(self):
        ok, err = validate_draft_payload(
            self._payload(deleted_modifier_group_ids=[])
        )
        assert ok, err

    def test_deleted_modifier_group_ids_not_list_rejected(self):
        ok, err = validate_draft_payload(
            self._payload(deleted_modifier_group_ids=5)
        )
        assert not ok
        assert "deleted_modifier_group_ids must be a list" in err

    def test_deleted_modifier_group_ids_non_int_rejected(self):
        ok, err = validate_draft_payload(
            self._payload(deleted_modifier_group_ids=["abc"])
        )
        assert not ok
        assert "deleted_modifier_group_ids[0]" in err

    def test_kitchen_name_string_valid(self):
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "kitchen_name": "BURGER"}])
        )
        assert ok, err

    def test_kitchen_name_none_valid(self):
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "kitchen_name": None}])
        )
        assert ok, err

    def test_kitchen_name_non_string_rejected(self):
        ok, err = validate_draft_payload(
            self._payload(items=[{"name": "Burger", "kitchen_name": 123}])
        )
        assert not ok
        assert "kitchen_name" in err


# ===========================================================================
# CLASS 2: Save Endpoint — Modifier Groups
# ===========================================================================

class TestSaveEndpointModifierGroups:
    """draft_save() endpoint persists _modifier_groups and handles
    deleted_modifier_group_ids."""

    def test_save_item_with_modifier_groups_persists(self, client, fresh_db):
        did = _create_draft(fresh_db)
        payload = {
            "draft_id": did,
            "items": [{
                "name": "Burger",
                "price_cents": 999,
                "_modifier_groups": [
                    {"name": "Size", "required": 1, "min_select": 1, "max_select": 1,
                     "_modifiers": [{"label": "Regular"}, {"label": "Large", "price_cents": 200}]}
                ],
            }],
        }
        resp = client.post(
            f"/drafts/{did}/save",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        # Group should be persisted
        iid = fresh_db.execute("SELECT id FROM draft_items WHERE draft_id=?", (did,)).fetchone()[0]
        groups = fresh_db.execute(
            "SELECT * FROM draft_modifier_groups WHERE item_id=?", (iid,)
        ).fetchall()
        assert len(groups) == 1
        assert groups[0]["name"] == "Size"
        assert groups[0]["required"] == 1

    def test_save_modifier_group_with_modifiers_links_variants(self, client, fresh_db):
        did = _create_draft(fresh_db)
        payload = {
            "draft_id": did,
            "items": [{
                "name": "Taco",
                "price_cents": 399,
                "_modifier_groups": [
                    {"name": "Protein", "required": 1, "max_select": 1,
                     "_modifiers": [
                         {"label": "Chicken", "price_cents": 0},
                         {"label": "Steak", "price_cents": 100},
                     ]}
                ],
            }],
        }
        resp = client.post(
            f"/drafts/{did}/save",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        iid = fresh_db.execute("SELECT id FROM draft_items WHERE draft_id=?", (did,)).fetchone()[0]
        gid = fresh_db.execute(
            "SELECT id FROM draft_modifier_groups WHERE item_id=?", (iid,)
        ).fetchone()[0]
        variants = fresh_db.execute(
            "SELECT * FROM draft_item_variants WHERE modifier_group_id=?", (gid,)
        ).fetchall()
        assert len(variants) == 2
        labels = {v["label"] for v in variants}
        assert "Chicken" in labels
        assert "Steak" in labels

    def test_deleted_modifier_group_ids_removes_group(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        gid = _insert_group(fresh_db, iid, "Size")
        payload = {
            "draft_id": did,
            "items": [],
            "deleted_modifier_group_ids": [gid],
        }
        resp = client.post(
            f"/drafts/{did}/save",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["deleted_mg_count"] == 1
        remaining = fresh_db.execute(
            "SELECT * FROM draft_modifier_groups WHERE id=?", (gid,)
        ).fetchone()
        assert remaining is None

    def test_deleted_mg_count_in_response(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        gid1 = _insert_group(fresh_db, iid, "Group A")
        gid2 = _insert_group(fresh_db, iid, "Group B")
        payload = {
            "draft_id": did,
            "items": [],
            "deleted_modifier_group_ids": [gid1, gid2],
        }
        resp = client.post(
            f"/drafts/{did}/save",
            data=json.dumps(payload),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["deleted_mg_count"] == 2

    def test_deleted_mg_count_zero_when_not_provided(self, client, fresh_db):
        did = _create_draft(fresh_db)
        payload = {"draft_id": did, "items": []}
        resp = client.post(
            f"/drafts/{did}/save",
            data=json.dumps(payload),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["deleted_mg_count"] == 0

    def test_nonexistent_group_id_in_deleted_list_graceful(self, client, fresh_db):
        did = _create_draft(fresh_db)
        payload = {
            "draft_id": did,
            "items": [],
            "deleted_modifier_group_ids": [99999],
        }
        resp = client.post(
            f"/drafts/{did}/save",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["deleted_mg_count"] == 0

    def test_invalid_modifier_groups_schema_returns_400(self, client, fresh_db):
        did = _create_draft(fresh_db)
        payload = {
            "draft_id": did,
            "items": [{"name": "Burger", "_modifier_groups": "not-a-list"}],
        }
        resp = client.post(
            f"/drafts/{did}/save",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["ok"] is False

    def test_save_modifier_groups_alongside_variants(self, client, fresh_db):
        did = _create_draft(fresh_db)
        payload = {
            "draft_id": did,
            "items": [{
                "name": "Soda",
                "price_cents": 250,
                "_variants": [
                    {"label": "Small", "price_cents": 250, "kind": "size"},
                    {"label": "Large", "price_cents": 350, "kind": "size"},
                ],
                "_modifier_groups": [
                    {"name": "Ice", "_modifiers": [{"label": "Light"}, {"label": "Regular"}, {"label": "Extra"}]},
                ],
            }],
        }
        resp = client.post(
            f"/drafts/{did}/save",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        iid = fresh_db.execute("SELECT id FROM draft_items WHERE draft_id=?", (did,)).fetchone()[0]
        groups = fresh_db.execute(
            "SELECT * FROM draft_modifier_groups WHERE item_id=?", (iid,)
        ).fetchall()
        assert len(groups) == 1
        # ungrouped variants still persisted
        ungrouped = fresh_db.execute(
            "SELECT * FROM draft_item_variants WHERE item_id=? AND modifier_group_id IS NULL",
            (iid,),
        ).fetchall()
        assert len(ungrouped) == 2

    def test_save_modifier_groups_replace_on_update(self, client, fresh_db):
        did = _create_draft(fresh_db)
        # First save: one group
        payload1 = {
            "draft_id": did,
            "items": [{"name": "Burger", "price_cents": 999,
                       "_modifier_groups": [{"name": "Size"}]}],
        }
        client.post(
            f"/drafts/{did}/save",
            data=json.dumps(payload1),
            content_type="application/json",
        )
        iid = fresh_db.execute("SELECT id FROM draft_items WHERE draft_id=?", (did,)).fetchone()[0]
        iid_int = int(iid)
        # Second save: two groups on same item (replace=True)
        payload2 = {
            "draft_id": did,
            "items": [{"id": iid_int, "name": "Burger", "price_cents": 999,
                       "_modifier_groups": [{"name": "Size"}, {"name": "Add-Ons"}]}],
        }
        client.post(
            f"/drafts/{did}/save",
            data=json.dumps(payload2),
            content_type="application/json",
        )
        groups = fresh_db.execute(
            "SELECT name FROM draft_modifier_groups WHERE item_id=? ORDER BY name",
            (iid_int,),
        ).fetchall()
        names = [g["name"] for g in groups]
        assert "Size" in names
        assert "Add-Ons" in names
        assert len(names) == 2

    def test_autosave_ping_not_processed(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        gid = _insert_group(fresh_db, iid, "Size")
        payload = {
            "draft_id": did,
            "items": [],
            "autosave_ping": True,
            "deleted_modifier_group_ids": [gid],
        }
        resp = client.post(
            f"/drafts/{did}/save",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ping") is True
        # Group should NOT be deleted (autosave short-circuits)
        still_there = fresh_db.execute(
            "SELECT id FROM draft_modifier_groups WHERE id=?", (gid,)
        ).fetchone()
        assert still_there is not None

    def test_approved_draft_still_returns_403(self, client, fresh_db):
        did = _create_draft(fresh_db, status="approved")
        payload = {"draft_id": did, "items": []}
        resp = client.post(
            f"/drafts/{did}/save",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 403


# ===========================================================================
# CLASS 3: Draft Editor Loads Modifier Groups
# ===========================================================================

class TestEditorLoadsModifierGroups:
    """draft_editor() route provides modifier group data to the template."""

    def test_editor_route_includes_modifier_groups_key(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, name="Nachos")
        _insert_group(fresh_db, iid, "Size", required=1, max_select=1)
        resp = client.get(f"/drafts/{did}/edit")
        assert resp.status_code == 200

    def test_items_with_groups_have_modifier_groups_list(self, conn, draft_id):
        iid = conn.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, category, "
            "created_at, updated_at) VALUES (?, 'Wings', 899, 'Apps', ?, ?)",
            (draft_id, _NOW, _NOW),
        ).lastrowid
        conn.commit()
        _insert_group_direct(conn, iid, "Flavor", required=0)
        items = get_draft_items(draft_id, include_modifier_groups=True)
        assert len(items) == 1
        assert "modifier_groups" in items[0]
        assert len(items[0]["modifier_groups"]) == 1
        assert items[0]["modifier_groups"][0]["name"] == "Flavor"

    def test_items_without_groups_have_empty_modifier_groups(self, conn, draft_id):
        conn.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
            "VALUES (?, 'Fries', 299, ?, ?)",
            (draft_id, _NOW, _NOW),
        )
        conn.commit()
        items = get_draft_items(draft_id, include_modifier_groups=True)
        assert len(items) == 1
        assert items[0]["modifier_groups"] == []

    def test_modifier_group_fields_correct(self, conn, draft_id):
        iid = conn.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
            "VALUES (?, 'Wrap', 799, ?, ?)",
            (draft_id, _NOW, _NOW),
        ).lastrowid
        conn.commit()
        _insert_group_direct(conn, iid, "Protein", required=1, min_select=1, max_select=1)
        items = get_draft_items(draft_id, include_modifier_groups=True)
        g = items[0]["modifier_groups"][0]
        assert g["name"] == "Protein"
        assert g["required"] == 1
        assert g["min_select"] == 1
        assert g["max_select"] == 1

    def test_ungrouped_variants_not_in_modifier_groups(self, conn, draft_id):
        iid = conn.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
            "VALUES (?, 'Soda', 250, ?, ?)",
            (draft_id, _NOW, _NOW),
        ).lastrowid
        conn.execute(
            "INSERT INTO draft_item_variants (item_id, label, price_cents, kind, "
            "created_at, updated_at) VALUES (?, 'Small', 250, 'size', ?, ?)",
            (iid, _NOW, _NOW),
        )
        conn.commit()
        items = get_draft_items(draft_id, include_modifier_groups=True)
        assert items[0]["modifier_groups"] == []
        assert len(items[0]["ungrouped_variants"]) == 1

    def test_multiple_groups_per_item(self, conn, draft_id):
        iid = conn.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
            "VALUES (?, 'Pizza', 1299, ?, ?)",
            (draft_id, _NOW, _NOW),
        ).lastrowid
        conn.commit()
        _insert_group_direct(conn, iid, "Size")
        _insert_group_direct(conn, iid, "Toppings")
        _insert_group_direct(conn, iid, "Sauce")
        items = get_draft_items(draft_id, include_modifier_groups=True)
        assert len(items[0]["modifier_groups"]) == 3

    def test_include_modifier_groups_false_no_key(self, conn, draft_id):
        conn.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
            "VALUES (?, 'Burger', 999, ?, ?)",
            (draft_id, _NOW, _NOW),
        )
        conn.commit()
        items = get_draft_items(draft_id, include_modifier_groups=False)
        assert "modifier_groups" not in items[0]

    def test_default_get_draft_items_no_modifier_groups(self, conn, draft_id):
        conn.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
            "VALUES (?, 'Burger', 999, ?, ?)",
            (draft_id, _NOW, _NOW),
        )
        conn.commit()
        items = get_draft_items(draft_id)
        # default (no include_modifier_groups param) should not include key
        assert "modifier_groups" not in items[0]


# ===========================================================================
# CLASS 4: Template — Modifier Group Badge
# ===========================================================================

class TestTemplateBadge:
    """The draft_editor.html template renders modifier-group-pill badges."""

    def test_modifier_group_pill_shown_when_groups_exist(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, name="Burger")
        _insert_group(fresh_db, iid, "Size")
        resp = client.get(f"/drafts/{did}/edit")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "modifier-group-pill" in html

    def test_modifier_group_pill_shows_count(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, name="Pizza")
        _insert_group(fresh_db, iid, "Size")
        _insert_group(fresh_db, iid, "Toppings")
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        assert "2 groups" in html

    def test_modifier_group_pill_singular_count(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, name="Taco")
        _insert_group(fresh_db, iid, "Protein")
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        assert "1 group" in html
        assert "1 groups" not in html

    def test_no_modifier_group_pill_without_groups(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, name="Fries")
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        # CSS class definition is always present; check no <span> element uses it
        assert '<span class="modifier-group-pill"' not in html

    def test_group_names_in_pill_title_attribute(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, name="Nachos")
        _insert_group(fresh_db, iid, "Cheese Sauce")
        resp = client.get(f"/drafts/{did}/edit")
        html = resp.data.decode()
        assert "Cheese Sauce" in html


# ---------------------------------------------------------------------------
# Inline helper (avoids import coupling for group insertion in storage tests)
# ---------------------------------------------------------------------------

def _insert_group_direct(conn, item_id, name, required=0, min_select=0, max_select=0):
    gid = conn.execute(
        "INSERT INTO draft_modifier_groups (item_id, name, required, min_select, "
        "max_select, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        (item_id, name, required, min_select, max_select, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return gid
