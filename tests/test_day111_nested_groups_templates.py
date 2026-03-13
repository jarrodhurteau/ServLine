"""
Day 111 — Sprint 12.1: Nested Modifier Groups + Template Library
=================================================================
Tests for:
  1. get_draft_items(include_modifier_groups=True) — full POS hierarchy
  2. draft_modifier_group_templates CRUD + MODIFIER_TEMPLATE_PRESETS
  3. apply_modifier_template(item_id, template_id)

35 tests across 3 classes.
"""

import sqlite3
import pytest
import storage.drafts as drafts_mod
from storage.drafts import (
    get_draft_items,
    insert_modifier_group,
    get_modifier_groups,
    insert_modifier_template,
    get_modifier_template,
    list_modifier_templates,
    delete_modifier_template,
    seed_modifier_template_presets,
    apply_modifier_template,
    MODIFIER_TEMPLATE_PRESETS,
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

_NOW = "2026-03-11T12:00:00"


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    return conn


def _seed(conn):
    """Return (restaurant_id, draft_id, item_id)."""
    rid = conn.execute(
        "INSERT INTO restaurants (name) VALUES ('Test')"
    ).lastrowid
    did = conn.execute(
        "INSERT INTO drafts (title, restaurant_id, status, created_at, updated_at) "
        "VALUES ('D', ?, 'editing', ?, ?)",
        (rid, _NOW, _NOW),
    ).lastrowid
    iid = conn.execute(
        "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
        "VALUES (?, 'Burger', 999, ?, ?)",
        (did, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return rid, did, iid


def _insert_variant(conn, item_id, label, kind="size", position=0, group_id=None):
    vid = conn.execute(
        "INSERT INTO draft_item_variants "
        "(item_id, label, price_cents, kind, position, modifier_group_id, created_at, updated_at) "
        "VALUES (?, ?, 0, ?, ?, ?, ?, ?)",
        (item_id, label, kind, position, group_id, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return vid


@pytest.fixture()
def db(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr(drafts_mod, "db_connect", lambda: conn)
    yield conn
    conn.close()


@pytest.fixture()
def seeded(db):
    rid, did, iid = _seed(db)
    return db, rid, did, iid


# ---------------------------------------------------------------------------
# 1. Nested get_draft_items(include_modifier_groups=True)
# ---------------------------------------------------------------------------

class TestNestedGetDraftItems:
    def test_default_call_returns_variants_key(self, seeded):
        db, _, did, iid = seeded
        _insert_variant(db, iid, "Small")
        items = get_draft_items(did)
        assert "variants" in items[0]
        assert "modifier_groups" not in items[0]

    def test_nested_returns_modifier_groups_key(self, seeded):
        _, _, did, _ = seeded
        items = get_draft_items(did, include_modifier_groups=True)
        assert "modifier_groups" in items[0]
        # Day 116: 'variants' is now aliased to ungrouped_variants for template compat
        assert "variants" in items[0]

    def test_nested_returns_ungrouped_variants_key(self, seeded):
        _, _, did, _ = seeded
        items = get_draft_items(did, include_modifier_groups=True)
        assert "ungrouped_variants" in items[0]

    def test_item_no_groups_modifier_groups_empty(self, seeded):
        _, _, did, _ = seeded
        items = get_draft_items(did, include_modifier_groups=True)
        assert items[0]["modifier_groups"] == []

    def test_item_no_variants_ungrouped_empty(self, seeded):
        _, _, did, _ = seeded
        items = get_draft_items(did, include_modifier_groups=True)
        assert items[0]["ungrouped_variants"] == []

    def test_grouped_variant_appears_in_group_modifiers(self, seeded):
        db, _, did, iid = seeded
        gid = insert_modifier_group(iid, "Size")
        _insert_variant(db, iid, "Small", group_id=gid)
        items = get_draft_items(did, include_modifier_groups=True)
        assert len(items[0]["modifier_groups"]) == 1
        assert len(items[0]["modifier_groups"][0]["modifiers"]) == 1
        assert items[0]["modifier_groups"][0]["modifiers"][0]["label"] == "Small"

    def test_group_fields_complete(self, seeded):
        _, _, did, iid = seeded
        insert_modifier_group(iid, "Size", required=True, min_select=1, max_select=1)
        items = get_draft_items(did, include_modifier_groups=True)
        g = items[0]["modifier_groups"][0]
        for key in ("id", "name", "required", "min_select", "max_select", "position", "modifiers"):
            assert key in g

    def test_multiple_groups_ordered_by_position(self, seeded):
        _, _, did, iid = seeded
        insert_modifier_group(iid, "Sauce", position=1)
        insert_modifier_group(iid, "Size", position=0)
        items = get_draft_items(did, include_modifier_groups=True)
        names = [g["name"] for g in items[0]["modifier_groups"]]
        assert names == ["Size", "Sauce"]

    def test_ungrouped_variant_not_in_groups(self, seeded):
        db, _, did, iid = seeded
        # ungrouped variant (modifier_group_id=None)
        _insert_variant(db, iid, "No Onions", kind="other", group_id=None)
        items = get_draft_items(did, include_modifier_groups=True)
        assert items[0]["modifier_groups"] == []
        assert len(items[0]["ungrouped_variants"]) == 1
        assert items[0]["ungrouped_variants"][0]["label"] == "No Onions"

    def test_grouped_and_ungrouped_coexist(self, seeded):
        db, _, did, iid = seeded
        gid = insert_modifier_group(iid, "Size")
        _insert_variant(db, iid, "Small", group_id=gid)
        _insert_variant(db, iid, "No Onions", kind="other", group_id=None)
        items = get_draft_items(did, include_modifier_groups=True)
        assert len(items[0]["modifier_groups"][0]["modifiers"]) == 1
        assert len(items[0]["ungrouped_variants"]) == 1

    def test_modifiers_within_group_ordered_by_position(self, seeded):
        db, _, did, iid = seeded
        gid = insert_modifier_group(iid, "Size")
        _insert_variant(db, iid, "Large", position=1, group_id=gid)
        _insert_variant(db, iid, "Small", position=0, group_id=gid)
        items = get_draft_items(did, include_modifier_groups=True)
        labels = [m["label"] for m in items[0]["modifier_groups"][0]["modifiers"]]
        assert labels == ["Small", "Large"]


# ---------------------------------------------------------------------------
# 2. Modifier Group Template CRUD + MODIFIER_TEMPLATE_PRESETS
# ---------------------------------------------------------------------------

class TestModifierGroupTemplates:
    def test_preset_keys_present(self):
        assert "size_sml" in MODIFIER_TEMPLATE_PRESETS
        assert "temperature" in MODIFIER_TEMPLATE_PRESETS
        assert "sauce_choice" in MODIFIER_TEMPLATE_PRESETS
        assert "protein_add" in MODIFIER_TEMPLATE_PRESETS

    def test_size_sml_preset_has_three_modifiers(self):
        assert len(MODIFIER_TEMPLATE_PRESETS["size_sml"]["modifiers"]) == 3

    def test_insert_returns_int(self, seeded):
        db, rid, _, _ = seeded
        tid = insert_modifier_template(rid, "Size (S/M/L)")
        assert isinstance(tid, int) and tid > 0

    def test_name_trimmed(self, seeded):
        db, rid, _, _ = seeded
        tid = insert_modifier_template(rid, "  Size  ")
        assert get_modifier_template(tid)["name"] == "Size"

    def test_required_persisted(self, seeded):
        db, rid, _, _ = seeded
        tid = insert_modifier_template(rid, "X", required=True)
        assert get_modifier_template(tid)["required"] == 1

    def test_optional_default(self, seeded):
        db, rid, _, _ = seeded
        tid = insert_modifier_template(rid, "X")
        assert get_modifier_template(tid)["required"] == 0

    def test_min_max_select_persisted(self, seeded):
        db, rid, _, _ = seeded
        tid = insert_modifier_template(rid, "X", min_select=1, max_select=3)
        t = get_modifier_template(tid)
        assert t["min_select"] == 1
        assert t["max_select"] == 3

    def test_modifiers_list_persisted(self, seeded):
        db, rid, _, _ = seeded
        mods = [{"label": "Small", "price_cents": 0, "kind": "size"}]
        tid = insert_modifier_template(rid, "Size", modifiers=mods)
        assert get_modifier_template(tid)["modifiers"] == mods

    def test_get_nonexistent_returns_none(self, seeded):
        assert get_modifier_template(99999) is None

    def test_list_returns_list(self, seeded):
        db, rid, _, _ = seeded
        insert_modifier_template(rid, "A")
        result = list_modifier_templates(rid)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_list_ordered_by_position(self, seeded):
        db, rid, _, _ = seeded
        insert_modifier_template(rid, "B", position=1)
        insert_modifier_template(rid, "A", position=0)
        result = list_modifier_templates(rid)
        names = [t["name"] for t in result]
        assert names.index("A") < names.index("B")

    def test_list_scoped_to_restaurant(self, seeded):
        db, rid, _, _ = seeded
        rid2 = db.execute("INSERT INTO restaurants (name) VALUES ('Other')").lastrowid
        db.commit()
        insert_modifier_template(rid, "Mine")
        insert_modifier_template(rid2, "Theirs")
        mine = list_modifier_templates(rid)
        names = [t["name"] for t in mine]
        assert "Mine" in names
        assert "Theirs" not in names

    def test_list_includes_global_templates(self, seeded):
        db, rid, _, _ = seeded
        insert_modifier_template(None, "Global")
        result = list_modifier_templates(rid)
        names = [t["name"] for t in result]
        assert "Global" in names

    def test_delete_existing_returns_true(self, seeded):
        db, rid, _, _ = seeded
        tid = insert_modifier_template(rid, "X")
        assert delete_modifier_template(tid) is True

    def test_delete_nonexistent_returns_false(self, seeded):
        assert delete_modifier_template(99999) is False

    def test_template_gone_after_delete(self, seeded):
        db, rid, _, _ = seeded
        tid = insert_modifier_template(rid, "X")
        delete_modifier_template(tid)
        assert get_modifier_template(tid) is None

    def test_seed_presets_inserts_all(self, seeded):
        db, rid, _, _ = seeded
        n = seed_modifier_template_presets(rid)
        assert n == len(MODIFIER_TEMPLATE_PRESETS)

    def test_seed_presets_idempotent(self, seeded):
        db, rid, _, _ = seeded
        seed_modifier_template_presets(rid)
        assert seed_modifier_template_presets(rid) == 0


# ---------------------------------------------------------------------------
# 3. apply_modifier_template
# ---------------------------------------------------------------------------

class TestApplyModifierTemplate:
    def test_returns_dict_with_group_and_modifier_ids(self, seeded):
        db, rid, did, iid = seeded
        tid = insert_modifier_template(rid, "Size", modifiers=[
            {"label": "Small", "price_cents": 0, "kind": "size"},
            {"label": "Large", "price_cents": 200, "kind": "size"},
        ])
        result = apply_modifier_template(iid, tid)
        assert "group_id" in result
        assert "modifier_ids" in result
        assert isinstance(result["group_id"], int)
        assert isinstance(result["modifier_ids"], list)

    def test_creates_modifier_group_on_item(self, seeded):
        db, rid, did, iid = seeded
        tid = insert_modifier_template(rid, "Size")
        apply_modifier_template(iid, tid)
        groups = get_modifier_groups(iid)
        assert len(groups) == 1
        assert groups[0]["name"] == "Size"

    def test_group_name_from_template(self, seeded):
        db, rid, did, iid = seeded
        tid = insert_modifier_template(rid, "My Group")
        apply_modifier_template(iid, tid)
        assert get_modifier_groups(iid)[0]["name"] == "My Group"

    def test_group_required_from_template(self, seeded):
        db, rid, did, iid = seeded
        tid = insert_modifier_template(rid, "R", required=True)
        apply_modifier_template(iid, tid)
        assert get_modifier_groups(iid)[0]["required"] == 1

    def test_group_min_max_from_template(self, seeded):
        db, rid, did, iid = seeded
        tid = insert_modifier_template(rid, "X", min_select=1, max_select=3)
        apply_modifier_template(iid, tid)
        g = get_modifier_groups(iid)[0]
        assert g["min_select"] == 1
        assert g["max_select"] == 3

    def test_modifiers_created_as_variants(self, seeded):
        db, rid, did, iid = seeded
        mods = [
            {"label": "Small", "price_cents": 0, "kind": "size"},
            {"label": "Medium", "price_cents": 100, "kind": "size"},
            {"label": "Large", "price_cents": 200, "kind": "size"},
        ]
        tid = insert_modifier_template(rid, "Size", modifiers=mods)
        result = apply_modifier_template(iid, tid)
        assert len(result["modifier_ids"]) == 3

    def test_modifier_labels_from_template(self, seeded):
        db, rid, did, iid = seeded
        mods = [{"label": "BBQ", "price_cents": 0, "kind": "flavor"}]
        tid = insert_modifier_template(rid, "Sauce", modifiers=mods)
        result = apply_modifier_template(iid, tid)
        row = db.execute(
            "SELECT label FROM draft_item_variants WHERE id=?",
            (result["modifier_ids"][0],),
        ).fetchone()
        assert row["label"] == "BBQ"

    def test_nonexistent_template_raises_value_error(self, seeded):
        db, rid, did, iid = seeded
        with pytest.raises(ValueError):
            apply_modifier_template(iid, 99999)

    def test_nonexistent_item_raises_value_error(self, seeded):
        db, rid, did, iid = seeded
        tid = insert_modifier_template(rid, "X")
        with pytest.raises(ValueError):
            apply_modifier_template(99999, tid)

    def test_applying_twice_creates_two_groups(self, seeded):
        db, rid, did, iid = seeded
        tid = insert_modifier_template(rid, "Size")
        apply_modifier_template(iid, tid)
        apply_modifier_template(iid, tid)
        assert len(get_modifier_groups(iid)) == 2

    def test_empty_modifiers_template_creates_group_no_variants(self, seeded):
        db, rid, did, iid = seeded
        tid = insert_modifier_template(rid, "Empty", modifiers=[])
        result = apply_modifier_template(iid, tid)
        assert result["modifier_ids"] == []
        assert len(get_modifier_groups(iid)) == 1
