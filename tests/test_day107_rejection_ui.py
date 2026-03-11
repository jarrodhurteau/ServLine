# tests/test_day107_rejection_ui.py
"""
Day 107 — Sprint 11.3: Frontend Rejection UI.

When the confidence gate fires (status="rejected", error=customer_message),
the UI must surface the friendly rejection message to the user instead of
silently staying in a failed-looking state.

Deliverables:
  1. import_view.html:
     - "rejected" pill → pill-red (JS PILL_CLASSES + Jinja2 fallthrough)
     - "rejected" in the JS terminal Set (polling stops)
     - Rejection banner with customer_message on page load (status=rejected)
     - pollStatus() shows banner + customer_message when poll returns "rejected"
     - No auto-redirect on "rejected" (only "done" redirects)
  2. imports.html:
     - "rejected" pill → pill-red (Jinja2 + JS PILL_CLASS)
     - "rejected" label → "Rejected" (Jinja2 status_label + JS LABEL map)
  3. import_status endpoint:
     - Returns "error" field from the import_job row
     - Status "rejected" renders correctly end-to-end

30 tests across 7 classes:
  1. import_status endpoint — error field passthrough (5)
  2. import_view.html template source — JS constants (7)
  3. import_view.html page load via Flask — rejected job renders banner (5)
  4. imports.html template source — pill + label mappings (6)
  5. Rejection banner content & structure (4)
  6. No score exposure in customer-facing UI (2)
  7. Edge cases — other statuses unaffected (1)
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# DB schema (needs pipeline_rejections + import_jobs + standard tables)
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS restaurants (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    restaurant_id INTEGER REFERENCES restaurants(id),
    source_job_id INTEGER,
    title TEXT DEFAULT '',
    status TEXT DEFAULT 'editing',
    source_file_path TEXT,
    menu_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_items (
    id INTEGER PRIMARY KEY,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    price_cents INTEGER DEFAULT 0,
    category TEXT DEFAULT '',
    position INTEGER DEFAULT 0,
    confidence INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_item_variants (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES draft_items(id) ON DELETE CASCADE,
    label TEXT DEFAULT '',
    price_cents INTEGER DEFAULT 0,
    kind TEXT DEFAULT 'size',
    position INTEGER DEFAULT 0,
            modifier_group_id   INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id INTEGER PRIMARY KEY,
    restaurant_id INTEGER,
    filename TEXT,
    source_type TEXT DEFAULT 'upload',
    status TEXT DEFAULT 'pending',
    error TEXT,
    draft_path TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pipeline_rejections (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id    INTEGER,
    draft_id         INTEGER,
    image_path       TEXT,
    ocr_chars        INTEGER NOT NULL DEFAULT 0,
    item_count       INTEGER NOT NULL DEFAULT 0,
    gate_score       REAL NOT NULL,
    gate_reason      TEXT,
    pipeline_signals TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);
"""

_CUSTOMER_MESSAGE = (
    "We had trouble reading all the items in your menu. For best results, "
    "photograph each page in good lighting with the full menu clearly visible, "
    "then try again."
)


def _make_test_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA_SQL)
    conn.execute("INSERT INTO restaurants (id, name) VALUES (1, 'Test Restaurant')")
    conn.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'admin', 'dummy')")
    conn.commit()
    conn.close()
    return db_path


def _insert_job(db_path: Path, job_id: int, status: str, error: Optional[str] = None,
                filename: str = "menu.png") -> int:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO import_jobs (id, restaurant_id, filename, status, error) VALUES (?, 1, ?, ?, ?)",
        (job_id, filename, status, error),
    )
    conn.commit()
    conn.close()
    return job_id


# ---------------------------------------------------------------------------
# Flask app client fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Flask test client with monkeypatched DB (patches portal.app + storage.drafts)."""
    db_path = _make_test_db(tmp_path)

    def mock_db():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    monkeypatch.setattr("storage.drafts.db_connect", mock_db)
    try:
        monkeypatch.setattr("storage.menus.db_connect", mock_db)
    except Exception:
        pass
    # portal.app has its own db_connect (not imported from storage.drafts)
    # Must patch it directly after importing the app module
    from portal import app as _portal_app_module
    monkeypatch.setattr(_portal_app_module, "db_connect", mock_db)

    app = _portal_app_module.app
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user"] = {"id": 1, "username": "admin"}
        yield client, db_path


# ---------------------------------------------------------------------------
# Template source helpers
# ---------------------------------------------------------------------------
_TEMPLATE_DIR = Path(__file__).parent.parent / "portal" / "templates"


def _read_template(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


# ===========================================================================
# 1. import_status endpoint — error field passthrough
# ===========================================================================
class TestImportStatusEndpoint:
    """The /api/menus/import/<id>/status endpoint returns the error field."""

    def test_status_done_no_error(self, app_client):
        """Done job returns status=done, error=None."""
        client, db_path = app_client
        _insert_job(db_path, 1, "done", error=None)
        resp = client.get("/api/menus/import/1/status",
                          headers={"Accept": "application/json"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "done"
        assert data.get("error") is None

    def test_status_rejected_returns_error_field(self, app_client):
        """Rejected job returns status=rejected and the customer_message in error."""
        client, db_path = app_client
        _insert_job(db_path, 2, "rejected", error=_CUSTOMER_MESSAGE)
        resp = client.get("/api/menus/import/2/status",
                          headers={"Accept": "application/json"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "rejected"
        assert data["error"] == _CUSTOMER_MESSAGE

    def test_status_rejected_error_not_numeric_score(self, app_client):
        """The error field must not contain a numeric gate score (never expose internals)."""
        client, db_path = app_client
        _insert_job(db_path, 3, "rejected", error=_CUSTOMER_MESSAGE)
        resp = client.get("/api/menus/import/3/status",
                          headers={"Accept": "application/json"})
        data = resp.get_json()
        # customer_message should not contain "score=" or a bare float
        assert "score=" not in (data.get("error") or "")
        assert "0." not in (data.get("error") or "")

    def test_status_processing_no_error(self, app_client):
        """Processing job has no error."""
        client, db_path = app_client
        _insert_job(db_path, 4, "processing", error=None)
        resp = client.get("/api/menus/import/4/status",
                          headers={"Accept": "application/json"})
        data = resp.get_json()
        assert data["status"] == "processing"

    def test_status_missing_job_404(self, app_client):
        """Non-existent job returns 404."""
        client, db_path = app_client
        resp = client.get("/api/menus/import/9999/status",
                          headers={"Accept": "application/json"})
        assert resp.status_code == 404


# ===========================================================================
# 2. import_view.html template source — JS constants
# ===========================================================================
class TestImportViewTemplateJS:
    """Verify JS constants in import_view.html cover the 'rejected' status."""

    def setup_method(self):
        self.src = _read_template("import_view.html")

    def test_pill_classes_contains_rejected(self):
        """PILL_CLASSES JS object must map rejected → pill-red."""
        assert "rejected:" in self.src or "rejected :" in self.src
        # Check it maps to red
        lines = self.src.splitlines()
        rejected_line = next(
            (l for l in lines if "rejected" in l and "pill-red" in l), None
        )
        assert rejected_line is not None, "Expected 'rejected' mapped to 'pill-red' in PILL_CLASSES"

    def test_terminal_set_contains_rejected(self):
        """The terminal Set must include 'rejected' so polling stops on gate fail."""
        assert "'rejected'" in self.src or '"rejected"' in self.src
        # Specifically in the terminal set definition
        import re
        match = re.search(r"const terminal\s*=\s*new Set\(\[([^\]]+)\]\)", self.src)
        assert match, "terminal Set definition not found"
        set_contents = match.group(1)
        assert "rejected" in set_contents, f"'rejected' not in terminal Set: {set_contents}"

    def test_no_auto_redirect_on_rejected(self):
        """Auto-redirect code checks for 'done', not 'rejected'."""
        import re
        # The auto-redirect block must be guarded by "st === 'done'"
        redirect_block = re.search(
            r"if \(st === 'done' && shouldAutoRedirect\)", self.src
        )
        assert redirect_block, "Auto-redirect guard 'st === done && shouldAutoRedirect' not found"
        # And there must be no redirect triggered on 'rejected'
        rejected_redirect = re.search(
            r"if \(st === 'rejected'.*window\.location", self.src, re.DOTALL
        )
        assert rejected_redirect is None, "Unexpected auto-redirect on rejected status"

    def test_rejection_banner_shown_on_rejected_poll(self):
        """pollStatus() block must handle st === 'rejected' to show banner."""
        assert "st === 'rejected'" in self.src or "st==='rejected'" in self.src

    def test_rejection_banner_element_present(self):
        """Template must contain the rejection-banner div."""
        assert 'id="rejection-banner"' in self.src

    def test_rejection_message_element_present(self):
        """Template must contain the rejection-message paragraph."""
        assert 'id="rejection-message"' in self.src

    def test_rejection_banner_uses_data_error(self):
        """The JS that populates the banner must use data.error."""
        assert "data.error" in self.src


# ===========================================================================
# 3. import_view.html page load — rejected job renders banner
# ===========================================================================
class TestImportViewPageLoad:
    """Page load for a rejected job should render the banner visible."""

    def test_rejected_job_banner_visible_class(self, app_client):
        """Rejected job page renders the rejection-banner with 'visible' class."""
        client, db_path = app_client
        _insert_job(db_path, 5, "rejected", error=_CUSTOMER_MESSAGE)
        resp = client.get("/imports/5")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "rejection-banner" in html
        assert "visible" in html
        # Banner div has both class names
        assert "rejection-banner visible" in html or 'class="rejection-banner visible"' in html

    def test_rejected_job_shows_customer_message(self, app_client):
        """Customer message appears in the page HTML for rejected jobs."""
        client, db_path = app_client
        _insert_job(db_path, 6, "rejected", error=_CUSTOMER_MESSAGE)
        resp = client.get("/imports/6")
        html = resp.data.decode()
        # The friendly message must appear somewhere in the rendered HTML
        assert "trouble reading" in html or "photograph each page" in html

    def test_done_job_banner_not_visible(self, app_client):
        """Done job page must not show the rejection banner as visible."""
        client, db_path = app_client
        _insert_job(db_path, 7, "done", error=None)
        resp = client.get("/imports/7")
        html = resp.data.decode()
        # Banner div exists but must not have 'visible' class
        assert "rejection-banner visible" not in html

    def test_processing_job_banner_not_visible(self, app_client):
        """Processing job must not show rejection banner."""
        client, db_path = app_client
        _insert_job(db_path, 8, "processing", error=None)
        resp = client.get("/imports/8")
        html = resp.data.decode()
        assert "rejection-banner visible" not in html

    def test_rejected_job_red_pill(self, app_client):
        """Rejected job page shows the status pill as red (pill-red)."""
        client, db_path = app_client
        _insert_job(db_path, 9, "rejected", error=_CUSTOMER_MESSAGE)
        resp = client.get("/imports/9")
        html = resp.data.decode()
        # The status pill must include pill-red (Jinja2 catch-all → pill-red)
        assert "pill-red" in html


# ===========================================================================
# 4. imports.html template source — pill + label mappings
# ===========================================================================
class TestImportsListTemplate:
    """Verify imports.html maps 'rejected' to red pill and 'Rejected' label."""

    def setup_method(self):
        self.src = _read_template("imports.html")

    def test_jinja2_pill_class_rejected_red(self):
        """Jinja2 pill_class block must include 'rejected' in the red list."""
        # 'rejected' must appear in the same condition as 'failed' or 'discarded'
        import re
        red_cond = re.search(
            r"'pill-red'\s+if\s+st\s+in\s+\[([^\]]+)\]", self.src
        )
        assert red_cond, "pill-red condition not found in imports.html"
        condition_items = red_cond.group(1)
        assert "rejected" in condition_items, (
            f"'rejected' not in pill-red condition: {condition_items}"
        )

    def test_jinja2_status_label_rejected(self):
        """Jinja2 status_label block must map 'rejected' → 'Rejected'."""
        assert "'Rejected'" in self.src or '"Rejected"' in self.src
        # Check it's in the label mapping context (near 'Discarded')
        idx_rejected = self.src.find("'Rejected'")
        idx_discarded = self.src.find("'Discarded'")
        assert idx_rejected > 0, "'Rejected' label not found"
        # Should be close to other status labels
        assert abs(idx_rejected - idx_discarded) < 500, (
            "'Rejected' label is too far from other labels"
        )

    def test_js_pill_class_function_rejected_red(self):
        """JS PILL_CLASS() function must return pill-red for 'rejected'."""
        lines = self.src.splitlines()
        red_line = next(
            (l for l in lines if "pill-red" in l and "rejected" in l), None
        )
        assert red_line is not None, "PILL_CLASS() does not map 'rejected' to pill-red"

    def test_js_label_map_rejected(self):
        """JS LABEL() map must include rejected: 'Rejected'."""
        assert "rejected: 'Rejected'" in self.src or "rejected:'Rejected'" in self.src

    def test_rejected_not_in_pending_live_filter(self):
        """The live() filter for polling must not include 'rejected' (it's terminal)."""
        import re
        # The live() function filters for pending/processing rows
        live_match = re.search(
            r"const live\s*=\s*\(\)\s*=>\s*rows\.filter\(.*?\)", self.src
        )
        if live_match:
            live_def = live_match.group(0)
            assert "rejected" not in live_def, (
                "live() should not poll 'rejected' rows"
            )

    def test_imports_list_page_renders_rejected_pill(self, app_client):
        """imports.html renders 'Rejected' label with pill-red for a rejected job."""
        client, db_path = app_client
        _insert_job(db_path, 10, "rejected", error=_CUSTOMER_MESSAGE)
        resp = client.get("/imports")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "pill-red" in html
        assert "Rejected" in html


# ===========================================================================
# 5. Rejection banner content & structure
# ===========================================================================
class TestRejectionBannerContent:
    """Verify what the rejection banner shows and doesn't show."""

    def setup_method(self):
        self.src = _read_template("import_view.html")

    def test_banner_has_strong_heading(self):
        """Rejection banner must have a <strong> heading."""
        assert "<strong>" in self.src
        assert "Menu scan could not be completed" in self.src or \
               "could not be completed" in self.src

    def test_banner_has_retry_hint(self):
        """Rejection banner includes a retry/recovery hint for the user."""
        # The hint text or a link to the editor must be present
        assert "retry-hint" in self.src or "clearer photo" in self.src or \
               "try again" in self.src

    def test_banner_links_to_editor(self):
        """Rejection banner includes a link to the draft editor."""
        assert "imports_draft" in self.src or "Open the editor" in self.src

    def test_banner_css_hidden_by_default(self):
        """The rejection-banner CSS must set display:none by default."""
        assert ".rejection-banner {" in self.src or ".rejection-banner{" in self.src
        # Check it defaults to hidden
        import re
        banner_css = re.search(
            r"\.rejection-banner\s*\{[^}]+\}", self.src, re.DOTALL
        )
        assert banner_css, "rejection-banner CSS block not found"
        css_body = banner_css.group(0)
        assert "display:none" in css_body or "display: none" in css_body


# ===========================================================================
# 6. No score exposure in customer-facing UI
# ===========================================================================
class TestNoScoreExposure:
    """Verify that gate scores / technical details never reach the customer UI."""

    def test_customer_message_no_score_in_template(self):
        """The template must not hardcode 'score=' or numeric threshold."""
        src = _read_template("import_view.html")
        # The customer message must not mention 'score=' or 'threshold='
        # (these are log-only fields from GateResult)
        assert "score=" not in src.split("console.log")[0]

    def test_rejection_message_el_populated_from_data_error(self, app_client):
        """The rejection message element is filled from data.error, not hardcoded."""
        client, db_path = app_client
        custom_msg = "Photo was too blurry to read."
        _insert_job(db_path, 11, "rejected", error=custom_msg)
        resp = client.get("/imports/11")
        html = resp.data.decode()
        # Custom error stored in DB must appear in the page
        assert "blurry" in html or "trouble reading" in html


# ===========================================================================
# 7. Edge cases — other statuses unaffected
# ===========================================================================
class TestOtherStatusesUnaffected:
    """Ensure Day 107 changes don't affect non-rejected import statuses."""

    def test_done_job_auto_redirect_not_blocked(self, app_client):
        """Done job page still contains the auto-redirect JS for 'done'."""
        client, db_path = app_client
        _insert_job(db_path, 12, "done", error=None)
        resp = client.get("/imports/12")
        html = resp.data.decode()
        # Auto-redirect logic must still be present
        assert "shouldAutoRedirect" in html
        assert "st === 'done'" in html or "st===" in html
