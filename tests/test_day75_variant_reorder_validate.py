"""
Day 75 — Inline Variant Validation & Reorder tests.

Sprint 9.2, Day 75: Verifies the contract validation for _variants, position
reorder persistence, template preset round-trips, and price-order validation
logic through the save/reload cycle.

Covers:
  Contract validation (_variants schema):
  - Valid _variants pass validation
  - _variants must be a list
  - Variant entry must be an object
  - Variant label must be a string
  - Variant label must not be empty
  - Variant price_cents must be int-like
  - Variant kind must be valid (size/combo/flavor/style/other)
  - Variant kind rejects invalid values
  - Variant position must be int-like
  - Variant id must be int-like or null
  - Multiple variants validated
  - Empty _variants list is valid
  - Items without _variants pass validation (backward compat)

  Contract validation (deleted_variant_ids):
  - Valid deleted_variant_ids pass
  - deleted_variant_ids must be a list
  - Non-integer entries rejected
  - Empty list is valid

  Position reorder persistence:
  - Save with reordered positions → reload preserves order
  - Position 0-indexed after reorder
  - Reorder via upsert replace strategy
  - Position gaps normalized on save

  Template preset round-trips:
  - S/M/L template creates 3 size variants
  - Half/Whole template creates 2 size variants
  - S/M/L/XL template creates 4 size variants
  - Combo template creates combo-kind variants
  - Template replaces existing variants (simulated)
  - Template preserves parent item data

  Price-order validation helpers:
  - SIZE_ORDINALS mapping sanity (S < M < L)
  - Inch-based ordinal ordering
  - Half/Whole ordering
  - Slice/Pie ordering
  - Single/Double/Triple ordering
  - Unknown labels return None ordinal
  - Price inversion detection logic
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 71-74 tests)
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


def _create_draft(conn, title="Test Draft") -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO drafts (title, status, created_at, updated_at) VALUES (?, 'editing', datetime('now'), datetime('now'))",
        (title,),
    )
    conn.commit()
    return int(cur.lastrowid)


# ===========================================================================
# Tests: Contract validation for _variants
# ===========================================================================
class TestContractVariantsValidation:
    """Tests for validate_draft_payload() _variants schema checks."""

    def _make_payload(self, items=None, **kwargs):
        base = {"draft_id": 1, "items": items or []}
        base.update(kwargs)
        return base

    def test_valid_variants_pass(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "price_cents": 1000,
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                {"label": "Large", "price_cents": 1200, "kind": "size", "position": 1},
            ]
        }])
        ok, err = validate_draft_payload(payload)
        assert ok, f"Expected valid but got: {err}"

    def test_variants_must_be_list(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": "not a list"
        }])
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "_variants must be a list" in err

    def test_variant_must_be_object(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": ["not an object"]
        }])
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "must be an object" in err

    def test_variant_label_required(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [{"price_cents": 800}]
        }])
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "label must be a string" in err

    def test_variant_label_must_be_string(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [{"label": 123, "price_cents": 800}]
        }])
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "label must be a string" in err

    def test_variant_label_must_not_be_empty(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [{"label": "  ", "price_cents": 800}]
        }])
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "must not be empty" in err

    def test_variant_price_must_be_int(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [{"label": "Small", "price_cents": "abc"}]
        }])
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "price_cents must be an integer" in err

    def test_variant_kind_valid_values(self, fresh_db):
        from portal.contracts import validate_draft_payload
        for kind in ["size", "combo", "flavor", "style", "other"]:
            payload = self._make_payload(items=[{
                "name": "Pizza",
                "_variants": [{"label": "X", "price_cents": 800, "kind": kind}]
            }])
            ok, err = validate_draft_payload(payload)
            assert ok, f"Kind '{kind}' should be valid but got: {err}"

    def test_variant_kind_rejects_invalid(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [{"label": "X", "price_cents": 800, "kind": "INVALID"}]
        }])
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "kind must be one of" in err

    def test_variant_position_must_be_int(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [{"label": "S", "price_cents": 800, "position": "abc"}]
        }])
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "position must be an integer" in err

    def test_variant_id_must_be_int_or_null(self, fresh_db):
        from portal.contracts import validate_draft_payload
        # null is OK
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [{"id": None, "label": "S", "price_cents": 800}]
        }])
        ok, err = validate_draft_payload(payload)
        assert ok, f"id=null should be valid but got: {err}"

        # int is OK
        payload2 = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [{"id": 42, "label": "S", "price_cents": 800}]
        }])
        ok2, err2 = validate_draft_payload(payload2)
        assert ok2, f"id=42 should be valid but got: {err2}"

        # string is NOT OK
        payload3 = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [{"id": "abc", "label": "S", "price_cents": 800}]
        }])
        ok3, err3 = validate_draft_payload(payload3)
        assert not ok3
        assert "id must be an integer" in err3

    def test_multiple_variants_validated(self, fresh_db):
        from portal.contracts import validate_draft_payload
        # First valid, second invalid
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size"},
                {"label": "", "price_cents": 1200},  # empty label
            ]
        }])
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "_variants[1]" in err

    def test_empty_variants_list_valid(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": []
        }])
        ok, err = validate_draft_payload(payload)
        assert ok, f"Empty _variants should be valid but got: {err}"

    def test_no_variants_key_valid(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "price_cents": 1000,
        }])
        ok, err = validate_draft_payload(payload)
        assert ok, f"No _variants key should be valid but got: {err}"

    def test_variant_price_cents_optional(self, fresh_db):
        """price_cents can be omitted (defaults downstream to 0)."""
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [{"label": "Small"}]
        }])
        ok, err = validate_draft_payload(payload)
        assert ok, f"Omitted price_cents should be valid but got: {err}"

    def test_variant_kind_optional(self, fresh_db):
        """kind can be omitted (defaults downstream to 'size')."""
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [{"label": "Small", "price_cents": 800}]
        }])
        ok, err = validate_draft_payload(payload)
        assert ok, f"Omitted kind should be valid but got: {err}"

    def test_variant_kind_null_valid(self, fresh_db):
        """kind=null is valid (defaults to 'size' downstream)."""
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(items=[{
            "name": "Pizza",
            "_variants": [{"label": "Small", "price_cents": 800, "kind": None}]
        }])
        ok, err = validate_draft_payload(payload)
        assert ok, f"kind=null should be valid but got: {err}"


# ===========================================================================
# Tests: Contract validation for deleted_variant_ids
# ===========================================================================
class TestContractDeletedVariantIds:
    """Tests for validate_draft_payload() deleted_variant_ids checks."""

    def _make_payload(self, **kwargs):
        base = {"draft_id": 1, "items": []}
        base.update(kwargs)
        return base

    def test_valid_deleted_variant_ids(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(deleted_variant_ids=[1, 2, 3])
        ok, err = validate_draft_payload(payload)
        assert ok, f"Expected valid but got: {err}"

    def test_deleted_variant_ids_must_be_list(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(deleted_variant_ids="not a list")
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "deleted_variant_ids must be a list" in err

    def test_deleted_variant_ids_non_int_rejected(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(deleted_variant_ids=[1, "abc", 3])
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "deleted_variant_ids[1]" in err

    def test_deleted_variant_ids_empty_valid(self, fresh_db):
        from portal.contracts import validate_draft_payload
        payload = self._make_payload(deleted_variant_ids=[])
        ok, err = validate_draft_payload(payload)
        assert ok, f"Empty deleted_variant_ids should be valid but got: {err}"

    def test_no_deleted_variant_ids_key_valid(self, fresh_db):
        """Omitting deleted_variant_ids entirely is fine."""
        from portal.contracts import validate_draft_payload
        payload = self._make_payload()
        ok, err = validate_draft_payload(payload)
        assert ok, f"No deleted_variant_ids should be valid but got: {err}"


# ===========================================================================
# Tests: Position reorder persistence
# ===========================================================================
class TestPositionReorderPersistence:
    """Tests that variant position changes persist through save/reload."""

    def test_reorder_positions_persist(self, fresh_db):
        """Simulates: user reorders S,M,L to L,M,S via up/down buttons → save → reload."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)

        # Initial save with S(0), M(1), L(2)
        items = [{
            "name": "Pizza",
            "price_cents": 800,
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                {"label": "Medium", "price_cents": 1000, "kind": "size", "position": 1},
                {"label": "Large", "price_cents": 1200, "kind": "size", "position": 2},
            ]
        }]
        result = upsert_draft_items(d, items)
        item_id = result["inserted_ids"][0]

        # User reorders to L(0), M(1), S(2) via up/down
        items2 = [{
            "id": item_id,
            "name": "Pizza",
            "price_cents": 800,
            "_variants": [
                {"label": "Large", "price_cents": 1200, "kind": "size", "position": 0},
                {"label": "Medium", "price_cents": 1000, "kind": "size", "position": 1},
                {"label": "Small", "price_cents": 800, "kind": "size", "position": 2},
            ]
        }]
        upsert_draft_items(d, items2)

        loaded = get_draft_items(d)
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert labels == ["Large", "Medium", "Small"]

    def test_position_zero_indexed(self, fresh_db):
        """Positions are 0-indexed as collected from DOM order."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Burger",
            "price_cents": 999,
            "_variants": [
                {"label": "Single", "price_cents": 999, "kind": "size", "position": 0},
                {"label": "Double", "price_cents": 1399, "kind": "size", "position": 1},
                {"label": "Triple", "price_cents": 1799, "kind": "size", "position": 2},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        positions = [v["position"] for v in loaded[0]["variants"]]
        assert positions == [0, 1, 2]

    def test_reorder_via_upsert_replace(self, fresh_db):
        """Reorder uses the delete-all + re-insert strategy on update."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)

        items = [{
            "name": "Wings",
            "price_cents": 899,
            "_variants": [
                {"label": "6pc", "price_cents": 899, "kind": "size", "position": 0},
                {"label": "12pc", "price_cents": 1499, "kind": "size", "position": 1},
                {"label": "24pc", "price_cents": 2499, "kind": "size", "position": 2},
            ]
        }]
        result = upsert_draft_items(d, items)
        item_id = result["inserted_ids"][0]

        # Move 24pc to first position
        items2 = [{
            "id": item_id,
            "name": "Wings",
            "price_cents": 899,
            "_variants": [
                {"label": "24pc", "price_cents": 2499, "kind": "size", "position": 0},
                {"label": "6pc", "price_cents": 899, "kind": "size", "position": 1},
                {"label": "12pc", "price_cents": 1499, "kind": "size", "position": 2},
            ]
        }]
        upsert_draft_items(d, items2)

        loaded = get_draft_items(d)
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert labels == ["24pc", "6pc", "12pc"]

    def test_position_gaps_normalized(self, fresh_db):
        """Non-contiguous positions still sort correctly."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Test",
            "price_cents": 500,
            "_variants": [
                {"label": "A", "price_cents": 500, "kind": "size", "position": 5},
                {"label": "B", "price_cents": 600, "kind": "size", "position": 0},
                {"label": "C", "price_cents": 700, "kind": "size", "position": 10},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert labels == ["B", "A", "C"]  # sorted by position: 0, 5, 10

    def test_swap_two_adjacent(self, fresh_db):
        """Simulates swapping two adjacent variants (move down)."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Taco",
            "price_cents": 300,
            "_variants": [
                {"label": "Chicken", "price_cents": 300, "kind": "flavor", "position": 0},
                {"label": "Beef", "price_cents": 350, "kind": "flavor", "position": 1},
            ]
        }]
        result = upsert_draft_items(d, items)
        item_id = result["inserted_ids"][0]

        # Swap: Beef to position 0, Chicken to position 1
        items2 = [{
            "id": item_id,
            "name": "Taco",
            "price_cents": 300,
            "_variants": [
                {"label": "Beef", "price_cents": 350, "kind": "flavor", "position": 0},
                {"label": "Chicken", "price_cents": 300, "kind": "flavor", "position": 1},
            ]
        }]
        upsert_draft_items(d, items2)

        loaded = get_draft_items(d)
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert labels == ["Beef", "Chicken"]


# ===========================================================================
# Tests: Template preset round-trips
# ===========================================================================
class TestTemplatePresetRoundTrips:
    """Tests simulating variant template preset application through save/reload."""

    def test_sml_template(self, fresh_db):
        """S/M/L template creates 3 size variants."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Pizza",
            "price_cents": 0,
            "category": "Pizza",
            "_variants": [
                {"label": "Small", "price_cents": 0, "kind": "size", "position": 0},
                {"label": "Medium", "price_cents": 0, "kind": "size", "position": 1},
                {"label": "Large", "price_cents": 0, "kind": "size", "position": 2},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert len(loaded[0]["variants"]) == 3
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert labels == ["Small", "Medium", "Large"]
        assert all(v["kind"] == "size" for v in loaded[0]["variants"])

    def test_half_whole_template(self, fresh_db):
        """Half/Whole template creates 2 size variants."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Calzone",
            "price_cents": 0,
            "_variants": [
                {"label": "Half", "price_cents": 0, "kind": "size", "position": 0},
                {"label": "Whole", "price_cents": 0, "kind": "size", "position": 1},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert len(loaded[0]["variants"]) == 2
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert labels == ["Half", "Whole"]

    def test_sml_xl_template(self, fresh_db):
        """S/M/L/XL template creates 4 size variants."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Sub",
            "price_cents": 0,
            "_variants": [
                {"label": "Small", "price_cents": 0, "kind": "size", "position": 0},
                {"label": "Medium", "price_cents": 0, "kind": "size", "position": 1},
                {"label": "Large", "price_cents": 0, "kind": "size", "position": 2},
                {"label": "Extra Large", "price_cents": 0, "kind": "size", "position": 3},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert len(loaded[0]["variants"]) == 4

    def test_combo_template(self, fresh_db):
        """Combo template creates combo-kind variants."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Chicken Tenders",
            "price_cents": 899,
            "_variants": [
                {"label": "W/Fries", "price_cents": 0, "kind": "combo", "position": 0},
                {"label": "W/Salad", "price_cents": 0, "kind": "combo", "position": 1},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert len(loaded[0]["variants"]) == 2
        assert all(v["kind"] == "combo" for v in loaded[0]["variants"])

    def test_template_replaces_existing(self, fresh_db):
        """Applying a template replaces old variants via the upsert replace strategy."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)

        # Original: Half/Whole
        items = [{
            "name": "Pizza",
            "price_cents": 800,
            "_variants": [
                {"label": "Half", "price_cents": 500, "kind": "size"},
                {"label": "Whole", "price_cents": 800, "kind": "size"},
            ]
        }]
        result = upsert_draft_items(d, items)
        item_id = result["inserted_ids"][0]

        # Apply S/M/L template (replaces existing)
        items2 = [{
            "id": item_id,
            "name": "Pizza",
            "price_cents": 800,
            "_variants": [
                {"label": "Small", "price_cents": 0, "kind": "size", "position": 0},
                {"label": "Medium", "price_cents": 0, "kind": "size", "position": 1},
                {"label": "Large", "price_cents": 0, "kind": "size", "position": 2},
            ]
        }]
        upsert_draft_items(d, items2)

        loaded = get_draft_items(d)
        assert len(loaded[0]["variants"]) == 3
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert "Half" not in labels
        assert "Whole" not in labels
        assert "Small" in labels
        assert "Medium" in labels
        assert "Large" in labels

    def test_template_preserves_parent_data(self, fresh_db):
        """Template application doesn't change parent item name/description/category."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Margherita Pizza",
            "description": "Fresh mozzarella, basil",
            "price_cents": 1200,
            "category": "Pizza",
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size"},
                {"label": "Large", "price_cents": 1200, "kind": "size"},
            ]
        }]
        upsert_draft_items(d, items)

        loaded = get_draft_items(d)
        assert loaded[0]["name"] == "Margherita Pizza"
        assert loaded[0]["description"] == "Fresh mozzarella, basil"
        assert loaded[0]["category"] == "Pizza"

    def test_slice_pie_template(self, fresh_db):
        """Slice/Pie template creates 2 size variants."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Cheese Pizza",
            "price_cents": 0,
            "_variants": [
                {"label": "Slice", "price_cents": 350, "kind": "size", "position": 0},
                {"label": "Pie", "price_cents": 1800, "kind": "size", "position": 1},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert labels == ["Slice", "Pie"]

    def test_single_double_triple_template(self, fresh_db):
        """Single/Double/Triple template."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Cheeseburger",
            "price_cents": 0,
            "_variants": [
                {"label": "Single", "price_cents": 799, "kind": "size", "position": 0},
                {"label": "Double", "price_cents": 1099, "kind": "size", "position": 1},
                {"label": "Triple", "price_cents": 1399, "kind": "size", "position": 2},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert labels == ["Single", "Double", "Triple"]


# ===========================================================================
# Tests: Price-order validation helpers (SIZE_ORDINALS logic)
# ===========================================================================
class TestPriceOrderValidation:
    """Tests for the size ordinal mapping and price inversion detection.

    These test the conceptual logic that the JS client implements.
    We verify the ordinal ordering is correct for all size categories.
    """

    # Replicate the JS SIZE_ORDINALS in Python for testing
    SIZE_ORDINALS = {
        'xs': 5, 'xsm': 5, 'extra small': 5,
        's': 10, 'sm': 10, 'sml': 10, 'small': 10, 'mini': 10, 'personal': 10, 'individual': 10,
        'm': 20, 'md': 20, 'med': 20, 'medium': 20, 'regular': 20, 'reg': 20,
        'l': 30, 'lg': 30, 'lrg': 30, 'large': 30,
        'xl': 40, 'xlg': 40, 'extra large': 40, 'x-large': 40,
        'xxl': 50, 'jumbo': 50, 'family': 50, 'party': 50,
        'half': 110, 'whole': 120, 'full': 120,
        'slice': 130, 'pie': 140,
        'single': 210, 'double': 220, 'triple': 230,
    }

    def _get_ordinal(self, label):
        norm = (label or "").strip().lower()
        if norm in self.SIZE_ORDINALS:
            return self.SIZE_ORDINALS[norm]
        import re
        m = re.match(r'^(\d+)["\u201d]?\s*(in|inch)?$', norm, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    def _detect_inversions(self, variants):
        """Returns list of (label_a, price_a, label_b, price_b) inversions."""
        sized = []
        for v in variants:
            ord_val = self._get_ordinal(v["label"])
            if ord_val is not None and v["price"] > 0:
                sized.append((ord_val, v["label"], v["price"]))
        sized.sort(key=lambda x: x[0])
        inversions = []
        for i in range(1, len(sized)):
            if sized[i][2] < sized[i-1][2]:
                inversions.append((sized[i-1][1], sized[i-1][2], sized[i][1], sized[i][2]))
        return inversions

    def test_sml_ordinal_order(self, fresh_db):
        """Small < Medium < Large in ordinal space."""
        assert self._get_ordinal("Small") < self._get_ordinal("Medium")
        assert self._get_ordinal("Medium") < self._get_ordinal("Large")

    def test_abbreviation_ordinals(self, fresh_db):
        """S, M, L abbreviations have same ordinals as full names."""
        assert self._get_ordinal("S") == self._get_ordinal("Small")
        assert self._get_ordinal("M") == self._get_ordinal("Medium")
        assert self._get_ordinal("L") == self._get_ordinal("Large")

    def test_xl_after_large(self, fresh_db):
        """XL > L in ordinal space."""
        assert self._get_ordinal("Large") < self._get_ordinal("Extra Large")
        assert self._get_ordinal("Large") < self._get_ordinal("XL")

    def test_family_after_xl(self, fresh_db):
        """Family/Party/Jumbo after XL."""
        assert self._get_ordinal("XL") < self._get_ordinal("Family")
        assert self._get_ordinal("Family") == self._get_ordinal("Party")
        assert self._get_ordinal("Family") == self._get_ordinal("Jumbo")

    def test_inch_ordinals(self, fresh_db):
        """Inch sizes ordered by numeric value."""
        assert self._get_ordinal('10"') < self._get_ordinal('12"')
        assert self._get_ordinal('12"') < self._get_ordinal('16"')
        assert self._get_ordinal('16"') < self._get_ordinal('18"')

    def test_half_whole_order(self, fresh_db):
        """Half < Whole."""
        assert self._get_ordinal("Half") < self._get_ordinal("Whole")
        assert self._get_ordinal("Whole") == self._get_ordinal("Full")

    def test_slice_pie_order(self, fresh_db):
        """Slice < Pie."""
        assert self._get_ordinal("Slice") < self._get_ordinal("Pie")

    def test_single_double_triple_order(self, fresh_db):
        """Single < Double < Triple."""
        assert self._get_ordinal("Single") < self._get_ordinal("Double")
        assert self._get_ordinal("Double") < self._get_ordinal("Triple")

    def test_unknown_label_returns_none(self, fresh_db):
        """Unknown labels return None (excluded from validation)."""
        assert self._get_ordinal("Special") is None
        assert self._get_ordinal("Deluxe") is None
        assert self._get_ordinal("") is None

    def test_no_inversion_sml(self, fresh_db):
        """Correct S < M < L pricing has no inversions."""
        variants = [
            {"label": "Small", "price": 800},
            {"label": "Medium", "price": 1000},
            {"label": "Large", "price": 1200},
        ]
        inversions = self._detect_inversions(variants)
        assert len(inversions) == 0

    def test_inversion_detected(self, fresh_db):
        """Large cheaper than Medium triggers inversion."""
        variants = [
            {"label": "Small", "price": 800},
            {"label": "Medium", "price": 1200},
            {"label": "Large", "price": 900},  # inversion!
        ]
        inversions = self._detect_inversions(variants)
        assert len(inversions) == 1
        assert inversions[0][0] == "Medium"
        assert inversions[0][2] == "Large"

    def test_equal_prices_no_inversion(self, fresh_db):
        """Equal prices are allowed (not a strict inversion)."""
        variants = [
            {"label": "Small", "price": 1000},
            {"label": "Medium", "price": 1000},
            {"label": "Large", "price": 1200},
        ]
        inversions = self._detect_inversions(variants)
        assert len(inversions) == 0

    def test_multiple_inversions(self, fresh_db):
        """Multiple inversions detected in one item."""
        variants = [
            {"label": "Small", "price": 1000},
            {"label": "Medium", "price": 800},   # inv 1
            {"label": "Large", "price": 600},     # inv 2
        ]
        inversions = self._detect_inversions(variants)
        assert len(inversions) == 2

    def test_inch_price_inversion(self, fresh_db):
        """Inch-based sizes also detect inversions."""
        variants = [
            {"label": '10"', "price": 1200},
            {"label": '12"', "price": 800},   # inversion
        ]
        inversions = self._detect_inversions(variants)
        assert len(inversions) == 1

    def test_zero_price_excluded(self, fresh_db):
        """Variants with $0 price are excluded from validation."""
        variants = [
            {"label": "Small", "price": 0},   # excluded
            {"label": "Medium", "price": 1000},
            {"label": "Large", "price": 1200},
        ]
        inversions = self._detect_inversions(variants)
        assert len(inversions) == 0

    def test_unknown_labels_excluded(self, fresh_db):
        """Variants with unknown labels are excluded from validation."""
        variants = [
            {"label": "Special", "price": 2000},   # excluded (unknown ordinal)
            {"label": "Small", "price": 800},
            {"label": "Large", "price": 1200},
        ]
        inversions = self._detect_inversions(variants)
        assert len(inversions) == 0

    def test_half_whole_inversion(self, fresh_db):
        """Half > Whole in price triggers inversion."""
        variants = [
            {"label": "Half", "price": 1200},
            {"label": "Whole", "price": 800},  # inv
        ]
        inversions = self._detect_inversions(variants)
        assert len(inversions) == 1

    def test_xs_before_small(self, fresh_db):
        """XS < S in ordinal space."""
        assert self._get_ordinal("XS") < self._get_ordinal("S")
        assert self._get_ordinal("Extra Small") < self._get_ordinal("Small")

    def test_personal_equals_small(self, fresh_db):
        """Personal = Small ordinal."""
        assert self._get_ordinal("Personal") == self._get_ordinal("Small")
        assert self._get_ordinal("Individual") == self._get_ordinal("Small")

    def test_reg_equals_medium(self, fresh_db):
        """Regular = Medium ordinal."""
        assert self._get_ordinal("Regular") == self._get_ordinal("Medium")
        assert self._get_ordinal("Reg") == self._get_ordinal("Medium")


# ===========================================================================
# Tests: Combined integration scenarios
# ===========================================================================
class TestIntegrationScenarios:
    """End-to-end scenarios combining multiple Day 75 features."""

    def test_reorder_then_save_and_validate_payload(self, fresh_db):
        """Full workflow: create with template → reorder → validate payload → save."""
        from portal.contracts import validate_draft_payload
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)

        # Step 1: Create with S/M/L template
        payload = {
            "draft_id": d,
            "items": [{
                "name": "Cheese Pizza",
                "price_cents": 800,
                "category": "Pizza",
                "_variants": [
                    {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                    {"label": "Medium", "price_cents": 1100, "kind": "size", "position": 1},
                    {"label": "Large", "price_cents": 1400, "kind": "size", "position": 2},
                ]
            }]
        }
        ok, err = validate_draft_payload(payload)
        assert ok, f"Payload should be valid: {err}"

        upsert_draft_items(d, payload["items"])
        loaded = get_draft_items(d)
        item_id = loaded[0]["id"]

        # Step 2: User reorders L to first position
        payload2 = {
            "draft_id": d,
            "items": [{
                "id": item_id,
                "name": "Cheese Pizza",
                "price_cents": 800,
                "category": "Pizza",
                "_variants": [
                    {"label": "Large", "price_cents": 1400, "kind": "size", "position": 0},
                    {"label": "Small", "price_cents": 800, "kind": "size", "position": 1},
                    {"label": "Medium", "price_cents": 1100, "kind": "size", "position": 2},
                ]
            }]
        }
        ok2, err2 = validate_draft_payload(payload2)
        assert ok2, f"Reordered payload should be valid: {err2}"

        upsert_draft_items(d, payload2["items"])
        reloaded = get_draft_items(d)
        labels = [v["label"] for v in reloaded[0]["variants"]]
        assert labels == ["Large", "Small", "Medium"]

    def test_template_then_delete_some_variants(self, fresh_db):
        """Apply template → delete some variants → save with deleted_variant_ids."""
        from portal.contracts import validate_draft_payload
        from storage.drafts import upsert_draft_items, get_draft_items, delete_variants_by_id
        d = _create_draft(fresh_db)

        # Step 1: S/M/L/XL
        items = [{
            "name": "Sub",
            "price_cents": 0,
            "_variants": [
                {"label": "Small", "price_cents": 699, "kind": "size", "position": 0},
                {"label": "Medium", "price_cents": 899, "kind": "size", "position": 1},
                {"label": "Large", "price_cents": 1099, "kind": "size", "position": 2},
                {"label": "Extra Large", "price_cents": 1299, "kind": "size", "position": 3},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert len(loaded[0]["variants"]) == 4

        # Step 2: Delete XL variant
        xl_id = loaded[0]["variants"][3]["id"]
        payload = {
            "draft_id": d,
            "items": [],
            "deleted_variant_ids": [xl_id]
        }
        ok, err = validate_draft_payload(payload)
        assert ok, f"Delete payload should be valid: {err}"
        delete_variants_by_id([xl_id])

        reloaded = get_draft_items(d)
        assert len(reloaded[0]["variants"]) == 3
        labels = [v["label"] for v in reloaded[0]["variants"]]
        assert "Extra Large" not in labels

    def test_mixed_kinds_with_reorder(self, fresh_db):
        """Item with mixed size+combo variants, reorder combo to top."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)

        items = [{
            "name": "Chicken Wrap",
            "price_cents": 899,
            "_variants": [
                {"label": "Regular", "price_cents": 899, "kind": "size", "position": 0},
                {"label": "Large", "price_cents": 1199, "kind": "size", "position": 1},
                {"label": "W/Fries", "price_cents": 1099, "kind": "combo", "position": 2},
            ]
        }]
        result = upsert_draft_items(d, items)
        item_id = result["inserted_ids"][0]

        # Move combo to first position
        items2 = [{
            "id": item_id,
            "name": "Chicken Wrap",
            "price_cents": 899,
            "_variants": [
                {"label": "W/Fries", "price_cents": 1099, "kind": "combo", "position": 0},
                {"label": "Regular", "price_cents": 899, "kind": "size", "position": 1},
                {"label": "Large", "price_cents": 1199, "kind": "size", "position": 2},
            ]
        }]
        upsert_draft_items(d, items2)

        loaded = get_draft_items(d)
        assert loaded[0]["variants"][0]["label"] == "W/Fries"
        assert loaded[0]["variants"][0]["kind"] == "combo"
        assert loaded[0]["variants"][1]["label"] == "Regular"
        assert loaded[0]["variants"][2]["label"] == "Large"

    def test_full_save_cycle_with_validation(self, fresh_db):
        """Complete: validate → save → reload → validate positions match."""
        from portal.contracts import validate_draft_payload
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)

        payload = {
            "draft_id": d,
            "items": [
                {
                    "name": "Pepperoni",
                    "price_cents": 1000,
                    "category": "Pizza",
                    "_variants": [
                        {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                        {"label": "Medium", "price_cents": 1100, "kind": "size", "position": 1},
                        {"label": "Large", "price_cents": 1400, "kind": "size", "position": 2},
                    ]
                },
                {
                    "name": "Cola",
                    "price_cents": 200,
                    "category": "Beverages",
                },
            ],
            "deleted_variant_ids": [],
        }
        ok, err = validate_draft_payload(payload)
        assert ok, f"Full payload should be valid: {err}"

        upsert_draft_items(d, payload["items"])
        loaded = get_draft_items(d)
        assert len(loaded) == 2
        pizza = next(i for i in loaded if i["name"] == "Pepperoni")
        cola = next(i for i in loaded if i["name"] == "Cola")
        assert len(pizza["variants"]) == 3
        assert len(cola["variants"]) == 0
        assert [v["position"] for v in pizza["variants"]] == [0, 1, 2]
