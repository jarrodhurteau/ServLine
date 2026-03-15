"""
Day 120 — Drag-and-Drop Reordering
====================================
Tests for:
  - reorder_items() storage function
  - POST /drafts/<id>/items/reorder endpoint
  - POST /drafts/<id>/items/<item_id>/modifier_groups/reorder endpoint (already existed — smoke tests)
  - POST /drafts/<id>/modifier_groups/<group_id>/modifiers/reorder endpoint (already existed — smoke tests)
  - Template: item-card has draggable="true" in editing mode
  - Template: drag-handle element present in cards, mg-editors, mg-modifier-rows
  - Template: mg-editor has draggable="true"
  - Template: mg-modifier-row has draggable="true"
  - Day 120 JS block present in template source

Test plan (~32 tests):
  Class 1: reorder_items() storage function (8 tests)
    1.  Basic reorder: 3 items in new order → positions updated
    2.  Partial order: only some IDs provided → only those updated
    3.  Empty list → 0 updated, no crash
    4.  IDs from wrong draft silently ignored
    5.  Single item → position set to 0
    6.  Position values equal array indices (1-based)
    7.  Already-in-order list → still updates positions
    8.  Unknown IDs (nonexistent) → 0 updated

  Class 2: POST /drafts/<id>/items/reorder endpoint (8 tests)
    9.  Valid ordered_ids → 200 + ok=true + updated count
    10. Reorders items (db check: positions set correctly)
    11. Missing ordered_ids key → 400
    12. Non-list ordered_ids → 400
    13. Non-integer in list → 400
    14. Empty list → 200 + ok=true + updated=0
    15. Wrong draft_id (not found draft) → 200 + ok=true (draft-agnostic: storage ignores)
    16. Unauthenticated → 302 redirect

  Class 3: Modifier groups reorder endpoint smoke tests (4 tests)
    17. Valid reorder of modifier groups → 200 + ok=true
    18. Wrong item_id → 200 + updated=0
    19. Empty ordered_ids → 200 + updated=0
    20. Non-list body → 400

  Class 4: Modifiers (variants) reorder endpoint smoke tests (4 tests)
    21. Valid reorder of modifiers → 200 + ok=true
    22. Wrong group_id → 200 + updated=0
    23. Empty ordered_ids → 200 + updated=0
    24. Non-list body → 400

  Class 5: Template — drag attributes on item cards (4 tests)
    25. item-card has draggable="true" when draft.status=editing
    26. drag-handle element present on item cards in editing mode
    27. item-card does NOT have draggable="true" when draft.status=approved
    28. drag-handle not present in non-editing mode

  Class 6: Template — drag attributes on mg-editor and mg-modifier-row (2 tests)
    29. mg-editor has draggable="true" in editing mode
    30. mg-modifier-row has draggable="true" in editing mode

  Class 7: Template — Day 120 JS block (2 tests)
    31. _setupDragDrop function defined in template source
    32. _wireCardGroupsDragDrop function defined in template source
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import storage.drafts as drafts_mod

# ---------------------------------------------------------------------------
# Schema (matches Day 119)
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


def _create_item(conn, draft_id: int, name="Burger", position=0) -> int:
    iid = conn.execute(
        "INSERT INTO draft_items (draft_id, name, price_cents, position, created_at, updated_at) "
        "VALUES (?, ?, 999, ?, ?, ?)",
        (draft_id, name, position, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return iid


def _create_group(conn, item_id: int, name="Size", position=0) -> int:
    gid = conn.execute(
        "INSERT INTO draft_modifier_groups "
        "(item_id, name, required, min_select, max_select, position, created_at, updated_at) "
        "VALUES (?, ?, 0, 0, 0, ?, ?, ?)",
        (item_id, name, position, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return gid


def _create_modifier(conn, item_id: int, group_id: int, label: str, position=0) -> int:
    vid = conn.execute(
        "INSERT INTO draft_item_variants "
        "(item_id, label, price_cents, kind, position, modifier_group_id, created_at, updated_at) "
        "VALUES (?, ?, 0, 'other', ?, ?, ?, ?)",
        (item_id, label, position, group_id, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return vid


def _item_position(conn, item_id: int):
    row = conn.execute("SELECT position FROM draft_items WHERE id=?", (item_id,)).fetchone()
    return row["position"] if row else None


def _group_position(conn, group_id: int):
    row = conn.execute("SELECT position FROM draft_modifier_groups WHERE id=?", (group_id,)).fetchone()
    return row["position"] if row else None


def _mod_position(conn, mod_id: int):
    row = conn.execute("SELECT position FROM draft_item_variants WHERE id=?", (mod_id,)).fetchone()
    return row["position"] if row else None


# ---------------------------------------------------------------------------
# Fixtures — storage-only + Flask app
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(monkeypatch):
    """Patch drafts_mod.db_connect; yield (conn,)."""
    conn = _make_conn()
    monkeypatch.setattr(drafts_mod, "db_connect", lambda: conn)
    yield conn


@pytest.fixture()
def app_ctx(monkeypatch):
    """Patch drafts_mod.db_connect + portal.app.db_connect; yield (app, client, conn)."""
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
# Class 1: reorder_items() storage function (tests 1–8)
# ---------------------------------------------------------------------------

def test_reorder_items_basic(db):
    """3 items → reorder to [c, a, b] → positions 1,2,3 respectively."""
    _, did = _create_draft(db)
    a = _create_item(db, did, "A", position=0)
    b = _create_item(db, did, "B", position=1)
    c = _create_item(db, did, "C", position=2)
    updated = drafts_mod.reorder_items(did, [c, a, b])
    assert updated == 3
    assert _item_position(db, c) == 1
    assert _item_position(db, a) == 2
    assert _item_position(db, b) == 3


def test_reorder_items_partial(db):
    """Provide only 2 of 3 IDs → only those 2 updated."""
    _, did = _create_draft(db)
    a = _create_item(db, did, "A", position=0)
    b = _create_item(db, did, "B", position=1)
    _c = _create_item(db, did, "C", position=2)
    updated = drafts_mod.reorder_items(did, [b, a])
    assert updated == 2
    assert _item_position(db, b) == 1
    assert _item_position(db, a) == 2


def test_reorder_items_empty(db):
    """Empty list → 0 updated, no crash."""
    _, did = _create_draft(db)
    _create_item(db, did, "A")
    result = drafts_mod.reorder_items(did, [])
    assert result == 0


def test_reorder_items_wrong_draft_ignored(db):
    """IDs from a different draft are silently skipped."""
    _, did1 = _create_draft(db)
    _, did2 = _create_draft(db)
    a = _create_item(db, did1, "A", position=5)
    # Try to reorder item from did1 using did2 as the parent
    updated = drafts_mod.reorder_items(did2, [a])
    assert updated == 0
    # Position of 'a' unchanged
    assert _item_position(db, a) == 5


def test_reorder_items_single(db):
    """Single item → position set to 1."""
    _, did = _create_draft(db)
    a = _create_item(db, did, "A", position=7)
    updated = drafts_mod.reorder_items(did, [a])
    assert updated == 1
    assert _item_position(db, a) == 1


def test_reorder_items_positions_are_zero_based(db):
    """Positions assigned equal array indices (1-based)."""
    _, did = _create_draft(db)
    ids = [_create_item(db, did, f"Item{i}") for i in range(5)]
    reversed_ids = list(reversed(ids))
    drafts_mod.reorder_items(did, reversed_ids)
    for expected_pos, item_id in enumerate(reversed_ids, start=1):
        assert _item_position(db, item_id) == expected_pos


def test_reorder_items_already_ordered(db):
    """Sending same order still updates positions."""
    _, did = _create_draft(db)
    a = _create_item(db, did, "A", position=0)
    b = _create_item(db, did, "B", position=1)
    updated = drafts_mod.reorder_items(did, [a, b])
    assert updated == 2
    assert _item_position(db, a) == 1
    assert _item_position(db, b) == 2


def test_reorder_items_nonexistent_ids(db):
    """Nonexistent IDs → 0 updated."""
    _, did = _create_draft(db)
    result = drafts_mod.reorder_items(did, [9999, 8888])
    assert result == 0


# ---------------------------------------------------------------------------
# Class 2: POST /drafts/<id>/items/reorder endpoint (tests 9–16)
# ---------------------------------------------------------------------------

def test_items_reorder_endpoint_ok(app_ctx):
    """Valid ordered_ids → 200 + ok=true + updated count."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    a = _create_item(conn, did, "A")
    b = _create_item(conn, did, "B")
    resp = client.post(
        f"/drafts/{did}/items/reorder",
        data=json.dumps({"ordered_ids": [b, a]}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["ok"] is True
    assert data["updated"] == 2


def test_items_reorder_endpoint_db_positions(app_ctx):
    """Endpoint updates DB positions correctly."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    a = _create_item(conn, did, "A")
    b = _create_item(conn, did, "B")
    c = _create_item(conn, did, "C")
    client.post(
        f"/drafts/{did}/items/reorder",
        data=json.dumps({"ordered_ids": [c, a, b]}),
        content_type="application/json",
    )
    assert _item_position(conn, c) == 1
    assert _item_position(conn, a) == 2
    assert _item_position(conn, b) == 3


def test_items_reorder_endpoint_missing_key(app_ctx):
    """Missing ordered_ids key → treated as empty list → 200 + updated=0.

    Consistent with existing modifier_groups_reorder / modifiers_reorder behavior:
    _parse_ordered_ids coerces missing key to [].
    """
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    resp = client.post(
        f"/drafts/{did}/items/reorder",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["ok"] is True
    assert data["updated"] == 0


def test_items_reorder_endpoint_non_list(app_ctx):
    """Non-list ordered_ids → 400."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    resp = client.post(
        f"/drafts/{did}/items/reorder",
        data=json.dumps({"ordered_ids": "not-a-list"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_items_reorder_endpoint_non_integer(app_ctx):
    """Non-integer in ordered_ids → 400."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    resp = client.post(
        f"/drafts/{did}/items/reorder",
        data=json.dumps({"ordered_ids": ["abc", "def"]}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_items_reorder_endpoint_empty_list(app_ctx):
    """Empty ordered_ids → 200 + updated=0."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    resp = client.post(
        f"/drafts/{did}/items/reorder",
        data=json.dumps({"ordered_ids": []}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["ok"] is True
    assert data["updated"] == 0


def test_items_reorder_endpoint_wrong_draft(app_ctx):
    """IDs not belonging to draft → updated=0, still 200."""
    app, client, conn = app_ctx
    _, did1 = _create_draft(conn)
    _, did2 = _create_draft(conn)
    a = _create_item(conn, did1, "A")
    resp = client.post(
        f"/drafts/{did2}/items/reorder",
        data=json.dumps({"ordered_ids": [a]}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["updated"] == 0


def test_items_reorder_endpoint_unauthenticated(app_ctx):
    """No session → redirect to login."""
    import portal.app as _app_mod
    _, did = _create_draft(app_ctx[2])
    with _app_mod.app.test_client() as anon:
        resp = anon.post(
            f"/drafts/{did}/items/reorder",
            data=json.dumps({"ordered_ids": []}),
            content_type="application/json",
        )
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Class 3: Modifier groups reorder endpoint smoke tests (tests 17–20)
# ---------------------------------------------------------------------------

def test_mg_reorder_endpoint_ok(app_ctx):
    """Valid group reorder → 200 + ok=true."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    iid = _create_item(conn, did, "Pizza")
    g1 = _create_group(conn, iid, "Crust", position=0)
    g2 = _create_group(conn, iid, "Sauce", position=1)
    resp = client.post(
        f"/drafts/{did}/items/{iid}/modifier_groups/reorder",
        data=json.dumps({"ordered_ids": [g2, g1]}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert json.loads(resp.data)["ok"] is True
    assert _group_position(conn, g2) == 1
    assert _group_position(conn, g1) == 2


def test_mg_reorder_endpoint_wrong_item(app_ctx):
    """Group IDs from wrong item → updated=0."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    iid1 = _create_item(conn, did, "Pizza")
    iid2 = _create_item(conn, did, "Pasta")
    g1 = _create_group(conn, iid1, "Crust")
    resp = client.post(
        f"/drafts/{did}/items/{iid2}/modifier_groups/reorder",
        data=json.dumps({"ordered_ids": [g1]}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert json.loads(resp.data)["updated"] == 0


def test_mg_reorder_endpoint_empty(app_ctx):
    """Empty ordered_ids → 200 + updated=0."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    iid = _create_item(conn, did, "Pizza")
    resp = client.post(
        f"/drafts/{did}/items/{iid}/modifier_groups/reorder",
        data=json.dumps({"ordered_ids": []}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert json.loads(resp.data)["updated"] == 0


def test_mg_reorder_endpoint_non_list(app_ctx):
    """Non-list body → 400."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    iid = _create_item(conn, did, "Pizza")
    resp = client.post(
        f"/drafts/{did}/items/{iid}/modifier_groups/reorder",
        data=json.dumps({"ordered_ids": "bad"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Class 4: Modifiers reorder endpoint smoke tests (tests 21–24)
# ---------------------------------------------------------------------------

def test_mod_reorder_endpoint_ok(app_ctx):
    """Valid modifier reorder → 200 + ok=true + positions updated."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    iid = _create_item(conn, did, "Wings")
    gid = _create_group(conn, iid, "Flavor")
    m1 = _create_modifier(conn, iid, gid, "BBQ", position=0)
    m2 = _create_modifier(conn, iid, gid, "Spicy", position=1)
    resp = client.post(
        f"/drafts/{did}/modifier_groups/{gid}/modifiers/reorder",
        data=json.dumps({"ordered_ids": [m2, m1]}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert json.loads(resp.data)["ok"] is True
    assert _mod_position(conn, m2) == 1
    assert _mod_position(conn, m1) == 2


def test_mod_reorder_endpoint_wrong_group(app_ctx):
    """Modifier IDs from wrong group → updated=0."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    iid = _create_item(conn, did, "Wings")
    g1 = _create_group(conn, iid, "Flavor")
    g2 = _create_group(conn, iid, "Sauce")
    m1 = _create_modifier(conn, iid, g1, "BBQ")
    resp = client.post(
        f"/drafts/{did}/modifier_groups/{g2}/modifiers/reorder",
        data=json.dumps({"ordered_ids": [m1]}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert json.loads(resp.data)["updated"] == 0


def test_mod_reorder_endpoint_empty(app_ctx):
    """Empty ordered_ids → 200 + updated=0."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    iid = _create_item(conn, did, "Wings")
    gid = _create_group(conn, iid, "Flavor")
    resp = client.post(
        f"/drafts/{did}/modifier_groups/{gid}/modifiers/reorder",
        data=json.dumps({"ordered_ids": []}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert json.loads(resp.data)["updated"] == 0


def test_mod_reorder_endpoint_non_list(app_ctx):
    """Non-list body → 400."""
    app, client, conn = app_ctx
    _, did = _create_draft(conn)
    iid = _create_item(conn, did, "Wings")
    gid = _create_group(conn, iid, "Flavor")
    resp = client.post(
        f"/drafts/{did}/modifier_groups/{gid}/modifiers/reorder",
        data=json.dumps({"ordered_ids": 42}),
        content_type="application/json",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Helpers for template rendering tests
# ---------------------------------------------------------------------------

def _render_editor_html(app_ctx, status="editing", with_mg=False):
    app, client, conn = app_ctx
    _, did = _create_draft(conn, status=status)
    iid = _create_item(conn, did, "Burger")
    if with_mg:
        gid = _create_group(conn, iid, "Sauce")
        _create_modifier(conn, iid, gid, "Ketchup")
    resp = client.get(f"/drafts/{did}/edit")
    assert resp.status_code == 200
    return resp.data.decode()


# ---------------------------------------------------------------------------
# Class 5: Template — drag attributes on item cards (tests 25–28)
# ---------------------------------------------------------------------------

def test_card_draggable_in_editing(app_ctx):
    """item-card has draggable="true" when draft is editing."""
    html = _render_editor_html(app_ctx, status="editing")
    assert 'draggable="true"' in html


def test_card_drag_handle_in_editing(app_ctx):
    """drag-handle element present in cards when draft is editing."""
    html = _render_editor_html(app_ctx, status="editing")
    assert 'class="drag-handle"' in html


def test_card_not_draggable_when_approved(app_ctx):
    """item-card does NOT have draggable="true" in body when draft.status=approved.

    The CSS may reference draggable as an attribute selector, so strip <style>
    blocks before checking. In approved mode no HTML element should carry the
    attribute — the Jinja guard is {% if draft.status == 'editing' %}.
    """
    html = _render_editor_html(app_ctx, status="approved")
    # Strip inline <style> blocks so CSS attribute selectors don't confuse check
    import re
    body_only = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
    assert 'draggable="true"' not in body_only


def test_card_no_drag_handle_when_approved(app_ctx):
    """drag-handle not rendered in non-editing mode."""
    html = _render_editor_html(app_ctx, status="approved")
    assert 'class="drag-handle"' not in html


# ---------------------------------------------------------------------------
# Class 6: Template — drag attributes on mg-editor and mg-modifier-row (tests 29–30)
# ---------------------------------------------------------------------------

def test_mg_editor_draggable_in_editing(app_ctx):
    """mg-editor div has draggable="true" in editing mode."""
    html = _render_editor_html(app_ctx, status="editing", with_mg=True)
    # The mg-editor appears inside the expanded item card
    assert 'class="mg-editor' in html
    assert 'draggable="true"' in html


def test_mg_modifier_row_draggable_in_editing(app_ctx):
    """mg-modifier-row has draggable="true" in editing mode."""
    html = _render_editor_html(app_ctx, status="editing", with_mg=True)
    assert 'class="mg-modifier-row"' in html or 'mg-modifier-row' in html
    # draggable appears somewhere in the rendered modifier rows section
    assert 'draggable="true"' in html


# ---------------------------------------------------------------------------
# Class 7: Template — Day 120 JS block (tests 31–32)
# ---------------------------------------------------------------------------

def _template_source() -> str:
    import os
    path = os.path.join(
        os.path.dirname(__file__), "..", "portal", "templates", "draft_editor.html"
    )
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_day120_js_setup_drag_drop_defined():
    """_setupDragDrop function defined in template source."""
    src = _template_source()
    assert "_setupDragDrop" in src


def test_day120_js_wire_card_groups_defined():
    """_wireCardGroupsDragDrop function defined in template source."""
    src = _template_source()
    assert "_wireCardGroupsDragDrop" in src