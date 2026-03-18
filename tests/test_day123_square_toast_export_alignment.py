"""
Day 123 — Square & Toast Export Alignment + Modifier Group Validation
=====================================================================
Tests for:
  - _build_square_rows() upgraded: modifier groups → Square Modifier Sets
    with selection rules (Required, Min Select, Max Select columns)
  - _build_toast_rows() upgraded: modifier groups → Toast Option Groups
    with Required column
  - Backward compat: items with only ungrouped variants export as before
  - Mixed: items with both modifier_groups and ungrouped_variants
  - Square/Toast route handlers use include_modifier_groups=True
  - _validate_draft_for_export() new warnings:
    modifier_group_empty, required_group_empty,
    group_min_exceeds_max, group_max_exceeds_count

Test plan (~32 tests):
  Class 1: _build_square_rows() with modifier groups (8 tests)
    1.  Item with no modifiers → single row
    2.  Item with modifier group → modifier rows with group name as set name
    3.  Required flag → "Y" in Required column
    4.  Non-required → "N" in Required column
    5.  min_select / max_select columns populated
    6.  Multiple groups → rows in group order
    7.  Ungrouped variants fall back to kind-based grouping
    8.  Mixed: modifier_groups + ungrouped_variants both emitted

  Class 2: _build_toast_rows() with modifier groups (8 tests)
    9.  Item with no modifiers → single row (7 cols)
    10. Item with modifier group → option rows with group name
    11. Required group → "Y" in Required column
    12. Non-required → "N" in Required column
    13. Multiple groups → rows in group order
    14. Ungrouped variants fall back to kind-based grouping
    15. Category defaults to "Uncategorized" when empty
    16. Mixed: modifier_groups + ungrouped_variants both emitted

  Class 3: Square CSV route handler (4 tests)
    17. GET /drafts/<id>/export_square.csv → 200 + CSV with new headers
    18. Header row includes Required, Min Select, Max Select columns
    19. Modifier group rows have selection rule values
    20. Unauthenticated → 302

  Class 4: Toast CSV route handler (4 tests)
    21. GET /drafts/<id>/export_toast.csv → 200 + CSV with new headers
    22. Header row includes Required column
    23. Modifier group rows have required flag
    24. Unauthenticated → 302

  Class 5: _validate_draft_for_export() modifier group warnings (8 tests)
    25. Empty modifier group → modifier_group_empty warning
    26. Required empty group → required_group_empty warning
    27. min_select > max_select → group_min_exceeds_max warning
    28. max_select > modifier count → group_max_exceeds_count warning
    29. Valid group → no modifier group warnings
    30. Multiple issues on one group → multiple warnings
    31. Items without modifier groups → no new warnings
    32. Existing warnings still fire alongside new ones
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3

import pytest

import storage.drafts as drafts_mod


# ---------------------------------------------------------------------------
# Schema (matches Day 122)
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
          description=""):
    d = {
        "id": 1, "name": name, "description": description,
        "price_cents": price_cents, "category": category,
    }
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
# Class 1: _build_square_rows() with modifier groups (tests 1–8)
# ---------------------------------------------------------------------------

class TestBuildSquareRowsModifierGroups:
    """_build_square_rows upgraded for POS-native modifier groups."""

    def _fn(self):
        import portal.app as _app_mod
        return _app_mod._build_square_rows

    def test_no_modifiers_single_row(self):
        """1. Item with no modifiers → single row."""
        rows = self._fn()([_item()])
        assert len(rows) == 1
        assert rows[0][0] == "item"
        assert rows[0][1] == "Burger"

    def test_modifier_group_rows(self):
        """2. Item with modifier group → modifier rows with group name."""
        item = _item(modifier_groups=[
            _mg("Size", modifiers=[
                _mod("Small", 0), _mod("Large", 200),
            ]),
        ])
        rows = self._fn()([item])
        assert len(rows) == 3  # 1 parent + 2 modifiers
        assert rows[0][0] == "item"
        assert rows[1][0] == "modifier"
        assert rows[1][5] == "Size"  # Modifier Set Name
        assert rows[1][6] == "Small"  # Modifier Name
        assert rows[2][6] == "Large"

    def test_required_flag_yes(self):
        """3. Required flag → 'Y' in Required column."""
        item = _item(modifier_groups=[
            _mg("Size", required=True, modifiers=[_mod("Small")]),
        ])
        rows = self._fn()([item])
        assert rows[1][8] == "Y"

    def test_required_flag_no(self):
        """4. Non-required → 'N' in Required column."""
        item = _item(modifier_groups=[
            _mg("Size", required=False, modifiers=[_mod("Small")]),
        ])
        rows = self._fn()([item])
        assert rows[1][8] == "N"

    def test_min_max_select_columns(self):
        """5. min_select / max_select columns populated."""
        item = _item(modifier_groups=[
            _mg("Size", min_select=1, max_select=3, modifiers=[_mod("Small")]),
        ])
        rows = self._fn()([item])
        assert rows[1][9] == "1"   # Min Select
        assert rows[1][10] == "3"  # Max Select

    def test_multiple_groups(self):
        """6. Multiple groups → rows in group order."""
        item = _item(modifier_groups=[
            _mg("Size", modifiers=[_mod("Small", 0)]),
            _mg("Sauce", modifiers=[_mod("Ranch", 50)]),
        ])
        rows = self._fn()([item])
        assert len(rows) == 3  # 1 parent + 1 + 1
        assert rows[1][5] == "Size"
        assert rows[2][5] == "Sauce"

    def test_ungrouped_variants_fallback(self):
        """7. Ungrouped variants fall back to kind-based grouping."""
        item = _item(ungrouped_variants=[
            _mod("Extra Cheese", 100, "combo"),
        ])
        rows = self._fn()([item])
        assert len(rows) == 2  # 1 parent + 1 modifier
        assert rows[1][5] == "Combo Add-on"
        assert rows[1][6] == "Extra Cheese"
        # No selection rules for ungrouped
        assert rows[1][8] == ""

    def test_mixed_groups_and_ungrouped(self):
        """8. Mixed: modifier_groups + ungrouped_variants both emitted."""
        item = _item(
            modifier_groups=[
                _mg("Size", modifiers=[_mod("Small", 0), _mod("Large", 200)]),
            ],
            ungrouped_variants=[
                _mod("Extra Cheese", 100, "combo"),
            ],
        )
        rows = self._fn()([item])
        # 1 parent + 2 grouped + 1 ungrouped = 4
        assert len(rows) == 4
        assert rows[1][5] == "Size"
        assert rows[3][5] == "Combo Add-on"


# ---------------------------------------------------------------------------
# Class 2: _build_toast_rows() with modifier groups (tests 9–16)
# ---------------------------------------------------------------------------

class TestBuildToastRowsModifierGroups:
    """_build_toast_rows upgraded for POS-native modifier groups."""

    def _fn(self):
        import portal.app as _app_mod
        return _app_mod._build_toast_rows

    def test_no_modifiers_single_row(self):
        """9. Item with no modifiers → single row (7 cols)."""
        rows = self._fn()([_item()])
        assert len(rows) == 1
        assert rows[0][0] == "Entrees"
        assert rows[0][1] == "Burger"
        assert len(rows[0]) == 7

    def test_modifier_group_rows(self):
        """10. Item with modifier group → option rows with group name."""
        item = _item(modifier_groups=[
            _mg("Size", modifiers=[
                _mod("Small", 0), _mod("Large", 200),
            ]),
        ])
        rows = self._fn()([item])
        assert len(rows) == 3
        assert rows[1][3] == "Size"  # Option Group
        assert rows[1][4] == "Small"  # Option
        assert rows[2][4] == "Large"

    def test_required_flag_yes(self):
        """11. Required group → 'Y' in Required column."""
        item = _item(modifier_groups=[
            _mg("Size", required=True, modifiers=[_mod("Small")]),
        ])
        rows = self._fn()([item])
        assert rows[1][6] == "Y"

    def test_required_flag_no(self):
        """12. Non-required → 'N' in Required column."""
        item = _item(modifier_groups=[
            _mg("Size", required=False, modifiers=[_mod("Small")]),
        ])
        rows = self._fn()([item])
        assert rows[1][6] == "N"

    def test_multiple_groups(self):
        """13. Multiple groups → rows in group order."""
        item = _item(modifier_groups=[
            _mg("Size", modifiers=[_mod("Small")]),
            _mg("Sauce", modifiers=[_mod("Ranch", 50)]),
        ])
        rows = self._fn()([item])
        assert len(rows) == 3
        assert rows[1][3] == "Size"
        assert rows[2][3] == "Sauce"

    def test_ungrouped_variants_fallback(self):
        """14. Ungrouped variants fall back to kind-based grouping."""
        item = _item(ungrouped_variants=[
            _mod("Extra Cheese", 100, "combo"),
        ])
        rows = self._fn()([item])
        assert len(rows) == 2
        assert rows[1][3] == "Combo Add-on"
        assert rows[1][4] == "Extra Cheese"
        assert rows[1][6] == ""  # No required flag for ungrouped

    def test_category_defaults_uncategorized(self):
        """15. Category defaults to 'Uncategorized' when empty."""
        item = _item(category="")
        rows = self._fn()([item])
        assert rows[0][0] == "Uncategorized"

    def test_mixed_groups_and_ungrouped(self):
        """16. Mixed: modifier_groups + ungrouped_variants both emitted."""
        item = _item(
            modifier_groups=[
                _mg("Size", modifiers=[_mod("Small"), _mod("Large", 200)]),
            ],
            ungrouped_variants=[
                _mod("Extra Cheese", 100, "combo"),
            ],
        )
        rows = self._fn()([item])
        assert len(rows) == 4
        assert rows[1][3] == "Size"
        assert rows[3][3] == "Combo Add-on"


# ---------------------------------------------------------------------------
# Class 3: Square CSV route handler (tests 17–20)
# ---------------------------------------------------------------------------

class TestSquareCsvEndpoint:
    """GET /drafts/<id>/export_square.csv with modifier groups."""

    def test_returns_200_csv(self, app_ctx):
        """17. GET → 200 + CSV response."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did)
        resp = client.get(f"/drafts/{did}/export_square.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["Content-Type"]

    def test_header_includes_selection_rule_columns(self, app_ctx):
        """18. Header row includes Required, Min Select, Max Select."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did)
        csv_text = resp_to_csv_text(client.get(f"/drafts/{did}/export_square.csv"))
        rows = _parse_csv(csv_text)
        header = rows[0]
        assert "Required" in header
        assert "Min Select" in header
        assert "Max Select" in header

    def test_modifier_group_rows_have_selection_rules(self, app_ctx):
        """19. Modifier group rows have selection rule values."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did)
        gid = _create_modifier_group(conn, iid, "Size", required=1, min_select=1, max_select=2)
        _create_variant(conn, iid, "Small", 0, "size", modifier_group_id=gid)
        _create_variant(conn, iid, "Large", 200, "size", modifier_group_id=gid)

        csv_text = resp_to_csv_text(client.get(f"/drafts/{did}/export_square.csv"))
        rows = _parse_csv(csv_text)

        # Find modifier rows (skip header + item row)
        mod_rows = [r for r in rows if r[0] == "modifier"]
        assert len(mod_rows) == 2
        assert mod_rows[0][5] == "Size"  # Modifier Set Name
        assert mod_rows[0][8] == "Y"     # Required
        assert mod_rows[0][9] == "1"     # Min Select
        assert mod_rows[0][10] == "2"    # Max Select

    def test_unauthenticated_redirect(self, app_ctx):
        """20. Unauthenticated → 302."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        # Clear session
        with client.session_transaction() as sess:
            sess.clear()
        resp = client.get(f"/drafts/{did}/export_square.csv")
        assert resp.status_code == 302


def resp_to_csv_text(resp) -> str:
    """Decode a CSV response to text."""
    return resp.data.decode("utf-8-sig")


# ---------------------------------------------------------------------------
# Class 4: Toast CSV route handler (tests 21–24)
# ---------------------------------------------------------------------------

class TestToastCsvEndpoint:
    """GET /drafts/<id>/export_toast.csv with modifier groups."""

    def test_returns_200_csv(self, app_ctx):
        """21. GET → 200 + CSV response."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did)
        resp = client.get(f"/drafts/{did}/export_toast.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["Content-Type"]

    def test_header_includes_required_column(self, app_ctx):
        """22. Header row includes Required column."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did)
        csv_text = resp_to_csv_text(client.get(f"/drafts/{did}/export_toast.csv"))
        rows = _parse_csv(csv_text)
        header = rows[0]
        assert "Required" in header

    def test_modifier_group_rows_have_required(self, app_ctx):
        """23. Modifier group rows have required flag."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did)
        gid = _create_modifier_group(conn, iid, "Sauce", required=1)
        _create_variant(conn, iid, "Ranch", 50, "flavor", modifier_group_id=gid)

        csv_text = resp_to_csv_text(client.get(f"/drafts/{did}/export_toast.csv"))
        rows = _parse_csv(csv_text)

        # Find option rows (non-header, non-parent)
        opt_rows = [r for r in rows[1:] if r[3] != ""]
        assert len(opt_rows) == 1
        assert opt_rows[0][3] == "Sauce"
        assert opt_rows[0][4] == "Ranch"
        assert opt_rows[0][6] == "Y"

    def test_unauthenticated_redirect(self, app_ctx):
        """24. Unauthenticated → 302."""
        app, client, conn = app_ctx
        _, did = _create_draft(conn)
        with client.session_transaction() as sess:
            sess.clear()
        resp = client.get(f"/drafts/{did}/export_toast.csv")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Class 5: _validate_draft_for_export() modifier group warnings (tests 25–32)
# ---------------------------------------------------------------------------

class TestValidateExportModifierGroupWarnings:
    """New modifier group validation warnings in _validate_draft_for_export."""

    def _fn(self):
        import portal.app as _app_mod
        return _app_mod._validate_draft_for_export

    def test_empty_group_warning(self):
        """25. Empty modifier group → modifier_group_empty warning."""
        item = _item(modifier_groups=[_mg("Size", modifiers=[])])
        warnings = self._fn()([item])
        types = [w["type"] for w in warnings]
        assert "modifier_group_empty" in types

    def test_required_empty_group_warning(self):
        """26. Required empty group → required_group_empty warning."""
        item = _item(modifier_groups=[_mg("Size", required=True, modifiers=[])])
        warnings = self._fn()([item])
        types = [w["type"] for w in warnings]
        assert "required_group_empty" in types

    def test_min_exceeds_max_warning(self):
        """27. min_select > max_select → group_min_exceeds_max warning."""
        item = _item(modifier_groups=[
            _mg("Size", min_select=3, max_select=1, modifiers=[_mod("S"), _mod("M"), _mod("L")]),
        ])
        warnings = self._fn()([item])
        types = [w["type"] for w in warnings]
        assert "group_min_exceeds_max" in types

    def test_max_exceeds_count_warning(self):
        """28. max_select > modifier count → group_max_exceeds_count warning."""
        item = _item(modifier_groups=[
            _mg("Size", max_select=5, modifiers=[_mod("S"), _mod("L")]),
        ])
        warnings = self._fn()([item])
        types = [w["type"] for w in warnings]
        assert "group_max_exceeds_count" in types

    def test_valid_group_no_warnings(self):
        """29. Valid group → no modifier group warnings."""
        item = _item(
            price_cents=999, category="Entrees",
            modifier_groups=[
                _mg("Size", min_select=1, max_select=2, modifiers=[
                    _mod("Small", 100), _mod("Large", 200),
                ]),
            ],
        )
        warnings = self._fn()([item])
        mg_types = {w["type"] for w in warnings if w["type"].startswith(("modifier_group", "required_group", "group_"))}
        assert len(mg_types) == 0

    def test_multiple_issues_multiple_warnings(self):
        """30. Multiple issues on one group → multiple warnings."""
        item = _item(modifier_groups=[
            _mg("Size", required=True, min_select=3, max_select=1, modifiers=[]),
        ])
        warnings = self._fn()([item])
        types = [w["type"] for w in warnings]
        # empty + required_empty + min>max (max_exceeds_count skipped: max=1, count=0, but 0 triggers empty)
        assert "modifier_group_empty" in types
        assert "required_group_empty" in types
        assert "group_min_exceeds_max" in types

    def test_no_groups_no_new_warnings(self):
        """31. Items without modifier groups → no new warnings."""
        item = _item(price_cents=999, category="Entrees")
        warnings = self._fn()([item])
        mg_types = {w["type"] for w in warnings if w["type"].startswith(("modifier_group", "required_group", "group_"))}
        assert len(mg_types) == 0

    def test_existing_warnings_still_fire(self):
        """32. Existing warnings still fire alongside new ones."""
        item = _item(
            name="", price_cents=0, category="",
            modifier_groups=[_mg("Size", modifiers=[])],
        )
        warnings = self._fn()([item])
        types = [w["type"] for w in warnings]
        assert "missing_name" in types
        assert "missing_category" in types
        assert "missing_price" in types
        assert "modifier_group_empty" in types
