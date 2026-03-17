"""
Day 122 — Bulk Card Actions & Editor Stats Bar
================================================
Tests for:
  - _compute_editor_stats() helper (item count, categories, mg coverage, price range)
  - GET /drafts/<id>/stats endpoint
  - POST /drafts/<id>/bulk_delete endpoint
  - POST /drafts/<id>/bulk_move_category endpoint
  - Template: #editor-stats-bar rendered with stats values
  - Template: .card-bulk-checkbox on cards (editing mode only)
  - Template: #bulk-card-toolbar auto-shows when cards selected
  - Template: #bulk-card-toolbar with action buttons
  - Template: empty-state-msg when draft has no items
  - CSS: Day 122 styles for stats bar, bulk mode, empty state
  - JS: Day 122 block with bulk mode functions

Test plan (~32 tests):
  Class 1: _compute_editor_stats() helper (6 tests)
    1.  Empty items list → all zeros
    2.  Items with categories → correct category_count
    3.  Items with modifier_groups → correct mg_coverage_pct
    4.  Items with prices → correct price_min/max
    5.  Mixed items: some with no price → excludes zeros from range
    6.  Duplicate categories counted once

  Class 2: GET /drafts/<id>/stats endpoint (4 tests)
    7.  Valid draft → 200 + stats object
    8.  Nonexistent draft → 404
    9.  Stats values match computed stats
    10. Unauthenticated → 302

  Class 3: POST /drafts/<id>/bulk_delete endpoint (6 tests)
    11. Valid item_ids → 200 + deleted count
    12. Items actually removed from DB
    13. Empty item_ids list → 200 + deleted=0
    14. Non-list item_ids → 400
    15. Approved draft → 403
    16. Nonexistent draft → 404

  Class 4: POST /drafts/<id>/bulk_move_category endpoint (6 tests)
    17. Valid move → 200 + updated count + category
    18. Items category updated in DB
    19. Missing category → 400
    20. Non-list item_ids → 400
    21. Approved draft → 403
    22. Empty item_ids → 200 + updated=0

  Class 5: Template — stats bar rendering (4 tests)
    23. #editor-stats-bar present when items exist
    24. Stats bar shows item count
    25. Stats bar shows category count
    26. Stats bar shows modifier coverage percentage

  Class 6: Template — bulk selection elements (4 tests)
    27. .card-bulk-checkbox present on cards in editing mode
    28. .card-bulk-checkbox NOT present in approved mode
    29. #bulk-card-toolbar hidden by default (no 'visible' class)
    30. #bulk-card-toolbar present in template

  Class 7: Template — empty state + CSS/JS (2 tests)
    31. Empty draft shows empty-state-msg
    32. Day 122 JS block present (bulk mode functions)
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest

import storage.drafts as drafts_mod


# ---------------------------------------------------------------------------
# Schema (matches Day 121)
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

_NOW = "2026-03-17T10:00:00"


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


def _create_item(conn, draft_id, name="Burger", price_cents=999, category=None) -> int:
    iid = conn.execute(
        "INSERT INTO draft_items (draft_id, name, price_cents, category, position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 0, ?, ?)",
        (draft_id, name, price_cents, category, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return iid


def _create_modifier_group(conn, item_id, name="Size") -> int:
    gid = conn.execute(
        "INSERT INTO draft_modifier_groups "
        "(item_id, name, required, min_select, max_select, position, created_at, updated_at) "
        "VALUES (?, ?, 0, 0, 0, 0, ?, ?)",
        (item_id, name, _NOW, _NOW),
    ).lastrowid
    conn.commit()
    return gid


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
    resp = client.get(f"/drafts/{draft_id}/edit")
    assert resp.status_code == 200
    return resp.data.decode("utf-8")


def _read_template_source() -> str:
    import os
    tpl_path = os.path.join(os.path.dirname(__file__), "..", "portal", "templates", "draft_editor.html")
    with open(tpl_path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Class 1: _compute_editor_stats() helper (tests 1–6)
# ---------------------------------------------------------------------------

class TestComputeEditorStats:
    """_compute_editor_stats helper function."""

    def test_empty_items(self):
        """1. Empty items list → all zeros."""
        import portal.app as _app_mod
        stats = _app_mod._compute_editor_stats([])
        assert stats["item_count"] == 0
        assert stats["category_count"] == 0
        assert stats["mg_coverage_pct"] == 0
        assert stats["price_min"] == 0
        assert stats["price_max"] == 0

    def test_category_count(self):
        """2. Items with categories → correct category_count."""
        import portal.app as _app_mod
        items = [
            {"name": "A", "category": "Appetizers", "price_cents": 500},
            {"name": "B", "category": "Entrees", "price_cents": 1200},
            {"name": "C", "category": "Appetizers", "price_cents": 700},
        ]
        stats = _app_mod._compute_editor_stats(items)
        assert stats["category_count"] == 2

    def test_mg_coverage(self):
        """3. Items with modifier_groups → correct mg_coverage_pct."""
        import portal.app as _app_mod
        items = [
            {"name": "A", "modifier_groups": [{"id": 1}], "price_cents": 500},
            {"name": "B", "modifier_groups": [], "price_cents": 700},
            {"name": "C", "modifier_groups": [{"id": 2}], "price_cents": 900},
            {"name": "D", "price_cents": 600},
        ]
        stats = _app_mod._compute_editor_stats(items)
        assert stats["mg_coverage_pct"] == 50  # 2 out of 4

    def test_price_range(self):
        """4. Items with prices → correct price_min/max."""
        import portal.app as _app_mod
        items = [
            {"name": "A", "price_cents": 450},
            {"name": "B", "price_cents": 2800},
            {"name": "C", "price_cents": 1200},
        ]
        stats = _app_mod._compute_editor_stats(items)
        assert stats["price_min"] == 450
        assert stats["price_max"] == 2800

    def test_zero_prices_excluded(self):
        """5. Items with zero price excluded from range."""
        import portal.app as _app_mod
        items = [
            {"name": "A", "price_cents": 0},
            {"name": "B", "price_cents": 500},
            {"name": "C", "price_cents": 1500},
        ]
        stats = _app_mod._compute_editor_stats(items)
        assert stats["price_min"] == 500
        assert stats["price_max"] == 1500

    def test_duplicate_categories_counted_once(self):
        """6. Duplicate categories counted once."""
        import portal.app as _app_mod
        items = [
            {"name": "A", "category": "Drinks", "price_cents": 300},
            {"name": "B", "category": "Drinks", "price_cents": 400},
            {"name": "C", "category": "Drinks", "price_cents": 500},
        ]
        stats = _app_mod._compute_editor_stats(items)
        assert stats["category_count"] == 1


# ---------------------------------------------------------------------------
# Class 2: GET /drafts/<id>/stats endpoint (tests 7–10)
# ---------------------------------------------------------------------------

class TestStatsEndpoint:
    """GET /drafts/<id>/stats returns live stats."""

    def test_stats_ok(self, app_ctx):
        """7. Valid draft → 200 + stats object."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, "Burger", price_cents=999, category="Entrees")
        resp = client.get(f"/drafts/{did}/stats")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert "stats" in data

    def test_stats_not_found(self, app_ctx):
        """8. Nonexistent draft → 404."""
        _, client, _ = app_ctx
        resp = client.get("/drafts/9999/stats")
        assert resp.status_code == 404

    def test_stats_values_correct(self, app_ctx):
        """9. Stats values match expected computation."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid1 = _create_item(conn, did, "Burger", price_cents=999, category="Entrees")
        _create_item(conn, did, "Fries", price_cents=499, category="Sides")
        _create_item(conn, did, "Soda", price_cents=299, category="Drinks")
        _create_modifier_group(conn, iid1, "Size")
        resp = client.get(f"/drafts/{did}/stats")
        data = json.loads(resp.data)
        s = data["stats"]
        assert s["item_count"] == 3
        assert s["category_count"] == 3
        assert s["price_min"] == 299
        assert s["price_max"] == 999

    def test_stats_unauthenticated(self, app_ctx):
        """10. Unauthenticated → 302."""
        import portal.app as _app_mod
        _, _, conn = app_ctx
        _, did = _create_draft(conn)
        with _app_mod.app.test_client() as anon:
            resp = anon.get(f"/drafts/{did}/stats")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Class 3: POST /drafts/<id>/bulk_delete (tests 11–16)
# ---------------------------------------------------------------------------

class TestBulkDelete:
    """POST /drafts/<id>/bulk_delete removes multiple items."""

    def test_bulk_delete_ok(self, app_ctx):
        """11. Valid item_ids → 200 + deleted count."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        a = _create_item(conn, did, "A")
        b = _create_item(conn, did, "B")
        resp = client.post(
            f"/drafts/{did}/bulk_delete",
            data=json.dumps({"item_ids": [a, b]}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["deleted"] == 2

    def test_bulk_delete_removes_from_db(self, app_ctx):
        """12. Items actually removed from DB."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        a = _create_item(conn, did, "A")
        b = _create_item(conn, did, "B")
        c = _create_item(conn, did, "C")
        client.post(
            f"/drafts/{did}/bulk_delete",
            data=json.dumps({"item_ids": [a, c]}),
            content_type="application/json",
        )
        remaining = conn.execute(
            "SELECT id FROM draft_items WHERE draft_id=?", (did,)
        ).fetchall()
        assert len(remaining) == 1
        assert remaining[0]["id"] == b

    def test_bulk_delete_empty_list(self, app_ctx):
        """13. Empty item_ids list → 200 + deleted=0."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, "A")
        resp = client.post(
            f"/drafts/{did}/bulk_delete",
            data=json.dumps({"item_ids": []}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["deleted"] == 0

    def test_bulk_delete_non_list(self, app_ctx):
        """14. Non-list item_ids → 400."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        resp = client.post(
            f"/drafts/{did}/bulk_delete",
            data=json.dumps({"item_ids": "not-a-list"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_bulk_delete_approved_draft(self, app_ctx):
        """15. Approved draft → 403."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn, status="approved")
        iid = _create_item(conn, did, "A")
        resp = client.post(
            f"/drafts/{did}/bulk_delete",
            data=json.dumps({"item_ids": [iid]}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_bulk_delete_not_found(self, app_ctx):
        """16. Nonexistent draft → 404."""
        _, client, _ = app_ctx
        resp = client.post(
            "/drafts/9999/bulk_delete",
            data=json.dumps({"item_ids": [1]}),
            content_type="application/json",
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Class 4: POST /drafts/<id>/bulk_move_category (tests 17–22)
# ---------------------------------------------------------------------------

class TestBulkMoveCategory:
    """POST /drafts/<id>/bulk_move_category updates categories."""

    def test_move_ok(self, app_ctx):
        """17. Valid move → 200 + updated count + category."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        a = _create_item(conn, did, "A", category="Old")
        b = _create_item(conn, did, "B", category="Old")
        resp = client.post(
            f"/drafts/{did}/bulk_move_category",
            data=json.dumps({"item_ids": [a, b], "category": "Appetizers"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["updated"] == 2
        assert data["category"] == "Appetizers"

    def test_move_updates_db(self, app_ctx):
        """18. Items category updated in DB."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        a = _create_item(conn, did, "A", category="Old")
        b = _create_item(conn, did, "B", category="Old")
        client.post(
            f"/drafts/{did}/bulk_move_category",
            data=json.dumps({"item_ids": [a, b], "category": "Entrees"}),
            content_type="application/json",
        )
        rows = conn.execute(
            "SELECT category FROM draft_items WHERE draft_id=? ORDER BY id", (did,)
        ).fetchall()
        assert all(r["category"] == "Entrees" for r in rows)

    def test_move_missing_category(self, app_ctx):
        """19. Missing category → 400."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        a = _create_item(conn, did, "A")
        resp = client.post(
            f"/drafts/{did}/bulk_move_category",
            data=json.dumps({"item_ids": [a]}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_move_non_list(self, app_ctx):
        """20. Non-list item_ids → 400."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        resp = client.post(
            f"/drafts/{did}/bulk_move_category",
            data=json.dumps({"item_ids": "bad", "category": "X"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_move_approved_draft(self, app_ctx):
        """21. Approved draft → 403."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn, status="approved")
        a = _create_item(conn, did, "A")
        resp = client.post(
            f"/drafts/{did}/bulk_move_category",
            data=json.dumps({"item_ids": [a], "category": "X"}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_move_empty_ids(self, app_ctx):
        """22. Empty item_ids → 200 + updated=0."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        resp = client.post(
            f"/drafts/{did}/bulk_move_category",
            data=json.dumps({"item_ids": [], "category": "X"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["updated"] == 0


# ---------------------------------------------------------------------------
# Class 5: Template — stats bar rendering (tests 23–26)
# ---------------------------------------------------------------------------

class TestStatsBarRendering:
    """Editor stats bar rendered in template."""

    def test_stats_bar_present(self, app_ctx):
        """23. #editor-stats-bar present when items exist."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, "Burger", price_cents=999, category="Entrees")
        html = _get_editor_html(client, did)
        assert 'id="editor-stats-bar"' in html

    def test_stats_bar_item_count(self, app_ctx):
        """24. Stats bar shows item count."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, "A", category="X")
        _create_item(conn, did, "B", category="X")
        _create_item(conn, did, "C", category="Y")
        html = _get_editor_html(client, did)
        # Find stats bar and check item count
        bar_match = re.search(r'id="editor-stats-bar"[^>]*>(.*?)</div>\s*</div>', html, re.DOTALL)
        assert bar_match is not None
        bar_html = bar_match.group(0)
        assert ">3</span>" in bar_html or ">3<" in bar_html

    def test_stats_bar_category_count(self, app_ctx):
        """25. Stats bar shows category count."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, "A", category="Entrees")
        _create_item(conn, did, "B", category="Sides")
        html = _get_editor_html(client, did)
        assert 'id="editor-stats-bar"' in html
        # Should show 2 categories
        bar_start = html.find('id="editor-stats-bar"')
        bar_chunk = html[bar_start:bar_start + 500]
        assert ">2</span>" in bar_chunk

    def test_stats_bar_mg_coverage(self, app_ctx):
        """26. Stats bar shows modifier coverage percentage."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        iid = _create_item(conn, did, "Pizza", category="Entrees")
        _create_item(conn, did, "Fries", category="Sides")
        _create_modifier_group(conn, iid, "Toppings")
        html = _get_editor_html(client, did)
        bar_start = html.find('id="editor-stats-bar"')
        bar_chunk = html[bar_start:bar_start + 500]
        assert "50%" in bar_chunk


# ---------------------------------------------------------------------------
# Class 6: Template — bulk selection elements (tests 27–30)
# ---------------------------------------------------------------------------

class TestBulkSelectionElements:
    """Bulk card selection UI elements."""

    def test_checkbox_present_editing(self, app_ctx):
        """27. .card-bulk-checkbox present on cards in editing mode."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn, status="editing")
        _create_item(conn, did, "Burger")
        html = _get_editor_html(client, did)
        assert "card-bulk-checkbox" in html
        inputs = re.findall(r'<input[^>]*card-bulk-checkbox[^>]*>', html)
        assert len(inputs) >= 1

    def test_checkbox_absent_approved(self, app_ctx):
        """28. .card-bulk-checkbox NOT rendered in approved mode."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn, status="approved")
        _create_item(conn, did, "Burger")
        html = _get_editor_html(client, did)
        inputs = re.findall(r'<input[^>]*card-bulk-checkbox[^>]*>', html)
        assert len(inputs) == 0

    def test_bulk_toolbar_hidden_by_default(self, app_ctx):
        """29. #bulk-card-toolbar hidden by default (no 'visible' class)."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn, status="editing")
        _create_item(conn, did, "Burger")
        html = _get_editor_html(client, did)
        # Toolbar is in DOM but not visible (no 'visible' class in server-rendered HTML)
        assert 'id="bulk-card-toolbar"' in html
        toolbar_match = re.search(r'id="bulk-card-toolbar"[^>]*class="([^"]*)"', html)
        if toolbar_match:
            assert "visible" not in toolbar_match.group(1)

    def test_bulk_card_toolbar_present(self, app_ctx):
        """30. #bulk-card-toolbar present in template."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        _create_item(conn, did, "Burger")
        html = _get_editor_html(client, did)
        assert 'id="bulk-card-toolbar"' in html


# ---------------------------------------------------------------------------
# Class 7: Template — empty state + CSS/JS (tests 31–32)
# ---------------------------------------------------------------------------

class TestEmptyStateAndJS:
    """Empty state messaging and Day 122 JS block."""

    def test_empty_draft_shows_message(self, app_ctx):
        """31. Empty draft shows empty-state-msg."""
        _, client, conn = app_ctx
        _, did = _create_draft(conn)
        # No items created
        html = _get_editor_html(client, did)
        assert "empty-state-msg" in html
        assert "No items in this draft yet" in html

    def test_day122_js_block_present(self):
        """32. Day 122 JS block present with bulk mode functions."""
        src = _read_template_source()
        assert "Day 122" in src
        assert "_updateBulkCount" in src
        assert "_clearBulkSelection" in src
        assert "bulk_delete" in src
        assert "bulk_move_category" in src
        assert "_refreshStatsBar" in src
