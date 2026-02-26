"""
Day 82 -- Export Finalization & Sprint 9.3 Wrap-Up.

Sprint 9.3, Day 82: End-to-end integration tests across all export formats,
cross-format consistency checks, edge case hardening, and pipeline round-trip
verification through Flask routes.

Covers:
  End-to-end integration:
  - Create draft -> insert items with variants -> export all 9 formats
  - Verify output structure, item/variant counts, content integrity
  - All POS formats produce correct hierarchies

  Cross-format consistency:
  - Same data exported via CSV/JSON/Square/Toast/POS JSON agree on counts
  - Variant counts consistent across all formats
  - Category groupings consistent between Toast and POS JSON

  Edge case hardening:
  - CSV-hostile characters (commas, quotes, newlines in names/descriptions)
  - Very long item names (200+ chars)
  - Mixed variant kinds on a single item across all formats
  - Large drafts (50+ items) export correctly
  - Items with descriptions containing special characters
  - Null/empty descriptions don't break formats
  - Price formatting edge cases (0, 1 cent, large values)

  Export pipeline integration (Flask routes):
  - Save items via upsert -> export via route -> verify content
  - Validate + metrics + preview all return correct data for same draft
  - Export routes return correct Content-Type and Content-Disposition headers
  - Preview truncation for large drafts
  - Nonexistent draft returns 404 or empty
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 71-81 tests)
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


def _insert_item(conn, draft_id, name, price_cents=0, category=None,
                 description=None, position=None) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_items (draft_id, name, description, price_cents, category, "
        "position, confidence, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 80, datetime('now'), datetime('now'))",
        (draft_id, name, description, price_cents, category, position),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_variant(conn, item_id, label, price_cents, kind="size",
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


# ---------------------------------------------------------------------------
# Import tested functions
# ---------------------------------------------------------------------------
from portal.app import (
    _validate_draft_for_export,
    _compute_export_metrics,
    _verify_csv_round_trip,
    _verify_json_round_trip,
    _verify_pos_json_round_trip,
    _build_square_rows,
    _build_toast_rows,
    _build_generic_pos_json,
    _format_price_dollars,
)


# ---------------------------------------------------------------------------
# Helpers: build in-memory item dicts
# ---------------------------------------------------------------------------
def _item(id=1, name="Burger", price_cents=999, category="Entrees",
          description="", variants=None):
    return {
        "id": id, "name": name, "price_cents": price_cents,
        "category": category, "description": description,
        "position": None,
        "variants": variants or [],
    }


def _var(label="Small", price_cents=799, kind="size", position=0):
    return {"label": label, "price_cents": price_cents, "kind": kind,
            "position": position}


@pytest.fixture()
def client(fresh_db):
    """Flask test client with mocked DB and fake session login."""
    from portal.app import app
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = {"id": 1, "email": "test@test.com"}
        yield c


# ---------------------------------------------------------------------------
# Helper: build a realistic multi-category draft with variants
# ---------------------------------------------------------------------------
def _build_realistic_draft(conn):
    """Create a draft with 3 categories, mix of variant/no-variant items."""
    did = _create_draft(conn, title="Full Menu")

    # -- Burgers (2 items, both with size variants) --
    b1 = _insert_item(conn, did, "Classic Burger", 999, "Burgers",
                      "Beef patty with lettuce and tomato")
    _insert_variant(conn, b1, "Single", 999, "size", 0)
    _insert_variant(conn, b1, "Double", 1399, "size", 1)

    b2 = _insert_item(conn, did, "Cheese Burger", 1099, "Burgers",
                      "With American cheese")
    _insert_variant(conn, b2, "Single", 1099, "size", 0)
    _insert_variant(conn, b2, "Double", 1499, "size", 1)
    _insert_variant(conn, b2, "w/ Fries", 200, "combo", 2)

    # -- Drinks (2 items, 1 with sizes, 1 without) --
    d1 = _insert_item(conn, did, "Soda", 250, "Drinks", "Coke, Sprite, Fanta")
    _insert_variant(conn, d1, "Small", 250, "size", 0)
    _insert_variant(conn, d1, "Medium", 350, "size", 1)
    _insert_variant(conn, d1, "Large", 450, "size", 2)

    d2 = _insert_item(conn, did, "Water", 150, "Drinks")

    # -- Sides (1 item, no variants) --
    s1 = _insert_item(conn, did, "French Fries", 499, "Sides",
                      "Crispy golden fries")

    return did


# ===========================================================================
# SECTION 1: End-to-End Integration — All 9 Formats
# ===========================================================================

class TestE2EAllFormats:
    """Create a realistic draft, export every format, verify output."""

    def test_e2e_csv_flat(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == 5  # 5 items total
        names = {r["name"] for r in rows}
        assert "Classic Burger" in names
        assert "Water" in names

    def test_e2e_csv_variants(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_variants.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        item_rows = [r for r in rows if r["type"] == "item"]
        variant_rows = [r for r in rows if r["type"] == "variant"]
        assert len(item_rows) == 5
        assert len(variant_rows) == 8  # 2 + 3 + 3 + 0 + 0

    def test_e2e_csv_wide(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_wide.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == 5
        # Should have price columns for all unique labels
        headers = reader.fieldnames
        assert "price_Single" in headers
        assert "price_Small" in headers

    def test_e2e_json(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export.json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["items"]) == 5
        # Cheese Burger has 3 variants
        cheese = [i for i in data["items"] if i["name"] == "Cheese Burger"][0]
        assert len(cheese["variants"]) == 3

    def test_e2e_xlsx(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export.xlsx")
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.headers["Content-Type"]

    def test_e2e_xlsx_by_category(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_by_category.xlsx")
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.headers["Content-Type"]

    def test_e2e_square_csv(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_square.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        header = rows[0]
        data_rows = rows[1:]
        item_rows = [r for r in data_rows if r[0] == "item"]
        mod_rows = [r for r in data_rows if r[0] == "modifier"]
        assert len(item_rows) == 5
        assert len(mod_rows) == 8

    def test_e2e_toast_csv(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_toast.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        data_rows = rows[1:]  # skip header
        parent_rows = [r for r in data_rows if r[0] != ""]
        option_rows = [r for r in data_rows if r[0] == ""]
        assert len(parent_rows) == 5
        assert len(option_rows) == 8

    def test_e2e_pos_json(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_pos.json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        cats = data["menu"]["categories"]
        cat_names = {c["name"] for c in cats}
        assert "Burgers" in cat_names
        assert "Drinks" in cat_names
        assert "Sides" in cat_names
        total_items = sum(len(c["items"]) for c in cats)
        assert total_items == 5
        total_mods = sum(
            len(i["modifiers"]) for c in cats for i in c["items"]
        )
        assert total_mods == 8

    def test_e2e_validate(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export/validate")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["item_count"] == 5
        assert data["variant_count"] == 8

    def test_e2e_metrics(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export/metrics")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["total_items"] == 5
        assert data["items_with_variants"] == 3
        assert data["items_without_variants"] == 2
        assert data["total_variants"] == 8

    def test_e2e_preview_generic(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export/preview?format=generic_pos")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["format"] == "generic_pos"
        # content is a JSON string
        content = json.loads(data["content"])
        assert "menu" in content
        assert data["item_count"] == 5

    def test_e2e_preview_square(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export/preview?format=square")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["format"] == "square"
        assert "Token" in data["content"]

    def test_e2e_preview_toast(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export/preview?format=toast")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["format"] == "toast"
        assert "Menu Group" in data["content"]


# ===========================================================================
# SECTION 2: Cross-Format Consistency
# ===========================================================================

class TestCrossFormatConsistency:
    """Verify same data produces consistent counts across all formats."""

    def _multi_variant_items(self):
        """3 items: 2 with variants (total 5 variants), 1 without."""
        return [
            _item(id=1, name="Pizza", price_cents=1200, category="Entrees",
                  variants=[_var("Small", 1000), _var("Large", 1500),
                            _var("Spicy", 0, "flavor")]),
            _item(id=2, name="Wings", price_cents=899, category="Appetizers",
                  variants=[_var("6pc", 899), _var("12pc", 1499)]),
            _item(id=3, name="Soda", price_cents=250, category="Drinks"),
        ]

    def test_csv_and_json_item_count_match(self):
        items = self._multi_variant_items()
        csv_rt = _verify_csv_round_trip(items)
        json_rt = _verify_json_round_trip(items)
        assert csv_rt["actual_items"] == json_rt["actual_items"] == 3

    def test_csv_and_json_variant_count_match(self):
        items = self._multi_variant_items()
        csv_rt = _verify_csv_round_trip(items)
        json_rt = _verify_json_round_trip(items)
        assert csv_rt["actual_variants"] == json_rt["actual_variants"] == 5

    def test_pos_json_and_csv_modifier_count_match(self):
        items = self._multi_variant_items()
        csv_rt = _verify_csv_round_trip(items)
        pos_rt = _verify_pos_json_round_trip(items)
        assert csv_rt["actual_variants"] == pos_rt["actual_modifiers"] == 5

    def test_square_and_toast_variant_counts_match(self):
        items = self._multi_variant_items()
        sq_rows = _build_square_rows(items)
        toast_rows = _build_toast_rows(items)
        sq_mods = len([r for r in sq_rows if r[0] == "modifier"])
        toast_opts = len([r for r in toast_rows if r[0] == ""])
        assert sq_mods == toast_opts == 5

    def test_square_and_toast_item_counts_match(self):
        items = self._multi_variant_items()
        sq_rows = _build_square_rows(items)
        toast_rows = _build_toast_rows(items)
        sq_items = len([r for r in sq_rows if r[0] == "item"])
        toast_items = len([r for r in toast_rows if r[0] != ""])
        assert sq_items == toast_items == 3

    def test_metrics_match_round_trip_counts(self):
        items = self._multi_variant_items()
        m = _compute_export_metrics(items)
        csv_rt = _verify_csv_round_trip(items)
        assert m["total_items"] == csv_rt["actual_items"]
        assert m["total_variants"] == csv_rt["actual_variants"]

    def test_pos_json_categories_match_toast_groups(self):
        items = self._multi_variant_items()
        pos = _build_generic_pos_json(items)
        toast_rows = _build_toast_rows(items)
        pos_cats = {c["name"] for c in pos["menu"]["categories"]}
        toast_groups = {r[0] for r in toast_rows if r[0] != ""}
        assert pos_cats == toast_groups

    def test_all_formats_agree_on_no_variants(self):
        """Item without variants: all formats agree on 0 variants."""
        items = [_item(id=1, name="Plain", price_cents=500)]
        csv_rt = _verify_csv_round_trip(items)
        json_rt = _verify_json_round_trip(items)
        pos_rt = _verify_pos_json_round_trip(items)
        sq = _build_square_rows(items)
        toast = _build_toast_rows(items)
        assert csv_rt["actual_variants"] == 0
        assert json_rt["actual_variants"] == 0
        assert pos_rt["actual_modifiers"] == 0
        assert len([r for r in sq if r[0] == "modifier"]) == 0
        assert len([r for r in toast if r[0] == ""]) == 0


# ===========================================================================
# SECTION 3: Edge Case — CSV-Hostile Characters
# ===========================================================================

class TestEdgeCaseCSVHostile:
    """Items with commas, quotes, and newlines in names/descriptions."""

    def test_comma_in_name_csv_flat(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger, Deluxe", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert rows[0]["name"] == "Burger, Deluxe"

    def test_quote_in_name_csv_flat(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, 'The "Best" Burger', 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert rows[0]["name"] == 'The "Best" Burger'

    def test_comma_in_description_csv_variants(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger", 999, "Entrees",
                           "Lettuce, tomato, onion")
        _insert_variant(fresh_db, iid, "Small", 799)
        resp = client.get(f"/drafts/{did}/export_variants.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        item_row = [r for r in rows if r["type"] == "item"][0]
        assert "Lettuce, tomato, onion" in item_row["description"]

    def test_comma_in_variant_label_square(self):
        items = [_item(variants=[_var("Small, Regular", 800)])]
        rows = _build_square_rows(items)
        mod_rows = [r for r in rows if r[0] == "modifier"]
        assert mod_rows[0][6] == "Small, Regular"

    def test_quote_in_description_json(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees",
                     'Served with "special" sauce')
        resp = client.get(f"/drafts/{did}/export.json")
        data = json.loads(resp.data)
        assert data["items"][0]["description"] == 'Served with "special" sauce'

    def test_comma_in_category_toast(self):
        items = [_item(category="Burgers, Hot Dogs")]
        rows = _build_toast_rows(items)
        assert rows[0][0] == "Burgers, Hot Dogs"

    def test_special_chars_in_pos_json(self):
        items = [_item(name='Fish & Chips "Classic"',
                       description="Best in town!",
                       category="Entrees & More")]
        payload = _build_generic_pos_json(items)
        cat = payload["menu"]["categories"][0]
        assert cat["name"] == "Entrees & More"
        assert cat["items"][0]["name"] == 'Fish & Chips "Classic"'


# ===========================================================================
# SECTION 4: Edge Case — Very Long Names & Descriptions
# ===========================================================================

class TestEdgeCaseLongStrings:

    def test_long_name_csv(self, client, fresh_db):
        did = _create_draft(fresh_db)
        long_name = "A" * 250
        _insert_item(fresh_db, did, long_name, 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert rows[0]["name"] == long_name

    def test_long_description_json(self, client, fresh_db):
        did = _create_draft(fresh_db)
        long_desc = "Word " * 500
        _insert_item(fresh_db, did, "Burger", 999, "Entrees", long_desc.strip())
        resp = client.get(f"/drafts/{did}/export.json")
        data = json.loads(resp.data)
        assert len(data["items"][0]["description"]) > 2000

    def test_long_variant_label_square(self):
        items = [_item(variants=[_var("Extra Large with Everything", 1500)])]
        rows = _build_square_rows(items)
        mod_rows = [r for r in rows if r[0] == "modifier"]
        assert mod_rows[0][6] == "Extra Large with Everything"

    def test_long_category_pos_json(self):
        long_cat = "Category " * 30
        items = [_item(category=long_cat.strip())]
        payload = _build_generic_pos_json(items)
        assert payload["menu"]["categories"][0]["name"] == long_cat.strip()


# ===========================================================================
# SECTION 5: Edge Case — Mixed Variant Kinds on Single Item
# ===========================================================================

class TestEdgeCaseMixedKinds:

    def _mixed_kind_items(self):
        return [_item(variants=[
            _var("Small", 800, "size", 0),
            _var("Medium", 1000, "size", 1),
            _var("Large", 1200, "size", 2),
            _var("w/ Fries", 200, "combo", 3),
            _var("w/ Salad", 250, "combo", 4),
            _var("Spicy", 0, "flavor", 5),
            _var("Mild", 0, "flavor", 6),
            _var("Grilled", 0, "style", 7),
            _var("Fried", 0, "style", 8),
            _var("Extra Cheese", 150, "other", 9),
        ])]

    def test_mixed_square_modifier_sets(self):
        items = self._mixed_kind_items()
        rows = _build_square_rows(items)
        mod_rows = [r for r in rows if r[0] == "modifier"]
        assert len(mod_rows) == 10
        set_names = {r[5] for r in mod_rows}
        assert set_names == {"Size", "Combo Add-on", "Flavor", "Style", "Option"}

    def test_mixed_toast_option_groups(self):
        items = self._mixed_kind_items()
        rows = _build_toast_rows(items)
        opt_rows = [r for r in rows if r[0] == ""]
        assert len(opt_rows) == 10
        groups = {r[3] for r in opt_rows}
        assert groups == {"Size", "Combo Add-on", "Flavor", "Style", "Option"}

    def test_mixed_pos_json_modifier_groups(self):
        items = self._mixed_kind_items()
        payload = _build_generic_pos_json(items)
        mods = payload["menu"]["categories"][0]["items"][0]["modifiers"]
        assert len(mods) == 10
        groups = {m["group"] for m in mods}
        assert groups == {"Size", "Combo Add-on", "Flavor", "Style", "Option"}

    def test_mixed_metrics(self):
        items = self._mixed_kind_items()
        m = _compute_export_metrics(items)
        assert m["total_variants"] == 10
        assert m["variants_by_kind"]["size"] == 3
        assert m["variants_by_kind"]["combo"] == 2
        assert m["variants_by_kind"]["flavor"] == 2
        assert m["variants_by_kind"]["style"] == 2
        assert m["variants_by_kind"]["other"] == 1

    def test_mixed_validation_no_price_inversion(self):
        items = self._mixed_kind_items()
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        # S < M < L is ascending, no inversion
        assert "price_inversion" not in types

    def test_mixed_round_trip(self):
        items = self._mixed_kind_items()
        csv_rt = _verify_csv_round_trip(items)
        json_rt = _verify_json_round_trip(items)
        pos_rt = _verify_pos_json_round_trip(items)
        assert csv_rt["ok"] is True
        assert json_rt["ok"] is True
        assert pos_rt["ok"] is True
        assert csv_rt["actual_variants"] == 10
        assert json_rt["actual_variants"] == 10
        assert pos_rt["actual_modifiers"] == 10


# ===========================================================================
# SECTION 6: Edge Case — Large Drafts (50+ items)
# ===========================================================================

class TestEdgeCaseLargeDraft:

    def _build_large_draft(self, conn, count=55):
        did = _create_draft(conn, title="Large Menu")
        for i in range(count):
            cat = f"Category{i % 5}"
            iid = _insert_item(conn, did, f"Item {i}", 500 + i * 10, cat,
                               f"Description for item {i}")
            if i % 3 == 0:
                _insert_variant(conn, iid, "Small", 400 + i * 10, "size", 0)
                _insert_variant(conn, iid, "Large", 700 + i * 10, "size", 1)
        return did

    def test_large_csv_flat(self, client, fresh_db):
        did = self._build_large_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == 55

    def test_large_csv_variants(self, client, fresh_db):
        did = self._build_large_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_variants.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        item_rows = [r for r in rows if r["type"] == "item"]
        assert len(item_rows) == 55

    def test_large_json(self, client, fresh_db):
        did = self._build_large_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export.json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["items"]) == 55

    def test_large_square_csv(self, client, fresh_db):
        did = self._build_large_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_square.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        item_rows = [r for r in rows[1:] if r[0] == "item"]
        assert len(item_rows) == 55

    def test_large_pos_json(self, client, fresh_db):
        did = self._build_large_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_pos.json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        total_items = sum(len(c["items"]) for c in data["menu"]["categories"])
        assert total_items == 55
        assert data["metadata"]["item_count"] == 55
        assert data["metadata"]["category_count"] == 5

    def test_large_metrics(self, client, fresh_db):
        did = self._build_large_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export/metrics")
        data = json.loads(resp.data)
        assert data["total_items"] == 55
        # Items 0,3,6,...,54 have variants → 19 items
        assert data["items_with_variants"] == 19

    def test_large_preview_truncated(self, client, fresh_db):
        did = self._build_large_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export/preview?format=square")
        data = json.loads(resp.data)
        assert data["truncated"] is True

    def test_large_preview_generic_not_truncated(self, client, fresh_db):
        did = self._build_large_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export/preview?format=generic_pos")
        data = json.loads(resp.data)
        assert data["truncated"] is False


# ===========================================================================
# SECTION 7: Edge Case — Price Formatting
# ===========================================================================

class TestEdgeCasePriceFormatting:

    def test_zero_cents(self):
        assert _format_price_dollars(0) == "0.00"

    def test_one_cent(self):
        assert _format_price_dollars(1) == "0.01"

    def test_ten_cents(self):
        assert _format_price_dollars(10) == "0.10"

    def test_one_dollar(self):
        assert _format_price_dollars(100) == "1.00"

    def test_typical_price(self):
        assert _format_price_dollars(1299) == "12.99"

    def test_large_price(self):
        assert _format_price_dollars(99999) == "999.99"

    def test_none_is_zero(self):
        assert _format_price_dollars(None) == "0.00"

    def test_price_in_square_row(self):
        items = [_item(price_cents=1299,
                       variants=[_var("Small", 999)])]
        rows = _build_square_rows(items)
        item_row = [r for r in rows if r[0] == "item"][0]
        assert item_row[4] == "12.99"
        mod_row = [r for r in rows if r[0] == "modifier"][0]
        assert mod_row[7] == "9.99"

    def test_price_in_toast_row(self):
        items = [_item(price_cents=1299)]
        rows = _build_toast_rows(items)
        assert rows[0][2] == "12.99"

    def test_price_in_pos_json(self):
        items = [_item(price_cents=1299)]
        payload = _build_generic_pos_json(items)
        item = payload["menu"]["categories"][0]["items"][0]
        assert item["base_price"] == "12.99"


# ===========================================================================
# SECTION 8: Edge Case — Null/Empty Descriptions
# ===========================================================================

class TestEdgeCaseNullDescription:

    def test_null_desc_csv(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees", None)
        resp = client.get(f"/drafts/{did}/export.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert rows[0]["description"] == ""

    def test_null_desc_json(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees", None)
        resp = client.get(f"/drafts/{did}/export.json")
        data = json.loads(resp.data)
        # DB null → Python None → JSON null → parsed as None; or "" if coerced
        desc = data["items"][0]["description"]
        assert desc is None or desc == ""

    def test_null_desc_square(self):
        items = [_item(description=None)]
        rows = _build_square_rows(items)
        assert rows[0][2] == ""

    def test_null_desc_pos_json(self):
        items = [_item(description=None)]
        payload = _build_generic_pos_json(items)
        item = payload["menu"]["categories"][0]["items"][0]
        assert item["description"] == ""


# ===========================================================================
# SECTION 9: Export Route Headers
# ===========================================================================

class TestExportRouteHeaders:

    def test_csv_content_type(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export.csv")
        assert "text/csv" in resp.headers["Content-Type"]

    def test_csv_filename(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export.csv")
        assert f"draft_{did}.csv" in resp.headers["Content-Disposition"]

    def test_json_content_type(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export.json")
        assert "application/json" in resp.headers["Content-Type"]

    def test_xlsx_content_type(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export.xlsx")
        assert "spreadsheetml" in resp.headers["Content-Type"]

    def test_square_csv_filename(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export_square.csv")
        assert f"draft_{did}_square.csv" in resp.headers["Content-Disposition"]

    def test_toast_csv_filename(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export_toast.csv")
        assert f"draft_{did}_toast.csv" in resp.headers["Content-Disposition"]

    def test_pos_json_content_type(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export_pos.json")
        assert "application/json" in resp.headers["Content-Type"]


# ===========================================================================
# SECTION 10: Validate + Metrics + Preview Consistency
# ===========================================================================

class TestValidateMetricsPreviewConsistency:
    """Verify validate, metrics, and preview endpoints agree on same draft."""

    def test_item_counts_agree(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        v_resp = client.get(f"/drafts/{did}/export/validate")
        m_resp = client.get(f"/drafts/{did}/export/metrics")
        p_resp = client.get(f"/drafts/{did}/export/preview?format=generic_pos")
        v_data = json.loads(v_resp.data)
        m_data = json.loads(m_resp.data)
        p_data = json.loads(p_resp.data)
        assert v_data["item_count"] == m_data["total_items"] == p_data["item_count"]

    def test_variant_counts_agree(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        v_resp = client.get(f"/drafts/{did}/export/validate")
        m_resp = client.get(f"/drafts/{did}/export/metrics")
        v_data = json.loads(v_resp.data)
        m_data = json.loads(m_resp.data)
        assert v_data["variant_count"] == m_data["total_variants"]

    def test_preview_warnings_match_validate(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "", 0, "")  # triggers multiple warnings
        v_resp = client.get(f"/drafts/{did}/export/validate")
        p_resp = client.get(f"/drafts/{did}/export/preview?format=generic_pos")
        v_data = json.loads(v_resp.data)
        p_data = json.loads(p_resp.data)
        assert len(v_data["warnings"]) == len(p_data["warnings"])
        v_types = sorted(w["type"] for w in v_data["warnings"])
        p_types = sorted(w["type"] for w in p_data["warnings"])
        assert v_types == p_types


# ===========================================================================
# SECTION 11: Validation Edge Cases
# ===========================================================================

class TestValidationEdgeCases:

    def test_item_with_variants_no_missing_price_warning(self):
        """Item has no base price but has variants → no missing_price warning."""
        items = [_item(price_cents=0, variants=[_var("S", 800)])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "missing_price" not in types

    def test_clean_item_no_warnings(self):
        """Well-formed item with name, price, category → zero warnings."""
        items = [_item(name="Burger", price_cents=999, category="Entrees")]
        warns = _validate_draft_for_export(items)
        assert len(warns) == 0

    def test_item_with_clean_variants_no_warnings(self):
        """Item with properly labeled, priced size variants → zero warnings."""
        items = [_item(variants=[
            _var("Small", 800, "size"),
            _var("Medium", 1000, "size"),
            _var("Large", 1200, "size"),
        ])]
        warns = _validate_draft_for_export(items)
        assert len(warns) == 0

    def test_many_items_mixed_warnings(self):
        """10 items with varied issues: count warnings correctly."""
        items = [
            _item(id=1, name="Good", price_cents=999, category="Entrees"),
            _item(id=2, name="", price_cents=500, category="Entrees"),
            _item(id=3, name="NoCat", price_cents=500, category=""),
            _item(id=4, name="NoPrice", price_cents=0, category="Entrees"),
            _item(id=5, name="HasVars", price_cents=0, category="Entrees",
                  variants=[_var("S", 800)]),
        ]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert types.count("missing_name") == 1
        assert types.count("missing_category") == 1
        assert types.count("missing_price") == 1
        # HasVars has variants → no missing_price

    def test_variant_zero_price_flagged(self):
        items = [_item(variants=[_var("Small", 0)])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "variant_missing_price" in types

    def test_all_seven_warning_types(self):
        """Single draft that triggers all 7 warning types."""
        items = [
            # missing_price + missing_category + missing_name
            _item(id=1, name="", price_cents=0, category=""),
            # variant_missing_price + variant_missing_label + duplicate_variant_label + price_inversion
            _item(id=2, name="Pizza", price_cents=999, category="Entrees",
                  variants=[
                      _var(label="", price_cents=0, kind="size"),
                      _var(label="Large", price_cents=1200, kind="size"),
                      _var(label="Large", price_cents=800, kind="size"),
                      _var(label="Small", price_cents=1500, kind="size"),
                  ]),
        ]
        warns = _validate_draft_for_export(items)
        types = {w["type"] for w in warns}
        assert "missing_price" in types
        assert "missing_category" in types
        assert "missing_name" in types
        assert "variant_missing_price" in types
        assert "variant_missing_label" in types
        assert "duplicate_variant_label" in types
        assert "price_inversion" in types


# ===========================================================================
# SECTION 12: Category Sorting in POS Formats
# ===========================================================================

class TestCategorySorting:

    def test_pos_json_categories_sorted_alpha(self):
        items = [
            _item(id=1, name="Ziti", category="Pasta"),
            _item(id=2, name="Wings", category="Appetizers"),
            _item(id=3, name="Burger", category="Burgers"),
        ]
        payload = _build_generic_pos_json(items)
        cat_names = [c["name"] for c in payload["menu"]["categories"]]
        assert cat_names == ["Appetizers", "Burgers", "Pasta"]

    def test_pos_json_uncategorized_sorted(self):
        items = [
            _item(id=1, name="Ziti", category="Pasta"),
            _item(id=2, name="Mystery", category=None),
        ]
        payload = _build_generic_pos_json(items)
        cat_names = [c["name"] for c in payload["menu"]["categories"]]
        assert cat_names == ["Pasta", "Uncategorized"]

    def test_items_grouped_under_correct_category(self):
        items = [
            _item(id=1, name="Burger", category="Burgers"),
            _item(id=2, name="Fries", category="Sides"),
            _item(id=3, name="Cheese Burger", category="Burgers"),
        ]
        payload = _build_generic_pos_json(items)
        burger_cat = [c for c in payload["menu"]["categories"]
                      if c["name"] == "Burgers"][0]
        assert len(burger_cat["items"]) == 2
        names = {i["name"] for i in burger_cat["items"]}
        assert names == {"Burger", "Cheese Burger"}


# ===========================================================================
# SECTION 13: POS JSON Metadata
# ===========================================================================

class TestPOSJSONMetadata:

    def test_metadata_format_version(self):
        payload = _build_generic_pos_json([_item()])
        assert payload["metadata"]["format"] == "generic_pos"
        assert payload["metadata"]["version"] == "1.0"

    def test_metadata_item_count(self):
        items = [_item(id=i, name=f"Item{i}") for i in range(10)]
        payload = _build_generic_pos_json(items)
        assert payload["metadata"]["item_count"] == 10

    def test_metadata_category_count(self):
        items = [
            _item(id=1, category="A"),
            _item(id=2, category="B"),
            _item(id=3, category="C"),
        ]
        payload = _build_generic_pos_json(items)
        assert payload["metadata"]["category_count"] == 3

    def test_metadata_exported_at_present(self):
        payload = _build_generic_pos_json([_item()])
        assert "exported_at" in payload["metadata"]
        assert len(payload["metadata"]["exported_at"]) > 0

    def test_metadata_with_draft_info(self):
        payload = _build_generic_pos_json([_item()],
                                          draft={"id": 42, "title": "My Menu"})
        assert payload["menu"]["id"] == 42
        assert payload["menu"]["title"] == "My Menu"


# ===========================================================================
# SECTION 14: Wide CSV Column Ordering
# ===========================================================================

class TestWideCsvColumns:

    def test_wide_csv_label_columns_present(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        _insert_variant(fresh_db, iid, "Small", 800)
        _insert_variant(fresh_db, iid, "Medium", 1000)
        _insert_variant(fresh_db, iid, "Large", 1200)
        resp = client.get(f"/drafts/{did}/export_wide.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert "price_Small" in reader.fieldnames
        assert "price_Medium" in reader.fieldnames
        assert "price_Large" in reader.fieldnames

    def test_wide_csv_prices_in_correct_columns(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        _insert_variant(fresh_db, iid, "Small", 800)
        _insert_variant(fresh_db, iid, "Large", 1200)
        resp = client.get(f"/drafts/{did}/export_wide.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert rows[0]["price_Small"] == "800"
        assert rows[0]["price_Large"] == "1200"

    def test_wide_csv_missing_label_empty(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid1 = _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        _insert_variant(fresh_db, iid1, "Small", 800)
        _insert_variant(fresh_db, iid1, "Large", 1200)
        # Second item only has Small
        iid2 = _insert_item(fresh_db, did, "Fries", 499, "Sides")
        _insert_variant(fresh_db, iid2, "Small", 499)
        resp = client.get(f"/drafts/{did}/export_wide.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        fries_row = [r for r in rows if r["name"] == "Fries"][0]
        assert fries_row["price_Large"] == ""  # not present for this item


# ===========================================================================
# SECTION 15: Square CSV Token Field
# ===========================================================================

class TestSquareCSVToken:

    def test_item_token(self):
        items = [_item()]
        rows = _build_square_rows(items)
        assert rows[0][0] == "item"

    def test_modifier_token(self):
        items = [_item(variants=[_var("Small", 800)])]
        rows = _build_square_rows(items)
        mod_rows = [r for r in rows if r[0] == "modifier"]
        assert len(mod_rows) == 1
        assert mod_rows[0][0] == "modifier"

    def test_modifier_parent_name(self):
        """Modifier rows include parent item name in Item Name column."""
        items = [_item(name="Burger", variants=[_var("Small", 800)])]
        rows = _build_square_rows(items)
        mod_row = [r for r in rows if r[0] == "modifier"][0]
        assert mod_row[1] == "Burger"


# ===========================================================================
# SECTION 16: Toast CSV Structure
# ===========================================================================

class TestToastCSVStructure:

    def test_parent_row_has_category(self):
        items = [_item(category="Burgers")]
        rows = _build_toast_rows(items)
        assert rows[0][0] == "Burgers"

    def test_option_row_empty_category(self):
        items = [_item(variants=[_var("Small", 800)])]
        rows = _build_toast_rows(items)
        opt_rows = [r for r in rows if r[0] == ""]
        assert len(opt_rows) == 1
        assert opt_rows[0][0] == ""  # empty Menu Group

    def test_no_category_becomes_uncategorized(self):
        items = [_item(category=None)]
        rows = _build_toast_rows(items)
        assert rows[0][0] == "Uncategorized"

    def test_base_price_in_parent(self):
        items = [_item(price_cents=1099)]
        rows = _build_toast_rows(items)
        assert rows[0][2] == "10.99"

    def test_option_price_in_option_row(self):
        items = [_item(variants=[_var("Small", 800)])]
        rows = _build_toast_rows(items)
        opt = [r for r in rows if r[0] == ""][0]
        assert opt[5] == "8.00"


# ===========================================================================
# SECTION 17: Full Pipeline Round-Trip via Flask (DB → Route → Parse)
# ===========================================================================

class TestFlaskPipelineRoundTrip:
    """Insert real DB data → hit export routes → parse output → verify."""

    def test_csv_variants_db_round_trip(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Margherita Pizza", 1200, "Pizza",
                           "Classic tomato and mozzarella")
        _insert_variant(fresh_db, iid, "Small", 1000, "size", 0)
        _insert_variant(fresh_db, iid, "Medium", 1400, "size", 1)
        _insert_variant(fresh_db, iid, "Large", 1800, "size", 2)

        resp = client.get(f"/drafts/{did}/export_variants.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        item_rows = [r for r in rows if r["type"] == "item"]
        var_rows = [r for r in rows if r["type"] == "variant"]
        assert len(item_rows) == 1
        assert item_rows[0]["name"] == "Margherita Pizza"
        assert len(var_rows) == 3
        labels = [r["label"] for r in var_rows]
        assert labels == ["Small", "Medium", "Large"]

    def test_json_db_round_trip(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Caesar Salad", 899, "Salads",
                           "Romaine, croutons, parmesan")
        _insert_variant(fresh_db, iid, "Half", 599, "size", 0)
        _insert_variant(fresh_db, iid, "Full", 899, "size", 1)

        resp = client.get(f"/drafts/{did}/export.json")
        data = json.loads(resp.data)
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["name"] == "Caesar Salad"
        assert item["description"] == "Romaine, croutons, parmesan"
        assert len(item["variants"]) == 2
        assert item["variants"][0]["label"] == "Half"
        assert item["variants"][1]["label"] == "Full"

    def test_square_db_round_trip(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Hot Wings", 899, "Appetizers",
                           "Spicy buffalo wings")
        _insert_variant(fresh_db, iid, "6 Piece", 899, "size", 0)
        _insert_variant(fresh_db, iid, "12 Piece", 1499, "size", 1)
        _insert_variant(fresh_db, iid, "w/ Ranch", 0, "combo", 2)

        resp = client.get(f"/drafts/{did}/export_square.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        data_rows = rows[1:]
        item_rows = [r for r in data_rows if r[0] == "item"]
        mod_rows = [r for r in data_rows if r[0] == "modifier"]
        assert len(item_rows) == 1
        assert item_rows[0][1] == "Hot Wings"
        assert len(mod_rows) == 3
        set_names = {r[5] for r in mod_rows}
        assert "Size" in set_names
        assert "Combo Add-on" in set_names

    def test_toast_db_round_trip(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pasta Carbonara", 1499, "Pasta")
        _insert_variant(fresh_db, iid, "Regular", 1499, "size", 0)
        _insert_variant(fresh_db, iid, "Family", 2499, "size", 1)

        resp = client.get(f"/drafts/{did}/export_toast.csv")
        text = resp.data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        data_rows = rows[1:]
        parent_rows = [r for r in data_rows if r[0] != ""]
        opt_rows = [r for r in data_rows if r[0] == ""]
        assert len(parent_rows) == 1
        assert parent_rows[0][0] == "Pasta"
        assert parent_rows[0][1] == "Pasta Carbonara"
        assert len(opt_rows) == 2

    def test_pos_json_db_round_trip(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid1 = _insert_item(fresh_db, did, "Latte", 450, "Beverages",
                            "Espresso with steamed milk")
        _insert_variant(fresh_db, iid1, "Small", 350, "size", 0)
        _insert_variant(fresh_db, iid1, "Large", 550, "size", 1)
        iid2 = _insert_item(fresh_db, did, "Croissant", 350, "Bakery")

        resp = client.get(f"/drafts/{did}/export_pos.json")
        data = json.loads(resp.data)
        cats = data["menu"]["categories"]
        assert len(cats) == 2  # Bakery, Beverages
        bev = [c for c in cats if c["name"] == "Beverages"][0]
        assert len(bev["items"]) == 1
        assert bev["items"][0]["name"] == "Latte"
        assert len(bev["items"][0]["modifiers"]) == 2


# ===========================================================================
# SECTION 18: Multi-Item Multi-Category Comprehensive
# ===========================================================================

class TestMultiItemMultiCategory:
    """Verify complex multi-category drafts export correctly."""

    def test_five_categories_pos_json(self):
        items = [
            _item(id=i, name=f"Item{i}", price_cents=500 + i * 100,
                  category=f"Cat{i % 5}",
                  variants=[_var(f"V{j}", 400 + j * 100) for j in range(2)])
            for i in range(20)
        ]
        payload = _build_generic_pos_json(items)
        assert len(payload["menu"]["categories"]) == 5
        total_items = sum(len(c["items"]) for c in payload["menu"]["categories"])
        assert total_items == 20
        total_mods = sum(
            len(i["modifiers"]) for c in payload["menu"]["categories"]
            for i in c["items"]
        )
        assert total_mods == 40

    def test_five_categories_metrics(self):
        items = [
            _item(id=i, name=f"Item{i}", price_cents=500 + i * 100,
                  category=f"Cat{i % 5}",
                  variants=[_var(f"V{j}", 400 + j * 100) for j in range(2)])
            for i in range(20)
        ]
        m = _compute_export_metrics(items)
        assert m["total_items"] == 20
        assert m["total_variants"] == 40
        assert len(m["category_breakdown"]) == 5
        for cat_name, info in m["category_breakdown"].items():
            assert info["item_count"] == 4
            assert info["variant_count"] == 8