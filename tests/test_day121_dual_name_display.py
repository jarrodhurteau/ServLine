"""
Day 121 — Dual Name Display
============================
Tests for:
  - Card view: kitchen name rendered as .card-kitchen-name subtitle
  - Card view: kitchen name NOT rendered when empty/null
  - Card quick-edit: .card-kitchen-qe input present in editing mode
  - Card quick-edit: kitchen name value pre-filled from item
  - Toggle button: #toggle-kitchen-names present in toolbar
  - Toggle button: aria-pressed attribute defaults to "true"
  - CSS: .card-kitchen-name style defined in template
  - CSS: .kitchen-names-hidden hides .card-kitchen-name
  - CSS: .kitchen-names-hidden hides .kitchen-name-row (table view)
  - CSS: .kitchen-names-hidden hides .card-kitchen-qe
  - Day 121 JS block: KN_STORAGE_KEY defined
  - Day 121 JS block: _setKitchenNamesVisible function defined
  - Day 121 JS block: card-kitchen-qe sync wiring
  - Dynamic card template: card-kitchen-qe input present
  - Non-editing mode: card-kitchen-qe NOT present
  - Non-editing mode: kitchen name subtitle still shown
  - Search includes kitchen name in card filter

Test plan (~32 tests):
  Class 1: Card kitchen name subtitle rendering (6 tests)
    1.  Item with kitchen_name → .card-kitchen-name element rendered
    2.  Item without kitchen_name → no .card-kitchen-name element
    3.  Kitchen name subtitle text matches item's kitchen_name value
    4.  Multiple items: only items with kitchen_name get subtitle
    5.  Kitchen name with special chars is HTML-escaped
    6.  Kitchen name subtitle appears between card-header and card-category-label

  Class 2: Card kitchen name quick-edit (6 tests)
    7.  .card-kitchen-qe input present in editing mode
    8.  .card-kitchen-qe NOT present in approved mode
    9.  .card-kitchen-qe value pre-filled from item's kitchen_name
    10. .card-kitchen-qe has placeholder="Kitchen name"
    11. .card-kitchen-qe has aria-label="Kitchen name"
    12. .card-kitchen-qe has data-card-id matching item id

  Class 3: Toggle button (6 tests)
    13. #toggle-kitchen-names button exists in toolbar
    14. Toggle button has aria-pressed="true" by default
    15. Toggle button has title="Show/hide kitchen names"
    16. Toggle button text is "Kitchen Names"
    17. Toggle button appears after view-toggle div
    18. Toggle button has class="btn"

  Class 4: CSS rules (6 tests)
    19. .card-kitchen-name style defined in template
    20. .card-kitchen-name color is var(--muted)
    21. .kitchen-names-hidden .card-kitchen-name { display: none }
    22. .kitchen-names-hidden .card-kitchen-qe { display: none }
    23. .kitchen-names-hidden .kitchen-name-row { display: none }
    24. .card-kitchen-qe style defined (flex: 1, font-size .8rem)

  Class 5: JavaScript wiring (5 tests)
    25. KN_STORAGE_KEY defined as "servline_kitchen_names_visible"
    26. _setKitchenNamesVisible function defined
    27. card-kitchen-qe sync wiring block present
    28. Day 121 comment marker in JS
    29. localStorage.getItem(KN_STORAGE_KEY) restore logic present

  Class 6: Dynamic card template (3 tests)
    30. Dynamic card innerHTML includes card-kitchen-qe input
    31. Dynamic card kitchen sync block present (_createDynamicCard)
    32. Dynamic card kitchen-qe has placeholder="Kitchen name"
"""

from __future__ import annotations

import re
import sqlite3

import pytest

import storage.drafts as drafts_mod


# ---------------------------------------------------------------------------
# Schema (matches Day 120)
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

_NOW = "2026-03-14T10:00:00"


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


def _create_item(conn, draft_id, name="Burger", kitchen_name=None, price_cents=999) -> int:
    iid = conn.execute(
        "INSERT INTO draft_items (draft_id, name, price_cents, kitchen_name, position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 0, ?, ?)",
        (draft_id, name, price_cents, kitchen_name, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return iid


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


def _get_editor_html(client, draft_id) -> str:
    """Fetch the draft editor page HTML."""
    resp = client.get(f"/drafts/{draft_id}/edit")
    assert resp.status_code == 200
    return resp.data.decode("utf-8")


# ---------------------------------------------------------------------------
# Helper: read template source (for CSS/JS inspection)
# ---------------------------------------------------------------------------

def _read_template_source() -> str:
    import os
    tpl_path = os.path.join(os.path.dirname(__file__), "..", "portal", "templates", "draft_editor.html")
    with open(tpl_path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Class 1: Card kitchen name subtitle rendering (tests 1–6)
# ---------------------------------------------------------------------------

class TestCardKitchenNameSubtitle:
    """Kitchen name subtitle in card view."""

    def test_kitchen_name_rendered_when_present(self, app_ctx):
        """1. Item with kitchen_name → .card-kitchen-name element rendered."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Cheeseburger", kitchen_name="CHZBRGR")
        html = _get_editor_html(client, did)
        assert "card-kitchen-name" in html
        assert "CHZBRGR" in html

    def test_no_kitchen_name_no_subtitle(self, app_ctx):
        """2. Item without kitchen_name → no .card-kitchen-name element."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Fries", kitchen_name=None)
        html = _get_editor_html(client, did)
        # There should be no card-kitchen-name div for this item
        # (The class may appear in CSS/JS definitions, so check for the div specifically)
        assert '<div class="card-kitchen-name">' not in html

    def test_kitchen_name_text_matches(self, app_ctx):
        """3. Subtitle text matches item's kitchen_name value."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Grilled Chicken Wrap", kitchen_name="GRL CHKN WRP")
        html = _get_editor_html(client, did)
        assert '>GRL CHKN WRP</div>' in html or 'GRL CHKN WRP</div>' in html

    def test_multiple_items_selective_subtitle(self, app_ctx):
        """4. Only items with kitchen_name get the subtitle."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Burger", kitchen_name="BRGR")
        _create_item(conn, did, name="Fries", kitchen_name=None)
        _create_item(conn, did, name="Shake", kitchen_name="SHK")
        html = _get_editor_html(client, did)
        # Count occurrences of card-kitchen-name div (rendered ones only)
        rendered = re.findall(r'<div class="card-kitchen-name">', html)
        assert len(rendered) == 2

    def test_kitchen_name_html_escaped(self, app_ctx):
        """5. Kitchen name with special chars is HTML-escaped."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Test", kitchen_name="<script>alert(1)</script>")
        html = _get_editor_html(client, did)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_kitchen_name_before_category(self, app_ctx):
        """6. Kitchen name subtitle appears between header and category label."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, name="Burger", kitchen_name="BRGR")
        conn.execute("UPDATE draft_items SET category='Entrees' WHERE id=?", (iid,))
        conn.commit()
        html = _get_editor_html(client, did)
        # Find the card div for this item, then check order within it
        card_start = html.find(f'id="card-{iid}"')
        assert card_start > 0
        card_html = html[card_start:card_start + 2000]
        kn_pos = card_html.find('card-kitchen-name')
        cat_pos = card_html.find('card-category-label')
        assert kn_pos > 0
        assert cat_pos > 0
        assert kn_pos < cat_pos


# ---------------------------------------------------------------------------
# Class 2: Card kitchen name quick-edit (tests 7–12)
# ---------------------------------------------------------------------------

class TestCardKitchenQuickEdit:
    """Kitchen name quick-edit input in card view."""

    def test_kitchen_qe_present_editing(self, app_ctx):
        """7. .card-kitchen-qe input present in editing mode."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn, status="editing")
        _create_item(conn, did, name="Burger", kitchen_name="BRGR")
        html = _get_editor_html(client, did)
        assert "card-kitchen-qe" in html

    def test_kitchen_qe_absent_approved(self, app_ctx):
        """8. .card-kitchen-qe NOT present when draft is approved."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn, status="approved")
        _create_item(conn, did, name="Burger", kitchen_name="BRGR")
        html = _get_editor_html(client, did)
        # The class name appears in CSS, but no actual input element should be rendered
        input_matches = re.findall(r'<input[^>]*card-kitchen-qe[^>]*>', html)
        assert len(input_matches) == 0

    def test_kitchen_qe_prefilled(self, app_ctx):
        """9. .card-kitchen-qe value pre-filled from item's kitchen_name."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Burger", kitchen_name="BRGR")
        html = _get_editor_html(client, did)
        # Find the input with the prefilled value
        match = re.search(r'<input[^>]*card-kitchen-qe[^>]*value="([^"]*)"', html)
        assert match is not None
        assert match.group(1) == "BRGR"

    def test_kitchen_qe_placeholder(self, app_ctx):
        """10. .card-kitchen-qe has placeholder='Kitchen name'."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Burger")
        html = _get_editor_html(client, did)
        match = re.search(r'<input[^>]*card-kitchen-qe[^>]*placeholder="([^"]*)"', html)
        assert match is not None
        assert match.group(1) == "Kitchen name"

    def test_kitchen_qe_aria_label(self, app_ctx):
        """11. .card-kitchen-qe has aria-label='Kitchen name'."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Burger")
        html = _get_editor_html(client, did)
        match = re.search(r'<input[^>]*card-kitchen-qe[^>]*aria-label="([^"]*)"', html)
        assert match is not None
        assert match.group(1) == "Kitchen name"

    def test_kitchen_qe_data_card_id(self, app_ctx):
        """12. .card-kitchen-qe has data-card-id matching item id."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, name="Burger")
        html = _get_editor_html(client, did)
        match = re.search(r'<input[^>]*card-kitchen-qe[^>]*data-card-id="(\d+)"', html)
        assert match is not None
        assert int(match.group(1)) == iid


# ---------------------------------------------------------------------------
# Class 3: Toggle button (tests 13–18)
# ---------------------------------------------------------------------------

class TestToggleButton:
    """Kitchen names show/hide toggle button."""

    def test_toggle_button_exists(self, app_ctx):
        """13. #toggle-kitchen-names button exists in toolbar."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Burger")
        html = _get_editor_html(client, did)
        assert 'id="toggle-kitchen-names"' in html

    def test_toggle_aria_pressed_true(self, app_ctx):
        """14. Toggle button has aria-pressed='true' by default."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Burger")
        html = _get_editor_html(client, did)
        match = re.search(r'id="toggle-kitchen-names"[^>]*aria-pressed="([^"]*)"', html)
        assert match is not None
        assert match.group(1) == "true"

    def test_toggle_title(self, app_ctx):
        """15. Toggle button has title='Show/hide kitchen names'."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Burger")
        html = _get_editor_html(client, did)
        assert 'title="Show/hide kitchen names"' in html

    def test_toggle_text(self, app_ctx):
        """16. Toggle button text is 'Kitchen Names'."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Burger")
        html = _get_editor_html(client, did)
        assert ">Kitchen Names</button>" in html

    def test_toggle_after_view_toggle(self, app_ctx):
        """17. Toggle button appears after the view-toggle div."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Burger")
        html = _get_editor_html(client, did)
        vt_pos = html.find('id="view-toggle"')
        kn_pos = html.find('id="toggle-kitchen-names"')
        assert vt_pos > 0
        assert kn_pos > 0
        assert kn_pos > vt_pos

    def test_toggle_has_btn_class(self, app_ctx):
        """18. Toggle button has class='btn'."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, name="Burger")
        html = _get_editor_html(client, did)
        match = re.search(r'id="toggle-kitchen-names"[^>]*class="([^"]*)"', html)
        assert match is not None
        assert "btn" in match.group(1)


# ---------------------------------------------------------------------------
# Class 4: CSS rules (tests 19–24)
# ---------------------------------------------------------------------------

class TestCSSRules:
    """CSS styles for kitchen name display and toggle."""

    def test_card_kitchen_name_style_defined(self):
        """19. .card-kitchen-name style defined in template."""
        src = _read_template_source()
        assert ".card-kitchen-name" in src

    def test_card_kitchen_name_color_muted(self):
        """20. .card-kitchen-name uses muted color."""
        src = _read_template_source()
        # Find the .card-kitchen-name CSS block
        match = re.search(r'\.card-kitchen-name\s*\{([^}]+)\}', src)
        assert match is not None
        assert "var(--muted)" in match.group(1)

    def test_hidden_class_hides_subtitle(self):
        """21. .kitchen-names-hidden .card-kitchen-name { display: none }."""
        src = _read_template_source()
        assert re.search(
            r'\.kitchen-names-hidden\s+\.card-kitchen-name\s*\{[^}]*display:\s*none',
            src
        )

    def test_hidden_class_hides_qe(self):
        """22. .kitchen-names-hidden .card-kitchen-qe { display: none }."""
        src = _read_template_source()
        assert re.search(
            r'\.kitchen-names-hidden\s+\.card-kitchen-qe\s*\{[^}]*display:\s*none',
            src
        )

    def test_hidden_class_hides_table_row(self):
        """23. .kitchen-names-hidden .kitchen-name-row { display: none }."""
        src = _read_template_source()
        assert re.search(
            r'\.kitchen-names-hidden\s+\.kitchen-name-row\s*\{[^}]*display:\s*none',
            src
        )

    def test_card_kitchen_qe_style(self):
        """24. .card-kitchen-qe style defined with font-size."""
        src = _read_template_source()
        match = re.search(r'\.card-kitchen-qe\s*\{([^}]+)\}', src)
        assert match is not None
        assert "font-size" in match.group(1)


# ---------------------------------------------------------------------------
# Class 5: JavaScript wiring (tests 25–29)
# ---------------------------------------------------------------------------

class TestJavaScriptWiring:
    """Day 121 JS block for kitchen name toggle and sync."""

    def test_kn_storage_key_defined(self):
        """25. KN_STORAGE_KEY defined as 'servline_kitchen_names_visible'."""
        src = _read_template_source()
        assert 'KN_STORAGE_KEY' in src
        assert '"servline_kitchen_names_visible"' in src

    def test_set_kitchen_names_visible_defined(self):
        """26. _setKitchenNamesVisible function defined."""
        src = _read_template_source()
        assert '_setKitchenNamesVisible' in src

    def test_kitchen_qe_sync_wiring(self):
        """27. card-kitchen-qe sync wiring block present."""
        src = _read_template_source()
        assert 'card-kitchen-qe' in src
        # Check that there's an addEventListener for the kitchen qe inputs
        assert re.search(r'\.card-kitchen-qe.*addEventListener', src, re.DOTALL)

    def test_day121_comment_marker(self):
        """28. Day 121 comment marker in JS."""
        src = _read_template_source()
        assert "Day 121" in src

    def test_localstorage_restore(self):
        """29. localStorage restore logic for kitchen names toggle."""
        src = _read_template_source()
        assert re.search(r'localStorage\.getItem\(KN_STORAGE_KEY\)', src)


# ---------------------------------------------------------------------------
# Class 6: Dynamic card template (tests 30–32)
# ---------------------------------------------------------------------------

class TestDynamicCardTemplate:
    """Dynamic card creation includes kitchen name elements."""

    def test_dynamic_card_has_kitchen_qe(self):
        """30. Dynamic card innerHTML includes card-kitchen-qe input."""
        src = _read_template_source()
        # Find _createDynamicCard function body and check for card-kitchen-qe
        match = re.search(r'function _createDynamicCard\(tr\)\s*\{(.*?)\n    \}', src, re.DOTALL)
        assert match is not None
        assert 'card-kitchen-qe' in match.group(1)

    def test_dynamic_card_kitchen_sync(self):
        """31. Dynamic card kitchen sync block present in _createDynamicCard."""
        src = _read_template_source()
        # Check for kitchen name sync in dynamic card section
        assert re.search(r'kitchenQe.*addEventListener', src, re.DOTALL)

    def test_dynamic_card_kitchen_qe_placeholder(self):
        """32. Dynamic card kitchen-qe has placeholder='Kitchen name'."""
        src = _read_template_source()
        # Find _createDynamicCard function body
        match = re.search(r'function _createDynamicCard\(tr\)\s*\{(.*?)\n    \}', src, re.DOTALL)
        assert match is not None
        body = match.group(1)
        assert 'card-kitchen-qe' in body
        assert 'placeholder="Kitchen name"' in body
