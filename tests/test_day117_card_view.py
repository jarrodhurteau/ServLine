"""
Day 117 — Item Card Layout (Card View)
=======================================
Tests for the card-view UI alongside the existing table view.

All tests verify server-rendered HTML because card view is rendered by
Jinja2 at template time (not built purely by client-side JS).

Test plan (~32 tests):
  Class 1: HTML Structure (8 tests)
    1.  View-toggle container present in rendered HTML
    2.  Table-view button present with id=view-table-btn
    3.  Card-view button present with id=view-cards-btn
    4.  view-table-btn has 'active' class (table is default)
    5.  view-cards-btn does NOT have 'active' class by default
    6.  Card-view container (#card-view) present
    7.  Card grid (#card-grid) present inside card-view
    8.  Table wrap (#items-table-wrap) still present alongside card-view

  Class 2: Card Rendering Per Item (12 tests)
    9.  Each item produces a card element with class 'item-card'
    10. Card element has data-id matching item id
    11. Card has data-cat attribute matching item category (lowercased)
    12. Card displays item name in .card-name-display
    13. Card displays price in .card-price-display when price > 0
    14. Card has .card-badges container
    15. Item with variants gets .card-badge-variant badge
    16. Item without variants has no .card-badge-variant badge
    17. Item with modifier groups gets .card-badge-mg badge
    18. Item without modifier groups has no .card-badge-mg badge
    19. Low-confidence item card has 'is-low' class
    20. Item with description shows .card-desc element

  Class 3: Modifier Groups Inside Card (6 tests)
    21. Card with modifier groups contains .card-groups element
    22. Each modifier group renders a .card-group div
    23. Group name appears in .card-group-name
    24. Modifier (variant) label appears in .card-modifiers li
    25. Modifier price renders in .card-mod-price when present
    26. Item without modifier groups has no .card-groups element

  Class 4: Quick-Edit Inputs (4 tests)
    27. Editing draft: card has .card-quick-edit div per item
    28. Editing draft: .card-name-qe input has correct value
    29. Editing draft: .card-price-qe input has correct value
    30. Non-editing draft: no .card-quick-edit divs rendered

  Class 5: Card View Toggle Attributes (4 tests)
    31. view-table-btn has aria-pressed="true"
    32. view-cards-btn has aria-pressed="false"
    33. card-expand-btn present for items that have modifier groups
    34. card-expand-btn absent for items without modifier groups

~34 tests across 5 classes.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

import pytest

import storage.drafts as drafts_mod


# ---------------------------------------------------------------------------
# Schema (same as Day 116)
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


def _insert_item(conn, draft_id, name="Burger", price=999, category="Mains",
                 description=None, confidence=None) -> int:
    iid = conn.execute(
        "INSERT INTO draft_items "
        "(draft_id, name, price_cents, category, description, confidence, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (draft_id, name, price, category, description, confidence, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return iid


def _insert_variant(conn, item_id, label="Small", price=500,
                    group_id=None) -> int:
    vid = conn.execute(
        "INSERT INTO draft_item_variants "
        "(item_id, label, price_cents, kind, position, modifier_group_id, created_at, updated_at) "
        "VALUES (?, ?, ?, 'size', 0, ?, ?, ?)",
        (item_id, label, price, group_id, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return vid


def _insert_group(conn, item_id, name="Sauce", required=0) -> int:
    gid = conn.execute(
        "INSERT INTO draft_modifier_groups "
        "(item_id, name, required, min_select, max_select, position, created_at, updated_at) "
        "VALUES (?, ?, ?, 0, 3, 0, ?, ?)",
        (item_id, name, required, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return gid


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


def _get_editor(client, draft_id: int):
    """GET /drafts/<id>/edit and return response text."""
    resp = client.get(f"/drafts/{draft_id}/edit")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    return resp.data.decode("utf-8")


# ---------------------------------------------------------------------------
# Class 1: HTML Structure
# ---------------------------------------------------------------------------

class TestHtmlStructure:
    """View toggle and card-view container are present in rendered HTML."""

    def test_view_toggle_container_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        html = _get_editor(client, did)
        assert 'id="view-toggle"' in html

    def test_view_table_btn_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        html = _get_editor(client, did)
        assert 'id="view-table-btn"' in html

    def test_view_cards_btn_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        html = _get_editor(client, did)
        assert 'id="view-cards-btn"' in html

    def test_table_btn_has_active_class(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        html = _get_editor(client, did)
        assert 'id="view-table-btn"' in html
        # Find the button and verify 'active' is in its class list
        import re
        m = re.search(r'id="view-table-btn"[^>]*class="([^"]*)"', html)
        if not m:
            m = re.search(r'class="([^"]*)"[^>]*id="view-table-btn"', html)
        assert m is not None, "view-table-btn not found"
        assert "active" in m.group(1)

    def test_cards_btn_no_active_class_by_default(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        html = _get_editor(client, did)
        import re
        m = re.search(r'id="view-cards-btn"[^>]*class="([^"]*)"', html)
        if not m:
            m = re.search(r'class="([^"]*)"[^>]*id="view-cards-btn"', html)
        assert m is not None, "view-cards-btn not found"
        assert "active" not in m.group(1)

    def test_card_view_container_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        html = _get_editor(client, did)
        assert 'id="card-view"' in html

    def test_card_grid_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        html = _get_editor(client, did)
        assert 'id="card-grid"' in html

    def test_table_wrap_still_present(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        html = _get_editor(client, did)
        assert 'id="items-table-wrap"' in html


# ---------------------------------------------------------------------------
# Class 2: Card Rendering Per Item
# ---------------------------------------------------------------------------

class TestCardRendering:
    """Each item renders a card element with correct content and attributes."""

    def test_item_produces_card_element(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza")
        html = _get_editor(client, did)
        assert f'id="card-{iid}"' in html

    def test_card_has_data_id(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Salad")
        html = _get_editor(client, did)
        assert f'data-id="{iid}"' in html

    def test_card_data_cat_is_lowercased(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Tacos", category="Mexican Food")
        html = _get_editor(client, did)
        assert 'data-cat="mexican food"' in html

    def test_card_displays_item_name(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Cheeseburger")
        html = _get_editor(client, did)
        assert "Cheeseburger" in html
        assert "card-name-display" in html

    def test_card_displays_price(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Wings", price=1299)
        html = _get_editor(client, did)
        assert "12.99" in html
        assert "card-price-display" in html

    def test_card_has_badges_container(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did)
        html = _get_editor(client, did)
        assert "card-badges" in html

    def test_item_with_ungrouped_variant_shows_variant_badge(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Soda")
        _insert_variant(fresh_db, iid, "Small", 199)
        html = _get_editor(client, did)
        assert "card-badge-variant" in html

    def test_item_without_variants_has_no_variant_badge(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Water")
        html = _get_editor(client, did)
        # Use the rendered attribute pattern (CSS selectors appear without quotes)
        assert 'class="card-badge card-badge-variant"' not in html

    def test_item_with_modifier_group_shows_mg_badge(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger")
        _insert_group(fresh_db, iid, "Toppings")
        html = _get_editor(client, did)
        assert "card-badge-mg" in html

    def test_item_without_modifier_group_has_no_mg_badge(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Fries")
        html = _get_editor(client, did)
        assert 'class="card-badge card-badge-mg"' not in html

    def test_low_confidence_item_card_has_is_low_class(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        # Insert item with low quality signals: no price, no category
        iid = fresh_db.execute(
            "INSERT INTO draft_items "
            "(draft_id, name, price_cents, category, confidence, created_at, updated_at) "
            "VALUES (?, ?, 0, NULL, 20, ?, ?)",
            (did, "??", _NOW, _NOW),
        ).lastrowid
        fresh_db.commit()
        html = _get_editor(client, did)
        assert f'id="card-{iid}"' in html
        # The card for this item should have is-low (rendered by template when low_confidence=True)
        # We check that the is-low class appears somewhere in card-view
        assert "is-low" in html

    def test_item_with_description_shows_card_desc(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Soup", description="Hot and delicious broth")
        html = _get_editor(client, did)
        assert "card-desc" in html
        assert "Hot and delicious broth" in html


# ---------------------------------------------------------------------------
# Class 3: Modifier Groups Inside Card
# ---------------------------------------------------------------------------

class TestCardModifierGroups:
    """Modifier groups are rendered inside the card template."""

    def test_card_with_group_has_card_groups_element(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza")
        _insert_group(fresh_db, iid, "Crust")
        html = _get_editor(client, did)
        assert "card-groups" in html

    def test_each_group_renders_card_group_div(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza")
        _insert_group(fresh_db, iid, "Crust")
        _insert_group(fresh_db, iid, "Sauce")
        html = _get_editor(client, did)
        # The rendered div uses class="card-group" (with closing ">")
        assert html.count('<div class="card-group">') == 2

    def test_group_name_in_card_group_name(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger")
        _insert_group(fresh_db, iid, "Toppings")
        html = _get_editor(client, did)
        assert "Toppings" in html
        assert "card-group-name" in html

    def test_modifier_label_in_card_modifiers(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger")
        gid = _insert_group(fresh_db, iid, "Toppings")
        _insert_variant(fresh_db, iid, "Bacon", 100, group_id=gid)
        html = _get_editor(client, did)
        assert "card-modifiers" in html
        assert "Bacon" in html

    def test_modifier_price_in_card_mod_price(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger")
        gid = _insert_group(fresh_db, iid, "Toppings")
        _insert_variant(fresh_db, iid, "Avocado", 200, group_id=gid)
        html = _get_editor(client, did)
        assert "card-mod-price" in html
        assert "2.00" in html

    def test_item_without_groups_has_no_card_groups(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Fries")
        html = _get_editor(client, did)
        # The rendered div: class="card-groups" — CSS selectors won't have this exact pattern
        assert 'class="card-groups"' not in html


# ---------------------------------------------------------------------------
# Class 4: Quick-Edit Inputs
# ---------------------------------------------------------------------------

class TestCardQuickEdit:
    """Quick-edit inputs are present for editing drafts, absent for locked drafts."""

    def test_editing_draft_has_card_quick_edit(self, client, fresh_db):
        _, did = _create_draft(fresh_db, status="editing")
        _insert_item(fresh_db, did, "Chicken")
        html = _get_editor(client, did)
        assert "card-quick-edit" in html

    def test_card_name_qe_has_correct_value(self, client, fresh_db):
        _, did = _create_draft(fresh_db, status="editing")
        iid = _insert_item(fresh_db, did, "Fried Chicken")
        html = _get_editor(client, did)
        assert "card-name-qe" in html
        assert "Fried Chicken" in html

    def test_card_price_qe_has_correct_value(self, client, fresh_db):
        _, did = _create_draft(fresh_db, status="editing")
        _insert_item(fresh_db, did, "Steak", price=2499)
        html = _get_editor(client, did)
        assert "card-price-qe" in html
        assert "24.99" in html

    def test_approved_draft_has_no_card_quick_edit(self, client, fresh_db):
        _, did = _create_draft(fresh_db, status="approved")
        _insert_item(fresh_db, did, "Lobster", price=5000)
        html = _get_editor(client, did)
        assert 'class="card-quick-edit"' not in html


# ---------------------------------------------------------------------------
# Class 5: Card View Toggle Attributes
# ---------------------------------------------------------------------------

class TestCardToggleAttributes:
    """aria-pressed and card-expand-btn attributes are correct."""

    def test_view_table_btn_aria_pressed_true(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        html = _get_editor(client, did)
        assert 'id="view-table-btn"' in html
        assert 'aria-pressed="true"' in html

    def test_view_cards_btn_aria_pressed_false(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        html = _get_editor(client, did)
        assert 'aria-pressed="false"' in html

    def test_card_expand_btn_present_for_item_with_groups(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger")
        _insert_group(fresh_db, iid, "Sauce")
        html = _get_editor(client, did)
        assert "card-expand-btn" in html

    def test_card_expand_btn_absent_for_item_without_groups(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Fries")
        html = _get_editor(client, did)
        assert 'class="card-expand-btn"' not in html

    def test_multiple_items_produce_multiple_cards(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        iid1 = _insert_item(fresh_db, did, "Item A")
        iid2 = _insert_item(fresh_db, did, "Item B")
        html = _get_editor(client, did)
        assert f'id="card-{iid1}"' in html
        assert f'id="card-{iid2}"' in html

    def test_card_count_matches_item_count(self, client, fresh_db):
        _, did = _create_draft(fresh_db)
        for i in range(5):
            _insert_item(fresh_db, did, f"Item {i}")
        html = _get_editor(client, did)
        # Each card opens with <div class="item-card..." — count the tag opening
        assert html.count('<div class="item-card') == 5

    def test_card_edit_btn_present_for_editing_draft(self, client, fresh_db):
        _, did = _create_draft(fresh_db, status="editing")
        _insert_item(fresh_db, did, "Pasta")
        html = _get_editor(client, did)
        assert "card-edit-btn" in html

    def test_card_edit_btn_absent_for_locked_draft(self, client, fresh_db):
        _, did = _create_draft(fresh_db, status="approved")
        _insert_item(fresh_db, did, "Pasta")
        html = _get_editor(client, did)
        assert 'class="card-edit-btn btn"' not in html
