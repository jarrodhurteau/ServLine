"""
Day 88 -- Publish to Versioned Menu (Phase 10, Sprint 10.1).

Flask route tests for wiring publish to menu versions, menu detail page,
and version detail view.

Covers:
  Versioned Publish (publish_now with menu_id):
  - publish with menu_id creates menu version
  - version has correct item count
  - version has correct variant count
  - version tracks source_draft_id
  - draft status set to published after versioned publish
  - flash message includes version label
  - redirects to menu detail page
  - multiple publishes create incrementing versions (v1, v2, v3)

  Legacy Publish (publish_now without menu_id):
  - publish without menu_id still inserts into menu_items
  - legacy publish redirects to items page
  - legacy publish sets draft status to published

  Menu Detail Page:
  - menu detail page renders for valid menu
  - menu detail page shows menu name
  - menu detail page shows version list
  - menu detail page shows version item counts
  - menu detail page marks current version
  - menu detail page shows source draft links
  - menu detail page 404 for missing menu
  - menu detail page shows empty state when no versions

  Version Detail Page:
  - version detail page renders
  - version detail page shows item list
  - version detail page shows variant sub-rows
  - version detail page shows breadcrumb nav
  - version detail page shows item prices
  - version detail page shows item categories
  - version detail page 404 for missing version
  - version detail page shows version metadata (notes, created_at)

  Menus List Integration:
  - menus list links to detail page (not legacy items)

  Storage Integration:
  - create_menu_version called with correct args
  - get_menu_version returns items with variants
  - list_menu_versions returns newest first
  - get_current_version returns highest version number
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 87)
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


def _create_draft_with_items(conn, restaurant_id=None, menu_id=None,
                             item_count=3, add_variants=False) -> int:
    """Create a draft with items and optionally variants."""
    draft_id = _create_draft(conn, restaurant_id=restaurant_id, menu_id=menu_id)
    for i in range(item_count):
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO draft_items (draft_id, name, description, price_cents, "
            "category, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (draft_id, f"Item {i+1}", f"Desc {i+1}", (i + 1) * 500,
             "Entrees" if i % 2 == 0 else "Sides", i),
        )
        item_id = int(cur.lastrowid)
        if add_variants:
            for vi, (label, price) in enumerate([("Small", 500), ("Large", 800)]):
                conn.execute(
                    "INSERT INTO draft_item_variants (item_id, label, price_cents, "
                    "kind, position, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'size', ?, datetime('now'), datetime('now'))",
                    (item_id, label, price + i * 100, vi),
                )
    conn.commit()
    return draft_id


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
# Versioned Publish Tests (publish_now with menu_id)
# ===========================================================================
class TestVersionedPublish:
    def test_publish_with_menu_id_creates_version(self, client, fresh_db):
        """Publishing a draft assigned to a menu creates a menu version."""
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Dinner")
        did = _create_draft_with_items(fresh_db, restaurant_id=rid, menu_id=mid)
        resp = client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        assert resp.status_code == 200
        # Verify version was created
        row = fresh_db.execute(
            "SELECT * FROM menu_versions WHERE menu_id=?", (mid,)
        ).fetchone()
        assert row is not None
        assert row["version_number"] == 1

    def test_version_has_correct_item_count(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft_with_items(fresh_db, restaurant_id=rid, menu_id=mid, item_count=5)
        client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        row = fresh_db.execute(
            "SELECT item_count FROM menu_versions WHERE menu_id=?", (mid,)
        ).fetchone()
        assert row["item_count"] == 5

    def test_version_has_correct_variant_count(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft_with_items(
            fresh_db, restaurant_id=rid, menu_id=mid, item_count=2, add_variants=True
        )
        client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        row = fresh_db.execute(
            "SELECT variant_count FROM menu_versions WHERE menu_id=?", (mid,)
        ).fetchone()
        # 2 items × 2 variants each = 4
        assert row["variant_count"] == 4

    def test_version_tracks_source_draft_id(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft_with_items(fresh_db, restaurant_id=rid, menu_id=mid)
        client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        row = fresh_db.execute(
            "SELECT source_draft_id FROM menu_versions WHERE menu_id=?", (mid,)
        ).fetchone()
        assert row["source_draft_id"] == did

    def test_draft_status_set_to_published(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft_with_items(fresh_db, restaurant_id=rid, menu_id=mid)
        client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        draft = fresh_db.execute(
            "SELECT status FROM drafts WHERE id=?", (did,)
        ).fetchone()
        assert draft["status"] == "published"

    def test_flash_message_includes_version_label(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Dinner Menu")
        did = _create_draft_with_items(fresh_db, restaurant_id=rid, menu_id=mid)
        resp = client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        assert b"v1" in resp.data
        assert b"Dinner Menu" in resp.data

    def test_redirects_to_menu_detail(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft_with_items(fresh_db, restaurant_id=rid, menu_id=mid)
        resp = client.post(f"/drafts/{did}/publish_now")
        assert resp.status_code == 302
        assert f"/menus/{mid}/detail" in resp.headers["Location"]

    def test_multiple_publishes_increment_versions(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        # Publish 3 drafts to same menu
        for i in range(3):
            did = _create_draft_with_items(fresh_db, restaurant_id=rid, menu_id=mid)
            client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        versions = fresh_db.execute(
            "SELECT version_number FROM menu_versions WHERE menu_id=? ORDER BY version_number",
            (mid,),
        ).fetchall()
        assert [v["version_number"] for v in versions] == [1, 2, 3]

    def test_version_items_snapshot_correct(self, client, fresh_db):
        """Verify items are actually copied into menu_version_items."""
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft_with_items(fresh_db, restaurant_id=rid, menu_id=mid, item_count=3)
        client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        version = fresh_db.execute(
            "SELECT id FROM menu_versions WHERE menu_id=?", (mid,)
        ).fetchone()
        items = fresh_db.execute(
            "SELECT * FROM menu_version_items WHERE version_id=? ORDER BY position",
            (version["id"],),
        ).fetchall()
        assert len(items) == 3
        assert items[0]["name"] == "Item 1"
        assert items[1]["name"] == "Item 2"

    def test_version_variant_snapshot_correct(self, client, fresh_db):
        """Verify variants are copied into menu_version_item_variants."""
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft_with_items(
            fresh_db, restaurant_id=rid, menu_id=mid,
            item_count=1, add_variants=True,
        )
        client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        version = fresh_db.execute(
            "SELECT id FROM menu_versions WHERE menu_id=?", (mid,)
        ).fetchone()
        vi = fresh_db.execute(
            "SELECT id FROM menu_version_items WHERE version_id=?",
            (version["id"],),
        ).fetchone()
        variants = fresh_db.execute(
            "SELECT * FROM menu_version_item_variants WHERE item_id=? ORDER BY position",
            (vi["id"],),
        ).fetchall()
        assert len(variants) == 2
        assert variants[0]["label"] == "Small"
        assert variants[1]["label"] == "Large"

    def test_publish_nonexistent_menu_shows_error(self, client, fresh_db):
        """If assigned menu_id doesn't exist, show error."""
        rid = _create_restaurant(fresh_db)
        did = _create_draft(fresh_db, restaurant_id=rid, menu_id=9999)
        resp = client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        assert b"Assigned menu not found" in resp.data


# ===========================================================================
# Legacy Publish Tests (publish_now without menu_id)
# ===========================================================================
class TestLegacyPublish:
    def test_publish_without_menu_id_uses_legacy_path(self, client, fresh_db):
        """Without menu_id, publish still inserts into menu_items."""
        rid = _create_restaurant(fresh_db)
        did = _create_draft_with_items(fresh_db, restaurant_id=rid)
        resp = client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        assert resp.status_code == 200
        # Should NOT create a menu_version
        versions = fresh_db.execute("SELECT * FROM menu_versions").fetchall()
        assert len(versions) == 0
        # Should have menu_items
        items = fresh_db.execute("SELECT * FROM menu_items").fetchall()
        assert len(items) > 0

    def test_legacy_publish_redirects_to_items_page(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        did = _create_draft_with_items(fresh_db, restaurant_id=rid)
        resp = client.post(f"/drafts/{did}/publish_now")
        assert resp.status_code == 302
        assert "/menus/" in resp.headers["Location"]
        assert "/items" in resp.headers["Location"]

    def test_legacy_publish_sets_status_published(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        did = _create_draft_with_items(fresh_db, restaurant_id=rid)
        client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        draft = fresh_db.execute(
            "SELECT status FROM drafts WHERE id=?", (did,)
        ).fetchone()
        assert draft["status"] == "published"


# ===========================================================================
# Menu Detail Page Tests
# ===========================================================================
class TestMenuDetailPage:
    def test_menu_detail_renders(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Dinner")
        resp = client.get(f"/menus/{mid}/detail")
        assert resp.status_code == 200

    def test_menu_detail_shows_menu_name(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Brunch Special")
        resp = client.get(f"/menus/{mid}/detail")
        assert b"Brunch Special" in resp.data

    def test_menu_detail_shows_version_list(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_raw(fresh_db, mid, version_number=1, item_count=5)
        _create_version_raw(fresh_db, mid, version_number=2, item_count=8)
        resp = client.get(f"/menus/{mid}/detail")
        assert b"v1" in resp.data
        assert b"v2" in resp.data

    def test_menu_detail_shows_item_counts(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_raw(fresh_db, mid, version_number=1, item_count=12, variant_count=4)
        resp = client.get(f"/menus/{mid}/detail")
        html = resp.data.decode()
        assert "12" in html
        assert "4" in html

    def test_menu_detail_marks_current_version(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_raw(fresh_db, mid, version_number=1)
        _create_version_raw(fresh_db, mid, version_number=2)
        resp = client.get(f"/menus/{mid}/detail")
        assert b"(current)" in resp.data

    def test_menu_detail_shows_source_draft_link(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft(fresh_db, restaurant_id=rid)
        _create_version_raw(fresh_db, mid, version_number=1, source_draft_id=did)
        resp = client.get(f"/menus/{mid}/detail")
        assert f"#{did}".encode() in resp.data

    def test_menu_detail_404_for_missing_menu(self, client, fresh_db):
        resp = client.get("/menus/9999/detail")
        assert resp.status_code == 404

    def test_menu_detail_empty_state(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        resp = client.get(f"/menus/{mid}/detail")
        assert b"No versions yet" in resp.data

    def test_menu_detail_shows_menu_type(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Morning", menu_type="breakfast")
        resp = client.get(f"/menus/{mid}/detail")
        assert b"Breakfast" in resp.data

    def test_menu_detail_shows_description(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, description="Served daily 11am-3pm")
        resp = client.get(f"/menus/{mid}/detail")
        assert b"Served daily 11am-3pm" in resp.data

    def test_menu_detail_shows_notes(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        _create_version_raw(fresh_db, mid, version_number=1, notes="Initial publish")
        resp = client.get(f"/menus/{mid}/detail")
        assert b"Initial publish" in resp.data

    def test_menu_detail_breadcrumb_nav(self, client, fresh_db):
        rid = _create_restaurant(fresh_db, name="Pizzeria Roma")
        mid = _create_menu_raw(fresh_db, rid, name="Dinner")
        resp = client.get(f"/menus/{mid}/detail")
        assert b"Pizzeria Roma" in resp.data
        assert b"Restaurants" in resp.data


# ===========================================================================
# Version Detail Page Tests
# ===========================================================================
class TestVersionDetailPage:
    def _publish_and_get_version_id(self, client, fresh_db, **kwargs):
        """Helper: publish a draft and return the created version id."""
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft_with_items(
            fresh_db, restaurant_id=rid, menu_id=mid, **kwargs
        )
        client.post(f"/drafts/{did}/publish_now", follow_redirects=True)
        row = fresh_db.execute(
            "SELECT id FROM menu_versions WHERE menu_id=?", (mid,)
        ).fetchone()
        return row["id"], mid

    def test_version_detail_renders(self, client, fresh_db):
        vid, _ = self._publish_and_get_version_id(client, fresh_db)
        resp = client.get(f"/menus/versions/{vid}")
        assert resp.status_code == 200

    def test_version_detail_shows_items(self, client, fresh_db):
        vid, _ = self._publish_and_get_version_id(client, fresh_db, item_count=3)
        resp = client.get(f"/menus/versions/{vid}")
        assert b"Item 1" in resp.data
        assert b"Item 2" in resp.data
        assert b"Item 3" in resp.data

    def test_version_detail_shows_variant_subrows(self, client, fresh_db):
        vid, _ = self._publish_and_get_version_id(
            client, fresh_db, item_count=1, add_variants=True
        )
        resp = client.get(f"/menus/versions/{vid}")
        assert b"Small" in resp.data
        assert b"Large" in resp.data

    def test_version_detail_shows_breadcrumb(self, client, fresh_db):
        vid, mid = self._publish_and_get_version_id(client, fresh_db)
        resp = client.get(f"/menus/versions/{vid}")
        assert b"Restaurants" in resp.data
        # Link back to menu detail
        assert f"/menus/{mid}/detail".encode() in resp.data

    def test_version_detail_shows_prices(self, client, fresh_db):
        vid, _ = self._publish_and_get_version_id(client, fresh_db, item_count=2)
        resp = client.get(f"/menus/versions/{vid}")
        html = resp.data.decode()
        # Item 1 = 500 cents = $5.00, Item 2 = 1000 cents = $10.00
        assert "$5.00" in html
        assert "$10.00" in html

    def test_version_detail_shows_categories(self, client, fresh_db):
        vid, _ = self._publish_and_get_version_id(client, fresh_db, item_count=2)
        resp = client.get(f"/menus/versions/{vid}")
        assert b"Entrees" in resp.data
        assert b"Sides" in resp.data

    def test_version_detail_404_for_missing(self, client, fresh_db):
        resp = client.get("/menus/versions/9999")
        assert resp.status_code == 404

    def test_version_detail_shows_metadata(self, client, fresh_db):
        vid, _ = self._publish_and_get_version_id(client, fresh_db)
        resp = client.get(f"/menus/versions/{vid}")
        html = resp.data.decode()
        # Should show item/variant counts
        assert "items" in html.lower()

    def test_version_detail_empty_items(self, client, fresh_db):
        """Version with 0 items shows empty state."""
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        vid = _create_version_raw(fresh_db, mid, version_number=1, item_count=0)
        resp = client.get(f"/menus/versions/{vid}")
        assert b"no items" in resp.data.lower()


# ===========================================================================
# Menus List Integration
# ===========================================================================
class TestMenusListIntegration:
    def test_menus_list_links_to_detail(self, client, fresh_db):
        """Menu name in list links to /menus/<id>/detail (not /items)."""
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Test Menu")
        resp = client.get(f"/restaurants/{rid}/menus")
        html = resp.data.decode()
        assert f"/menus/{mid}/detail" in html

    def test_menus_list_does_not_link_to_legacy_items(self, client, fresh_db):
        """Menu name no longer links to /items."""
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Test Menu")
        resp = client.get(f"/restaurants/{rid}/menus")
        html = resp.data.decode()
        assert f"/menus/{mid}/items" not in html


# ===========================================================================
# Storage Integration Tests
# ===========================================================================
class TestStorageIntegration:
    def test_create_menu_version_with_draft(self, fresh_db):
        """create_menu_version snapshots items from draft."""
        import storage.menus as menus_mod
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft_with_items(
            fresh_db, restaurant_id=rid, menu_id=mid,
            item_count=4, add_variants=True,
        )
        version = menus_mod.create_menu_version(mid, source_draft_id=did)
        assert version["item_count"] == 4
        assert version["variant_count"] == 8  # 4 items × 2 variants
        assert version["version_number"] == 1
        assert version["source_draft_id"] == did

    def test_get_menu_version_returns_items_with_variants(self, fresh_db):
        import storage.menus as menus_mod
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft_with_items(
            fresh_db, restaurant_id=rid, menu_id=mid,
            item_count=2, add_variants=True,
        )
        version = menus_mod.create_menu_version(mid, source_draft_id=did)
        full = menus_mod.get_menu_version(version["id"], include_items=True)
        assert len(full["items"]) == 2
        assert len(full["items"][0]["variants"]) == 2
        assert full["items"][0]["variants"][0]["label"] == "Small"

    def test_list_menu_versions_newest_first(self, fresh_db):
        import storage.menus as menus_mod
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        for _ in range(3):
            did = _create_draft_with_items(fresh_db, restaurant_id=rid, menu_id=mid)
            menus_mod.create_menu_version(mid, source_draft_id=did)
        versions = menus_mod.list_menu_versions(mid)
        assert len(versions) == 3
        assert versions[0]["version_number"] == 3  # newest first
        assert versions[2]["version_number"] == 1

    def test_get_current_version_returns_highest(self, fresh_db):
        import storage.menus as menus_mod
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        for _ in range(3):
            did = _create_draft_with_items(fresh_db, restaurant_id=rid, menu_id=mid)
            menus_mod.create_menu_version(mid, source_draft_id=did)
        current = menus_mod.get_current_version(mid)
        assert current["version_number"] == 3

    def test_version_preserves_item_categories(self, fresh_db):
        import storage.menus as menus_mod
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft_with_items(fresh_db, restaurant_id=rid, menu_id=mid, item_count=2)
        version = menus_mod.create_menu_version(mid, source_draft_id=did)
        full = menus_mod.get_menu_version(version["id"], include_items=True)
        categories = [it["category"] for it in full["items"]]
        assert "Entrees" in categories
        assert "Sides" in categories

    def test_version_preserves_item_descriptions(self, fresh_db):
        import storage.menus as menus_mod
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        did = _create_draft_with_items(fresh_db, restaurant_id=rid, menu_id=mid, item_count=2)
        version = menus_mod.create_menu_version(mid, source_draft_id=did)
        full = menus_mod.get_menu_version(version["id"], include_items=True)
        descs = [it["description"] for it in full["items"]]
        assert "Desc 1" in descs
        assert "Desc 2" in descs

    def test_version_without_draft_creates_empty(self, fresh_db):
        """Creating version without source_draft_id creates empty version."""
        import storage.menus as menus_mod
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid)
        version = menus_mod.create_menu_version(mid)
        assert version["item_count"] == 0
        assert version["variant_count"] == 0
        full = menus_mod.get_menu_version(version["id"], include_items=True)
        assert full["items"] == []
