"""
Day 77 — Low-Confidence Panel & Bulk Operations tests.

Sprint 9.2, Day 77: Verifies that the low-confidence panel shows variant info
for flagged items, bulk category change preserves variants, delete-selected
cascades variant deletion, duplicate copies variants, and search includes
variant labels.

Covers:
  Low-confidence quality scoring with variants:
  - Items below quality threshold are flagged as low_confidence
  - Low-confidence items include variant data when loaded with include_variants
  - Items with variants preserve variant info in quality scoring context
  - Quality score unaffected by variant presence (no double-counting)
  - Zero-price parent with priced variants still flagged for price component

  Bulk category change preserves variants:
  - Upserting items with new category but no _variants preserves existing variants
  - Upserting multiple items' categories simultaneously preserves all variants
  - Category change + variant-bearing items: variants intact after reload
  - Category change on item without variants works normally

  Duplicate (clone_draft) copies variants:
  - clone_draft copies variant rows to new draft
  - Cloned variants have correct parent item references
  - Cloned variant labels, prices, kinds match originals
  - Clone draft with mix of variant/non-variant items

  Delete cascading:
  - Deleting parent item cascades to variant rows (FK constraint)
  - Deleting all items in draft cascades all variants
  - Deleting item via SQL leaves no orphan variants

  Search / filter with variants (contract tests):
  - get_draft_items with include_variants returns variant labels
  - Variant labels accessible for search matching
  - Items without variants have empty variants list

  Contract validation:
  - validate_draft_payload accepts items with _variants
  - validate_draft_payload accepts items with category change and _variants
  - validate_draft_payload rejects invalid _variants structure
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 71-76 tests)
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
                 description=None, confidence=80) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_items (draft_id, name, description, price_cents, category, confidence, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (draft_id, name, description, price_cents, category, confidence),
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


def _count_variants(conn, item_id=None) -> int:
    if item_id is not None:
        return conn.execute("SELECT COUNT(*) FROM draft_item_variants WHERE item_id=?", (item_id,)).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM draft_item_variants").fetchone()[0]


def _count_items(conn, draft_id) -> int:
    return conn.execute("SELECT COUNT(*) FROM draft_items WHERE draft_id=?", (draft_id,)).fetchone()[0]


# ===========================================================================
# Tests: Low-Confidence Quality Scoring with Variants
# ===========================================================================
class TestLowConfidenceWithVariants:
    """Items flagged as low-confidence include variant info when loaded."""

    def test_low_conf_items_include_variants(self, fresh_db):
        """Low-confidence items loaded with include_variants=True have variant data."""
        from storage.drafts import get_draft_items
        d = _create_draft(fresh_db)
        # Low confidence item (confidence=20 → score will be low)
        item_id = _insert_item(fresh_db, d, "Pz", 0, None, confidence=20)
        _insert_variant(fresh_db, item_id, "Small", 800, "size", 0)
        _insert_variant(fresh_db, item_id, "Large", 1200, "size", 1)
        loaded = get_draft_items(d, include_variants=True)
        assert len(loaded) == 1
        assert len(loaded[0]["variants"]) == 2
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert "Small" in labels
        assert "Large" in labels

    def test_quality_score_flags_low_items(self, fresh_db):
        """Items with poor attributes are correctly flagged below threshold."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'portal'))
        from app import _compute_item_quality, QUALITY_LOW_THRESHOLD

        # Bad item: short name, no price, no category, low confidence
        bad_item = {"name": "X", "price_cents": 0, "category": None, "confidence": 20}
        score, is_low = _compute_item_quality(bad_item)
        assert is_low is True
        assert score < QUALITY_LOW_THRESHOLD

        # Good item: long name, price, category, high confidence
        good_item = {"name": "Cheese Pizza", "price_cents": 1200, "category": "Pizza", "confidence": 95}
        score, is_low = _compute_item_quality(good_item)
        assert is_low is False
        assert score >= QUALITY_LOW_THRESHOLD

    def test_quality_score_unaffected_by_variants(self, fresh_db):
        """Quality score is the same whether variants are present or not."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'portal'))
        from app import _compute_item_quality

        base = {"name": "Pizza", "price_cents": 800, "category": "Pizza", "confidence": 85}
        score_without, _ = _compute_item_quality(base)

        with_variants = dict(base)
        with_variants["variants"] = [
            {"label": "Small", "price_cents": 800},
            {"label": "Large", "price_cents": 1200},
        ]
        score_with, _ = _compute_item_quality(with_variants)
        assert score_without == score_with

    def test_zero_price_parent_still_flagged(self, fresh_db):
        """Parent with zero price is penalized even if variants have prices."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'portal'))
        from app import _compute_item_quality

        item = {"name": "Cheese Pizza", "price_cents": 0, "category": "Pizza", "confidence": 85}
        score, _ = _compute_item_quality(item)
        # -20 for zero price → score should be 80 or lower
        assert score <= 80

    def test_low_conf_item_variants_accessible_for_display(self, fresh_db):
        """Low-confidence items have variant labels accessible for panel display."""
        from storage.drafts import get_draft_items
        d = _create_draft(fresh_db)
        item_id = _insert_item(fresh_db, d, "Ab", 0, None, confidence=10)
        _insert_variant(fresh_db, item_id, "Small", 500, "size", 0)
        _insert_variant(fresh_db, item_id, "Medium", 700, "size", 1)
        _insert_variant(fresh_db, item_id, "Large", 900, "size", 2)
        loaded = get_draft_items(d, include_variants=True)
        item = loaded[0]
        # Simulate what the template does: map(attribute='label')|join(', ')
        var_labels = ", ".join(v["label"] for v in item["variants"])
        assert var_labels == "Small, Medium, Large"


# ===========================================================================
# Tests: Bulk Category Change Preserves Variants
# ===========================================================================
class TestBulkCategoryPreservesVariants:
    """Changing category on items (without sending _variants) preserves variants."""

    def test_category_change_without_variants_preserves(self, fresh_db):
        """Upserting with new category but no _variants key preserves existing variants."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        # Create item with variants
        items = [{
            "name": "Cheese Pizza",
            "price_cents": 800,
            "category": "Pizza",
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                {"label": "Large", "price_cents": 1200, "kind": "size", "position": 1},
            ],
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d, include_variants=True)
        item_id = loaded[0]["id"]
        assert len(loaded[0]["variants"]) == 2

        # Bulk category change: update category only, no _variants
        items2 = [{"id": item_id, "name": "Cheese Pizza", "price_cents": 800, "category": "Specialty Pizza"}]
        upsert_draft_items(d, items2)
        reloaded = get_draft_items(d, include_variants=True)
        assert reloaded[0]["category"] == "Specialty Pizza"
        assert len(reloaded[0]["variants"]) == 2
        labels = {v["label"] for v in reloaded[0]["variants"]}
        assert labels == {"Small", "Large"}

    def test_multi_item_category_change_preserves_all_variants(self, fresh_db):
        """Changing category on multiple items simultaneously preserves all variants."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [
            {
                "name": "Pizza A",
                "price_cents": 800,
                "category": "Pizza",
                "_variants": [
                    {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                    {"label": "Large", "price_cents": 1200, "kind": "size", "position": 1},
                ],
            },
            {
                "name": "Pizza B",
                "price_cents": 900,
                "category": "Pizza",
                "_variants": [
                    {"label": "Slice", "price_cents": 300, "kind": "size", "position": 0},
                    {"label": "Pie", "price_cents": 1800, "kind": "size", "position": 1},
                ],
            },
        ]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d, include_variants=True)
        assert len(loaded) == 2

        # Bulk change: both items to "Specialty Pizza", no _variants
        items2 = [
            {"id": loaded[0]["id"], "name": "Pizza A", "price_cents": 800, "category": "Specialty Pizza"},
            {"id": loaded[1]["id"], "name": "Pizza B", "price_cents": 900, "category": "Specialty Pizza"},
        ]
        upsert_draft_items(d, items2)
        reloaded = get_draft_items(d, include_variants=True)
        for it in reloaded:
            assert it["category"] == "Specialty Pizza"
            assert len(it["variants"]) == 2

    def test_category_change_with_explicit_variants_also_works(self, fresh_db):
        """If _variants IS sent alongside category change, replace strategy works."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Wings",
            "price_cents": 900,
            "category": "Appetizers",
            "_variants": [
                {"label": "6pc", "price_cents": 900, "kind": "size", "position": 0},
                {"label": "12pc", "price_cents": 1500, "kind": "size", "position": 1},
            ],
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d, include_variants=True)
        item_id = loaded[0]["id"]

        # Category change WITH explicit variants (same variants)
        items2 = [{
            "id": item_id,
            "name": "Wings",
            "price_cents": 900,
            "category": "Starters",
            "_variants": [
                {"label": "6pc", "price_cents": 900, "kind": "size", "position": 0},
                {"label": "12pc", "price_cents": 1500, "kind": "size", "position": 1},
            ],
        }]
        upsert_draft_items(d, items2)
        reloaded = get_draft_items(d, include_variants=True)
        assert reloaded[0]["category"] == "Starters"
        assert len(reloaded[0]["variants"]) == 2

    def test_category_change_on_item_without_variants(self, fresh_db):
        """Category change on plain item (no variants) works normally."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{"name": "Garlic Bread", "price_cents": 500, "category": "Sides"}]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d, include_variants=True)
        item_id = loaded[0]["id"]
        assert len(loaded[0]["variants"]) == 0

        items2 = [{"id": item_id, "name": "Garlic Bread", "price_cents": 500, "category": "Appetizers"}]
        upsert_draft_items(d, items2)
        reloaded = get_draft_items(d, include_variants=True)
        assert reloaded[0]["category"] == "Appetizers"
        assert len(reloaded[0]["variants"]) == 0


# ===========================================================================
# Tests: Duplicate (clone_draft) Copies Variants
# ===========================================================================
class TestDuplicateCopiesVariants:
    """clone_draft preserves variant rows for all items."""

    def test_clone_copies_variants(self, fresh_db):
        """Cloned draft has same variant rows as original."""
        from storage.drafts import upsert_draft_items, get_draft_items, clone_draft
        d = _create_draft(fresh_db)
        items = [{
            "name": "Pizza",
            "price_cents": 800,
            "category": "Pizza",
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                {"label": "Medium", "price_cents": 1000, "kind": "size", "position": 1},
                {"label": "Large", "price_cents": 1200, "kind": "size", "position": 2},
            ],
        }]
        upsert_draft_items(d, items)

        result = clone_draft(d)
        new_id = result["id"]
        cloned = get_draft_items(new_id, include_variants=True)
        assert len(cloned) == 1
        assert cloned[0]["name"] == "Pizza"
        assert len(cloned[0]["variants"]) == 3
        labels = [v["label"] for v in sorted(cloned[0]["variants"], key=lambda v: v["position"])]
        assert labels == ["Small", "Medium", "Large"]

    def test_cloned_variants_have_correct_parent(self, fresh_db):
        """Cloned variant rows reference the new parent item, not the original."""
        from storage.drafts import upsert_draft_items, get_draft_items, clone_draft
        d = _create_draft(fresh_db)
        items = [{
            "name": "Sub",
            "price_cents": 600,
            "category": "Subs",
            "_variants": [
                {"label": "Half", "price_cents": 600, "kind": "size", "position": 0},
                {"label": "Whole", "price_cents": 1000, "kind": "size", "position": 1},
            ],
        }]
        upsert_draft_items(d, items)
        orig_items = get_draft_items(d, include_variants=True)
        orig_item_id = orig_items[0]["id"]

        result = clone_draft(d)
        new_id = result["id"]
        cloned = get_draft_items(new_id, include_variants=True)
        cloned_item_id = cloned[0]["id"]
        assert cloned_item_id != orig_item_id

        # Verify variant parent references in DB
        rows = fresh_db.execute(
            "SELECT item_id FROM draft_item_variants WHERE item_id=?", (cloned_item_id,)
        ).fetchall()
        assert len(rows) == 2  # Both variants point to new parent

    def test_cloned_variant_data_matches(self, fresh_db):
        """Cloned variant labels, prices, and kinds match originals."""
        from storage.drafts import upsert_draft_items, get_draft_items, clone_draft
        d = _create_draft(fresh_db)
        items = [{
            "name": "Combo Meal",
            "price_cents": 1200,
            "category": "Combos",
            "_variants": [
                {"label": "W/Fries", "price_cents": 1200, "kind": "combo", "position": 0},
                {"label": "W/Salad", "price_cents": 1200, "kind": "combo", "position": 1},
                {"label": "W/Soup", "price_cents": 1300, "kind": "combo", "position": 2},
            ],
        }]
        upsert_draft_items(d, items)

        result = clone_draft(d)
        cloned = get_draft_items(result["id"], include_variants=True)
        variants = sorted(cloned[0]["variants"], key=lambda v: v["position"])
        assert variants[0]["label"] == "W/Fries"
        assert variants[0]["kind"] == "combo"
        assert variants[0]["price_cents"] == 1200
        assert variants[1]["label"] == "W/Salad"
        assert variants[2]["label"] == "W/Soup"
        assert variants[2]["price_cents"] == 1300

    def test_clone_mixed_variant_nonvariant(self, fresh_db):
        """Cloning draft with mix of variant and non-variant items preserves both."""
        from storage.drafts import upsert_draft_items, get_draft_items, clone_draft
        d = _create_draft(fresh_db)
        items = [
            {
                "name": "Pizza",
                "price_cents": 800,
                "category": "Pizza",
                "_variants": [
                    {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                    {"label": "Large", "price_cents": 1200, "kind": "size", "position": 1},
                ],
            },
            {
                "name": "Breadsticks",
                "price_cents": 400,
                "category": "Sides",
            },
        ]
        upsert_draft_items(d, items)

        result = clone_draft(d)
        cloned = get_draft_items(result["id"], include_variants=True)
        assert len(cloned) == 2
        by_name = {it["name"]: it for it in cloned}
        assert len(by_name["Pizza"]["variants"]) == 2
        assert len(by_name["Breadsticks"]["variants"]) == 0


# ===========================================================================
# Tests: Delete Cascading
# ===========================================================================
class TestDeleteCascading:
    """Deleting parent items cascades to variant rows via FK constraint."""

    def test_delete_parent_cascades_variants(self, fresh_db):
        """Deleting a parent item via SQL cascades to all its variants."""
        d = _create_draft(fresh_db)
        item_id = _insert_item(fresh_db, d, "Pizza", 800, "Pizza")
        _insert_variant(fresh_db, item_id, "Small", 800)
        _insert_variant(fresh_db, item_id, "Large", 1200)
        assert _count_variants(fresh_db, item_id) == 2

        # Delete parent
        fresh_db.execute("DELETE FROM draft_items WHERE id=?", (item_id,))
        fresh_db.commit()
        assert _count_variants(fresh_db, item_id) == 0

    def test_delete_all_items_cascades_all_variants(self, fresh_db):
        """Deleting all items in a draft removes all associated variants."""
        d = _create_draft(fresh_db)
        id1 = _insert_item(fresh_db, d, "Pizza", 800, "Pizza")
        _insert_variant(fresh_db, id1, "Small", 800)
        _insert_variant(fresh_db, id1, "Large", 1200)
        id2 = _insert_item(fresh_db, d, "Wings", 900, "Appetizers")
        _insert_variant(fresh_db, id2, "6pc", 900)
        assert _count_variants(fresh_db) == 3

        fresh_db.execute("DELETE FROM draft_items WHERE draft_id=?", (d,))
        fresh_db.commit()
        assert _count_variants(fresh_db) == 0

    def test_delete_draft_cascades_items_and_variants(self, fresh_db):
        """Deleting the entire draft cascades through items to variants."""
        d = _create_draft(fresh_db)
        item_id = _insert_item(fresh_db, d, "Burger", 1000, "Burgers")
        _insert_variant(fresh_db, item_id, "Single", 1000)
        _insert_variant(fresh_db, item_id, "Double", 1400)
        assert _count_items(fresh_db, d) == 1
        assert _count_variants(fresh_db) == 2

        fresh_db.execute("DELETE FROM drafts WHERE id=?", (d,))
        fresh_db.commit()
        assert _count_items(fresh_db, d) == 0
        assert _count_variants(fresh_db) == 0

    def test_no_orphan_variants_after_item_delete(self, fresh_db):
        """After deleting parent, no variant rows exist with that item_id."""
        d = _create_draft(fresh_db)
        id1 = _insert_item(fresh_db, d, "Pizza", 800, "Pizza")
        _insert_variant(fresh_db, id1, "Small", 800)
        id2 = _insert_item(fresh_db, d, "Wings", 900, "Apps")
        _insert_variant(fresh_db, id2, "6pc", 900)

        # Delete only Pizza
        fresh_db.execute("DELETE FROM draft_items WHERE id=?", (id1,))
        fresh_db.commit()
        orphans = fresh_db.execute(
            "SELECT COUNT(*) FROM draft_item_variants WHERE item_id=?", (id1,)
        ).fetchone()[0]
        assert orphans == 0
        # Wings variants still intact
        assert _count_variants(fresh_db, id2) == 1


# ===========================================================================
# Tests: Search / Filter with Variants
# ===========================================================================
class TestSearchWithVariants:
    """Variant labels are accessible for search/filter matching."""

    def test_variant_labels_accessible(self, fresh_db):
        """get_draft_items returns variant labels that can be searched."""
        from storage.drafts import get_draft_items
        d = _create_draft(fresh_db)
        item_id = _insert_item(fresh_db, d, "Pizza", 800, "Pizza")
        _insert_variant(fresh_db, item_id, "Small", 800)
        _insert_variant(fresh_db, item_id, "Medium", 1000)
        _insert_variant(fresh_db, item_id, "Large", 1200)

        loaded = get_draft_items(d, include_variants=True)
        all_labels = []
        for it in loaded:
            for v in it.get("variants", []):
                all_labels.append(v["label"].lower())
        assert "small" in all_labels
        assert "medium" in all_labels
        assert "large" in all_labels

    def test_items_without_variants_have_empty_list(self, fresh_db):
        """Items without variants return empty variants list for consistent search."""
        from storage.drafts import get_draft_items
        d = _create_draft(fresh_db)
        _insert_item(fresh_db, d, "Breadsticks", 400, "Sides")
        loaded = get_draft_items(d, include_variants=True)
        assert loaded[0]["variants"] == []

    def test_mixed_search_context(self, fresh_db):
        """Search context includes both item names and variant labels."""
        from storage.drafts import get_draft_items
        d = _create_draft(fresh_db)
        id1 = _insert_item(fresh_db, d, "Pizza", 800, "Pizza")
        _insert_variant(fresh_db, id1, "Personal", 600)
        _insert_variant(fresh_db, id1, "Family", 1800)
        _insert_item(fresh_db, d, "Garlic Bread", 400, "Sides")

        loaded = get_draft_items(d, include_variants=True)
        # Simulate search for "family"
        query = "family"
        matches = []
        for it in loaded:
            name_match = query in it["name"].lower()
            var_match = any(query in v["label"].lower() for v in it.get("variants", []))
            if name_match or var_match:
                matches.append(it["name"])
        assert "Pizza" in matches  # matched via variant label "Family"
        assert "Garlic Bread" not in matches


# ===========================================================================
# Tests: Contract Validation with Variants + Category
# ===========================================================================
class TestContractValidation:
    """validate_draft_payload works correctly for bulk operations with variants."""

    def test_payload_with_category_and_variants_valid(self, fresh_db):
        """Payload with category change and _variants passes validation."""
        from portal.contracts import validate_draft_payload
        payload = {
            "draft_id": 1,
            "items": [{
                "id": 1,
                "name": "Pizza",
                "price_cents": 800,
                "category": "Specialty Pizza",
                "_variants": [
                    {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
                ],
            }],
        }
        ok, err = validate_draft_payload(payload)
        assert ok, f"Unexpected error: {err}"

    def test_payload_category_only_no_variants_valid(self, fresh_db):
        """Payload with only category change (no _variants) passes validation."""
        from portal.contracts import validate_draft_payload
        payload = {
            "draft_id": 1,
            "items": [
                {"id": 1, "name": "Pizza A", "price_cents": 800, "category": "New Category"},
                {"id": 2, "name": "Pizza B", "price_cents": 900, "category": "New Category"},
            ],
        }
        ok, err = validate_draft_payload(payload)
        assert ok, f"Unexpected error: {err}"

    def test_payload_with_deleted_variant_ids_valid(self, fresh_db):
        """Payload with deleted_variant_ids passes validation."""
        from portal.contracts import validate_draft_payload
        payload = {
            "draft_id": 1,
            "items": [{"name": "Pizza", "price_cents": 800}],
            "deleted_variant_ids": [10, 20, 30],
        }
        ok, err = validate_draft_payload(payload)
        assert ok, f"Unexpected error: {err}"

    def test_payload_invalid_variant_label_rejected(self, fresh_db):
        """Payload with empty variant label is rejected."""
        from portal.contracts import validate_draft_payload
        payload = {
            "draft_id": 1,
            "items": [{
                "name": "Pizza",
                "price_cents": 800,
                "_variants": [
                    {"label": "  ", "price_cents": 800},
                ],
            }],
        }
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "label" in err.lower()

    def test_payload_invalid_variant_kind_rejected(self, fresh_db):
        """Payload with invalid variant kind is rejected."""
        from portal.contracts import validate_draft_payload
        payload = {
            "draft_id": 1,
            "items": [{
                "name": "Pizza",
                "price_cents": 800,
                "_variants": [
                    {"label": "Small", "price_cents": 800, "kind": "invalid_kind"},
                ],
            }],
        }
        ok, err = validate_draft_payload(payload)
        assert not ok
        assert "kind" in err.lower()
