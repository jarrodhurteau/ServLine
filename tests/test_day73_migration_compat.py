"""
Day 73 — Migration & Backward Compatibility tests.

Sprint 9.1, Day 73: Ensures existing drafts without variants continue to
work, parent price_cents = base/lowest price, and the publish flow correctly
expands variant data.

Covers:
  - Old drafts (0 variant rows) load cleanly as single-price items
  - New drafts with variants load with variants attached
  - get_publish_rows() expands variants → flat publishable rows
  - get_publish_rows() passes through items without variants unchanged
  - ensure_parent_base_price() fixes drift between parent and min variant
  - Publish flow dedup works with variant-expanded names
  - Clone preserves variants correctly (smoke test)
  - Backfill idempotent on already-migrated items
  - Mixed drafts (some items with variants, some without)
  - Edge cases: empty drafts, items with empty variant list, single variant
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 71/72 tests)
# ---------------------------------------------------------------------------
_TEST_CONN: Optional[sqlite3.Connection] = None


def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the required schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    # restaurants table (FK reference from drafts)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # drafts table
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

    # draft_items table
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

    # draft_item_variants table
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

    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_draft ON draft_items(draft_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_variants_item ON draft_item_variants(item_id)")
    conn.commit()
    return conn


def _patch_db(monkeypatch):
    """Monkey-patch drafts.db_connect to use in-memory DB."""
    global _TEST_CONN
    _TEST_CONN = _make_test_db()

    import storage.drafts as drafts_mod

    def mock_connect():
        return _TEST_CONN

    monkeypatch.setattr(drafts_mod, "db_connect", mock_connect)
    return _TEST_CONN


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    """Each test gets a fresh in-memory DB."""
    conn = _patch_db(monkeypatch)
    yield conn
    global _TEST_CONN
    _TEST_CONN = None


def _create_draft(conn, title="Test Draft") -> int:
    """Insert a bare draft row and return its id."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO drafts (title, status, created_at, updated_at) VALUES (?, 'editing', datetime('now'), datetime('now'))",
        (title,),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_item(conn, draft_id, name, price_cents, category=None, position=None) -> int:
    """Insert a draft item and return its id."""
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO draft_items (draft_id, name, description, price_cents, category, position, created_at, updated_at)
           VALUES (?, ?, '', ?, ?, ?, datetime('now'), datetime('now'))""",
        (draft_id, name, price_cents, category, position),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_variant(conn, item_id, label, price_cents, kind="size", position=0) -> int:
    """Insert a variant row and return its id."""
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO draft_item_variants (item_id, label, price_cents, kind, position, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
        (item_id, label, price_cents, kind, position),
    )
    conn.commit()
    return int(cur.lastrowid)


# ===================================================================
# SECTION 1: Old drafts (no variants) continue to work
# ===================================================================

class TestOldDraftsNoVariants:
    """Existing drafts with 0 variant rows work as single-price items."""

    def test_load_old_draft_items_no_variants(self, fresh_db):
        """Items without variant rows get empty variants list."""
        from storage.drafts import get_draft_items

        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Cheese Pizza", 999, "Pizza")
        _insert_item(fresh_db, did, "Garden Salad", 799, "Salads")

        items = get_draft_items(did, include_variants=True)
        assert len(items) == 2
        for it in items:
            assert it["variants"] == []

    def test_load_old_draft_backward_compat_flag(self, fresh_db):
        """include_variants=False returns old-style dicts without variants key."""
        from storage.drafts import get_draft_items

        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 1299, "Burgers")

        items = get_draft_items(did, include_variants=False)
        assert len(items) == 1
        assert "variants" not in items[0]
        assert items[0]["name"] == "Burger"
        assert items[0]["price_cents"] == 1299

    def test_publish_rows_no_variants_passthrough(self, fresh_db):
        """Items with no variants pass through get_publish_rows() unchanged."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Cheese Pizza", 999, "Pizza")
        _insert_item(fresh_db, did, "Garden Salad", 799, "Salads")

        rows = get_publish_rows(did)
        assert len(rows) == 2
        assert rows[0]["name"] == "Cheese Pizza"
        assert rows[0]["price_cents"] == 999
        assert rows[1]["name"] == "Garden Salad"
        assert rows[1]["price_cents"] == 799

    def test_publish_rows_preserves_description(self, fresh_db):
        """Description is preserved in publish rows."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Wings", 1199, "Appetizers")
        fresh_db.execute(
            "UPDATE draft_items SET description='Buffalo style' WHERE id=?",
            (iid,),
        )
        fresh_db.commit()

        rows = get_publish_rows(did)
        assert len(rows) == 1
        assert rows[0]["description"] == "Buffalo style"

    def test_publish_rows_preserves_category(self, fresh_db):
        """Category is preserved in publish rows."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Tiramisu", 899, "Desserts")

        rows = get_publish_rows(did)
        assert rows[0]["category"] == "Desserts"

    def test_ensure_parent_price_noop_no_variants(self, fresh_db):
        """ensure_parent_base_price() does nothing for items without variants."""
        from storage.drafts import ensure_parent_base_price

        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Pasta", 1499, "Pasta")

        updated = ensure_parent_base_price(did)
        assert updated == 0

    def test_empty_draft_loads_cleanly(self, fresh_db):
        """Empty draft returns empty list."""
        from storage.drafts import get_draft_items, get_publish_rows

        did = _create_draft(fresh_db)

        items = get_draft_items(did)
        assert items == []

        rows = get_publish_rows(did)
        assert rows == []


# ===================================================================
# SECTION 2: New drafts with variants
# ===================================================================

class TestNewDraftsWithVariants:
    """Drafts with variant rows load correctly with variants attached."""

    def test_load_items_with_variants(self, fresh_db):
        """Items with variant rows include them in the variants list."""
        from storage.drafts import get_draft_items

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Cheese Pizza", 999, "Pizza")
        _insert_variant(fresh_db, iid, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid, "Medium", 1299, "size", 1)
        _insert_variant(fresh_db, iid, "Large", 1599, "size", 2)

        items = get_draft_items(did)
        assert len(items) == 1
        assert len(items[0]["variants"]) == 3
        assert items[0]["variants"][0]["label"] == "Small"
        assert items[0]["variants"][1]["label"] == "Medium"
        assert items[0]["variants"][2]["label"] == "Large"

    def test_variant_prices_correct(self, fresh_db):
        """Variant prices are correct in loaded data."""
        from storage.drafts import get_draft_items

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Wings", 899, "Appetizers")
        _insert_variant(fresh_db, iid, "6 Piece", 899, "size", 0)
        _insert_variant(fresh_db, iid, "12 Piece", 1499, "size", 1)

        items = get_draft_items(did)
        assert items[0]["variants"][0]["price_cents"] == 899
        assert items[0]["variants"][1]["price_cents"] == 1499

    def test_variant_kinds_preserved(self, fresh_db):
        """Variant kind field is preserved correctly."""
        from storage.drafts import get_draft_items

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Combo Platter", 1299, "Combos")
        _insert_variant(fresh_db, iid, "W/Fries", 1299, "combo", 0)
        _insert_variant(fresh_db, iid, "W/Onion Rings", 1499, "combo", 1)

        items = get_draft_items(did)
        assert items[0]["variants"][0]["kind"] == "combo"
        assert items[0]["variants"][1]["kind"] == "combo"

    def test_publish_rows_expands_variants(self, fresh_db):
        """get_publish_rows() expands variants into separate rows."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Cheese Pizza", 999, "Pizza")
        _insert_variant(fresh_db, iid, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid, "Medium", 1299, "size", 1)
        _insert_variant(fresh_db, iid, "Large", 1599, "size", 2)

        rows = get_publish_rows(did)
        assert len(rows) == 3
        assert rows[0]["name"] == "Cheese Pizza (Small)"
        assert rows[0]["price_cents"] == 999
        assert rows[1]["name"] == "Cheese Pizza (Medium)"
        assert rows[1]["price_cents"] == 1299
        assert rows[2]["name"] == "Cheese Pizza (Large)"
        assert rows[2]["price_cents"] == 1599

    def test_publish_rows_variant_description_inherited(self, fresh_db):
        """Variant rows inherit the parent's description."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Margherita", 1099, "Pizza")
        fresh_db.execute(
            "UPDATE draft_items SET description='Fresh mozzarella, basil' WHERE id=?",
            (iid,),
        )
        fresh_db.commit()
        _insert_variant(fresh_db, iid, "Personal", 1099, "size", 0)
        _insert_variant(fresh_db, iid, "Large", 1599, "size", 1)

        rows = get_publish_rows(did)
        assert len(rows) == 2
        for r in rows:
            assert r["description"] == "Fresh mozzarella, basil"

    def test_publish_rows_variant_category_inherited(self, fresh_db):
        """Variant rows inherit the parent's category."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Buffalo Wings", 899, "Appetizers")
        _insert_variant(fresh_db, iid, "6 Piece", 899, "size", 0)
        _insert_variant(fresh_db, iid, "12 Piece", 1499, "size", 1)

        rows = get_publish_rows(did)
        for r in rows:
            assert r["category"] == "Appetizers"

    def test_publish_rows_combo_variants(self, fresh_db):
        """Combo variants expand with correct label format."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger", 1099, "Burgers")
        _insert_variant(fresh_db, iid, "W/Fries", 1099, "combo", 0)
        _insert_variant(fresh_db, iid, "W/Onion Rings", 1299, "combo", 1)
        _insert_variant(fresh_db, iid, "W/Salad", 1199, "combo", 2)

        rows = get_publish_rows(did)
        assert len(rows) == 3
        assert rows[0]["name"] == "Burger (W/Fries)"
        assert rows[1]["name"] == "Burger (W/Onion Rings)"
        assert rows[2]["name"] == "Burger (W/Salad)"


# ===================================================================
# SECTION 3: Parent price = base/lowest price
# ===================================================================

class TestParentBasePrice:
    """price_cents on parent item = base/lowest price."""

    def test_ensure_parent_base_price_corrects_drift(self, fresh_db):
        """Parent price drifted above min variant → corrected."""
        from storage.drafts import ensure_parent_base_price, get_draft_items

        did = _create_draft(fresh_db)
        # Parent has wrong price (1599 instead of 999)
        iid = _insert_item(fresh_db, did, "Pizza", 1599, "Pizza")
        _insert_variant(fresh_db, iid, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid, "Large", 1599, "size", 1)

        updated = ensure_parent_base_price(did)
        assert updated == 1

        items = get_draft_items(did)
        assert items[0]["price_cents"] == 999

    def test_ensure_parent_base_price_already_correct(self, fresh_db):
        """Parent already at min variant price → no update."""
        from storage.drafts import ensure_parent_base_price

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza", 999, "Pizza")
        _insert_variant(fresh_db, iid, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid, "Large", 1599, "size", 1)

        updated = ensure_parent_base_price(did)
        assert updated == 0

    def test_ensure_parent_base_price_multiple_items(self, fresh_db):
        """Multiple items: only those with drift are updated."""
        from storage.drafts import ensure_parent_base_price, get_draft_items

        did = _create_draft(fresh_db)

        # Item 1: correct price
        iid1 = _insert_item(fresh_db, did, "Pizza", 999, "Pizza")
        _insert_variant(fresh_db, iid1, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid1, "Large", 1599, "size", 1)

        # Item 2: drifted price (0 instead of 899)
        iid2 = _insert_item(fresh_db, did, "Wings", 0, "Appetizers")
        _insert_variant(fresh_db, iid2, "6 Piece", 899, "size", 0)
        _insert_variant(fresh_db, iid2, "12 Piece", 1499, "size", 1)

        # Item 3: no variants, should be untouched
        _insert_item(fresh_db, did, "Salad", 799, "Salads")

        updated = ensure_parent_base_price(did)
        assert updated == 1  # only Item 2

        items = get_draft_items(did)
        pizza = next(i for i in items if i["name"] == "Pizza")
        wings = next(i for i in items if i["name"] == "Wings")
        salad = next(i for i in items if i["name"] == "Salad")
        assert pizza["price_cents"] == 999
        assert wings["price_cents"] == 899
        assert salad["price_cents"] == 799

    def test_ensure_parent_base_price_parent_below_min(self, fresh_db):
        """Parent price below min variant → corrected upward."""
        from storage.drafts import ensure_parent_base_price, get_draft_items

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Sub", 500, "Subs")
        _insert_variant(fresh_db, iid, "6 Inch", 899, "size", 0)
        _insert_variant(fresh_db, iid, "12 Inch", 1299, "size", 1)

        updated = ensure_parent_base_price(did)
        assert updated == 1

        items = get_draft_items(did)
        assert items[0]["price_cents"] == 899


# ===================================================================
# SECTION 4: Mixed drafts (some items with variants, some without)
# ===================================================================

class TestMixedDrafts:
    """Drafts containing both variant and non-variant items."""

    def test_mixed_load(self, fresh_db):
        """Items with and without variants load together."""
        from storage.drafts import get_draft_items

        did = _create_draft(fresh_db)
        iid1 = _insert_item(fresh_db, did, "Pizza", 999, "Pizza", 1)
        _insert_variant(fresh_db, iid1, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid1, "Large", 1599, "size", 1)
        _insert_item(fresh_db, did, "Garden Salad", 799, "Salads", 2)

        items = get_draft_items(did)
        assert len(items) == 2
        assert len(items[0]["variants"]) == 2
        assert len(items[1]["variants"]) == 0

    def test_mixed_publish_rows(self, fresh_db):
        """Mixed draft: variants expand, non-variants pass through."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        iid1 = _insert_item(fresh_db, did, "Pizza", 999, "Pizza", 1)
        _insert_variant(fresh_db, iid1, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid1, "Large", 1599, "size", 1)
        _insert_item(fresh_db, did, "Garden Salad", 799, "Salads", 2)

        rows = get_publish_rows(did)
        assert len(rows) == 3
        assert rows[0]["name"] == "Pizza (Small)"
        assert rows[0]["price_cents"] == 999
        assert rows[1]["name"] == "Pizza (Large)"
        assert rows[1]["price_cents"] == 1599
        assert rows[2]["name"] == "Garden Salad"
        assert rows[2]["price_cents"] == 799

    def test_mixed_ensure_parent_base_price(self, fresh_db):
        """Only items with variants are checked by ensure_parent_base_price."""
        from storage.drafts import ensure_parent_base_price

        did = _create_draft(fresh_db)
        iid1 = _insert_item(fresh_db, did, "Pizza", 999, "Pizza")
        _insert_variant(fresh_db, iid1, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid1, "Large", 1599, "size", 1)
        _insert_item(fresh_db, did, "Salad", 799, "Salads")

        updated = ensure_parent_base_price(did)
        assert updated == 0  # Pizza already correct, Salad has no variants


# ===================================================================
# SECTION 5: Upsert pipeline → variant creation (integration)
# ===================================================================

class TestUpsertVariantPipeline:
    """Items with _variants key create child variant rows in DB."""

    def test_insert_items_with_variants(self, fresh_db):
        """_insert_items_bulk creates variant rows from _variants key."""
        from storage.drafts import _insert_items_bulk, get_draft_items

        did = _create_draft(fresh_db)
        items = [
            {
                "name": "Pepperoni Pizza",
                "description": "Classic pepperoni",
                "price_cents": 1099,
                "category": "Pizza",
                "position": 1,
                "confidence": 90,
                "_variants": [
                    {"label": "Small", "price_cents": 1099, "kind": "size", "position": 0},
                    {"label": "Medium", "price_cents": 1399, "kind": "size", "position": 1},
                    {"label": "Large", "price_cents": 1699, "kind": "size", "position": 2},
                ],
            },
        ]
        ids = _insert_items_bulk(did, items)
        assert len(ids) == 1

        loaded = get_draft_items(did)
        assert len(loaded) == 1
        assert len(loaded[0]["variants"]) == 3
        assert loaded[0]["variants"][0]["label"] == "Small"
        assert loaded[0]["variants"][2]["price_cents"] == 1699

    def test_insert_items_without_variants(self, fresh_db):
        """Items without _variants key produce 0 variant rows."""
        from storage.drafts import _insert_items_bulk, get_draft_items

        did = _create_draft(fresh_db)
        items = [
            {
                "name": "Garden Salad",
                "description": "Mixed greens",
                "price_cents": 799,
                "category": "Salads",
                "position": 1,
                "confidence": 85,
            },
        ]
        _insert_items_bulk(did, items)

        loaded = get_draft_items(did)
        assert len(loaded) == 1
        assert loaded[0]["variants"] == []

    def test_upsert_update_replaces_variants(self, fresh_db):
        """Upsert with _variants replaces existing variants on update."""
        from storage.drafts import _insert_items_bulk, upsert_draft_items, get_draft_items

        did = _create_draft(fresh_db)
        ids = _insert_items_bulk(did, [
            {
                "name": "Pizza",
                "description": "",
                "price_cents": 999,
                "category": "Pizza",
                "position": 1,
                "confidence": 90,
                "_variants": [
                    {"label": "Small", "price_cents": 999, "kind": "size", "position": 0},
                ],
            },
        ])
        item_id = ids[0]

        # Now upsert with updated variants
        upsert_draft_items(did, [
            {
                "id": item_id,
                "name": "Pizza",
                "description": "",
                "price_cents": 999,
                "category": "Pizza",
                "position": 1,
                "confidence": 90,
                "_variants": [
                    {"label": "Small", "price_cents": 999, "kind": "size", "position": 0},
                    {"label": "Large", "price_cents": 1599, "kind": "size", "position": 1},
                ],
            },
        ])

        loaded = get_draft_items(did)
        assert len(loaded) == 1
        assert len(loaded[0]["variants"]) == 2
        assert loaded[0]["variants"][0]["label"] == "Small"
        assert loaded[0]["variants"][1]["label"] == "Large"

    def test_upsert_without_variants_preserves_existing(self, fresh_db):
        """Upsert without _variants key does NOT touch existing variants."""
        from storage.drafts import _insert_items_bulk, upsert_draft_items, get_draft_items

        did = _create_draft(fresh_db)
        ids = _insert_items_bulk(did, [
            {
                "name": "Pizza",
                "description": "",
                "price_cents": 999,
                "category": "Pizza",
                "position": 1,
                "confidence": 90,
                "_variants": [
                    {"label": "Small", "price_cents": 999, "kind": "size", "position": 0},
                    {"label": "Large", "price_cents": 1599, "kind": "size", "position": 1},
                ],
            },
        ])
        item_id = ids[0]

        # Upsert with NO _variants key — should not remove existing variants
        upsert_draft_items(did, [
            {
                "id": item_id,
                "name": "Pizza Updated",
                "description": "New desc",
                "price_cents": 999,
                "category": "Pizza",
                "position": 1,
                "confidence": 90,
            },
        ])

        loaded = get_draft_items(did)
        assert len(loaded) == 1
        assert loaded[0]["name"] == "Pizza Updated"
        assert len(loaded[0]["variants"]) == 2  # variants preserved


# ===================================================================
# SECTION 6: Clone preserves variants
# ===================================================================

class TestCloneWithVariants:
    """Clone a draft and verify variants are copied."""

    def test_clone_copies_variants(self, fresh_db):
        """Cloned draft has same items and variants."""
        from storage.drafts import clone_draft, get_draft_items

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza", 999, "Pizza", 1)
        _insert_variant(fresh_db, iid, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid, "Medium", 1299, "size", 1)
        _insert_variant(fresh_db, iid, "Large", 1599, "size", 2)
        _insert_item(fresh_db, did, "Salad", 799, "Salads", 2)

        result = clone_draft(did)
        new_id = result["draft_id"]

        new_items = get_draft_items(new_id)
        assert len(new_items) == 2

        pizza = next(i for i in new_items if i["name"] == "Pizza")
        salad = next(i for i in new_items if i["name"] == "Salad")

        assert len(pizza["variants"]) == 3
        assert pizza["variants"][0]["label"] == "Small"
        assert pizza["variants"][1]["label"] == "Medium"
        assert pizza["variants"][2]["label"] == "Large"
        assert len(salad["variants"]) == 0

    def test_clone_variants_independent(self, fresh_db):
        """Modifying cloned draft variants does not affect original."""
        from storage.drafts import clone_draft, get_draft_items, delete_all_variants_for_item

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza", 999, "Pizza")
        _insert_variant(fresh_db, iid, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid, "Large", 1599, "size", 1)

        result = clone_draft(did)
        new_id = result["draft_id"]

        # Delete variants from clone
        new_items = get_draft_items(new_id)
        clone_pizza_id = new_items[0]["id"]
        delete_all_variants_for_item(clone_pizza_id)

        # Original should still have variants
        orig_items = get_draft_items(did)
        assert len(orig_items[0]["variants"]) == 2

        # Clone should have none
        clone_items = get_draft_items(new_id)
        assert len(clone_items[0]["variants"]) == 0


# ===================================================================
# SECTION 7: Backfill idempotent on already-migrated items
# ===================================================================

class TestBackfillIdempotent:
    """backfill_variants_from_names() skips items with existing variants."""

    def test_backfill_skips_items_with_variants(self, fresh_db):
        """Items already having variants are not re-backfilled."""
        from storage.drafts import backfill_variants_from_names, get_draft_items

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza (Small)", 999, "Pizza")
        _insert_variant(fresh_db, iid, "Small", 999, "size", 0)
        _insert_item(fresh_db, did, "Pizza (Large)", 1599, "Pizza")

        # Backfill should NOT merge these because first item already has variants
        result = backfill_variants_from_names(did)
        assert result["groups_found"] == 0

    def test_backfill_works_on_fresh_items(self, fresh_db):
        """Items without variants DO get backfilled."""
        from storage.drafts import backfill_variants_from_names, get_draft_items

        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Pizza (Small)", 999, "Pizza")
        _insert_item(fresh_db, did, "Pizza (Large)", 1599, "Pizza")

        result = backfill_variants_from_names(did)
        assert result["groups_found"] == 1
        assert result["variants_created"] == 2
        assert result["items_deleted"] == 1

        items = get_draft_items(did)
        assert len(items) == 1
        assert items[0]["name"] == "Pizza"
        assert len(items[0]["variants"]) == 2

    def test_backfill_then_rerun_is_noop(self, fresh_db):
        """Running backfill twice produces same result."""
        from storage.drafts import backfill_variants_from_names, get_draft_items

        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Sub (6 Inch)", 899, "Subs")
        _insert_item(fresh_db, did, "Sub (12 Inch)", 1299, "Subs")

        result1 = backfill_variants_from_names(did)
        assert result1["groups_found"] == 1

        result2 = backfill_variants_from_names(did)
        assert result2["groups_found"] == 0  # idempotent

        items = get_draft_items(did)
        assert len(items) == 1
        assert len(items[0]["variants"]) == 2


# ===================================================================
# SECTION 8: Edge cases
# ===================================================================

class TestEdgeCases:
    """Edge cases for backward compatibility."""

    def test_single_variant_item(self, fresh_db):
        """Item with exactly 1 variant still expands in publish."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Soup of the Day", 599, "Soups")
        _insert_variant(fresh_db, iid, "Bowl", 599, "size", 0)

        rows = get_publish_rows(did)
        assert len(rows) == 1
        assert rows[0]["name"] == "Soup of the Day (Bowl)"
        assert rows[0]["price_cents"] == 599

    def test_many_variants(self, fresh_db):
        """Item with many variants expands all of them."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Milkshake", 499, "Beverages")
        flavors = ["Vanilla", "Chocolate", "Strawberry", "Banana", "Peanut Butter",
                    "Oreo", "Mint", "Caramel"]
        for i, f in enumerate(flavors):
            _insert_variant(fresh_db, iid, f, 499 + i * 50, "flavor", i)

        rows = get_publish_rows(did)
        assert len(rows) == 8
        assert rows[0]["name"] == "Milkshake (Vanilla)"
        assert rows[7]["name"] == "Milkshake (Caramel)"

    def test_item_with_empty_name_skipped(self, fresh_db):
        """Items with empty name are skipped in publish rows."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "", 999, "Pizza")
        _insert_item(fresh_db, did, "Salad", 799, "Salads")

        rows = get_publish_rows(did)
        assert len(rows) == 1
        assert rows[0]["name"] == "Salad"

    def test_variant_with_empty_label(self, fresh_db):
        """Variant with empty label uses parent name only."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Special", 1099, "Specials")
        _insert_variant(fresh_db, iid, "", 1099, "size", 0)

        rows = get_publish_rows(did)
        assert len(rows) == 1
        assert rows[0]["name"] == "Special"  # no "()" suffix

    def test_variant_zero_price(self, fresh_db):
        """Variant with 0 price still publishes (common for base option)."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza", 0, "Pizza")
        _insert_variant(fresh_db, iid, "Cheese", 0, "style", 0)
        _insert_variant(fresh_db, iid, "Pepperoni", 200, "style", 1)

        rows = get_publish_rows(did)
        assert len(rows) == 2
        assert rows[0]["price_cents"] == 0
        assert rows[1]["price_cents"] == 200

    def test_cascade_delete_item_removes_variants(self, fresh_db):
        """Deleting an item cascades to delete its variants."""
        from storage.drafts import get_draft_items

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza", 999, "Pizza")
        _insert_variant(fresh_db, iid, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid, "Large", 1599, "size", 1)

        # Delete the item
        fresh_db.execute("DELETE FROM draft_items WHERE id=?", (iid,))
        fresh_db.commit()

        # Verify variants are gone too
        rows = fresh_db.execute(
            "SELECT * FROM draft_item_variants WHERE item_id=?", (iid,)
        ).fetchall()
        assert len(rows) == 0

    def test_cascade_delete_draft_removes_items_and_variants(self, fresh_db):
        """Deleting a draft cascades to delete items and their variants."""
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza", 999, "Pizza")
        _insert_variant(fresh_db, iid, "Small", 999, "size", 0)

        # Delete the draft
        fresh_db.execute("DELETE FROM drafts WHERE id=?", (did,))
        fresh_db.commit()

        items = fresh_db.execute(
            "SELECT * FROM draft_items WHERE draft_id=?", (did,)
        ).fetchall()
        assert len(items) == 0

        variants = fresh_db.execute(
            "SELECT * FROM draft_item_variants WHERE item_id=?", (iid,)
        ).fetchall()
        assert len(variants) == 0

    def test_publish_rows_ordering_preserved(self, fresh_db):
        """Publish rows maintain item ordering (position)."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Dessert", 699, "Desserts", 3)
        iid = _insert_item(fresh_db, did, "Pizza", 999, "Pizza", 1)
        _insert_variant(fresh_db, iid, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid, "Large", 1599, "size", 1)
        _insert_item(fresh_db, did, "Salad", 799, "Salads", 2)

        rows = get_publish_rows(did)
        assert len(rows) == 4
        # Position order: Pizza(1) → Salad(2) → Dessert(3)
        assert rows[0]["name"] == "Pizza (Small)"
        assert rows[1]["name"] == "Pizza (Large)"
        assert rows[2]["name"] == "Salad"
        assert rows[3]["name"] == "Dessert"


# ===================================================================
# SECTION 9: Extraction path variant integration (smoke tests)
# ===================================================================

class TestExtractionPathVariants:
    """Verify extraction functions produce _variants that create DB rows."""

    def test_claude_items_to_draft_rows_with_sizes(self):
        """claude_items_to_draft_rows converts sizes[] to _variants."""
        from storage.ai_menu_extract import claude_items_to_draft_rows

        items = [
            {
                "name": "Cheese Pizza",
                "description": "Classic cheese",
                "price": 0,
                "category": "Pizza",
                "sizes": [
                    {"label": "Small", "price": 10.99},
                    {"label": "Large", "price": 15.99},
                ],
            },
        ]
        rows = claude_items_to_draft_rows(items)
        assert len(rows) == 1
        assert "_variants" in rows[0]
        assert len(rows[0]["_variants"]) == 2
        assert rows[0]["_variants"][0]["label"] == "Small"
        assert rows[0]["_variants"][0]["price_cents"] == 1099
        assert rows[0]["_variants"][1]["label"] == "Large"
        assert rows[0]["_variants"][1]["price_cents"] == 1599
        # Base price should be first variant price
        assert rows[0]["price_cents"] == 1099

    def test_claude_items_to_draft_rows_no_sizes(self):
        """claude_items_to_draft_rows without sizes has no _variants."""
        from storage.ai_menu_extract import claude_items_to_draft_rows

        items = [
            {
                "name": "Garden Salad",
                "description": "Fresh greens",
                "price": 7.99,
                "category": "Salads",
            },
        ]
        rows = claude_items_to_draft_rows(items)
        assert len(rows) == 1
        assert "_variants" not in rows[0]
        assert rows[0]["price_cents"] == 799

    def test_insert_claude_rows_creates_variant_rows(self, fresh_db):
        """Full pipeline: Claude extract → insert → load with variants."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.drafts import _insert_items_bulk, get_draft_items

        items = [
            {
                "name": "Pepperoni Pizza",
                "price": 0,
                "category": "Pizza",
                "sizes": [
                    {"label": "Personal", "price": 8.99},
                    {"label": "Medium", "price": 12.99},
                    {"label": "Large", "price": 16.99},
                ],
            },
            {
                "name": "Caesar Salad",
                "price": 9.99,
                "category": "Salads",
            },
        ]
        rows = claude_items_to_draft_rows(items)
        did = _create_draft(fresh_db)
        _insert_items_bulk(did, rows)

        loaded = get_draft_items(did)
        assert len(loaded) == 2

        pizza = next(i for i in loaded if i["name"] == "Pepperoni Pizza")
        salad = next(i for i in loaded if i["name"] == "Caesar Salad")

        assert len(pizza["variants"]) == 3
        assert pizza["variants"][0]["label"] == "Personal"
        assert pizza["variants"][0]["price_cents"] == 899
        assert pizza["price_cents"] == 899  # base = first variant

        assert len(salad["variants"]) == 0
        assert salad["price_cents"] == 999

    def test_full_pipeline_publish(self, fresh_db):
        """Full pipeline: extract → insert → publish rows."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.drafts import _insert_items_bulk, get_publish_rows

        items = [
            {
                "name": "Hawaiian Pizza",
                "price": 0,
                "category": "Pizza",
                "sizes": [
                    {"label": "Small", "price": 11.99},
                    {"label": "Large", "price": 17.99},
                ],
            },
            {
                "name": "Breadsticks",
                "price": 5.99,
                "category": "Sides",
            },
        ]
        rows = claude_items_to_draft_rows(items)
        did = _create_draft(fresh_db)
        _insert_items_bulk(did, rows)

        pub = get_publish_rows(did)
        assert len(pub) == 3
        assert pub[0]["name"] == "Hawaiian Pizza (Small)"
        assert pub[0]["price_cents"] == 1199
        assert pub[1]["name"] == "Hawaiian Pizza (Large)"
        assert pub[1]["price_cents"] == 1799
        assert pub[2]["name"] == "Breadsticks"
        assert pub[2]["price_cents"] == 599


# ===================================================================
# SECTION 10: Realistic menu scenarios
# ===================================================================

class TestRealisticMenuScenarios:
    """Full restaurant menu scenarios for backward compat."""

    def test_legacy_menu_no_variants(self, fresh_db):
        """A legacy menu (pre-Phase 9) with no variants loads/publishes fine."""
        from storage.drafts import get_draft_items, get_publish_rows

        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Margherita", 1299, "Pizza", 1)
        _insert_item(fresh_db, did, "Pepperoni", 1399, "Pizza", 2)
        _insert_item(fresh_db, did, "Caesar Salad", 899, "Salads", 3)
        _insert_item(fresh_db, did, "Garlic Bread", 599, "Sides", 4)
        _insert_item(fresh_db, did, "Tiramisu", 799, "Desserts", 5)

        items = get_draft_items(did)
        assert len(items) == 5
        for it in items:
            assert it["variants"] == []

        pub = get_publish_rows(did)
        assert len(pub) == 5
        # Names unchanged
        names = [r["name"] for r in pub]
        assert "Margherita" in names
        assert "Tiramisu" in names

    def test_modern_menu_with_sizes(self, fresh_db):
        """A Phase 9 menu with sized items publishes with expanded variants."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)

        # Pizza with sizes
        iid1 = _insert_item(fresh_db, did, "Margherita", 1099, "Pizza", 1)
        _insert_variant(fresh_db, iid1, "Personal", 1099, "size", 0)
        _insert_variant(fresh_db, iid1, "Medium", 1499, "size", 1)
        _insert_variant(fresh_db, iid1, "Large", 1899, "size", 2)

        # Pizza with sizes
        iid2 = _insert_item(fresh_db, did, "Pepperoni", 1199, "Pizza", 2)
        _insert_variant(fresh_db, iid2, "Personal", 1199, "size", 0)
        _insert_variant(fresh_db, iid2, "Medium", 1599, "size", 1)
        _insert_variant(fresh_db, iid2, "Large", 1999, "size", 2)

        # Single-price items
        _insert_item(fresh_db, did, "Caesar Salad", 899, "Salads", 3)
        _insert_item(fresh_db, did, "Garlic Bread", 599, "Sides", 4)

        pub = get_publish_rows(did)
        assert len(pub) == 8  # 3 + 3 + 1 + 1

        # Check pizza variants
        pizza_rows = [r for r in pub if "Margherita" in r["name"]]
        assert len(pizza_rows) == 3
        assert pizza_rows[0]["name"] == "Margherita (Personal)"
        assert pizza_rows[1]["name"] == "Margherita (Medium)"
        assert pizza_rows[2]["name"] == "Margherita (Large)"

        # Check single-price items
        salad = next(r for r in pub if r["name"] == "Caesar Salad")
        assert salad["price_cents"] == 899

    def test_combo_menu_publish(self, fresh_db):
        """Combo items with combo-kind variants expand correctly."""
        from storage.drafts import get_publish_rows

        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Cheeseburger", 1099, "Burgers", 1)
        _insert_variant(fresh_db, iid, "W/Fries", 1099, "combo", 0)
        _insert_variant(fresh_db, iid, "W/Onion Rings", 1299, "combo", 1)
        _insert_variant(fresh_db, iid, "W/Side Salad", 1199, "combo", 2)

        pub = get_publish_rows(did)
        assert len(pub) == 3
        assert pub[0]["name"] == "Cheeseburger (W/Fries)"
        assert pub[1]["name"] == "Cheeseburger (W/Onion Rings)"
        assert pub[2]["name"] == "Cheeseburger (W/Side Salad)"
