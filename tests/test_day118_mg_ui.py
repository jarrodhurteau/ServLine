"""
Day 118 — Modifier Group Management UI
========================================
Tests for the modifier-group editing UI wired into the card view.

All server-side features are tested via the Flask test client (rendered HTML
+ endpoint assertions).  JavaScript behaviour is not tested here.

Test plan (~36 tests):
  Class 1: Add Modifier Group Button (5 tests)
    1.  Editing draft: each item card has a .card-add-mg-btn
    2.  .card-add-mg-btn carries data-item-id matching item id
    3.  .card-add-mg-btn carries data-draft-id attribute
    4.  Non-editing draft: no .card-add-mg-btn rendered
    5.  Item already has groups: add-mg button still present

  Class 2: Group Editor Panel (8 tests)
    6.  Editing draft with group: .mg-editor panel rendered
    7.  .mg-editor has data-group-id matching group id
    8.  .mg-editor-name input has correct group name value
    9.  .mg-required-toggle checkbox present per group
    10. .mg-min-select element present per group
    11. .mg-max-select element present per group
    12. required=1 group: .mg-required-toggle has 'checked' attribute
    13. Non-editing draft: no .mg-editor panels (read-only card-group only)
    14. .mg-delete-btn present per group in editing mode

  Class 3: Modifier Rows within Group (6 tests)
    15. Group with modifier: .mg-modifier-row present in editing mode
    16. .mg-mod-label input has correct modifier label value
    17. .mg-mod-price input has correct price value (dollars)
    18. .mg-mod-delete button present per modifier row in editing mode
    19. .mg-add-mod-row present per group in editing mode
    20. Non-editing draft: no .mg-mod-delete buttons

  Class 4: Template Library UI (5 tests)
    21. Editing draft: .card-apply-template-btn per item card
    22. .card-apply-template-btn carries data-item-id
    23. Non-editing draft: no .card-apply-template-btn
    24. #mg-template-modal element present in editing draft
    25. GET /restaurants/<id>/modifier_templates → 200 + ok + templates list

  Class 5: Endpoint Tests (8 tests)
    26. POST add_modifier_group → 201 + group_id
    27. POST add_modifier_group with missing name → 400
    28. PATCH update_modifier_group name → 200
    29. PATCH update_modifier_group not found → 404
    30. PATCH update_modifier_group required field → 200
    31. POST apply_modifier_template without template_id → 400
    32. POST apply_modifier_template with valid template → 200 + group_id
    33. GET list_modifier_templates → 200 + count field

  Class 6: Save Integration (4 tests)
    34. POST /drafts/<id>/save with deleted_modifier_group_ids → 200 + deleted_mg_count
    35. Save with empty deleted_modifier_group_ids list → ok + deleted_mg_count 0
    36. Save with non-existent mg id in deleted list → ok (graceful skip)
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

import pytest

import storage.drafts as drafts_mod


# ---------------------------------------------------------------------------
# Schema
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


def _insert_item(conn, draft_id, name="Burger", price=999, category="Mains") -> int:
    iid = conn.execute(
        "INSERT INTO draft_items "
        "(draft_id, name, price_cents, category, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (draft_id, name, price, category, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return iid


def _insert_group(conn, item_id, name="Sauce", required=0,
                  min_select=0, max_select=3) -> int:
    gid = conn.execute(
        "INSERT INTO draft_modifier_groups "
        "(item_id, name, required, min_select, max_select, position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        (item_id, name, required, min_select, max_select, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return gid


def _insert_modifier(conn, item_id, group_id, label="Ranch", price=0) -> int:
    vid = conn.execute(
        "INSERT INTO draft_item_variants "
        "(item_id, label, price_cents, kind, position, modifier_group_id, created_at, updated_at) "
        "VALUES (?, ?, ?, 'other', 0, ?, ?, ?)",
        (item_id, label, price, group_id, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return vid


def _insert_template(conn, restaurant_id=None, name="Sauces",
                     modifiers=None) -> int:
    mods_json = json.dumps(modifiers or [{"label": "Ranch", "price_cents": 0}])
    tid = conn.execute(
        "INSERT INTO draft_modifier_group_templates "
        "(restaurant_id, name, required, min_select, max_select, position, modifiers, created_at, updated_at) "
        "VALUES (?, ?, 0, 0, 3, 0, ?, ?, ?)",
        (restaurant_id, name, mods_json, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return tid


# ---------------------------------------------------------------------------
# Shared DB / Flask client fixtures
# ---------------------------------------------------------------------------

_TEST_CONN: Optional[sqlite3.Connection] = None


def _patch_db(monkeypatch):
    global _TEST_CONN
    _TEST_CONN = _make_conn()
    import portal.app as _portal_app_module

    def _mock_connect():
        return _TEST_CONN

    monkeypatch.setattr(drafts_mod, "db_connect", _mock_connect)
    monkeypatch.setattr(_portal_app_module, "db_connect", _mock_connect)
    return _TEST_CONN


@pytest.fixture
def fresh_db(monkeypatch):
    c = _patch_db(monkeypatch)
    yield c


@pytest.fixture
def client(fresh_db):
    import portal.app as _app_module
    app = _app_module.app
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = {"id": 1, "email": "test@test.com"}
        yield c


def _get_editor(client, draft_id: int) -> str:
    resp = client.get(f"/drafts/{draft_id}/edit")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    return resp.data.decode("utf-8")


# ---------------------------------------------------------------------------
# Class 1: Add Modifier Group Button
# ---------------------------------------------------------------------------

class TestAddMgButton:
    """Per-item Add Modifier Group button rendered in editing mode."""

    def test_add_mg_btn_present_in_editing(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did)
        html = _get_editor(client, did)
        assert '<button class="card-add-mg-btn"' in html

    def test_add_mg_btn_has_data_item_id(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        html = _get_editor(client, did)
        import re
        m = re.search(r'card-add-mg-btn[^>]*data-item-id="(\d+)"', html)
        if not m:
            m = re.search(r'data-item-id="(\d+)"[^>]*card-add-mg-btn', html)
        assert m is not None, "card-add-mg-btn missing data-item-id"
        assert int(m.group(1)) == iid

    def test_add_mg_btn_has_data_draft_id(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did)
        html = _get_editor(client, did)
        import re
        m = re.search(r'card-add-mg-btn[^>]*data-draft-id="(\d+)"', html)
        if not m:
            m = re.search(r'data-draft-id="(\d+)"[^>]*card-add-mg-btn', html)
        assert m is not None, "card-add-mg-btn missing data-draft-id"
        assert int(m.group(1)) == did

    def test_add_mg_btn_absent_in_non_editing(self, client, fresh_db):
        _, did = _create_draft(fresh_db, status="approved")
        _insert_item(fresh_db, did)
        html = _get_editor(client, did)
        assert '<button class="card-add-mg-btn"' not in html

    def test_add_mg_btn_present_when_item_has_groups(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        _insert_group(fresh_db, iid)
        html = _get_editor(client, did)
        assert '<button class="card-add-mg-btn"' in html


# ---------------------------------------------------------------------------
# Class 2: Group Editor Panel
# ---------------------------------------------------------------------------

class TestGroupEditorPanel:
    """mg-editor panels rendered per group in editing mode."""

    def test_mg_editor_present_in_editing(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        _insert_group(fresh_db, iid)
        html = _get_editor(client, did)
        assert 'class="mg-editor"' in html

    def test_mg_editor_has_data_group_id(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        gid = _insert_group(fresh_db, iid)
        html = _get_editor(client, did)
        assert f'class="mg-editor" data-group-id="{gid}"' in html

    def test_mg_editor_name_input_value(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        _insert_group(fresh_db, iid, name="Dipping Sauce")
        html = _get_editor(client, did)
        assert 'class="mg-editor-name"' in html
        assert "Dipping Sauce" in html

    def test_mg_required_toggle_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        _insert_group(fresh_db, iid)
        html = _get_editor(client, did)
        assert 'class="mg-required-toggle"' in html

    def test_mg_min_select_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        _insert_group(fresh_db, iid)
        html = _get_editor(client, did)
        assert 'class="mg-min-select"' in html

    def test_mg_max_select_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        _insert_group(fresh_db, iid)
        html = _get_editor(client, did)
        assert 'class="mg-max-select"' in html

    def test_required_group_toggle_checked(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        _insert_group(fresh_db, iid, required=1)
        html = _get_editor(client, did)
        # The required toggle input should carry the 'checked' attribute
        import re
        # Find mg-required-toggle input and verify checked attribute
        m = re.search(r'class="mg-required-toggle"[^>]*(checked)', html)
        if not m:
            m = re.search(r'(checked)[^>]*class="mg-required-toggle"', html)
        assert m is not None, "Required group toggle should have checked attribute"

    def test_no_mg_editor_in_non_editing(self, client, fresh_db):
        _, did = _create_draft(fresh_db, status="approved")
        iid = _insert_item(fresh_db, did)
        _insert_group(fresh_db, iid)
        html = _get_editor(client, did)
        assert 'class="mg-editor"' not in html

    def test_mg_delete_btn_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        _insert_group(fresh_db, iid)
        html = _get_editor(client, did)
        assert 'class="mg-delete-btn"' in html


# ---------------------------------------------------------------------------
# Class 3: Modifier Rows within Group
# ---------------------------------------------------------------------------

class TestModifierRows:
    """Modifier (variant) rows rendered inside group panel in editing mode."""

    def test_mg_modifier_row_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        gid = _insert_group(fresh_db, iid)
        _insert_modifier(fresh_db, iid, gid)
        html = _get_editor(client, did)
        assert 'class="mg-modifier-row"' in html

    def test_mg_mod_label_input_value(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        gid = _insert_group(fresh_db, iid)
        _insert_modifier(fresh_db, iid, gid, label="Ranch")
        html = _get_editor(client, did)
        assert 'class="mg-mod-label"' in html
        assert "Ranch" in html

    def test_mg_mod_price_input_value(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        gid = _insert_group(fresh_db, iid)
        _insert_modifier(fresh_db, iid, gid, label="Truffle", price=150)
        html = _get_editor(client, did)
        assert 'class="mg-mod-price"' in html
        assert "1.50" in html

    def test_mg_mod_delete_btn_present_in_editing(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        gid = _insert_group(fresh_db, iid)
        _insert_modifier(fresh_db, iid, gid)
        html = _get_editor(client, did)
        assert 'class="mg-mod-delete"' in html

    def test_mg_add_mod_row_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        _insert_group(fresh_db, iid)
        html = _get_editor(client, did)
        assert 'class="mg-add-mod-row"' in html

    def test_no_mg_mod_delete_in_non_editing(self, client, fresh_db):
        _, did = _create_draft(fresh_db, status="approved")
        iid = _insert_item(fresh_db, did)
        gid = _insert_group(fresh_db, iid)
        _insert_modifier(fresh_db, iid, gid)
        html = _get_editor(client, did)
        assert 'class="mg-mod-delete"' not in html


# ---------------------------------------------------------------------------
# Class 4: Template Library UI
# ---------------------------------------------------------------------------

class TestTemplateLibraryUI:
    """Apply-from-template button and modal rendered in editing mode."""

    def test_apply_template_btn_present_in_editing(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did)
        html = _get_editor(client, did)
        assert '<button class="card-apply-template-btn"' in html

    def test_apply_template_btn_has_data_item_id(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        html = _get_editor(client, did)
        import re
        m = re.search(
            r'card-apply-template-btn[^>]*data-item-id="(\d+)"', html
        )
        if not m:
            m = re.search(
                r'data-item-id="(\d+)"[^>]*card-apply-template-btn', html
            )
        assert m is not None, "card-apply-template-btn missing data-item-id"
        assert int(m.group(1)) == iid

    def test_apply_template_btn_absent_in_non_editing(self, client, fresh_db):
        _, did = _create_draft(fresh_db, status="approved")
        _insert_item(fresh_db, did)
        html = _get_editor(client, did)
        assert '<button class="card-apply-template-btn"' not in html

    def test_mg_template_modal_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did)
        html = _get_editor(client, did)
        assert 'id="mg-template-modal"' in html

    def test_get_modifier_templates_endpoint(self, client, fresh_db):
        rid, _ = _create_draft(fresh_db)
        _insert_template(fresh_db, restaurant_id=rid, name="Sauces")
        resp = client.get(f"/restaurants/{rid}/modifier_templates")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert isinstance(data["templates"], list)
        assert data["count"] >= 1


# ---------------------------------------------------------------------------
# Class 5: Endpoint Tests
# ---------------------------------------------------------------------------

class TestEndpoints:
    """Direct endpoint tests for modifier group CRUD + template apply."""

    def test_add_modifier_group_success(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/modifier_groups",
            json={"name": "Toppings", "required": False,
                  "min_select": 0, "max_select": 3},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["ok"] is True
        assert isinstance(data["group_id"], int)

    def test_add_modifier_group_missing_name(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/modifier_groups",
            json={"required": False},
        )
        assert resp.status_code == 400

    def test_update_modifier_group_name(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        gid = _insert_group(fresh_db, iid, name="Old Name")
        resp = client.patch(
            f"/drafts/{did}/modifier_groups/{gid}",
            json={"name": "New Name"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_update_modifier_group_not_found(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        resp = client.patch(
            f"/drafts/{did}/modifier_groups/99999",
            json={"name": "Ghost"},
        )
        assert resp.status_code == 404

    def test_update_modifier_group_required(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        gid = _insert_group(fresh_db, iid, required=0)
        resp = client.patch(
            f"/drafts/{did}/modifier_groups/{gid}",
            json={"required": True},
        )
        assert resp.status_code == 200

    def test_apply_template_missing_template_id(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        resp = client.post(
            f"/drafts/{did}/items/{iid}/apply_template",
            json={},
        )
        assert resp.status_code == 400

    def test_apply_template_success(self, client, fresh_db):
        rid, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        tid = _insert_template(
            fresh_db, restaurant_id=rid, name="Sauces",
            modifiers=[{"label": "Ranch", "price_cents": 0},
                       {"label": "Ketchup", "price_cents": 50}],
        )
        resp = client.post(
            f"/drafts/{did}/items/{iid}/apply_template",
            json={"template_id": tid},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "group_id" in data

    def test_list_modifier_templates_count(self, client, fresh_db):
        rid, _ = _create_draft(fresh_db)
        _insert_template(fresh_db, restaurant_id=rid, name="T1")
        _insert_template(fresh_db, restaurant_id=rid, name="T2")
        resp = client.get(f"/restaurants/{rid}/modifier_templates")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 2


# ---------------------------------------------------------------------------
# Class 6: Save Integration
# ---------------------------------------------------------------------------

class TestSaveIntegration:
    """Save endpoint correctly handles deleted_modifier_group_ids."""

    def test_save_deletes_modifier_groups(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        gid = _insert_group(fresh_db, iid)
        resp = client.post(
            f"/drafts/{did}/save",
            json={
                "items": [{"id": iid, "name": "Burger", "price_cents": 999,
                            "category": "Mains", "position": 0}],
                "deleted_modifier_group_ids": [gid],
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["deleted_mg_count"] == 1

    def test_save_empty_deleted_mg_ids(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        resp = client.post(
            f"/drafts/{did}/save",
            json={
                "items": [{"id": iid, "name": "Burger", "price_cents": 999,
                            "category": "Mains", "position": 0}],
                "deleted_modifier_group_ids": [],
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["deleted_mg_count"] == 0

    def test_save_nonexistent_mg_id_graceful(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        resp = client.post(
            f"/drafts/{did}/save",
            json={
                "items": [{"id": iid, "name": "Burger", "price_cents": 999,
                            "category": "Mains", "position": 0}],
                "deleted_modifier_group_ids": [99999],
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_save_without_deleted_mg_ids_ok(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did)
        resp = client.post(
            f"/drafts/{did}/save",
            json={
                "items": [{"id": iid, "name": "Burger", "price_cents": 999,
                            "category": "Mains", "position": 0}],
            },
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
