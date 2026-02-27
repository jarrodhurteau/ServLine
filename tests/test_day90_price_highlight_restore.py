"""
Day 90 -- Price Change Highlighting & Restore from Version (Phase 10, Sprint 10.2).

Price highlighting in diff engine and compare template;
restore_version_to_draft() in storage/menus.py; Flask restore route.

Covers:
  Price Direction — Item Diffs:
  - price increase detected with direction "increase"
  - price decrease detected with direction "decrease"
  - price unchanged has no price_direction key
  - price added (None to value) direction is increase
  - price removed (value to None) direction is decrease
  - multiple field changes include price_direction only on price
  - zero to positive is increase
  - positive to zero is decrease

  Price Direction — Variant Diffs:
  - variant price increase detected
  - variant price decrease detected
  - variant price unchanged has no direction
  - variant non-price field change has no direction

  Restore to Draft — Storage:
  - restore creates draft with correct title
  - restore copies restaurant_id from menu
  - restore sets menu_id on draft
  - restore sets status editing
  - restore sets source version_restore
  - restore copies all items
  - restore copies item names correctly
  - restore copies item descriptions
  - restore copies item prices
  - restore copies item categories
  - restore copies item positions
  - restore copies variants
  - restore copies variant labels
  - restore copies variant prices
  - restore copies variant kinds
  - restore copies variant positions
  - restore from version with no items creates empty draft
  - restore nonexistent version returns None
  - return dict has correct item_count
  - return dict has correct variant_count
  - return dict has version_label
  - restored draft items independent of version items

  Restore Route:
  - POST redirects to draft editor
  - flash message contains draft id
  - 404 for missing version
  - restored draft visible in drafts list

  Restore UI:
  - restore button visible on version detail page
  - restore button visible in menu detail version history

  Price Highlighting Route:
  - compare page shows price-increase class
  - compare page shows price-decrease class
  - compare page shows old price with strikethrough
  - compare page shows new price with arrow
  - variant price change shows highlight
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 89)
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


def _create_version_with_items(conn, menu_id, version_number=1,
                                items=None, label=None) -> int:
    """Create a version with directly inserted items + variants."""
    items = items or []
    variant_total = sum(len(it.get("variants", [])) for it in items)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO menu_versions (menu_id, version_number, label, source_draft_id, "
        "item_count, variant_count, notes, created_at) "
        "VALUES (?, ?, ?, NULL, ?, ?, NULL, datetime('now'))",
        (menu_id, version_number, label or f"v{version_number}",
         len(items), variant_total),
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
# Price Direction — Item Diffs
# ===========================================================================
class TestPriceDirectionItems:
    """_diff_item_fields includes price_direction for price changes."""

    def test_price_increase(self):
        from storage.menus import _diff_item_fields
        fc = _diff_item_fields(
            {"name": "A", "price_cents": 1000},
            {"name": "A", "price_cents": 1500},
        )
        price_change = [c for c in fc if c["field"] == "price_cents"]
        assert len(price_change) == 1
        assert price_change[0]["price_direction"] == "increase"

    def test_price_decrease(self):
        from storage.menus import _diff_item_fields
        fc = _diff_item_fields(
            {"name": "A", "price_cents": 1500},
            {"name": "A", "price_cents": 1000},
        )
        price_change = [c for c in fc if c["field"] == "price_cents"]
        assert len(price_change) == 1
        assert price_change[0]["price_direction"] == "decrease"

    def test_price_unchanged_no_direction(self):
        from storage.menus import _diff_item_fields
        fc = _diff_item_fields(
            {"name": "A", "price_cents": 1000},
            {"name": "A", "price_cents": 1000},
        )
        price_changes = [c for c in fc if c["field"] == "price_cents"]
        assert len(price_changes) == 0

    def test_price_added_none_to_value(self):
        from storage.menus import _diff_item_fields
        fc = _diff_item_fields(
            {"name": "A", "price_cents": None},
            {"name": "A", "price_cents": 999},
        )
        price_change = [c for c in fc if c["field"] == "price_cents"]
        assert len(price_change) == 1
        assert price_change[0]["price_direction"] == "increase"

    def test_price_removed_value_to_none(self):
        from storage.menus import _diff_item_fields
        fc = _diff_item_fields(
            {"name": "A", "price_cents": 999},
            {"name": "A", "price_cents": None},
        )
        price_change = [c for c in fc if c["field"] == "price_cents"]
        assert len(price_change) == 1
        assert price_change[0]["price_direction"] == "decrease"

    def test_multiple_fields_price_direction_only_on_price(self):
        from storage.menus import _diff_item_fields
        fc = _diff_item_fields(
            {"name": "A", "description": "Old", "price_cents": 500, "category": "Apps"},
            {"name": "A", "description": "New", "price_cents": 800, "category": "Mains"},
        )
        for change in fc:
            if change["field"] == "price_cents":
                assert "price_direction" in change
            else:
                assert "price_direction" not in change

    def test_zero_to_positive_is_increase(self):
        from storage.menus import _diff_item_fields
        fc = _diff_item_fields(
            {"name": "A", "price_cents": 0},
            {"name": "A", "price_cents": 500},
        )
        price_change = [c for c in fc if c["field"] == "price_cents"]
        assert price_change[0]["price_direction"] == "increase"

    def test_positive_to_zero_is_decrease(self):
        from storage.menus import _diff_item_fields
        fc = _diff_item_fields(
            {"name": "A", "price_cents": 500},
            {"name": "A", "price_cents": 0},
        )
        price_change = [c for c in fc if c["field"] == "price_cents"]
        assert price_change[0]["price_direction"] == "decrease"


# ===========================================================================
# Price Direction — Variant Diffs
# ===========================================================================
class TestPriceDirectionVariants:
    """_diff_variants includes price_direction on variant price changes."""

    def test_variant_price_increase(self):
        from storage.menus import _diff_variants
        result = _diff_variants(
            [{"label": "Small", "price_cents": 500, "kind": "size", "position": 0}],
            [{"label": "Small", "price_cents": 800, "kind": "size", "position": 0}],
        )
        assert len(result["modified"]) == 1
        pc = [fc for fc in result["modified"][0]["field_changes"]
              if fc["field"] == "price_cents"]
        assert pc[0]["price_direction"] == "increase"

    def test_variant_price_decrease(self):
        from storage.menus import _diff_variants
        result = _diff_variants(
            [{"label": "Large", "price_cents": 1200, "kind": "size", "position": 0}],
            [{"label": "Large", "price_cents": 900, "kind": "size", "position": 0}],
        )
        pc = [fc for fc in result["modified"][0]["field_changes"]
              if fc["field"] == "price_cents"]
        assert pc[0]["price_direction"] == "decrease"

    def test_variant_price_unchanged_no_direction(self):
        from storage.menus import _diff_variants
        result = _diff_variants(
            [{"label": "Med", "price_cents": 700, "kind": "size", "position": 0}],
            [{"label": "Med", "price_cents": 700, "kind": "size", "position": 0}],
        )
        assert len(result["modified"]) == 0
        assert len(result["unchanged"]) == 1

    def test_variant_non_price_no_direction(self):
        from storage.menus import _diff_variants
        result = _diff_variants(
            [{"label": "Small", "price_cents": 500, "kind": "size", "position": 0}],
            [{"label": "Small", "price_cents": 500, "kind": "flavor", "position": 0}],
        )
        assert len(result["modified"]) == 1
        for fc in result["modified"][0]["field_changes"]:
            if fc["field"] != "price_cents":
                assert "price_direction" not in fc


# ===========================================================================
# Price Direction — Full compare_menu_versions
# ===========================================================================
class TestPriceDirectionFullDiff:
    """compare_menu_versions propagates price_direction through the diff."""

    def test_price_direction_in_full_diff(self, fresh_db):
        from storage.menus import compare_menu_versions
        conn = fresh_db
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        v1 = _create_version_with_items(conn, mid, 1, [
            {"name": "Burger", "price_cents": 1000, "category": "Mains"},
        ])
        v2 = _create_version_with_items(conn, mid, 2, [
            {"name": "Burger", "price_cents": 1300, "category": "Mains"},
        ])
        diff = compare_menu_versions(v1, v2)
        modified = [c for c in diff["changes"] if c["status"] == "modified"]
        assert len(modified) == 1
        pc = [fc for fc in modified[0]["field_changes"]
              if fc["field"] == "price_cents"]
        assert pc[0]["price_direction"] == "increase"

    def test_price_decrease_in_full_diff(self, fresh_db):
        from storage.menus import compare_menu_versions
        conn = fresh_db
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        v1 = _create_version_with_items(conn, mid, 1, [
            {"name": "Salad", "price_cents": 1200, "category": "Sides"},
        ])
        v2 = _create_version_with_items(conn, mid, 2, [
            {"name": "Salad", "price_cents": 800, "category": "Sides"},
        ])
        diff = compare_menu_versions(v1, v2)
        modified = [c for c in diff["changes"] if c["status"] == "modified"]
        pc = [fc for fc in modified[0]["field_changes"]
              if fc["field"] == "price_cents"]
        assert pc[0]["price_direction"] == "decrease"

    def test_variant_price_direction_in_full_diff(self, fresh_db):
        from storage.menus import compare_menu_versions
        conn = fresh_db
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        v1 = _create_version_with_items(conn, mid, 1, [
            {"name": "Pizza", "price_cents": 1000, "category": "Pizza",
             "variants": [{"label": "Large", "price_cents": 1500}]},
        ])
        v2 = _create_version_with_items(conn, mid, 2, [
            {"name": "Pizza", "price_cents": 1000, "category": "Pizza",
             "variants": [{"label": "Large", "price_cents": 1800}]},
        ])
        diff = compare_menu_versions(v1, v2)
        modified = [c for c in diff["changes"] if c["status"] == "modified"]
        assert len(modified) == 1
        vm = modified[0]["variant_changes"]["modified"]
        assert len(vm) == 1
        vpc = [fc for fc in vm[0]["field_changes"] if fc["field"] == "price_cents"]
        assert vpc[0]["price_direction"] == "increase"


# ===========================================================================
# Restore to Draft — Storage
# ===========================================================================
class TestRestoreToDraft:
    """restore_version_to_draft() creates a new draft from a version."""

    def _setup_version(self, conn, items=None, version_number=1):
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        if items is None:
            items = [
                {"name": "Burger", "description": "Beef patty", "price_cents": 1200,
                 "category": "Mains", "position": 1,
                 "variants": [
                     {"label": "Single", "price_cents": 1200, "kind": "size", "position": 0},
                     {"label": "Double", "price_cents": 1600, "kind": "size", "position": 1},
                 ]},
                {"name": "Fries", "description": "Crispy", "price_cents": 500,
                 "category": "Sides", "position": 2},
            ]
        vid = _create_version_with_items(conn, mid, version_number, items)
        return rid, mid, vid

    def test_restore_creates_draft(self, fresh_db):
        from storage.menus import restore_version_to_draft
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        assert result is not None
        assert "draft_id" in result

    def test_restore_title(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        draft = get_draft(result["draft_id"])
        assert "Restored from" in draft["title"]
        assert "v1" in draft["title"]

    def test_restore_restaurant_id(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft
        rid, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        draft = get_draft(result["draft_id"])
        assert draft["restaurant_id"] == rid

    def test_restore_menu_id(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft
        _, mid, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        draft = get_draft(result["draft_id"])
        assert draft["menu_id"] == mid

    def test_restore_status_editing(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        draft = get_draft(result["draft_id"])
        assert draft["status"] == "editing"

    def test_restore_source_version_restore(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        draft = get_draft(result["draft_id"])
        assert draft["source"] == "version_restore"

    def test_restore_copies_all_items(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        items = get_draft_items(result["draft_id"])
        assert len(items) == 2

    def test_restore_item_names(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        items = get_draft_items(result["draft_id"])
        names = {it["name"] for it in items}
        assert names == {"Burger", "Fries"}

    def test_restore_item_descriptions(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        items = get_draft_items(result["draft_id"])
        descs = {it["name"]: it["description"] for it in items}
        assert descs["Burger"] == "Beef patty"
        assert descs["Fries"] == "Crispy"

    def test_restore_item_prices(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        items = get_draft_items(result["draft_id"])
        prices = {it["name"]: it["price_cents"] for it in items}
        assert prices["Burger"] == 1200
        assert prices["Fries"] == 500

    def test_restore_item_categories(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        items = get_draft_items(result["draft_id"])
        cats = {it["name"]: it["category"] for it in items}
        assert cats["Burger"] == "Mains"
        assert cats["Fries"] == "Sides"

    def test_restore_item_positions(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        items = get_draft_items(result["draft_id"])
        positions = {it["name"]: it["position"] for it in items}
        assert positions["Burger"] == 1
        assert positions["Fries"] == 2

    def test_restore_copies_variants(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        items = get_draft_items(result["draft_id"], include_variants=True)
        burger = [it for it in items if it["name"] == "Burger"][0]
        assert len(burger.get("variants", [])) == 2

    def test_restore_variant_labels(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        items = get_draft_items(result["draft_id"], include_variants=True)
        burger = [it for it in items if it["name"] == "Burger"][0]
        labels = {v["label"] for v in burger["variants"]}
        assert labels == {"Single", "Double"}

    def test_restore_variant_prices(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        items = get_draft_items(result["draft_id"], include_variants=True)
        burger = [it for it in items if it["name"] == "Burger"][0]
        prices = {v["label"]: v["price_cents"] for v in burger["variants"]}
        assert prices["Single"] == 1200
        assert prices["Double"] == 1600

    def test_restore_variant_kinds(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        items = get_draft_items(result["draft_id"], include_variants=True)
        burger = [it for it in items if it["name"] == "Burger"][0]
        kinds = {v["label"]: v["kind"] for v in burger["variants"]}
        assert kinds["Single"] == "size"
        assert kinds["Double"] == "size"

    def test_restore_variant_positions(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        items = get_draft_items(result["draft_id"], include_variants=True)
        burger = [it for it in items if it["name"] == "Burger"][0]
        positions = {v["label"]: v["position"] for v in burger["variants"]}
        assert positions["Single"] == 0
        assert positions["Double"] == 1

    def test_restore_empty_version(self, fresh_db):
        from storage.menus import restore_version_to_draft
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db, items=[])
        result = restore_version_to_draft(vid)
        assert result is not None
        items = get_draft_items(result["draft_id"])
        assert len(items) == 0

    def test_restore_nonexistent_version(self, fresh_db):
        from storage.menus import restore_version_to_draft
        result = restore_version_to_draft(99999)
        assert result is None

    def test_return_item_count(self, fresh_db):
        from storage.menus import restore_version_to_draft
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        assert result["item_count"] == 2

    def test_return_variant_count(self, fresh_db):
        from storage.menus import restore_version_to_draft
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        assert result["variant_count"] == 2

    def test_return_version_label(self, fresh_db):
        from storage.menus import restore_version_to_draft
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)
        assert result["version_label"] == "v1"

    def test_restored_draft_independent_of_version(self, fresh_db):
        """Modifying restored draft items doesn't affect version items."""
        from storage.menus import restore_version_to_draft, get_menu_version
        from storage.drafts import get_draft_items
        _, _, vid = self._setup_version(fresh_db)
        result = restore_version_to_draft(vid)

        # Verify draft items exist
        draft_items = get_draft_items(result["draft_id"])
        assert len(draft_items) == 2

        # Delete draft items manually
        fresh_db.execute("DELETE FROM draft_items WHERE draft_id=?",
                         (result["draft_id"],))
        fresh_db.commit()

        # Version items should still be intact
        version = get_menu_version(vid, include_items=True)
        assert len(version["items"]) == 2


# ===========================================================================
# Restore Route
# ===========================================================================
class TestRestoreRoute:
    """POST /menus/versions/<id>/restore route tests."""

    def _setup(self, conn):
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        vid = _create_version_with_items(conn, mid, 1, [
            {"name": "Taco", "price_cents": 800, "category": "Mains",
             "variants": [{"label": "Chicken", "price_cents": 800, "kind": "style"}]},
        ])
        return rid, mid, vid

    def test_restore_redirects(self, client, fresh_db):
        _, _, vid = self._setup(fresh_db)
        resp = client.post(f"/menus/versions/{vid}/restore")
        assert resp.status_code == 302
        assert "/drafts/" in resp.headers["Location"]
        assert "/edit" in resp.headers["Location"]

    def test_restore_flash_message(self, client, fresh_db):
        _, _, vid = self._setup(fresh_db)
        resp = client.post(f"/menus/versions/{vid}/restore",
                           follow_redirects=True)
        html = resp.data.decode()
        assert "Created draft #" in html

    def test_restore_404_missing(self, client, fresh_db):
        resp = client.post("/menus/versions/99999/restore")
        assert resp.status_code == 404

    def test_restored_draft_in_list(self, client, fresh_db):
        _, _, vid = self._setup(fresh_db)
        client.post(f"/menus/versions/{vid}/restore")
        # Verify draft exists in DB
        row = fresh_db.execute(
            "SELECT * FROM drafts WHERE source='version_restore'"
        ).fetchone()
        assert row is not None
        assert row["status"] == "editing"


# ===========================================================================
# Restore UI
# ===========================================================================
class TestRestoreUI:
    """Restore buttons in templates."""

    def _setup(self, conn):
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        vid = _create_version_with_items(conn, mid, 1, [
            {"name": "Salad", "price_cents": 900, "category": "Sides"},
        ])
        return rid, mid, vid

    def test_restore_button_on_version_detail(self, client, fresh_db):
        _, mid, vid = self._setup(fresh_db)
        resp = client.get(f"/menus/versions/{vid}")
        html = resp.data.decode()
        assert "Restore to Draft" in html
        assert f"/menus/versions/{vid}/restore" in html

    def test_restore_button_on_menu_detail(self, client, fresh_db):
        _, mid, vid = self._setup(fresh_db)
        resp = client.get(f"/menus/{mid}/detail")
        html = resp.data.decode()
        assert "Restore" in html
        assert f"/menus/versions/{vid}/restore" in html


# ===========================================================================
# Price Highlighting Route
# ===========================================================================
class TestPriceHighlightRoute:
    """Compare page renders price highlighting classes."""

    def _setup_price_change(self, conn, old_price, new_price):
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        v1 = _create_version_with_items(conn, mid, 1, [
            {"name": "Burger", "price_cents": old_price, "category": "Mains"},
        ])
        v2 = _create_version_with_items(conn, mid, 2, [
            {"name": "Burger", "price_cents": new_price, "category": "Mains"},
        ])
        return mid, v1, v2

    def test_price_increase_class(self, client, fresh_db):
        mid, v1, v2 = self._setup_price_change(fresh_db, 1000, 1500)
        resp = client.get(f"/menus/{mid}/compare?a={v1}&b={v2}")
        html = resp.data.decode()
        assert "price-increase" in html

    def test_price_decrease_class(self, client, fresh_db):
        mid, v1, v2 = self._setup_price_change(fresh_db, 1500, 1000)
        resp = client.get(f"/menus/{mid}/compare?a={v1}&b={v2}")
        html = resp.data.decode()
        assert "price-decrease" in html

    def test_old_price_strikethrough(self, client, fresh_db):
        mid, v1, v2 = self._setup_price_change(fresh_db, 1000, 1500)
        resp = client.get(f"/menus/{mid}/compare?a={v1}&b={v2}")
        html = resp.data.decode()
        assert "price-old" in html
        assert "$10.00" in html

    def test_new_price_with_arrow(self, client, fresh_db):
        mid, v1, v2 = self._setup_price_change(fresh_db, 1000, 1500)
        resp = client.get(f"/menus/{mid}/compare?a={v1}&b={v2}")
        html = resp.data.decode()
        assert "$15.00" in html
        # Up triangle for increase (Unicode character)
        assert "\u25b2" in html

    def test_variant_price_highlight(self, client, fresh_db):
        conn = fresh_db
        rid = _create_restaurant(conn)
        mid = _create_menu_raw(conn, rid)
        v1 = _create_version_with_items(conn, mid, 1, [
            {"name": "Pizza", "price_cents": 1000, "category": "Pizza",
             "variants": [{"label": "Large", "price_cents": 1500}]},
        ])
        v2 = _create_version_with_items(conn, mid, 2, [
            {"name": "Pizza", "price_cents": 1000, "category": "Pizza",
             "variants": [{"label": "Large", "price_cents": 1800}]},
        ])
        resp = client.get(f"/menus/{mid}/compare?a={v1}&b={v2}")
        html = resp.data.decode()
        assert "price-increase" in html
        assert "$15.00" in html  # old
        assert "$18.00" in html  # new
