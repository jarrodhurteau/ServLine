"""
Day 124 — CSV, JSON & Generic POS JSON Modifier Group Export Upgrades
=====================================================================
Tests for:
  - CSV variants export: modifier_group + modifier rows alongside variant rows
  - CSV wide export: modifier group columns as GroupName:Label
  - JSON export: modifier_groups[] array per item
  - Generic POS JSON builder: modifier_groups → nested POS structure
  - Route handlers fetch with include_modifier_groups=True
  - Backward compat: items without modifier groups export as before

Test plan (~32 tests):
  Class 1: CSV variants export with modifier groups (8 tests)
    1.  Item with no modifiers → item row only
    2.  Item with modifier group → modifier_group + modifier rows
    3.  Modifier rows carry group_name column
    4.  Ungrouped variants → variant rows (backward compat)
    5.  Mixed: modifier_groups + ungrouped_variants both emitted
    6.  Multiple groups → rows in group order
    7.  Route GET → 200 CSV with new columns
    8.  Unauthenticated → 302

  Class 2: CSV wide export with modifier groups (8 tests)
    9.  Item with no modifiers → base columns only
    10. Modifier group → columns as GroupName:Label
    11. Multiple groups → multiple prefixed columns
    12. Ungrouped variants → plain label columns (backward compat)
    13. Mixed: grouped + ungrouped columns coexist
    14. Price values placed in correct columns
    15. Route GET → 200 CSV with grouped headers
    16. Unauthenticated → 302

  Class 3: JSON export with modifier groups (8 tests)
    17. Item with no modifiers → empty modifier_groups + variants
    18. Item with modifier group → modifier_groups[] populated
    19. Group includes required, min_select, max_select
    20. Group modifiers include label, price_cents, kind
    21. Ungrouped variants → variants[] (backward compat)
    22. Mixed: both modifier_groups and variants populated
    23. Route GET → 200 JSON with modifier_groups
    24. Unauthenticated → 302

  Class 4: _build_generic_pos_json() with modifier groups (8 tests)
    25. Item with no modifiers → empty modifier_groups + modifiers
    26. Modifier group → POS nested structure with selection rules
    27. Modifier prices formatted as dollars
    28. Multiple groups → multiple entries in modifier_groups[]
    29. Ungrouped variants → flat modifiers with kind-based groups
    30. Mixed: modifier_groups + flat modifiers both present
    31. Categories sorted alphabetically
    32. Metadata includes item/category counts
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3

import pytest

import storage.drafts as drafts_mod


# ---------------------------------------------------------------------------
# Schema (matches Day 123)
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
    category_order TEXT,
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
CREATE TABLE IF NOT EXISTS draft_export_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    format TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    variant_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    exported_at TEXT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS menu_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash TEXT UNIQUE NOT NULL,
    restaurant_id INTEGER,
    label TEXT,
    active INTEGER DEFAULT 1,
    rate_limit_rpm INTEGER DEFAULT 60,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    gate_score REAL,
    gate_threshold REAL,
    reason TEXT,
    customer_message TEXT,
    rejected_at TEXT NOT NULL
);
"""

_NOW = "2026-03-18T10:00:00"


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(_SCHEMA_SQL)
    return conn


def _create_draft(conn, status="editing") -> tuple[int, int]:
    rid = conn.execute(
        "INSERT INTO restaurants (name, active) VALUES ('R', 1)"
    ).lastrowid
    did = conn.execute(
        "INSERT INTO drafts (title, restaurant_id, status, created_at, updated_at) "
        "VALUES ('Menu', ?, ?, ?, ?)",
        (rid, status, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return rid, did


def _create_item(conn, draft_id, name="Burger", price_cents=999, category="Entrees") -> int:
    iid = conn.execute(
        "INSERT INTO draft_items (draft_id, name, price_cents, category, position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 0, ?, ?)",
        (draft_id, name, price_cents, category, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return iid


def _create_modifier_group(conn, item_id, name="Size", required=0,
                            min_select=0, max_select=0, position=0) -> int:
    gid = conn.execute(
        "INSERT INTO draft_modifier_groups "
        "(item_id, name, required, min_select, max_select, position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, name, required, min_select, max_select, position, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return gid


def _create_variant(conn, item_id, label="Small", price_cents=0, kind="size",
                     modifier_group_id=None, position=0) -> int:
    vid = conn.execute(
        "INSERT INTO draft_item_variants "
        "(item_id, label, price_cents, kind, position, modifier_group_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, label, price_cents, kind, position, modifier_group_id, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return vid


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app_ctx(monkeypatch):
    """Patch db_connect for both storage + portal; yield (app, client, conn)."""
    import portal.app as _app_mod

    conn = _make_conn()

    def _fake_connect():
        return conn

    monkeypatch.setattr(drafts_mod, "db_connect", _fake_connect)
    monkeypatch.setattr(_app_mod, "db_connect", _fake_connect)

    _app_mod.app.config["TESTING"] = True
    _app_mod.app.config["WTF_CSRF_ENABLED"] = False
    _app_mod.app.secret_key = "test"

    with _app_mod.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user"] = "tester"
        yield _app_mod.app, client, conn


# ---------------------------------------------------------------------------
# Helpers for building test data dicts (for unit-testing builder functions)
# ---------------------------------------------------------------------------

def _item(name="Burger", price_cents=999, category="Entrees",
          modifier_groups=None, ungrouped_variants=None, variants=None,
          description="", kitchen_name=""):
    d = {
        "id": 1, "name": name, "description": description,
        "price_cents": price_cents, "category": category,
    }
    if kitchen_name:
        d["kitchen_name"] = kitchen_name
    if modifier_groups is not None:
        d["modifier_groups"] = modifier_groups
    if ungrouped_variants is not None:
        d["ungrouped_variants"] = ungrouped_variants
    if variants is not None:
        d["variants"] = variants
    return d


def _mg(name="Size", required=False, min_select=0, max_select=0, modifiers=None):
    return {
        "id": 1, "name": name,
        "required": 1 if required else 0,
        "min_select": min_select, "max_select": max_select,
        "modifiers": modifiers or [],
    }


def _mod(label="Small", price_cents=0, kind="size"):
    return {"id": 1, "label": label, "price_cents": price_cents, "kind": kind}


def _parse_csv(csv_text: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(csv_text))
    return [row for row in reader]


# ---------------------------------------------------------------------------
# Class 1: CSV variants export with modifier groups (tests 1–8)
# ---------------------------------------------------------------------------

class TestCSVVariantsModifierGroups:
    """CSV variants export upgraded for POS-native modifier groups."""

    def test_no_modifiers_item_row_only(self, app_ctx):
        """1. Item with no modifiers → item row only."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, "Burger", 999, "Entrees")

        resp = client.get(f"/drafts/{did}/export_variants.csv")
        assert resp.status_code == 200
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        # Header + 1 item row
        assert len(rows) == 2
        assert rows[1][0] == "item"
        assert rows[1][2] == "Burger"

    def test_modifier_group_emits_group_and_modifier_rows(self, app_ctx):
        """2. Item with modifier group → modifier_group header + modifier rows."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Burger", 999, "Entrees")
        gid = _create_modifier_group(conn, iid, "Size", required=1)
        _create_variant(conn, iid, "Small", 0, "size", modifier_group_id=gid)
        _create_variant(conn, iid, "Large", 200, "size", modifier_group_id=gid)

        resp = client.get(f"/drafts/{did}/export_variants.csv")
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        types = [r[0] for r in rows[1:]]
        assert "item" in types
        assert "modifier_group" in types
        assert "modifier" in types

    def test_modifier_rows_carry_group_name(self, app_ctx):
        """3. Modifier rows carry group_name column."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Burger", 999, "Entrees")
        gid = _create_modifier_group(conn, iid, "Size", required=1)
        _create_variant(conn, iid, "Small", 0, "size", modifier_group_id=gid)

        resp = client.get(f"/drafts/{did}/export_variants.csv")
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        header = rows[0]
        group_name_idx = header.index("group_name")

        # modifier_group row has group name
        grp_rows = [r for r in rows[1:] if r[0] == "modifier_group"]
        assert grp_rows[0][group_name_idx] == "Size"

        # modifier row also has group name
        mod_rows = [r for r in rows[1:] if r[0] == "modifier"]
        assert mod_rows[0][group_name_idx] == "Size"

    def test_ungrouped_variants_backward_compat(self, app_ctx):
        """4. Ungrouped variants → variant rows (backward compat)."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Fries", 499, "Sides")
        _create_variant(conn, iid, "Small", 0, "size")
        _create_variant(conn, iid, "Large", 200, "size")

        resp = client.get(f"/drafts/{did}/export_variants.csv")
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        types = [r[0] for r in rows[1:]]
        assert types.count("variant") == 2
        assert "modifier_group" not in types
        assert "modifier" not in types

    def test_mixed_groups_and_ungrouped(self, app_ctx):
        """5. Mixed: modifier_groups + ungrouped_variants both emitted."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Burger", 999, "Entrees")
        gid = _create_modifier_group(conn, iid, "Toppings")
        _create_variant(conn, iid, "Cheese", 100, "other", modifier_group_id=gid)
        _create_variant(conn, iid, "Small", 0, "size")  # ungrouped

        resp = client.get(f"/drafts/{did}/export_variants.csv")
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        types = [r[0] for r in rows[1:]]
        assert "modifier_group" in types
        assert "modifier" in types
        assert "variant" in types

    def test_multiple_groups_in_order(self, app_ctx):
        """6. Multiple groups → rows in group order."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Burger", 999, "Entrees")
        gid1 = _create_modifier_group(conn, iid, "Size", position=0)
        gid2 = _create_modifier_group(conn, iid, "Toppings", position=1)
        _create_variant(conn, iid, "Small", 0, "size", modifier_group_id=gid1)
        _create_variant(conn, iid, "Cheese", 100, "other", modifier_group_id=gid2)

        resp = client.get(f"/drafts/{did}/export_variants.csv")
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        header = rows[0]
        gn_idx = header.index("group_name")
        grp_rows = [r for r in rows[1:] if r[0] == "modifier_group"]
        assert grp_rows[0][gn_idx] == "Size"
        assert grp_rows[1][gn_idx] == "Toppings"

    def test_route_returns_200_csv(self, app_ctx):
        """7. Route GET → 200 CSV with new columns."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did)

        resp = client.get(f"/drafts/{did}/export_variants.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["Content-Type"]
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        header = rows[0]
        assert "group_name" in header
        assert "required" in header

    def test_unauthenticated_redirect(self, app_ctx):
        """8. Unauthenticated → 302."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)

        # Clear session
        with client.session_transaction() as sess:
            sess.clear()

        resp = client.get(f"/drafts/{did}/export_variants.csv")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Class 2: CSV wide export with modifier groups (tests 9–16)
# ---------------------------------------------------------------------------

class TestCSVWideModifierGroups:
    """CSV wide export: modifier groups as GroupName:Label columns."""

    def test_no_modifiers_base_columns_only(self, app_ctx):
        """9. Item with no modifiers → base columns only."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, "Burger", 999, "Entrees")

        resp = client.get(f"/drafts/{did}/export_wide.csv")
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        header = rows[0]
        assert header == ["id", "name", "description", "price_cents", "category"]

    def test_modifier_group_columns_prefixed(self, app_ctx):
        """10. Modifier group → columns as GroupName:Label."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Burger", 999, "Entrees")
        gid = _create_modifier_group(conn, iid, "Size")
        _create_variant(conn, iid, "Small", 599, "size", modifier_group_id=gid)
        _create_variant(conn, iid, "Large", 899, "size", modifier_group_id=gid)

        resp = client.get(f"/drafts/{did}/export_wide.csv")
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        header = rows[0]
        assert "price_Size:Small" in header
        assert "price_Size:Large" in header

    def test_multiple_groups_multiple_columns(self, app_ctx):
        """11. Multiple groups → multiple prefixed columns."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Burger", 999, "Entrees")
        gid1 = _create_modifier_group(conn, iid, "Size", position=0)
        gid2 = _create_modifier_group(conn, iid, "Toppings", position=1)
        _create_variant(conn, iid, "Small", 599, "size", modifier_group_id=gid1)
        _create_variant(conn, iid, "Cheese", 100, "other", modifier_group_id=gid2)

        resp = client.get(f"/drafts/{did}/export_wide.csv")
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        header = rows[0]
        assert "price_Size:Small" in header
        assert "price_Toppings:Cheese" in header

    def test_ungrouped_variants_plain_labels(self, app_ctx):
        """12. Ungrouped variants → plain label columns (backward compat)."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Fries", 499, "Sides")
        _create_variant(conn, iid, "Small", 399, "size")
        _create_variant(conn, iid, "Large", 599, "size")

        resp = client.get(f"/drafts/{did}/export_wide.csv")
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        header = rows[0]
        assert "price_Small" in header
        assert "price_Large" in header

    def test_mixed_grouped_and_ungrouped_columns(self, app_ctx):
        """13. Mixed: grouped + ungrouped columns coexist."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Burger", 999, "Entrees")
        gid = _create_modifier_group(conn, iid, "Toppings")
        _create_variant(conn, iid, "Cheese", 100, "other", modifier_group_id=gid)
        _create_variant(conn, iid, "Small", 0, "size")  # ungrouped

        resp = client.get(f"/drafts/{did}/export_wide.csv")
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        header = rows[0]
        assert "price_Toppings:Cheese" in header
        assert "price_Small" in header

    def test_price_values_in_correct_columns(self, app_ctx):
        """14. Price values placed in correct columns."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Burger", 999, "Entrees")
        gid = _create_modifier_group(conn, iid, "Size")
        _create_variant(conn, iid, "Small", 599, "size", modifier_group_id=gid)
        _create_variant(conn, iid, "Large", 899, "size", modifier_group_id=gid)

        resp = client.get(f"/drafts/{did}/export_wide.csv")
        rows = _parse_csv(resp.data.decode("utf-8-sig"))
        header = rows[0]
        small_idx = header.index("price_Size:Small")
        large_idx = header.index("price_Size:Large")
        data_row = rows[1]
        assert data_row[small_idx] == "599"
        assert data_row[large_idx] == "899"

    def test_route_returns_200_csv(self, app_ctx):
        """15. Route GET → 200 CSV with grouped headers."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did)

        resp = client.get(f"/drafts/{did}/export_wide.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["Content-Type"]

    def test_unauthenticated_redirect(self, app_ctx):
        """16. Unauthenticated → 302."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)

        with client.session_transaction() as sess:
            sess.clear()

        resp = client.get(f"/drafts/{did}/export_wide.csv")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Class 3: JSON export with modifier groups (tests 17–24)
# ---------------------------------------------------------------------------

class TestJSONExportModifierGroups:
    """JSON export upgraded for POS-native modifier groups."""

    def test_no_modifiers_empty_arrays(self, app_ctx):
        """17. Item with no modifiers → empty modifier_groups + variants."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, "Burger", 999, "Entrees")

        resp = client.get(f"/drafts/{did}/export.json")
        assert resp.status_code == 200
        payload = json.loads(resp.data)
        item = payload["items"][0]
        assert item["modifier_groups"] == []
        assert item["variants"] == []

    def test_modifier_group_populated(self, app_ctx):
        """18. Item with modifier group → modifier_groups[] populated."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Burger", 999, "Entrees")
        gid = _create_modifier_group(conn, iid, "Size", required=1, min_select=1, max_select=1)
        _create_variant(conn, iid, "Small", 599, "size", modifier_group_id=gid)
        _create_variant(conn, iid, "Large", 899, "size", modifier_group_id=gid)

        resp = client.get(f"/drafts/{did}/export.json")
        payload = json.loads(resp.data)
        item = payload["items"][0]
        assert len(item["modifier_groups"]) == 1
        grp = item["modifier_groups"][0]
        assert grp["name"] == "Size"
        assert len(grp["modifiers"]) == 2

    def test_group_includes_selection_rules(self, app_ctx):
        """19. Group includes required, min_select, max_select."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Burger", 999, "Entrees")
        gid = _create_modifier_group(conn, iid, "Size", required=1, min_select=1, max_select=3)
        _create_variant(conn, iid, "Small", 599, "size", modifier_group_id=gid)

        resp = client.get(f"/drafts/{did}/export.json")
        payload = json.loads(resp.data)
        grp = payload["items"][0]["modifier_groups"][0]
        assert grp["required"] is True
        assert grp["min_select"] == 1
        assert grp["max_select"] == 3

    def test_group_modifiers_fields(self, app_ctx):
        """20. Group modifiers include label, price_cents, kind."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Burger", 999, "Entrees")
        gid = _create_modifier_group(conn, iid, "Toppings")
        _create_variant(conn, iid, "Cheese", 150, "other", modifier_group_id=gid)

        resp = client.get(f"/drafts/{did}/export.json")
        payload = json.loads(resp.data)
        mod = payload["items"][0]["modifier_groups"][0]["modifiers"][0]
        assert mod["label"] == "Cheese"
        assert mod["price_cents"] == 150
        assert mod["kind"] == "other"

    def test_ungrouped_variants_backward_compat(self, app_ctx):
        """21. Ungrouped variants → variants[] (backward compat)."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Fries", 499, "Sides")
        _create_variant(conn, iid, "Small", 399, "size")
        _create_variant(conn, iid, "Large", 599, "size")

        resp = client.get(f"/drafts/{did}/export.json")
        payload = json.loads(resp.data)
        item = payload["items"][0]
        assert item["modifier_groups"] == []
        assert len(item["variants"]) == 2
        assert item["variants"][0]["label"] == "Small"

    def test_mixed_groups_and_ungrouped(self, app_ctx):
        """22. Mixed: both modifier_groups and variants populated."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Burger", 999, "Entrees")
        gid = _create_modifier_group(conn, iid, "Toppings")
        _create_variant(conn, iid, "Cheese", 100, "other", modifier_group_id=gid)
        _create_variant(conn, iid, "Small", 0, "size")  # ungrouped

        resp = client.get(f"/drafts/{did}/export.json")
        payload = json.loads(resp.data)
        item = payload["items"][0]
        assert len(item["modifier_groups"]) == 1
        assert len(item["variants"]) == 1

    def test_route_returns_200_json(self, app_ctx):
        """23. Route GET → 200 JSON with modifier_groups."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did)

        resp = client.get(f"/drafts/{did}/export.json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["Content-Type"]
        payload = json.loads(resp.data)
        assert "items" in payload
        assert "modifier_groups" in payload["items"][0]

    def test_unauthenticated_redirect(self, app_ctx):
        """24. Unauthenticated → 302."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)

        with client.session_transaction() as sess:
            sess.clear()

        resp = client.get(f"/drafts/{did}/export.json")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Class 4: _build_generic_pos_json() with modifier groups (tests 25–32)
# ---------------------------------------------------------------------------

class TestBuildGenericPosJsonModifierGroups:
    """_build_generic_pos_json already supports modifier groups — verify."""

    def _fn(self):
        import portal.app as _app_mod
        return _app_mod._build_generic_pos_json

    def test_no_modifiers_empty_arrays(self):
        """25. Item with no modifiers → empty modifier_groups + modifiers."""
        result = self._fn()([_item()])
        item = result["menu"]["categories"][0]["items"][0]
        assert item["modifier_groups"] == []
        assert item["modifiers"] == []

    def test_modifier_group_nested_structure(self):
        """26. Modifier group → POS nested structure with selection rules."""
        items = [_item(modifier_groups=[
            _mg(name="Size", required=True, min_select=1, max_select=1,
                modifiers=[_mod("Small", 599), _mod("Large", 899)]),
        ])]
        result = self._fn()(items)
        item = result["menu"]["categories"][0]["items"][0]
        assert len(item["modifier_groups"]) == 1
        grp = item["modifier_groups"][0]
        assert grp["name"] == "Size"
        assert grp["required"] is True
        assert grp["min_select"] == 1
        assert grp["max_select"] == 1
        assert len(grp["modifiers"]) == 2

    def test_modifier_prices_formatted_as_dollars(self):
        """27. Modifier prices formatted as dollars."""
        items = [_item(modifier_groups=[
            _mg(name="Size", modifiers=[_mod("Large", 899)]),
        ])]
        result = self._fn()(items)
        mod = result["menu"]["categories"][0]["items"][0]["modifier_groups"][0]["modifiers"][0]
        assert mod["price"] == "8.99"

    def test_multiple_groups(self):
        """28. Multiple groups → multiple entries in modifier_groups[]."""
        items = [_item(modifier_groups=[
            _mg(name="Size", modifiers=[_mod("Small", 599)]),
            _mg(name="Toppings", modifiers=[_mod("Cheese", 100)]),
        ])]
        result = self._fn()(items)
        item = result["menu"]["categories"][0]["items"][0]
        assert len(item["modifier_groups"]) == 2
        assert item["modifier_groups"][0]["name"] == "Size"
        assert item["modifier_groups"][1]["name"] == "Toppings"

    def test_ungrouped_variants_flat_modifiers(self):
        """29. Ungrouped variants → flat modifiers with kind-based groups."""
        items = [_item(variants=[
            {"label": "Small", "price_cents": 599, "kind": "size"},
            {"label": "Large", "price_cents": 899, "kind": "size"},
        ])]
        result = self._fn()(items)
        item = result["menu"]["categories"][0]["items"][0]
        assert item["modifier_groups"] == []
        assert len(item["modifiers"]) == 2
        assert item["modifiers"][0]["group"] == "Size"

    def test_mixed_groups_and_flat(self):
        """30. Mixed: modifier_groups + flat modifiers both present."""
        items = [_item(
            modifier_groups=[
                _mg(name="Toppings", modifiers=[_mod("Cheese", 100, "other")]),
            ],
            ungrouped_variants=[
                {"label": "Small", "price_cents": 0, "kind": "size"},
            ],
        )]
        result = self._fn()(items)
        item = result["menu"]["categories"][0]["items"][0]
        assert len(item["modifier_groups"]) == 1
        # Flat modifiers include both group modifiers + ungrouped
        assert len(item["modifiers"]) == 2

    def test_categories_sorted_alphabetically(self):
        """31. Categories sorted alphabetically."""
        items = [
            _item(name="Steak", category="Entrees"),
            _item(name="Beer", category="Drinks"),
        ]
        result = self._fn()(items)
        cats = [c["name"] for c in result["menu"]["categories"]]
        assert cats == ["Drinks", "Entrees"]

    def test_metadata_counts(self):
        """32. Metadata includes item/category counts."""
        items = [
            _item(name="Steak", category="Entrees"),
            _item(name="Beer", category="Drinks"),
        ]
        result = self._fn()(items)
        meta = result["metadata"]
        assert meta["item_count"] == 2
        assert meta["category_count"] == 2
        assert meta["format"] == "generic_pos"
        assert meta["version"] == "1.1"
