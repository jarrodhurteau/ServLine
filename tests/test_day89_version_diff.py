"""
Day 89 -- Version Comparison & Diff Engine (Phase 10, Sprint 10.2).

Diff engine in storage/menus.py and Flask comparison route.

Covers:
  Diff Engine — Identical Versions:
  - identical items all classified unchanged
  - summary counts all zero
  - no field changes on unchanged items
  - identical variants classified unchanged

  Diff Engine — Added Items:
  - empty A, all items added
  - added items have item_b set, item_a None
  - summary added count matches
  - added items preserve all fields

  Diff Engine — Removed Items:
  - empty B, all items removed
  - removed items have item_a set, item_b None
  - summary removed count matches
  - removed items preserve all fields

  Diff Engine — Modified Items:
  - name case change detected
  - description change detected
  - price change detected
  - category change detected
  - position change detected
  - multiple field changes
  - old/new values correct
  - summary modified count matches

  Diff Engine — Mixed Changes:
  - mixed add/remove/modify/unchanged
  - summary totals correct
  - changes sorted: modified > added > removed > unchanged
  - single item versions, same name different price
  - large version diff (many items)

  Diff Engine — Variant Changes:
  - variant added on matched item
  - variant removed on matched item
  - variant price change detected
  - variant kind change detected
  - multiple variant changes simultaneously
  - item unchanged but variant added = modified
  - variant changes on field-modified item
  - items with no variants both sides

  Diff Engine — Edge Cases:
  - both versions empty
  - version not found returns None
  - different menus returns None
  - duplicate names same category matched
  - duplicate names different categories matched
  - whitespace normalization in name matching
  - case insensitive name matching

  Compare Route:
  - route renders 200
  - summary displayed in HTML
  - added item visible
  - removed item visible with strikethrough class
  - modified field changes visible
  - 404 for missing version
  - 404 for wrong menu
  - missing params redirects to menu detail

  Compare UI on Menu Detail:
  - compare form shown with 2+ versions
  - no compare form with single version
  - no compare form with no versions
  - compare form has version selectors
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 88)
# ---------------------------------------------------------------------------
_TEST_CONN: Optional[sqlite3.Connection] = None


def _make_test_db() -> sqlite3.Connection:
    """Create in-memory SQLite DB with full schema."""
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
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO menus (restaurant_id, name, menu_type, description, "
        "active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 1, datetime('now'), datetime('now'))",
        (restaurant_id, name, menu_type, description),
    )
    conn.commit()
    return int(cur.lastrowid)


def _create_version_raw(conn, menu_id, version_number=1, source_draft_id=None,
                        item_count=0, variant_count=0, label=None, notes=None) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO menu_versions (menu_id, version_number, label, source_draft_id, "
        "item_count, variant_count, notes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (menu_id, version_number, label or f"v{version_number}",
         source_draft_id, item_count, variant_count, notes),
    )
    conn.commit()
    return int(cur.lastrowid)


def _create_version_with_items(conn, menu_id, version_number=1,
                                items=None, label=None) -> int:
    """Create a version with directly inserted items + variants.

    items: list of dicts with name, description, price_cents, category, position,
           variants: [{label, price_cents, kind, position}]
    """
    items = items or []
    variant_total = sum(len(it.get("variants", [])) for it in items)
    vid = _create_version_raw(
        conn, menu_id, version_number=version_number,
        item_count=len(items), variant_count=variant_total,
        label=label,
    )
    for it in items:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO menu_version_items (version_id, name, description, "
            "price_cents, category, position, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
            (vid, it["name"], it.get("description"), it.get("price_cents", 0),
             it.get("category"), it.get("position", 0)),
        )
        item_id = int(cur.lastrowid)
        for v in it.get("variants", []):
            conn.execute(
                "INSERT INTO menu_version_item_variants (item_id, label, "
                "price_cents, kind, position, created_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (item_id, v["label"], v.get("price_cents", 0),
                 v.get("kind", "size"), v.get("position", 0)),
            )
    conn.commit()
    return vid


# ---------------------------------------------------------------------------
# Flask test client fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch, fresh_db):
    """Flask test client with authenticated session."""
    from portal import app as app_mod
    import storage.menus as menus_mod

    monkeypatch.setattr(app_mod, "menus_store", menus_mod)

    def mock_connect():
        return _TEST_CONN
    monkeypatch.setattr(app_mod, "db_connect", mock_connect)

    app_mod.app.config["TESTING"] = True
    app_mod.app.config["SECRET_KEY"] = "test-secret"
    with app_mod.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = {"id": 1, "email": "test@test.com"}
        yield c


# ===========================================================================
# Diff Engine — Identical Versions
# ===========================================================================
class TestDiffIdentical:
    """Two versions with the same items should be all unchanged."""

    def _setup_identical(self, conn):
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        items = [
            {"name": "Burger", "description": "Beef", "price_cents": 1200,
             "category": "Entrees", "position": 0},
            {"name": "Fries", "description": "Crispy", "price_cents": 500,
             "category": "Sides", "position": 1},
        ]
        v1 = _create_version_with_items(conn, mid, 1, items)
        v2 = _create_version_with_items(conn, mid, 2, items)
        return v1, v2

    def test_all_unchanged(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._setup_identical(fresh_db)
        diff = compare_menu_versions(v1, v2)
        assert diff is not None
        statuses = [c["status"] for c in diff["changes"]]
        assert all(s == "unchanged" for s in statuses)

    def test_summary_zeroes(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._setup_identical(fresh_db)
        diff = compare_menu_versions(v1, v2)
        assert diff["summary"]["added"] == 0
        assert diff["summary"]["removed"] == 0
        assert diff["summary"]["modified"] == 0
        assert diff["summary"]["unchanged"] == 2

    def test_no_field_changes(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._setup_identical(fresh_db)
        diff = compare_menu_versions(v1, v2)
        for c in diff["changes"]:
            assert c["field_changes"] == []

    def test_identical_variants_unchanged(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        items = [
            {"name": "Pizza", "price_cents": 1500, "category": "Entrees",
             "position": 0, "variants": [
                 {"label": "Small", "price_cents": 1000, "kind": "size", "position": 0},
                 {"label": "Large", "price_cents": 1800, "kind": "size", "position": 1},
             ]},
        ]
        v1 = _create_version_with_items(fresh_db, mid, 1, items)
        v2 = _create_version_with_items(fresh_db, mid, 2, items)
        diff = compare_menu_versions(v1, v2)
        c = diff["changes"][0]
        assert c["status"] == "unchanged"
        vc = c["variant_changes"]
        assert len(vc["unchanged"]) == 2
        assert vc["added"] == []
        assert vc["removed"] == []
        assert vc["modified"] == []


# ===========================================================================
# Diff Engine — Added Items
# ===========================================================================
class TestDiffAdded:
    """Version A empty, version B has items — all should be added."""

    def _setup(self, conn):
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        v1 = _create_version_with_items(conn, mid, 1, [])
        items_b = [
            {"name": "Burger", "price_cents": 1200, "category": "Entrees", "position": 0},
            {"name": "Salad", "price_cents": 800, "category": "Sides", "position": 1},
        ]
        v2 = _create_version_with_items(conn, mid, 2, items_b)
        return v1, v2

    def test_all_added(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._setup(fresh_db)
        diff = compare_menu_versions(v1, v2)
        statuses = [c["status"] for c in diff["changes"]]
        assert all(s == "added" for s in statuses)
        assert len(statuses) == 2

    def test_item_b_set_item_a_none(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._setup(fresh_db)
        diff = compare_menu_versions(v1, v2)
        for c in diff["changes"]:
            assert c["item_b"] is not None
            assert c["item_a"] is None

    def test_summary_added_count(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._setup(fresh_db)
        diff = compare_menu_versions(v1, v2)
        assert diff["summary"]["added"] == 2
        assert diff["summary"]["total_a"] == 0
        assert diff["summary"]["total_b"] == 2

    def test_added_preserves_fields(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._setup(fresh_db)
        diff = compare_menu_versions(v1, v2)
        names = {c["item_b"]["name"] for c in diff["changes"]}
        assert "Burger" in names
        assert "Salad" in names


# ===========================================================================
# Diff Engine — Removed Items
# ===========================================================================
class TestDiffRemoved:
    """Version A has items, version B empty — all should be removed."""

    def _setup(self, conn):
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        items_a = [
            {"name": "Burger", "price_cents": 1200, "category": "Entrees", "position": 0},
            {"name": "Wings", "price_cents": 900, "category": "Appetizers", "position": 1},
        ]
        v1 = _create_version_with_items(conn, mid, 1, items_a)
        v2 = _create_version_with_items(conn, mid, 2, [])
        return v1, v2

    def test_all_removed(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._setup(fresh_db)
        diff = compare_menu_versions(v1, v2)
        statuses = [c["status"] for c in diff["changes"]]
        assert all(s == "removed" for s in statuses)
        assert len(statuses) == 2

    def test_item_a_set_item_b_none(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._setup(fresh_db)
        diff = compare_menu_versions(v1, v2)
        for c in diff["changes"]:
            assert c["item_a"] is not None
            assert c["item_b"] is None

    def test_summary_removed_count(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._setup(fresh_db)
        diff = compare_menu_versions(v1, v2)
        assert diff["summary"]["removed"] == 2
        assert diff["summary"]["total_a"] == 2
        assert diff["summary"]["total_b"] == 0

    def test_removed_preserves_fields(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._setup(fresh_db)
        diff = compare_menu_versions(v1, v2)
        names = {c["item_a"]["name"] for c in diff["changes"]}
        assert "Burger" in names
        assert "Wings" in names


# ===========================================================================
# Diff Engine — Modified Items
# ===========================================================================
class TestDiffModified:
    """Items present in both versions with field changes."""

    def _make_pair(self, conn, item_a, item_b):
        """Create two single-item versions to compare."""
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        v1 = _create_version_with_items(conn, mid, 1, [item_a])
        v2 = _create_version_with_items(conn, mid, 2, [item_b])
        return v1, v2

    def test_name_case_change(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._make_pair(fresh_db,
            {"name": "Burger", "price_cents": 1200, "category": "Entrees", "position": 0},
            {"name": "BURGER", "price_cents": 1200, "category": "Entrees", "position": 0},
        )
        diff = compare_menu_versions(v1, v2)
        c = diff["changes"][0]
        assert c["status"] == "modified"
        fields = {fc["field"] for fc in c["field_changes"]}
        assert "name" in fields

    def test_description_change(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._make_pair(fresh_db,
            {"name": "Burger", "description": "Classic", "price_cents": 1200,
             "category": "Entrees", "position": 0},
            {"name": "Burger", "description": "Premium Angus", "price_cents": 1200,
             "category": "Entrees", "position": 0},
        )
        diff = compare_menu_versions(v1, v2)
        c = diff["changes"][0]
        assert c["status"] == "modified"
        fc = [f for f in c["field_changes"] if f["field"] == "description"][0]
        assert fc["old"] == "Classic"
        assert fc["new"] == "Premium Angus"

    def test_price_change(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._make_pair(fresh_db,
            {"name": "Burger", "price_cents": 1200, "category": "Entrees", "position": 0},
            {"name": "Burger", "price_cents": 1500, "category": "Entrees", "position": 0},
        )
        diff = compare_menu_versions(v1, v2)
        c = diff["changes"][0]
        assert c["status"] == "modified"
        fc = [f for f in c["field_changes"] if f["field"] == "price_cents"][0]
        assert fc["old"] == 1200
        assert fc["new"] == 1500

    def test_category_change(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._make_pair(fresh_db,
            {"name": "Burger", "price_cents": 1200, "category": "Appetizers", "position": 0},
            {"name": "Burger", "price_cents": 1200, "category": "Entrees", "position": 0},
        )
        diff = compare_menu_versions(v1, v2)
        c = diff["changes"][0]
        fc = [f for f in c["field_changes"] if f["field"] == "category"][0]
        assert fc["old"] == "Appetizers"
        assert fc["new"] == "Entrees"

    def test_position_change(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._make_pair(fresh_db,
            {"name": "Burger", "price_cents": 1200, "category": "Entrees", "position": 0},
            {"name": "Burger", "price_cents": 1200, "category": "Entrees", "position": 5},
        )
        diff = compare_menu_versions(v1, v2)
        c = diff["changes"][0]
        fc = [f for f in c["field_changes"] if f["field"] == "position"][0]
        assert fc["old"] == 0
        assert fc["new"] == 5

    def test_multiple_field_changes(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._make_pair(fresh_db,
            {"name": "Burger", "description": "Old", "price_cents": 1200,
             "category": "Appetizers", "position": 0},
            {"name": "Burger", "description": "New", "price_cents": 1500,
             "category": "Entrees", "position": 0},
        )
        diff = compare_menu_versions(v1, v2)
        c = diff["changes"][0]
        assert len(c["field_changes"]) == 3  # description, price, category

    def test_old_new_values_correct(self, fresh_db):
        from storage.menus import compare_menu_versions
        v1, v2 = self._make_pair(fresh_db,
            {"name": "Burger", "price_cents": 1000, "category": "Entrees", "position": 0},
            {"name": "Burger", "price_cents": 2000, "category": "Entrees", "position": 0},
        )
        diff = compare_menu_versions(v1, v2)
        fc = diff["changes"][0]["field_changes"][0]
        assert fc["field"] == "price_cents"
        assert fc["old"] == 1000
        assert fc["new"] == 2000

    def test_summary_modified_count(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "A", "price_cents": 100, "position": 0},
            {"name": "B", "price_cents": 200, "position": 1},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "A", "price_cents": 150, "position": 0},
            {"name": "B", "price_cents": 250, "position": 1},
        ])
        diff = compare_menu_versions(v1, v2)
        assert diff["summary"]["modified"] == 2


# ===========================================================================
# Diff Engine — Mixed Changes
# ===========================================================================
class TestDiffMixed:
    """Various combinations of add/remove/modify/unchanged."""

    def test_mixed_scenario(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 1200, "category": "Entrees", "position": 0},
            {"name": "Fries", "price_cents": 500, "category": "Sides", "position": 1},
            {"name": "Soda", "price_cents": 200, "category": "Drinks", "position": 2},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Burger", "price_cents": 1500, "category": "Entrees", "position": 0},  # modified
            {"name": "Fries", "price_cents": 500, "category": "Sides", "position": 1},  # unchanged
            # Soda removed
            {"name": "Salad", "price_cents": 700, "category": "Sides", "position": 3},  # added
        ])
        diff = compare_menu_versions(v1, v2)
        statuses = {c["status"] for c in diff["changes"]}
        assert statuses == {"modified", "unchanged", "removed", "added"}

    def test_summary_totals(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "A", "price_cents": 100, "position": 0},
            {"name": "B", "price_cents": 200, "position": 1},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "A", "price_cents": 100, "position": 0},
            {"name": "C", "price_cents": 300, "position": 1},
        ])
        diff = compare_menu_versions(v1, v2)
        assert diff["summary"]["total_a"] == 2
        assert diff["summary"]["total_b"] == 2
        assert diff["summary"]["unchanged"] == 1
        assert diff["summary"]["removed"] == 1
        assert diff["summary"]["added"] == 1

    def test_sort_order(self, fresh_db):
        """Modified first, then added, then removed, then unchanged."""
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Mod", "price_cents": 100, "position": 0},
            {"name": "Rem", "price_cents": 200, "position": 1},
            {"name": "Same", "price_cents": 300, "position": 2},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Mod", "price_cents": 999, "position": 0},
            {"name": "Same", "price_cents": 300, "position": 2},
            {"name": "New", "price_cents": 400, "position": 3},
        ])
        diff = compare_menu_versions(v1, v2)
        statuses = [c["status"] for c in diff["changes"]]
        # modified < added < removed < unchanged
        for i in range(len(statuses) - 1):
            order = {"modified": 0, "added": 1, "removed": 2, "unchanged": 3}
            assert order[statuses[i]] <= order[statuses[i + 1]]

    def test_single_item_same_name_different_price(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Pizza", "price_cents": 1200, "position": 0},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Pizza", "price_cents": 1500, "position": 0},
        ])
        diff = compare_menu_versions(v1, v2)
        assert len(diff["changes"]) == 1
        assert diff["changes"][0]["status"] == "modified"

    def test_large_version(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        items_a = [{"name": f"Item{i}", "price_cents": i * 100, "position": i}
                    for i in range(20)]
        # Modify 5, remove 3, keep 12 unchanged, add 4
        items_b = []
        for i in range(20):
            if i < 5:
                items_b.append({"name": f"Item{i}", "price_cents": i * 100 + 50, "position": i})
            elif i < 8:
                continue  # removed
            else:
                items_b.append({"name": f"Item{i}", "price_cents": i * 100, "position": i})
        for i in range(20, 24):
            items_b.append({"name": f"Item{i}", "price_cents": i * 100, "position": i})

        v1 = _create_version_with_items(fresh_db, mid, 1, items_a)
        v2 = _create_version_with_items(fresh_db, mid, 2, items_b)
        diff = compare_menu_versions(v1, v2)
        assert diff["summary"]["modified"] == 5
        assert diff["summary"]["removed"] == 3
        assert diff["summary"]["unchanged"] == 12
        assert diff["summary"]["added"] == 4


# ===========================================================================
# Diff Engine — Variant Changes
# ===========================================================================
class TestDiffVariants:
    """Variant-level diff on matched items."""

    def test_variant_added(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Pizza", "price_cents": 1000, "position": 0,
             "variants": [{"label": "Small", "price_cents": 800}]},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Pizza", "price_cents": 1000, "position": 0,
             "variants": [
                 {"label": "Small", "price_cents": 800},
                 {"label": "Large", "price_cents": 1500},
             ]},
        ])
        diff = compare_menu_versions(v1, v2)
        c = diff["changes"][0]
        assert c["status"] == "modified"
        assert len(c["variant_changes"]["added"]) == 1
        assert c["variant_changes"]["added"][0]["label"] == "Large"

    def test_variant_removed(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Pizza", "price_cents": 1000, "position": 0,
             "variants": [
                 {"label": "Small", "price_cents": 800},
                 {"label": "Large", "price_cents": 1500},
             ]},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Pizza", "price_cents": 1000, "position": 0,
             "variants": [{"label": "Small", "price_cents": 800}]},
        ])
        diff = compare_menu_versions(v1, v2)
        c = diff["changes"][0]
        assert c["status"] == "modified"
        assert len(c["variant_changes"]["removed"]) == 1
        assert c["variant_changes"]["removed"][0]["label"] == "Large"

    def test_variant_price_change(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Pizza", "price_cents": 1000, "position": 0,
             "variants": [{"label": "Small", "price_cents": 800}]},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Pizza", "price_cents": 1000, "position": 0,
             "variants": [{"label": "Small", "price_cents": 900}]},
        ])
        diff = compare_menu_versions(v1, v2)
        c = diff["changes"][0]
        assert c["status"] == "modified"
        vm = c["variant_changes"]["modified"]
        assert len(vm) == 1
        fc = vm[0]["field_changes"]
        assert any(f["field"] == "price_cents" and f["old"] == 800 and f["new"] == 900
                    for f in fc)

    def test_variant_kind_change(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Pizza", "price_cents": 1000, "position": 0,
             "variants": [{"label": "Extra Cheese", "price_cents": 200, "kind": "combo"}]},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Pizza", "price_cents": 1000, "position": 0,
             "variants": [{"label": "Extra Cheese", "price_cents": 200, "kind": "flavor"}]},
        ])
        diff = compare_menu_versions(v1, v2)
        vm = diff["changes"][0]["variant_changes"]["modified"]
        assert len(vm) == 1
        assert any(f["field"] == "kind" for f in vm[0]["field_changes"])

    def test_multiple_variant_changes(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Pizza", "price_cents": 1000, "position": 0,
             "variants": [
                 {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                 {"label": "Medium", "price_cents": 1200, "kind": "size", "position": 1},
                 {"label": "Large", "price_cents": 1500, "kind": "size", "position": 2},
             ]},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Pizza", "price_cents": 1000, "position": 0,
             "variants": [
                 {"label": "Small", "price_cents": 900, "kind": "size", "position": 0},  # modified (price)
                 # Medium removed
                 {"label": "Large", "price_cents": 1500, "kind": "size", "position": 2},  # unchanged (keep same position)
                 {"label": "XL", "price_cents": 2000, "kind": "size", "position": 3},  # added
             ]},
        ])
        diff = compare_menu_versions(v1, v2)
        vc = diff["changes"][0]["variant_changes"]
        assert len(vc["modified"]) == 1
        assert len(vc["removed"]) == 1
        assert len(vc["added"]) == 1
        assert len(vc["unchanged"]) == 1

    def test_item_unchanged_variant_added_equals_modified(self, fresh_db):
        """Item with no field changes but a new variant should be 'modified'."""
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Pizza", "price_cents": 1000, "category": "Entrees", "position": 0,
             "variants": []},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Pizza", "price_cents": 1000, "category": "Entrees", "position": 0,
             "variants": [{"label": "Small", "price_cents": 800}]},
        ])
        diff = compare_menu_versions(v1, v2)
        c = diff["changes"][0]
        assert c["status"] == "modified"
        assert c["field_changes"] == []
        assert len(c["variant_changes"]["added"]) == 1

    def test_variant_changes_on_modified_item(self, fresh_db):
        """Item with both field and variant changes."""
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Pizza", "price_cents": 1000, "position": 0,
             "variants": [{"label": "Small", "price_cents": 800}]},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Pizza", "price_cents": 1200, "position": 0,
             "variants": [{"label": "Small", "price_cents": 900}]},
        ])
        diff = compare_menu_versions(v1, v2)
        c = diff["changes"][0]
        assert c["status"] == "modified"
        assert len(c["field_changes"]) >= 1
        assert len(c["variant_changes"]["modified"]) == 1

    def test_no_variants_both_sides(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 1200, "position": 0},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Burger", "price_cents": 1200, "position": 0},
        ])
        diff = compare_menu_versions(v1, v2)
        vc = diff["changes"][0]["variant_changes"]
        assert vc["added"] == []
        assert vc["removed"] == []
        assert vc["modified"] == []
        assert vc["unchanged"] == []


# ===========================================================================
# Diff Engine — Edge Cases
# ===========================================================================
class TestDiffEdgeCases:

    def test_both_empty(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [])
        v2 = _create_version_with_items(fresh_db, mid, 2, [])
        diff = compare_menu_versions(v1, v2)
        assert diff["summary"]["added"] == 0
        assert diff["summary"]["removed"] == 0
        assert diff["changes"] == []

    def test_version_not_found(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [])
        result = compare_menu_versions(v1, 99999)
        assert result is None

    def test_different_menus(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid1 = _create_menu_raw(fresh_db, rid, name="Lunch")
        mid2 = _create_menu_raw(fresh_db, rid, name="Dinner")
        v1 = _create_version_with_items(fresh_db, mid1, 1, [
            {"name": "Burger", "price_cents": 1200, "position": 0},
        ])
        v2 = _create_version_with_items(fresh_db, mid2, 1, [
            {"name": "Burger", "price_cents": 1200, "position": 0},
        ])
        result = compare_menu_versions(v1, v2)
        assert result is None

    def test_duplicate_names_same_category(self, fresh_db):
        """Two items with same name and category — should pair correctly."""
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Special", "price_cents": 1000, "category": "Entrees", "position": 0},
            {"name": "Special", "price_cents": 1500, "category": "Entrees", "position": 1},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Special", "price_cents": 1100, "category": "Entrees", "position": 0},
            {"name": "Special", "price_cents": 1600, "category": "Entrees", "position": 1},
        ])
        diff = compare_menu_versions(v1, v2)
        # Both should be matched (modified), not added+removed
        assert diff["summary"]["modified"] == 2
        assert diff["summary"]["added"] == 0
        assert diff["summary"]["removed"] == 0

    def test_duplicate_names_different_categories(self, fresh_db):
        """Two 'Burger' items in different categories match by name+category."""
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 1000, "category": "Lunch", "position": 0},
            {"name": "Burger", "price_cents": 1500, "category": "Dinner", "position": 1},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Burger", "price_cents": 1100, "category": "Lunch", "position": 0},
            {"name": "Burger", "price_cents": 1600, "category": "Dinner", "position": 1},
        ])
        diff = compare_menu_versions(v1, v2)
        assert diff["summary"]["modified"] == 2
        assert diff["summary"]["added"] == 0

    def test_whitespace_normalization(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "  Burger  ", "price_cents": 1200, "position": 0},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Burger", "price_cents": 1200, "position": 0},
        ])
        diff = compare_menu_versions(v1, v2)
        # Should match (with name field change for the whitespace diff)
        assert diff["summary"]["removed"] == 0
        assert diff["summary"]["added"] == 0
        assert len(diff["changes"]) == 1

    def test_case_insensitive_matching(self, fresh_db):
        from storage.menus import compare_menu_versions
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "BURGER", "price_cents": 1200, "position": 0},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "burger", "price_cents": 1200, "position": 0},
        ])
        diff = compare_menu_versions(v1, v2)
        # Should match as modified (name case change), not removed+added
        assert diff["summary"]["removed"] == 0
        assert diff["summary"]["added"] == 0
        c = diff["changes"][0]
        assert c["status"] == "modified"
        assert any(fc["field"] == "name" for fc in c["field_changes"])


# ===========================================================================
# Compare Route Tests
# ===========================================================================
class TestCompareRoute:
    """Flask route: GET /menus/<id>/compare?a=&b="""

    def _setup_versions(self, conn):
        """Create restaurant + menu + two versions for route testing."""
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        v1 = _create_version_with_items(conn, mid, 1, [
            {"name": "Burger", "price_cents": 1200, "category": "Entrees", "position": 0},
            {"name": "Fries", "price_cents": 500, "category": "Sides", "position": 1},
        ])
        v2 = _create_version_with_items(conn, mid, 2, [
            {"name": "Burger", "price_cents": 1500, "category": "Entrees", "position": 0},
            {"name": "Salad", "price_cents": 700, "category": "Sides", "position": 1},
        ])
        return rid, mid, v1, v2

    def test_renders_200(self, client, fresh_db):
        rid, mid, v1, v2 = self._setup_versions(fresh_db)
        resp = client.get(f"/menus/{mid}/compare?a={v1}&b={v2}")
        assert resp.status_code == 200

    def test_shows_summary(self, client, fresh_db):
        rid, mid, v1, v2 = self._setup_versions(fresh_db)
        resp = client.get(f"/menus/{mid}/compare?a={v1}&b={v2}")
        html = resp.data.decode()
        # Burger modified (price), Fries removed, Salad added
        assert "modified" in html.lower()
        assert "added" in html.lower()
        assert "removed" in html.lower()

    def test_shows_added_item(self, client, fresh_db):
        rid, mid, v1, v2 = self._setup_versions(fresh_db)
        resp = client.get(f"/menus/{mid}/compare?a={v1}&b={v2}")
        html = resp.data.decode()
        assert "Salad" in html

    def test_shows_removed_item(self, client, fresh_db):
        rid, mid, v1, v2 = self._setup_versions(fresh_db)
        resp = client.get(f"/menus/{mid}/compare?a={v1}&b={v2}")
        html = resp.data.decode()
        assert "Fries" in html
        assert "diff-removed" in html

    def test_shows_modified_field(self, client, fresh_db):
        rid, mid, v1, v2 = self._setup_versions(fresh_db)
        resp = client.get(f"/menus/{mid}/compare?a={v1}&b={v2}")
        html = resp.data.decode()
        # Price change from $12.00 to $15.00
        assert "$12.00" in html
        assert "$15.00" in html

    def test_404_missing_version(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        v1 = _create_version_with_items(fresh_db, mid, 1, [])
        resp = client.get(f"/menus/{mid}/compare?a={v1}&b=99999")
        assert resp.status_code == 404

    def test_404_wrong_menu(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid1 = _create_menu_raw(fresh_db, rid, name="Lunch")
        mid2 = _create_menu_raw(fresh_db, rid, name="Dinner")
        v1 = _create_version_with_items(fresh_db, mid1, 1, [])
        v2 = _create_version_with_items(fresh_db, mid2, 1, [])
        # Versions belong to different menus → diff returns None → 404
        resp = client.get(f"/menus/{mid1}/compare?a={v1}&b={v2}")
        assert resp.status_code == 404

    def test_missing_params_redirect(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        # No a or b params
        resp = client.get(f"/menus/{mid}/compare")
        assert resp.status_code == 302
        assert f"/menus/{mid}/detail" in resp.headers.get("Location", "")


# ===========================================================================
# Compare UI on Menu Detail Page
# ===========================================================================
class TestCompareUI:
    """Compare form should appear on menu_detail.html when 2+ versions exist."""

    def test_compare_form_with_two_versions(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_with_items(fresh_db, mid, 1, [
            {"name": "A", "price_cents": 100, "position": 0},
        ])
        _create_version_with_items(fresh_db, mid, 2, [
            {"name": "B", "price_cents": 200, "position": 0},
        ])
        resp = client.get(f"/menus/{mid}/detail")
        html = resp.data.decode()
        assert "Compare Versions" in html
        assert f"/menus/{mid}/compare" in html

    def test_no_compare_form_single_version(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_with_items(fresh_db, mid, 1, [
            {"name": "A", "price_cents": 100, "position": 0},
        ])
        resp = client.get(f"/menus/{mid}/detail")
        html = resp.data.decode()
        assert "Compare Versions" not in html

    def test_no_compare_form_no_versions(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        resp = client.get(f"/menus/{mid}/detail")
        html = resp.data.decode()
        assert "Compare Versions" not in html

    def test_compare_form_has_selectors(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_with_items(fresh_db, mid, 1, [
            {"name": "A", "price_cents": 100, "position": 0},
        ])
        _create_version_with_items(fresh_db, mid, 2, [
            {"name": "B", "price_cents": 200, "position": 0},
        ])
        resp = client.get(f"/menus/{mid}/detail")
        html = resp.data.decode()
        assert 'name="a"' in html
        assert 'name="b"' in html
