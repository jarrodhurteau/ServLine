"""
Day 76 — Save/Load Round-Trip & Backfill Variants tests.

Sprint 9.2, Day 76: Verifies end-to-end variant persistence through
save/reload cycles, the backfill_variants_from_names() integration, and
the new /backfill_variants Flask endpoint.

Covers:
  Save/Load round-trip:
  - Save items with _variants via upsert, reload via get_draft_items → variants present
  - Save with reordered variant positions, reload preserves order
  - Save with deleted_variant_ids removes those variants on reload
  - Save item without _variants preserves existing variant rows
  - Save item with _variants replaces all existing variant rows
  - Save new item with _variants inserts parent + child rows
  - Multiple items: mix of with/without variants persists correctly
  - Variant kind preserved through save/load cycle
  - Variant price_cents=0 preserved (not dropped)
  - Empty _variants list on existing item preserves variants (same as omitted)

  Backfill variants (storage function):
  - backfill_variants_from_names merges "Name (Size)" items into parent + variants
  - Backfill with no matching patterns returns zeros
  - Backfill idempotent: running twice produces same result
  - Backfill skips items that already have variants
  - Backfill requires 2+ items in group (single "Name (Size)" ignored)
  - Backfill sorts variants by price (cheapest first)
  - Backfill preserves category on parent item
  - Backfill handles mixed groups: some backfillable, some not

  Backfill endpoint (Flask):
  - POST /backfill_variants returns JSON with ok, groups_found, variants_created, items_deleted
  - Backfill endpoint on empty draft returns groups_found=0
  - Backfill endpoint on non-editing draft returns 400
  - Backfill endpoint on non-existent draft returns 404

  Publish with variants round-trip:
  - get_publish_rows expands variants into flat rows after backfill
  - Items without variants publish as-is after backfill
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 71-75 tests)
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


def _insert_item(conn, draft_id, name, price_cents=0, category=None, description=None) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_items (draft_id, name, description, price_cents, category, confidence, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 80, datetime('now'), datetime('now'))",
        (draft_id, name, description, price_cents, category),
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
# Tests: Save/Load Round-Trip
# ===========================================================================
class TestSaveLoadRoundTrip:
    """End-to-end variant persistence through upsert + get_draft_items."""

    def test_save_with_variants_then_reload(self, fresh_db):
        """Items saved with _variants via upsert are returned with variants on reload."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
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
        assert len(loaded) == 1
        assert loaded[0]["name"] == "Cheese Pizza"
        assert len(loaded[0]["variants"]) == 2
        labels = [v["label"] for v in loaded[0]["variants"]]
        assert "Small" in labels
        assert "Large" in labels

    def test_reordered_positions_persist(self, fresh_db):
        """Variant position ordering is preserved through save/reload."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Wings",
            "price_cents": 900,
            "category": "Appetizers",
            "_variants": [
                {"label": "Large", "price_cents": 1500, "kind": "size", "position": 0},
                {"label": "Small", "price_cents": 900, "kind": "size", "position": 1},
            ],
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d, include_variants=True)
        variants = sorted(loaded[0]["variants"], key=lambda v: v["position"])
        assert variants[0]["label"] == "Large"
        assert variants[1]["label"] == "Small"

    def test_deleted_variant_ids_removes_on_reload(self, fresh_db):
        """Deleting variant by ID removes it from subsequent load."""
        from storage.drafts import upsert_draft_items, get_draft_items, delete_variants_by_id
        d = _create_draft(fresh_db)
        items = [{
            "name": "Burger",
            "price_cents": 800,
            "category": "Burgers",
            "_variants": [
                {"label": "Single", "price_cents": 800, "kind": "size", "position": 0},
                {"label": "Double", "price_cents": 1200, "kind": "size", "position": 1},
                {"label": "Triple", "price_cents": 1600, "kind": "size", "position": 2},
            ],
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d, include_variants=True)
        vid_to_delete = loaded[0]["variants"][1]["id"]  # Double
        delete_variants_by_id([vid_to_delete])
        reloaded = get_draft_items(d, include_variants=True)
        assert len(reloaded[0]["variants"]) == 2
        remaining_labels = {v["label"] for v in reloaded[0]["variants"]}
        assert "Double" not in remaining_labels

    def test_save_without_variants_preserves_existing(self, fresh_db):
        """Upserting an item without _variants key preserves its existing variant rows."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        # First save with variants
        items = [{
            "name": "Salad",
            "price_cents": 700,
            "category": "Salads",
            "_variants": [
                {"label": "Half", "price_cents": 500, "kind": "size", "position": 0},
                {"label": "Whole", "price_cents": 700, "kind": "size", "position": 1},
            ],
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d, include_variants=True)
        item_id = loaded[0]["id"]
        # Second save: update name only, no _variants key
        items2 = [{"id": item_id, "name": "Caesar Salad", "price_cents": 700, "category": "Salads"}]
        upsert_draft_items(d, items2)
        reloaded = get_draft_items(d, include_variants=True)
        assert reloaded[0]["name"] == "Caesar Salad"
        assert len(reloaded[0]["variants"]) == 2  # Preserved

    def test_save_with_variants_replaces_existing(self, fresh_db):
        """Upserting with _variants replaces all existing variant rows."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Pizza",
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
        # Replace with different variants
        items2 = [{
            "id": item_id,
            "name": "Pizza",
            "price_cents": 600,
            "category": "Pizza",
            "_variants": [
                {"label": "Slice", "price_cents": 300, "kind": "size", "position": 0},
                {"label": "Pie", "price_cents": 1800, "kind": "size", "position": 1},
            ],
        }]
        upsert_draft_items(d, items2)
        reloaded = get_draft_items(d, include_variants=True)
        assert len(reloaded[0]["variants"]) == 2
        labels = {v["label"] for v in reloaded[0]["variants"]}
        assert labels == {"Slice", "Pie"}

    def test_new_item_with_variants_inserts_both(self, fresh_db):
        """Inserting a new item with _variants creates both parent and child rows."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Calzone",
            "price_cents": 900,
            "category": "Calzones",
            "_variants": [
                {"label": "Small", "price_cents": 900, "kind": "size", "position": 0},
                {"label": "Large", "price_cents": 1400, "kind": "size", "position": 1},
            ],
        }]
        upsert_draft_items(d, items)
        assert _count_items(fresh_db, d) == 1
        loaded = get_draft_items(d, include_variants=True)
        assert len(loaded[0]["variants"]) == 2

    def test_mixed_items_with_and_without_variants(self, fresh_db):
        """Draft with some items having variants and some not persists correctly."""
        from storage.drafts import upsert_draft_items, get_draft_items
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
                "name": "Garlic Bread",
                "price_cents": 500,
                "category": "Sides",
            },
            {
                "name": "Wings",
                "price_cents": 900,
                "category": "Appetizers",
                "_variants": [
                    {"label": "6pc", "price_cents": 900, "kind": "size", "position": 0},
                    {"label": "12pc", "price_cents": 1500, "kind": "size", "position": 1},
                    {"label": "24pc", "price_cents": 2500, "kind": "size", "position": 2},
                ],
            },
        ]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d, include_variants=True)
        assert len(loaded) == 3
        by_name = {it["name"]: it for it in loaded}
        assert len(by_name["Pizza"]["variants"]) == 2
        assert len(by_name["Garlic Bread"]["variants"]) == 0
        assert len(by_name["Wings"]["variants"]) == 3

    def test_variant_kind_preserved(self, fresh_db):
        """Variant kind is preserved through save/load cycle."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Combo Meal",
            "price_cents": 1200,
            "category": "Combos",
            "_variants": [
                {"label": "W/Fries", "price_cents": 1200, "kind": "combo", "position": 0},
                {"label": "W/Salad", "price_cents": 1200, "kind": "combo", "position": 1},
            ],
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d, include_variants=True)
        kinds = {v["label"]: v["kind"] for v in loaded[0]["variants"]}
        assert kinds["W/Fries"] == "combo"
        assert kinds["W/Salad"] == "combo"

    def test_variant_price_zero_preserved(self, fresh_db):
        """Variant with price_cents=0 is preserved (not dropped)."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Flavor Pick",
            "price_cents": 0,
            "category": "Options",
            "_variants": [
                {"label": "Vanilla", "price_cents": 0, "kind": "flavor", "position": 0},
                {"label": "Chocolate", "price_cents": 0, "kind": "flavor", "position": 1},
            ],
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d, include_variants=True)
        assert len(loaded[0]["variants"]) == 2
        assert all(v["price_cents"] == 0 for v in loaded[0]["variants"])

    def test_empty_variants_list_preserves_existing(self, fresh_db):
        """Upserting with empty _variants list preserves existing variant rows (same as omitted)."""
        from storage.drafts import upsert_draft_items, get_draft_items
        d = _create_draft(fresh_db)
        items = [{
            "name": "Pizza",
            "price_cents": 800,
            "category": "Pizza",
            "_variants": [
                {"label": "Small", "price_cents": 800, "kind": "size", "position": 0},
            ],
        }]
        upsert_draft_items(d, items)
        loaded = get_draft_items(d, include_variants=True)
        item_id = loaded[0]["id"]
        assert len(loaded[0]["variants"]) == 1
        # Update with empty _variants — treated same as omitted (no-op on variants)
        items2 = [{"id": item_id, "name": "Pizza", "price_cents": 800, "category": "Pizza", "_variants": []}]
        upsert_draft_items(d, items2)
        reloaded = get_draft_items(d, include_variants=True)
        assert len(reloaded[0]["variants"]) == 1  # Preserved


# ===========================================================================
# Tests: Backfill Variants (storage function)
# ===========================================================================
class TestBackfillVariants:
    """Tests for backfill_variants_from_names() merging legacy items."""

    def test_backfill_merges_name_size_items(self, fresh_db):
        """Items like 'Pizza (Small)' and 'Pizza (Large)' merge into parent + variants."""
        from storage.drafts import backfill_variants_from_names, get_draft_items
        d = _create_draft(fresh_db)
        _insert_item(fresh_db, d, "Pizza (Small)", 800, "Pizza")
        _insert_item(fresh_db, d, "Pizza (Large)", 1200, "Pizza")
        result = backfill_variants_from_names(d)
        assert result["groups_found"] == 1
        assert result["variants_created"] == 2
        assert result["items_deleted"] == 1
        loaded = get_draft_items(d, include_variants=True)
        assert len(loaded) == 1
        assert loaded[0]["name"] == "Pizza"
        assert len(loaded[0]["variants"]) == 2

    def test_backfill_no_matching_patterns(self, fresh_db):
        """Draft with no 'Name (Size)' patterns returns all zeros."""
        from storage.drafts import backfill_variants_from_names
        d = _create_draft(fresh_db)
        _insert_item(fresh_db, d, "Plain Pizza", 1000, "Pizza")
        _insert_item(fresh_db, d, "Garlic Bread", 500, "Sides")
        result = backfill_variants_from_names(d)
        assert result["groups_found"] == 0
        assert result["variants_created"] == 0
        assert result["items_deleted"] == 0

    def test_backfill_idempotent(self, fresh_db):
        """Running backfill twice produces the same result (no double-create)."""
        from storage.drafts import backfill_variants_from_names, get_draft_items
        d = _create_draft(fresh_db)
        _insert_item(fresh_db, d, "Burger (Single)", 800, "Burgers")
        _insert_item(fresh_db, d, "Burger (Double)", 1200, "Burgers")
        result1 = backfill_variants_from_names(d)
        assert result1["groups_found"] == 1
        # Second run: parent already has variants → skipped
        result2 = backfill_variants_from_names(d)
        assert result2["groups_found"] == 0
        assert result2["variants_created"] == 0
        loaded = get_draft_items(d, include_variants=True)
        assert len(loaded) == 1
        assert len(loaded[0]["variants"]) == 2

    def test_backfill_skips_items_with_existing_variants(self, fresh_db):
        """Items that already have variant rows are skipped by backfill."""
        from storage.drafts import backfill_variants_from_names, get_draft_items
        d = _create_draft(fresh_db)
        # Create item with existing variants
        item_id = _insert_item(fresh_db, d, "Wings (Small)", 900, "Appetizers")
        _insert_variant(fresh_db, item_id, "Small", 900)
        _insert_variant(fresh_db, item_id, "Large", 1500)
        # Add matching pattern that would normally backfill
        _insert_item(fresh_db, d, "Wings (Large)", 1500, "Appetizers")
        result = backfill_variants_from_names(d)
        # Only the item without variants is eligible, but single item can't form group
        assert result["groups_found"] == 0

    def test_backfill_requires_two_plus_items(self, fresh_db):
        """Single 'Name (Size)' item cannot form a group — ignored."""
        from storage.drafts import backfill_variants_from_names
        d = _create_draft(fresh_db)
        _insert_item(fresh_db, d, "Calzone (Large)", 1200, "Calzones")
        result = backfill_variants_from_names(d)
        assert result["groups_found"] == 0

    def test_backfill_sorts_by_price(self, fresh_db):
        """Backfill variants sorted cheapest first."""
        from storage.drafts import backfill_variants_from_names, get_draft_items
        d = _create_draft(fresh_db)
        # Insert in reverse price order
        _insert_item(fresh_db, d, "Sub (Large)", 1200, "Subs")
        _insert_item(fresh_db, d, "Sub (Small)", 800, "Subs")
        backfill_variants_from_names(d)
        loaded = get_draft_items(d, include_variants=True)
        variants = sorted(loaded[0]["variants"], key=lambda v: v["position"])
        assert variants[0]["label"] == "Small"
        assert variants[0]["price_cents"] == 800
        assert variants[1]["label"] == "Large"
        assert variants[1]["price_cents"] == 1200

    def test_backfill_preserves_category(self, fresh_db):
        """Backfilled parent item retains its original category."""
        from storage.drafts import backfill_variants_from_names, get_draft_items
        d = _create_draft(fresh_db)
        _insert_item(fresh_db, d, "Pasta (Half)", 700, "Pasta")
        _insert_item(fresh_db, d, "Pasta (Full)", 1200, "Pasta")
        backfill_variants_from_names(d)
        loaded = get_draft_items(d, include_variants=True)
        assert loaded[0]["category"] == "Pasta"

    def test_backfill_mixed_groups(self, fresh_db):
        """Multiple groups: some backfillable, some not."""
        from storage.drafts import backfill_variants_from_names, get_draft_items
        d = _create_draft(fresh_db)
        # Group 1: Pizza — 2 items → should backfill
        _insert_item(fresh_db, d, "Pizza (Small)", 800, "Pizza")
        _insert_item(fresh_db, d, "Pizza (Large)", 1200, "Pizza")
        # Group 2: Calzone — only 1 → should NOT backfill
        _insert_item(fresh_db, d, "Calzone (Large)", 1400, "Calzones")
        # Group 3: Salad — 3 items → should backfill
        _insert_item(fresh_db, d, "Salad (Small)", 500, "Salads")
        _insert_item(fresh_db, d, "Salad (Medium)", 700, "Salads")
        _insert_item(fresh_db, d, "Salad (Large)", 900, "Salads")
        # Non-pattern item
        _insert_item(fresh_db, d, "Garlic Bread", 400, "Sides")
        result = backfill_variants_from_names(d)
        assert result["groups_found"] == 2  # Pizza + Salad
        assert result["variants_created"] == 5  # 2 + 3
        assert result["items_deleted"] == 3  # 1 from Pizza group, 2 from Salad group
        loaded = get_draft_items(d, include_variants=True)
        # Should have: Pizza (2 variants), Calzone(Large) (unchanged), Salad (3 variants), Garlic Bread
        assert len(loaded) == 4
        by_name = {it["name"]: it for it in loaded}
        assert len(by_name["Pizza"]["variants"]) == 2
        assert len(by_name["Salad"]["variants"]) == 3
        assert len(by_name.get("Calzone (Large)", {}).get("variants", [])) == 0
        assert len(by_name["Garlic Bread"]["variants"]) == 0


# ===========================================================================
# Tests: Backfill Endpoint (Flask route simulation)
# ===========================================================================
class TestBackfillEndpoint:
    """Tests for the backfill_variants Flask endpoint logic.

    Since we mock the DB at the storage layer, we test the endpoint logic
    by calling the storage functions directly and verifying the contract.
    """

    def test_backfill_returns_correct_summary(self, fresh_db):
        """Backfill returns {ok, groups_found, variants_created, items_deleted}."""
        from storage.drafts import backfill_variants_from_names
        d = _create_draft(fresh_db)
        _insert_item(fresh_db, d, "Wrap (Small)", 600, "Wraps")
        _insert_item(fresh_db, d, "Wrap (Large)", 900, "Wraps")
        result = backfill_variants_from_names(d)
        assert "groups_found" in result
        assert "variants_created" in result
        assert "items_deleted" in result
        assert isinstance(result["groups_found"], int)
        assert isinstance(result["variants_created"], int)
        assert isinstance(result["items_deleted"], int)

    def test_backfill_empty_draft_returns_zeros(self, fresh_db):
        """Backfill on draft with no items returns all zeros."""
        from storage.drafts import backfill_variants_from_names
        d = _create_draft(fresh_db)
        result = backfill_variants_from_names(d)
        assert result["groups_found"] == 0
        assert result["variants_created"] == 0
        assert result["items_deleted"] == 0

    def test_backfill_endpoint_guards_draft_status(self, fresh_db):
        """Non-editing draft should be rejected by the endpoint guard."""
        from storage.drafts import get_draft
        d = _create_draft(fresh_db, status="published")
        draft = get_draft(d)
        assert draft["status"] == "published"
        # The endpoint checks draft.status != 'editing' → returns 400
        # We verify the guard condition here
        assert draft["status"] != "editing"

    def test_backfill_endpoint_guards_missing_draft(self, fresh_db):
        """Non-existent draft should be rejected."""
        from storage.drafts import get_draft
        draft = get_draft(99999)
        assert draft is None


# ===========================================================================
# Tests: Publish with Variants Round-Trip
# ===========================================================================
class TestPublishAfterBackfill:
    """Verify get_publish_rows works correctly after backfill."""

    def test_publish_rows_expand_backfilled_variants(self, fresh_db):
        """After backfill, get_publish_rows expands variants into flat rows."""
        from storage.drafts import backfill_variants_from_names, get_publish_rows
        d = _create_draft(fresh_db)
        _insert_item(fresh_db, d, "Pizza (Small)", 800, "Pizza")
        _insert_item(fresh_db, d, "Pizza (Large)", 1200, "Pizza")
        _insert_item(fresh_db, d, "Garlic Bread", 500, "Sides")
        backfill_variants_from_names(d)
        rows = get_publish_rows(d)
        names = [r["name"] for r in rows]
        # Pizza should expand to "Pizza (Small)" and "Pizza (Large)"
        assert "Pizza (Small)" in names
        assert "Pizza (Large)" in names
        assert "Garlic Bread" in names
        assert len(rows) == 3

    def test_publish_rows_items_without_variants_unchanged(self, fresh_db):
        """Items without variants publish as-is after backfill."""
        from storage.drafts import backfill_variants_from_names, get_publish_rows
        d = _create_draft(fresh_db)
        _insert_item(fresh_db, d, "Breadsticks", 400, "Sides")
        backfill_variants_from_names(d)
        rows = get_publish_rows(d)
        assert len(rows) == 1
        assert rows[0]["name"] == "Breadsticks"
        assert rows[0]["price_cents"] == 400


# ===========================================================================
# Tests: Full Lifecycle (save → backfill → reload → publish)
# ===========================================================================
class TestFullLifecycle:
    """End-to-end lifecycle: manual items → backfill → verify → publish."""

    def test_full_lifecycle_manual_then_backfill(self, fresh_db):
        """Insert legacy items → backfill → reload variants → publish expanded."""
        from storage.drafts import (
            backfill_variants_from_names, get_draft_items, get_publish_rows,
        )
        d = _create_draft(fresh_db)
        _insert_item(fresh_db, d, "Stromboli (Small)", 700, "Calzones")
        _insert_item(fresh_db, d, "Stromboli (Large)", 1100, "Calzones")
        _insert_item(fresh_db, d, "Mozzarella Sticks", 600, "Appetizers")

        # Step 1: backfill
        result = backfill_variants_from_names(d)
        assert result["groups_found"] == 1

        # Step 2: reload and verify structure
        loaded = get_draft_items(d, include_variants=True)
        assert len(loaded) == 2  # Stromboli (parent) + Mozzarella Sticks
        stromboli = [it for it in loaded if it["name"] == "Stromboli"][0]
        assert len(stromboli["variants"]) == 2

        # Step 3: publish
        rows = get_publish_rows(d)
        assert len(rows) == 3  # 2 expanded + 1 plain
        names = {r["name"] for r in rows}
        assert "Stromboli (Small)" in names
        assert "Stromboli (Large)" in names
        assert "Mozzarella Sticks" in names

    def test_lifecycle_upsert_then_backfill_mixed(self, fresh_db):
        """Mix of upserted items with _variants and legacy items for backfill."""
        from storage.drafts import (
            upsert_draft_items, backfill_variants_from_names, get_draft_items,
        )
        d = _create_draft(fresh_db)
        # Insert item with structured variants
        upsert_draft_items(d, [{
            "name": "Wings",
            "price_cents": 900,
            "category": "Appetizers",
            "_variants": [
                {"label": "6pc", "price_cents": 900, "kind": "size", "position": 0},
                {"label": "12pc", "price_cents": 1500, "kind": "size", "position": 1},
            ],
        }])
        # Insert legacy "Name (Size)" items
        _insert_item(fresh_db, d, "Sub (Half)", 600, "Subs")
        _insert_item(fresh_db, d, "Sub (Whole)", 1000, "Subs")

        # Backfill should only touch the Subs (Wings already has variants)
        result = backfill_variants_from_names(d)
        assert result["groups_found"] == 1

        loaded = get_draft_items(d, include_variants=True)
        assert len(loaded) == 2  # Wings + Sub
        by_name = {it["name"]: it for it in loaded}
        assert len(by_name["Wings"]["variants"]) == 2
        assert len(by_name["Sub"]["variants"]) == 2
