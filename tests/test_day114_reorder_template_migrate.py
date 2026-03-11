"""
Day 114 — Sprint 12.1: Modifier Group Reorder + Template/Migration Endpoints
=============================================================================
Tests for:
  1. reorder_modifier_groups()  — bulk position update for groups
  2. reorder_modifiers()        — bulk position update for modifiers in a group
  3. migrate_draft_modifier_groups() — batch migrate all items in a draft
  4. POST /drafts/<id>/items/<iid>/modifier_groups/reorder  — portal endpoint
  5. POST /drafts/<id>/modifier_groups/<gid>/modifiers/reorder — portal endpoint
  6. GET  /restaurants/<rid>/modifier_templates              — portal endpoint
  7. POST /drafts/<id>/items/<iid>/apply_template            — portal endpoint
  8. POST /drafts/<id>/migrate_modifier_groups               — portal endpoint

~40 tests across 5 classes.
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional

import storage.drafts as drafts_mod
from storage.drafts import (
    insert_modifier_group,
    get_modifier_groups,
    insert_modifier_template,
    seed_modifier_template_presets,
    list_modifier_templates,
    reorder_modifier_groups,
    reorder_modifiers,
    migrate_draft_modifier_groups,
)


# ---------------------------------------------------------------------------
# Schema (full Day 113 schema + templates)
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


def _insert_item(conn, draft_id, name="Burger"):
    iid = conn.execute(
        "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
        "VALUES (?, ?, 999, ?, ?)",
        (draft_id, name, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return iid


def _insert_variant(conn, item_id, label="Small", kind="size", position=0, group_id=None):
    vid = conn.execute(
        "INSERT INTO draft_item_variants "
        "(item_id, label, price_cents, kind, position, modifier_group_id, created_at, updated_at) "
        "VALUES (?, ?, 0, ?, ?, ?, ?, ?)",
        (item_id, label, kind, position, group_id, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return vid


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


def _db_item(conn, draft_id, name="Burger"):
    iid = conn.execute(
        "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
        "VALUES (?, ?, 999, ?, ?)",
        (draft_id, name, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return iid


def _db_group(conn, item_id, name="Size", pos=0):
    gid = conn.execute(
        "INSERT INTO draft_modifier_groups "
        "(item_id, name, required, min_select, max_select, position, created_at, updated_at) "
        "VALUES (?, ?, 0, 0, 0, ?, ?, ?)",
        (item_id, name, pos, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return gid


def _db_variant(conn, item_id, label="Small", group_id=None, pos=0):
    vid = conn.execute(
        "INSERT INTO draft_item_variants "
        "(item_id, label, price_cents, kind, position, modifier_group_id, created_at, updated_at) "
        "VALUES (?, ?, 0, 'size', ?, ?, ?, ?)",
        (item_id, label, pos, group_id, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return vid


def _group_position(conn, group_id):
    row = conn.execute(
        "SELECT position FROM draft_modifier_groups WHERE id=?", (group_id,)
    ).fetchone()
    return row["position"] if row else None


def _variant_position(conn, variant_id):
    row = conn.execute(
        "SELECT position FROM draft_item_variants WHERE id=?", (variant_id,)
    ).fetchone()
    return row["position"] if row else None


# ===========================================================================
# Class 1 — reorder_modifier_groups() storage function
# ===========================================================================

class TestReorderModifierGroups:
    def test_basic_reorder(self, seeded):
        conn, _, did = seeded
        iid = _insert_item(conn, did)
        g1 = insert_modifier_group(iid, "Size", position=0)
        g2 = insert_modifier_group(iid, "Sauce", position=1)
        g3 = insert_modifier_group(iid, "Add-ons", position=2)

        updated = reorder_modifier_groups(iid, [g3, g1, g2])

        assert updated == 3
        assert _group_position(conn, g3) == 0
        assert _group_position(conn, g1) == 1
        assert _group_position(conn, g2) == 2

    def test_empty_list_returns_zero(self, seeded):
        conn, _, did = seeded
        iid = _insert_item(conn, did)
        insert_modifier_group(iid, "Size", position=0)
        assert reorder_modifier_groups(iid, []) == 0

    def test_unknown_id_skipped(self, seeded):
        conn, _, did = seeded
        iid = _insert_item(conn, did)
        g1 = insert_modifier_group(iid, "Size", position=0)

        updated = reorder_modifier_groups(iid, [9999, g1])
        # only g1 belongs to iid; 9999 is skipped
        assert updated == 1
        assert _group_position(conn, g1) == 1  # at index 1

    def test_group_belonging_to_other_item_skipped(self, seeded):
        conn, _, did = seeded
        iid1 = _insert_item(conn, did, "Burger")
        iid2 = _insert_item(conn, did, "Pizza")
        g1 = insert_modifier_group(iid1, "Size", position=5)
        g2 = insert_modifier_group(iid2, "Crust", position=5)

        # reorder for iid1 — g2 (belongs to iid2) should be ignored
        updated = reorder_modifier_groups(iid1, [g2, g1])
        assert updated == 1
        assert _group_position(conn, g1) == 1   # moved
        assert _group_position(conn, g2) == 5   # unchanged

    def test_single_group_reorder(self, seeded):
        conn, _, did = seeded
        iid = _insert_item(conn, did)
        g1 = insert_modifier_group(iid, "Size", position=99)

        updated = reorder_modifier_groups(iid, [g1])
        assert updated == 1
        assert _group_position(conn, g1) == 0

    def test_returns_int(self, seeded):
        conn, _, did = seeded
        iid = _insert_item(conn, did)
        g1 = insert_modifier_group(iid, "Size")
        result = reorder_modifier_groups(iid, [g1])
        assert isinstance(result, int)


# ===========================================================================
# Class 2 — reorder_modifiers() storage function
# ===========================================================================

class TestReorderModifiers:
    def test_basic_reorder(self, seeded):
        conn, _, did = seeded
        iid = _insert_item(conn, did)
        gid = insert_modifier_group(iid, "Size")
        v1 = _insert_variant(conn, iid, "Small", group_id=gid, position=0)
        v2 = _insert_variant(conn, iid, "Medium", group_id=gid, position=1)
        v3 = _insert_variant(conn, iid, "Large", group_id=gid, position=2)

        updated = reorder_modifiers(gid, [v3, v2, v1])

        assert updated == 3
        assert _variant_position(conn, v3) == 0
        assert _variant_position(conn, v2) == 1
        assert _variant_position(conn, v1) == 2

    def test_empty_list_returns_zero(self, seeded):
        conn, _, did = seeded
        iid = _insert_item(conn, did)
        gid = insert_modifier_group(iid, "Size")
        assert reorder_modifiers(gid, []) == 0

    def test_modifier_belonging_to_other_group_skipped(self, seeded):
        conn, _, did = seeded
        iid = _insert_item(conn, did)
        g1 = insert_modifier_group(iid, "Size")
        g2 = insert_modifier_group(iid, "Sauce")
        v1 = _insert_variant(conn, iid, "Small", group_id=g1, position=5)
        v2 = _insert_variant(conn, iid, "Ranch", group_id=g2, position=5)

        updated = reorder_modifiers(g1, [v2, v1])
        assert updated == 1
        assert _variant_position(conn, v1) == 1   # moved
        assert _variant_position(conn, v2) == 5   # unchanged

    def test_ungrouped_modifier_not_moved(self, seeded):
        conn, _, did = seeded
        iid = _insert_item(conn, did)
        gid = insert_modifier_group(iid, "Size")
        v_grouped = _insert_variant(conn, iid, "Small", group_id=gid, position=5)
        v_ungrouped = _insert_variant(conn, iid, "Extra", group_id=None, position=5)

        updated = reorder_modifiers(gid, [v_ungrouped, v_grouped])
        assert updated == 1
        assert _variant_position(conn, v_grouped) == 1
        assert _variant_position(conn, v_ungrouped) == 5  # unchanged

    def test_single_modifier_reorder(self, seeded):
        conn, _, did = seeded
        iid = _insert_item(conn, did)
        gid = insert_modifier_group(iid, "Size")
        v1 = _insert_variant(conn, iid, "Small", group_id=gid, position=99)

        assert reorder_modifiers(gid, [v1]) == 1
        assert _variant_position(conn, v1) == 0


# ===========================================================================
# Class 3 — migrate_draft_modifier_groups() storage function
# ===========================================================================

class TestMigrateDraftModifierGroups:
    def test_migrates_items_with_variants(self, seeded):
        conn, _, did = seeded
        iid1 = _insert_item(conn, did, "Burger")
        iid2 = _insert_item(conn, did, "Pizza")
        _insert_variant(conn, iid1, "Small", kind="size")
        _insert_variant(conn, iid1, "Large", kind="size")
        _insert_variant(conn, iid2, "Thin", kind="style")

        result = migrate_draft_modifier_groups(did)

        assert result["item_count"] == 2
        assert result["migrated_count"] == 2
        # Groups created
        assert len(get_modifier_groups(iid1)) == 1  # one size group
        assert len(get_modifier_groups(iid2)) == 1  # one style group

    def test_skips_already_migrated_items(self, seeded):
        conn, _, did = seeded
        iid = _insert_item(conn, did)
        _insert_variant(conn, iid, "Small", kind="size")
        # Pre-migrate item 1
        migrate_draft_modifier_groups(did)

        # Add a second item with variants
        iid2 = _insert_item(conn, did, "Pizza")
        _insert_variant(conn, iid2, "Thin", kind="style")

        result = migrate_draft_modifier_groups(did)

        # Only iid2 should be migrated this time
        assert result["item_count"] == 2
        assert result["migrated_count"] == 1

    def test_empty_draft_returns_zero(self, seeded):
        conn, _, did = seeded
        result = migrate_draft_modifier_groups(did)
        assert result["item_count"] == 0
        assert result["migrated_count"] == 0

    def test_items_without_variants_not_counted_as_migrated(self, seeded):
        conn, _, did = seeded
        _insert_item(conn, did, "Plain Item")  # no variants

        result = migrate_draft_modifier_groups(did)

        assert result["item_count"] == 1
        assert result["migrated_count"] == 0

    def test_mixed_draft(self, seeded):
        conn, _, did = seeded
        iid_with = _insert_item(conn, did, "Burger")
        _iid_without = _insert_item(conn, did, "Salad")
        _insert_variant(conn, iid_with, "Small", kind="size")

        result = migrate_draft_modifier_groups(did)

        assert result["item_count"] == 2
        assert result["migrated_count"] == 1

    def test_returns_dict_with_expected_keys(self, seeded):
        conn, _, did = seeded
        result = migrate_draft_modifier_groups(did)
        assert "item_count" in result
        assert "migrated_count" in result


# ===========================================================================
# Class 4 — Portal reorder endpoints
# ===========================================================================

class TestReorderEndpoints:
    def test_modifier_groups_reorder_happy(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        g1 = _db_group(fresh_db, iid, "Size", pos=0)
        g2 = _db_group(fresh_db, iid, "Sauce", pos=1)
        g3 = _db_group(fresh_db, iid, "Add-ons", pos=2)

        resp = client.post(
            f"/drafts/{did}/items/{iid}/modifier_groups/reorder",
            json={"ordered_ids": [g3, g1, g2]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["updated"] == 3

        assert _group_position(fresh_db, g3) == 0
        assert _group_position(fresh_db, g1) == 1
        assert _group_position(fresh_db, g2) == 2

    def test_modifier_groups_reorder_empty_list(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/modifier_groups/reorder",
            json={"ordered_ids": []},
        )
        assert resp.status_code == 200
        assert resp.get_json()["updated"] == 0

    def test_modifier_groups_reorder_bad_payload(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/modifier_groups/reorder",
            json={"ordered_ids": "not-a-list"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_modifiers_reorder_happy(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        gid = _db_group(fresh_db, iid, "Size")
        v1 = _db_variant(fresh_db, iid, "Small", group_id=gid, pos=0)
        v2 = _db_variant(fresh_db, iid, "Large", group_id=gid, pos=1)

        resp = client.post(
            f"/drafts/{did}/modifier_groups/{gid}/modifiers/reorder",
            json={"ordered_ids": [v2, v1]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["updated"] == 2

        assert _variant_position(fresh_db, v2) == 0
        assert _variant_position(fresh_db, v1) == 1

    def test_modifiers_reorder_empty_list(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        gid = _db_group(fresh_db, iid, "Size")
        resp = client.post(
            f"/drafts/{did}/modifier_groups/{gid}/modifiers/reorder",
            json={"ordered_ids": []},
        )
        assert resp.status_code == 200
        assert resp.get_json()["updated"] == 0

    def test_modifiers_reorder_bad_payload(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        gid = _db_group(fresh_db, iid, "Size")
        resp = client.post(
            f"/drafts/{did}/modifier_groups/{gid}/modifiers/reorder",
            json={"ordered_ids": {"bad": "dict"}},
        )
        assert resp.status_code == 400

    def test_reorder_requires_login(self, fresh_db):
        import portal.app as _app_module
        app = _app_module.app
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        with app.test_client() as anon:
            resp = anon.post(
                f"/drafts/{did}/items/{iid}/modifier_groups/reorder",
                json={"ordered_ids": []},
            )
        assert resp.status_code in (302, 401, 403)


# ===========================================================================
# Class 5 — Portal template and migration endpoints
# ===========================================================================

class TestTemplateAndMigrateEndpoints:
    def test_list_templates_empty(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        resp = client.get(f"/restaurants/{rid}/modifier_templates")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["templates"] == []
        assert data["count"] == 0

    def test_list_templates_returns_restaurant_and_global(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        seed_modifier_template_presets(rid)

        resp = client.get(f"/restaurants/{rid}/modifier_templates")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["count"] == 4  # 4 built-in presets
        names = [t["name"] for t in data["templates"]]
        assert "Size (S/M/L)" in names
        assert "Temperature" in names

    def test_list_templates_global_also_returned(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        # Insert a global template (restaurant_id=None)
        insert_modifier_template(
            None, "Global Option", [{"label": "Yes", "price_cents": 0}],
            required=False, min_select=0, max_select=1, position=0,
        )
        resp = client.get(f"/restaurants/{rid}/modifier_templates")
        data = resp.get_json()
        names = [t["name"] for t in data["templates"]]
        assert "Global Option" in names

    def test_apply_template_happy(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        seed_modifier_template_presets(rid)
        templates = list_modifier_templates(rid)
        size_tmpl = next(t for t in templates if t["name"] == "Size (S/M/L)")

        resp = client.post(
            f"/drafts/{did}/items/{iid}/apply_template",
            json={"template_id": size_tmpl["id"]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "group_id" in data
        assert len(data["modifier_ids"]) == 3  # S/M/L

    def test_apply_template_creates_group_in_db(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        seed_modifier_template_presets(rid)
        templates = list_modifier_templates(rid)
        tmpl = templates[0]

        client.post(
            f"/drafts/{did}/items/{iid}/apply_template",
            json={"template_id": tmpl["id"]},
        )
        groups = get_modifier_groups(iid)
        assert len(groups) == 1
        assert groups[0]["name"] == tmpl["name"]

    def test_apply_template_twice_creates_two_groups(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        seed_modifier_template_presets(rid)
        templates = list_modifier_templates(rid)
        tmpl = templates[0]

        client.post(
            f"/drafts/{did}/items/{iid}/apply_template",
            json={"template_id": tmpl["id"]},
        )
        client.post(
            f"/drafts/{did}/items/{iid}/apply_template",
            json={"template_id": tmpl["id"]},
        )
        groups = get_modifier_groups(iid)
        assert len(groups) == 2

    def test_apply_template_missing_template_id(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/apply_template",
            json={},
        )
        assert resp.status_code == 400

    def test_apply_template_nonexistent_template(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/apply_template",
            json={"template_id": 9999},
        )
        assert resp.status_code == 404
        assert resp.get_json()["ok"] is False

    def test_apply_template_nonexistent_item(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        seed_modifier_template_presets(rid)
        templates = list_modifier_templates(rid)
        resp = client.post(
            f"/drafts/{did}/items/9999/apply_template",
            json={"template_id": templates[0]["id"]},
        )
        assert resp.status_code == 404

    def test_migrate_modifier_groups_happy(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid1 = _db_item(fresh_db, did, "Burger")
        iid2 = _db_item(fresh_db, did, "Pizza")
        _db_variant(fresh_db, iid1, "Small")
        _db_variant(fresh_db, iid2, "Thin")

        resp = client.post(f"/drafts/{did}/migrate_modifier_groups")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["item_count"] == 2
        assert data["migrated_count"] == 2

    def test_migrate_modifier_groups_empty_draft(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        resp = client.post(f"/drafts/{did}/migrate_modifier_groups")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["migrated_count"] == 0

    def test_migrate_modifier_groups_idempotent(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _db_item(fresh_db, did)
        _db_variant(fresh_db, iid, "Small")

        client.post(f"/drafts/{did}/migrate_modifier_groups")
        resp = client.post(f"/drafts/{did}/migrate_modifier_groups")
        data = resp.get_json()
        # Second call: item already has groups, migrated_count=0
        assert data["migrated_count"] == 0

    def test_migrate_requires_login(self, fresh_db):
        import portal.app as _app_module
        app = _app_module.app
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        rid, did = _create_draft(fresh_db)
        with app.test_client() as anon:
            resp = anon.post(f"/drafts/{did}/migrate_modifier_groups")
        assert resp.status_code in (302, 401, 403)
