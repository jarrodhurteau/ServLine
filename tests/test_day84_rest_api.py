"""
Day 84 -- REST API Endpoints for External POS Integrations.

Sprint 9.4, Day 84: Token-authenticated REST API with rate limiting
for external POS systems to read/write draft items.

Covers:
  API Key CRUD:
  - create_api_key() returns raw key + record
  - validate_api_key() with valid key returns record
  - validate_api_key() with invalid key returns None
  - validate_api_key() with empty/None returns None
  - revoke_api_key() deactivates key
  - Revoked key record has active=0
  - Multiple keys can coexist
  - Custom rate_limit_rpm persists
  - Custom restaurant_id persists

  Auth Decorator:
  - Missing header -> 401
  - Invalid key -> 401
  - Revoked key -> 403
  - Valid X-API-Key header -> 200
  - Valid Authorization: Bearer header -> 200
  - Rate limit headers present on success

  Rate Limiting:
  - Within limit -> allowed
  - Exceeded -> 429 with Retry-After
  - Different keys have independent limits
  - Custom rate_limit_rpm respected
  - Headers: X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset

  GET /api/drafts/<id>/items:
  - Success with items and variants
  - Empty draft returns empty list
  - Draft not found -> 404
  - Returns correct count
  - Variants nested under items
  - Auth required (no key -> 401)

  POST /api/drafts/<id>/items:
  - Success creates items, returns 201
  - Creates items with variants
  - Draft not found -> 404
  - Non-editing status -> 403
  - Non-JSON body -> 400
  - Validation error -> 400
  - Missing 'items' key -> 400

  PUT /api/drafts/<id>/items/<item_id>:
  - Success updates item
  - Updates item with variants
  - Draft not found -> 404
  - Item not in draft -> 404
  - Non-editing status -> 403
  - Non-JSON body -> 400
  - Validation error -> 400

  Response Consistency:
  - All success responses have "ok": True
  - All error responses have "ok": False + "error"
  - All authenticated responses have rate limit headers
  - JSON content-type on all responses
"""

from __future__ import annotations

import json
import sqlite3
import time
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 71-83 tests)
# ---------------------------------------------------------------------------
_TEST_CONN: Optional[sqlite3.Connection] = None


def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the required schema incl. api_keys."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            restaurant_id INTEGER,
            status TEXT NOT NULL DEFAULT 'editing',
            source TEXT,
            source_job_id INTEGER,
            source_file_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            category TEXT,
            position INTEGER,
            confidence INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_item_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            price_cents INTEGER NOT NULL DEFAULT 0,
            kind TEXT DEFAULT 'size',
            position INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (item_id) REFERENCES draft_items(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_export_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            format TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            variant_count INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0,
            exported_at TEXT NOT NULL,
            FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT NOT NULL UNIQUE,
            restaurant_id INTEGER,
            label TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            rate_limit_rpm INTEGER NOT NULL DEFAULT 60,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER,
            name TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            category TEXT,
            position INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_draft ON draft_items(draft_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_variants_item ON draft_item_variants(item_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_export_history_draft ON draft_export_history(draft_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)")
    conn.commit()
    return conn


def _patch_db(monkeypatch):
    global _TEST_CONN
    _TEST_CONN = _make_test_db()
    import storage.drafts as drafts_mod
    def mock_connect():
        return _TEST_CONN
    monkeypatch.setattr(drafts_mod, "db_connect", mock_connect)
    return _TEST_CONN


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    conn = _patch_db(monkeypatch)
    yield conn
    global _TEST_CONN
    _TEST_CONN = None


# ---------------------------------------------------------------------------
# Data factory helpers
# ---------------------------------------------------------------------------
def _create_draft(conn, title="Test Draft", status="editing") -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO drafts (title, status, created_at, updated_at) "
        "VALUES (?, ?, datetime('now'), datetime('now'))",
        (title, status),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_item(conn, draft_id, name, price_cents=0, category=None,
                 description=None, position=None) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_items (draft_id, name, description, price_cents, category, "
        "position, confidence, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 80, datetime('now'), datetime('now'))",
        (draft_id, name, description, price_cents, category, position),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_variant(conn, item_id, label, price_cents, kind="size",
                    position=0) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_item_variants (item_id, label, price_cents, kind, "
        "position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (item_id, label, price_cents, kind, position),
    )
    conn.commit()
    return int(cur.lastrowid)


def _create_key(conn, label="test", rpm=60, restaurant_id=None):
    """Create an API key via storage layer; return (raw_key, record_dict)."""
    import storage.drafts as ds
    result = ds.create_api_key(label=label, rate_limit_rpm=rpm,
                               restaurant_id=restaurant_id)
    return result["raw_key"], result


# ---------------------------------------------------------------------------
# Flask test client fixture (API key auth, no session)
# ---------------------------------------------------------------------------
@pytest.fixture()
def api_client(fresh_db):
    """Flask test client for API key-authenticated endpoints."""
    from portal.app import app, _rate_limit_windows
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    _rate_limit_windows.clear()
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Import tested modules
# ---------------------------------------------------------------------------
import storage.drafts as drafts_store


# ===========================================================================
# SECTION 1: API Key CRUD
# ===========================================================================

class TestApiKeyCRUD:
    """Tests for create_api_key, validate_api_key, revoke_api_key."""

    def test_create_returns_raw_key(self, fresh_db):
        raw_key, record = _create_key(fresh_db)
        assert raw_key is not None
        assert len(raw_key) > 20
        assert record["id"] > 0

    def test_create_stores_label(self, fresh_db):
        _, record = _create_key(fresh_db, label="POS Terminal 1")
        assert record["label"] == "POS Terminal 1"

    def test_create_custom_rpm(self, fresh_db):
        _, record = _create_key(fresh_db, rpm=120)
        assert record["rate_limit_rpm"] == 120

    def test_create_with_restaurant_id(self, fresh_db):
        # Insert a restaurant first (FK target)
        fresh_db.execute(
            "INSERT INTO restaurants (name) VALUES (?)", ("Test Grill",))
        fresh_db.commit()
        _, record = _create_key(fresh_db, restaurant_id=1)
        assert record["restaurant_id"] == 1

    def test_validate_valid_key(self, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        result = drafts_store.validate_api_key(raw_key)
        assert result is not None
        assert result["active"] == 1

    def test_validate_invalid_key(self, fresh_db):
        result = drafts_store.validate_api_key("totally-bogus-key")
        assert result is None

    def test_validate_empty_key(self, fresh_db):
        assert drafts_store.validate_api_key("") is None
        assert drafts_store.validate_api_key(None) is None

    def test_revoke_deactivates(self, fresh_db):
        raw_key, record = _create_key(fresh_db)
        ok = drafts_store.revoke_api_key(record["id"])
        assert ok is True
        result = drafts_store.validate_api_key(raw_key)
        assert result is not None
        assert result["active"] == 0

    def test_multiple_keys_coexist(self, fresh_db):
        key1, _ = _create_key(fresh_db, label="key1")
        key2, _ = _create_key(fresh_db, label="key2")
        r1 = drafts_store.validate_api_key(key1)
        r2 = drafts_store.validate_api_key(key2)
        assert r1["label"] == "key1"
        assert r2["label"] == "key2"
        assert r1["id"] != r2["id"]

    def test_revoke_nonexistent_returns_false(self, fresh_db):
        ok = drafts_store.revoke_api_key(9999)
        assert ok is False


# ===========================================================================
# SECTION 2: Auth Decorator
# ===========================================================================

class TestAuthDecorator:
    """Tests for api_key_required decorator behavior."""

    def test_missing_key_401(self, api_client, fresh_db):
        did = _create_draft(fresh_db)
        resp = api_client.get(f"/api/drafts/{did}/items")
        assert resp.status_code == 401
        assert resp.get_json()["ok"] is False
        assert "Missing" in resp.get_json()["error"]

    def test_invalid_key_401(self, api_client, fresh_db):
        did = _create_draft(fresh_db)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": "bogus-key-here"},
        )
        assert resp.status_code == 401
        assert "Invalid" in resp.get_json()["error"]

    def test_revoked_key_403(self, api_client, fresh_db):
        raw_key, record = _create_key(fresh_db)
        drafts_store.revoke_api_key(record["id"])
        did = _create_draft(fresh_db)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 403
        assert "revoked" in resp.get_json()["error"]

    def test_valid_x_api_key_header(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_valid_bearer_header(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_rate_limit_headers_present(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert "X-RateLimit-Reset" in resp.headers


# ===========================================================================
# SECTION 3: Rate Limiting
# ===========================================================================

class TestRateLimiting:
    """Tests for sliding-window rate limiter."""

    def test_within_limit_allowed(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db, rpm=5)
        did = _create_draft(fresh_db)
        for _ in range(5):
            resp = api_client.get(
                f"/api/drafts/{did}/items",
                headers={"X-API-Key": raw_key},
            )
            assert resp.status_code == 200

    def test_exceeded_returns_429(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db, rpm=3)
        did = _create_draft(fresh_db)
        for _ in range(3):
            api_client.get(
                f"/api/drafts/{did}/items",
                headers={"X-API-Key": raw_key},
            )
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 429
        assert "Rate limit" in resp.get_json()["error"]
        assert "Retry-After" in resp.headers

    def test_different_keys_independent(self, api_client, fresh_db):
        key1, _ = _create_key(fresh_db, rpm=2, label="k1")
        key2, _ = _create_key(fresh_db, rpm=2, label="k2")
        did = _create_draft(fresh_db)
        # Exhaust key1
        for _ in range(2):
            api_client.get(f"/api/drafts/{did}/items",
                           headers={"X-API-Key": key1})
        resp1 = api_client.get(f"/api/drafts/{did}/items",
                               headers={"X-API-Key": key1})
        assert resp1.status_code == 429
        # key2 still works
        resp2 = api_client.get(f"/api/drafts/{did}/items",
                               headers={"X-API-Key": key2})
        assert resp2.status_code == 200

    def test_custom_rpm_respected(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db, rpm=2)
        did = _create_draft(fresh_db)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        assert resp.headers["X-RateLimit-Limit"] == "2"

    def test_remaining_decrements(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db, rpm=5)
        did = _create_draft(fresh_db)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        assert int(resp.headers["X-RateLimit-Remaining"]) == 4


# ===========================================================================
# SECTION 4: GET /api/drafts/<id>/items
# ===========================================================================

class TestGetDraftItems:
    """Tests for the GET endpoint."""

    def test_success_with_items(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        _insert_item(fresh_db, did, "Fries", 499, "Sides")
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["count"] == 2
        assert len(data["items"]) == 2

    def test_empty_draft(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert data["items"] == []
        assert data["count"] == 0

    def test_draft_not_found_404(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        resp = api_client.get(
            "/api/drafts/9999/items",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 404

    def test_returns_draft_id(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        assert resp.get_json()["draft_id"] == did

    def test_variants_nested(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza", 1299, "Entrees")
        _insert_variant(fresh_db, iid, "Small", 999, "size", 0)
        _insert_variant(fresh_db, iid, "Large", 1599, "size", 1)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        items = resp.get_json()["items"]
        assert len(items) == 1
        assert len(items[0]["variants"]) == 2
        assert items[0]["variants"][0]["label"] == "Small"
        assert items[0]["variants"][1]["label"] == "Large"

    def test_no_auth_401(self, api_client, fresh_db):
        did = _create_draft(fresh_db)
        resp = api_client.get(f"/api/drafts/{did}/items")
        assert resp.status_code == 401

    def test_approved_draft_readable(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db, status="approved")
        _insert_item(fresh_db, did, "Salad", 799)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 1


# ===========================================================================
# SECTION 5: POST /api/drafts/<id>/items
# ===========================================================================

class TestPostDraftItems:
    """Tests for the POST (create items) endpoint."""

    def test_create_items_201(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.post(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
            json={"items": [
                {"name": "Burger", "price_cents": 999, "category": "Entrees"},
                {"name": "Fries", "price_cents": 499, "category": "Sides"},
            ]},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["ok"] is True
        assert len(data["inserted_ids"]) == 2

    def test_create_with_variants(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.post(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
            json={"items": [{
                "name": "Pizza",
                "price_cents": 1299,
                "category": "Entrees",
                "_variants": [
                    {"label": "Small", "price_cents": 999, "kind": "size"},
                    {"label": "Large", "price_cents": 1599, "kind": "size"},
                ],
            }]},
        )
        assert resp.status_code == 201
        # Verify variants stored
        items = drafts_store.get_draft_items(did, include_variants=True)
        assert len(items) == 1
        assert len(items[0]["variants"]) == 2

    def test_draft_not_found_404(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        resp = api_client.post(
            "/api/drafts/9999/items",
            headers={"X-API-Key": raw_key},
            json={"items": [{"name": "X"}]},
        )
        assert resp.status_code == 404

    def test_approved_draft_403(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db, status="approved")
        resp = api_client.post(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
            json={"items": [{"name": "X"}]},
        )
        assert resp.status_code == 403
        assert "approved" in resp.get_json()["error"]

    def test_published_draft_403(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db, status="published")
        resp = api_client.post(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
            json={"items": [{"name": "X"}]},
        )
        assert resp.status_code == 403

    def test_non_json_400(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.post(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
            data="not json",
        )
        assert resp.status_code == 400
        assert "JSON" in resp.get_json()["error"]

    def test_missing_items_key_400(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.post(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
            json={"data": []},
        )
        assert resp.status_code == 400
        assert "items" in resp.get_json()["error"]

    def test_validation_error_400(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.post(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
            json={"items": [{"not_a_name": "bad"}]},
        )
        assert resp.status_code == 400
        assert "Validation" in resp.get_json()["error"]

    def test_empty_items_list_201(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.post(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
            json={"items": []},
        )
        assert resp.status_code == 201
        assert resp.get_json()["ok"] is True


# ===========================================================================
# SECTION 6: PUT /api/drafts/<id>/items/<item_id>
# ===========================================================================

class TestPutDraftItem:
    """Tests for the PUT (update single item) endpoint."""

    def test_update_item(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = api_client.put(
            f"/api/drafts/{did}/items/{iid}",
            headers={"X-API-Key": raw_key},
            json={"name": "Cheeseburger", "price_cents": 1099},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert iid in data["updated_ids"]

    def test_update_with_variants(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Pizza", 1299, "Entrees")
        resp = api_client.put(
            f"/api/drafts/{did}/items/{iid}",
            headers={"X-API-Key": raw_key},
            json={
                "name": "Pizza",
                "price_cents": 1299,
                "_variants": [
                    {"label": "Personal", "price_cents": 899, "kind": "size"},
                    {"label": "Family", "price_cents": 2199, "kind": "size"},
                ],
            },
        )
        assert resp.status_code == 200
        items = drafts_store.get_draft_items(did, include_variants=True)
        assert len(items[0]["variants"]) == 2

    def test_draft_not_found_404(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        resp = api_client.put(
            "/api/drafts/9999/items/1",
            headers={"X-API-Key": raw_key},
            json={"name": "X"},
        )
        assert resp.status_code == 404
        assert "Draft" in resp.get_json()["error"]

    def test_item_not_in_draft_404(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999)
        # Try to update item 9999 which doesn't exist
        resp = api_client.put(
            f"/api/drafts/{did}/items/9999",
            headers={"X-API-Key": raw_key},
            json={"name": "Ghost"},
        )
        assert resp.status_code == 404
        assert "Item" in resp.get_json()["error"]

    def test_item_in_other_draft_404(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did1 = _create_draft(fresh_db, title="Draft 1")
        did2 = _create_draft(fresh_db, title="Draft 2")
        iid = _insert_item(fresh_db, did1, "Burger", 999)
        # Try to update item from draft1 via draft2's URL
        resp = api_client.put(
            f"/api/drafts/{did2}/items/{iid}",
            headers={"X-API-Key": raw_key},
            json={"name": "Stolen"},
        )
        assert resp.status_code == 404

    def test_approved_draft_403(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db, status="approved")
        iid = _insert_item(fresh_db, did, "Burger", 999)
        resp = api_client.put(
            f"/api/drafts/{did}/items/{iid}",
            headers={"X-API-Key": raw_key},
            json={"name": "Updated"},
        )
        assert resp.status_code == 403

    def test_non_json_400(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger", 999)
        resp = api_client.put(
            f"/api/drafts/{did}/items/{iid}",
            headers={"X-API-Key": raw_key},
            data="not json",
        )
        assert resp.status_code == 400

    def test_validation_error_400(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger", 999)
        resp = api_client.put(
            f"/api/drafts/{did}/items/{iid}",
            headers={"X-API-Key": raw_key},
            json={"name": 12345},  # name must be string
        )
        assert resp.status_code == 400


# ===========================================================================
# SECTION 7: Response Consistency
# ===========================================================================

class TestResponseConsistency:
    """Verify all responses follow the standard envelope."""

    def test_success_has_ok_true(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        assert resp.get_json()["ok"] is True

    def test_error_has_ok_false_and_error(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        resp = api_client.get(
            "/api/drafts/9999/items",
            headers={"X-API-Key": raw_key},
        )
        data = resp.get_json()
        assert data["ok"] is False
        assert "error" in data

    def test_401_has_ok_false(self, api_client, fresh_db):
        did = _create_draft(fresh_db)
        resp = api_client.get(f"/api/drafts/{did}/items")
        data = resp.get_json()
        assert data["ok"] is False

    def test_json_content_type(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        resp = api_client.get(
            f"/api/drafts/{did}/items",
            headers={"X-API-Key": raw_key},
        )
        assert "application/json" in resp.content_type

    def test_rate_limit_headers_on_all_methods(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        did = _create_draft(fresh_db)
        iid = _insert_item(fresh_db, did, "Burger", 999)
        # GET
        r1 = api_client.get(f"/api/drafts/{did}/items",
                            headers={"X-API-Key": raw_key})
        assert "X-RateLimit-Limit" in r1.headers
        # POST
        r2 = api_client.post(f"/api/drafts/{did}/items",
                             headers={"X-API-Key": raw_key},
                             json={"items": []})
        assert "X-RateLimit-Limit" in r2.headers
        # PUT
        r3 = api_client.put(f"/api/drafts/{did}/items/{iid}",
                            headers={"X-API-Key": raw_key},
                            json={"name": "Burger", "price_cents": 999})
        assert "X-RateLimit-Limit" in r3.headers
