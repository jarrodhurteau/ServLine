"""
Day 112 — Sprint 12.1: Extraction Pipeline → Modifier Groups + Kitchen Name Wiring
====================================================================================
Tests for:
  1. _build_modifier_groups_from_claude() — Claude output → _modifier_groups format
  2. claude_items_to_draft_rows() — modifier_groups field + backward compat (sizes)
  3. _insert_items_bulk() — kitchen_name + _modifier_groups persisted to DB
  4. upsert_draft_items() — kitchen_name INSERT/UPDATE + _modifier_groups insert/replace
  5. _normalize_item_for_db() — kitchen_name field extraction

38 tests across 5 classes.
"""

import sqlite3
import pytest
import storage.drafts as drafts_mod
from storage.drafts import (
    _normalize_item_for_db,
    _insert_items_bulk,
    upsert_draft_items,
    get_draft_items,
    get_modifier_groups,
)
from storage.ai_menu_extract import (
    _build_modifier_groups_from_claude,
    claude_items_to_draft_rows,
)


# ---------------------------------------------------------------------------
# Schema / fixtures
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS restaurants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    active INTEGER DEFAULT 1
);
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
);
CREATE TABLE IF NOT EXISTS draft_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    price_cents INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    position INTEGER,
    confidence INTEGER,
    kitchen_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS draft_item_variants (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id           INTEGER NOT NULL,
    label             TEXT NOT NULL,
    price_cents       INTEGER NOT NULL DEFAULT 0,
    kind              TEXT DEFAULT 'size',
    position          INTEGER DEFAULT 0,
    modifier_group_id INTEGER,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES draft_items(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS draft_modifier_groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    required    INTEGER DEFAULT 0,
    min_select  INTEGER DEFAULT 0,
    max_select  INTEGER DEFAULT 0,
    position    INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES draft_items(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS draft_modifier_group_templates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER,
    name          TEXT NOT NULL,
    required      INTEGER DEFAULT 0,
    min_select    INTEGER DEFAULT 0,
    max_select    INTEGER DEFAULT 0,
    position      INTEGER DEFAULT 0,
    modifiers     TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

_NOW = "2026-03-12T10:00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    return conn


def _seed(conn):
    """Return (restaurant_id, draft_id)."""
    rid = conn.execute(
        "INSERT INTO restaurants (name) VALUES ('Taqueria')"
    ).lastrowid
    did = conn.execute(
        "INSERT INTO drafts (title, restaurant_id, status, created_at, updated_at) "
        "VALUES ('Menu', ?, 'editing', ?, ?)",
        (rid, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return rid, did


@pytest.fixture
def conn(monkeypatch):
    c = _make_conn()
    monkeypatch.setattr(drafts_mod, "db_connect", lambda: c)
    return c


@pytest.fixture
def draft_id(conn):
    _, did = _seed(conn)
    return did


# ---------------------------------------------------------------------------
# 1. _build_modifier_groups_from_claude
# ---------------------------------------------------------------------------

class TestBuildModifierGroupsFromClaude:
    def test_basic_group(self):
        raw = [{"name": "Sauce Choice", "required": True, "min_select": 1,
                "max_select": 1, "modifiers": [
                    {"label": "Ranch", "price": 0.0},
                    {"label": "BBQ", "price": 0.5},
                ]}]
        groups = _build_modifier_groups_from_claude(raw)
        assert len(groups) == 1
        g = groups[0]
        assert g["name"] == "Sauce Choice"
        assert g["required"] is True
        assert g["min_select"] == 1
        assert g["max_select"] == 1
        assert len(g["_modifiers"]) == 2

    def test_modifier_price_converted_to_cents(self):
        raw = [{"name": "Add-Ons", "required": False, "min_select": 0,
                "max_select": 3, "modifiers": [
                    {"label": "Cheese", "price": 1.00},
                    {"label": "Bacon", "price": 1.50},
                ]}]
        groups = _build_modifier_groups_from_claude(raw)
        mods = groups[0]["_modifiers"]
        assert mods[0]["price_cents"] == 100
        assert mods[1]["price_cents"] == 150

    def test_modifier_kind_defaults_to_other(self):
        raw = [{"name": "Extras", "modifiers": [{"label": "Guac", "price": 2.0}]}]
        groups = _build_modifier_groups_from_claude(raw)
        assert groups[0]["_modifiers"][0]["kind"] == "other"

    def test_multiple_groups(self):
        raw = [
            {"name": "Size", "required": True, "modifiers": [
                {"label": "Small", "price": 8.0},
                {"label": "Large", "price": 12.0},
            ]},
            {"name": "Bread", "required": True, "modifiers": [
                {"label": "White", "price": 0.0},
                {"label": "Wheat", "price": 0.0},
            ]},
        ]
        groups = _build_modifier_groups_from_claude(raw)
        assert len(groups) == 2
        assert groups[0]["name"] == "Size"
        assert groups[1]["name"] == "Bread"

    def test_group_position_assigned(self):
        raw = [
            {"name": "First", "modifiers": []},
            {"name": "Second", "modifiers": []},
        ]
        groups = _build_modifier_groups_from_claude(raw)
        assert groups[0]["position"] == 0
        assert groups[1]["position"] == 1

    def test_modifier_position_assigned(self):
        raw = [{"name": "Sauces", "modifiers": [
            {"label": "Hot", "price": 0.0},
            {"label": "Mild", "price": 0.0},
            {"label": "BBQ", "price": 0.0},
        ]}]
        mods = _build_modifier_groups_from_claude(raw)[0]["_modifiers"]
        assert [m["position"] for m in mods] == [0, 1, 2]

    def test_skips_nameless_group(self):
        raw = [{"name": "", "modifiers": [{"label": "X", "price": 0}]},
               {"name": "Valid", "modifiers": []}]
        groups = _build_modifier_groups_from_claude(raw)
        assert len(groups) == 1
        assert groups[0]["name"] == "Valid"

    def test_skips_non_dict_group(self):
        raw = ["not a dict", {"name": "Real", "modifiers": []}]
        groups = _build_modifier_groups_from_claude(raw)
        assert len(groups) == 1

    def test_skips_nameless_modifier(self):
        raw = [{"name": "G", "modifiers": [
            {"label": "", "price": 1.0},
            {"label": "Good", "price": 1.0},
        ]}]
        mods = _build_modifier_groups_from_claude(raw)[0]["_modifiers"]
        assert len(mods) == 1
        assert mods[0]["label"] == "Good"

    def test_empty_list_returns_empty(self):
        assert _build_modifier_groups_from_claude([]) == []

    def test_non_list_input_handled(self):
        # Should not crash on None or non-list
        assert _build_modifier_groups_from_claude(None or []) == []

    def test_defaults_when_fields_missing(self):
        raw = [{"name": "Toppings", "modifiers": [{"label": "X", "price": 0}]}]
        g = _build_modifier_groups_from_claude(raw)[0]
        assert g["required"] is False
        assert g["min_select"] == 0
        assert g["max_select"] == 0

    def test_group_with_zero_price_modifier(self):
        raw = [{"name": "Gratis", "modifiers": [{"label": "Free Option", "price": 0.0}]}]
        mods = _build_modifier_groups_from_claude(raw)[0]["_modifiers"]
        assert mods[0]["price_cents"] == 0


# ---------------------------------------------------------------------------
# 2. claude_items_to_draft_rows — modifier_groups field
# ---------------------------------------------------------------------------

class TestClaudeItemsToDraftRows:
    def test_modifier_groups_included(self):
        items = [{
            "name": "Burrito",
            "price": 9.99,
            "category": "Entrees",
            "modifier_groups": [
                {"name": "Protein", "required": True, "min_select": 1,
                 "max_select": 1, "modifiers": [
                     {"label": "Chicken", "price": 0.0},
                     {"label": "Beef", "price": 1.0},
                 ]},
            ],
        }]
        rows = claude_items_to_draft_rows(items)
        assert len(rows) == 1
        assert "_modifier_groups" in rows[0]
        assert len(rows[0]["_modifier_groups"]) == 1
        assert rows[0]["_modifier_groups"][0]["name"] == "Protein"

    def test_sizes_still_produce_variants(self):
        items = [{
            "name": "Coffee",
            "price": 0.0,
            "category": "Beverages",
            "sizes": [
                {"label": "Small", "price": 2.5},
                {"label": "Large", "price": 4.0},
            ],
        }]
        rows = claude_items_to_draft_rows(items)
        assert "_variants" in rows[0]
        assert len(rows[0]["_variants"]) == 2
        assert "_modifier_groups" not in rows[0]

    def test_both_sizes_and_modifier_groups(self):
        """Items can have both sizes (ungrouped variants) and modifier groups."""
        items = [{
            "name": "Sandwich",
            "price": 8.0,
            "category": "Sandwiches",
            "sizes": [{"label": "Half", "price": 5.0}, {"label": "Full", "price": 8.0}],
            "modifier_groups": [
                {"name": "Bread", "modifiers": [{"label": "White", "price": 0.0}]},
            ],
        }]
        rows = claude_items_to_draft_rows(items)
        assert "_variants" in rows[0]
        assert "_modifier_groups" in rows[0]

    def test_empty_modifier_groups_not_included(self):
        items = [{"name": "Salad", "price": 7.0, "category": "Salads",
                  "modifier_groups": []}]
        rows = claude_items_to_draft_rows(items)
        assert "_modifier_groups" not in rows[0]

    def test_no_modifier_groups_field_backward_compat(self):
        items = [{"name": "Pizza", "price": 12.0, "category": "Pizza"}]
        rows = claude_items_to_draft_rows(items)
        assert "_modifier_groups" not in rows[0]
        assert "_variants" not in rows[0]

    def test_modifier_price_in_cents(self):
        items = [{
            "name": "Wings",
            "price": 10.0,
            "category": "Wings",
            "modifier_groups": [{"name": "Sauce", "modifiers": [
                {"label": "Hot", "price": 0.0},
                {"label": "Garlic Parm", "price": 0.75},
            ]}],
        }]
        rows = claude_items_to_draft_rows(items)
        mods = rows[0]["_modifier_groups"][0]["_modifiers"]
        assert mods[1]["price_cents"] == 75

    def test_position_assigned_to_rows(self):
        items = [
            {"name": "Item A", "price": 1.0, "category": "Other"},
            {"name": "Item B", "price": 2.0, "category": "Other"},
        ]
        rows = claude_items_to_draft_rows(items)
        assert rows[0]["position"] == 1
        assert rows[1]["position"] == 2

    def test_confidence_set_to_90(self):
        items = [{"name": "Taco", "price": 3.5, "category": "Other"}]
        rows = claude_items_to_draft_rows(items)
        assert rows[0]["confidence"] == 90

    def test_nameless_items_skipped(self):
        items = [
            {"name": "", "price": 5.0, "category": "Other"},
            {"name": "Valid", "price": 5.0, "category": "Other"},
        ]
        rows = claude_items_to_draft_rows(items)
        assert len(rows) == 1
        assert rows[0]["name"] == "Valid"


# ---------------------------------------------------------------------------
# 3. _normalize_item_for_db — kitchen_name field
# ---------------------------------------------------------------------------

class TestNormalizeItemKitchenName:
    def test_kitchen_name_present(self):
        norm = _normalize_item_for_db({"name": "Burger", "kitchen_name": "BRG"})
        assert norm["kitchen_name"] == "BRG"

    def test_kitchen_name_absent(self):
        norm = _normalize_item_for_db({"name": "Burger"})
        assert norm["kitchen_name"] is None

    def test_kitchen_name_empty_string_becomes_none(self):
        norm = _normalize_item_for_db({"name": "Burger", "kitchen_name": "  "})
        assert norm["kitchen_name"] is None

    def test_kitchen_name_strips_whitespace(self):
        norm = _normalize_item_for_db({"name": "Burger", "kitchen_name": "  BRG  "})
        assert norm["kitchen_name"] == "BRG"

    def test_kitchen_name_numeric_coerced_to_string(self):
        norm = _normalize_item_for_db({"name": "Burger", "kitchen_name": 42})
        assert norm["kitchen_name"] == "42"


# ---------------------------------------------------------------------------
# 4. _insert_items_bulk — kitchen_name + _modifier_groups
# ---------------------------------------------------------------------------

class TestInsertItemsBulkDay112:
    def test_kitchen_name_persisted(self, conn, draft_id):
        _insert_items_bulk(draft_id, [
            {"name": "Taco", "price_cents": 300, "kitchen_name": "TCO"},
        ])
        row = conn.execute("SELECT kitchen_name FROM draft_items WHERE name='Taco'").fetchone()
        assert row["kitchen_name"] == "TCO"

    def test_kitchen_name_null_when_absent(self, conn, draft_id):
        _insert_items_bulk(draft_id, [{"name": "Chip", "price_cents": 100}])
        row = conn.execute("SELECT kitchen_name FROM draft_items WHERE name='Chip'").fetchone()
        assert row["kitchen_name"] is None

    def test_modifier_groups_created(self, conn, draft_id):
        _insert_items_bulk(draft_id, [{
            "name": "Burrito",
            "price_cents": 999,
            "_modifier_groups": [
                {"name": "Protein", "required": True, "min_select": 1,
                 "max_select": 1, "position": 0, "_modifiers": [
                     {"label": "Chicken", "price_cents": 0, "kind": "other", "position": 0},
                 ]},
            ],
        }])
        item_id = conn.execute(
            "SELECT id FROM draft_items WHERE name='Burrito'"
        ).fetchone()["id"]
        groups = conn.execute(
            "SELECT * FROM draft_modifier_groups WHERE item_id=?", (item_id,)
        ).fetchall()
        assert len(groups) == 1
        assert groups[0]["name"] == "Protein"
        assert groups[0]["required"] == 1

    def test_modifier_group_modifiers_linked(self, conn, draft_id):
        _insert_items_bulk(draft_id, [{
            "name": "Bowl",
            "price_cents": 1200,
            "_modifier_groups": [
                {"name": "Sauce", "required": False, "min_select": 0,
                 "max_select": 2, "position": 0, "_modifiers": [
                     {"label": "Salsa", "price_cents": 0, "kind": "other", "position": 0},
                     {"label": "Guac", "price_cents": 150, "kind": "other", "position": 1},
                 ]},
            ],
        }])
        item_id = conn.execute(
            "SELECT id FROM draft_items WHERE name='Bowl'"
        ).fetchone()["id"]
        group_id = conn.execute(
            "SELECT id FROM draft_modifier_groups WHERE item_id=?", (item_id,)
        ).fetchone()["id"]
        variants = conn.execute(
            "SELECT * FROM draft_item_variants WHERE modifier_group_id=?", (group_id,)
        ).fetchall()
        assert len(variants) == 2
        labels = {v["label"] for v in variants}
        assert "Salsa" in labels and "Guac" in labels

    def test_ungrouped_variants_still_work(self, conn, draft_id):
        _insert_items_bulk(draft_id, [{
            "name": "Coffee",
            "price_cents": 0,
            "_variants": [
                {"label": "Small", "price_cents": 250, "kind": "size", "position": 0},
            ],
        }])
        item_id = conn.execute(
            "SELECT id FROM draft_items WHERE name='Coffee'"
        ).fetchone()["id"]
        variants = conn.execute(
            "SELECT * FROM draft_item_variants WHERE item_id=? AND modifier_group_id IS NULL",
            (item_id,)
        ).fetchall()
        assert len(variants) == 1

    def test_multiple_groups_per_item(self, conn, draft_id):
        _insert_items_bulk(draft_id, [{
            "name": "Sub",
            "price_cents": 850,
            "_modifier_groups": [
                {"name": "Bread", "position": 0, "_modifiers": [
                    {"label": "White", "price_cents": 0, "kind": "other", "position": 0},
                ]},
                {"name": "Extras", "position": 1, "_modifiers": [
                    {"label": "Cheese", "price_cents": 75, "kind": "other", "position": 0},
                ]},
            ],
        }])
        item_id = conn.execute(
            "SELECT id FROM draft_items WHERE name='Sub'"
        ).fetchone()["id"]
        groups = conn.execute(
            "SELECT * FROM draft_modifier_groups WHERE item_id=? ORDER BY position",
            (item_id,)
        ).fetchall()
        assert len(groups) == 2
        assert groups[0]["name"] == "Bread"
        assert groups[1]["name"] == "Extras"


# ---------------------------------------------------------------------------
# 5. upsert_draft_items — kitchen_name INSERT/UPDATE + _modifier_groups replace
# ---------------------------------------------------------------------------

class TestUpsertDraftItemsDay112:
    def test_kitchen_name_inserted(self, conn, draft_id):
        result = upsert_draft_items(draft_id, [
            {"name": "Tostada", "price_cents": 400, "kitchen_name": "TST"},
        ])
        assert len(result["inserted_ids"]) == 1
        iid = result["inserted_ids"][0]
        row = conn.execute("SELECT kitchen_name FROM draft_items WHERE id=?", (iid,)).fetchone()
        assert row["kitchen_name"] == "TST"

    def test_kitchen_name_updated(self, conn, draft_id):
        iid = conn.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, kitchen_name, created_at, updated_at) "
            "VALUES (?, 'Tamale', 500, 'TML', ?, ?)", (draft_id, _NOW, _NOW)
        ).lastrowid
        conn.commit()
        upsert_draft_items(draft_id, [
            {"id": iid, "name": "Tamale", "price_cents": 500, "kitchen_name": "TAM-NEW"},
        ])
        row = conn.execute("SELECT kitchen_name FROM draft_items WHERE id=?", (iid,)).fetchone()
        assert row["kitchen_name"] == "TAM-NEW"

    def test_kitchen_name_can_be_cleared(self, conn, draft_id):
        iid = conn.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, kitchen_name, created_at, updated_at) "
            "VALUES (?, 'Enchilada', 700, 'ENC', ?, ?)", (draft_id, _NOW, _NOW)
        ).lastrowid
        conn.commit()
        upsert_draft_items(draft_id, [
            {"id": iid, "name": "Enchilada", "price_cents": 700, "kitchen_name": ""},
        ])
        row = conn.execute("SELECT kitchen_name FROM draft_items WHERE id=?", (iid,)).fetchone()
        assert row["kitchen_name"] is None

    def test_modifier_groups_inserted_on_new_item(self, conn, draft_id):
        result = upsert_draft_items(draft_id, [{
            "name": "Nachos",
            "price_cents": 1100,
            "_modifier_groups": [
                {"name": "Heat Level", "required": False, "min_select": 0,
                 "max_select": 1, "position": 0, "_modifiers": [
                     {"label": "Mild", "price_cents": 0, "kind": "other", "position": 0},
                     {"label": "Hot", "price_cents": 0, "kind": "other", "position": 1},
                 ]},
            ],
        }])
        iid = result["inserted_ids"][0]
        groups = conn.execute(
            "SELECT * FROM draft_modifier_groups WHERE item_id=?", (iid,)
        ).fetchall()
        assert len(groups) == 1
        variants = conn.execute(
            "SELECT * FROM draft_item_variants WHERE modifier_group_id=?",
            (groups[0]["id"],)
        ).fetchall()
        assert len(variants) == 2

    def test_modifier_groups_replaced_on_update(self, conn, draft_id):
        iid = conn.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
            "VALUES (?, 'Quesadilla', 900, ?, ?)", (draft_id, _NOW, _NOW)
        ).lastrowid
        gid = conn.execute(
            "INSERT INTO draft_modifier_groups (item_id, name, required, min_select, "
            "max_select, position, created_at, updated_at) VALUES (?, 'Old Group', 0, 0, 0, 0, ?, ?)",
            (iid, _NOW, _NOW)
        ).lastrowid
        conn.commit()
        # Update with new groups
        upsert_draft_items(draft_id, [{
            "id": iid,
            "name": "Quesadilla",
            "price_cents": 900,
            "_modifier_groups": [
                {"name": "New Group", "position": 0, "_modifiers": []},
            ],
        }])
        groups = conn.execute(
            "SELECT * FROM draft_modifier_groups WHERE item_id=?", (iid,)
        ).fetchall()
        assert len(groups) == 1
        assert groups[0]["name"] == "New Group"

    def test_no_modifier_groups_key_preserves_existing_groups(self, conn, draft_id):
        """If _modifier_groups is absent, existing groups are not touched."""
        iid = conn.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
            "VALUES (?, 'Fajita', 1300, ?, ?)", (draft_id, _NOW, _NOW)
        ).lastrowid
        conn.execute(
            "INSERT INTO draft_modifier_groups (item_id, name, required, min_select, "
            "max_select, position, created_at, updated_at) VALUES (?, 'Keep Me', 0, 0, 0, 0, ?, ?)",
            (iid, _NOW, _NOW)
        )
        conn.commit()
        # Update without _modifier_groups — groups should be untouched
        upsert_draft_items(draft_id, [{
            "id": iid,
            "name": "Fajita Updated",
            "price_cents": 1300,
        }])
        groups = conn.execute(
            "SELECT * FROM draft_modifier_groups WHERE item_id=?", (iid,)
        ).fetchall()
        assert len(groups) == 1
        assert groups[0]["name"] == "Keep Me"

    def test_full_round_trip_via_get_draft_items(self, conn, draft_id):
        """Insert item with modifier groups, then get_draft_items returns full hierarchy."""
        upsert_draft_items(draft_id, [{
            "name": "Torta",
            "price_cents": 950,
            "kitchen_name": "TRT",
            "_modifier_groups": [
                {"name": "Bread Choice", "required": True, "min_select": 1,
                 "max_select": 1, "position": 0, "_modifiers": [
                     {"label": "Bolillo", "price_cents": 0, "kind": "other", "position": 0},
                     {"label": "Telera", "price_cents": 0, "kind": "other", "position": 1},
                 ]},
            ],
        }])
        items = get_draft_items(draft_id, include_modifier_groups=True)
        assert len(items) == 1
        item = items[0]
        assert item["name"] == "Torta"
        assert item["kitchen_name"] == "TRT"
        assert len(item["modifier_groups"]) == 1
        assert item["modifier_groups"][0]["name"] == "Bread Choice"
        assert len(item["modifier_groups"][0]["modifiers"]) == 2
