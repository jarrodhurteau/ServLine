"""
Day 85 -- Webhooks, API Documentation & Sprint 9.4 Wrap-Up.

Sprint 9.4, Day 85: Webhook notifications on draft approval/export,
public API documentation page, and Sprint 9.4 completion.

Covers:
  Webhook CRUD (storage layer):
  - register_webhook() returns id and secret
  - register_webhook() stores url
  - register_webhook() stores event types
  - register_webhook() with restaurant_id
  - register_webhook() rejects invalid event types
  - register_webhook() rejects empty event types
  - list_webhooks() returns active webhooks
  - list_webhooks() excludes secrets
  - list_webhooks() filters by restaurant_id
  - get_webhook() by id
  - delete_webhook() removes row
  - delete_webhook() nonexistent returns false

  Event Matching:
  - get_webhooks_for_event() matches draft.approved
  - get_webhooks_for_event() matches draft.exported
  - get_webhooks_for_event() no match for wrong event
  - get_webhooks_for_event() filters by restaurant_id
  - Global webhook (restaurant_id=None) matches any restaurant
  - Inactive webhook excluded from matching

  Webhook Dispatch:
  - fire_webhooks() returns count
  - fire_webhooks() returns zero when no hooks
  - fire_webhooks() sends POST request
  - fire_webhooks() includes signature header
  - fire_webhooks() signature is valid HMAC
  - fire_webhooks() includes event header
  - fire_webhooks() tolerates HTTP errors

  Webhook API Endpoints:
  - POST /api/webhooks 201 success
  - POST /api/webhooks returns secret
  - POST /api/webhooks missing url 400
  - POST /api/webhooks invalid url 400
  - POST /api/webhooks missing event_types 400
  - POST /api/webhooks invalid event_types 400
  - POST /api/webhooks no auth 401
  - GET /api/webhooks empty list
  - GET /api/webhooks after register
  - GET /api/webhooks no auth 401
  - DELETE /api/webhooks success
  - DELETE /api/webhooks not found 404
  - DELETE /api/webhooks no auth 401
  - Rate limit headers on webhook endpoints

  Approve/Export Integration:
  - approve_export fires draft.approved webhook
  - approve_export fires draft.exported webhook
  - approve_export webhook payload has draft_id
  - approve_export webhook payload has counts
  - approve_export continues if webhook fails
  - approve_export with restaurant-scoped webhook
  - No webhooks registered still works
  - Both events fired on single approve

  API Documentation Page:
  - GET /api/docs returns 200
  - GET /api/docs no auth required
  - GET /api/docs contains authentication section
  - GET /api/docs contains endpoints section
  - GET /api/docs contains webhooks section

  HMAC Signature:
  - Signature matches manual computation
  - Signature changes with different secret
  - Signature changes with different body
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
import pytest
from typing import Optional
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 84 tests)
# ---------------------------------------------------------------------------
_TEST_CONN: Optional[sqlite3.Connection] = None


def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with full schema incl. webhooks."""
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
        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER,
            url TEXT NOT NULL,
            event_types TEXT NOT NULL DEFAULT '',
            secret TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_webhooks_restaurant ON webhooks(restaurant_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_webhooks_active ON webhooks(active)")
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
def _create_restaurant(conn, name="Test Restaurant") -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO restaurants (name, created_at) VALUES (?, datetime('now'))",
        (name,),
    )
    conn.commit()
    return int(cur.lastrowid)


def _create_draft(conn, title="Test Draft", status="editing",
                  restaurant_id=None) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO drafts (title, status, restaurant_id, created_at, updated_at) "
        "VALUES (?, ?, ?, datetime('now'), datetime('now'))",
        (title, status, restaurant_id),
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
# Flask test client fixtures
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


@pytest.fixture()
def auth_client(fresh_db):
    """Flask test client with session auth for web endpoints."""
    from portal.app import app, _rate_limit_windows
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    _rate_limit_windows.clear()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = {"username": "admin", "role": "admin"}
        yield c


# ===========================================================================
# SECTION 1: Webhook CRUD (storage layer)
# ===========================================================================
class TestWebhookCRUD:

    def test_register_returns_id_and_secret(self, fresh_db):
        import storage.drafts as ds
        result = ds.register_webhook(
            url="https://example.com/hook",
            event_types=["draft.approved"],
        )
        assert "id" in result
        assert "secret" in result
        assert isinstance(result["id"], int)
        assert len(result["secret"]) > 10

    def test_register_stores_url(self, fresh_db):
        import storage.drafts as ds
        result = ds.register_webhook(
            url="https://example.com/my-hook",
            event_types=["draft.approved"],
        )
        hook = ds.get_webhook(result["id"])
        assert hook["url"] == "https://example.com/my-hook"

    def test_register_stores_event_types(self, fresh_db):
        import storage.drafts as ds
        result = ds.register_webhook(
            url="https://example.com/hook",
            event_types=["draft.approved", "draft.exported"],
        )
        hook = ds.get_webhook(result["id"])
        assert set(hook["event_types"]) == {"draft.approved", "draft.exported"}

    def test_register_with_restaurant_id(self, fresh_db):
        import storage.drafts as ds
        rid = _create_restaurant(fresh_db, "Pizza Place")
        result = ds.register_webhook(
            url="https://example.com/hook",
            event_types=["draft.approved"],
            restaurant_id=rid,
        )
        hook = ds.get_webhook(result["id"])
        assert hook["restaurant_id"] == rid

    def test_register_invalid_event_types_raises(self, fresh_db):
        import storage.drafts as ds
        with pytest.raises(ValueError, match="No valid event types"):
            ds.register_webhook(
                url="https://example.com/hook",
                event_types=["bogus.event"],
            )

    def test_register_empty_event_types_raises(self, fresh_db):
        import storage.drafts as ds
        with pytest.raises(ValueError, match="No valid event types"):
            ds.register_webhook(
                url="https://example.com/hook",
                event_types=[],
            )

    def test_list_returns_active_webhooks(self, fresh_db):
        import storage.drafts as ds
        ds.register_webhook(url="https://a.com/h1", event_types=["draft.approved"])
        ds.register_webhook(url="https://b.com/h2", event_types=["draft.exported"])
        hooks = ds.list_webhooks()
        assert len(hooks) == 2
        urls = {h["url"] for h in hooks}
        assert "https://a.com/h1" in urls
        assert "https://b.com/h2" in urls

    def test_list_excludes_secret(self, fresh_db):
        import storage.drafts as ds
        ds.register_webhook(url="https://a.com/h1", event_types=["draft.approved"])
        hooks = ds.list_webhooks()
        for h in hooks:
            assert "secret" not in h

    def test_list_filters_by_restaurant_id(self, fresh_db):
        import storage.drafts as ds
        rid1 = _create_restaurant(fresh_db, "R1")
        rid2 = _create_restaurant(fresh_db, "R2")
        ds.register_webhook(url="https://a.com/h1", event_types=["draft.approved"],
                            restaurant_id=rid1)
        ds.register_webhook(url="https://b.com/h2", event_types=["draft.approved"],
                            restaurant_id=rid2)
        hooks = ds.list_webhooks(restaurant_id=rid1)
        assert len(hooks) == 1
        assert hooks[0]["url"] == "https://a.com/h1"

    def test_get_webhook_by_id(self, fresh_db):
        import storage.drafts as ds
        result = ds.register_webhook(
            url="https://example.com/hook",
            event_types=["draft.approved"],
        )
        hook = ds.get_webhook(result["id"])
        assert hook is not None
        assert hook["url"] == "https://example.com/hook"
        assert hook["active"] == 1

    def test_delete_webhook_removes_row(self, fresh_db):
        import storage.drafts as ds
        result = ds.register_webhook(
            url="https://example.com/hook",
            event_types=["draft.approved"],
        )
        assert ds.delete_webhook(result["id"]) is True
        assert ds.get_webhook(result["id"]) is None

    def test_delete_nonexistent_returns_false(self, fresh_db):
        import storage.drafts as ds
        assert ds.delete_webhook(99999) is False


# ===========================================================================
# SECTION 2: Event Matching
# ===========================================================================
class TestWebhookEventMatching:

    def test_matches_approved_event(self, fresh_db):
        import storage.drafts as ds
        ds.register_webhook(url="https://a.com/h", event_types=["draft.approved"])
        hooks = ds.get_webhooks_for_event(None, "draft.approved")
        assert len(hooks) == 1

    def test_matches_exported_event(self, fresh_db):
        import storage.drafts as ds
        ds.register_webhook(url="https://a.com/h", event_types=["draft.exported"])
        hooks = ds.get_webhooks_for_event(None, "draft.exported")
        assert len(hooks) == 1

    def test_no_match_for_wrong_event(self, fresh_db):
        import storage.drafts as ds
        ds.register_webhook(url="https://a.com/h", event_types=["draft.approved"])
        hooks = ds.get_webhooks_for_event(None, "draft.exported")
        assert len(hooks) == 0

    def test_filters_by_restaurant_id(self, fresh_db):
        import storage.drafts as ds
        rid1 = _create_restaurant(fresh_db, "R1")
        rid2 = _create_restaurant(fresh_db, "R2")
        ds.register_webhook(url="https://a.com/h", event_types=["draft.approved"],
                            restaurant_id=rid1)
        ds.register_webhook(url="https://b.com/h", event_types=["draft.approved"],
                            restaurant_id=rid2)
        hooks = ds.get_webhooks_for_event(rid1, "draft.approved")
        assert len(hooks) == 1
        assert hooks[0]["url"] == "https://a.com/h"

    def test_global_webhook_matches_any_restaurant(self, fresh_db):
        import storage.drafts as ds
        rid = _create_restaurant(fresh_db, "R1")
        ds.register_webhook(url="https://global.com/h", event_types=["draft.approved"])
        hooks = ds.get_webhooks_for_event(rid, "draft.approved")
        assert len(hooks) == 1
        assert hooks[0]["url"] == "https://global.com/h"

    def test_inactive_webhook_excluded(self, fresh_db):
        import storage.drafts as ds
        result = ds.register_webhook(url="https://a.com/h", event_types=["draft.approved"])
        # Deactivate by direct SQL
        fresh_db.execute("UPDATE webhooks SET active=0 WHERE id=?", (result["id"],))
        fresh_db.commit()
        hooks = ds.get_webhooks_for_event(None, "draft.approved")
        assert len(hooks) == 0


# ===========================================================================
# SECTION 3: Webhook Dispatch
# ===========================================================================
class TestWebhookDispatch:

    def test_fire_returns_count(self, fresh_db):
        import storage.drafts as ds
        ds.register_webhook(url="https://a.com/h", event_types=["draft.approved"])
        ds.register_webhook(url="https://b.com/h", event_types=["draft.approved"])
        with patch("storage.drafts.urllib.request.urlopen"):
            count = ds.fire_webhooks(None, "draft.approved", {"test": True})
        assert count == 2

    def test_fire_no_hooks_returns_zero(self, fresh_db):
        import storage.drafts as ds
        count = ds.fire_webhooks(None, "draft.approved", {"test": True})
        assert count == 0

    def test_fire_sends_post_request(self, fresh_db):
        import storage.drafts as ds
        ds.register_webhook(url="https://example.com/hook",
                            event_types=["draft.approved"])
        with patch("storage.drafts.urllib.request.urlopen") as mock_open:
            ds.fire_webhooks(None, "draft.approved", {"key": "value"})
            time.sleep(0.3)
            mock_open.assert_called_once()
            req = mock_open.call_args[0][0]
            assert req.method == "POST"
            assert req.full_url == "https://example.com/hook"

    def test_fire_includes_signature_header(self, fresh_db):
        import storage.drafts as ds
        result = ds.register_webhook(url="https://example.com/hook",
                                     event_types=["draft.approved"])
        with patch("storage.drafts.urllib.request.urlopen") as mock_open:
            ds.fire_webhooks(None, "draft.approved", {"key": "value"})
            time.sleep(0.3)
            req = mock_open.call_args[0][0]
            # urllib normalizes header case: "X-webhook-signature"
            assert req.get_header("X-webhook-signature") is not None

    def test_fire_signature_is_valid_hmac(self, fresh_db):
        import storage.drafts as ds
        result = ds.register_webhook(url="https://example.com/hook",
                                     event_types=["draft.approved"])
        secret = result["secret"]
        payload = {"key": "value"}
        body = json.dumps(payload, default=str)
        expected_sig = hmac.new(
            secret.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        with patch("storage.drafts.urllib.request.urlopen") as mock_open:
            ds.fire_webhooks(None, "draft.approved", payload)
            time.sleep(0.3)
            req = mock_open.call_args[0][0]
            actual_sig = req.get_header("X-webhook-signature")
            assert actual_sig == expected_sig

    def test_fire_includes_event_header(self, fresh_db):
        import storage.drafts as ds
        ds.register_webhook(url="https://example.com/hook",
                            event_types=["draft.approved"])
        with patch("storage.drafts.urllib.request.urlopen") as mock_open:
            ds.fire_webhooks(None, "draft.approved", {"key": "value"})
            time.sleep(0.3)
            req = mock_open.call_args[0][0]
            assert req.get_header("X-webhook-event") == "draft.approved"

    def test_fire_tolerates_http_error(self, fresh_db):
        import storage.drafts as ds
        ds.register_webhook(url="https://example.com/hook",
                            event_types=["draft.approved"])
        with patch("storage.drafts.urllib.request.urlopen",
                   side_effect=Exception("Connection refused")):
            count = ds.fire_webhooks(None, "draft.approved", {"key": "value"})
            assert count == 1  # still returns count, failure is silent
            time.sleep(0.3)  # let thread finish


# ===========================================================================
# SECTION 4: Webhook API Endpoints
# ===========================================================================
class TestWebhookAPIEndpoints:

    def test_register_webhook_201(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        resp = api_client.post("/api/webhooks",
            json={"url": "https://example.com/hook",
                  "event_types": ["draft.approved"]},
            headers={"X-API-Key": raw_key})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["ok"] is True
        assert "webhook" in data
        assert data["webhook"]["url"] == "https://example.com/hook"

    def test_register_webhook_returns_secret(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        resp = api_client.post("/api/webhooks",
            json={"url": "https://example.com/hook",
                  "event_types": ["draft.approved"]},
            headers={"X-API-Key": raw_key})
        data = resp.get_json()
        assert "secret" in data["webhook"]
        assert len(data["webhook"]["secret"]) > 10

    def test_register_missing_url_400(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        resp = api_client.post("/api/webhooks",
            json={"event_types": ["draft.approved"]},
            headers={"X-API-Key": raw_key})
        assert resp.status_code == 400
        assert "url" in resp.get_json()["error"].lower()

    def test_register_invalid_url_400(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        resp = api_client.post("/api/webhooks",
            json={"url": "ftp://bad.com/hook",
                  "event_types": ["draft.approved"]},
            headers={"X-API-Key": raw_key})
        assert resp.status_code == 400

    def test_register_missing_event_types_400(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        resp = api_client.post("/api/webhooks",
            json={"url": "https://example.com/hook"},
            headers={"X-API-Key": raw_key})
        assert resp.status_code == 400

    def test_register_invalid_event_types_400(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        resp = api_client.post("/api/webhooks",
            json={"url": "https://example.com/hook",
                  "event_types": ["bogus.event"]},
            headers={"X-API-Key": raw_key})
        assert resp.status_code == 400

    def test_register_no_auth_401(self, api_client):
        resp = api_client.post("/api/webhooks",
            json={"url": "https://example.com/hook",
                  "event_types": ["draft.approved"]})
        assert resp.status_code == 401

    def test_list_webhooks_empty(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        resp = api_client.get("/api/webhooks",
            headers={"X-API-Key": raw_key})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["webhooks"] == []
        assert data["count"] == 0

    def test_list_webhooks_after_register(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        api_client.post("/api/webhooks",
            json={"url": "https://example.com/hook",
                  "event_types": ["draft.approved"]},
            headers={"X-API-Key": raw_key})
        resp = api_client.get("/api/webhooks",
            headers={"X-API-Key": raw_key})
        data = resp.get_json()
        assert data["count"] == 1
        assert data["webhooks"][0]["url"] == "https://example.com/hook"

    def test_list_webhooks_no_auth_401(self, api_client):
        resp = api_client.get("/api/webhooks")
        assert resp.status_code == 401

    def test_delete_webhook_success(self, api_client, fresh_db):
        import storage.drafts as ds
        raw_key, _ = _create_key(fresh_db)
        hook = ds.register_webhook(url="https://example.com/hook",
                                   event_types=["draft.approved"])
        resp = api_client.delete(f"/api/webhooks/{hook['id']}",
            headers={"X-API-Key": raw_key})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert resp.get_json()["deleted"] is True

    def test_delete_webhook_not_found_404(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db)
        resp = api_client.delete("/api/webhooks/99999",
            headers={"X-API-Key": raw_key})
        assert resp.status_code == 404

    def test_delete_webhook_no_auth_401(self, api_client):
        resp = api_client.delete("/api/webhooks/1")
        assert resp.status_code == 401

    def test_rate_limit_headers_on_webhook_endpoints(self, api_client, fresh_db):
        raw_key, _ = _create_key(fresh_db, rpm=60)
        resp = api_client.get("/api/webhooks",
            headers={"X-API-Key": raw_key})
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert "X-RateLimit-Reset" in resp.headers


# ===========================================================================
# SECTION 5: Approve/Export Webhook Integration
# ===========================================================================
class TestWebhookApproveExportIntegration:

    def _setup_draft_with_items(self, conn, restaurant_id=None):
        """Create a draft with items and return draft_id."""
        draft_id = _create_draft(conn, "Menu", status="editing",
                                 restaurant_id=restaurant_id)
        item_id = _insert_item(conn, draft_id, "Burger", price_cents=999,
                               category="Burgers")
        _insert_variant(conn, item_id, "Single", 999, kind="size", position=0)
        _insert_variant(conn, item_id, "Double", 1399, kind="size", position=1)
        return draft_id

    def test_approve_fires_approved_event(self, auth_client, fresh_db):
        draft_id = self._setup_draft_with_items(fresh_db)
        with patch("storage.drafts.fire_webhooks") as mock_fire:
            # We need to patch at the portal.app level where it's called
            pass
        # Use the actual flow and check via mock on fire_webhooks
        import storage.drafts as ds
        ds.register_webhook(url="https://example.com/hook",
                            event_types=["draft.approved"])
        with patch("storage.drafts.urllib.request.urlopen") as mock_open:
            resp = auth_client.post(f"/drafts/{draft_id}/approve_export")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            time.sleep(0.3)
            # Should have been called at least once for draft.approved
            calls = mock_open.call_args_list
            events = []
            for c in calls:
                req = c[0][0]
                events.append(req.get_header("X-webhook-event"))
            assert "draft.approved" in events

    def test_approve_fires_exported_event(self, auth_client, fresh_db):
        draft_id = self._setup_draft_with_items(fresh_db)
        import storage.drafts as ds
        ds.register_webhook(url="https://example.com/hook",
                            event_types=["draft.exported"])
        with patch("storage.drafts.urllib.request.urlopen") as mock_open:
            resp = auth_client.post(f"/drafts/{draft_id}/approve_export")
            assert resp.status_code == 200
            time.sleep(0.3)
            calls = mock_open.call_args_list
            events = []
            for c in calls:
                req = c[0][0]
                events.append(req.get_header("X-webhook-event"))
            assert "draft.exported" in events

    def test_approve_webhook_payload_has_draft_id(self, auth_client, fresh_db):
        draft_id = self._setup_draft_with_items(fresh_db)
        import storage.drafts as ds
        ds.register_webhook(url="https://example.com/hook",
                            event_types=["draft.approved"])
        with patch("storage.drafts.urllib.request.urlopen") as mock_open:
            auth_client.post(f"/drafts/{draft_id}/approve_export")
            time.sleep(0.3)
            req = mock_open.call_args_list[0][0][0]
            body = json.loads(req.data.decode("utf-8"))
            assert body["draft_id"] == draft_id

    def test_approve_webhook_payload_has_counts(self, auth_client, fresh_db):
        draft_id = self._setup_draft_with_items(fresh_db)
        import storage.drafts as ds
        ds.register_webhook(url="https://example.com/hook",
                            event_types=["draft.approved"])
        with patch("storage.drafts.urllib.request.urlopen") as mock_open:
            auth_client.post(f"/drafts/{draft_id}/approve_export")
            time.sleep(0.3)
            req = mock_open.call_args_list[0][0][0]
            body = json.loads(req.data.decode("utf-8"))
            assert "item_count" in body
            assert "variant_count" in body
            assert body["item_count"] >= 1
            assert body["variant_count"] >= 2

    def test_approve_continues_if_webhook_fails(self, auth_client, fresh_db):
        draft_id = self._setup_draft_with_items(fresh_db)
        import storage.drafts as ds
        ds.register_webhook(url="https://example.com/hook",
                            event_types=["draft.approved", "draft.exported"])
        with patch("storage.drafts.urllib.request.urlopen",
                   side_effect=Exception("Connection refused")):
            resp = auth_client.post(f"/drafts/{draft_id}/approve_export")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert "pos_json" in data

    def test_approve_with_restaurant_scoped_webhook(self, auth_client, fresh_db):
        rid = _create_restaurant(fresh_db, "Pizza Place")
        draft_id = self._setup_draft_with_items(fresh_db, restaurant_id=rid)
        import storage.drafts as ds
        ds.register_webhook(url="https://example.com/hook",
                            event_types=["draft.approved"],
                            restaurant_id=rid)
        with patch("storage.drafts.urllib.request.urlopen") as mock_open:
            resp = auth_client.post(f"/drafts/{draft_id}/approve_export")
            assert resp.status_code == 200
            time.sleep(0.3)
            assert mock_open.called

    def test_no_webhooks_registered_still_works(self, auth_client, fresh_db):
        draft_id = self._setup_draft_with_items(fresh_db)
        resp = auth_client.post(f"/drafts/{draft_id}/approve_export")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "pos_json" in data

    def test_both_events_fired_on_approve(self, auth_client, fresh_db):
        draft_id = self._setup_draft_with_items(fresh_db)
        import storage.drafts as ds
        ds.register_webhook(url="https://example.com/hook",
                            event_types=["draft.approved", "draft.exported"])
        with patch("storage.drafts.urllib.request.urlopen") as mock_open:
            auth_client.post(f"/drafts/{draft_id}/approve_export")
            time.sleep(0.3)
            calls = mock_open.call_args_list
            events = set()
            for c in calls:
                req = c[0][0]
                events.add(req.get_header("X-webhook-event"))
            assert "draft.approved" in events
            assert "draft.exported" in events


# ===========================================================================
# SECTION 6: API Documentation Page
# ===========================================================================
class TestAPIDocsPage:

    def test_api_docs_returns_200(self, api_client):
        resp = api_client.get("/api/docs")
        assert resp.status_code == 200

    def test_api_docs_no_auth_required(self, api_client):
        # No API key header, should still work
        resp = api_client.get("/api/docs")
        assert resp.status_code == 200

    def test_api_docs_contains_authentication_section(self, api_client):
        resp = api_client.get("/api/docs")
        html = resp.data.decode("utf-8")
        assert "Authentication" in html
        assert "X-API-Key" in html

    def test_api_docs_contains_endpoints_section(self, api_client):
        resp = api_client.get("/api/docs")
        html = resp.data.decode("utf-8")
        assert "/api/drafts/" in html
        assert "GET" in html
        assert "POST" in html
        assert "PUT" in html

    def test_api_docs_contains_webhooks_section(self, api_client):
        resp = api_client.get("/api/docs")
        html = resp.data.decode("utf-8")
        assert "Webhook" in html
        assert "draft.approved" in html
        assert "draft.exported" in html
        assert "X-Webhook-Signature" in html


# ===========================================================================
# SECTION 7: HMAC Signature Verification
# ===========================================================================
class TestHMACSignature:

    def test_signature_matches_manual_computation(self, fresh_db):
        import storage.drafts as ds
        result = ds.register_webhook(url="https://example.com/hook",
                                     event_types=["draft.approved"])
        secret = result["secret"]
        payload = {"event": "draft.approved", "draft_id": 1}
        body = json.dumps(payload, default=str)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        with patch("storage.drafts.urllib.request.urlopen") as mock_open:
            ds.fire_webhooks(None, "draft.approved", payload)
            time.sleep(0.3)
            req = mock_open.call_args[0][0]
            actual = req.get_header("X-webhook-signature")
            assert actual == expected

    def test_signature_changes_with_different_secret(self, fresh_db):
        import storage.drafts as ds
        r1 = ds.register_webhook(url="https://a.com/h", event_types=["draft.approved"])
        r2 = ds.register_webhook(url="https://b.com/h", event_types=["draft.approved"])
        assert r1["secret"] != r2["secret"]
        payload = {"test": True}
        body = json.dumps(payload, default=str)
        sig1 = hmac.new(r1["secret"].encode(), body.encode(), hashlib.sha256).hexdigest()
        sig2 = hmac.new(r2["secret"].encode(), body.encode(), hashlib.sha256).hexdigest()
        assert sig1 != sig2

    def test_signature_changes_with_different_body(self, fresh_db):
        secret = "test-secret-key"
        body1 = json.dumps({"a": 1}, default=str)
        body2 = json.dumps({"b": 2}, default=str)
        sig1 = hmac.new(secret.encode(), body1.encode(), hashlib.sha256).hexdigest()
        sig2 = hmac.new(secret.encode(), body2.encode(), hashlib.sha256).hexdigest()
        assert sig1 != sig2
