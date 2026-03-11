"""
Day 110 — Sprint 11.3 Capstone / Sprint 12.1 Schema Kickoff
============================================================
Tests for the modifier group schema foundation:
  - draft_modifier_groups table
  - modifier_group_id column on draft_item_variants
  - CRUD: insert/get/update/delete modifier groups
  - migrate_variants_to_modifier_groups() — kind → group mapping
  - get_draft_items() includes modifier_group_id on variants

40 tests across 8 classes.
"""

import sqlite3
import pytest
import storage.drafts as drafts_mod
from storage.drafts import (
    insert_modifier_group,
    get_modifier_group,
    get_modifier_groups,
    update_modifier_group,
    delete_modifier_group,
    migrate_variants_to_modifier_groups,
    get_draft_items,
    _KIND_TO_GROUP_DEFAULTS,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers
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
"""


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    return conn


def _seed(conn):
    """Return (restaurant_id, draft_id, item_id) for a basic setup."""
    now = "2026-03-11T12:00:00"
    rid = conn.execute(
        "INSERT INTO restaurants (name) VALUES ('Test Rest')"
    ).lastrowid
    did = conn.execute(
        "INSERT INTO drafts (title, restaurant_id, status, created_at, updated_at) "
        "VALUES ('Draft', ?, 'editing', ?, ?)",
        (rid, now, now),
    ).lastrowid
    iid = conn.execute(
        "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
        "VALUES (?, 'Burger', 999, ?, ?)",
        (did, now, now),
    ).lastrowid
    conn.commit()
    return rid, did, iid


def _insert_variant(conn, item_id, label, kind="size", position=0, group_id=None):
    now = "2026-03-11T12:00:00"
    vid = conn.execute(
        "INSERT INTO draft_item_variants "
        "(item_id, label, price_cents, kind, position, modifier_group_id, created_at, updated_at) "
        "VALUES (?, ?, 0, ?, ?, ?, ?, ?)",
        (item_id, label, kind, position, group_id, now, now),
    ).lastrowid
    conn.commit()
    return vid


@pytest.fixture()
def db(monkeypatch):
    """In-memory SQLite wired into storage.drafts."""
    conn = _make_conn()
    monkeypatch.setattr(drafts_mod, "db_connect", lambda: conn)
    yield conn
    conn.close()


@pytest.fixture()
def seeded(db):
    """db + seeded restaurant / draft / item."""
    rid, did, iid = _seed(db)
    return db, rid, did, iid


# ---------------------------------------------------------------------------
# 1. Schema: table and column existence
# ---------------------------------------------------------------------------

class TestModifierGroupSchema:
    def test_modifier_groups_table_exists(self, db):
        tables = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "draft_modifier_groups" in tables

    def test_variants_has_modifier_group_id_column(self, db):
        cols = {
            r[1].lower()
            for r in db.execute("PRAGMA table_info(draft_item_variants)").fetchall()
        }
        assert "modifier_group_id" in cols

    def test_modifier_groups_columns(self, db):
        cols = {
            r[1].lower()
            for r in db.execute(
                "PRAGMA table_info(draft_modifier_groups)"
            ).fetchall()
        }
        expected = {
            "id", "item_id", "name", "required",
            "min_select", "max_select", "position",
            "created_at", "updated_at",
        }
        assert expected.issubset(cols)

    def test_kind_defaults_covers_all_five_kinds(self):
        assert set(_KIND_TO_GROUP_DEFAULTS.keys()) == {
            "size", "combo", "flavor", "style", "other"
        }


# ---------------------------------------------------------------------------
# 2. insert_modifier_group
# ---------------------------------------------------------------------------

class TestInsertModifierGroup:
    def test_returns_integer_id(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Size")
        assert isinstance(gid, int) and gid > 0

    def test_required_true_persisted(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Size", required=True)
        row = get_modifier_group(gid)
        assert row["required"] == 1

    def test_required_false_default(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Extras")
        row = get_modifier_group(gid)
        assert row["required"] == 0

    def test_min_max_select_persisted(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Sauce", min_select=1, max_select=3)
        row = get_modifier_group(gid)
        assert row["min_select"] == 1
        assert row["max_select"] == 3

    def test_position_persisted(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Add-ons", position=2)
        row = get_modifier_group(gid)
        assert row["position"] == 2

    def test_name_is_trimmed(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "  Size  ")
        row = get_modifier_group(gid)
        assert row["name"] == "Size"

    def test_timestamps_set(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Size")
        row = get_modifier_group(gid)
        assert row["created_at"]
        assert row["updated_at"]


# ---------------------------------------------------------------------------
# 3. get_modifier_group
# ---------------------------------------------------------------------------

class TestGetModifierGroup:
    def test_get_by_id_returns_dict(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Flavor")
        result = get_modifier_group(gid)
        assert isinstance(result, dict)
        assert result["id"] == gid
        assert result["name"] == "Flavor"
        assert result["item_id"] == iid

    def test_nonexistent_returns_none(self, seeded):
        assert get_modifier_group(99999) is None

    def test_all_fields_present(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "X", required=True, min_select=1, max_select=2)
        row = get_modifier_group(gid)
        for key in ("id", "item_id", "name", "required", "min_select", "max_select",
                    "position", "created_at", "updated_at"):
            assert key in row


# ---------------------------------------------------------------------------
# 4. get_modifier_groups
# ---------------------------------------------------------------------------

class TestGetModifierGroups:
    def test_empty_list_for_no_groups(self, seeded):
        _, _, _, iid = seeded
        assert get_modifier_groups(iid) == []

    def test_multiple_groups_returned(self, seeded):
        _, _, _, iid = seeded
        insert_modifier_group(iid, "Size", position=0)
        insert_modifier_group(iid, "Sauce", position=1)
        groups = get_modifier_groups(iid)
        assert len(groups) == 2

    def test_ordered_by_position(self, seeded):
        _, _, _, iid = seeded
        insert_modifier_group(iid, "Sauce", position=1)
        insert_modifier_group(iid, "Size", position=0)
        groups = get_modifier_groups(iid)
        assert groups[0]["name"] == "Size"
        assert groups[1]["name"] == "Sauce"

    def test_separate_items_are_isolated(self, seeded):
        db, _, did, iid = seeded
        now = "2026-03-11T12:00:00"
        iid2 = db.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
            "VALUES (?, 'Fries', 299, ?, ?)",
            (did, now, now),
        ).lastrowid
        db.commit()
        insert_modifier_group(iid, "Size")
        insert_modifier_group(iid2, "Sauce")
        assert len(get_modifier_groups(iid)) == 1
        assert len(get_modifier_groups(iid2)) == 1


# ---------------------------------------------------------------------------
# 5. update_modifier_group
# ---------------------------------------------------------------------------

class TestUpdateModifierGroup:
    def test_update_name(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Old")
        assert update_modifier_group(gid, name="New") is True
        assert get_modifier_group(gid)["name"] == "New"

    def test_update_required(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Size", required=False)
        update_modifier_group(gid, required=1)
        assert get_modifier_group(gid)["required"] == 1

    def test_update_min_max_select(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Pick")
        update_modifier_group(gid, min_select=1, max_select=3)
        row = get_modifier_group(gid)
        assert row["min_select"] == 1
        assert row["max_select"] == 3

    def test_update_position(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Size")
        update_modifier_group(gid, position=5)
        assert get_modifier_group(gid)["position"] == 5

    def test_returns_true_on_success(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "X")
        assert update_modifier_group(gid, name="Y") is True

    def test_returns_false_nonexistent(self, seeded):
        assert update_modifier_group(99999, name="X") is False

    def test_unknown_fields_ignored_returns_false(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "X")
        assert update_modifier_group(gid, bogus="oops") is False


# ---------------------------------------------------------------------------
# 6. delete_modifier_group
# ---------------------------------------------------------------------------

class TestDeleteModifierGroup:
    def test_delete_existing_returns_true(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Size")
        assert delete_modifier_group(gid) is True

    def test_delete_nonexistent_returns_false(self, seeded):
        assert delete_modifier_group(99999) is False

    def test_group_gone_after_delete(self, seeded):
        _, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Size")
        delete_modifier_group(gid)
        assert get_modifier_group(gid) is None

    def test_variants_lose_group_id_on_delete(self, seeded):
        db, _, _, iid = seeded
        gid = insert_modifier_group(iid, "Size")
        vid = _insert_variant(db, iid, "Small", kind="size", group_id=gid)
        delete_modifier_group(gid)
        row = db.execute(
            "SELECT modifier_group_id FROM draft_item_variants WHERE id=?", (vid,)
        ).fetchone()
        assert row["modifier_group_id"] is None


# ---------------------------------------------------------------------------
# 7. migrate_variants_to_modifier_groups
# ---------------------------------------------------------------------------

class TestMigrateVariantsToModifierGroups:
    def test_no_variants_returns_zero(self, seeded):
        _, _, _, iid = seeded
        assert migrate_variants_to_modifier_groups(iid) == 0

    def test_size_variants_create_size_group(self, seeded):
        db, _, _, iid = seeded
        _insert_variant(db, iid, "Small", kind="size")
        _insert_variant(db, iid, "Large", kind="size")
        n = migrate_variants_to_modifier_groups(iid)
        assert n == 1
        groups = get_modifier_groups(iid)
        g = groups[0]
        assert g["name"] == "Size"
        assert g["required"] == 1
        assert g["max_select"] == 1

    def test_combo_variants_create_addons_group(self, seeded):
        db, _, _, iid = seeded
        _insert_variant(db, iid, "Extra Cheese", kind="combo")
        migrate_variants_to_modifier_groups(iid)
        groups = get_modifier_groups(iid)
        assert groups[0]["name"] == "Add-ons"
        assert groups[0]["required"] == 0

    def test_flavor_variants_create_flavor_group(self, seeded):
        db, _, _, iid = seeded
        _insert_variant(db, iid, "Chocolate", kind="flavor")
        migrate_variants_to_modifier_groups(iid)
        groups = get_modifier_groups(iid)
        assert groups[0]["name"] == "Flavor"
        assert groups[0]["max_select"] == 1

    def test_style_variants_create_style_group(self, seeded):
        db, _, _, iid = seeded
        _insert_variant(db, iid, "Grilled", kind="style")
        migrate_variants_to_modifier_groups(iid)
        groups = get_modifier_groups(iid)
        assert groups[0]["name"] == "Style"

    def test_other_variants_create_options_group(self, seeded):
        db, _, _, iid = seeded
        _insert_variant(db, iid, "No Onions", kind="other")
        migrate_variants_to_modifier_groups(iid)
        groups = get_modifier_groups(iid)
        assert groups[0]["name"] == "Options"

    def test_mixed_kinds_create_multiple_groups(self, seeded):
        db, _, _, iid = seeded
        _insert_variant(db, iid, "Small", kind="size", position=0)
        _insert_variant(db, iid, "Chocolate", kind="flavor", position=1)
        _insert_variant(db, iid, "No Onions", kind="other", position=2)
        n = migrate_variants_to_modifier_groups(iid)
        assert n == 3
        groups = get_modifier_groups(iid)
        names = [g["name"] for g in groups]
        assert "Size" in names
        assert "Flavor" in names
        assert "Options" in names

    def test_idempotent_second_call_returns_zero(self, seeded):
        db, _, _, iid = seeded
        _insert_variant(db, iid, "Small", kind="size")
        migrate_variants_to_modifier_groups(iid)
        assert migrate_variants_to_modifier_groups(iid) == 0

    def test_variants_assigned_modifier_group_id(self, seeded):
        db, _, _, iid = seeded
        vid = _insert_variant(db, iid, "Small", kind="size")
        migrate_variants_to_modifier_groups(iid)
        row = db.execute(
            "SELECT modifier_group_id FROM draft_item_variants WHERE id=?", (vid,)
        ).fetchone()
        assert row["modifier_group_id"] is not None


# ---------------------------------------------------------------------------
# 8. get_draft_items includes modifier_group_id in variant output
# ---------------------------------------------------------------------------

class TestGetDraftItemsModifierGroupId:
    def test_variant_has_modifier_group_id_key(self, seeded):
        db, _, did, iid = seeded
        _insert_variant(db, iid, "Small", kind="size")
        items = get_draft_items(did)
        assert len(items) == 1
        v = items[0]["variants"][0]
        assert "modifier_group_id" in v

    def test_modifier_group_id_none_by_default(self, seeded):
        db, _, did, iid = seeded
        _insert_variant(db, iid, "Small", kind="size")
        items = get_draft_items(did)
        assert items[0]["variants"][0]["modifier_group_id"] is None

    def test_modifier_group_id_populated_after_migration(self, seeded):
        db, _, did, iid = seeded
        _insert_variant(db, iid, "Small", kind="size")
        migrate_variants_to_modifier_groups(iid)
        items = get_draft_items(did)
        v = items[0]["variants"][0]
        assert v["modifier_group_id"] is not None
        assert isinstance(v["modifier_group_id"], int)

    def test_item_with_no_variants_unaffected(self, seeded):
        _, _, did, _ = seeded
        items = get_draft_items(did)
        assert items[0]["variants"] == []
