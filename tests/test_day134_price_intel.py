# tests/test_day134_price_intel.py
"""
Day 134 — Sprint 13.2: Google Places API integration for price intelligence.

Deliverables:
  1. price_comparison_cache + price_comparison_results tables created
  2. search_nearby_restaurants() — geocode + nearby search + cache
  3. Cache hit/miss/expiry logic with 7-day TTL
  4. Rate limiting (10 calls/min)
  5. get_cached_comparisons() returns stored results
  6. get_market_summary() computes avg rating + price distribution
  7. clear_cache() purges entries
  8. Portal routes: POST trigger + GET JSON API

32 tests across 8 classes:
  1. Schema — tables exist with correct columns (4)
  2. Cache store/retrieve (4)
  3. Cache expiry + force refresh (4)
  4. Rate limiting (4)
  5. search_nearby_restaurants full flow with mocked API (4)
  6. get_cached_comparisons + get_market_summary (4)
  7. clear_cache (4)
  8. Portal route integration (4)
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

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
"""


# ---------------------------------------------------------------------------
# Sample Google Places API responses
# ---------------------------------------------------------------------------
SAMPLE_GEOCODE_RESPONSE = {
    "status": "OK",
    "results": [{"geometry": {"location": {"lat": 40.7128, "lng": -74.0060}}}],
}

SAMPLE_NEARBY_RESPONSE = {
    "status": "OK",
    "results": [
        {
            "place_id": "ChIJ_abc123",
            "name": "Joe's Pizza",
            "vicinity": "123 Broadway, New York",
            "price_level": 1,
            "rating": 4.5,
            "user_ratings_total": 850,
            "geometry": {"location": {"lat": 40.713, "lng": -74.005}},
            "types": ["restaurant", "food"],
        },
        {
            "place_id": "ChIJ_def456",
            "name": "Luigi's Italian",
            "vicinity": "456 5th Ave, New York",
            "price_level": 2,
            "rating": 4.2,
            "user_ratings_total": 320,
            "geometry": {"location": {"lat": 40.714, "lng": -74.003}},
            "types": ["restaurant", "food"],
        },
        {
            "place_id": "ChIJ_ghi789",
            "name": "Sal's Slice",
            "vicinity": "789 Park Ave, New York",
            "price_level": 1,
            "rating": 3.9,
            "user_ratings_total": 150,
            "geometry": {"location": {"lat": 40.715, "lng": -74.007}},
            "types": ["restaurant", "food"],
        },
    ],
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
    conn.execute(
        "INSERT INTO restaurants (id, name, phone, address, zip_code, cuisine_type) "
        "VALUES (1, 'Test Pizza', '555-1234', '123 Main St', '10001', 'pizza')"
    )
    conn.execute(
        "INSERT INTO restaurants (id, name, phone, address, active) "
        "VALUES (2, 'No Profile', '555-0000', '456 Oak Ave', 1)"
    )
    conn.commit()
    conn.close()

    import storage.drafts as _drafts
    import storage.users as _users
    import storage.menus as _menus
    import storage.price_intel as _pi
    monkeypatch.setattr(_drafts, "db_connect", _connect)
    monkeypatch.setattr(_users, "db_connect", _connect)
    monkeypatch.setattr(_menus, "db_connect", _connect)
    monkeypatch.setattr(_pi, "db_connect", _connect)
    return _connect


@pytest.fixture()
def app_client(mock_db, monkeypatch):
    import portal.app as _app
    import storage.users as _users
    import storage.price_intel as _pi
    monkeypatch.setattr(_app, "db_connect", mock_db)
    monkeypatch.setattr(_app, "users_store", _users)
    monkeypatch.setattr(_app, "price_intel", _pi)
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


def _login_customer(client, email="cust@example.com", password="securepass1"):
    return client.post("/login", data={
        "email": email, "password": password,
    }, follow_redirects=True)


def _seed_cache(connect_fn, zip_code="10001", cuisine="pizza", results=None, expired=False):
    """Insert a cache row directly for testing."""
    now = datetime.utcnow()
    if expired:
        exp = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        exp = (now + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    if results is None:
        results = [
            {"name": "Joe's Pizza", "place_id": "abc", "address": "123 Broadway",
             "price_level": 1, "price_label": "$", "rating": 4.5,
             "user_ratings_total": 850, "lat": 40.713, "lng": -74.005},
            {"name": "Luigi's Italian", "place_id": "def", "address": "456 5th Ave",
             "price_level": 2, "price_label": "$$", "rating": 4.2,
             "user_ratings_total": 320, "lat": 40.714, "lng": -74.003},
        ]
    conn = connect_fn()
    conn.execute(
        "INSERT INTO price_comparison_cache (zip_code, cuisine_type, results_json, result_count, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (zip_code, cuisine, json.dumps(results), len(results),
         now.strftime("%Y-%m-%d %H:%M:%S"), exp),
    )
    cache_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for r in results:
        conn.execute(
            "INSERT INTO price_comparison_results "
            "(restaurant_id, cache_id, place_id, place_name, place_address, "
            "price_level, price_label, rating, user_ratings, cuisine_match, latitude, longitude, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, cache_id, r.get("place_id"), r["name"], r.get("address"),
             r.get("price_level"), r.get("price_label"), r.get("rating"),
             r.get("user_ratings_total", 0), cuisine, r.get("lat"), r.get("lng"),
             now.strftime("%Y-%m-%d %H:%M:%S")),
        )
    conn.commit()
    conn.close()
    return cache_id


# ===========================================================================
# 1. Schema — tables exist with correct columns
# ===========================================================================
class TestSchema:
    def test_cache_table_exists(self, mock_db):
        conn = mock_db()
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='price_comparison_cache'").fetchall()
        conn.close()
        assert len(rows) == 1

    def test_results_table_exists(self, mock_db):
        conn = mock_db()
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='price_comparison_results'").fetchall()
        conn.close()
        assert len(rows) == 1

    def test_cache_unique_constraint(self, mock_db):
        conn = mock_db()
        conn.execute(
            "INSERT INTO price_comparison_cache (zip_code, cuisine_type, results_json, result_count, created_at, expires_at) "
            "VALUES ('10001', 'pizza', '[]', 0, '2026-01-01', '2026-02-01')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO price_comparison_cache (zip_code, cuisine_type, results_json, result_count, created_at, expires_at) "
                "VALUES ('10001', 'pizza', '[]', 0, '2026-01-01', '2026-02-01')"
            )
        conn.close()

    def test_results_cascade_delete(self, mock_db):
        cache_id = _seed_cache(mock_db)
        conn = mock_db()
        assert conn.execute("SELECT COUNT(*) FROM price_comparison_results WHERE cache_id = ?", (cache_id,)).fetchone()[0] == 2
        conn.execute("DELETE FROM price_comparison_cache WHERE id = ?", (cache_id,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM price_comparison_results WHERE cache_id = ?", (cache_id,)).fetchone()[0] == 0
        conn.close()


# ===========================================================================
# 2. Cache store/retrieve
# ===========================================================================
class TestCacheStoreRetrieve:
    def test_store_and_retrieve(self, mock_db):
        from storage.price_intel import _store_cache, _get_cached
        results = [{"name": "Pizza Place", "price_level": 1, "price_label": "$", "rating": 4.0, "user_ratings_total": 100}]
        _store_cache("10001", "pizza", results, 1)
        cached = _get_cached("10001", "pizza")
        assert cached is not None
        assert cached["from_cache"] is True
        assert cached["result_count"] == 1

    def test_cache_miss(self, mock_db):
        from storage.price_intel import _get_cached
        assert _get_cached("99999", "mexican") is None

    def test_upsert_replaces(self, mock_db):
        from storage.price_intel import _store_cache, _get_cached
        _store_cache("10001", "pizza", [{"name": "A", "user_ratings_total": 0}], 1)
        _store_cache("10001", "pizza", [{"name": "B", "user_ratings_total": 0}, {"name": "C", "user_ratings_total": 0}], 1)
        cached = _get_cached("10001", "pizza")
        assert cached["result_count"] == 2
        assert cached["results"][1]["name"] == "C"

    def test_different_zip_codes_separate(self, mock_db):
        from storage.price_intel import _store_cache, _get_cached
        _store_cache("10001", "pizza", [{"name": "NY", "user_ratings_total": 0}], 1)
        _store_cache("90210", "pizza", [{"name": "LA", "user_ratings_total": 0}], 1)
        ny = _get_cached("10001", "pizza")
        la = _get_cached("90210", "pizza")
        assert ny["results"][0]["name"] == "NY"
        assert la["results"][0]["name"] == "LA"


# ===========================================================================
# 3. Cache expiry + force refresh
# ===========================================================================
class TestCacheExpiry:
    def test_expired_cache_returns_none(self, mock_db):
        _seed_cache(mock_db, expired=True)
        from storage.price_intel import _get_cached
        assert _get_cached("10001", "pizza") is None

    def test_valid_cache_returns_data(self, mock_db):
        _seed_cache(mock_db)
        from storage.price_intel import _get_cached
        cached = _get_cached("10001", "pizza")
        assert cached is not None
        assert cached["result_count"] == 2

    def test_expired_cache_deleted(self, mock_db):
        _seed_cache(mock_db, expired=True)
        from storage.price_intel import _get_cached
        _get_cached("10001", "pizza")  # triggers deletion
        conn = mock_db()
        count = conn.execute("SELECT COUNT(*) FROM price_comparison_cache WHERE zip_code='10001'").fetchone()[0]
        conn.close()
        assert count == 0

    def test_force_refresh_bypasses_cache(self, mock_db):
        _seed_cache(mock_db)
        from storage.price_intel import search_nearby_restaurants
        with patch("storage.price_intel._get_api_key", return_value="fake-key"), \
             patch("storage.price_intel._geocode_zip", return_value={"lat": 40.7, "lng": -74.0}), \
             patch("storage.price_intel._search_nearby", return_value=[
                 {"name": "New Place", "place_id": "new", "price_level": 3, "price_label": "$$$",
                  "rating": 4.8, "user_ratings_total": 50, "lat": 40.7, "lng": -74.0}
             ]):
            result = search_nearby_restaurants(1, force_refresh=True)
        assert result["result_count"] == 1
        assert result["from_cache"] is False


# ===========================================================================
# 4. Rate limiting
# ===========================================================================
class TestRateLimiting:
    def test_under_limit_passes(self, mock_db):
        from storage.price_intel import _check_rate_limit, _call_timestamps
        _call_timestamps.clear()
        _check_rate_limit()  # should not raise

    def test_at_limit_raises(self, mock_db):
        from storage.price_intel import _check_rate_limit, _call_timestamps, RATE_LIMIT_PER_MINUTE
        _call_timestamps.clear()
        now = time.time()
        for _ in range(RATE_LIMIT_PER_MINUTE):
            _call_timestamps.append(now)
        with pytest.raises(RuntimeError, match="rate limit"):
            _check_rate_limit()
        _call_timestamps.clear()

    def test_old_timestamps_pruned(self, mock_db):
        from storage.price_intel import _check_rate_limit, _call_timestamps, RATE_LIMIT_PER_MINUTE
        _call_timestamps.clear()
        old = time.time() - 120  # 2 minutes ago
        for _ in range(RATE_LIMIT_PER_MINUTE):
            _call_timestamps.append(old)
        _check_rate_limit()  # should not raise — old calls pruned
        _call_timestamps.clear()

    def test_record_api_call(self, mock_db):
        from storage.price_intel import _record_api_call, _call_timestamps
        _call_timestamps.clear()
        _record_api_call()
        assert len(_call_timestamps) == 1
        _call_timestamps.clear()


# ===========================================================================
# 5. search_nearby_restaurants full flow with mocked API
# ===========================================================================
class TestSearchNearbyFull:
    def test_success_flow(self, mock_db):
        from storage.price_intel import search_nearby_restaurants, _call_timestamps
        _call_timestamps.clear()
        with patch("storage.price_intel._get_api_key", return_value="fake-key"), \
             patch("storage.price_intel._geocode_zip", return_value={"lat": 40.7, "lng": -74.0}), \
             patch("storage.price_intel._search_nearby", return_value=[
                 {"name": "Joe's Pizza", "place_id": "abc", "price_level": 1, "price_label": "$",
                  "rating": 4.5, "user_ratings_total": 850, "lat": 40.713, "lng": -74.005},
             ]):
            result = search_nearby_restaurants(1)
        assert result["result_count"] == 1
        assert result["from_cache"] is False
        assert result["zip_code"] == "10001"
        _call_timestamps.clear()

    def test_no_zip_code(self, mock_db):
        from storage.price_intel import search_nearby_restaurants
        result = search_nearby_restaurants(2)  # restaurant 2 has no zip_code
        assert result["error"]
        assert "zip code" in result["error"].lower()

    def test_restaurant_not_found(self, mock_db):
        from storage.price_intel import search_nearby_restaurants
        result = search_nearby_restaurants(999)
        assert result["error"] == "Restaurant not found"

    def test_geocode_failure(self, mock_db):
        from storage.price_intel import search_nearby_restaurants, _call_timestamps
        _call_timestamps.clear()
        with patch("storage.price_intel._get_api_key", return_value="fake-key"), \
             patch("storage.price_intel._geocode_zip", return_value=None):
            result = search_nearby_restaurants(1)
        assert "geocode" in result["error"].lower()
        _call_timestamps.clear()


# ===========================================================================
# 6. get_cached_comparisons + get_market_summary
# ===========================================================================
class TestComparisonsAndSummary:
    def test_get_cached_comparisons(self, mock_db):
        _seed_cache(mock_db)
        from storage.price_intel import get_cached_comparisons
        comps = get_cached_comparisons(1)
        assert len(comps) == 2
        assert comps[0]["place_name"] in ("Joe's Pizza", "Luigi's Italian")

    def test_market_summary_with_data(self, mock_db):
        _seed_cache(mock_db)
        from storage.price_intel import get_market_summary
        summary = get_market_summary(1)
        assert summary["has_data"] is True
        assert summary["competitor_count"] == 2
        assert summary["avg_rating"] == 4.35  # (4.5 + 4.2) / 2

    def test_market_summary_no_data(self, mock_db):
        from storage.price_intel import get_market_summary
        summary = get_market_summary(999)
        assert summary["has_data"] is False
        assert summary["competitor_count"] == 0

    def test_price_distribution(self, mock_db):
        _seed_cache(mock_db)
        from storage.price_intel import get_market_summary
        summary = get_market_summary(1)
        dist = summary["price_distribution"]
        assert dist.get("$") == 1
        assert dist.get("$$") == 1


# ===========================================================================
# 7. clear_cache
# ===========================================================================
class TestClearCache:
    def test_clear_all(self, mock_db):
        _seed_cache(mock_db, zip_code="10001", cuisine="pizza")
        _seed_cache(mock_db, zip_code="90210", cuisine="mexican")
        from storage.price_intel import clear_cache
        deleted = clear_cache()
        assert deleted == 2

    def test_clear_by_zip(self, mock_db):
        _seed_cache(mock_db, zip_code="10001", cuisine="pizza")
        _seed_cache(mock_db, zip_code="90210", cuisine="mexican")
        from storage.price_intel import clear_cache
        deleted = clear_cache(zip_code="10001")
        assert deleted == 1

    def test_clear_by_zip_and_cuisine(self, mock_db):
        _seed_cache(mock_db, zip_code="10001", cuisine="pizza")
        _seed_cache(mock_db, zip_code="10001", cuisine="italian")
        from storage.price_intel import clear_cache
        deleted = clear_cache(zip_code="10001", cuisine_type="pizza")
        assert deleted == 1

    def test_clear_empty(self, mock_db):
        from storage.price_intel import clear_cache
        deleted = clear_cache()
        assert deleted == 0


# ===========================================================================
# 8. Portal route integration
# ===========================================================================
class TestPortalRoutes:
    def _setup_customer_with_restaurant(self, client, mock_db):
        """Register customer, link to restaurant #1."""
        _register_and_choose_tier(client, tier="premium")
        import storage.users as _users
        with mock_db() as conn:
            uid = conn.execute("SELECT id FROM users ORDER BY id DESC LIMIT 1").fetchone()[0]
        _users.link_user_restaurant(uid, 1, role="owner")

    def test_price_intel_post_requires_login(self, app_client):
        resp = app_client.post("/restaurants/1/price_intel")
        assert resp.status_code in (302, 401)  # redirect to login

    def test_api_price_intel_returns_json(self, app_client, mock_db):
        self._setup_customer_with_restaurant(app_client, mock_db)
        _seed_cache(mock_db)
        resp = app_client.get("/api/restaurants/1/price_intel")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "comparisons" in data
        assert "summary" in data

    def test_api_price_intel_empty(self, app_client, mock_db):
        self._setup_customer_with_restaurant(app_client, mock_db)
        resp = app_client.get("/api/restaurants/1/price_intel")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["summary"]["has_data"] is False

    def test_post_trigger_redirects(self, app_client, mock_db):
        self._setup_customer_with_restaurant(app_client, mock_db)
        with patch("storage.price_intel.search_nearby_restaurants", return_value={
            "results": [], "result_count": 0, "from_cache": False,
            "zip_code": "10001", "cuisine_type": "pizza",
        }):
            resp = app_client.post("/restaurants/1/price_intel", follow_redirects=False)
        assert resp.status_code == 302
