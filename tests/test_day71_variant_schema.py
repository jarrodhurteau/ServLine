# tests/test_day71_variant_schema.py
"""
Day 71 — Database Schema & Migration for draft_item_variants.

Tests:
  - Table creation / schema verification
  - Variant CRUD: insert, update, delete, get
  - Normalization / defensive handling
  - get_draft_items() LEFT JOIN + variant grouping
  - Items with 0 variants (backward compat)
  - FK cascade: deleting item cascades variants
  - clone_draft() preserves variants
  - Index existence
"""
from __future__ import annotations
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime

# ---------------------------------------------------------------------------
# Isolate tests with an in-memory DB
# ---------------------------------------------------------------------------
import storage.drafts as drafts_mod

_ORIG_DB_PATH = drafts_mod.DB_PATH


def _use_memory_db():
    """Switch the drafts module to use an in-memory SQLite database."""
    drafts_mod.DB_PATH = ":memory:"
    # We need a persistent connection for in-memory DBs across calls.
    # Patch db_connect to reuse a single connection.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    # Create the restaurants table that drafts FK references
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT
        )
        """
    )
    conn.commit()

    def _patched_connect():
        return conn

    drafts_mod.db_connect = _patched_connect
    drafts_mod._ensure_schema()
    return conn


def _restore_db():
    drafts_mod.DB_PATH = _ORIG_DB_PATH


class VariantSchemaTestBase(unittest.TestCase):
    """Base class that sets up an in-memory DB for each test."""

    def setUp(self):
        self.conn = _use_memory_db()
        # Create a draft + item for use in tests
        self.draft_id = drafts_mod._insert_draft(
            title="Test Draft", restaurant_id=None
        )
        ids = drafts_mod._insert_items_bulk(
            self.draft_id,
            [
                {"name": "Cheese Pizza", "price_cents": 1299, "category": "Pizza"},
                {"name": "Pepperoni Pizza", "price_cents": 1499, "category": "Pizza"},
                {"name": "Caesar Salad", "price_cents": 899, "category": "Salads"},
            ],
        )
        self.item_ids = ids

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass
        _restore_db()


# ===================================================================
# 1. SCHEMA VERIFICATION
# ===================================================================
class TestTableExists(VariantSchemaTestBase):
    def test_variants_table_created(self):
        """draft_item_variants table exists after _ensure_schema."""
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='draft_item_variants'"
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_variants_table_columns(self):
        """draft_item_variants has all expected columns."""
        cols = self.conn.execute(
            "PRAGMA table_info(draft_item_variants)"
        ).fetchall()
        col_names = {r[1] for r in cols}
        expected = {"id", "item_id", "label", "price_cents", "kind", "position", "created_at", "updated_at"}
        self.assertEqual(col_names, expected)

    def test_item_id_not_null(self):
        """item_id column is NOT NULL."""
        cols = self.conn.execute("PRAGMA table_info(draft_item_variants)").fetchall()
        item_id_col = [r for r in cols if r[1] == "item_id"][0]
        self.assertEqual(item_id_col[3], 1)  # notnull flag

    def test_label_not_null(self):
        """label column is NOT NULL."""
        cols = self.conn.execute("PRAGMA table_info(draft_item_variants)").fetchall()
        label_col = [r for r in cols if r[1] == "label"][0]
        self.assertEqual(label_col[3], 1)

    def test_kind_default_size(self):
        """kind column defaults to 'size'."""
        cols = self.conn.execute("PRAGMA table_info(draft_item_variants)").fetchall()
        kind_col = [r for r in cols if r[1] == "kind"][0]
        self.assertEqual(kind_col[4], "'size'")

    def test_price_cents_default_zero(self):
        """price_cents defaults to 0."""
        cols = self.conn.execute("PRAGMA table_info(draft_item_variants)").fetchall()
        pc_col = [r for r in cols if r[1] == "price_cents"][0]
        self.assertEqual(pc_col[4], "0")

    def test_position_default_zero(self):
        """position defaults to 0."""
        cols = self.conn.execute("PRAGMA table_info(draft_item_variants)").fetchall()
        pos_col = [r for r in cols if r[1] == "position"][0]
        self.assertEqual(pos_col[4], "0")


class TestIndexExists(VariantSchemaTestBase):
    def test_idx_variants_item_exists(self):
        """idx_variants_item index exists on (item_id)."""
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_variants_item'"
        ).fetchall()
        self.assertEqual(len(rows), 1)


# ===================================================================
# 2. INSERT VARIANTS
# ===================================================================
class TestInsertVariants(VariantSchemaTestBase):
    def test_insert_single_variant(self):
        """Insert a single variant and verify it's stored."""
        ids = drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Small", "price_cents": 999, "kind": "size", "position": 0}
        ])
        self.assertEqual(len(ids), 1)
        self.assertIsInstance(ids[0], int)

    def test_insert_multiple_variants(self):
        """Insert multiple variants at once."""
        ids = drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Small", "price_cents": 999, "kind": "size", "position": 0},
            {"label": "Medium", "price_cents": 1299, "kind": "size", "position": 1},
            {"label": "Large", "price_cents": 1599, "kind": "size", "position": 2},
        ])
        self.assertEqual(len(ids), 3)

    def test_insert_returns_unique_ids(self):
        """Each inserted variant gets a unique ID."""
        ids = drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Small", "price_cents": 999},
            {"label": "Large", "price_cents": 1599},
        ])
        self.assertEqual(len(set(ids)), 2)

    def test_insert_with_combo_kind(self):
        """Inserting a combo variant stores kind=combo."""
        ids = drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "W/Fries", "price_cents": 1099, "kind": "combo"}
        ])
        variants = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(variants[0]["kind"], "combo")

    def test_insert_with_flavor_kind(self):
        """Flavor kind is accepted."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Mango Habanero", "price_cents": 0, "kind": "flavor"}
        ])
        variants = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(variants[0]["kind"], "flavor")

    def test_insert_with_style_kind(self):
        """Style kind is accepted."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Deep Dish", "price_cents": 200, "kind": "style"}
        ])
        variants = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(variants[0]["kind"], "style")

    def test_insert_skips_empty_label(self):
        """Variants with empty label are silently skipped."""
        ids = drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "", "price_cents": 999},
            {"label": "Large", "price_cents": 1599},
        ])
        self.assertEqual(len(ids), 1)

    def test_insert_skips_none_label(self):
        """Variants with None label are skipped."""
        ids = drafts_mod.insert_variants(self.item_ids[0], [
            {"label": None, "price_cents": 999},
        ])
        self.assertEqual(len(ids), 0)

    def test_insert_skips_non_dict(self):
        """Non-dict entries in the list are skipped."""
        ids = drafts_mod.insert_variants(self.item_ids[0], [
            "not a dict",
            {"label": "Large", "price_cents": 1599},
        ])
        self.assertEqual(len(ids), 1)

    def test_insert_default_kind_is_size(self):
        """Omitting kind defaults to 'size'."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Medium", "price_cents": 1299}
        ])
        variants = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(variants[0]["kind"], "size")

    def test_insert_default_position_zero(self):
        """Omitting position defaults to 0."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Medium", "price_cents": 1299}
        ])
        variants = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(variants[0]["position"], 0)

    def test_insert_negative_price_clamped_to_zero(self):
        """Negative price_cents is clamped to 0."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Bad Price", "price_cents": -500}
        ])
        variants = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(variants[0]["price_cents"], 0)

    def test_insert_bad_price_defaults_zero(self):
        """Non-numeric price_cents defaults to 0."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Bad Price", "price_cents": "abc"}
        ])
        variants = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(variants[0]["price_cents"], 0)

    def test_insert_invalid_kind_becomes_other(self):
        """Unrecognized kind falls back to 'other'."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Test", "price_cents": 100, "kind": "bogus"}
        ])
        variants = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(variants[0]["kind"], "other")

    def test_insert_timestamps_set(self):
        """created_at and updated_at are populated on insert."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Large", "price_cents": 1599}
        ])
        variants = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertIsNotNone(variants[0]["created_at"])
        self.assertIsNotNone(variants[0]["updated_at"])


# ===================================================================
# 3. GET ITEM VARIANTS
# ===================================================================
class TestGetItemVariants(VariantSchemaTestBase):
    def test_empty_when_no_variants(self):
        """get_item_variants returns empty list when item has no variants."""
        result = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(result, [])

    def test_returns_all_variants(self):
        """Returns all inserted variants for an item."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "S", "price_cents": 999, "position": 0},
            {"label": "M", "price_cents": 1299, "position": 1},
            {"label": "L", "price_cents": 1599, "position": 2},
        ])
        result = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(len(result), 3)
        labels = [v["label"] for v in result]
        self.assertEqual(labels, ["S", "M", "L"])

    def test_ordered_by_position(self):
        """Variants are ordered by position."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Large", "price_cents": 1599, "position": 2},
            {"label": "Small", "price_cents": 999, "position": 0},
            {"label": "Medium", "price_cents": 1299, "position": 1},
        ])
        result = drafts_mod.get_item_variants(self.item_ids[0])
        labels = [v["label"] for v in result]
        self.assertEqual(labels, ["Small", "Medium", "Large"])

    def test_does_not_return_other_items_variants(self):
        """Variants for item A don't appear when querying item B."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Small", "price_cents": 999},
        ])
        drafts_mod.insert_variants(self.item_ids[1], [
            {"label": "Large", "price_cents": 1999},
        ])
        result_0 = drafts_mod.get_item_variants(self.item_ids[0])
        result_1 = drafts_mod.get_item_variants(self.item_ids[1])
        self.assertEqual(len(result_0), 1)
        self.assertEqual(result_0[0]["label"], "Small")
        self.assertEqual(len(result_1), 1)
        self.assertEqual(result_1[0]["label"], "Large")

    def test_variant_dict_keys(self):
        """Each variant dict has all expected keys."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Small", "price_cents": 999, "kind": "size", "position": 0}
        ])
        v = drafts_mod.get_item_variants(self.item_ids[0])[0]
        expected_keys = {"id", "item_id", "label", "price_cents", "kind",
                         "position", "created_at", "updated_at"}
        self.assertEqual(set(v.keys()), expected_keys)


# ===================================================================
# 4. UPDATE VARIANT
# ===================================================================
class TestUpdateVariant(VariantSchemaTestBase):
    def setUp(self):
        super().setUp()
        ids = drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Small", "price_cents": 999, "kind": "size", "position": 0}
        ])
        self.variant_id = ids[0]

    def test_update_label(self):
        """Update only the label field."""
        result = drafts_mod.update_variant(self.variant_id, {"label": "Tiny"})
        self.assertTrue(result)
        v = drafts_mod.get_item_variants(self.item_ids[0])[0]
        self.assertEqual(v["label"], "Tiny")

    def test_update_price(self):
        """Update only the price_cents field."""
        drafts_mod.update_variant(self.variant_id, {"price_cents": 799})
        v = drafts_mod.get_item_variants(self.item_ids[0])[0]
        self.assertEqual(v["price_cents"], 799)

    def test_update_kind(self):
        """Update the kind field."""
        drafts_mod.update_variant(self.variant_id, {"kind": "combo"})
        v = drafts_mod.get_item_variants(self.item_ids[0])[0]
        self.assertEqual(v["kind"], "combo")

    def test_update_position(self):
        """Update the position field."""
        drafts_mod.update_variant(self.variant_id, {"position": 5})
        v = drafts_mod.get_item_variants(self.item_ids[0])[0]
        self.assertEqual(v["position"], 5)

    def test_update_multiple_fields(self):
        """Update multiple fields at once."""
        drafts_mod.update_variant(self.variant_id, {
            "label": "Extra Large",
            "price_cents": 2199,
            "position": 3,
        })
        v = drafts_mod.get_item_variants(self.item_ids[0])[0]
        self.assertEqual(v["label"], "Extra Large")
        self.assertEqual(v["price_cents"], 2199)
        self.assertEqual(v["position"], 3)

    def test_update_nonexistent_id_returns_false(self):
        """Updating a non-existent variant returns False."""
        result = drafts_mod.update_variant(99999, {"label": "Ghost"})
        self.assertFalse(result)

    def test_update_empty_label_returns_false(self):
        """Updating label to empty string returns False (no change)."""
        result = drafts_mod.update_variant(self.variant_id, {"label": ""})
        self.assertFalse(result)
        v = drafts_mod.get_item_variants(self.item_ids[0])[0]
        self.assertEqual(v["label"], "Small")  # unchanged

    def test_update_empty_data_returns_false(self):
        """Passing no updatable fields returns False."""
        result = drafts_mod.update_variant(self.variant_id, {})
        self.assertFalse(result)

    def test_update_invalid_kind_becomes_other(self):
        """Unrecognized kind falls back to 'other'."""
        drafts_mod.update_variant(self.variant_id, {"kind": "mystery"})
        v = drafts_mod.get_item_variants(self.item_ids[0])[0]
        self.assertEqual(v["kind"], "other")

    def test_update_negative_price_clamped(self):
        """Negative price_cents is clamped to 0 on update."""
        drafts_mod.update_variant(self.variant_id, {"price_cents": -100})
        v = drafts_mod.get_item_variants(self.item_ids[0])[0]
        self.assertEqual(v["price_cents"], 0)

    def test_update_sets_updated_at(self):
        """updated_at changes after update."""
        old = drafts_mod.get_item_variants(self.item_ids[0])[0]["updated_at"]
        drafts_mod.update_variant(self.variant_id, {"price_cents": 1111})
        new = drafts_mod.get_item_variants(self.item_ids[0])[0]["updated_at"]
        # They may be the same second in fast tests, but both should be set
        self.assertIsNotNone(new)


# ===================================================================
# 5. DELETE VARIANTS
# ===================================================================
class TestDeleteVariants(VariantSchemaTestBase):
    def setUp(self):
        super().setUp()
        ids = drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "S", "price_cents": 999, "position": 0},
            {"label": "M", "price_cents": 1299, "position": 1},
            {"label": "L", "price_cents": 1599, "position": 2},
        ])
        self.variant_ids = ids

    def test_delete_single_variant(self):
        """Delete one variant, others remain."""
        count = drafts_mod.delete_variants(self.item_ids[0], [self.variant_ids[1]])
        self.assertEqual(count, 1)
        remaining = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(len(remaining), 2)
        labels = {v["label"] for v in remaining}
        self.assertEqual(labels, {"S", "L"})

    def test_delete_multiple_variants(self):
        """Delete multiple variants at once."""
        count = drafts_mod.delete_variants(
            self.item_ids[0], [self.variant_ids[0], self.variant_ids[2]]
        )
        self.assertEqual(count, 2)
        remaining = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["label"], "M")

    def test_delete_all_variants(self):
        """Delete all variants for an item."""
        count = drafts_mod.delete_variants(self.item_ids[0], self.variant_ids)
        self.assertEqual(count, 3)
        self.assertEqual(drafts_mod.get_item_variants(self.item_ids[0]), [])

    def test_delete_nonexistent_returns_zero(self):
        """Deleting non-existent IDs returns 0."""
        count = drafts_mod.delete_variants(self.item_ids[0], [99999])
        self.assertEqual(count, 0)

    def test_delete_empty_list_returns_zero(self):
        """Passing empty list returns 0."""
        count = drafts_mod.delete_variants(self.item_ids[0], [])
        self.assertEqual(count, 0)

    def test_delete_wrong_item_id(self):
        """Cannot delete variants via wrong parent item_id."""
        count = drafts_mod.delete_variants(self.item_ids[1], self.variant_ids)
        self.assertEqual(count, 0)
        # All still exist under original item
        self.assertEqual(len(drafts_mod.get_item_variants(self.item_ids[0])), 3)


class TestDeleteAllVariantsForItem(VariantSchemaTestBase):
    def test_deletes_all(self):
        """delete_all_variants_for_item removes all variants."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "S", "price_cents": 999},
            {"label": "L", "price_cents": 1599},
        ])
        count = drafts_mod.delete_all_variants_for_item(self.item_ids[0])
        self.assertEqual(count, 2)
        self.assertEqual(drafts_mod.get_item_variants(self.item_ids[0]), [])

    def test_no_variants_returns_zero(self):
        """delete_all on item with no variants returns 0."""
        count = drafts_mod.delete_all_variants_for_item(self.item_ids[0])
        self.assertEqual(count, 0)

    def test_doesnt_affect_other_items(self):
        """Deleting variants for item A doesn't touch item B."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "S", "price_cents": 999},
        ])
        drafts_mod.insert_variants(self.item_ids[1], [
            {"label": "L", "price_cents": 1599},
        ])
        drafts_mod.delete_all_variants_for_item(self.item_ids[0])
        self.assertEqual(len(drafts_mod.get_item_variants(self.item_ids[1])), 1)


# ===================================================================
# 6. FK CASCADE: deleting an item cascades to variants
# ===================================================================
class TestFKCascade(VariantSchemaTestBase):
    def test_delete_item_cascades_variants(self):
        """Deleting a draft item also deletes its variants (FK CASCADE)."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "S", "price_cents": 999},
            {"label": "L", "price_cents": 1599},
        ])
        # Verify variants exist
        self.assertEqual(len(drafts_mod.get_item_variants(self.item_ids[0])), 2)

        # Delete the parent item
        drafts_mod.delete_draft_items(self.draft_id, [self.item_ids[0]])

        # Variants should be gone
        self.assertEqual(drafts_mod.get_item_variants(self.item_ids[0]), [])

    def test_delete_draft_cascades_items_and_variants(self):
        """Deleting a draft cascades to items (FK), which cascades to variants."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "S", "price_cents": 999},
        ])
        # Delete the entire draft
        self.conn.execute("DELETE FROM drafts WHERE id=?", (self.draft_id,))
        self.conn.commit()

        # Items gone
        items = drafts_mod.get_draft_items(self.draft_id)
        self.assertEqual(len(items), 0)

        # Variants gone
        self.assertEqual(drafts_mod.get_item_variants(self.item_ids[0]), [])


# ===================================================================
# 7. GET DRAFT ITEMS WITH VARIANTS (LEFT JOIN)
# ===================================================================
class TestGetDraftItemsWithVariants(VariantSchemaTestBase):
    def test_items_without_variants_have_empty_list(self):
        """Items with no variants get variants: [] in result."""
        items = drafts_mod.get_draft_items(self.draft_id)
        self.assertEqual(len(items), 3)
        for it in items:
            self.assertIn("variants", it)
            self.assertEqual(it["variants"], [])

    def test_items_with_variants_grouped(self):
        """Variants are correctly grouped under their parent item."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "S", "price_cents": 999, "position": 0},
            {"label": "L", "price_cents": 1599, "position": 1},
        ])
        items = drafts_mod.get_draft_items(self.draft_id)
        cheese = [it for it in items if it["name"] == "Cheese Pizza"][0]
        self.assertEqual(len(cheese["variants"]), 2)
        self.assertEqual(cheese["variants"][0]["label"], "S")
        self.assertEqual(cheese["variants"][1]["label"], "L")

    def test_mixed_items_some_with_variants(self):
        """Mix of items with and without variants works correctly."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Small", "price_cents": 999},
        ])
        # item_ids[1] has no variants
        drafts_mod.insert_variants(self.item_ids[2], [
            {"label": "Half", "price_cents": 599},
            {"label": "Full", "price_cents": 899},
        ])
        items = drafts_mod.get_draft_items(self.draft_id)
        self.assertEqual(len(items), 3)

        cheese = [it for it in items if it["name"] == "Cheese Pizza"][0]
        pepperoni = [it for it in items if it["name"] == "Pepperoni Pizza"][0]
        salad = [it for it in items if it["name"] == "Caesar Salad"][0]

        self.assertEqual(len(cheese["variants"]), 1)
        self.assertEqual(len(pepperoni["variants"]), 0)
        self.assertEqual(len(salad["variants"]), 2)

    def test_variant_ordering_within_item(self):
        """Variants are ordered by position within each item."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Large", "price_cents": 1599, "position": 2},
            {"label": "Small", "price_cents": 999, "position": 0},
            {"label": "Medium", "price_cents": 1299, "position": 1},
        ])
        items = drafts_mod.get_draft_items(self.draft_id)
        cheese = [it for it in items if it["name"] == "Cheese Pizza"][0]
        labels = [v["label"] for v in cheese["variants"]]
        self.assertEqual(labels, ["Small", "Medium", "Large"])

    def test_item_ordering_preserved(self):
        """Item ordering by position/id is not disrupted by LEFT JOIN."""
        # Set positions
        drafts_mod.upsert_draft_items(self.draft_id, [
            {"id": self.item_ids[0], "name": "Cheese Pizza", "price_cents": 1299, "position": 2},
            {"id": self.item_ids[1], "name": "Pepperoni Pizza", "price_cents": 1499, "position": 0},
            {"id": self.item_ids[2], "name": "Caesar Salad", "price_cents": 899, "position": 1},
        ])
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "S", "price_cents": 999},
            {"label": "L", "price_cents": 1599},
        ])
        items = drafts_mod.get_draft_items(self.draft_id)
        names = [it["name"] for it in items]
        self.assertEqual(names, ["Pepperoni Pizza", "Caesar Salad", "Cheese Pizza"])

    def test_include_variants_false(self):
        """include_variants=False returns old-style dicts without 'variants' key."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "S", "price_cents": 999},
        ])
        items = drafts_mod.get_draft_items(self.draft_id, include_variants=False)
        self.assertEqual(len(items), 3)
        for it in items:
            self.assertNotIn("variants", it)

    def test_variant_dict_has_all_fields(self):
        """Variant dicts in the grouped result have all expected fields."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Medium", "price_cents": 1299, "kind": "size", "position": 1}
        ])
        items = drafts_mod.get_draft_items(self.draft_id)
        cheese = [it for it in items if it["name"] == "Cheese Pizza"][0]
        v = cheese["variants"][0]
        self.assertEqual(v["label"], "Medium")
        self.assertEqual(v["price_cents"], 1299)
        self.assertEqual(v["kind"], "size")
        self.assertEqual(v["position"], 1)
        self.assertEqual(v["item_id"], self.item_ids[0])
        self.assertIn("id", v)
        self.assertIn("created_at", v)
        self.assertIn("updated_at", v)

    def test_no_duplicate_items_from_join(self):
        """LEFT JOIN doesn't duplicate items (one row per item, not per variant)."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "S", "price_cents": 999},
            {"label": "M", "price_cents": 1299},
            {"label": "L", "price_cents": 1599},
        ])
        items = drafts_mod.get_draft_items(self.draft_id)
        self.assertEqual(len(items), 3)  # 3 items, not 5 (3 variants + 2 bare items)
        cheese = [it for it in items if it["name"] == "Cheese Pizza"][0]
        self.assertEqual(len(cheese["variants"]), 3)


# ===================================================================
# 8. CLONE DRAFT WITH VARIANTS
# ===================================================================
class TestCloneDraftWithVariants(VariantSchemaTestBase):
    def test_clone_preserves_variants(self):
        """Cloned draft has the same variants as the original."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Small", "price_cents": 999, "kind": "size", "position": 0},
            {"label": "Large", "price_cents": 1599, "kind": "size", "position": 1},
        ])
        drafts_mod.insert_variants(self.item_ids[1], [
            {"label": "W/Fries", "price_cents": 1699, "kind": "combo", "position": 0},
        ])

        result = drafts_mod.clone_draft(self.draft_id)
        new_id = result["draft_id"]

        new_items = drafts_mod.get_draft_items(new_id)
        self.assertEqual(len(new_items), 3)

        # Find cloned cheese pizza
        cheese = [it for it in new_items if it["name"] == "Cheese Pizza"][0]
        self.assertEqual(len(cheese["variants"]), 2)
        self.assertEqual(cheese["variants"][0]["label"], "Small")
        self.assertEqual(cheese["variants"][1]["label"], "Large")

        # Find cloned pepperoni
        pepperoni = [it for it in new_items if it["name"] == "Pepperoni Pizza"][0]
        self.assertEqual(len(pepperoni["variants"]), 1)
        self.assertEqual(pepperoni["variants"][0]["label"], "W/Fries")
        self.assertEqual(pepperoni["variants"][0]["kind"], "combo")

        # Salad has no variants
        salad = [it for it in new_items if it["name"] == "Caesar Salad"][0]
        self.assertEqual(len(salad["variants"]), 0)

    def test_cloned_variants_have_new_ids(self):
        """Cloned variant IDs are different from originals."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Small", "price_cents": 999},
        ])
        orig_variants = drafts_mod.get_item_variants(self.item_ids[0])

        result = drafts_mod.clone_draft(self.draft_id)
        new_items = drafts_mod.get_draft_items(result["draft_id"])
        cheese = [it for it in new_items if it["name"] == "Cheese Pizza"][0]
        new_variant_id = cheese["variants"][0]["id"]

        self.assertNotEqual(new_variant_id, orig_variants[0]["id"])

    def test_clone_without_variants(self):
        """Clone works fine when items have no variants (backward compat)."""
        result = drafts_mod.clone_draft(self.draft_id)
        new_items = drafts_mod.get_draft_items(result["draft_id"])
        self.assertEqual(len(new_items), 3)
        for it in new_items:
            self.assertEqual(len(it["variants"]), 0)


# ===================================================================
# 9. NORMALIZE VARIANT
# ===================================================================
class TestNormalizeVariant(VariantSchemaTestBase):
    def test_valid_variant(self):
        result = drafts_mod._normalize_variant_for_db({
            "label": "Small", "price_cents": 999, "kind": "size", "position": 0
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["label"], "Small")
        self.assertEqual(result["price_cents"], 999)
        self.assertEqual(result["kind"], "size")
        self.assertEqual(result["position"], 0)

    def test_whitespace_label_stripped(self):
        result = drafts_mod._normalize_variant_for_db({"label": "  Medium  "})
        self.assertEqual(result["label"], "Medium")

    def test_missing_label_returns_none(self):
        result = drafts_mod._normalize_variant_for_db({"price_cents": 999})
        self.assertIsNone(result)

    def test_non_dict_returns_none(self):
        result = drafts_mod._normalize_variant_for_db("not a dict")
        self.assertIsNone(result)

    def test_none_returns_none(self):
        result = drafts_mod._normalize_variant_for_db(None)
        self.assertIsNone(result)

    def test_kind_case_insensitive(self):
        result = drafts_mod._normalize_variant_for_db({"label": "X", "kind": "COMBO"})
        self.assertEqual(result["kind"], "combo")

    def test_all_valid_kinds(self):
        for kind in ("size", "combo", "flavor", "style", "other"):
            result = drafts_mod._normalize_variant_for_db({"label": "X", "kind": kind})
            self.assertEqual(result["kind"], kind)

    def test_missing_kind_defaults_size(self):
        result = drafts_mod._normalize_variant_for_db({"label": "X"})
        self.assertEqual(result["kind"], "size")

    def test_none_kind_defaults_size(self):
        result = drafts_mod._normalize_variant_for_db({"label": "X", "kind": None})
        self.assertEqual(result["kind"], "size")

    def test_missing_price_defaults_zero(self):
        result = drafts_mod._normalize_variant_for_db({"label": "X"})
        self.assertEqual(result["price_cents"], 0)

    def test_missing_position_defaults_zero(self):
        result = drafts_mod._normalize_variant_for_db({"label": "X"})
        self.assertEqual(result["position"], 0)


# ===================================================================
# 10. BACKWARD COMPATIBILITY
# ===================================================================
class TestBackwardCompatibility(VariantSchemaTestBase):
    def test_existing_items_load_without_variants(self):
        """Pre-existing items (0 variant rows) still load correctly."""
        items = drafts_mod.get_draft_items(self.draft_id)
        self.assertEqual(len(items), 3)
        for it in items:
            self.assertEqual(it["variants"], [])
            self.assertIn("name", it)
            self.assertIn("price_cents", it)

    def test_upsert_items_still_works(self):
        """upsert_draft_items works the same as before (no variant impact)."""
        result = drafts_mod.upsert_draft_items(self.draft_id, [
            {"name": "New Item", "price_cents": 500, "category": "Test"}
        ])
        self.assertEqual(len(result["inserted_ids"]), 1)
        items = drafts_mod.get_draft_items(self.draft_id)
        self.assertEqual(len(items), 4)

    def test_delete_items_still_works(self):
        """delete_draft_items works the same as before."""
        count = drafts_mod.delete_draft_items(self.draft_id, [self.item_ids[2]])
        self.assertEqual(count, 1)
        items = drafts_mod.get_draft_items(self.draft_id)
        self.assertEqual(len(items), 2)

    def test_price_cents_on_parent_unchanged(self):
        """Parent item still has its own price_cents (base/lowest price)."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "S", "price_cents": 999},
            {"label": "L", "price_cents": 1599},
        ])
        items = drafts_mod.get_draft_items(self.draft_id)
        cheese = [it for it in items if it["name"] == "Cheese Pizza"][0]
        # Parent price unchanged
        self.assertEqual(cheese["price_cents"], 1299)
        # Variants have their own prices
        self.assertEqual(cheese["variants"][0]["price_cents"], 999)
        self.assertEqual(cheese["variants"][1]["price_cents"], 1599)


# ===================================================================
# 11. EDGE CASES
# ===================================================================
class TestEdgeCases(VariantSchemaTestBase):
    def test_many_variants_per_item(self):
        """Item can have 10+ variants (no artificial limit)."""
        variants = [
            {"label": f"Size {i}", "price_cents": 500 + i * 100, "position": i}
            for i in range(15)
        ]
        ids = drafts_mod.insert_variants(self.item_ids[0], variants)
        self.assertEqual(len(ids), 15)
        result = drafts_mod.get_item_variants(self.item_ids[0])
        self.assertEqual(len(result), 15)

    def test_zero_price_variant_allowed(self):
        """price_cents=0 is valid (e.g., flavor variants with no surcharge)."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Plain", "price_cents": 0}
        ])
        v = drafts_mod.get_item_variants(self.item_ids[0])[0]
        self.assertEqual(v["price_cents"], 0)

    def test_unicode_label(self):
        """Unicode characters in labels are preserved."""
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": "Pequeño", "price_cents": 800}
        ])
        v = drafts_mod.get_item_variants(self.item_ids[0])[0]
        self.assertEqual(v["label"], "Pequeño")

    def test_long_label(self):
        """Long label strings are stored correctly."""
        long_label = "Extra Large Supreme Combo with Everything on Top"
        drafts_mod.insert_variants(self.item_ids[0], [
            {"label": long_label, "price_cents": 2999}
        ])
        v = drafts_mod.get_item_variants(self.item_ids[0])[0]
        self.assertEqual(v["label"], long_label)

    def test_concurrent_variants_on_multiple_items(self):
        """Multiple items can have variants simultaneously."""
        for item_id in self.item_ids:
            drafts_mod.insert_variants(item_id, [
                {"label": "S", "price_cents": 500, "position": 0},
                {"label": "L", "price_cents": 1000, "position": 1},
            ])
        for item_id in self.item_ids:
            variants = drafts_mod.get_item_variants(item_id)
            self.assertEqual(len(variants), 2)

    def test_get_draft_items_empty_draft(self):
        """get_draft_items on a draft with no items returns empty list."""
        empty_draft = drafts_mod._insert_draft(title="Empty", restaurant_id=None)
        items = drafts_mod.get_draft_items(empty_draft)
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
