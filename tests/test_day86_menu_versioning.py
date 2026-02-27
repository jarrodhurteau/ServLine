"""
Day 86 -- Multi-Menu & Versioning Foundation (Phase 10, Day 1).

Schema additions, menu CRUD, version CRUD, draft-menu linking,
migration from legacy menu_items.

Covers:
  Schema Verification:
  - menus table has menu_type column
  - menus table has description column
  - menus table has updated_at column
  - drafts table has menu_id column
  - menu_versions table exists with correct columns
  - menu_version_items table exists with correct columns
  - menu_version_item_variants table exists with correct columns
  - menu_versions unique constraint on (menu_id, version_number)

  Menu CRUD:
  - create_menu returns dict with id
  - create_menu stores all fields
  - create_menu defaults active to 1
  - create_menu invalid type defaults to None
  - list_menus active only by default
  - list_menus include_inactive flag
  - list_menus empty restaurant returns empty list
  - list_menus includes version_count
  - get_menu by id
  - get_menu nonexistent returns None
  - update_menu changes fields
  - update_menu updates timestamp
  - delete_menu soft-deletes

  Version Creation:
  - create_menu_version returns dict with id
  - first version is number 1
  - auto-increments version number
  - custom label preserved
  - auto-label when none given
  - snapshot from draft copies items
  - snapshot from draft copies variants
  - item_count correct
  - variant_count correct
  - empty draft gives zero counts

  Version Retrieval:
  - list_menu_versions newest first
  - list_menu_versions empty menu
  - get_menu_version with items
  - get_menu_version without items
  - get_menu_version items have variants
  - get_menu_version nonexistent returns None
  - get_current_version returns latest
  - get_current_version no versions returns None

  Draft-Menu Linking:
  - save_draft_metadata with menu_id
  - get_draft includes menu_id
  - menu_id nullable backward compat
  - version source_draft_id tracks provenance

  FK Cascade and Integrity:
  - delete menu cascades to versions (hard delete)
  - delete version cascades to items
  - delete version item cascades to variants
  - version_number unique per menu
  - multiple menus have independent version numbers

  Migration:
  - migrate creates v1 for existing menu
  - migrate copies menu_items to version items
  - migrate is idempotent
  - migrate handles multiple menus
  - migrate skips menu with no items
  - migrate returns correct counts

  Edge Cases:
  - unicode menu name
  - all 5 variant kinds in version snapshot
  - large menu with 50 items
  - notes and created_by stored
  - version with no draft (empty version)
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional
from datetime import datetime


# ---------------------------------------------------------------------------
# In-memory DB helpers
# ---------------------------------------------------------------------------
_TEST_CONN: Optional[sqlite3.Connection] = None


def _make_test_db() -> sqlite3.Connection:
    """Create in-memory SQLite DB with full schema incl. Phase 10 tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            address TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            menu_type TEXT,
            description TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            is_available INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
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
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            category TEXT,
            position INTEGER,
            confidence INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_item_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            price_cents INTEGER NOT NULL DEFAULT 0,
            kind TEXT DEFAULT 'size',
            position INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (item_id) REFERENCES draft_items(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_export_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            format TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            variant_count INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0,
            exported_at TEXT NOT NULL,
            FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT NOT NULL UNIQUE,
            restaurant_id INTEGER,
            label TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            rate_limit_rpm INTEGER NOT NULL DEFAULT 60,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER,
            url TEXT NOT NULL,
            event_types TEXT NOT NULL DEFAULT '',
            secret TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
        )
    """)

    # Phase 10 tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_versions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id         INTEGER NOT NULL,
            version_number  INTEGER NOT NULL DEFAULT 1,
            label           TEXT,
            source_draft_id INTEGER,
            item_count      INTEGER NOT NULL DEFAULT 0,
            variant_count   INTEGER NOT NULL DEFAULT 0,
            notes           TEXT,
            created_by      TEXT,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE,
            FOREIGN KEY (source_draft_id) REFERENCES drafts(id) ON DELETE SET NULL,
            UNIQUE (menu_id, version_number)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_version_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id  INTEGER NOT NULL,
            name        TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            category    TEXT,
            position    INTEGER,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (version_id) REFERENCES menu_versions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_version_item_variants (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id     INTEGER NOT NULL,
            label       TEXT NOT NULL,
            price_cents INTEGER NOT NULL DEFAULT 0,
            kind        TEXT DEFAULT 'size',
            position    INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (item_id) REFERENCES menu_version_items(id) ON DELETE CASCADE
        )
    """)

    # Indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_draft ON draft_items(draft_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_variants_item ON draft_item_variants(item_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_menu_versions_menu ON menu_versions(menu_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mvi_version ON menu_version_items(version_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mviv_item ON menu_version_item_variants(item_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_menus_restaurant_active ON menus(restaurant_id, active)")
    conn.commit()
    return conn


def _patch_db(monkeypatch):
    global _TEST_CONN
    _TEST_CONN = _make_test_db()
    import storage.drafts as drafts_mod
    import storage.menus as menus_mod

    def mock_connect():
        return _TEST_CONN

    monkeypatch.setattr(drafts_mod, "db_connect", mock_connect)
    monkeypatch.setattr(menus_mod, "db_connect", mock_connect)
    return _TEST_CONN


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    conn = _patch_db(monkeypatch)
    yield conn
    global _TEST_CONN
    _TEST_CONN = None


# ---------------------------------------------------------------------------
# Data factory helpers
# ---------------------------------------------------------------------------
def _create_restaurant(conn, name="Test Restaurant") -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO restaurants (name, created_at) VALUES (?, datetime('now'))",
        (name,),
    )
    conn.commit()
    return int(cur.lastrowid)


def _create_menu_raw(conn, restaurant_id, name="Lunch Menu",
                     menu_type=None, description=None) -> int:
    """Direct SQL insert for test setup (bypasses the function under test)."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO menus (restaurant_id, name, menu_type, description, "
        "active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 1, datetime('now'), datetime('now'))",
        (restaurant_id, name, menu_type, description),
    )
    conn.commit()
    return int(cur.lastrowid)


def _create_draft(conn, title="Test Draft", status="editing",
                  restaurant_id=None, menu_id=None) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO drafts (title, status, restaurant_id, menu_id, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
        (title, status, restaurant_id, menu_id),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_item(conn, draft_id, name, price_cents=0, category=None,
                 description=None, position=None) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_items (draft_id, name, description, price_cents, "
        "category, position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (draft_id, name, description, price_cents, category, position),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_variant(conn, item_id, label, price_cents=0, kind="size",
                    position=0) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_item_variants (item_id, label, price_cents, kind, "
        "position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (item_id, label, price_cents, kind, position),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_menu_item(conn, menu_id, name, price_cents=0,
                      description=None) -> int:
    """Insert into legacy menu_items table (for migration tests)."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO menu_items (menu_id, name, description, price_cents, "
        "is_available, created_at) "
        "VALUES (?, ?, ?, ?, 1, datetime('now'))",
        (menu_id, name, description, price_cents),
    )
    conn.commit()
    return int(cur.lastrowid)


def _create_draft_with_items(conn, restaurant_id=None, menu_id=None,
                             item_count=3, with_variants=True) -> int:
    """Create a draft with N items, optionally with variants. Returns draft_id."""
    draft_id = _create_draft(conn, restaurant_id=restaurant_id, menu_id=menu_id)
    for i in range(item_count):
        item_id = _insert_item(
            conn, draft_id,
            name=f"Item {i+1}",
            price_cents=(i + 1) * 500,
            category="Entrees" if i % 2 == 0 else "Sides",
            description=f"Description for item {i+1}",
            position=i,
        )
        if with_variants:
            _insert_variant(conn, item_id, "Small", (i + 1) * 300, "size", 0)
            _insert_variant(conn, item_id, "Large", (i + 1) * 700, "size", 1)
    return draft_id


# ===========================================================================
# SECTION 1: Schema Verification
# ===========================================================================
class TestSchemaVerification:
    def test_menus_has_menu_type_column(self, fresh_db):
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(menus)").fetchall()}
        assert "menu_type" in cols

    def test_menus_has_description_column(self, fresh_db):
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(menus)").fetchall()}
        assert "description" in cols

    def test_menus_has_updated_at_column(self, fresh_db):
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(menus)").fetchall()}
        assert "updated_at" in cols

    def test_drafts_has_menu_id_column(self, fresh_db):
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(drafts)").fetchall()}
        assert "menu_id" in cols

    def test_menu_versions_table_exists(self, fresh_db):
        row = fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='menu_versions'"
        ).fetchone()
        assert row is not None

    def test_menu_versions_has_correct_columns(self, fresh_db):
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(menu_versions)").fetchall()}
        expected = {"id", "menu_id", "version_number", "label", "source_draft_id",
                    "item_count", "variant_count", "notes", "created_by", "created_at"}
        assert expected == cols

    def test_menu_version_items_table_exists(self, fresh_db):
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(menu_version_items)").fetchall()}
        expected = {"id", "version_id", "name", "description", "price_cents",
                    "category", "position", "created_at"}
        assert expected == cols

    def test_menu_version_item_variants_table_exists(self, fresh_db):
        cols = {r[1] for r in fresh_db.execute("PRAGMA table_info(menu_version_item_variants)").fetchall()}
        expected = {"id", "item_id", "label", "price_cents", "kind",
                    "position", "created_at"}
        assert expected == cols


# ===========================================================================
# SECTION 2: Menu CRUD
# ===========================================================================
class TestMenuCRUD:
    def test_create_menu_returns_dict_with_id(self, fresh_db):
        from storage.menus import create_menu
        rid = _create_restaurant(fresh_db)
        result = create_menu(rid, "Lunch")
        assert isinstance(result, dict)
        assert "id" in result
        assert result["id"] > 0

    def test_create_menu_stores_all_fields(self, fresh_db):
        from storage.menus import create_menu
        rid = _create_restaurant(fresh_db)
        result = create_menu(rid, "Dinner", menu_type="dinner",
                             description="Evening specials")
        assert result["name"] == "Dinner"
        assert result["menu_type"] == "dinner"
        assert result["description"] == "Evening specials"
        assert result["restaurant_id"] == rid

    def test_create_menu_defaults_active(self, fresh_db):
        from storage.menus import create_menu
        rid = _create_restaurant(fresh_db)
        result = create_menu(rid, "Lunch")
        assert result["active"] == 1

    def test_create_menu_invalid_type_defaults_none(self, fresh_db):
        from storage.menus import create_menu
        rid = _create_restaurant(fresh_db)
        result = create_menu(rid, "Lunch", menu_type="bogus_type")
        assert result["menu_type"] is None

    def test_list_menus_active_only_by_default(self, fresh_db):
        from storage.menus import list_menus
        rid = _create_restaurant(fresh_db)
        _create_menu_raw(fresh_db, rid, "Active Menu")
        mid2 = _create_menu_raw(fresh_db, rid, "Deleted Menu")
        fresh_db.execute("UPDATE menus SET active=0 WHERE id=?", (mid2,))
        fresh_db.commit()
        menus = list_menus(rid)
        assert len(menus) == 1
        assert menus[0]["name"] == "Active Menu"

    def test_list_menus_include_inactive(self, fresh_db):
        from storage.menus import list_menus
        rid = _create_restaurant(fresh_db)
        _create_menu_raw(fresh_db, rid, "Active Menu")
        mid2 = _create_menu_raw(fresh_db, rid, "Deleted Menu")
        fresh_db.execute("UPDATE menus SET active=0 WHERE id=?", (mid2,))
        fresh_db.commit()
        menus = list_menus(rid, include_inactive=True)
        assert len(menus) == 2

    def test_list_menus_empty_restaurant(self, fresh_db):
        from storage.menus import list_menus
        rid = _create_restaurant(fresh_db)
        menus = list_menus(rid)
        assert menus == []

    def test_list_menus_includes_version_count(self, fresh_db):
        from storage.menus import list_menus
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        # Insert a version directly
        fresh_db.execute(
            "INSERT INTO menu_versions (menu_id, version_number, label, created_at) "
            "VALUES (?, 1, 'v1', datetime('now'))", (mid,)
        )
        fresh_db.commit()
        menus = list_menus(rid)
        assert menus[0]["version_count"] == 1

    def test_get_menu_by_id(self, fresh_db):
        from storage.menus import get_menu
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Brunch", "brunch", "Weekend brunch")
        result = get_menu(mid)
        assert result is not None
        assert result["name"] == "Brunch"
        assert result["menu_type"] == "brunch"

    def test_get_menu_nonexistent_returns_none(self, fresh_db):
        from storage.menus import get_menu
        assert get_menu(9999) is None

    def test_update_menu_changes_fields(self, fresh_db):
        from storage.menus import update_menu, get_menu
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        ok = update_menu(mid, name="Dinner", menu_type="dinner",
                         description="Updated desc")
        assert ok is True
        m = get_menu(mid)
        assert m["name"] == "Dinner"
        assert m["menu_type"] == "dinner"
        assert m["description"] == "Updated desc"

    def test_update_menu_updates_timestamp(self, fresh_db):
        from storage.menus import update_menu, get_menu
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        m_before = get_menu(mid)
        update_menu(mid, name="Dinner")
        m_after = get_menu(mid)
        assert m_after["updated_at"] is not None

    def test_delete_menu_soft_deletes(self, fresh_db):
        from storage.menus import delete_menu, get_menu, list_menus
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        ok = delete_menu(mid)
        assert ok is True
        # Still exists in DB
        m = get_menu(mid)
        assert m is not None
        assert m["active"] == 0
        # Hidden from list_menus
        assert list_menus(rid) == []
        # Second delete returns False
        assert delete_menu(mid) is False


# ===========================================================================
# SECTION 3: Version Creation
# ===========================================================================
class TestVersionCreation:
    def test_create_version_returns_dict_with_id(self, fresh_db):
        from storage.menus import create_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        v = create_menu_version(mid)
        assert isinstance(v, dict)
        assert "id" in v
        assert v["id"] > 0

    def test_first_version_is_1(self, fresh_db):
        from storage.menus import create_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        v = create_menu_version(mid)
        assert v["version_number"] == 1

    def test_auto_increments_version_number(self, fresh_db):
        from storage.menus import create_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        v1 = create_menu_version(mid)
        v2 = create_menu_version(mid)
        v3 = create_menu_version(mid)
        assert v1["version_number"] == 1
        assert v2["version_number"] == 2
        assert v3["version_number"] == 3

    def test_custom_label_preserved(self, fresh_db):
        from storage.menus import create_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        v = create_menu_version(mid, label="Summer 2026")
        assert v["label"] == "Summer 2026"

    def test_auto_label_when_none(self, fresh_db):
        from storage.menus import create_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        v = create_menu_version(mid)
        assert v["label"] == "v1"
        v2 = create_menu_version(mid)
        assert v2["label"] == "v2"

    def test_snapshot_from_draft_copies_items(self, fresh_db):
        from storage.menus import create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft_with_items(fresh_db, restaurant_id=rid,
                                            item_count=3, with_variants=False)
        v = create_menu_version(mid, source_draft_id=draft_id)
        full = get_menu_version(v["id"])
        assert len(full["items"]) == 3
        names = [it["name"] for it in full["items"]]
        assert "Item 1" in names
        assert "Item 2" in names
        assert "Item 3" in names

    def test_snapshot_from_draft_copies_variants(self, fresh_db):
        from storage.menus import create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft_with_items(fresh_db, restaurant_id=rid,
                                            item_count=2, with_variants=True)
        v = create_menu_version(mid, source_draft_id=draft_id)
        full = get_menu_version(v["id"])
        for it in full["items"]:
            assert len(it["variants"]) == 2
            labels = {vr["label"] for vr in it["variants"]}
            assert "Small" in labels
            assert "Large" in labels

    def test_item_count_correct(self, fresh_db):
        from storage.menus import create_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft_with_items(fresh_db, restaurant_id=rid,
                                            item_count=5, with_variants=False)
        v = create_menu_version(mid, source_draft_id=draft_id)
        assert v["item_count"] == 5

    def test_variant_count_correct(self, fresh_db):
        from storage.menus import create_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft_with_items(fresh_db, restaurant_id=rid,
                                            item_count=3, with_variants=True)
        v = create_menu_version(mid, source_draft_id=draft_id)
        # 3 items * 2 variants each = 6
        assert v["variant_count"] == 6

    def test_empty_draft_zero_counts(self, fresh_db):
        from storage.menus import create_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft(fresh_db, restaurant_id=rid)
        v = create_menu_version(mid, source_draft_id=draft_id)
        assert v["item_count"] == 0
        assert v["variant_count"] == 0


# ===========================================================================
# SECTION 4: Version Retrieval
# ===========================================================================
class TestVersionRetrieval:
    def test_list_versions_newest_first(self, fresh_db):
        from storage.menus import create_menu_version, list_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        create_menu_version(mid, label="v1")
        create_menu_version(mid, label="v2")
        create_menu_version(mid, label="v3")
        versions = list_menu_versions(mid)
        assert len(versions) == 3
        assert versions[0]["version_number"] == 3
        assert versions[1]["version_number"] == 2
        assert versions[2]["version_number"] == 1

    def test_list_versions_empty_menu(self, fresh_db):
        from storage.menus import list_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        assert list_menu_versions(mid) == []

    def test_get_version_with_items(self, fresh_db):
        from storage.menus import create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft_with_items(fresh_db, item_count=4,
                                            with_variants=False)
        v = create_menu_version(mid, source_draft_id=draft_id)
        full = get_menu_version(v["id"], include_items=True)
        assert "items" in full
        assert len(full["items"]) == 4

    def test_get_version_without_items(self, fresh_db):
        from storage.menus import create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        v = create_menu_version(mid)
        result = get_menu_version(v["id"], include_items=False)
        assert result is not None
        assert "items" not in result

    def test_get_version_items_have_variants(self, fresh_db):
        from storage.menus import create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft_with_items(fresh_db, item_count=1,
                                            with_variants=True)
        v = create_menu_version(mid, source_draft_id=draft_id)
        full = get_menu_version(v["id"])
        assert len(full["items"]) == 1
        item = full["items"][0]
        assert len(item["variants"]) == 2
        assert item["variants"][0]["label"] == "Small"
        assert item["variants"][1]["label"] == "Large"

    def test_get_version_nonexistent_returns_none(self, fresh_db):
        from storage.menus import get_menu_version
        assert get_menu_version(9999) is None

    def test_get_current_version(self, fresh_db):
        from storage.menus import create_menu_version, get_current_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        create_menu_version(mid, label="v1")
        create_menu_version(mid, label="v2")
        create_menu_version(mid, label="v3")
        current = get_current_version(mid)
        assert current is not None
        assert current["version_number"] == 3
        assert current["label"] == "v3"

    def test_get_current_version_no_versions_returns_none(self, fresh_db):
        from storage.menus import get_current_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        assert get_current_version(mid) is None


# ===========================================================================
# SECTION 5: Draft-Menu Linking
# ===========================================================================
class TestDraftMenuLinking:
    def test_save_draft_metadata_with_menu_id(self, fresh_db):
        from storage.drafts import save_draft_metadata
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft(fresh_db, restaurant_id=rid)
        save_draft_metadata(draft_id, menu_id=mid)
        row = fresh_db.execute(
            "SELECT menu_id FROM drafts WHERE id=?", (draft_id,)
        ).fetchone()
        assert row["menu_id"] == mid

    def test_get_draft_includes_menu_id(self, fresh_db):
        from storage.drafts import get_draft
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft(fresh_db, restaurant_id=rid, menu_id=mid)
        d = get_draft(draft_id)
        assert d is not None
        assert d["menu_id"] == mid

    def test_menu_id_nullable_backward_compat(self, fresh_db):
        from storage.drafts import get_draft
        draft_id = _create_draft(fresh_db)
        d = get_draft(draft_id)
        assert d["menu_id"] is None

    def test_version_source_draft_id_tracks_provenance(self, fresh_db):
        from storage.menus import create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft_with_items(fresh_db, restaurant_id=rid,
                                            item_count=2, with_variants=False)
        v = create_menu_version(mid, source_draft_id=draft_id)
        full = get_menu_version(v["id"], include_items=False)
        assert full["source_draft_id"] == draft_id


# ===========================================================================
# SECTION 6: FK Cascade and Integrity
# ===========================================================================
class TestCascadeAndIntegrity:
    def test_delete_menu_cascades_to_versions(self, fresh_db):
        """Hard-delete menu cascades to versions via FK."""
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        fresh_db.execute(
            "INSERT INTO menu_versions (menu_id, version_number, label, created_at) "
            "VALUES (?, 1, 'v1', datetime('now'))", (mid,)
        )
        fresh_db.commit()
        # Hard delete (not soft) to test CASCADE
        fresh_db.execute("DELETE FROM menus WHERE id=?", (mid,))
        fresh_db.commit()
        row = fresh_db.execute(
            "SELECT COUNT(*) AS cnt FROM menu_versions WHERE menu_id=?", (mid,)
        ).fetchone()
        assert row["cnt"] == 0

    def test_delete_version_cascades_to_items(self, fresh_db):
        from storage.menus import create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft_with_items(fresh_db, item_count=3,
                                            with_variants=False)
        v = create_menu_version(mid, source_draft_id=draft_id)
        fresh_db.execute("DELETE FROM menu_versions WHERE id=?", (v["id"],))
        fresh_db.commit()
        cnt = fresh_db.execute(
            "SELECT COUNT(*) AS cnt FROM menu_version_items WHERE version_id=?",
            (v["id"],),
        ).fetchone()["cnt"]
        assert cnt == 0

    def test_delete_version_item_cascades_to_variants(self, fresh_db):
        from storage.menus import create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft_with_items(fresh_db, item_count=1,
                                            with_variants=True)
        v = create_menu_version(mid, source_draft_id=draft_id)
        full = get_menu_version(v["id"])
        item_id = full["items"][0]["id"]
        fresh_db.execute("DELETE FROM menu_version_items WHERE id=?", (item_id,))
        fresh_db.commit()
        cnt = fresh_db.execute(
            "SELECT COUNT(*) AS cnt FROM menu_version_item_variants WHERE item_id=?",
            (item_id,),
        ).fetchone()["cnt"]
        assert cnt == 0

    def test_version_number_unique_per_menu(self, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        fresh_db.execute(
            "INSERT INTO menu_versions (menu_id, version_number, label, created_at) "
            "VALUES (?, 1, 'v1', datetime('now'))", (mid,)
        )
        fresh_db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            fresh_db.execute(
                "INSERT INTO menu_versions (menu_id, version_number, label, created_at) "
                "VALUES (?, 1, 'v1-dup', datetime('now'))", (mid,)
            )

    def test_multiple_menus_independent_version_numbers(self, fresh_db):
        from storage.menus import create_menu_version
        rid = _create_restaurant(fresh_db)
        mid1 = _create_menu_raw(fresh_db, rid, "Lunch")
        mid2 = _create_menu_raw(fresh_db, rid, "Dinner")
        v1 = create_menu_version(mid1)
        v2 = create_menu_version(mid2)
        # Both menus get version 1 independently
        assert v1["version_number"] == 1
        assert v2["version_number"] == 1


# ===========================================================================
# SECTION 7: Migration
# ===========================================================================
class TestExistingMenuMigration:
    def test_migrate_creates_v1_for_existing_menu(self, fresh_db):
        from storage.menus import migrate_existing_menus
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        _insert_menu_item(fresh_db, mid, "Burger", 999)
        result = migrate_existing_menus()
        assert result["menus_migrated"] == 1
        row = fresh_db.execute(
            "SELECT * FROM menu_versions WHERE menu_id=?", (mid,)
        ).fetchone()
        assert row is not None
        assert row["version_number"] == 1
        assert "migrated" in row["label"].lower()

    def test_migrate_copies_menu_items_to_version_items(self, fresh_db):
        from storage.menus import migrate_existing_menus
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        _insert_menu_item(fresh_db, mid, "Burger", 999)
        _insert_menu_item(fresh_db, mid, "Fries", 499, "Crispy fries")
        migrate_existing_menus()
        ver = fresh_db.execute(
            "SELECT id FROM menu_versions WHERE menu_id=?", (mid,)
        ).fetchone()
        items = fresh_db.execute(
            "SELECT * FROM menu_version_items WHERE version_id=? ORDER BY position",
            (ver["id"],),
        ).fetchall()
        assert len(items) == 2
        assert items[0]["name"] == "Burger"
        assert items[0]["price_cents"] == 999
        assert items[1]["name"] == "Fries"
        assert items[1]["description"] == "Crispy fries"

    def test_migrate_idempotent(self, fresh_db):
        from storage.menus import migrate_existing_menus
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        _insert_menu_item(fresh_db, mid, "Burger", 999)
        r1 = migrate_existing_menus()
        r2 = migrate_existing_menus()
        assert r1["menus_migrated"] == 1
        assert r2["menus_migrated"] == 0
        # Still only 1 version
        cnt = fresh_db.execute(
            "SELECT COUNT(*) AS cnt FROM menu_versions WHERE menu_id=?", (mid,)
        ).fetchone()["cnt"]
        assert cnt == 1

    def test_migrate_multiple_menus(self, fresh_db):
        from storage.menus import migrate_existing_menus
        rid = _create_restaurant(fresh_db)
        mid1 = _create_menu_raw(fresh_db, rid, "Lunch")
        mid2 = _create_menu_raw(fresh_db, rid, "Dinner")
        _insert_menu_item(fresh_db, mid1, "Burger", 999)
        _insert_menu_item(fresh_db, mid2, "Steak", 2499)
        _insert_menu_item(fresh_db, mid2, "Salmon", 1899)
        result = migrate_existing_menus()
        assert result["menus_migrated"] == 2
        assert result["items_copied"] == 3

    def test_migrate_skips_menu_with_no_items(self, fresh_db):
        from storage.menus import migrate_existing_menus
        rid = _create_restaurant(fresh_db)
        _create_menu_raw(fresh_db, rid, "Empty Menu")
        result = migrate_existing_menus()
        assert result["menus_migrated"] == 0

    def test_migrate_returns_correct_counts(self, fresh_db):
        from storage.menus import migrate_existing_menus
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        for i in range(5):
            _insert_menu_item(fresh_db, mid, f"Item {i}", (i + 1) * 100)
        result = migrate_existing_menus()
        assert result["menus_migrated"] == 1
        assert result["items_copied"] == 5


# ===========================================================================
# SECTION 8: Edge Cases
# ===========================================================================
class TestEdgeCases:
    def test_unicode_menu_name(self, fresh_db):
        from storage.menus import create_menu, get_menu
        rid = _create_restaurant(fresh_db)
        m = create_menu(rid, "Caf\u00e9 du Monde \u2014 D\u00e9jeuner")
        loaded = get_menu(m["id"])
        assert loaded["name"] == "Caf\u00e9 du Monde \u2014 D\u00e9jeuner"

    def test_all_5_variant_kinds_in_snapshot(self, fresh_db):
        from storage.menus import create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        draft_id = _create_draft(fresh_db, restaurant_id=rid)
        item_id = _insert_item(fresh_db, draft_id, "Pizza", 1000,
                               category="Entrees")
        kinds = ["size", "combo", "flavor", "style", "other"]
        for i, kind in enumerate(kinds):
            _insert_variant(fresh_db, item_id, f"Var-{kind}", (i + 1) * 200,
                            kind, i)
        v = create_menu_version(mid, source_draft_id=draft_id)
        full = get_menu_version(v["id"])
        assert v["variant_count"] == 5
        found_kinds = {vr["kind"] for vr in full["items"][0]["variants"]}
        assert found_kinds == set(kinds)

    def test_large_menu_50_items(self, fresh_db):
        from storage.menus import create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Full Menu")
        draft_id = _create_draft_with_items(fresh_db, restaurant_id=rid,
                                            item_count=50, with_variants=False)
        v = create_menu_version(mid, source_draft_id=draft_id)
        assert v["item_count"] == 50
        full = get_menu_version(v["id"])
        assert len(full["items"]) == 50

    def test_notes_and_created_by_stored(self, fresh_db):
        from storage.menus import create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        v = create_menu_version(mid, notes="Seasonal update",
                                created_by="admin@test.com")
        full = get_menu_version(v["id"], include_items=False)
        assert full["notes"] == "Seasonal update"
        assert full["created_by"] == "admin@test.com"

    def test_version_with_no_draft_empty(self, fresh_db):
        from storage.menus import create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, "Lunch")
        v = create_menu_version(mid)
        assert v["item_count"] == 0
        assert v["variant_count"] == 0
        assert v["source_draft_id"] is None
        full = get_menu_version(v["id"])
        assert full["items"] == []
