"""
Day 91 -- Version Change Summaries & Annotations (Phase 10, Sprint 10.2).

Auto-generated change summaries on publish, generate_change_summary(),
update_menu_version() for editing label/notes, Flask edit route,
session user capture in created_by, change_summary display in templates.

Covers:
  generate_change_summary():
  - empty diff returns "No changes"
  - all unchanged items returns "N items unchanged"
  - only added items returns "+N added"
  - only removed items returns "-N removed"
  - only modified items returns "~N modified"
  - mixed added+modified+removed returns all three parts
  - price increases counted in summary
  - price decreases counted in summary
  - mixed price ups and downs in summary
  - variant price changes included in counts
  - None diff returns empty string
  - single added singular form
  - single price increase singular form

  _auto_generate_change_summary():
  - first version returns None (no previous to diff)
  - second version returns summary string
  - summary stored in change_summary column

  Auto-summary on publish (create_menu_version):
  - first version has no change_summary
  - second version auto-generates change_summary
  - change_summary includes added items
  - change_summary includes modified prices
  - change_summary persisted in DB

  update_menu_version():
  - update label only
  - update notes only
  - update both label and notes
  - empty call returns False (no changes)
  - nonexistent version returns False
  - original label preserved when only notes updated
  - original notes preserved when only label updated

  Flask version edit route:
  - POST updates label and redirects
  - POST updates notes and redirects
  - flash message on success
  - 404 for missing version
  - no-change flash message

  Session user in created_by:
  - publish route captures session user email
  - publish route captures session user name when no email
  - created_by visible in version detail page
  - created_by visible in menu detail version table

  Template changes:
  - change_summary column visible in menu detail
  - "Initial version" shown for v1
  - edit button visible per version
  - version edit modal present in page
  - change_summary shown on version detail page
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 89-90)
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

    # Phase 10 tables (Day 91: includes change_summary column)
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
            change_summary  TEXT,
            pinned          INTEGER NOT NULL DEFAULT 0,
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_activity (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id     INTEGER NOT NULL,
            version_id  INTEGER,
            action      TEXT NOT NULL,
            detail      TEXT,
            actor       TEXT,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE,
            FOREIGN KEY (version_id) REFERENCES menu_versions(id) ON DELETE SET NULL
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


def _create_version_with_items(conn, menu_id, version_number=1,
                                items=None, label=None,
                                change_summary=None) -> int:
    """Create a version with directly inserted items + variants."""
    items = items or []
    variant_total = sum(len(it.get("variants", [])) for it in items)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO menu_versions (menu_id, version_number, label, source_draft_id, "
        "item_count, variant_count, notes, change_summary, created_at) "
        "VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, datetime('now'))",
        (menu_id, version_number, label or f"v{version_number}",
         len(items), variant_total, change_summary),
    )
    vid = int(cur.lastrowid)
    for it in items:
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


def _create_draft_with_items(conn, restaurant_id, items, menu_id=None):
    """Create a draft and insert items+variants, return draft_id."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO drafts (title, restaurant_id, status, source, "
        "menu_id, created_at, updated_at) "
        "VALUES (?, ?, 'editing', 'test', ?, datetime('now'), datetime('now'))",
        ("Test Draft", restaurant_id, menu_id),
    )
    draft_id = int(cur.lastrowid)
    for pos, it in enumerate(items):
        cur.execute(
            "INSERT INTO draft_items (draft_id, name, description, price_cents, "
            "category, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (draft_id, it["name"], it.get("description"), it.get("price_cents", 0),
             it.get("category"), pos),
        )
        item_id = int(cur.lastrowid)
        for vp, v in enumerate(it.get("variants", [])):
            cur.execute(
                "INSERT INTO draft_item_variants (item_id, label, price_cents, "
                "kind, position, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
                (item_id, v["label"], v.get("price_cents", 0),
                 v.get("kind", "size"), vp),
            )
    conn.commit()
    return draft_id


# ===========================================================================
# generate_change_summary() tests
# ===========================================================================
class TestGenerateChangeSummary:
    """Tests for generate_change_summary()."""

    def test_none_diff_returns_empty_string(self):
        from storage.menus import generate_change_summary
        assert generate_change_summary(None) == ""

    def test_no_changes_returns_no_changes(self):
        from storage.menus import generate_change_summary
        diff = {"summary": {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}, "changes": []}
        assert generate_change_summary(diff) == "No changes"

    def test_all_unchanged_returns_count(self):
        from storage.menus import generate_change_summary
        diff = {"summary": {"added": 0, "removed": 0, "modified": 0, "unchanged": 5}, "changes": []}
        assert generate_change_summary(diff) == "5 items unchanged"

    def test_only_added(self):
        from storage.menus import generate_change_summary
        diff = {"summary": {"added": 3, "removed": 0, "modified": 0, "unchanged": 0}, "changes": []}
        assert generate_change_summary(diff) == "+3 added"

    def test_only_removed(self):
        from storage.menus import generate_change_summary
        diff = {"summary": {"added": 0, "removed": 2, "modified": 0, "unchanged": 0}, "changes": []}
        assert generate_change_summary(diff) == "-2 removed"

    def test_only_modified(self):
        from storage.menus import generate_change_summary
        diff = {"summary": {"added": 0, "removed": 0, "modified": 4, "unchanged": 0}, "changes": []}
        assert generate_change_summary(diff) == "~4 modified"

    def test_mixed_changes(self):
        from storage.menus import generate_change_summary
        diff = {"summary": {"added": 2, "removed": 1, "modified": 3, "unchanged": 5}, "changes": []}
        result = generate_change_summary(diff)
        assert "+2 added" in result
        assert "~3 modified" in result
        assert "-1 removed" in result

    def test_price_increases_counted(self):
        from storage.menus import generate_change_summary
        diff = {
            "summary": {"added": 0, "removed": 0, "modified": 2, "unchanged": 0},
            "changes": [
                {"field_changes": [{"field": "price_cents", "price_direction": "increase"}],
                 "variant_changes": {"modified": []}},
                {"field_changes": [{"field": "price_cents", "price_direction": "increase"}],
                 "variant_changes": {"modified": []}},
            ],
        }
        result = generate_change_summary(diff)
        assert "2 price increases" in result

    def test_price_decreases_counted(self):
        from storage.menus import generate_change_summary
        diff = {
            "summary": {"added": 0, "removed": 0, "modified": 1, "unchanged": 0},
            "changes": [
                {"field_changes": [{"field": "price_cents", "price_direction": "decrease"}],
                 "variant_changes": {"modified": []}},
            ],
        }
        result = generate_change_summary(diff)
        assert "1 price decrease" in result

    def test_mixed_price_ups_and_downs(self):
        from storage.menus import generate_change_summary
        diff = {
            "summary": {"added": 0, "removed": 0, "modified": 2, "unchanged": 0},
            "changes": [
                {"field_changes": [{"field": "price_cents", "price_direction": "increase"}],
                 "variant_changes": {"modified": []}},
                {"field_changes": [{"field": "price_cents", "price_direction": "decrease"}],
                 "variant_changes": {"modified": []}},
            ],
        }
        result = generate_change_summary(diff)
        assert "1 price increase" in result
        assert "1 price decrease" in result

    def test_variant_price_changes_included(self):
        from storage.menus import generate_change_summary
        diff = {
            "summary": {"added": 0, "removed": 0, "modified": 1, "unchanged": 0},
            "changes": [
                {
                    "field_changes": [],
                    "variant_changes": {
                        "modified": [
                            {"field_changes": [{"field": "price_cents", "price_direction": "increase"}]},
                            {"field_changes": [{"field": "price_cents", "price_direction": "decrease"}]},
                        ],
                    },
                },
            ],
        }
        result = generate_change_summary(diff)
        assert "1 price increase" in result
        assert "1 price decrease" in result

    def test_single_added_form(self):
        from storage.menus import generate_change_summary
        diff = {"summary": {"added": 1, "removed": 0, "modified": 0, "unchanged": 0}, "changes": []}
        assert "+1 added" in generate_change_summary(diff)

    def test_single_price_increase_singular(self):
        from storage.menus import generate_change_summary
        diff = {
            "summary": {"added": 0, "removed": 0, "modified": 1, "unchanged": 0},
            "changes": [
                {"field_changes": [{"field": "price_cents", "price_direction": "increase"}],
                 "variant_changes": {"modified": []}},
            ],
        }
        result = generate_change_summary(diff)
        assert "1 price increase" in result
        assert "increases" not in result


# ===========================================================================
# _auto_generate_change_summary() tests
# ===========================================================================
class TestAutoGenerateChangeSummary:
    """Tests for _auto_generate_change_summary()."""

    def test_first_version_returns_none(self, fresh_db):
        from storage.menus import _auto_generate_change_summary
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        result = _auto_generate_change_summary(vid, mid)
        assert result is None

    def test_second_version_returns_summary(self, fresh_db):
        from storage.menus import _auto_generate_change_summary
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Burger", "price_cents": 999},
            {"name": "Fries", "price_cents": 499},
        ])
        result = _auto_generate_change_summary(v2, mid)
        assert result is not None
        assert "+1 added" in result

    def test_summary_stored_in_column(self, fresh_db):
        """Verify _auto_generate_change_summary produces a string that can be stored."""
        from storage.menus import _auto_generate_change_summary
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        v2 = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Burger", "price_cents": 1199},
        ])
        result = _auto_generate_change_summary(v2, mid)
        assert isinstance(result, str)
        assert len(result) > 0


# ===========================================================================
# Auto-summary on publish (create_menu_version) tests
# ===========================================================================
class TestAutoSummaryOnPublish:
    """Test that create_menu_version auto-generates change_summary."""

    def test_first_version_no_summary(self, fresh_db):
        from storage.menus import create_menu, create_menu_version
        rid = _create_restaurant(fresh_db)
        menu = create_menu(rid, "Dinner")
        draft_id = _create_draft_with_items(fresh_db, rid, [
            {"name": "Steak", "price_cents": 2500},
        ], menu_id=menu["id"])
        v = create_menu_version(menu["id"], source_draft_id=draft_id)
        assert v.get("change_summary") is None

    def test_second_version_auto_summary(self, fresh_db):
        from storage.menus import create_menu, create_menu_version
        rid = _create_restaurant(fresh_db)
        menu = create_menu(rid, "Dinner")
        d1 = _create_draft_with_items(fresh_db, rid, [
            {"name": "Steak", "price_cents": 2500},
        ], menu_id=menu["id"])
        create_menu_version(menu["id"], source_draft_id=d1)
        d2 = _create_draft_with_items(fresh_db, rid, [
            {"name": "Steak", "price_cents": 2500},
            {"name": "Salmon", "price_cents": 2200},
        ], menu_id=menu["id"])
        v2 = create_menu_version(menu["id"], source_draft_id=d2)
        assert v2.get("change_summary") is not None
        assert "+1 added" in v2["change_summary"]

    def test_summary_includes_modified_prices(self, fresh_db):
        from storage.menus import create_menu, create_menu_version
        rid = _create_restaurant(fresh_db)
        menu = create_menu(rid, "Dinner")
        d1 = _create_draft_with_items(fresh_db, rid, [
            {"name": "Steak", "price_cents": 2500},
        ], menu_id=menu["id"])
        create_menu_version(menu["id"], source_draft_id=d1)
        d2 = _create_draft_with_items(fresh_db, rid, [
            {"name": "Steak", "price_cents": 2800},
        ], menu_id=menu["id"])
        v2 = create_menu_version(menu["id"], source_draft_id=d2)
        assert "modified" in v2["change_summary"]
        assert "price increase" in v2["change_summary"]

    def test_summary_persisted_in_db(self, fresh_db):
        from storage.menus import create_menu, create_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        menu = create_menu(rid, "Dinner")
        d1 = _create_draft_with_items(fresh_db, rid, [
            {"name": "Steak", "price_cents": 2500},
        ], menu_id=menu["id"])
        create_menu_version(menu["id"], source_draft_id=d1)
        d2 = _create_draft_with_items(fresh_db, rid, [
            {"name": "Steak", "price_cents": 2500},
            {"name": "Fries", "price_cents": 499},
        ], menu_id=menu["id"])
        v2 = create_menu_version(menu["id"], source_draft_id=d2)
        # Re-read from DB
        fetched = get_menu_version(v2["id"], include_items=False)
        assert fetched["change_summary"] is not None
        assert "+1 added" in fetched["change_summary"]


# ===========================================================================
# update_menu_version() tests
# ===========================================================================
class TestUpdateMenuVersion:
    """Tests for update_menu_version()."""

    def test_update_label_only(self, fresh_db):
        from storage.menus import update_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        result = update_menu_version(vid, label="Spring 2026")
        assert result is True
        fetched = get_menu_version(vid, include_items=False)
        assert fetched["label"] == "Spring 2026"

    def test_update_notes_only(self, fresh_db):
        from storage.menus import update_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        result = update_menu_version(vid, notes="Updated pricing for spring")
        assert result is True
        fetched = get_menu_version(vid, include_items=False)
        assert fetched["notes"] == "Updated pricing for spring"

    def test_update_both_label_and_notes(self, fresh_db):
        from storage.menus import update_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        result = update_menu_version(vid, label="Summer Menu", notes="Seasonal items")
        assert result is True
        fetched = get_menu_version(vid, include_items=False)
        assert fetched["label"] == "Summer Menu"
        assert fetched["notes"] == "Seasonal items"

    def test_empty_call_returns_false(self, fresh_db):
        from storage.menus import update_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        result = update_menu_version(vid)
        assert result is False

    def test_nonexistent_version_returns_false(self, fresh_db):
        from storage.menus import update_menu_version
        result = update_menu_version(99999, label="Nope")
        assert result is False

    def test_label_preserved_when_only_notes_updated(self, fresh_db):
        from storage.menus import update_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ], label="Original Label")
        update_menu_version(vid, notes="New notes")
        fetched = get_menu_version(vid, include_items=False)
        assert fetched["label"] == "Original Label"

    def test_notes_preserved_when_only_label_updated(self, fresh_db):
        from storage.menus import update_menu_version, get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        # Set initial notes
        fresh_db.execute("UPDATE menu_versions SET notes='Original notes' WHERE id=?", (vid,))
        fresh_db.commit()
        update_menu_version(vid, label="New Label")
        fetched = get_menu_version(vid, include_items=False)
        assert fetched["notes"] == "Original notes"


# ===========================================================================
# Flask route tests
# ===========================================================================
@pytest.fixture
def client(fresh_db, monkeypatch):
    """Create Flask test client with DB patched."""
    import portal.app as app_mod
    app_mod.app.config["TESTING"] = True
    app_mod.app.config["SECRET_KEY"] = "test-secret"
    with app_mod.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = {"username": "admin", "role": "admin"}
        yield c


class TestVersionEditRoute:
    """Tests for POST /menus/versions/<id>/edit."""

    def test_post_updates_label_and_redirects(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        resp = client.post(
            f"/menus/versions/{vid}/edit",
            data={"label": "Updated Label", "notes": "New note"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert f"/menus/{mid}/detail" in resp.headers["Location"]

    def test_post_updates_notes(self, client, fresh_db):
        from storage.menus import get_menu_version
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        client.post(
            f"/menus/versions/{vid}/edit",
            data={"notes": "Edited notes"},
            follow_redirects=True,
        )
        fetched = get_menu_version(vid, include_items=False)
        assert fetched["notes"] == "Edited notes"

    def test_flash_message_on_success(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        resp = client.post(
            f"/menus/versions/{vid}/edit",
            data={"label": "New Label"},
            follow_redirects=True,
        )
        assert b"updated" in resp.data.lower()

    def test_404_for_missing_version(self, client, fresh_db):
        resp = client.post(
            "/menus/versions/99999/edit",
            data={"label": "Nope"},
        )
        assert resp.status_code == 404

    def test_no_change_flash(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        resp = client.post(
            f"/menus/versions/{vid}/edit",
            data={},
            follow_redirects=True,
        )
        assert b"No changes" in resp.data or b"no changes" in resp.data.lower()


# ===========================================================================
# Session user in created_by tests
# ===========================================================================
class TestCreatedByCapture:
    """Tests for session user capture in created_by field."""

    def test_publish_captures_session_user_email(self, client, fresh_db):
        from storage.menus import create_menu, get_menu_version, list_menu_versions
        rid = _create_restaurant(fresh_db)
        menu = create_menu(rid, "Dinner")
        draft_id = _create_draft_with_items(
            fresh_db, rid,
            [{"name": "Steak", "price_cents": 2500}],
            menu_id=menu["id"],
        )
        with client.session_transaction() as sess:
            sess["user"] = {"username": "admin", "role": "admin", "email": "chef@restaurant.com"}
        client.post(
            f"/drafts/{draft_id}/publish_now",
            data={"restaurant_id": str(rid)},
            follow_redirects=True,
        )
        versions = list_menu_versions(menu["id"])
        assert len(versions) >= 1
        v = versions[0]
        assert v["created_by"] == "chef@restaurant.com"

    def test_publish_captures_name_when_no_email(self, client, fresh_db):
        from storage.menus import create_menu, list_menu_versions
        rid = _create_restaurant(fresh_db)
        menu = create_menu(rid, "Dinner")
        draft_id = _create_draft_with_items(
            fresh_db, rid,
            [{"name": "Steak", "price_cents": 2500}],
            menu_id=menu["id"],
        )
        with client.session_transaction() as sess:
            sess["user"] = {"username": "admin", "role": "admin", "name": "Chef Bob"}
        client.post(
            f"/drafts/{draft_id}/publish_now",
            data={"restaurant_id": str(rid)},
            follow_redirects=True,
        )
        versions = list_menu_versions(menu["id"])
        assert len(versions) >= 1
        v = versions[0]
        assert v["created_by"] == "Chef Bob"


# ===========================================================================
# Template display tests
# ===========================================================================
class TestTemplateChanges:
    """Tests for change_summary and edit button display in templates."""

    def test_change_summary_visible_in_menu_detail(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Burger", "price_cents": 999},
            {"name": "Fries", "price_cents": 499},
        ], change_summary="+1 added")
        resp = client.get(f"/menus/{mid}/detail")
        assert resp.status_code == 200
        assert b"+1 added" in resp.data

    def test_initial_version_shows_initial_text(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        resp = client.get(f"/menus/{mid}/detail")
        assert resp.status_code == 200
        assert b"Initial version" in resp.data

    def test_edit_button_visible_per_version(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        resp = client.get(f"/menus/{mid}/detail")
        assert resp.status_code == 200
        assert b"Edit" in resp.data
        assert b"openVersionEditModal" in resp.data

    def test_version_edit_modal_present(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        resp = client.get(f"/menus/{mid}/detail")
        assert resp.status_code == 200
        assert b"versionEditModal" in resp.data
        assert b"versionEditForm" in resp.data

    def test_change_summary_on_version_detail(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_with_items(fresh_db, mid, 2, [
            {"name": "Burger", "price_cents": 999},
        ], change_summary="+1 added, ~1 modified")
        # Need v1 first for version_number ordering, but we can directly set v2
        resp = client.get(f"/menus/versions/{vid}")
        assert resp.status_code == 200
        assert b"+1 added" in resp.data
        assert b"Changes:" in resp.data

    def test_created_by_visible_in_menu_detail(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        fresh_db.execute(
            "INSERT INTO menu_versions (menu_id, version_number, label, "
            "item_count, variant_count, created_by, created_at) "
            "VALUES (?, 1, 'v1', 0, 0, 'chef@restaurant.com', datetime('now'))",
            (mid,),
        )
        fresh_db.commit()
        resp = client.get(f"/menus/{mid}/detail")
        assert resp.status_code == 200
        assert b"chef@restaurant.com" in resp.data

    def test_created_by_visible_in_version_detail(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        cur = fresh_db.cursor()
        cur.execute(
            "INSERT INTO menu_versions (menu_id, version_number, label, "
            "item_count, variant_count, created_by, created_at) "
            "VALUES (?, 1, 'v1', 0, 0, 'chef@restaurant.com', datetime('now'))",
            (mid,),
        )
        vid = int(cur.lastrowid)
        fresh_db.commit()
        resp = client.get(f"/menus/versions/{vid}")
        assert resp.status_code == 200
        assert b"chef@restaurant.com" in resp.data

    def test_published_by_column_header(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        resp = client.get(f"/menus/{mid}/detail")
        assert resp.status_code == 200
        assert b"Published By" in resp.data

    def test_changes_column_header(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_with_items(fresh_db, mid, 1, [
            {"name": "Burger", "price_cents": 999},
        ])
        resp = client.get(f"/menus/{mid}/detail")
        assert resp.status_code == 200
        assert b"Changes" in resp.data
