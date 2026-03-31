# tests/test_day135_price_intelligence.py
"""
Day 135 — Sprint 13.2: Claude Call 4 — Price Intelligence.

Deliverables:
  1. price_intelligence_results + price_intelligence_summary tables created
  2. analyze_menu_prices() — builds prompt, calls Claude, stores results
  3. Prompt construction with items + competitor context + cuisine + region
  4. Result validation + normalization (assessment values, price cents)
  5. get_price_intelligence() — retrieve stored results by draft
  6. get_item_assessment() — retrieve per-item assessment
  7. clear_price_intelligence() — purge draft's intel data
  8. Portal routes: POST trigger + GET JSON APIs

32 tests across 8 classes:
  1. Schema — tables exist with correct columns (4)
  2. Prompt construction (4)
  3. Result validation + normalization (4)
  4. analyze_menu_prices full flow with mocked Claude (4)
  5. Cached results / skip behavior (4)
  6. get_price_intelligence + get_item_assessment (4)
  7. clear_price_intelligence (4)
  8. Portal route integration (4)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Schema (extends Day 134 schema with new tables)
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
    zip_code TEXT,
    address_line2 TEXT,
    city TEXT,
    state TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash   TEXT NOT NULL,
    display_name    TEXT,
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

CREATE TABLE IF NOT EXISTS price_comparison_cache (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    zip_code      TEXT    NOT NULL,
    cuisine_type  TEXT    NOT NULL,
    results_json  TEXT    NOT NULL,
    result_count  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    expires_at    TEXT    NOT NULL,
    UNIQUE(zip_code, cuisine_type)
);

CREATE TABLE IF NOT EXISTS price_comparison_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id   INTEGER NOT NULL,
    cache_id        INTEGER NOT NULL REFERENCES price_comparison_cache(id) ON DELETE CASCADE,
    place_id        TEXT,
    place_name      TEXT    NOT NULL,
    place_address   TEXT,
    price_level     INTEGER,
    price_label     TEXT,
    rating          REAL,
    user_ratings    INTEGER,
    cuisine_match   TEXT,
    latitude        REAL,
    longitude       REAL,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS price_intelligence_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id        INTEGER NOT NULL,
    restaurant_id   INTEGER NOT NULL,
    item_id         INTEGER,
    item_name       TEXT NOT NULL,
    item_category   TEXT,
    current_price   INTEGER NOT NULL DEFAULT 0,
    assessment      TEXT NOT NULL DEFAULT 'unknown',
    suggested_low   INTEGER,
    suggested_high  INTEGER,
    regional_avg    INTEGER,
    reasoning       TEXT,
    confidence      REAL DEFAULT 0.0,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS price_intelligence_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id        INTEGER NOT NULL UNIQUE,
    restaurant_id   INTEGER NOT NULL,
    cuisine_type    TEXT,
    zip_code        TEXT,
    competitor_count INTEGER DEFAULT 0,
    avg_market_tier TEXT,
    total_items     INTEGER DEFAULT 0,
    items_assessed  INTEGER DEFAULT 0,
    underpriced     INTEGER DEFAULT 0,
    fair_priced     INTEGER DEFAULT 0,
    overpriced      INTEGER DEFAULT 0,
    category_avgs   TEXT,
    model_used      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_price_intel_draft ON price_intelligence_results(draft_id);
CREATE INDEX IF NOT EXISTS idx_price_intel_item ON price_intelligence_results(item_id);
"""


# ---------------------------------------------------------------------------
# Sample Claude Call 4 response
# ---------------------------------------------------------------------------
SAMPLE_CLAUDE_RESPONSE = {
    "assessments": [
        {
            "item_name": "Margherita Pizza",
            "assessment": "fair",
            "suggested_low": 1299,
            "suggested_high": 1699,
            "regional_avg": 1499,
            "reasoning": "In line with local pizzeria pricing for a classic margherita.",
            "confidence": 0.9,
        },
        {
            "item_name": "Caesar Salad",
            "assessment": "slightly_underpriced",
            "suggested_low": 999,
            "suggested_high": 1399,
            "regional_avg": 1199,
            "reasoning": "Most competitors charge $11-14 for a Caesar salad in this area.",
            "confidence": 0.85,
        },
        {
            "item_name": "Chicken Parm",
            "assessment": "overpriced",
            "suggested_low": 1599,
            "suggested_high": 1999,
            "regional_avg": 1799,
            "reasoning": "At $24.99, this is above the local range of $16-20 for chicken parm.",
            "confidence": 0.8,
        },
        {
            "item_name": "Garlic Bread",
            "assessment": "fair",
            "suggested_low": 499,
            "suggested_high": 799,
            "regional_avg": 599,
            "reasoning": "Standard pricing for garlic bread as a side dish.",
            "confidence": 0.95,
        },
    ],
    "category_averages": {
        "Pizza": {
            "avg_price_cents": 1599,
            "typical_range_low": 1299,
            "typical_range_high": 1899,
            "item_count": 1,
        },
        "Salads": {
            "avg_price_cents": 1199,
            "typical_range_low": 999,
            "typical_range_high": 1399,
            "item_count": 1,
        },
        "Entrees": {
            "avg_price_cents": 1799,
            "typical_range_low": 1599,
            "typical_range_high": 2199,
            "item_count": 1,
        },
        "Sides": {
            "avg_price_cents": 599,
            "typical_range_low": 399,
            "typical_range_high": 799,
            "item_count": 1,
        },
    },
    "market_context": {
        "market_tier": "$$",
        "price_pressure": "moderate",
        "summary": "This is a moderately priced pizza-focused area with several competitors nearby.",
    },
}

# Items matching the sample response
SAMPLE_ITEMS = [
    {"id": 1, "name": "Margherita Pizza", "category": "Pizza", "price_cents": 1499, "position": 0},
    {"id": 2, "name": "Caesar Salad", "category": "Salads", "price_cents": 899, "position": 1},
    {"id": 3, "name": "Chicken Parm", "category": "Entrees", "price_cents": 2499, "position": 2},
    {"id": 4, "name": "Garlic Bread", "category": "Sides", "price_cents": 599, "position": 3},
]

SAMPLE_COMPETITORS = [
    {"place_name": "Joe's Pizza", "place_address": "123 Broadway", "price_level": 1, "price_label": "$", "rating": 4.5, "user_ratings": 850},
    {"place_name": "Luigi's Italian", "place_address": "456 5th Ave", "price_level": 2, "price_label": "$$", "rating": 4.2, "user_ratings": 320},
]

SAMPLE_MARKET_SUMMARY = {
    "competitor_count": 2,
    "avg_rating": 4.35,
    "price_distribution": {"$": 1, "$$": 1},
    "has_data": True,
}


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
    # Insert test restaurant
    conn.execute(
        "INSERT INTO restaurants (id, name, phone, address, zip_code, cuisine_type) "
        "VALUES (1, 'Test Pizza', '555-1234', '123 Main St', '10001', 'pizza')"
    )
    # Insert test draft + items
    conn.execute(
        "INSERT INTO drafts (id, restaurant_id, title, status) "
        "VALUES (1, 1, 'Test Menu', 'editing')"
    )
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    for item in SAMPLE_ITEMS:
        conn.execute(
            "INSERT INTO draft_items (id, draft_id, name, category, price_cents, position, created_at, updated_at) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
            (item["id"], item["name"], item["category"], item["price_cents"], item["position"], now, now),
        )
    conn.commit()
    conn.close()

    import storage.drafts as _drafts
    import storage.users as _users
    import storage.menus as _menus
    import storage.price_intel as _pi
    import storage.ai_price_intel as _api
    monkeypatch.setattr(_drafts, "db_connect", _connect)
    monkeypatch.setattr(_users, "db_connect", _connect)
    monkeypatch.setattr(_menus, "db_connect", _connect)
    monkeypatch.setattr(_pi, "db_connect", _connect)
    monkeypatch.setattr(_api, "_db_connect", _connect)
    return _connect


@pytest.fixture()
def app_client(mock_db, monkeypatch):
    import portal.app as _app
    import storage.users as _users
    import storage.price_intel as _pi
    import storage.ai_price_intel as _api
    monkeypatch.setattr(_app, "db_connect", mock_db)
    monkeypatch.setattr(_app, "users_store", _users)
    monkeypatch.setattr(_app, "price_intel", _pi)
    monkeypatch.setattr(_app, "ai_price_intel", _api)
    _app.app.config["TESTING"] = True
    _app.app.config["WTF_CSRF_ENABLED"] = False
    return _app.app.test_client()


def _register_and_choose_tier(client, tier="premium", email="cust@example.com", password="securepass1"):
    client.post("/register", data={
        "email": email,
        "password": password,
        "confirm_password": password,
        "display_name": "Test Customer",
    })
    client.post("/choose-plan", data={"tier": tier})
    return client


def _mock_claude_response(text_content):
    """Create a mock Anthropic messages.create response."""
    mock_block = MagicMock()
    mock_block.text = text_content
    mock_resp = MagicMock()
    mock_resp.content = [mock_block]
    return mock_resp


# ===================================================================
# Class 1: Schema — tables exist with correct columns (4)
# ===================================================================
class TestSchema:
    def test_price_intelligence_results_table_exists(self, mock_db):
        conn = mock_db()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='price_intelligence_results'"
        ).fetchall()
        assert len(rows) == 1

    def test_price_intelligence_summary_table_exists(self, mock_db):
        conn = mock_db()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='price_intelligence_summary'"
        ).fetchall()
        assert len(rows) == 1

    def test_results_table_columns(self, mock_db):
        conn = mock_db()
        cols = conn.execute("PRAGMA table_info(price_intelligence_results)").fetchall()
        col_names = {c[1] for c in cols}
        expected = {
            "id", "draft_id", "restaurant_id", "item_id", "item_name",
            "item_category", "current_price", "assessment", "suggested_low",
            "suggested_high", "regional_avg", "reasoning", "confidence", "created_at",
        }
        assert expected.issubset(col_names)

    def test_summary_table_columns(self, mock_db):
        conn = mock_db()
        cols = conn.execute("PRAGMA table_info(price_intelligence_summary)").fetchall()
        col_names = {c[1] for c in cols}
        expected = {
            "id", "draft_id", "restaurant_id", "cuisine_type", "zip_code",
            "competitor_count", "avg_market_tier", "total_items", "items_assessed",
            "underpriced", "fair_priced", "overpriced", "category_avgs",
            "model_used", "created_at", "updated_at",
        }
        assert expected.issubset(col_names)


# ===================================================================
# Class 2: Prompt construction (4)
# ===================================================================
class TestPromptConstruction:
    def test_prompt_includes_items(self):
        from storage.ai_price_intel import _build_prompt
        prompt = _build_prompt(SAMPLE_ITEMS, "pizza", "10001", SAMPLE_COMPETITORS, SAMPLE_MARKET_SUMMARY)
        assert "Margherita Pizza" in prompt
        assert "Caesar Salad" in prompt
        assert "$14.99" in prompt

    def test_prompt_includes_cuisine_and_zip(self):
        from storage.ai_price_intel import _build_prompt
        prompt = _build_prompt(SAMPLE_ITEMS, "pizza", "10001", [], {"has_data": False})
        assert "pizza" in prompt
        assert "10001" in prompt

    def test_prompt_includes_competitors(self):
        from storage.ai_price_intel import _build_prompt
        prompt = _build_prompt(SAMPLE_ITEMS, "pizza", "10001", SAMPLE_COMPETITORS, SAMPLE_MARKET_SUMMARY)
        assert "Joe's Pizza" in prompt
        assert "Luigi's Italian" in prompt

    def test_prompt_handles_no_competitors(self):
        from storage.ai_price_intel import _build_prompt
        prompt = _build_prompt(SAMPLE_ITEMS, "pizza", "10001", [], {"has_data": False})
        assert "No competitor data" in prompt


# ===================================================================
# Class 3: Result validation + normalization (4)
# ===================================================================
class TestResultValidation:
    def test_validates_known_assessments(self):
        from storage.ai_price_intel import _normalize_assessment
        assert _normalize_assessment("fair") == "fair"
        assert _normalize_assessment("overpriced") == "overpriced"
        assert _normalize_assessment("slightly_underpriced") == "slightly_underpriced"

    def test_normalizes_unknown_values(self):
        from storage.ai_price_intel import _normalize_assessment
        assert _normalize_assessment("") == "unknown"
        assert _normalize_assessment("garbage") == "unknown"
        assert _normalize_assessment(None) == "unknown"

    def test_validate_results_matches_items(self):
        from storage.ai_price_intel import _validate_results
        result = _validate_results(SAMPLE_CLAUDE_RESPONSE, SAMPLE_ITEMS)
        assessments = result["assessments"]
        assert len(assessments) == 4
        assert assessments[0]["item_id"] == 1
        assert assessments[0]["item_name"] == "Margherita Pizza"
        assert assessments[0]["assessment"] == "fair"

    def test_validate_results_clamps_confidence(self):
        from storage.ai_price_intel import _validate_results
        data = {
            "assessments": [{
                "item_name": "Margherita Pizza",
                "assessment": "fair",
                "suggested_low": 1299,
                "suggested_high": 1699,
                "regional_avg": 1499,
                "reasoning": "ok",
                "confidence": 1.5,  # exceeds 1.0
            }],
            "category_averages": {},
            "market_context": {},
        }
        result = _validate_results(data, SAMPLE_ITEMS)
        assert result["assessments"][0]["confidence"] == 1.0


# ===================================================================
# Class 4: analyze_menu_prices full flow with mocked Claude (4)
# ===================================================================
class TestAnalyzeMenuPrices:
    @patch("storage.ai_price_intel._get_client")
    def test_full_flow_returns_assessments(self, mock_client, mock_db):
        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            json.dumps(SAMPLE_CLAUDE_RESPONSE)
        )
        mock_client.return_value = client

        from storage.ai_price_intel import analyze_menu_prices
        result = analyze_menu_prices(1, 1, force_refresh=True)

        assert result["skipped"] is False
        assert result["total_items"] == 4
        assert result["items_assessed"] == 4
        assert len(result["assessments"]) == 4

    @patch("storage.ai_price_intel._get_client")
    def test_stores_results_in_db(self, mock_client, mock_db):
        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            json.dumps(SAMPLE_CLAUDE_RESPONSE)
        )
        mock_client.return_value = client

        from storage.ai_price_intel import analyze_menu_prices
        analyze_menu_prices(1, 1, force_refresh=True)

        conn = mock_db()
        rows = conn.execute(
            "SELECT * FROM price_intelligence_results WHERE draft_id = 1"
        ).fetchall()
        assert len(rows) == 4

        summary = conn.execute(
            "SELECT * FROM price_intelligence_summary WHERE draft_id = 1"
        ).fetchone()
        assert summary is not None
        assert summary["total_items"] == 4

    @patch("storage.ai_price_intel._get_client")
    def test_handles_api_failure(self, mock_client, mock_db):
        mock_client.return_value = None  # No client

        from storage.ai_price_intel import analyze_menu_prices
        result = analyze_menu_prices(1, 1, force_refresh=True)

        assert result["skipped"] is True
        assert "error" in result

    def test_skips_if_too_few_items(self, mock_db):
        # Delete items until we have fewer than MIN_ITEMS_FOR_ANALYSIS
        conn = mock_db()
        conn.execute("DELETE FROM draft_items WHERE id > 2")
        conn.commit()

        from storage.ai_price_intel import analyze_menu_prices
        result = analyze_menu_prices(1, 1, force_refresh=True)
        assert result["skipped"] is True
        assert "at least" in result.get("error", "")


# ===================================================================
# Class 5: Cached results / skip behavior (4)
# ===================================================================
class TestCacheBehavior:
    @patch("storage.ai_price_intel._get_client")
    def test_returns_cached_without_calling_claude(self, mock_client, mock_db):
        # First call: populate
        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            json.dumps(SAMPLE_CLAUDE_RESPONSE)
        )
        mock_client.return_value = client

        from storage.ai_price_intel import analyze_menu_prices
        analyze_menu_prices(1, 1, force_refresh=True)

        # Reset call count
        client.messages.create.reset_mock()

        # Second call: should return cached
        result = analyze_menu_prices(1, 1, force_refresh=False)
        assert result.get("from_cache") is True
        client.messages.create.assert_not_called()

    @patch("storage.ai_price_intel._get_client")
    def test_force_refresh_bypasses_cache(self, mock_client, mock_db):
        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            json.dumps(SAMPLE_CLAUDE_RESPONSE)
        )
        mock_client.return_value = client

        from storage.ai_price_intel import analyze_menu_prices
        analyze_menu_prices(1, 1, force_refresh=True)
        client.messages.create.reset_mock()

        # Force refresh should call Claude again
        analyze_menu_prices(1, 1, force_refresh=True)
        client.messages.create.assert_called_once()

    def test_nonexistent_restaurant(self, mock_db):
        from storage.ai_price_intel import analyze_menu_prices
        result = analyze_menu_prices(1, 999, force_refresh=True)
        assert result["skipped"] is True
        assert "not found" in result.get("error", "")

    def test_empty_draft_skips(self, mock_db):
        # Create an empty draft
        conn = mock_db()
        conn.execute("INSERT INTO drafts (id, restaurant_id, title) VALUES (99, 1, 'Empty')")
        conn.commit()

        from storage.ai_price_intel import analyze_menu_prices
        result = analyze_menu_prices(99, 1, force_refresh=True)
        assert result["skipped"] is True


# ===================================================================
# Class 6: get_price_intelligence + get_item_assessment (4)
# ===================================================================
class TestRetrieveResults:
    def _populate(self, mock_db):
        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            json.dumps(SAMPLE_CLAUDE_RESPONSE)
        )
        with patch("storage.ai_price_intel._get_client", return_value=client):
            from storage.ai_price_intel import analyze_menu_prices
            analyze_menu_prices(1, 1, force_refresh=True)

    def test_get_price_intelligence_returns_full_data(self, mock_db):
        self._populate(mock_db)
        from storage.ai_price_intel import get_price_intelligence
        result = get_price_intelligence(1)
        assert result is not None
        assert len(result["assessments"]) == 4
        assert result["total_items"] == 4

    def test_get_price_intelligence_includes_category_avgs(self, mock_db):
        self._populate(mock_db)
        from storage.ai_price_intel import get_price_intelligence
        result = get_price_intelligence(1)
        assert "category_avgs" in result
        assert isinstance(result["category_avgs"], dict)

    def test_get_item_assessment(self, mock_db):
        self._populate(mock_db)
        from storage.ai_price_intel import get_item_assessment
        a = get_item_assessment(1, 1)  # draft=1, item=1 (Margherita)
        assert a is not None
        assert a["assessment"] == "fair"
        assert a["item_name"] == "Margherita Pizza"

    def test_get_price_intelligence_returns_none_for_missing(self, mock_db):
        from storage.ai_price_intel import get_price_intelligence
        result = get_price_intelligence(999)
        assert result is None


# ===================================================================
# Class 7: clear_price_intelligence (4)
# ===================================================================
class TestClearIntelligence:
    def _populate(self, mock_db):
        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            json.dumps(SAMPLE_CLAUDE_RESPONSE)
        )
        with patch("storage.ai_price_intel._get_client", return_value=client):
            from storage.ai_price_intel import analyze_menu_prices
            analyze_menu_prices(1, 1, force_refresh=True)

    def test_clear_removes_results(self, mock_db):
        self._populate(mock_db)
        from storage.ai_price_intel import clear_price_intelligence, get_price_intelligence
        deleted = clear_price_intelligence(1)
        assert deleted == 4
        assert get_price_intelligence(1) is None

    def test_clear_removes_summary(self, mock_db):
        self._populate(mock_db)
        from storage.ai_price_intel import clear_price_intelligence
        clear_price_intelligence(1)
        conn = mock_db()
        row = conn.execute(
            "SELECT * FROM price_intelligence_summary WHERE draft_id = 1"
        ).fetchone()
        assert row is None

    def test_clear_nonexistent_returns_zero(self, mock_db):
        from storage.ai_price_intel import clear_price_intelligence
        deleted = clear_price_intelligence(999)
        assert deleted == 0

    def test_clear_then_rerun(self, mock_db):
        """After clearing, a new run should work and not return cached."""
        self._populate(mock_db)
        from storage.ai_price_intel import clear_price_intelligence, analyze_menu_prices

        clear_price_intelligence(1)

        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            json.dumps(SAMPLE_CLAUDE_RESPONSE)
        )
        with patch("storage.ai_price_intel._get_client", return_value=client):
            result = analyze_menu_prices(1, 1, force_refresh=False)
        assert result["skipped"] is False
        assert result.get("from_cache") is not True


# ===================================================================
# Class 8: Portal route integration (4)
# ===================================================================
class TestPortalRoutes:
    def test_post_price_intelligence_requires_login(self, app_client):
        resp = app_client.post("/drafts/1/price_intelligence")
        assert resp.status_code in (302, 401, 403)

    @patch("storage.ai_price_intel.analyze_menu_prices")
    def test_post_price_intelligence_triggers_analysis(self, mock_analyze, app_client):
        mock_analyze.return_value = {
            "assessments": SAMPLE_CLAUDE_RESPONSE["assessments"],
            "items_assessed": 4,
            "total_items": 4,
            "skipped": False,
        }
        _register_and_choose_tier(app_client)
        resp = app_client.post("/drafts/1/price_intelligence", follow_redirects=True)
        assert resp.status_code == 200
        mock_analyze.assert_called_once()

    def test_get_api_price_intelligence_requires_login(self, app_client):
        resp = app_client.get("/api/drafts/1/price_intelligence")
        assert resp.status_code in (302, 401, 403)

    @patch("storage.ai_price_intel.get_price_intelligence")
    def test_get_api_price_intelligence_returns_json(self, mock_get, app_client):
        mock_get.return_value = {
            "assessments": [],
            "total_items": 0,
            "items_assessed": 0,
        }
        _register_and_choose_tier(app_client)
        resp = app_client.get("/api/drafts/1/price_intelligence")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("has_data") is True
