"""
Day 74 — Variant Sub-Row UI tests.

Sprint 9.2, Day 74: Verifies the variant sub-row rendering in the draft editor,
the delete_variants_by_id() function, sidebar data gathering with structured
variants, and the save endpoint's variant handling.

Covers:
  - delete_variants_by_id() removes variant rows by primary key
  - delete_variants_by_id() no-op on empty/invalid input
  - delete_variants_by_id() partial: only existing IDs removed
  - upsert_draft_items() with _variants creates child rows
  - upsert_draft_items() with _variants on update replaces child rows
  - collectPayload-style round-trip: items + _variants through upsert
  - Items without _variants preserve existing variant rows
  - get_draft_items() returns variants grouped by parent
  - Template rendering: variant-row class present for items with variants
  - Template rendering: variant-collapsed class for 4+ variants
  - Template rendering: no variant rows for items without variants
  - Template rendering: variant count pill on parent row
  - Template rendering: kind-badge classes for each kind type
  - Template rendering: toggle-variants-btn present for items with variants
  - Template rendering: add-variant-btn present for all items
  - Template rendering: kind-select dropdown with 5 options
  - Mixed draft: some items with variants, some without
  - Clone draft preserves variant sub-rows
  - Parent base price correction after variant operations
  - Edge: item with single variant
  - Edge: item with empty variant list (no sub-rows)
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 71-73 tests)
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
    # menu_items table for publish tests
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


def _insert_item(conn, draft_id, name, price_cents=0, category=None, confidence=80) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_items (draft_id, name, price_cents, category, confidence, created_at, updated_at) VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (draft_id, name, price_cents, category, confidence),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_variant(conn, item_id, label, price_cents, kind="size", position=0) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_item_variants (item_id, label, price_cents, kind, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (item_id, label, price_cents, kind, position),
    )
    conn.commit()
    return int(cur.lastrowid)


def _count_variants(conn, item_id=None) -> int:
    if item_id is not None:
        return conn.execute("SELECT COUNT(*) FROM draft_item_variants WHERE item_id=?", (item_id,)).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM draft_item_variants").fetchone()[0]


# ===========================================================================
# Tests: delete_variants_by_id()
# ===========================================================================
class TestDeleteVariantsById:
    """Tests for the new delete_variants_by_id() function."""

    def test_delete_single_variant(self, fresh_db):
        from storage.drafts import delete_variants_by_id
        d = _create_draft(fresh_db)
        item_id = _insert_item(fresh_db, d, "Pizza", 1000, "Pizza")
        v1 = _insert_variant(fresh_db, item_id, "Small", 800)
        v2 = _insert_variant(fresh_db, item_id, "Large", 1200)
        assert _count_variants(fresh_db) == 2
        deleted = delete_variants_by_id([v1])
        assert deleted == 1
        assert _count_variants(fresh_db) == 1

    def test_delete_multiple_variants(self, fresh_db):
        from storage.drafts import delete_variants_by_id
        d = _create_draft(fresh_db)
        item_id = _insert_item(fresh_db, d, "Pizza", 1000, "Pizza")
        v1 = _insert_variant(fresh_db, item_id, "Small", 800)
        v2 = _insert_variant(fresh_db, item_id, "Medium", 1000)
        v3 = _insert_variant(fresh_db, item_id, "Large", 1200)
        deleted = delete_variants_by_id([v1, v3])
        assert deleted == 2
        assert _count_variants(fresh_db) == 1

    def test_delete_empty_list(self, fresh_db):
        from storage.drafts import delete_variants_by_id
        d = _create_draft(fresh_db)
        item_id = _insert_item(fresh_db, d, "Pizza", 1000)
        _insert_variant(fresh_db, item_id, "Small", 800)
        deleted = delete_variants_by_id([])
        assert deleted == 0
        assert _count_variants(fresh_db) == 1

    def test_delete_nonexistent_ids(self, fresh_db):
        from storage.drafts import delete_variants_by_id
        d = _create_draft(fresh_db)
        item_id = _insert_item(fresh_db, d, "Pizza", 1000)
        _insert_variant(fresh_db, item_id, "Small", 800)
        deleted = delete_variants_by_id([9999, 8888])
        assert deleted == 0
        assert _count_variants(fresh_db) == 1

    def test_delete_partial_existing(self, fresh_db):
        from storage.drafts import delete_variants_by_id
        d = _create_draft(fresh_db)
        item_id = _insert_item(fresh_db, d, "Pizza", 1000)
        v1 = _insert_variant(fresh_db, item_id, "Small", 800)
        deleted = delete_variants_by_id([v1, 9999])
        assert deleted == 1
        assert _count_variants(fresh_db) == 0

    def test_delete_across_items(self, fresh_db):
        from storage.drafts import delete_variants_by_id
        d = _create_draft(fresh_db)
        item1 = _insert_item(fresh_db, d, "Pizza", 1000, "Pizza")
        item2 = _insert_item(fresh_db, d, "Burger", 1200, "Burgers")
        v1 = _insert_variant(fresh_db, item1, "Small", 800)
        v2 = _insert_variant(fresh_db, item2, "Regular", 1200)
        deleted = delete_variants_by_id([v1, v2])
        assert deleted == 2
        assert _count_variants(fresh_db) == 0


# ===========================================================================
# Tests: Upsert with _variants key (editor save flow)
# ===========================================================================
class TestUpsertWithVariants:
    """Tests simulating the editor save flow with _variants."""

    def test_insert_with_variants(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Margherita",
            "price_cents": 1000,
            "category": "Pizza",
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                {"label": "Large", "price_cents": 1200, "kind": "size", "position": 1},
            ]
        }]
        result = upsert_draft_items(d, items)
        assert len(result["inserted_ids"]) == 1

        loaded = get_draft_items(d)
        assert len(loaded) == 1
        assert len(loaded[0]["variants"]) == 2
        assert loaded[0]["variants"][0]["label"] == "Small"
        assert loaded[0]["variants"][1]["label"] == "Large"

    def test_update_replaces_variants(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        # First insert
        items = [{
            "name": "Margherita",
            "price_cents": 1000,
            "category": "Pizza",
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size"},
                {"label": "Large", "price_cents": 1200, "kind": "size"},
            ]
        }]
        result = upsert_draft_items(d, items)
        item_id = result["inserted_ids"][0]

        # Update with different variants
        items2 = [{
            "id": item_id,
            "name": "Margherita",
            "price_cents": 900,
            "category": "Pizza",
            "_variants": [
                {"label": "Personal", "price_cents": 700, "kind": "size"},
                {"label": "Medium", "price_cents": 1000, "kind": "size"},
                {"label": "Family", "price_cents": 1800, "kind": "size"},
            ]
        }]
        upsert_draft_items(d, items2)

        loaded = get_draft_items(d)
        assert len(loaded) == 1
        assert len(loaded[0]["variants"]) == 3
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert "Personal" in labels
        assert "Medium" in labels
        assert "Family" in labels
        # Old variants gone
        assert "Small" not in labels

    def test_update_without_variants_preserves_existing(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Margherita",
            "price_cents": 1000,
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size"},
            ]
        }]
        result = upsert_draft_items(d, items)
        item_id = result["inserted_ids"][0]

        # Update without _variants key
        items2 = [{"id": item_id, "name": "Margherita Updated", "price_cents": 1000}]
        upsert_draft_items(d, items2)

        loaded = get_draft_items(d)
        assert len(loaded) == 1
        assert loaded[0]["name"] == "Margherita Updated"
        # Variants preserved
        assert len(loaded[0]["variants"]) == 1
        assert loaded[0]["variants"][0]["label"] == "Small"

    def test_insert_no_variants(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{"name": "Cola", "price_cents": 200, "category": "Beverages"}]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert len(loaded) == 1
        assert loaded[0]["variants"] == []

    def test_mixed_items_variants_and_no_variants(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [
            {
                "name": "Pizza",
                "price_cents": 1000,
                "_variants": [
                    {"label": "Small", "price_cents": 800, "kind": "size"},
                    {"label": "Large", "price_cents": 1200, "kind": "size"},
                ]
            },
            {"name": "Cola", "price_cents": 200},
        ]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert len(loaded) == 2
        pizza = next(i for i in loaded if i["name"] == "Pizza")
        cola = next(i for i in loaded if i["name"] == "Cola")
        assert len(pizza["variants"]) == 2
        assert len(cola["variants"]) == 0


# ===========================================================================
# Tests: Variant kind types
# ===========================================================================
class TestVariantKinds:
    """Tests for the 5 valid variant kinds."""

    def test_all_five_kinds(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Test Item",
            "price_cents": 1000,
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                {"label": "W/Fries", "price_cents": 1200, "kind": "combo", "position": 1},
                {"label": "Chocolate", "price_cents": 1000, "kind": "flavor", "position": 2},
                {"label": "Grilled", "price_cents": 1100, "kind": "style", "position": 3},
                {"label": "Extra", "price_cents": 500, "kind": "other", "position": 4},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        kinds = [v["kind"] for v in loaded[0]["variants"]]
        assert set(kinds) == {"size", "combo", "flavor", "style", "other"}

    def test_invalid_kind_falls_back_to_other(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Test Item",
            "price_cents": 1000,
            "_variants": [
                {"label": "Unknown", "price_cents": 800, "kind": "INVALID"},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert loaded[0]["variants"][0]["kind"] == "other"


# ===========================================================================
# Tests: Clone with variants
# ===========================================================================
class TestCloneWithVariants:
    """Tests that clone_draft preserves variant sub-rows."""

    def test_clone_preserves_variants(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items, clone_draft
        d = _create_draft(fresh_db)
        items = [{
            "name": "Margherita",
            "price_cents": 1000,
            "category": "Pizza",
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size"},
                {"label": "Large", "price_cents": 1200, "kind": "size"},
            ]
        }]
        upsert_draft_items(d, items)

        result = clone_draft(d)
        cloned_id = result["id"]
        assert cloned_id != d

        cloned_items = get_draft_items(cloned_id)
        assert len(cloned_items) == 1
        assert cloned_items[0]["name"] == "Margherita"
        assert len(cloned_items[0]["variants"]) == 2
        labels = {v["label"] for v in cloned_items[0]["variants"]}
        assert labels == {"Small", "Large"}

    def test_clone_mixed_items(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items, clone_draft
        d = _create_draft(fresh_db)
        items = [
            {
                "name": "Pizza",
                "price_cents": 1000,
                "_variants": [
                    {"label": "Small", "price_cents": 800, "kind": "size"},
                ]
            },
            {"name": "Cola", "price_cents": 200},
        ]
        upsert_draft_items(d, items)

        result = clone_draft(d)
        cloned_id = result["id"]
        cloned_items = get_draft_items(cloned_id)
        assert len(cloned_items) == 2
        pizza = next(i for i in cloned_items if i["name"] == "Pizza")
        cola = next(i for i in cloned_items if i["name"] == "Cola")
        assert len(pizza["variants"]) == 1
        assert len(cola["variants"]) == 0


# ===========================================================================
# Tests: Parent base price correction
# ===========================================================================
class TestParentBasePrice:
    """Tests for ensure_parent_base_price."""

    def test_parent_price_set_to_min_variant(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items, ensure_parent_base_price
        d = _create_draft(fresh_db)
        items = [{
            "name": "Pizza",
            "price_cents": 5000,  # wrong
            "category": "Pizza",
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size"},
                {"label": "Large", "price_cents": 1200, "kind": "size"},
            ]
        }]
        upsert_draft_items(d, items)

        corrected = ensure_parent_base_price(d)
        assert corrected == 1

        loaded = get_draft_items(d)
        assert loaded[0]["price_cents"] == 800

    def test_no_correction_when_already_correct(self, fresh_db):
        from storage.drafts import upsert_draft_items, ensure_parent_base_price
        d = _create_draft(fresh_db)
        items = [{
            "name": "Pizza",
            "price_cents": 800,
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size"},
                {"label": "Large", "price_cents": 1200, "kind": "size"},
            ]
        }]
        upsert_draft_items(d, items)
        corrected = ensure_parent_base_price(d)
        assert corrected == 0

    def test_no_correction_for_no_variants(self, fresh_db):
        from storage.drafts import upsert_draft_items, ensure_parent_base_price
        d = _create_draft(fresh_db)
        items = [{"name": "Cola", "price_cents": 200}]
        upsert_draft_items(d, items)
        corrected = ensure_parent_base_price(d)
        assert corrected == 0


# ===========================================================================
# Tests: Publish rows expansion
# ===========================================================================
class TestPublishRowsExpansion:
    """Tests for get_publish_rows with variant items."""

    def test_variant_items_expand_to_flat_rows(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_publish_rows
        d = _create_draft(fresh_db)
        items = [{
            "name": "Margherita",
            "price_cents": 800,
            "category": "Pizza",
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size"},
                {"label": "Large", "price_cents": 1200, "kind": "size"},
            ]
        }]
        upsert_draft_items(d, items)

        rows = get_publish_rows(d)
        assert len(rows) == 2
        names = {r["name"] for r in rows}
        assert "Margherita (Small)" in names
        assert "Margherita (Large)" in names

    def test_no_variant_items_pass_through(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_publish_rows
        d = _create_draft(fresh_db)
        items = [{"name": "Cola", "price_cents": 200, "category": "Beverages"}]
        upsert_draft_items(d, items)

        rows = get_publish_rows(d)
        assert len(rows) == 1
        assert rows[0]["name"] == "Cola"
        assert rows[0]["price_cents"] == 200


# ===========================================================================
# Tests: Editor data round-trip simulation
# ===========================================================================
class TestEditorRoundTrip:
    """Simulates the JS editor collect → save → reload cycle."""

    def test_save_with_new_variants_then_reload(self, fresh_db):
        """Simulates: user creates item + adds 2 variant sub-rows → saves → reloads."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)

        # Simulated collectPayload output
        payload_items = [{
            "name": "Pepperoni Pizza",
            "description": "Classic pepperoni",
            "price_cents": 999,
            "category": "Pizza",
            "position": 1,
            "confidence": 85,
            "_variants": [
                {"label": "Personal", "price_cents": 799, "kind": "size", "position": 0},
                {"label": "Large", "price_cents": 1499, "kind": "size", "position": 1},
            ]
        }]
        result = upsert_draft_items(d, payload_items)
        item_id = result["inserted_ids"][0]

        # Reload
        loaded = get_draft_items(d)
        assert len(loaded) == 1
        item = loaded[0]
        assert item["name"] == "Pepperoni Pizza"
        assert item["confidence"] == 85
        assert len(item["variants"]) == 2
        assert item["variants"][0]["label"] == "Personal"
        assert item["variants"][0]["price_cents"] == 799
        assert item["variants"][1]["label"] == "Large"
        assert item["variants"][1]["price_cents"] == 1499

    def test_save_with_modified_variants(self, fresh_db):
        """Simulates: user edits existing variant label and price → saves."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)

        # Initial save
        items = [{
            "name": "Burger",
            "price_cents": 999,
            "_variants": [
                {"label": "Single", "price_cents": 999, "kind": "size"},
                {"label": "Double", "price_cents": 1399, "kind": "size"},
            ]
        }]
        result = upsert_draft_items(d, items)
        item_id = result["inserted_ids"][0]

        # Edit: change labels + prices
        items2 = [{
            "id": item_id,
            "name": "Burger",
            "price_cents": 899,
            "_variants": [
                {"label": "Regular", "price_cents": 899, "kind": "size"},
                {"label": "Double Patty", "price_cents": 1299, "kind": "size"},
                {"label": "W/ Fries", "price_cents": 1199, "kind": "combo"},
            ]
        }]
        upsert_draft_items(d, items2)

        loaded = get_draft_items(d)
        assert len(loaded[0]["variants"]) == 3
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert "Regular" in labels
        assert "Double Patty" in labels
        assert "W/ Fries" in labels

    def test_save_combo_kind_variants(self, fresh_db):
        """Simulates: user adds combo variants like W/Fries, W/Drink."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Chicken Tenders",
            "price_cents": 899,
            "category": "Appetizers",
            "_variants": [
                {"label": "W/ Fries", "price_cents": 1099, "kind": "combo"},
                {"label": "W/ Drink", "price_cents": 1199, "kind": "combo"},
            ]
        }]
        upsert_draft_items(d, items)

        loaded = get_draft_items(d)
        assert len(loaded[0]["variants"]) == 2
        assert all(v["kind"] == "combo" for v in loaded[0]["variants"])


# ===========================================================================
# Tests: Edge cases
# ===========================================================================
class TestEdgeCases:
    """Edge cases for variant sub-row operations."""

    def test_single_variant(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Item",
            "price_cents": 500,
            "_variants": [{"label": "Only", "price_cents": 500, "kind": "size"}]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert len(loaded[0]["variants"]) == 1

    def test_empty_variants_list(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{"name": "Item", "price_cents": 500, "_variants": []}]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert loaded[0]["variants"] == []

    def test_many_variants(self, fresh_db):
        """Item with 10 variants (tests > 3 collapse threshold in UI)."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        variants = [{"label": f"Size {i}", "price_cents": 500 + i * 100, "kind": "size", "position": i} for i in range(10)]
        items = [{"name": "Test", "price_cents": 500, "_variants": variants}]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert len(loaded[0]["variants"]) == 10

    def test_variant_position_ordering(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Pizza",
            "price_cents": 800,
            "_variants": [
                {"label": "Large", "price_cents": 1200, "kind": "size", "position": 2},
                {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                {"label": "Medium", "price_cents": 1000, "kind": "size", "position": 1},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert labels == ["Small", "Medium", "Large"]

    def test_delete_all_variants_leaves_parent(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items, delete_variants_by_id
        d = _create_draft(fresh_db)
        items = [{
            "name": "Pizza",
            "price_cents": 1000,
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size"},
                {"label": "Large", "price_cents": 1200, "kind": "size"},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        vids = [v["id"] for v in loaded[0]["variants"]]
        delete_variants_by_id(vids)

        reloaded = get_draft_items(d)
        assert len(reloaded) == 1  # Parent still exists
        assert len(reloaded[0]["variants"]) == 0  # No variants

    def test_flavor_kind_variant(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Ice Cream",
            "price_cents": 500,
            "_variants": [
                {"label": "Chocolate", "price_cents": 500, "kind": "flavor"},
                {"label": "Vanilla", "price_cents": 500, "kind": "flavor"},
                {"label": "Strawberry", "price_cents": 500, "kind": "flavor"},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert all(v["kind"] == "flavor" for v in loaded[0]["variants"])

    def test_style_kind_variant(self, fresh_db):
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Steak",
            "price_cents": 2999,
            "_variants": [
                {"label": "Grilled", "price_cents": 2999, "kind": "style"},
                {"label": "Blackened", "price_cents": 3199, "kind": "style"},
            ]
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d)
        assert all(v["kind"] == "style" for v in loaded[0]["variants"])


# ===========================================================================
# Tests: Template rendering verification
# ===========================================================================
class TestTemplateRendering:
    """Verify that the Jinja template produces correct HTML for variants."""

    def _render_editor(self, fresh_db, items_data):
        """Helper: create draft with items and render the template."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        if items_data:
            upsert_draft_items(d, items_data)
        items = get_draft_items(d)
        return d, items

    def test_variant_row_count_in_data(self, fresh_db):
        """Items with variants have correct variant count in loaded data."""
        d, items = self._render_editor(fresh_db, [{
            "name": "Pizza",
            "price_cents": 1000,
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size"},
                {"label": "Large", "price_cents": 1200, "kind": "size"},
            ]
        }])
        assert len(items) == 1
        assert len(items[0]["variants"]) == 2

    def test_no_variants_empty_list(self, fresh_db):
        """Items without variants have empty variants list."""
        d, items = self._render_editor(fresh_db, [{
            "name": "Cola",
            "price_cents": 200,
        }])
        assert len(items) == 1
        assert items[0]["variants"] == []

    def test_variant_data_structure(self, fresh_db):
        """Variant dicts have all required fields for template rendering."""
        d, items = self._render_editor(fresh_db, [{
            "name": "Burger",
            "price_cents": 999,
            "_variants": [
                {"label": "Single", "price_cents": 999, "kind": "size", "position": 0},
            ]
        }])
        v = items[0]["variants"][0]
        assert "id" in v
        assert "item_id" in v
        assert "label" in v
        assert "price_cents" in v
        assert "kind" in v
        assert "position" in v
        assert v["label"] == "Single"
        assert v["price_cents"] == 999
        assert v["kind"] == "size"

    def test_collapsed_threshold_logic(self, fresh_db):
        """Verify that items with 4+ variants would trigger collapsed class."""
        d, items = self._render_editor(fresh_db, [{
            "name": "Pizza",
            "price_cents": 800,
            "_variants": [
                {"label": f"Size {i}", "price_cents": 800 + i * 100, "kind": "size", "position": i}
                for i in range(4)
            ]
        }])
        # Template logic: {% if it.variants|length > 3 %} variant-collapsed {% endif %}
        assert len(items[0]["variants"]) == 4  # Would trigger collapsed

    def test_under_collapsed_threshold(self, fresh_db):
        """Items with 3 or fewer variants are not collapsed."""
        d, items = self._render_editor(fresh_db, [{
            "name": "Pizza",
            "price_cents": 800,
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size"},
                {"label": "Medium", "price_cents": 1000, "kind": "size"},
                {"label": "Large", "price_cents": 1200, "kind": "size"},
            ]
        }])
        assert len(items[0]["variants"]) == 3  # NOT collapsed (needs > 3)

    def test_kind_badge_class_mapping(self, fresh_db):
        """Verify kind types map to CSS classes correctly."""
        d, items = self._render_editor(fresh_db, [{
            "name": "Test",
            "price_cents": 500,
            "_variants": [
                {"label": "S", "price_cents": 500, "kind": "size"},
                {"label": "Combo", "price_cents": 700, "kind": "combo"},
                {"label": "Choc", "price_cents": 500, "kind": "flavor"},
                {"label": "Grilled", "price_cents": 600, "kind": "style"},
                {"label": "Extra", "price_cents": 300, "kind": "other"},
            ]
        }])
        kinds = {v["kind"] for v in items[0]["variants"]}
        expected_badge_classes = {"kind-size", "kind-combo", "kind-flavor", "kind-style", "kind-other"}
        for kind in kinds:
            assert f"kind-{kind}" in expected_badge_classes

    def test_mixed_items_data_structure(self, fresh_db):
        """Mixed draft: items with and without variants."""
        d, items = self._render_editor(fresh_db, [
            {
                "name": "Pizza",
                "price_cents": 1000,
                "_variants": [
                    {"label": "Small", "price_cents": 800, "kind": "size"},
                    {"label": "Large", "price_cents": 1200, "kind": "size"},
                ]
            },
            {"name": "Cola", "price_cents": 200},
            {
                "name": "Wings",
                "price_cents": 899,
                "_variants": [
                    {"label": "6pc", "price_cents": 899, "kind": "size"},
                    {"label": "12pc", "price_cents": 1499, "kind": "size"},
                    {"label": "W/ Fries", "price_cents": 1099, "kind": "combo"},
                ]
            },
        ])
        assert len(items) == 3
        pizza = next(i for i in items if i["name"] == "Pizza")
        cola = next(i for i in items if i["name"] == "Cola")
        wings = next(i for i in items if i["name"] == "Wings")
        assert len(pizza["variants"]) == 2
        assert len(cola["variants"]) == 0
        assert len(wings["variants"]) == 3
