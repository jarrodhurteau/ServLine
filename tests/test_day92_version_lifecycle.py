"""
Day 92 -- Version Lifecycle — Pin, Delete & Activity Log (Phase 10, Sprint 10.2).

Pin/unpin versions, delete versions with safety checks, menu activity log,
version stats, Flask routes for pin/unpin/delete/activity, template updates.

Covers:
  pin_menu_version():
  - pin a version returns True
  - pin already-pinned returns False
  - pinned flag persisted in DB
  - pinned visible in list_menu_versions

  unpin_menu_version():
  - unpin a pinned version returns True
  - unpin already-unpinned returns False
  - unpinned flag persisted in DB

  delete_menu_version():
  - delete a version returns info dict
  - delete removes version from DB
  - delete cascades items and variants
  - delete pinned version raises ValueError
  - delete sole version raises ValueError
  - delete nonexistent version returns None
  - delete middle version leaves others intact

  record_menu_activity():
  - records activity with all fields
  - invalid action falls back to version_published
  - returns activity row id

  list_menu_activity():
  - returns activities newest first
  - respects limit and offset
  - empty list for no activity

  get_version_stats():
  - returns correct total_versions
  - returns correct total_pinned
  - returns item_trend in oldest-first order
  - returns price increase/decrease totals
  - empty menu returns zero stats

  Flask pin/unpin route:
  - POST pin toggles pin on
  - POST pin toggles pin off (unpin)
  - pin redirects to menu_detail
  - flash message on pin
  - flash message on unpin
  - 404 for missing version

  Flask delete route:
  - POST delete removes version
  - flash message on delete
  - blocked delete shows error flash
  - 404 for missing version
  - redirects to menu_detail

  Flask activity route:
  - GET shows activity page
  - page contains activity table
  - 404 for missing menu

  Template updates:
  - pin button visible in version row
  - delete button visible in version row
  - delete button disabled when pinned
  - pin badge shown for pinned version
  - stats section shown when versions exist
  - activity section shown when activity exists
  - activity feed link in menu detail
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 89-91)
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

    # Phase 10 tables (Day 92: includes pinned column)
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

    # Day 92: menu_activity table
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

    conn.commit()
    return conn


def _test_db_connect():
    global _TEST_CONN
    if _TEST_CONN is None:
        _TEST_CONN = _make_test_db()
    return _TEST_CONN


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    """Redirect all DB access to in-memory test DB."""
    global _TEST_CONN
    _TEST_CONN = _make_test_db()
    monkeypatch.setattr("storage.drafts.db_connect", _test_db_connect)
    monkeypatch.setattr("storage.menus.db_connect", _test_db_connect)
    yield
    _TEST_CONN = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _seed_restaurant(name: str = "Test Diner") -> int:
    conn = _test_db_connect()
    cur = conn.execute(
        "INSERT INTO restaurants (name, created_at) VALUES (?, datetime('now'))",
        (name,),
    )
    conn.commit()
    return cur.lastrowid


def _seed_menu(restaurant_id: int, name: str = "Main Menu") -> int:
    conn = _test_db_connect()
    cur = conn.execute(
        "INSERT INTO menus (restaurant_id, name, active, created_at) "
        "VALUES (?, ?, 1, datetime('now'))",
        (restaurant_id, name),
    )
    conn.commit()
    return cur.lastrowid


def _seed_draft(restaurant_id: int, menu_id: int = None) -> int:
    conn = _test_db_connect()
    cur = conn.execute(
        "INSERT INTO drafts (title, restaurant_id, status, menu_id, created_at, updated_at) "
        "VALUES (?, ?, 'editing', ?, datetime('now'), datetime('now'))",
        ("Test Draft", restaurant_id, menu_id),
    )
    conn.commit()
    return cur.lastrowid


def _seed_draft_items(draft_id: int, items: list) -> list:
    conn = _test_db_connect()
    ids = []
    for i, it in enumerate(items):
        cur = conn.execute(
            "INSERT INTO draft_items (draft_id, name, description, price_cents, "
            "category, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (draft_id, it.get("name", "Item"), it.get("description"),
             it.get("price_cents", 0), it.get("category"), i),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def _seed_draft_variants(item_id: int, variants: list):
    conn = _test_db_connect()
    for i, v in enumerate(variants):
        conn.execute(
            "INSERT INTO draft_item_variants (item_id, label, price_cents, kind, "
            "position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            (item_id, v.get("label", ""), v.get("price_cents", 0),
             v.get("kind", "size"), i),
        )
    conn.commit()


def _create_version_with_items(menu_id, draft_id):
    """Helper to create a version from a draft."""
    from storage.menus import create_menu_version
    return create_menu_version(menu_id, source_draft_id=draft_id)


# ===========================================================================
# Tests: pin_menu_version
# ===========================================================================

class TestPinMenuVersion:
    def test_pin_returns_true(self):
        from storage.menus import pin_menu_version, create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        assert pin_menu_version(v["id"]) is True

    def test_pin_already_pinned_returns_false(self):
        from storage.menus import pin_menu_version, create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        pin_menu_version(v["id"])
        assert pin_menu_version(v["id"]) is False

    def test_pinned_persisted_in_db(self):
        from storage.menus import pin_menu_version, create_menu_version, get_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        pin_menu_version(v["id"])
        fetched = get_menu_version(v["id"], include_items=False)
        assert fetched["pinned"] == 1

    def test_pinned_visible_in_list(self):
        from storage.menus import pin_menu_version, create_menu_version, list_menu_versions
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        pin_menu_version(v["id"])
        versions = list_menu_versions(mid)
        assert versions[0]["pinned"] == 1


# ===========================================================================
# Tests: unpin_menu_version
# ===========================================================================

class TestUnpinMenuVersion:
    def test_unpin_returns_true(self):
        from storage.menus import pin_menu_version, unpin_menu_version, create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        pin_menu_version(v["id"])
        assert unpin_menu_version(v["id"]) is True

    def test_unpin_already_unpinned_returns_false(self):
        from storage.menus import unpin_menu_version, create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        assert unpin_menu_version(v["id"]) is False

    def test_unpinned_persisted_in_db(self):
        from storage.menus import pin_menu_version, unpin_menu_version, create_menu_version, get_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        pin_menu_version(v["id"])
        unpin_menu_version(v["id"])
        fetched = get_menu_version(v["id"], include_items=False)
        assert fetched["pinned"] == 0


# ===========================================================================
# Tests: delete_menu_version
# ===========================================================================

class TestDeleteMenuVersion:
    def test_delete_returns_info_dict(self):
        from storage.menus import delete_menu_version, create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        create_menu_version(mid, label="v1")
        v2 = create_menu_version(mid, label="v2")
        result = delete_menu_version(v2["id"])
        assert result is not None
        assert result["id"] == v2["id"]
        assert result["label"] == "v2"

    def test_delete_removes_from_db(self):
        from storage.menus import delete_menu_version, create_menu_version, get_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        create_menu_version(mid)
        v2 = create_menu_version(mid)
        delete_menu_version(v2["id"])
        assert get_menu_version(v2["id"]) is None

    def test_delete_cascades_items_and_variants(self):
        from storage.menus import delete_menu_version, create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        did = _seed_draft(rid, mid)
        _seed_draft_items(did, [
            {"name": "Burger", "price_cents": 999},
        ])
        create_menu_version(mid, source_draft_id=did)
        v2 = create_menu_version(mid, source_draft_id=did)
        # Verify items exist
        conn = _test_db_connect()
        items_before = conn.execute(
            "SELECT COUNT(*) AS cnt FROM menu_version_items WHERE version_id=?",
            (v2["id"],)
        ).fetchone()["cnt"]
        assert items_before > 0
        delete_menu_version(v2["id"])
        items_after = conn.execute(
            "SELECT COUNT(*) AS cnt FROM menu_version_items WHERE version_id=?",
            (v2["id"],)
        ).fetchone()["cnt"]
        assert items_after == 0

    def test_delete_pinned_raises_valueerror(self):
        from storage.menus import delete_menu_version, pin_menu_version, create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        create_menu_version(mid)
        v2 = create_menu_version(mid)
        pin_menu_version(v2["id"])
        with pytest.raises(ValueError, match="pinned"):
            delete_menu_version(v2["id"])

    def test_delete_sole_version_raises_valueerror(self):
        from storage.menus import delete_menu_version, create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        with pytest.raises(ValueError, match="only version"):
            delete_menu_version(v["id"])

    def test_delete_nonexistent_returns_none(self):
        from storage.menus import delete_menu_version
        assert delete_menu_version(99999) is None

    def test_delete_middle_leaves_others(self):
        from storage.menus import delete_menu_version, create_menu_version, list_menu_versions
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v1 = create_menu_version(mid, label="v1")
        v2 = create_menu_version(mid, label="v2")
        v3 = create_menu_version(mid, label="v3")
        delete_menu_version(v2["id"])
        remaining = list_menu_versions(mid)
        remaining_ids = {v["id"] for v in remaining}
        assert v1["id"] in remaining_ids
        assert v3["id"] in remaining_ids
        assert v2["id"] not in remaining_ids


# ===========================================================================
# Tests: record_menu_activity
# ===========================================================================

class TestRecordMenuActivity:
    def test_records_all_fields(self):
        from storage.menus import record_menu_activity, list_menu_activity, create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        aid = record_menu_activity(
            mid, "version_published",
            version_id=v["id"],
            detail="Published v1",
            actor="admin",
        )
        assert aid > 0
        activities = list_menu_activity(mid)
        assert len(activities) == 1
        a = activities[0]
        assert a["menu_id"] == mid
        assert a["version_id"] == v["id"]
        assert a["action"] == "version_published"
        assert a["detail"] == "Published v1"
        assert a["actor"] == "admin"

    def test_invalid_action_fallback(self):
        from storage.menus import record_menu_activity, list_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        record_menu_activity(mid, "bogus_action", detail="test")
        activities = list_menu_activity(mid)
        assert activities[0]["action"] == "version_published"

    def test_returns_row_id(self):
        from storage.menus import record_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        aid = record_menu_activity(mid, "version_pinned")
        assert isinstance(aid, int)
        assert aid > 0


# ===========================================================================
# Tests: list_menu_activity
# ===========================================================================

class TestListMenuActivity:
    def test_newest_first(self):
        from storage.menus import record_menu_activity, list_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        record_menu_activity(mid, "version_published", detail="first")
        record_menu_activity(mid, "version_pinned", detail="second")
        activities = list_menu_activity(mid)
        assert activities[0]["detail"] == "second"
        assert activities[1]["detail"] == "first"

    def test_limit_and_offset(self):
        from storage.menus import record_menu_activity, list_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        for i in range(5):
            record_menu_activity(mid, "version_published", detail=f"event_{i}")
        # limit=2
        batch = list_menu_activity(mid, limit=2)
        assert len(batch) == 2
        # offset=3
        batch2 = list_menu_activity(mid, limit=10, offset=3)
        assert len(batch2) == 2  # events 0,1 (oldest two)

    def test_empty_list(self):
        from storage.menus import list_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        assert list_menu_activity(mid) == []


# ===========================================================================
# Tests: get_version_stats
# ===========================================================================

class TestGetVersionStats:
    def test_total_versions(self):
        from storage.menus import get_version_stats, create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        create_menu_version(mid)
        create_menu_version(mid)
        stats = get_version_stats(mid)
        assert stats["total_versions"] == 2

    def test_total_pinned(self):
        from storage.menus import get_version_stats, create_menu_version, pin_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v1 = create_menu_version(mid)
        create_menu_version(mid)
        pin_menu_version(v1["id"])
        stats = get_version_stats(mid)
        assert stats["total_pinned"] == 1

    def test_item_trend_oldest_first(self):
        from storage.menus import get_version_stats, create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        did1 = _seed_draft(rid, mid)
        _seed_draft_items(did1, [{"name": "A"}])
        create_menu_version(mid, source_draft_id=did1)
        did2 = _seed_draft(rid, mid)
        _seed_draft_items(did2, [{"name": "A"}, {"name": "B"}])
        create_menu_version(mid, source_draft_id=did2)
        stats = get_version_stats(mid)
        trend = stats["item_trend"]
        assert len(trend) == 2
        assert trend[0]["version_number"] == 1
        assert trend[0]["item_count"] == 1
        assert trend[1]["version_number"] == 2
        assert trend[1]["item_count"] == 2

    def test_price_change_totals(self):
        from storage.menus import get_version_stats, create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        # v1 with item at 500
        did1 = _seed_draft(rid, mid)
        _seed_draft_items(did1, [{"name": "Burger", "price_cents": 500}])
        create_menu_version(mid, source_draft_id=did1)
        # v2 with item at 600 (price increase)
        did2 = _seed_draft(rid, mid)
        _seed_draft_items(did2, [{"name": "Burger", "price_cents": 600}])
        create_menu_version(mid, source_draft_id=did2)
        stats = get_version_stats(mid)
        assert stats["total_price_increases"] >= 1

    def test_empty_menu_zero_stats(self):
        from storage.menus import get_version_stats
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        stats = get_version_stats(mid)
        assert stats["total_versions"] == 0
        assert stats["total_pinned"] == 0
        assert stats["latest_version"] is None
        assert stats["item_trend"] == []


# ===========================================================================
# Tests: Flask Routes
# ===========================================================================

@pytest.fixture
def client():
    """Flask test client with session auth."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "portal"))
    from app import app as flask_app
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test"
    with flask_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = {"username": "admin", "role": "admin", "name": "Admin"}
        yield c


class TestFlaskPinRoute:
    def test_pin_toggles_on(self, client):
        from storage.menus import create_menu_version, get_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        resp = client.post(f"/menus/versions/{v['id']}/pin", follow_redirects=True)
        assert resp.status_code == 200
        fetched = get_menu_version(v["id"], include_items=False)
        assert fetched["pinned"] == 1

    def test_pin_toggles_off(self, client):
        from storage.menus import create_menu_version, pin_menu_version, get_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        pin_menu_version(v["id"])
        resp = client.post(f"/menus/versions/{v['id']}/pin", follow_redirects=True)
        assert resp.status_code == 200
        fetched = get_menu_version(v["id"], include_items=False)
        assert fetched["pinned"] == 0

    def test_pin_redirects(self, client):
        from storage.menus import create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        resp = client.post(f"/menus/versions/{v['id']}/pin")
        assert resp.status_code == 302

    def test_pin_flash_message(self, client):
        from storage.menus import create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        resp = client.post(f"/menus/versions/{v['id']}/pin", follow_redirects=True)
        assert b"Pinned" in resp.data

    def test_unpin_flash_message(self, client):
        from storage.menus import create_menu_version, pin_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        pin_menu_version(v["id"])
        resp = client.post(f"/menus/versions/{v['id']}/pin", follow_redirects=True)
        assert b"Unpinned" in resp.data

    def test_pin_404_missing_version(self, client):
        resp = client.post("/menus/versions/99999/pin")
        assert resp.status_code == 404


class TestFlaskDeleteRoute:
    def test_delete_removes_version(self, client):
        from storage.menus import create_menu_version, get_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        create_menu_version(mid)
        v2 = create_menu_version(mid)
        resp = client.post(f"/menus/versions/{v2['id']}/delete", follow_redirects=True)
        assert resp.status_code == 200
        assert get_menu_version(v2["id"]) is None

    def test_delete_flash_message(self, client):
        from storage.menus import create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        create_menu_version(mid)
        v2 = create_menu_version(mid)
        resp = client.post(f"/menus/versions/{v2['id']}/delete", follow_redirects=True)
        assert b"Deleted" in resp.data

    def test_delete_blocked_shows_error(self, client):
        from storage.menus import create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)  # only version
        resp = client.post(f"/menus/versions/{v['id']}/delete", follow_redirects=True)
        assert b"only version" in resp.data

    def test_delete_404_missing(self, client):
        resp = client.post("/menus/versions/99999/delete")
        assert resp.status_code == 404

    def test_delete_redirects(self, client):
        from storage.menus import create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        create_menu_version(mid)
        v2 = create_menu_version(mid)
        resp = client.post(f"/menus/versions/{v2['id']}/delete")
        assert resp.status_code == 302


class TestFlaskActivityRoute:
    def test_activity_page_renders(self, client):
        from storage.menus import record_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        record_menu_activity(mid, "version_published", detail="Test event")
        resp = client.get(f"/menus/{mid}/activity")
        assert resp.status_code == 200
        assert b"Activity Log" in resp.data
        assert b"Test event" in resp.data

    def test_activity_404_missing_menu(self, client):
        resp = client.get("/menus/99999/activity")
        assert resp.status_code == 404


# ===========================================================================
# Tests: Template Content
# ===========================================================================

class TestTemplateContent:
    def test_pin_button_visible(self, client):
        from storage.menus import create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        create_menu_version(mid)
        resp = client.get(f"/menus/{mid}/detail")
        assert b"/pin" in resp.data
        assert b"Pin" in resp.data

    def test_delete_button_visible(self, client):
        from storage.menus import create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        create_menu_version(mid)
        resp = client.get(f"/menus/{mid}/detail")
        assert b"/delete" in resp.data
        assert b"Delete" in resp.data

    def test_delete_button_disabled_when_pinned(self, client):
        from storage.menus import create_menu_version, pin_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        pin_menu_version(v["id"])
        resp = client.get(f"/menus/{mid}/detail")
        assert b"disabled" in resp.data
        assert b"Unpin before deleting" in resp.data

    def test_pin_badge_shown(self, client):
        from storage.menus import create_menu_version, pin_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        pin_menu_version(v["id"])
        resp = client.get(f"/menus/{mid}/detail")
        assert b"pin-badge" in resp.data

    def test_stats_section_shown(self, client):
        from storage.menus import create_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        create_menu_version(mid)
        resp = client.get(f"/menus/{mid}/detail")
        assert b"version" in resp.data

    def test_activity_section_shown(self, client):
        from storage.menus import record_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        record_menu_activity(mid, "version_published", detail="Pub event")
        resp = client.get(f"/menus/{mid}/detail")
        assert b"Recent Activity" in resp.data
        assert b"Pub event" in resp.data

    def test_activity_feed_link(self, client):
        from storage.menus import record_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        record_menu_activity(mid, "version_published", detail="event")
        resp = client.get(f"/menus/{mid}/detail")
        assert f"/menus/{mid}/activity".encode() in resp.data


# ===========================================================================
# Tests: Activity recorded by existing routes
# ===========================================================================

class TestActivityWiring:
    def test_pin_records_activity(self, client):
        from storage.menus import create_menu_version, list_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        client.post(f"/menus/versions/{v['id']}/pin", follow_redirects=True)
        activities = list_menu_activity(mid)
        assert any(a["action"] == "version_pinned" for a in activities)

    def test_unpin_records_activity(self, client):
        from storage.menus import create_menu_version, pin_menu_version, list_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        pin_menu_version(v["id"])
        client.post(f"/menus/versions/{v['id']}/pin", follow_redirects=True)
        activities = list_menu_activity(mid)
        assert any(a["action"] == "version_unpinned" for a in activities)

    def test_delete_records_activity(self, client):
        from storage.menus import create_menu_version, list_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        create_menu_version(mid)
        v2 = create_menu_version(mid)
        client.post(f"/menus/versions/{v2['id']}/delete", follow_redirects=True)
        activities = list_menu_activity(mid)
        assert any(a["action"] == "version_deleted" for a in activities)

    def test_edit_records_activity(self, client):
        from storage.menus import create_menu_version, list_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        client.post(
            f"/menus/versions/{v['id']}/edit",
            data={"label": "New Label"},
            follow_redirects=True,
        )
        activities = list_menu_activity(mid)
        assert any(a["action"] == "version_edited" for a in activities)

    def test_restore_records_activity(self, client):
        from storage.menus import create_menu_version, list_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        client.post(f"/menus/versions/{v['id']}/restore", follow_redirects=True)
        activities = list_menu_activity(mid)
        assert any(a["action"] == "version_restored" for a in activities)


# ===========================================================================
# Tests: Schema migration (pinned column)
# ===========================================================================

class TestSchemaMigration:
    def test_pinned_column_exists(self):
        conn = _test_db_connect()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(menu_versions)").fetchall()]
        assert "pinned" in cols

    def test_menu_activity_table_exists(self):
        conn = _test_db_connect()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "menu_activity" in tables

    def test_pinned_default_zero(self):
        from storage.menus import create_menu_version, get_menu_version
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        v = create_menu_version(mid)
        fetched = get_menu_version(v["id"], include_items=False)
        assert fetched["pinned"] == 0
