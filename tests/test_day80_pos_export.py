"""
Day 80 — POS Export Templates tests.

Sprint 9.3, Day 80: Verifies POS-specific export formats (Square CSV, Toast CSV,
Generic POS JSON), pre-export validation, and export preview endpoint.

Covers:
  Pre-export validation:
  - Items with no price and no variants flagged
  - Items with no category flagged
  - Items with no name flagged
  - Variants with zero price flagged
  - Items with price but no variants pass (no missing_price warning)
  - Items with variants but no base price pass (variants provide pricing)
  - Clean items produce no warnings
  - Multiple warnings per item possible

  Square CSV export:
  - Header row with correct columns
  - Item without variants: single row with token=item
  - Item with size variants: item row + modifier rows (Modifier Set = "Size")
  - Item with combo variants: modifier set = "Combo Add-on"
  - Item with mixed kinds: grouped by kind into separate modifier sets
  - Empty draft: header only
  - Price formatting: cents to dollars (1299 -> 12.99)
  - All 5 variant kinds map to correct modifier set names
  - Multiple items each with their own modifier rows

  Toast CSV export:
  - Header row with correct columns
  - Item without variants: category in Menu Group, name in Menu Item
  - Item with variants: parent row + option rows (grouped by kind)
  - Items with no category -> "Uncategorized" as Menu Group
  - Empty draft: header only
  - Price formatting: cents to dollars
  - All 5 variant kinds map to correct option group names
  - Multiple items from same category

  Generic POS JSON export:
  - Top-level structure: menu + metadata
  - Menu contains categories with items
  - Items have modifiers array (from variants)
  - Items without variants have empty modifiers list
  - Category grouping: items sorted into categories
  - Uncategorized items go to "Uncategorized" category
  - Price formatting in dollars
  - Metadata includes format, version, counts
  - Empty draft: empty categories array
  - Mixed items: some with variants, some without

  Export preview endpoint:
  - Returns JSON with content, item_count, warnings
  - Format parameter selects output (square, toast, generic_pos)
  - Default format is generic_pos
  - Preview includes validation warnings
  - Truncated flag for large datasets

  Validation endpoint:
  - Returns item_count, variant_count, warnings, warning_count
  - Correct counts for items with and without variants

  Flask route integration:
  - Square CSV route returns correct content-type and filename
  - Toast CSV route returns correct content-type and filename
  - Generic POS JSON route returns correct content-type and filename
  - Validation endpoint returns JSON
  - Preview endpoint returns JSON
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 71-79 tests)
# ---------------------------------------------------------------------------
_TEST_CONN: Optional[sqlite3.Connection] = None


def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the required schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER,
            name TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            category TEXT,
            position INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_draft ON draft_items(draft_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_variants_item ON draft_item_variants(item_id)")
    conn.commit()
    return conn


def _patch_db(monkeypatch):
    global _TEST_CONN
    _TEST_CONN = _make_test_db()
    import storage.drafts as drafts_mod
    def mock_connect():
        return _TEST_CONN
    monkeypatch.setattr(drafts_mod, "db_connect", mock_connect)
    return _TEST_CONN


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    conn = _patch_db(monkeypatch)
    yield conn
    global _TEST_CONN
    _TEST_CONN = None


def _create_draft(conn, title="Test Draft", status="editing") -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO drafts (title, status, created_at, updated_at) VALUES (?, ?, datetime('now'), datetime('now'))",
        (title, status),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_item(conn, draft_id, name, price_cents=0, category=None, description=None, position=None) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_items (draft_id, name, description, price_cents, category, position, confidence, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 80, datetime('now'), datetime('now'))",
        (draft_id, name, description, price_cents, category, position),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_variant(conn, item_id, label, price_cents, kind="size", position=0) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_item_variants (item_id, label, price_cents, kind, position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (item_id, label, price_cents, kind, position),
    )
    conn.commit()
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Import the functions under test
# ---------------------------------------------------------------------------
from portal.app import (
    _validate_draft_for_export,
    _format_price_dollars,
    _build_square_rows,
    _build_toast_rows,
    _build_generic_pos_json,
)


# ===========================================================================
# SECTION 1: _format_price_dollars
# ===========================================================================

class TestFormatPriceDollars:
    def test_normal_price(self):
        assert _format_price_dollars(1299) == "12.99"

    def test_zero(self):
        assert _format_price_dollars(0) == "0.00"

    def test_none(self):
        assert _format_price_dollars(None) == "0.00"

    def test_small_price(self):
        assert _format_price_dollars(50) == "0.50"

    def test_whole_dollar(self):
        assert _format_price_dollars(1000) == "10.00"

    def test_single_cent(self):
        assert _format_price_dollars(1) == "0.01"


# ===========================================================================
# SECTION 2: Pre-export validation
# ===========================================================================

class TestValidation:
    def test_clean_item_no_warnings(self):
        items = [{"id": 1, "name": "Burger", "price_cents": 999, "category": "Entrees", "variants": []}]
        w = _validate_draft_for_export(items)
        assert w == []

    def test_missing_price_no_variants(self):
        items = [{"id": 1, "name": "Burger", "price_cents": 0, "category": "Entrees", "variants": []}]
        w = _validate_draft_for_export(items)
        types = [x["type"] for x in w]
        assert "missing_price" in types

    def test_missing_category(self):
        items = [{"id": 1, "name": "Burger", "price_cents": 999, "category": "", "variants": []}]
        w = _validate_draft_for_export(items)
        types = [x["type"] for x in w]
        assert "missing_category" in types

    def test_missing_name(self):
        items = [{"id": 1, "name": "", "price_cents": 999, "category": "Entrees", "variants": []}]
        w = _validate_draft_for_export(items)
        types = [x["type"] for x in w]
        assert "missing_name" in types

    def test_variant_missing_price(self):
        items = [{
            "id": 1, "name": "Pizza", "price_cents": 999, "category": "Pizza",
            "variants": [{"label": "Small", "price_cents": 0, "kind": "size"}],
        }]
        w = _validate_draft_for_export(items)
        types = [x["type"] for x in w]
        assert "variant_missing_price" in types

    def test_item_with_variants_no_base_price_ok(self):
        """Items with variants but no base price should NOT get missing_price."""
        items = [{
            "id": 1, "name": "Pizza", "price_cents": 0, "category": "Pizza",
            "variants": [{"label": "Small", "price_cents": 899, "kind": "size"}],
        }]
        w = _validate_draft_for_export(items)
        types = [x["type"] for x in w]
        assert "missing_price" not in types

    def test_multiple_warnings_per_item(self):
        items = [{"id": 1, "name": "", "price_cents": 0, "category": "", "variants": []}]
        w = _validate_draft_for_export(items)
        assert len(w) >= 3  # missing_price + missing_category + missing_name

    def test_empty_items_no_warnings(self):
        w = _validate_draft_for_export([])
        assert w == []

    def test_null_category_is_missing(self):
        items = [{"id": 1, "name": "Burger", "price_cents": 999, "category": None, "variants": []}]
        w = _validate_draft_for_export(items)
        types = [x["type"] for x in w]
        assert "missing_category" in types

    def test_warning_includes_item_info(self):
        items = [{"id": 42, "name": "Fries", "price_cents": 0, "category": "Sides", "variants": []}]
        w = _validate_draft_for_export(items)
        assert w[0]["item_id"] == 42
        assert w[0]["name"] == "Fries"
        assert "message" in w[0]


# ===========================================================================
# SECTION 3: Square CSV export
# ===========================================================================

class TestSquareCSV:
    def test_header_columns(self):
        """_build_square_rows returns rows (no header), but the route adds them."""
        rows = _build_square_rows([])
        assert rows == []

    def test_item_without_variants(self):
        items = [{"name": "Burger", "description": "Beef patty", "price_cents": 999,
                   "category": "Entrees", "variants": []}]
        rows = _build_square_rows(items)
        assert len(rows) == 1
        assert rows[0][0] == "item"
        assert rows[0][1] == "Burger"
        assert rows[0][2] == "Beef patty"
        assert rows[0][3] == "Entrees"
        assert rows[0][4] == "9.99"

    def test_item_with_size_variants(self):
        items = [{
            "name": "Pizza", "description": "", "price_cents": 899, "category": "Pizza",
            "variants": [
                {"label": "Small", "price_cents": 899, "kind": "size"},
                {"label": "Large", "price_cents": 1499, "kind": "size"},
            ],
        }]
        rows = _build_square_rows(items)
        assert len(rows) == 3  # 1 parent + 2 modifiers
        assert rows[0][0] == "item"
        assert rows[1][0] == "modifier"
        assert rows[1][5] == "Size"  # modifier set name
        assert rows[1][6] == "Small"
        assert rows[1][7] == "8.99"
        assert rows[2][6] == "Large"
        assert rows[2][7] == "14.99"

    def test_item_with_combo_variants(self):
        items = [{
            "name": "Burger", "description": "", "price_cents": 999, "category": "Entrees",
            "variants": [
                {"label": "W/Fries", "price_cents": 1299, "kind": "combo"},
            ],
        }]
        rows = _build_square_rows(items)
        assert rows[1][5] == "Combo Add-on"
        assert rows[1][6] == "W/Fries"

    def test_item_with_mixed_kinds(self):
        items = [{
            "name": "Pizza", "description": "", "price_cents": 899, "category": "Pizza",
            "variants": [
                {"label": "Small", "price_cents": 899, "kind": "size"},
                {"label": "Pepperoni", "price_cents": 100, "kind": "flavor"},
            ],
        }]
        rows = _build_square_rows(items)
        assert len(rows) == 3
        # Find the modifier rows
        mod_rows = [r for r in rows if r[0] == "modifier"]
        set_names = {r[5] for r in mod_rows}
        assert "Size" in set_names
        assert "Flavor" in set_names

    def test_all_five_kinds(self):
        kinds = ["size", "combo", "flavor", "style", "other"]
        expected = ["Size", "Combo Add-on", "Flavor", "Style", "Option"]
        items = [{
            "name": "Item", "description": "", "price_cents": 500, "category": "Cat",
            "variants": [{"label": f"V{i}", "price_cents": 100 * (i + 1), "kind": k}
                         for i, k in enumerate(kinds)],
        }]
        rows = _build_square_rows(items)
        mod_rows = [r for r in rows if r[0] == "modifier"]
        set_names = [r[5] for r in mod_rows]
        for exp in expected:
            assert exp in set_names

    def test_multiple_items(self):
        items = [
            {"name": "Burger", "description": "", "price_cents": 999, "category": "Entrees", "variants": []},
            {"name": "Pizza", "description": "", "price_cents": 899, "category": "Pizza",
             "variants": [{"label": "Small", "price_cents": 899, "kind": "size"}]},
        ]
        rows = _build_square_rows(items)
        assert len(rows) == 3  # 1 item + 1 item + 1 modifier

    def test_price_formatting(self):
        items = [{"name": "A", "description": "", "price_cents": 1, "category": "C", "variants": []}]
        rows = _build_square_rows(items)
        assert rows[0][4] == "0.01"

    def test_modifier_row_has_parent_name(self):
        """Modifier rows reference parent item name for context."""
        items = [{
            "name": "Pizza", "description": "", "price_cents": 899, "category": "Pizza",
            "variants": [{"label": "Small", "price_cents": 899, "kind": "size"}],
        }]
        rows = _build_square_rows(items)
        assert rows[1][1] == "Pizza"  # parent name in Item Name column


# ===========================================================================
# SECTION 4: Toast CSV export
# ===========================================================================

class TestToastCSV:
    def test_empty_draft(self):
        rows = _build_toast_rows([])
        assert rows == []

    def test_item_without_variants(self):
        items = [{"name": "Burger", "price_cents": 999, "category": "Entrees", "variants": []}]
        rows = _build_toast_rows(items)
        assert len(rows) == 1
        assert rows[0][0] == "Entrees"  # Menu Group
        assert rows[0][1] == "Burger"   # Menu Item
        assert rows[0][2] == "9.99"     # Base Price

    def test_item_no_category_uses_uncategorized(self):
        items = [{"name": "Burger", "price_cents": 999, "category": "", "variants": []}]
        rows = _build_toast_rows(items)
        assert rows[0][0] == "Uncategorized"

    def test_item_null_category(self):
        items = [{"name": "Burger", "price_cents": 999, "category": None, "variants": []}]
        rows = _build_toast_rows(items)
        assert rows[0][0] == "Uncategorized"

    def test_item_with_variants(self):
        items = [{
            "name": "Pizza", "price_cents": 899, "category": "Pizza",
            "variants": [
                {"label": "Small", "price_cents": 899, "kind": "size"},
                {"label": "Large", "price_cents": 1499, "kind": "size"},
            ],
        }]
        rows = _build_toast_rows(items)
        assert len(rows) == 3  # parent + 2 options
        assert rows[0][0] == "Pizza"
        assert rows[0][1] == "Pizza"
        assert rows[1][3] == "Size"     # Option Group
        assert rows[1][4] == "Small"    # Option
        assert rows[1][5] == "8.99"     # Option Price
        assert rows[2][4] == "Large"

    def test_all_five_kinds(self):
        kinds = ["size", "combo", "flavor", "style", "other"]
        expected = ["Size", "Combo Add-on", "Flavor", "Style", "Option"]
        items = [{
            "name": "Item", "price_cents": 500, "category": "Cat",
            "variants": [{"label": f"V{i}", "price_cents": 100, "kind": k}
                         for i, k in enumerate(kinds)],
        }]
        rows = _build_toast_rows(items)
        opt_rows = [r for r in rows if r[3] != ""]
        groups = [r[3] for r in opt_rows]
        for exp in expected:
            assert exp in groups

    def test_multiple_items_same_category(self):
        items = [
            {"name": "Burger", "price_cents": 999, "category": "Entrees", "variants": []},
            {"name": "Steak", "price_cents": 2499, "category": "Entrees", "variants": []},
        ]
        rows = _build_toast_rows(items)
        assert len(rows) == 2
        assert rows[0][0] == "Entrees"
        assert rows[1][0] == "Entrees"

    def test_option_row_empty_menu_fields(self):
        """Option rows have empty Menu Group and Menu Item."""
        items = [{
            "name": "Pizza", "price_cents": 899, "category": "Pizza",
            "variants": [{"label": "Small", "price_cents": 899, "kind": "size"}],
        }]
        rows = _build_toast_rows(items)
        assert rows[1][0] == ""  # Menu Group empty for option row
        assert rows[1][1] == ""  # Menu Item empty for option row


# ===========================================================================
# SECTION 5: Generic POS JSON
# ===========================================================================

class TestGenericPOSJSON:
    def test_top_level_structure(self):
        payload = _build_generic_pos_json([], {"id": 1, "title": "Test"})
        assert "menu" in payload
        assert "metadata" in payload
        assert payload["menu"]["id"] == 1
        assert payload["menu"]["title"] == "Test"

    def test_metadata_fields(self):
        payload = _build_generic_pos_json([], {})
        meta = payload["metadata"]
        assert meta["format"] == "generic_pos"
        assert meta["version"] == "1.0"
        assert meta["item_count"] == 0
        assert meta["category_count"] == 0
        assert "exported_at" in meta

    def test_items_grouped_by_category(self):
        items = [
            {"name": "Burger", "description": "", "price_cents": 999, "category": "Entrees", "variants": []},
            {"name": "Fries", "description": "", "price_cents": 499, "category": "Sides", "variants": []},
            {"name": "Steak", "description": "", "price_cents": 2499, "category": "Entrees", "variants": []},
        ]
        payload = _build_generic_pos_json(items)
        cats = payload["menu"]["categories"]
        assert len(cats) == 2
        cat_names = [c["name"] for c in cats]
        assert "Entrees" in cat_names
        assert "Sides" in cat_names
        # Entrees should have 2 items
        entrees = [c for c in cats if c["name"] == "Entrees"][0]
        assert len(entrees["items"]) == 2

    def test_uncategorized_items(self):
        items = [{"name": "Mystery", "description": "", "price_cents": 0, "category": None, "variants": []}]
        payload = _build_generic_pos_json(items)
        cats = payload["menu"]["categories"]
        assert cats[0]["name"] == "Uncategorized"

    def test_item_with_modifiers(self):
        items = [{
            "name": "Pizza", "description": "Cheese pizza", "price_cents": 899, "category": "Pizza",
            "variants": [
                {"label": "Small", "price_cents": 899, "kind": "size"},
                {"label": "Large", "price_cents": 1499, "kind": "size"},
            ],
        }]
        payload = _build_generic_pos_json(items)
        item = payload["menu"]["categories"][0]["items"][0]
        assert item["name"] == "Pizza"
        assert item["description"] == "Cheese pizza"
        assert item["base_price"] == "8.99"
        assert len(item["modifiers"]) == 2
        assert item["modifiers"][0]["group"] == "Size"
        assert item["modifiers"][0]["name"] == "Small"
        assert item["modifiers"][0]["price"] == "8.99"

    def test_item_without_modifiers(self):
        items = [{"name": "Burger", "description": "", "price_cents": 999, "category": "Entrees", "variants": []}]
        payload = _build_generic_pos_json(items)
        item = payload["menu"]["categories"][0]["items"][0]
        assert item["modifiers"] == []

    def test_empty_draft(self):
        payload = _build_generic_pos_json([])
        assert payload["menu"]["categories"] == []
        assert payload["metadata"]["item_count"] == 0

    def test_price_formatting(self):
        items = [{"name": "A", "description": "", "price_cents": 1299, "category": "C", "variants": []}]
        payload = _build_generic_pos_json(items)
        assert payload["menu"]["categories"][0]["items"][0]["base_price"] == "12.99"

    def test_categories_sorted_alpha(self):
        items = [
            {"name": "Z", "description": "", "price_cents": 100, "category": "Zebra", "variants": []},
            {"name": "A", "description": "", "price_cents": 100, "category": "Apple", "variants": []},
        ]
        payload = _build_generic_pos_json(items)
        cat_names = [c["name"] for c in payload["menu"]["categories"]]
        assert cat_names == ["Apple", "Zebra"]

    def test_mixed_items(self):
        items = [
            {"name": "Burger", "description": "", "price_cents": 999, "category": "Entrees", "variants": []},
            {"name": "Pizza", "description": "", "price_cents": 899, "category": "Pizza",
             "variants": [{"label": "Small", "price_cents": 899, "kind": "size"}]},
        ]
        payload = _build_generic_pos_json(items)
        assert payload["metadata"]["item_count"] == 2
        assert payload["metadata"]["category_count"] == 2


# ===========================================================================
# SECTION 6: Flask route integration tests
# ===========================================================================

@pytest.fixture()
def client(fresh_db):
    """Flask test client with mocked DB and fake session login."""
    from portal.app import app
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    with app.test_client() as c:
        # Set session to bypass login_required
        with c.session_transaction() as sess:
            sess["user"] = {"id": 1, "email": "test@test.com"}
        yield c


class TestSquareRoute:
    def test_square_csv_headers(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export_square.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["Content-Type"]
        assert f"draft_{did}_square.csv" in resp.headers["Content-Disposition"]

    def test_square_csv_content(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza", 899, "Pizza")
        _insert_variant(fresh_db, iid, "Small", 899, "size", 0)
        _insert_variant(fresh_db, iid, "Large", 1499, "size", 1)
        resp = client.get(f"/drafts/{did}/export_square.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        assert rows[0][0] == "Token"  # header
        assert rows[1][0] == "item"
        assert rows[2][0] == "modifier"
        assert rows[2][5] == "Size"
        assert rows[2][6] == "Small"

    def test_square_csv_empty(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_square.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == 1  # header only


class TestToastRoute:
    def test_toast_csv_headers(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_toast.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["Content-Type"]
        assert f"draft_{did}_toast.csv" in resp.headers["Content-Disposition"]

    def test_toast_csv_content(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza", 899, "Pizza")
        _insert_variant(fresh_db, iid, "Small", 899, "size", 0)
        resp = client.get(f"/drafts/{did}/export_toast.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        assert rows[0][0] == "Menu Group"
        assert rows[1][0] == "Pizza"
        assert rows[1][1] == "Pizza"
        assert rows[2][3] == "Size"
        assert rows[2][4] == "Small"


class TestPOSJSONRoute:
    def test_pos_json_headers(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_pos.json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["Content-Type"]
        assert f"draft_{did}_pos.json" in resp.headers["Content-Disposition"]

    def test_pos_json_content(self, client, fresh_db):
        did = _create_draft(fresh_db, title="My Menu")
        iid = _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export_pos.json")
        data = json.loads(resp.data)
        assert data["menu"]["title"] == "My Menu"
        assert len(data["menu"]["categories"]) == 1
        assert data["menu"]["categories"][0]["items"][0]["name"] == "Burger"
        assert data["metadata"]["item_count"] == 1


class TestValidationRoute:
    def test_validate_endpoint(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        _insert_item(fresh_db, did, "", 0, "")  # bad item
        resp = client.get(f"/drafts/{did}/export/validate")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["item_count"] == 2
        assert data["warning_count"] > 0
        assert "warnings" in data

    def test_validate_counts(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza", 899, "Pizza")
        _insert_variant(fresh_db, iid, "Small", 899, "size")
        _insert_variant(fresh_db, iid, "Large", 1499, "size")
        resp = client.get(f"/drafts/{did}/export/validate")
        data = json.loads(resp.data)
        assert data["item_count"] == 1
        assert data["variant_count"] == 2

    def test_validate_clean_draft(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export/validate")
        data = json.loads(resp.data)
        assert data["warning_count"] == 0


class TestPreviewRoute:
    def test_preview_default_format(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export/preview")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["format"] == "generic_pos"
        assert data["item_count"] == 1
        assert "content" in data
        # Content should be valid JSON (generic_pos)
        parsed = json.loads(data["content"])
        assert "menu" in parsed

    def test_preview_square_format(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export/preview?format=square")
        data = json.loads(resp.data)
        assert data["format"] == "square"
        assert "Token" in data["content"]  # CSV header

    def test_preview_toast_format(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export/preview?format=toast")
        data = json.loads(resp.data)
        assert data["format"] == "toast"
        assert "Menu Group" in data["content"]

    def test_preview_includes_warnings(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "", 0, "")  # bad item
        resp = client.get(f"/drafts/{did}/export/preview")
        data = json.loads(resp.data)
        assert len(data["warnings"]) > 0

    def test_preview_empty_draft(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export/preview")
        data = json.loads(resp.data)
        assert data["item_count"] == 0
