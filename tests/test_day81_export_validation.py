"""
Day 81 -- Export Validation & Metrics tests.

Sprint 9.3, Day 81: Verifies export metrics computation, enhanced validation
with new warning types, round-trip verification for CSV/JSON/POS JSON formats,
and edge cases across all export formats.

Covers:
  Export metrics:
  - Empty items return all zeros
  - Item/variant counting
  - Variants by kind breakdown
  - Category breakdown with item and variant counts
  - Price statistics (min/max/avg excluding zeros)
  - Null category maps to "Uncategorized"

  Enhanced validation (new warning types):
  - variant_missing_label: empty/whitespace labels flagged
  - duplicate_variant_label: same label on same item (case-insensitive)
  - price_inversion: size variants with non-ascending prices
  - Backward compatibility: original 4 types unchanged
  - Non-size kinds not checked for price inversion

  Round-trip verification:
  - CSV variants format: export -> parse -> counts match
  - JSON format: export -> parse -> structure preserved
  - POS JSON format: export -> parse -> items/modifiers match
  - Unicode names survive round-trip
  - 10+ variants round-trip correctly

  Edge cases:
  - Empty draft: all formats return valid empty output
  - Item with 10+ variants: all POS formats handle correctly
  - Unicode names: all formats preserve correctly
  - Item with variants but no base price: POS formats handle
  - All 5 variant kinds: correct kind-to-label mapping in all formats

  Metrics route integration:
  - Returns JSON with correct structure
  - Counts match database contents
  - Empty draft returns all zeros
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 71-80 tests)
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
# Helpers: build in-memory item dicts (for pure-function tests)
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
    return {"label": label, "price_cents": price_cents, "kind": kind, "position": position}


# ===========================================================================
# SECTION 1: _compute_export_metrics
# ===========================================================================

class TestExportMetrics:
    def test_empty_items(self):
        m = _compute_export_metrics([])
        assert m["total_items"] == 0
        assert m["items_with_variants"] == 0
        assert m["items_without_variants"] == 0
        assert m["total_variants"] == 0
        assert m["variants_by_kind"] == {}
        assert m["category_breakdown"] == {}
        assert m["price_stats"]["min_cents"] == 0
        assert m["price_stats"]["max_cents"] == 0
        assert m["price_stats"]["avg_cents"] == 0
        assert m["price_stats"]["price_count"] == 0

    def test_single_item_no_variants(self):
        m = _compute_export_metrics([_item(price_cents=1000)])
        assert m["total_items"] == 1
        assert m["items_with_variants"] == 0
        assert m["items_without_variants"] == 1
        assert m["total_variants"] == 0

    def test_single_item_with_variants(self):
        items = [_item(variants=[_var("S", 800), _var("L", 1200)])]
        m = _compute_export_metrics(items)
        assert m["total_items"] == 1
        assert m["items_with_variants"] == 1
        assert m["items_without_variants"] == 0
        assert m["total_variants"] == 2

    def test_variants_by_kind_single_kind(self):
        items = [_item(variants=[_var("S", 800, "size"), _var("L", 1200, "size")])]
        m = _compute_export_metrics(items)
        assert m["variants_by_kind"] == {"size": 2}

    def test_variants_by_kind_multiple_kinds(self):
        items = [_item(variants=[
            _var("S", 800, "size"),
            _var("w/ Fries", 200, "combo"),
            _var("Spicy", 0, "flavor"),
        ])]
        m = _compute_export_metrics(items)
        assert m["variants_by_kind"]["size"] == 1
        assert m["variants_by_kind"]["combo"] == 1
        assert m["variants_by_kind"]["flavor"] == 1

    def test_category_breakdown_single(self):
        items = [_item(category="Burgers"), _item(id=2, name="Fries", category="Burgers")]
        m = _compute_export_metrics(items)
        assert m["category_breakdown"]["Burgers"]["item_count"] == 2
        assert m["category_breakdown"]["Burgers"]["variant_count"] == 0

    def test_category_breakdown_multiple(self):
        items = [
            _item(category="Burgers"),
            _item(id=2, name="Soda", category="Drinks", price_cents=300),
        ]
        m = _compute_export_metrics(items)
        assert "Burgers" in m["category_breakdown"]
        assert "Drinks" in m["category_breakdown"]
        assert m["category_breakdown"]["Burgers"]["item_count"] == 1
        assert m["category_breakdown"]["Drinks"]["item_count"] == 1

    def test_category_breakdown_null_maps_to_uncategorized(self):
        items = [_item(category=None), _item(id=2, name="B", category="")]
        m = _compute_export_metrics(items)
        assert "Uncategorized" in m["category_breakdown"]
        assert m["category_breakdown"]["Uncategorized"]["item_count"] == 2

    def test_category_breakdown_variant_counts(self):
        items = [_item(category="Burgers", variants=[_var("S", 800), _var("L", 1200)])]
        m = _compute_export_metrics(items)
        assert m["category_breakdown"]["Burgers"]["variant_count"] == 2

    def test_price_stats_basic(self):
        items = [_item(price_cents=500), _item(id=2, name="B", price_cents=1500)]
        m = _compute_export_metrics(items)
        assert m["price_stats"]["min_cents"] == 500
        assert m["price_stats"]["max_cents"] == 1500
        assert m["price_stats"]["avg_cents"] == 1000
        assert m["price_stats"]["price_count"] == 2

    def test_price_stats_includes_variants(self):
        items = [_item(price_cents=1000, variants=[_var("S", 800), _var("L", 1200)])]
        m = _compute_export_metrics(items)
        # 3 prices: 1000 (base), 800, 1200
        assert m["price_stats"]["min_cents"] == 800
        assert m["price_stats"]["max_cents"] == 1200
        assert m["price_stats"]["price_count"] == 3

    def test_price_stats_excludes_zero(self):
        items = [_item(price_cents=0, variants=[_var("S", 0), _var("L", 1200)])]
        m = _compute_export_metrics(items)
        assert m["price_stats"]["min_cents"] == 1200
        assert m["price_stats"]["max_cents"] == 1200
        assert m["price_stats"]["price_count"] == 1

    def test_all_five_kinds_counted(self):
        items = [_item(variants=[
            _var("S", 800, "size"),
            _var("w/ Fries", 200, "combo"),
            _var("Spicy", 0, "flavor"),
            _var("Grilled", 0, "style"),
            _var("Extra Cheese", 150, "other"),
        ])]
        m = _compute_export_metrics(items)
        assert len(m["variants_by_kind"]) == 5
        for k in ("size", "combo", "flavor", "style", "other"):
            assert m["variants_by_kind"][k] == 1


# ===========================================================================
# SECTION 2: Enhanced Validation — new warning types
# ===========================================================================

class TestEnhancedValidation:
    def test_variant_missing_label_empty(self):
        items = [_item(variants=[_var(label="", price_cents=800)])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "variant_missing_label" in types

    def test_variant_missing_label_whitespace(self):
        items = [_item(variants=[_var(label="   ", price_cents=800)])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "variant_missing_label" in types

    def test_variant_with_label_no_warning(self):
        items = [_item(variants=[_var(label="Small", price_cents=800)])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "variant_missing_label" not in types

    def test_duplicate_variant_label_same_item(self):
        items = [_item(variants=[_var("Small", 800), _var("Small", 1000)])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "duplicate_variant_label" in types

    def test_duplicate_variant_label_case_insensitive(self):
        items = [_item(variants=[_var("Small", 800), _var("small", 1000)])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "duplicate_variant_label" in types

    def test_no_duplicate_across_items(self):
        items = [
            _item(id=1, variants=[_var("Small", 800)]),
            _item(id=2, name="Fries", variants=[_var("Small", 500)]),
        ]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "duplicate_variant_label" not in types

    def test_price_inversion_detected(self):
        # Small=1200, Large=800 — inverted
        items = [_item(variants=[_var("Small", 1200, "size"), _var("Large", 800, "size")])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "price_inversion" in types

    def test_price_ascending_no_warning(self):
        items = [_item(variants=[
            _var("Small", 800, "size"),
            _var("Medium", 1000, "size"),
            _var("Large", 1200, "size"),
        ])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "price_inversion" not in types

    def test_price_inversion_non_size_ignored(self):
        # combo variants with "inverted" prices should NOT trigger
        items = [_item(variants=[
            _var("w/ Fries", 1200, "combo"),
            _var("w/ Salad", 800, "combo"),
        ])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "price_inversion" not in types

    def test_price_inversion_single_size_no_warning(self):
        items = [_item(variants=[_var("Small", 800, "size")])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "price_inversion" not in types

    def test_existing_warnings_preserved(self):
        """Original 4 warning types still work."""
        items = [_item(name="", price_cents=0, category="", variants=[])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "missing_price" in types
        assert "missing_category" in types
        assert "missing_name" in types

    def test_multiple_new_warnings_per_item(self):
        """Missing label + duplicate label on same item."""
        items = [_item(variants=[
            _var(label="", price_cents=800),
            _var(label="Small", price_cents=900),
            _var(label="Small", price_cents=1000),
        ])]
        warns = _validate_draft_for_export(items)
        types = [w["type"] for w in warns]
        assert "variant_missing_label" in types
        assert "duplicate_variant_label" in types


# ===========================================================================
# SECTION 3: CSV Round-Trip Verification
# ===========================================================================

class TestRoundTripCSV:
    def test_empty(self):
        result = _verify_csv_round_trip([])
        assert result["ok"] is True
        assert result["expected_items"] == 0
        assert result["actual_items"] == 0

    def test_items_only(self):
        items = [_item(id=1), _item(id=2, name="Fries", price_cents=500)]
        result = _verify_csv_round_trip(items)
        assert result["ok"] is True
        assert result["actual_items"] == 2
        assert result["actual_variants"] == 0

    def test_items_with_variants(self):
        items = [_item(variants=[_var("S", 800), _var("L", 1200)])]
        result = _verify_csv_round_trip(items)
        assert result["ok"] is True
        assert result["actual_items"] == 1
        assert result["actual_variants"] == 2

    def test_mixed(self):
        items = [
            _item(id=1, variants=[_var("S", 800)]),
            _item(id=2, name="Fries", price_cents=500),
        ]
        result = _verify_csv_round_trip(items)
        assert result["ok"] is True
        assert result["actual_items"] == 2
        assert result["actual_variants"] == 1

    def test_many_variants(self):
        variants = [_var(f"Size{i}", 500 + i * 100) for i in range(12)]
        items = [_item(variants=variants)]
        result = _verify_csv_round_trip(items)
        assert result["ok"] is True
        assert result["actual_variants"] == 12

    def test_special_chars(self):
        items = [_item(name="Caf\u00e9 Latt\u00e9", variants=[_var("\u5c0f", 800)])]
        result = _verify_csv_round_trip(items)
        assert result["ok"] is True


# ===========================================================================
# SECTION 4: JSON Round-Trip Verification
# ===========================================================================

class TestRoundTripJSON:
    def test_empty(self):
        result = _verify_json_round_trip([])
        assert result["ok"] is True

    def test_with_variants(self):
        items = [_item(variants=[_var("S", 800), _var("L", 1200)])]
        result = _verify_json_round_trip(items)
        assert result["ok"] is True
        assert result["actual_variants"] == 2

    def test_mixed(self):
        items = [
            _item(id=1, variants=[_var("S", 800)]),
            _item(id=2, name="Fries"),
        ]
        result = _verify_json_round_trip(items)
        assert result["ok"] is True
        assert result["actual_items"] == 2

    def test_all_kinds(self):
        items = [_item(variants=[
            _var("S", 800, "size"),
            _var("Fries", 200, "combo"),
            _var("Spicy", 0, "flavor"),
            _var("Grilled", 0, "style"),
            _var("Extra", 150, "other"),
        ])]
        result = _verify_json_round_trip(items)
        assert result["ok"] is True
        assert result["actual_variants"] == 5

    def test_unicode(self):
        items = [_item(name="\u62c9\u9762", variants=[_var("\u5927\u4efd", 1200)])]
        result = _verify_json_round_trip(items)
        assert result["ok"] is True


# ===========================================================================
# SECTION 5: POS JSON Round-Trip Verification
# ===========================================================================

class TestRoundTripPOSJSON:
    def test_empty(self):
        result = _verify_pos_json_round_trip([])
        assert result["ok"] is True
        assert result["actual_items"] == 0

    def test_with_variants(self):
        items = [_item(variants=[_var("S", 800), _var("L", 1200)])]
        result = _verify_pos_json_round_trip(items)
        assert result["ok"] is True
        assert result["actual_modifiers"] == 2

    def test_metadata(self):
        items = [_item()]
        result = _verify_pos_json_round_trip(items, draft={"id": 99, "title": "Test"})
        meta = result["metadata"]
        assert meta["format"] == "generic_pos"
        assert meta["version"] == "1.0"
        assert meta["item_count"] == 1

    def test_categories_distributed(self):
        items = [
            _item(id=1, category="Burgers"),
            _item(id=2, name="Soda", category="Drinks", price_cents=300),
        ]
        result = _verify_pos_json_round_trip(items)
        assert result["ok"] is True
        assert result["actual_items"] == 2

    def test_large_draft(self):
        items = [_item(id=i, name=f"Item {i}", price_cents=500 + i * 50,
                        category=f"Cat{i % 3}",
                        variants=[_var(f"S{i}", 400 + i * 50)])
                 for i in range(25)]
        result = _verify_pos_json_round_trip(items)
        assert result["ok"] is True
        assert result["actual_items"] == 25
        assert result["actual_modifiers"] == 25


# ===========================================================================
# SECTION 6: Edge Case — Empty Draft (all 9 export formats)
# ===========================================================================

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


class TestEdgeCaseEmptyDraft:
    def test_empty_csv_flat(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        lines = [l for l in text.strip().split("\n") if l.strip()]
        assert len(lines) == 1  # header only

    def test_empty_csv_variants(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_variants.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        lines = [l for l in text.strip().split("\n") if l.strip()]
        assert len(lines) == 1  # header only

    def test_empty_csv_wide(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_wide.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        lines = [l for l in text.strip().split("\n") if l.strip()]
        assert len(lines) == 1  # header only

    def test_empty_json(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export.json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["items"] == []

    def test_empty_square_csv(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_square.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        lines = [l for l in text.strip().split("\n") if l.strip()]
        assert len(lines) == 1  # header only

    def test_empty_toast_csv(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_toast.csv")
        assert resp.status_code == 200
        text = resp.data.decode("utf-8-sig")
        lines = [l for l in text.strip().split("\n") if l.strip()]
        assert len(lines) == 1  # header only

    def test_empty_pos_json(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_pos.json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["menu"]["categories"] == []

    def test_empty_validate(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export/validate")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["item_count"] == 0
        assert data["warnings"] == []

    def test_empty_metrics(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export/metrics")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["total_items"] == 0
        assert data["total_variants"] == 0
        assert data["variants_by_kind"] == {}


# ===========================================================================
# SECTION 7: Edge Case — Item with 10+ variants
# ===========================================================================

class TestEdgeCaseManyVariants:
    def _build_many_variant_items(self):
        variants = [_var(f"Size{i}", 500 + i * 100, "size", i) for i in range(12)]
        return [_item(variants=variants)]

    def test_ten_plus_square(self):
        items = self._build_many_variant_items()
        rows = _build_square_rows(items)
        modifier_rows = [r for r in rows if r[0] == "modifier"]
        assert len(modifier_rows) == 12

    def test_ten_plus_toast(self):
        items = self._build_many_variant_items()
        rows = _build_toast_rows(items)
        # Option rows have empty Menu Group
        option_rows = [r for r in rows if r[0] == ""]
        assert len(option_rows) == 12

    def test_ten_plus_pos_json(self):
        items = self._build_many_variant_items()
        payload = _build_generic_pos_json(items)
        cat_items = payload["menu"]["categories"][0]["items"]
        assert len(cat_items[0]["modifiers"]) == 12

    def test_ten_plus_csv_subrow(self):
        items = self._build_many_variant_items()
        result = _verify_csv_round_trip(items)
        assert result["ok"] is True
        assert result["actual_variants"] == 12


# ===========================================================================
# SECTION 8: Edge Case — Unicode names
# ===========================================================================

class TestEdgeCaseUnicode:
    def test_unicode_square_csv(self):
        items = [_item(name="\u62c9\u9762", category="\u4e3b\u98df",
                       variants=[_var("\u5c0f\u4efd", 800), _var("\u5927\u4efd", 1200)])]
        rows = _build_square_rows(items)
        item_row = [r for r in rows if r[0] == "item"][0]
        assert "\u62c9\u9762" in item_row[1]

    def test_unicode_toast_csv(self):
        items = [_item(name="\u62c9\u9762", category="\u4e3b\u98df",
                       variants=[_var("\u5c0f\u4efd", 800)])]
        rows = _build_toast_rows(items)
        # Parent row has category + name
        parent = rows[0]
        assert parent[0] == "\u4e3b\u98df"
        assert parent[1] == "\u62c9\u9762"

    def test_unicode_json_export(self):
        items = [_item(name="\u62c9\u9762", variants=[_var("\u5c0f\u4efd", 800)])]
        result = _verify_json_round_trip(items)
        assert result["ok"] is True

    def test_unicode_pos_json(self):
        items = [_item(name="\u62c9\u9762", category="\u4e3b\u98df",
                       variants=[_var("\u5c0f\u4efd", 800)])]
        payload = _build_generic_pos_json(items)
        cat = payload["menu"]["categories"][0]
        assert cat["name"] == "\u4e3b\u98df"
        assert cat["items"][0]["name"] == "\u62c9\u9762"


# ===========================================================================
# SECTION 9: Edge Case — Variant-only items (no base price)
# ===========================================================================

class TestEdgeCaseVariantOnly:
    def test_variant_only_square(self):
        items = [_item(price_cents=0, variants=[_var("S", 800), _var("L", 1200)])]
        rows = _build_square_rows(items)
        item_row = [r for r in rows if r[0] == "item"][0]
        assert item_row[4] == "0.00"  # base price = 0.00
        mod_rows = [r for r in rows if r[0] == "modifier"]
        assert len(mod_rows) == 2
        prices = [r[7] for r in mod_rows]
        assert "8.00" in prices
        assert "12.00" in prices

    def test_variant_only_toast(self):
        items = [_item(price_cents=0, variants=[_var("S", 800), _var("L", 1200)])]
        rows = _build_toast_rows(items)
        parent = rows[0]
        assert parent[2] == "0.00"  # base price
        option_rows = [r for r in rows if r[0] == ""]
        assert len(option_rows) == 2

    def test_variant_only_pos_json(self):
        items = [_item(price_cents=0, variants=[_var("S", 800), _var("L", 1200)])]
        payload = _build_generic_pos_json(items)
        item = payload["menu"]["categories"][0]["items"][0]
        assert item["base_price"] == "0.00"
        assert len(item["modifiers"]) == 2
        mod_prices = [m["price"] for m in item["modifiers"]]
        assert "8.00" in mod_prices


# ===========================================================================
# SECTION 10: Edge Case — All 5 variant kinds
# ===========================================================================

KIND_LABEL_MAP = {
    "size": "Size",
    "combo": "Combo Add-on",
    "flavor": "Flavor",
    "style": "Style",
    "other": "Option",
}


class TestEdgeCaseAllKinds:
    def _items_all_kinds(self):
        return [_item(variants=[
            _var("S", 800, "size", 0),
            _var("w/ Fries", 200, "combo", 1),
            _var("Spicy", 0, "flavor", 2),
            _var("Grilled", 0, "style", 3),
            _var("Extra Cheese", 150, "other", 4),
        ])]

    def test_all_kinds_square_mapping(self):
        items = self._items_all_kinds()
        rows = _build_square_rows(items)
        mod_rows = [r for r in rows if r[0] == "modifier"]
        set_names = {r[5] for r in mod_rows}
        for expected in KIND_LABEL_MAP.values():
            assert expected in set_names, f"Missing modifier set: {expected}"

    def test_all_kinds_toast_mapping(self):
        items = self._items_all_kinds()
        rows = _build_toast_rows(items)
        option_rows = [r for r in rows if r[0] == ""]
        group_names = {r[3] for r in option_rows}
        for expected in KIND_LABEL_MAP.values():
            assert expected in group_names, f"Missing option group: {expected}"

    def test_all_kinds_metrics(self):
        items = self._items_all_kinds()
        m = _compute_export_metrics(items)
        assert len(m["variants_by_kind"]) == 5
        for k in ("size", "combo", "flavor", "style", "other"):
            assert k in m["variants_by_kind"]


# ===========================================================================
# SECTION 11: Metrics Route Integration
# ===========================================================================

class TestMetricsRoute:
    def test_returns_json(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.get(f"/drafts/{did}/export/metrics")
        assert resp.status_code == 200
        assert resp.content_type.startswith("application/json")

    def test_correct_counts(self, client, fresh_db):
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        _insert_variant(fresh_db, iid, "Small", 800)
        _insert_variant(fresh_db, iid, "Large", 1200)
        _insert_item(fresh_db, did, "Fries", 500, "Sides")
        resp = client.get(f"/drafts/{did}/export/metrics")
        data = json.loads(resp.data)
        assert data["total_items"] == 2
        assert data["items_with_variants"] == 1
        assert data["total_variants"] == 2
        assert data["draft_id"] == did

    def test_empty_draft(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export/metrics")
        data = json.loads(resp.data)
        assert data["total_items"] == 0
        assert data["total_variants"] == 0
        assert data["price_stats"]["price_count"] == 0
