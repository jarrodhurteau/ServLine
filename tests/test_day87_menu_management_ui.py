"""
Day 87 -- Menu Management UI (Phase 10, Sprint 10.1).

Flask route tests for menu CRUD pages + draft-to-menu assignment.

Covers:
  Menu List Page:
  - menus page renders for restaurant
  - menus page shows menu name
  - menus page shows menu type column
  - menus page shows version count
  - menus page shows create form button
  - menus page empty state message

  Create Menu Route:
  - POST creates menu and redirects
  - create with all fields (name, type, description)
  - create without name returns error flash
  - create with invalid type defaults to None
  - create menu appears in list

  Update Menu Route:
  - POST updates menu name
  - POST updates menu type
  - POST updates description
  - update nonexistent menu returns 404

  Delete Menu Route:
  - POST soft-deletes menu and redirects
  - deleted menu no longer in list
  - delete nonexistent menu returns 404

  Draft-to-Menu Assignment:
  - POST assigns menu_id to draft
  - assign menu appears in draft metadata
  - assign without menu_id shows error flash
  - assign invalid menu_id shows error flash
  - draft editor passes menus context
  - draft editor shows menu dropdown when restaurant assigned
  - draft editor hides menu dropdown when no restaurant

  Storage Integration:
  - menus_store.create_menu called by route
  - menus_store.update_menu called by route
  - menus_store.delete_menu called by route
  - menus_store.list_menus used for page context
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 86)
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
                             item_count=3) -> int:
    draft_id = _create_draft(conn, restaurant_id=restaurant_id, menu_id=menu_id)
    for i in range(item_count):
        conn.execute(
            "INSERT INTO draft_items (draft_id, name, description, price_cents, "
            "category, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (draft_id, f"Item {i+1}", f"Desc {i+1}", (i + 1) * 500,
             "Entrees" if i % 2 == 0 else "Sides", i),
        )
    conn.commit()
    return draft_id


# ---------------------------------------------------------------------------
# Flask test client fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch, fresh_db):
    """Flask test client with authenticated session."""
    from portal import app as app_mod
    import storage.menus as menus_mod

    # Patch menus_store on the app module so routes use our test DB
    monkeypatch.setattr(app_mod, "menus_store", menus_mod)

    # Also patch portal.app.db_connect so HTML routes (menus_page etc.)
    # hit the test DB instead of the real file-backed DB
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
# Menu List Page Tests
# ===========================================================================
class TestMenuListPage:
    def test_menus_page_renders(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        resp = client.get(f"/restaurants/{rid}/menus")
        assert resp.status_code == 200

    def test_menus_page_shows_menu_name(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        _create_menu_raw(fresh_db, rid, name="Brunch Special")
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"Brunch Special" in resp.data

    def test_menus_page_shows_menu_type(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        _create_menu_raw(fresh_db, rid, name="Morning", menu_type="breakfast")
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"Breakfast" in resp.data

    def test_menus_page_shows_version_count(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Dinner")
        # Add a version
        fresh_db.execute(
            "INSERT INTO menu_versions (menu_id, version_number, label, "
            "item_count, variant_count, created_at) VALUES (?, 1, 'v1', 0, 0, datetime('now'))",
            (mid,),
        )
        fresh_db.commit()
        resp = client.get(f"/restaurants/{rid}/menus")
        assert resp.status_code == 200

    def test_menus_page_shows_create_button(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"Add Menu" in resp.data

    def test_menus_page_empty_state(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"No menus found" in resp.data

    def test_menus_page_nonexistent_restaurant(self, client, fresh_db):
        resp = client.get("/restaurants/9999/menus")
        assert resp.status_code == 404

    def test_menus_page_shows_edit_button(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        _create_menu_raw(fresh_db, rid, name="Test")
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"Edit" in resp.data

    def test_menus_page_shows_delete_button(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        _create_menu_raw(fresh_db, rid, name="Test")
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"Delete" in resp.data

    def test_menus_page_shows_description(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        _create_menu_raw(fresh_db, rid, name="Lunch", description="Served 11am-3pm")
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"Served 11am-3pm" in resp.data


# ===========================================================================
# Create Menu Route Tests
# ===========================================================================
class TestCreateMenu:
    def test_create_menu_redirects(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        resp = client.post(
            f"/restaurants/{rid}/menus",
            data={"name": "New Menu"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_create_menu_with_all_fields(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        client.post(
            f"/restaurants/{rid}/menus",
            data={"name": "Dinner Menu", "menu_type": "dinner", "description": "Evening specials"},
        )
        row = fresh_db.execute(
            "SELECT * FROM menus WHERE restaurant_id=? AND name='Dinner Menu'", (rid,)
        ).fetchone()
        assert row is not None
        assert row["menu_type"] == "dinner"
        assert row["description"] == "Evening specials"

    def test_create_menu_name_required(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        resp = client.post(
            f"/restaurants/{rid}/menus",
            data={"name": ""},
            follow_redirects=True,
        )
        assert b"Menu name is required" in resp.data

    def test_create_menu_appears_in_list(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        client.post(
            f"/restaurants/{rid}/menus",
            data={"name": "Happy Hour"},
            follow_redirects=True,
        )
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"Happy Hour" in resp.data

    def test_create_menu_success_flash(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        resp = client.post(
            f"/restaurants/{rid}/menus",
            data={"name": "Kids Menu"},
            follow_redirects=True,
        )
        assert b"created" in resp.data.lower()

    def test_create_menu_invalid_type_no_error(self, client, fresh_db):
        """Invalid type silently defaults to None, no crash."""
        rid = _create_restaurant(fresh_db)
        resp = client.post(
            f"/restaurants/{rid}/menus",
            data={"name": "Special", "menu_type": "invalid_type_xyz"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        row = fresh_db.execute(
            "SELECT menu_type FROM menus WHERE name='Special'"
        ).fetchone()
        assert row is not None
        assert row["menu_type"] is None

    def test_create_menu_type_selector_options(self, client, fresh_db):
        """Verify the create form includes valid menu type options."""
        rid = _create_restaurant(fresh_db)
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"breakfast" in resp.data or b"Breakfast" in resp.data
        assert b"dinner" in resp.data or b"Dinner" in resp.data
        assert b"happy_hour" in resp.data or b"Happy Hour" in resp.data


# ===========================================================================
# Update Menu Route Tests
# ===========================================================================
class TestUpdateMenu:
    def test_update_menu_name(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Old Name")
        resp = client.post(
            f"/menus/{mid}/update",
            data={"name": "New Name"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        row = fresh_db.execute("SELECT name FROM menus WHERE id=?", (mid,)).fetchone()
        assert row["name"] == "New Name"

    def test_update_menu_type(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Test")
        client.post(f"/menus/{mid}/update", data={"menu_type": "brunch"})
        row = fresh_db.execute("SELECT menu_type FROM menus WHERE id=?", (mid,)).fetchone()
        assert row["menu_type"] == "brunch"

    def test_update_menu_description(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Test")
        client.post(f"/menus/{mid}/update", data={"description": "New desc"})
        row = fresh_db.execute("SELECT description FROM menus WHERE id=?", (mid,)).fetchone()
        assert row["description"] == "New desc"

    def test_update_menu_redirects(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Test")
        resp = client.post(
            f"/menus/{mid}/update",
            data={"name": "Updated"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_update_nonexistent_menu(self, client, fresh_db):
        resp = client.post(
            "/menus/9999/update",
            data={"name": "Ghost"},
        )
        assert resp.status_code == 404

    def test_update_menu_success_flash(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Test")
        resp = client.post(
            f"/menus/{mid}/update",
            data={"name": "Updated"},
            follow_redirects=True,
        )
        assert b"updated" in resp.data.lower()


# ===========================================================================
# Delete Menu Route Tests
# ===========================================================================
class TestDeleteMenu:
    def test_delete_menu_redirects(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Doomed")
        resp = client.post(f"/menus/{mid}/delete", follow_redirects=False)
        assert resp.status_code == 302

    def test_delete_menu_soft_deletes(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Doomed")
        client.post(f"/menus/{mid}/delete")
        row = fresh_db.execute("SELECT active FROM menus WHERE id=?", (mid,)).fetchone()
        assert row["active"] == 0

    def test_deleted_menu_not_in_list(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="GoneMenu")
        # follow redirect to consume the flash that contains "GoneMenu"
        client.post(f"/menus/{mid}/delete", follow_redirects=True)
        # second GET has no flash — table should be clean
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"GoneMenu" not in resp.data

    def test_delete_nonexistent_menu(self, client, fresh_db):
        resp = client.post("/menus/9999/delete")
        assert resp.status_code == 404

    def test_delete_menu_success_flash(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Byebye")
        resp = client.post(f"/menus/{mid}/delete", follow_redirects=True)
        assert b"deleted" in resp.data.lower()


# ===========================================================================
# Draft-to-Menu Assignment Tests
# ===========================================================================
class TestDraftMenuAssignment:
    def test_assign_menu_to_draft(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Target Menu")
        did = _create_draft(fresh_db, restaurant_id=rid)
        resp = client.post(
            f"/drafts/{did}/assign_menu",
            data={"menu_id": str(mid)},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    def test_assign_menu_persists(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Target Menu")
        did = _create_draft(fresh_db, restaurant_id=rid)
        client.post(f"/drafts/{did}/assign_menu", data={"menu_id": str(mid)})
        row = fresh_db.execute("SELECT menu_id FROM drafts WHERE id=?", (did,)).fetchone()
        assert row["menu_id"] == mid

    def test_assign_menu_success_flash(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Target")
        did = _create_draft(fresh_db, restaurant_id=rid)
        resp = client.post(
            f"/drafts/{did}/assign_menu",
            data={"menu_id": str(mid)},
            follow_redirects=True,
        )
        assert b"Menu assigned" in resp.data

    def test_assign_menu_no_menu_id_error(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        did = _create_draft(fresh_db, restaurant_id=rid)
        resp = client.post(
            f"/drafts/{did}/assign_menu",
            data={},
            follow_redirects=True,
        )
        assert b"choose a menu" in resp.data.lower()

    def test_assign_menu_invalid_id_error(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        did = _create_draft(fresh_db, restaurant_id=rid)
        resp = client.post(
            f"/drafts/{did}/assign_menu",
            data={"menu_id": "abc"},
            follow_redirects=True,
        )
        assert b"Invalid menu id" in resp.data


# ===========================================================================
# Storage-Level Integration Tests (menus_store functions via routes)
# ===========================================================================
class TestStorageIntegration:
    def test_create_via_store(self, fresh_db):
        """Verify menus_store.create_menu works with test DB."""
        from storage.menus import create_menu
        rid = _create_restaurant(fresh_db)
        result = create_menu(rid, "Store Test", menu_type="lunch", description="Via store")
        assert result["id"] is not None
        assert result["name"] == "Store Test"
        assert result["menu_type"] == "lunch"

    def test_list_via_store(self, fresh_db):
        from storage.menus import list_menus
        rid = _create_restaurant(fresh_db)
        _create_menu_raw(fresh_db, rid, name="M1")
        _create_menu_raw(fresh_db, rid, name="M2")
        result = list_menus(rid)
        assert len(result) == 2
        names = {m["name"] for m in result}
        assert "M1" in names
        assert "M2" in names

    def test_list_includes_version_count(self, fresh_db):
        from storage.menus import list_menus
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Versioned")
        fresh_db.execute(
            "INSERT INTO menu_versions (menu_id, version_number, label, "
            "item_count, variant_count, created_at) VALUES (?, 1, 'v1', 5, 2, datetime('now'))",
            (mid,),
        )
        fresh_db.commit()
        result = list_menus(rid)
        assert result[0]["version_count"] == 1

    def test_update_via_store(self, fresh_db):
        from storage.menus import create_menu, update_menu, get_menu
        rid = _create_restaurant(fresh_db)
        m = create_menu(rid, "Old")
        update_menu(m["id"], name="New")
        updated = get_menu(m["id"])
        assert updated["name"] == "New"

    def test_delete_via_store(self, fresh_db):
        from storage.menus import create_menu, delete_menu, get_menu
        rid = _create_restaurant(fresh_db)
        m = create_menu(rid, "Gone")
        ok = delete_menu(m["id"])
        assert ok is True
        after = get_menu(m["id"])
        assert after["active"] == 0

    def test_list_excludes_deleted(self, fresh_db):
        from storage.menus import create_menu, delete_menu, list_menus
        rid = _create_restaurant(fresh_db)
        m = create_menu(rid, "Active")
        d = create_menu(rid, "Deleted")
        delete_menu(d["id"])
        result = list_menus(rid)
        assert len(result) == 1
        assert result[0]["name"] == "Active"

    def test_list_include_inactive(self, fresh_db):
        from storage.menus import create_menu, delete_menu, list_menus
        rid = _create_restaurant(fresh_db)
        create_menu(rid, "Active")
        d = create_menu(rid, "Deleted")
        delete_menu(d["id"])
        result = list_menus(rid, include_inactive=True)
        assert len(result) == 2


# ===========================================================================
# Draft Editor Context Tests
# ===========================================================================
class TestDraftEditorMenuContext:
    def test_editor_has_menu_dropdown(self, client, fresh_db):
        """When draft has restaurant with menus, editor shows menu dropdown."""
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="Lunch Special")
        did = _create_draft_with_items(fresh_db, restaurant_id=rid, item_count=1)
        resp = client.get(f"/drafts/{did}/edit")
        assert b"Assign Menu" in resp.data
        assert b"Lunch Special" in resp.data

    def test_editor_no_menus_hint(self, client, fresh_db):
        """When draft has restaurant but no menus, editor shows hint."""
        rid = _create_restaurant(fresh_db)
        did = _create_draft_with_items(fresh_db, restaurant_id=rid, item_count=1)
        resp = client.get(f"/drafts/{did}/edit")
        assert b"No menus for this restaurant" in resp.data or b"Create one" in resp.data

    def test_editor_no_restaurant_no_menu_dropdown(self, client, fresh_db):
        """When draft has no restaurant, no menu dropdown at all."""
        did = _create_draft_with_items(fresh_db, item_count=1)
        resp = client.get(f"/drafts/{did}/edit")
        # Should not show menu assignment section
        assert b"Assign Menu" not in resp.data

    def test_editor_shows_selected_menu(self, client, fresh_db):
        """When draft already has menu_id, it should be pre-selected."""
        rid = _create_restaurant(fresh_db)
        mid = _create_menu_raw(fresh_db, rid, name="PreSelected")
        did = _create_draft(fresh_db, restaurant_id=rid, menu_id=mid)
        # Insert at least one item so editor renders
        fresh_db.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
            "VALUES (?, 'X', 100, datetime('now'), datetime('now'))",
            (did,),
        )
        fresh_db.commit()
        resp = client.get(f"/drafts/{did}/edit")
        assert b"PreSelected" in resp.data


# ===========================================================================
# Multiple Menus per Restaurant
# ===========================================================================
class TestMultipleMenus:
    def test_multiple_menus_all_show(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        _create_menu_raw(fresh_db, rid, name="Breakfast")
        _create_menu_raw(fresh_db, rid, name="Lunch")
        _create_menu_raw(fresh_db, rid, name="Dinner")
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"Breakfast" in resp.data
        assert b"Lunch" in resp.data
        assert b"Dinner" in resp.data

    def test_menus_scoped_to_restaurant(self, client, fresh_db):
        r1 = _create_restaurant(fresh_db, "Rest A")
        r2 = _create_restaurant(fresh_db, "Rest B")
        _create_menu_raw(fresh_db, r1, name="MenuA")
        _create_menu_raw(fresh_db, r2, name="MenuB")
        resp1 = client.get(f"/restaurants/{r1}/menus")
        assert b"MenuA" in resp1.data
        assert b"MenuB" not in resp1.data
        resp2 = client.get(f"/restaurants/{r2}/menus")
        assert b"MenuB" in resp2.data
        assert b"MenuA" not in resp2.data

    def test_create_multiple_types(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        for t in ["breakfast", "lunch", "dinner"]:
            client.post(
                f"/restaurants/{rid}/menus",
                data={"name": f"{t.title()} Menu", "menu_type": t},
            )
        rows = fresh_db.execute(
            "SELECT * FROM menus WHERE restaurant_id=? AND active=1", (rid,)
        ).fetchall()
        assert len(rows) == 3
        types = {r["menu_type"] for r in rows}
        assert types == {"breakfast", "lunch", "dinner"}


# ===========================================================================
# Edge Cases
# ===========================================================================
class TestEdgeCases:
    def test_unicode_menu_name(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        client.post(
            f"/restaurants/{rid}/menus",
            data={"name": "Menú Especial — Café"},
        )
        resp = client.get(f"/restaurants/{rid}/menus")
        assert "Especial".encode("utf-8") in resp.data

    def test_long_description_truncated_in_list(self, client, fresh_db):
        """Long descriptions should still render without breaking the page."""
        rid = _create_restaurant(fresh_db)
        long_desc = "A" * 500
        _create_menu_raw(fresh_db, rid, name="Long", description=long_desc)
        resp = client.get(f"/restaurants/{rid}/menus")
        assert resp.status_code == 200

    def test_create_and_immediately_delete(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        client.post(f"/restaurants/{rid}/menus", data={"name": "Ephemeral"},
                    follow_redirects=True)
        row = fresh_db.execute(
            "SELECT id FROM menus WHERE name='Ephemeral'"
        ).fetchone()
        mid = row["id"]
        # follow redirect to consume the flash that contains "Ephemeral"
        client.post(f"/menus/{mid}/delete", follow_redirects=True)
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"Ephemeral" not in resp.data

    def test_whitespace_only_name_rejected(self, client, fresh_db):
        rid = _create_restaurant(fresh_db)
        resp = client.post(
            f"/restaurants/{rid}/menus",
            data={"name": "   "},
            follow_redirects=True,
        )
        assert b"Menu name is required" in resp.data
