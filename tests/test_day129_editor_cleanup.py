# tests/test_day129_editor_cleanup.py
"""
Day 129 — Sprint 13.1: Customer-Facing Editor Cleanup.

Deliverables:
  1. Header info chips (Draft ID, Restaurant ID, Source, Import Job) hidden for customers
  2. Pipeline Debug, Clean & Refine, Finalize with AI buttons hidden for customers
  3. Confidence threshold slider hidden for customers
  4. Auto Kitchen Names button hidden for customers
  5. Position column (header + cells) hidden for customers
  6. Confidence badges + provenance pins hidden for customers
  7. Low Confidence panel hidden for customers
  8. Sidebar dev tools (Backfill, OCR Debug, Pipeline Debug, Back to Import) consolidated + hidden
  9. OCR Debug CSV hidden from export dropdown for customers
  10. Admin sees everything (no regressions)
  11. Customer retains: search, table/cards toggle, kitchen names toggle, add item,
      bulk category, delete selected, export dropdown, save, approve & export
  12. Customer UX: modifier coverage hidden, Back to Drafts hidden, Categories header,
      Add Category button, search bar in sidebar, prominent Add Item button

41 tests across 10 classes:
  1. Customer header — dev chips hidden (4)
  2. Customer toolbar — dev buttons hidden (4)
  3. Customer sidebar — dev tools hidden (4)
  4. Customer table — position + confidence hidden (4)
  5. Customer export — OCR debug hidden (4)
  6. Admin header — everything visible (4)
  7. Admin sidebar + toolbar — everything visible (4)
  8. Customer retains — core editor features present (4)
  9. Customer UX — sidebar improvements (5)
  10. Customer UX — toolbar layout (4)
"""

from __future__ import annotations

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS restaurants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT,
    address TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    cuisine_type TEXT,
    website TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash   TEXT NOT NULL,
    display_name    TEXT,
    role            TEXT DEFAULT 'customer',
    email_verified  INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1,
    account_tier    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_restaurants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    restaurant_id   INTEGER NOT NULL,
    role            TEXT NOT NULL DEFAULT 'owner',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE CASCADE,
    UNIQUE(user_id, restaurant_id)
);

CREATE TABLE IF NOT EXISTS email_verification_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TEXT NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    restaurant_id INTEGER REFERENCES restaurants(id),
    source_job_id INTEGER,
    title TEXT DEFAULT '',
    status TEXT DEFAULT 'editing',
    source TEXT,
    source_file_path TEXT,
    menu_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    name TEXT,
    description TEXT,
    price_cents INTEGER DEFAULT 0,
    category TEXT,
    position INTEGER DEFAULT 0,
    confidence INTEGER,
    quality INTEGER,
    kitchen_name TEXT,
    low_confidence INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_item_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES draft_items(id) ON DELETE CASCADE,
    label TEXT,
    price_cents INTEGER DEFAULT 0,
    kind TEXT DEFAULT 'size',
    position INTEGER DEFAULT 0,
    modifier_group_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_modifier_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    required INTEGER DEFAULT 0,
    min_select INTEGER DEFAULT 0,
    max_select INTEGER DEFAULT 0,
    position INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (item_id) REFERENCES draft_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS draft_modifier_group_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER,
    name TEXT NOT NULL,
    required INTEGER DEFAULT 0,
    min_select INTEGER DEFAULT 0,
    max_select INTEGER DEFAULT 0,
    position INTEGER DEFAULT 0,
    modifiers TEXT NOT NULL DEFAULT '[]',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_category_order (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    position INTEGER DEFAULT 0,
    UNIQUE(draft_id, category)
);

CREATE TABLE IF NOT EXISTS menus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER NOT NULL REFERENCES restaurants(id),
    name TEXT NOT NULL,
    menu_type TEXT,
    description TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER,
    filename TEXT,
    status TEXT DEFAULT 'pending',
    error TEXT,
    draft_id INTEGER,
    draft_path TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_export_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    format TEXT,
    item_count INTEGER DEFAULT 0,
    variant_count INTEGER DEFAULT 0,
    warning_count INTEGER DEFAULT 0,
    exported_at TEXT DEFAULT (datetime('now'))
);
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"

    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    conn = _connect()
    conn.executescript(_SCHEMA_SQL)
    # Seed restaurant
    conn.execute("INSERT INTO restaurants (id, name, phone, address) VALUES (1, 'Test Bistro', '555-1234', '123 Main St')")
    # Seed draft with items (including low-confidence + source_job_id for full coverage)
    conn.execute("""
        INSERT INTO drafts (id, restaurant_id, title, status, source_job_id, source)
        VALUES (10, 1, 'Test Draft', 'editing', 5, 'upload.pdf')
    """)
    conn.execute("""
        INSERT INTO draft_items (draft_id, name, description, price_cents, category, position, confidence, quality, low_confidence)
        VALUES (10, 'Burger', 'Juicy beef', 999, 'Entrees', 1, 92, 85, 0)
    """)
    conn.execute("""
        INSERT INTO draft_items (draft_id, name, description, price_cents, category, position, confidence, quality, low_confidence)
        VALUES (10, 'Mystery Item', '', 0, '', 2, 30, 20, 1)
    """)
    conn.execute("INSERT INTO menus (id, restaurant_id, name, menu_type) VALUES (100, 1, 'Lunch Menu', 'lunch')")
    conn.commit()
    conn.close()

    import storage.drafts as _drafts
    import storage.users as _users
    import storage.menus as _menus
    monkeypatch.setattr(_drafts, "db_connect", _connect)
    monkeypatch.setattr(_users, "db_connect", _connect)
    monkeypatch.setattr(_menus, "db_connect", _connect)
    return _connect


@pytest.fixture()
def app_client(mock_db, monkeypatch):
    import portal.app as _app
    import storage.users as _users
    monkeypatch.setattr(_app, "db_connect", mock_db)
    monkeypatch.setattr(_app, "users_store", _users)
    _app.app.config["TESTING"] = True
    _app.app.config["WTF_CSRF_ENABLED"] = False
    return _app.app.test_client()


def _register_customer(client, email="cust@example.com", password="securepass1"):
    """Register a customer and log in."""
    client.post("/register", data={
        "email": email,
        "password": password,
        "confirm_password": password,
        "display_name": "Test Customer",
    })
    return client


def _admin_login(client):
    """Log in as the legacy dev admin (show_admin=True)."""
    client.post("/login", data={"username": "admin", "password": "letmein"})
    return client


def _link_customer(mock_db, email, restaurant_id):
    """Link registered customer to restaurant."""
    import storage.users as _users
    user = _users.get_user_by_email(email)
    _users.link_user_restaurant(user["id"], restaurant_id, role="owner")


def _get_editor_html(client, draft_id=10):
    """GET the draft editor page and return HTML string."""
    resp = client.get(f"/drafts/{draft_id}/edit")
    return resp.data.decode("utf-8")


def _strip_style_script(html):
    """Remove <style>...</style> and <script>...</script> blocks to test only visible HTML."""
    import re
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    return html


# ===========================================================================
# 1. Customer header — dev chips hidden (4 tests)
# ===========================================================================
class TestCustomerHeaderChipsHidden:
    """Customer should NOT see Draft ID, Restaurant ID, Source, Import Job chips."""

    def test_draft_id_chip_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert '<span class="label">Draft #</span>' not in html

    def test_restaurant_id_chip_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert '<span class="label">Restaurant</span>' not in html

    def test_source_chip_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert '<span class="label">Source</span>' not in html

    def test_import_job_chip_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert '<span class="label">Import Job</span>' not in html


# ===========================================================================
# 2. Customer toolbar — dev buttons hidden (4 tests)
# ===========================================================================
class TestCustomerToolbarHidden:
    """Customer should NOT see Pipeline Debug, Clean & Refine, Finalize AI, confidence slider."""

    def test_pipeline_debug_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert '>Pipeline Debug</a>' not in html

    def test_clean_refine_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'id="refine-form"' not in html

    def test_finalize_ai_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'id="finalize-ai-form"' not in html

    def test_confidence_slider_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'class="confidence-filter"' not in html


# ===========================================================================
# 3. Customer sidebar — dev tools hidden (4 tests)
# ===========================================================================
class TestCustomerSidebarHidden:
    """Customer should NOT see Backfill Variants, OCR Debug, Pipeline Debug, Back to Import in sidebar."""

    def test_backfill_variants_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'backfill-variants-btn' not in html

    def test_ocr_debug_json_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'View OCR Debug (JSON)' not in html

    def test_ocr_debug_csv_sidebar_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'Download OCR Debug (CSV)' not in html

    def test_dev_tools_section_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'Dev Tools' not in html


# ===========================================================================
# 4. Customer table — position + confidence hidden (4 tests)
# ===========================================================================
class TestCustomerTableHidden:
    """Customer should NOT see Position column header, position cells, confidence badges, provenance pins."""

    def test_position_header_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert '>Position</th>' not in html

    def test_position_input_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'class="cell-input cell-number position"' not in html

    def test_confidence_badges_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'class="conf-badge' not in html

    def test_provenance_pin_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'class="prov-pin"' not in html


# ===========================================================================
# 5. Customer export — OCR debug hidden (4 tests)
# ===========================================================================
class TestCustomerExportHidden:
    """Customer should NOT see OCR Debug CSV in export dropdown. Standard exports remain."""

    def test_ocr_debug_csv_export_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'OCR Debug (CSV)' not in html

    def test_auto_kitchen_btn_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'id="auto-kitchen-btn"' not in html

    def test_low_confidence_panel_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'id="low-conf-panel"' not in html

    def test_quality_meta_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'class="quality-meta"' not in html


# ===========================================================================
# 6. Admin header — everything visible (4 tests)
# ===========================================================================
class TestAdminHeaderVisible:
    """Admin should see all dev chips and buttons."""

    def test_draft_id_chip_visible(self, app_client, mock_db):
        _admin_login(app_client)
        html = _get_editor_html(app_client)
        assert '<span class="label">Draft #</span>' in html

    def test_source_chip_visible(self, app_client, mock_db):
        _admin_login(app_client)
        html = _get_editor_html(app_client)
        assert '<span class="label">Source</span>' in html

    def test_pipeline_debug_visible(self, app_client, mock_db):
        _admin_login(app_client)
        html = _get_editor_html(app_client)
        assert '>Pipeline Debug<' in html

    def test_finalize_ai_visible(self, app_client, mock_db):
        _admin_login(app_client)
        html = _get_editor_html(app_client)
        assert 'Finalize with AI Cleanup' in html


# ===========================================================================
# 7. Admin sidebar + toolbar — everything visible (4 tests)
# ===========================================================================
class TestAdminSidebarToolbarVisible:
    """Admin should see all dev tools in sidebar and toolbar."""

    def test_dev_tools_section_visible(self, app_client, mock_db):
        _admin_login(app_client)
        html = _get_editor_html(app_client)
        assert 'Dev Tools' in html

    def test_backfill_variants_visible(self, app_client, mock_db):
        _admin_login(app_client)
        html = _get_editor_html(app_client)
        assert 'backfill-variants-btn' in html

    def test_confidence_slider_visible(self, app_client, mock_db):
        _admin_login(app_client)
        html = _get_editor_html(app_client)
        assert 'confidence-threshold' in html

    def test_position_column_visible(self, app_client, mock_db):
        _admin_login(app_client)
        html = _get_editor_html(app_client)
        assert '>Position</th>' in html


# ===========================================================================
# 8. Customer retains — core editor features present (4 tests)
# ===========================================================================
class TestCustomerRetainsCoreFeatures:
    """Customer should still have search, add item, save, approve & export, export dropdown."""

    def test_search_present(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _get_editor_html(app_client)
        assert 'id="search"' in html

    def test_add_item_present(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _get_editor_html(app_client)
        assert 'add-row-btn' in html

    def test_save_button_present(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _get_editor_html(app_client)
        assert 'save-btn' in html

    def test_export_dropdown_present(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _get_editor_html(app_client)
        assert 'Export' in html
        assert 'CSV (flat rows)' in html


# ===========================================================================
# 9. Customer UX — sidebar improvements (4 tests)
# ===========================================================================
class TestCustomerSidebarUX:
    """Customer sidebar: Categories header, Add Category button, search in sidebar."""

    def test_categories_header_shown(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'Categories' in html

    def test_add_category_button_shown(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'add-category-btn' in html

    def test_search_in_sidebar(self, app_client, mock_db):
        """Customer search bar should be in the sidebar, not in the toolbar."""
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _get_editor_html(app_client)
        assert 'sidebar-search-wrap' in html

    def test_admin_search_stays_in_toolbar(self, app_client, mock_db):
        """Admin search bar should remain in the toolbar (no sidebar search)."""
        _admin_login(app_client)
        html = _get_editor_html(app_client)
        assert 'sidebar-search-wrap' not in html

    def test_category_rename_pencil_wired(self, app_client, mock_db):
        """The inline rename pencil icon should be wired via JS (cat-edit-btn)."""
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _get_editor_html(app_client)
        assert 'cat-edit-btn' in html
        assert '_renameCategoryInline' in html


# ===========================================================================
# 10. Customer UX — toolbar layout (4 tests)
# ===========================================================================
class TestCustomerToolbarUX:
    """Customer toolbar: prominent Add Item, no modifier coverage, no Back to Drafts."""

    def test_modifier_coverage_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'modifier coverage' not in html

    def test_back_to_drafts_hidden(self, app_client, mock_db):
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'Back to Drafts' not in html

    def test_add_item_btn_prominent(self, app_client, mock_db):
        """Customer Add Item button should be styled as btn-success (green, prominent)."""
        _register_customer(app_client)
        _link_customer(mock_db, "cust@example.com", 1)
        html = _strip_style_script(_get_editor_html(app_client))
        assert 'btn btn-success' in html
        assert '+ Add Item' in html

    def test_admin_back_to_drafts_visible(self, app_client, mock_db):
        """Admin should still see Back to Drafts link."""
        _admin_login(app_client)
        html = _get_editor_html(app_client)
        assert 'Back to Drafts' in html
