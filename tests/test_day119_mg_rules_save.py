"""
Day 119 — Modifier Group Rules Preview + Full Save Lifecycle
==============================================================
Tests for:
  - .mg-rules-preview element rendering with correct initial text
  - is-required CSS class on required modifier groups
  - JS helper functions present in template (updateRulesPreview, _mgRulesText)
  - upsert_group_modifiers() storage function
  - Save endpoint: modifier_groups_by_item syncs metadata + modifiers
  - Save endpoint: server-side warnings for required groups with no modifiers
  - Save endpoint: mg_synced count in response

Test plan (~35 tests):
  Class 1: Rules preview text logic via template rendering (8 tests)
    1.  required=0, min=0, max=0 → preview = "Optional"
    2.  required=0, min=0, max=3 → preview = "Choose up to 3"
    3.  required=1, min=0, max=0 → preview = "Required"
    4.  required=1, min=0, max=2 → preview = "Must choose up to 2"
    5.  required=1, min=1, max=1 → preview = "Must choose exactly 1"
    6.  required=1, min=2, max=2 → preview = "Must choose exactly 2"
    7.  required=1, min=1, max=3 → preview = "Must choose 1–3"
    8.  required=1, min=2, max=5 → preview = "Must choose 2–5"

  Class 2: Template structure — preview element + CSS class (7 tests)
    9.  mg-editor contains .mg-rules-preview element
    10. .mg-rules-preview carries data-group-id matching group
    11. .mg-rules-preview carries data-required attribute
    12. .mg-rules-preview carries data-min and data-max attributes
    13. Required group: .mg-editor has class "is-required"
    14. Optional group: .mg-editor does NOT have class "is-required"
    15. JS function updateRulesPreview present in template source

  Class 3: JS helpers in template source (3 tests)
    16. _mgRulesText function defined in template source
    17. window.updateRulesPreview assignment present
    18. .mg-rules-preview.is-optional CSS rule present in template

  Class 4: upsert_group_modifiers() storage function (8 tests)
    19. Basic insert: 2 modifiers → inserted=2, deleted=0
    20. Replace: existing 3 modifiers → new 2: deleted=3, inserted=2
    21. Empty list: clears all modifiers for group
    22. Blank label entries are skipped
    23. Unknown group_id → inserted=0, deleted=0
    24. price_cents defaults to 0 if missing
    25. Modifiers stored with correct modifier_group_id
    26. Modifiers stored with correct item_id

  Class 5: Save endpoint — modifier_groups_by_item (9 tests)
    27. modifier_groups_by_item with valid group → 200 + mg_synced=1
    28. modifier_groups_by_item updates group name via update_modifier_group
    29. modifier_groups_by_item updates required field
    30. modifier_groups_by_item updates min_select and max_select
    31. modifier_groups_by_item with modifiers → upserts modifiers (db check)
    32. modifier_groups_by_item with empty modifiers → clears modifiers
    33. modifier_groups_by_item with unknown group id → skips gracefully (mg_synced=0)
    34. modifier_groups_by_item absent → ok, mg_synced=0
    35. warnings list returned for required group with no modifiers
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import storage.drafts as drafts_mod

# ---------------------------------------------------------------------------
# Schema (matches Day 118)
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

_NOW = "2026-03-13T10:00:00"


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


def _create_item(conn, draft_id: int, name="Burger") -> int:
    iid = conn.execute(
        "INSERT INTO draft_items (draft_id, name, price_cents, created_at, updated_at) "
        "VALUES (?, ?, 999, ?, ?)",
        (draft_id, name, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return iid


def _create_group(conn, item_id: int, name="Size", required=0, min_sel=0, max_sel=0) -> int:
    gid = conn.execute(
        "INSERT INTO draft_modifier_groups "
        "(item_id, name, required, min_select, max_select, position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        (item_id, name, required, min_sel, max_sel, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return gid


def _create_modifier(conn, item_id: int, group_id: int, label: str, price_cents=0) -> int:
    vid = conn.execute(
        "INSERT INTO draft_item_variants "
        "(item_id, label, price_cents, kind, position, modifier_group_id, created_at, updated_at) "
        "VALUES (?, ?, ?, 'other', 0, ?, ?, ?)",
        (item_id, label, price_cents, group_id, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return vid


# ---------------------------------------------------------------------------
# Fixtures — Flask app + portal client
# ---------------------------------------------------------------------------

@pytest.fixture()
def app_ctx(monkeypatch):
    """Patch drafts_mod.db_connect to an in-memory DB, return (app, conn)."""
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
# Class 1: Rules preview text via template rendering (tests 1–8)
# ---------------------------------------------------------------------------

def _render_editor(app_ctx, required: int, min_sel: int, max_sel: int):
    """Render draft editor with one item that has one modifier group."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn, "editing")
    iid = _create_item(conn, did)
    _create_group(conn, iid, "Sauce", required=required, min_sel=min_sel, max_sel=max_sel)
    resp = client.get(f"/drafts/{did}/edit")
    assert resp.status_code == 200
    return resp.data.decode()


def test_preview_optional_no_max(app_ctx):
    """required=0, min=0, max=0 → 'Optional'"""
    html = _render_editor(app_ctx, required=0, min_sel=0, max_sel=0)
    assert "Optional" in html


def test_preview_optional_with_max(app_ctx):
    """required=0, max=3 → 'Choose up to 3'"""
    html = _render_editor(app_ctx, required=0, min_sel=0, max_sel=3)
    assert "Choose up to 3" in html


def test_preview_required_no_count(app_ctx):
    """required=1, min=0, max=0 → 'Required'"""
    html = _render_editor(app_ctx, required=1, min_sel=0, max_sel=0)
    assert "Required" in html


def test_preview_required_up_to(app_ctx):
    """required=1, min=0, max=2 → 'Must choose up to 2'"""
    html = _render_editor(app_ctx, required=1, min_sel=0, max_sel=2)
    assert "Must choose up to 2" in html


def test_preview_exactly_one(app_ctx):
    """required=1, min=1, max=1 → 'Must choose exactly 1'"""
    html = _render_editor(app_ctx, required=1, min_sel=1, max_sel=1)
    assert "Must choose exactly 1" in html


def test_preview_exactly_two(app_ctx):
    """required=1, min=2, max=2 → 'Must choose exactly 2'"""
    html = _render_editor(app_ctx, required=1, min_sel=2, max_sel=2)
    assert "Must choose exactly 2" in html


def test_preview_range_1_3(app_ctx):
    """required=1, min=1, max=3 → 'Must choose 1–3'"""
    html = _render_editor(app_ctx, required=1, min_sel=1, max_sel=3)
    assert "Must choose 1" in html
    assert "3" in html


def test_preview_range_2_5(app_ctx):
    """required=1, min=2, max=5 → 'Must choose 2–5'"""
    html = _render_editor(app_ctx, required=1, min_sel=2, max_sel=5)
    assert "Must choose 2" in html
    assert "5" in html


# ---------------------------------------------------------------------------
# Class 2: Template structure (tests 9–15)
# ---------------------------------------------------------------------------

def test_preview_element_present(app_ctx):
    """.mg-editor contains .mg-rules-preview element"""
    html = _render_editor(app_ctx, required=0, min_sel=0, max_sel=0)
    assert "mg-rules-preview" in html


def test_preview_data_group_id(app_ctx):
    """mg-rules-preview carries data-group-id"""
    app, client, conn = app_ctx
    _, did = _create_draft(conn, "editing")
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Toppings")
    resp = client.get(f"/drafts/{did}/edit")
    html = resp.data.decode()
    assert f'data-group-id="{gid}"' in html


def test_preview_data_required_attr(app_ctx):
    """mg-rules-preview carries data-required attribute"""
    html = _render_editor(app_ctx, required=1, min_sel=0, max_sel=0)
    assert 'data-required="true"' in html


def test_preview_data_min_max_attrs(app_ctx):
    """mg-rules-preview carries data-min and data-max"""
    html = _render_editor(app_ctx, required=0, min_sel=1, max_sel=4)
    assert 'data-min="1"' in html
    assert 'data-max="4"' in html


def test_required_group_has_is_required_class(app_ctx):
    """Required group: .mg-editor has class 'is-required'"""
    html = _render_editor(app_ctx, required=1, min_sel=0, max_sel=0)
    assert "mg-editor is-required" in html


def test_optional_group_no_is_required_class(app_ctx):
    """Optional group: .mg-editor does NOT have is-required class"""
    html = _render_editor(app_ctx, required=0, min_sel=0, max_sel=0)
    # The mg-editor open tag should not include is-required
    import re
    # Find the mg-editor opening div
    match = re.search(r'<div class="mg-editor([^"]*)"', html)
    assert match is not None
    assert "is-required" not in match.group(1)


def test_js_update_rules_preview_present(app_ctx):
    """JS function updateRulesPreview present in template source"""
    html = _render_editor(app_ctx, required=0, min_sel=0, max_sel=0)
    assert "updateRulesPreview" in html


# ---------------------------------------------------------------------------
# Class 3: JS helpers in template source (tests 16–18)
# ---------------------------------------------------------------------------

def test_js_mg_rules_text_defined(app_ctx):
    """_mgRulesText function defined in template"""
    html = _render_editor(app_ctx, required=0, min_sel=0, max_sel=0)
    assert "_mgRulesText" in html


def test_js_window_update_rules_preview_assigned(app_ctx):
    """window.updateRulesPreview assignment present"""
    html = _render_editor(app_ctx, required=0, min_sel=0, max_sel=0)
    assert "window.updateRulesPreview" in html


def test_css_mg_rules_preview_present(app_ctx):
    """mg-rules-preview CSS rule present in template"""
    html = _render_editor(app_ctx, required=0, min_sel=0, max_sel=0)
    assert ".mg-rules-preview" in html


# ---------------------------------------------------------------------------
# Class 4: upsert_group_modifiers() storage function (tests 19–26)
# ---------------------------------------------------------------------------

def _storage_ctx(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr(drafts_mod, "db_connect", lambda: conn)
    return conn


def test_upsert_basic_insert(monkeypatch):
    """2 modifiers → inserted=2, deleted=0"""
    conn = _storage_ctx(monkeypatch)
    _, did = _create_draft(conn)
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce")
    result = drafts_mod.upsert_group_modifiers(gid, [
        {"label": "Ranch", "price_cents": 0},
        {"label": "BBQ", "price_cents": 50},
    ])
    assert result["inserted"] == 2
    assert result["deleted"] == 0


def test_upsert_replace_existing(monkeypatch):
    """3 existing modifiers → replace with 2: deleted=3, inserted=2"""
    conn = _storage_ctx(monkeypatch)
    _, did = _create_draft(conn)
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce")
    _create_modifier(conn, iid, gid, "Ranch")
    _create_modifier(conn, iid, gid, "BBQ")
    _create_modifier(conn, iid, gid, "Honey Mustard")
    result = drafts_mod.upsert_group_modifiers(gid, [
        {"label": "Ranch", "price_cents": 0},
        {"label": "BBQ", "price_cents": 50},
    ])
    assert result["deleted"] == 3
    assert result["inserted"] == 2


def test_upsert_empty_clears(monkeypatch):
    """Empty list clears all modifiers for group"""
    conn = _storage_ctx(monkeypatch)
    _, did = _create_draft(conn)
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce")
    _create_modifier(conn, iid, gid, "Ranch")
    result = drafts_mod.upsert_group_modifiers(gid, [])
    assert result["deleted"] == 1
    assert result["inserted"] == 0
    rows = conn.execute(
        "SELECT * FROM draft_item_variants WHERE modifier_group_id=?", (gid,)
    ).fetchall()
    assert len(rows) == 0


def test_upsert_blank_labels_skipped(monkeypatch):
    """Blank label entries are skipped"""
    conn = _storage_ctx(monkeypatch)
    _, did = _create_draft(conn)
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce")
    result = drafts_mod.upsert_group_modifiers(gid, [
        {"label": "", "price_cents": 0},
        {"label": "  ", "price_cents": 0},
        {"label": "Ranch", "price_cents": 0},
    ])
    assert result["inserted"] == 1


def test_upsert_unknown_group(monkeypatch):
    """Unknown group_id → inserted=0, deleted=0"""
    conn = _storage_ctx(monkeypatch)
    _make_conn()  # no data
    result = drafts_mod.upsert_group_modifiers(99999, [{"label": "Foo"}])
    assert result["inserted"] == 0
    assert result["deleted"] == 0


def test_upsert_price_defaults_zero(monkeypatch):
    """price_cents defaults to 0 if missing"""
    conn = _storage_ctx(monkeypatch)
    _, did = _create_draft(conn)
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce")
    drafts_mod.upsert_group_modifiers(gid, [{"label": "Ranch"}])
    row = conn.execute(
        "SELECT price_cents FROM draft_item_variants WHERE modifier_group_id=?", (gid,)
    ).fetchone()
    assert row["price_cents"] == 0


def test_upsert_modifier_group_id_set(monkeypatch):
    """Modifiers stored with correct modifier_group_id"""
    conn = _storage_ctx(monkeypatch)
    _, did = _create_draft(conn)
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce")
    drafts_mod.upsert_group_modifiers(gid, [{"label": "Ranch", "price_cents": 25}])
    row = conn.execute(
        "SELECT modifier_group_id FROM draft_item_variants WHERE label='Ranch'"
    ).fetchone()
    assert row["modifier_group_id"] == gid


def test_upsert_item_id_set(monkeypatch):
    """Modifiers stored with correct item_id"""
    conn = _storage_ctx(monkeypatch)
    _, did = _create_draft(conn)
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce")
    drafts_mod.upsert_group_modifiers(gid, [{"label": "BBQ", "price_cents": 0}])
    row = conn.execute(
        "SELECT item_id FROM draft_item_variants WHERE label='BBQ'"
    ).fetchone()
    assert row["item_id"] == iid


# ---------------------------------------------------------------------------
# Class 5: Save endpoint — modifier_groups_by_item (tests 27–35)
# ---------------------------------------------------------------------------

def _save(client, draft_id: int, payload: dict):
    return client.post(
        f"/drafts/{draft_id}/save",
        data=json.dumps(payload),
        content_type="application/json",
    )


def test_save_mg_synced_count(app_ctx):
    """modifier_groups_by_item with valid group → 200 + mg_synced=1"""
    app, client, conn = app_ctx
    _, did = _create_draft(conn, "editing")
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce")
    payload = {
        "items": [],
        "modifier_groups_by_item": {
            str(iid): [{"id": gid, "name": "Sauce", "required": False,
                        "min_select": 0, "max_select": 0}]
        },
    }
    resp = _save(client, did, payload)
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["ok"] is True
    assert data["mg_synced"] == 1


def test_save_mg_updates_name(app_ctx):
    """modifier_groups_by_item updates group name"""
    app, client, conn = app_ctx
    _, did = _create_draft(conn, "editing")
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "OldName")
    payload = {
        "items": [],
        "modifier_groups_by_item": {
            str(iid): [{"id": gid, "name": "NewName"}]
        },
    }
    _save(client, did, payload)
    row = conn.execute("SELECT name FROM draft_modifier_groups WHERE id=?", (gid,)).fetchone()
    assert row["name"] == "NewName"


def test_save_mg_updates_required(app_ctx):
    """modifier_groups_by_item updates required field"""
    app, client, conn = app_ctx
    _, did = _create_draft(conn, "editing")
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce", required=0)
    payload = {
        "items": [],
        "modifier_groups_by_item": {
            str(iid): [{"id": gid, "name": "Sauce", "required": True}]
        },
    }
    _save(client, did, payload)
    row = conn.execute(
        "SELECT required FROM draft_modifier_groups WHERE id=?", (gid,)
    ).fetchone()
    assert row["required"] == 1


def test_save_mg_updates_min_max(app_ctx):
    """modifier_groups_by_item updates min_select and max_select"""
    app, client, conn = app_ctx
    _, did = _create_draft(conn, "editing")
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce")
    payload = {
        "items": [],
        "modifier_groups_by_item": {
            str(iid): [{"id": gid, "name": "Sauce", "min_select": 1, "max_select": 3}]
        },
    }
    _save(client, did, payload)
    row = conn.execute(
        "SELECT min_select, max_select FROM draft_modifier_groups WHERE id=?", (gid,)
    ).fetchone()
    assert row["min_select"] == 1
    assert row["max_select"] == 3


def test_save_mg_upserts_modifiers(app_ctx):
    """modifier_groups_by_item with modifiers → modifiers written to DB"""
    app, client, conn = app_ctx
    _, did = _create_draft(conn, "editing")
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce")
    payload = {
        "items": [],
        "modifier_groups_by_item": {
            str(iid): [{
                "id": gid, "name": "Sauce",
                "modifiers": [
                    {"label": "Ranch", "price_cents": 0},
                    {"label": "BBQ", "price_cents": 75},
                ],
            }]
        },
    }
    _save(client, did, payload)
    rows = conn.execute(
        "SELECT label FROM draft_item_variants WHERE modifier_group_id=? ORDER BY label",
        (gid,)
    ).fetchall()
    labels = [r["label"] for r in rows]
    assert "Ranch" in labels
    assert "BBQ" in labels


def test_save_mg_empty_modifiers_clears(app_ctx):
    """modifier_groups_by_item with empty modifiers → clears existing"""
    app, client, conn = app_ctx
    _, did = _create_draft(conn, "editing")
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce")
    _create_modifier(conn, iid, gid, "Ranch")
    payload = {
        "items": [],
        "modifier_groups_by_item": {
            str(iid): [{"id": gid, "name": "Sauce", "modifiers": []}]
        },
    }
    _save(client, did, payload)
    rows = conn.execute(
        "SELECT * FROM draft_item_variants WHERE modifier_group_id=?", (gid,)
    ).fetchall()
    assert len(rows) == 0


def test_save_mg_unknown_group_skipped(app_ctx):
    """modifier_groups_by_item with unknown group id → skips, mg_synced=0"""
    app, client, conn = app_ctx
    _, did = _create_draft(conn, "editing")
    iid = _create_item(conn, did)
    payload = {
        "items": [],
        "modifier_groups_by_item": {
            str(iid): [{"id": 99999, "name": "Ghost"}]
        },
    }
    resp = _save(client, did, payload)
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["mg_synced"] == 0


def test_save_mg_absent_returns_zero(app_ctx):
    """modifier_groups_by_item absent → ok, mg_synced=0"""
    app, client, conn = app_ctx
    _, did = _create_draft(conn, "editing")
    _create_item(conn, did)
    payload = {"items": []}
    resp = _save(client, did, payload)
    data = resp.get_json()
    assert data["ok"] is True
    assert data["mg_synced"] == 0


def test_save_required_group_no_modifiers_warning(app_ctx):
    """Required group with no modifiers → warnings list contains message"""
    app, client, conn = app_ctx
    _, did = _create_draft(conn, "editing")
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Toppings", required=1)
    payload = {
        "items": [],
        "modifier_groups_by_item": {
            str(iid): [{
                "id": gid, "name": "Toppings",
                "required": True, "modifiers": [],
            }]
        },
    }
    resp = _save(client, did, payload)
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["ok"] is True
    assert len(data["warnings"]) > 0
    assert "Toppings" in data["warnings"][0]


def test_save_warnings_empty_when_no_issues(app_ctx):
    """No required groups with missing modifiers → warnings=[]"""
    app, client, conn = app_ctx
    _, did = _create_draft(conn, "editing")
    iid = _create_item(conn, did)
    gid = _create_group(conn, iid, "Sauce", required=0)
    payload = {
        "items": [],
        "modifier_groups_by_item": {
            str(iid): [{"id": gid, "name": "Sauce", "required": False, "modifiers": []}]
        },
    }
    resp = _save(client, did, payload)
    data = resp.get_json()
    assert data["warnings"] == []
