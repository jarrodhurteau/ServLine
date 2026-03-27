# tests/test_day131_free_tier_access.py
"""
Day 131 — Sprint 13.1: Free Tier Access Controls.

Deliverables:
  1. account_tier column on users table
  2. set_user_tier() / get_user_tier() CRUD
  3. check_feature_access() per-tier gating
  4. /choose-plan page (GET + POST)
  5. Registration redirects to /choose-plan
  6. Login redirects to /choose-plan when no tier chosen
  7. Dashboard requires tier chosen
  8. OCR image upload gated to premium tier
  9. POS exports require tier chosen
  10. Template context includes account_tier

32 tests across 8 classes:
  1. Tier CRUD (4)
  2. Feature access checks (4)
  3. Choose plan page GET (4)
  4. Choose plan page POST (4)
  5. Registration flow redirect (4)
  6. Login flow tier handling (4)
  7. Dashboard tier gate (4)
  8. Route gating by tier (4)
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
    conn.execute("INSERT INTO restaurants (id, name, phone, address) VALUES (1, 'Test Bistro', '555-1234', '123 Main St')")
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
    """Register a customer (ends up on /choose-plan, no tier yet)."""
    client.post("/register", data={
        "email": email,
        "password": password,
        "confirm_password": password,
        "display_name": "Test Customer",
    })
    return client


def _register_and_choose_tier(client, tier="free", email="cust@example.com", password="securepass1"):
    """Register + pick a tier so customer lands on dashboard."""
    _register_customer(client, email=email, password=password)
    client.post("/choose-plan", data={"tier": tier})
    return client


def _create_user_directly(mock_db, email="direct@example.com", password="securepass1"):
    """Create a user directly via users_store (no portal)."""
    import storage.users as _users
    return _users.create_user(email, password, display_name="Direct User")


def _login_customer(client, email="cust@example.com", password="securepass1"):
    """Log in as an existing customer."""
    return client.post("/login", data={
        "email": email,
        "password": password,
    }, follow_redirects=False)


# ===========================================================================
# 1. Tier CRUD (4 tests)
# ===========================================================================
class TestTierCRUD:
    """Test set_user_tier / get_user_tier in storage layer."""

    def test_new_user_has_no_tier(self, mock_db):
        import storage.users as _users
        user = _users.create_user("new@x.com", "password123")
        assert _users.get_user_tier(user["id"]) is None

    def test_set_tier_free(self, mock_db):
        import storage.users as _users
        user = _users.create_user("free@x.com", "password123")
        result = _users.set_user_tier(user["id"], "free")
        assert result is True
        assert _users.get_user_tier(user["id"]) == "free"

    def test_set_tier_premium(self, mock_db):
        import storage.users as _users
        user = _users.create_user("light@x.com", "password123")
        result = _users.set_user_tier(user["id"], "premium")
        assert result is True
        assert _users.get_user_tier(user["id"]) == "premium"

    def test_set_invalid_tier_raises(self, mock_db):
        import storage.users as _users
        user = _users.create_user("bad@x.com", "password123")
        with pytest.raises(ValueError, match="Invalid tier"):
            _users.set_user_tier(user["id"], "gold")


# ===========================================================================
# 2. Feature access checks (4 tests)
# ===========================================================================
class TestFeatureAccess:
    """Test check_feature_access for free vs premium tiers."""

    def test_free_tier_has_editor(self, mock_db):
        import storage.users as _users
        user = _users.create_user("f@x.com", "password123")
        _users.set_user_tier(user["id"], "free")
        assert _users.check_feature_access(user["id"], "editor") is True
        assert _users.check_feature_access(user["id"], "csv_json_export") is True
        assert _users.check_feature_access(user["id"], "pos_export") is True

    def test_free_tier_no_ai_parse(self, mock_db):
        import storage.users as _users
        user = _users.create_user("f2@x.com", "password123")
        _users.set_user_tier(user["id"], "free")
        assert _users.check_feature_access(user["id"], "ai_parse") is False
        assert _users.check_feature_access(user["id"], "ocr_upload") is False
        assert _users.check_feature_access(user["id"], "wizard") is False

    def test_premium_has_everything(self, mock_db):
        import storage.users as _users
        user = _users.create_user("l@x.com", "password123")
        _users.set_user_tier(user["id"], "premium")
        for feat in ("editor", "csv_json_export", "pos_export", "ai_parse", "ocr_upload", "wizard"):
            assert _users.check_feature_access(user["id"], feat) is True

    def test_no_tier_no_access(self, mock_db):
        import storage.users as _users
        user = _users.create_user("none@x.com", "password123")
        assert _users.check_feature_access(user["id"], "editor") is False


# ===========================================================================
# 3. Choose plan page GET (4 tests)
# ===========================================================================
class TestChoosePlanGet:
    """Test /choose-plan GET rendering."""

    def test_unauthenticated_redirects_to_login(self, app_client):
        resp = app_client.get("/choose-plan")
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("Location", "")

    def test_authenticated_no_tier_shows_page(self, app_client):
        _register_customer(app_client)
        resp = app_client.get("/choose-plan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Continue Free" in html
        assert "Premium Package" in html

    def test_page_shows_free_description(self, app_client):
        _register_customer(app_client)
        resp = app_client.get("/choose-plan")
        html = resp.data.decode()
        assert "menu editor" in html.lower() or "editor" in html.lower()
        assert "Excel" in html or "CSV" in html

    def test_already_has_tier_can_still_view_plans(self, app_client):
        _register_and_choose_tier(app_client, tier="free")
        resp = app_client.get("/choose-plan")
        assert resp.status_code == 200


# ===========================================================================
# 4. Choose plan page POST (4 tests)
# ===========================================================================
class TestChoosePlanPost:
    """Test /choose-plan POST sets tier correctly."""

    def test_choose_free_sets_tier_and_redirects(self, app_client, mock_db):
        _register_customer(app_client)
        resp = app_client.post("/choose-plan", data={"tier": "free"})
        assert resp.status_code in (302, 303)
        assert "/import" in resp.headers.get("Location", "")

    def test_choose_premium_sets_tier(self, app_client, mock_db):
        _register_customer(app_client)
        resp = app_client.post("/choose-plan", data={"tier": "premium"})
        assert resp.status_code in (302, 303)
        # Verify in DB
        import storage.users as _users
        conn = mock_db()
        row = conn.execute("SELECT account_tier FROM users WHERE email = 'cust@example.com'").fetchone()
        conn.close()
        assert row["account_tier"] == "premium"

    def test_choose_invalid_tier_stays_on_page(self, app_client):
        _register_customer(app_client)
        resp = app_client.post("/choose-plan", data={"tier": "platinum"}, follow_redirects=True)
        html = resp.data.decode()
        assert "Please select a plan" in html or "Continue Free" in html

    def test_choose_empty_tier_stays_on_page(self, app_client):
        _register_customer(app_client)
        resp = app_client.post("/choose-plan", data={"tier": ""}, follow_redirects=True)
        html = resp.data.decode()
        assert "Please select a plan" in html or "Continue Free" in html


# ===========================================================================
# 5. Registration flow redirect (4 tests)
# ===========================================================================
class TestRegistrationRedirect:
    """Test that registration redirects to /choose-plan."""

    def test_register_redirects_to_choose_plan(self, app_client):
        resp = app_client.post("/register", data={
            "email": "new@example.com",
            "password": "securepass1",
            "confirm_password": "securepass1",
            "display_name": "New User",
        })
        assert resp.status_code in (302, 303)
        assert "/choose-plan" in resp.headers.get("Location", "")

    def test_register_then_follow_lands_on_choose_plan(self, app_client):
        resp = app_client.post("/register", data={
            "email": "new2@example.com",
            "password": "securepass1",
            "confirm_password": "securepass1",
        }, follow_redirects=True)
        html = resp.data.decode()
        assert "Continue Free" in html
        assert "Premium Package" in html

    def test_register_session_has_no_tier(self, app_client):
        _register_customer(app_client)
        with app_client.session_transaction() as sess:
            user = sess.get("user", {})
            assert user.get("account_tier") is None or user.get("account_tier") == ""

    def test_register_then_choose_free_lands_on_dashboard(self, app_client):
        _register_customer(app_client)
        resp = app_client.post("/choose-plan", data={"tier": "free"}, follow_redirects=True)
        html = resp.data.decode()
        assert "Dashboard" in html


# ===========================================================================
# 6. Login flow tier handling (4 tests)
# ===========================================================================
class TestLoginTierHandling:
    """Test that login checks tier and redirects accordingly."""

    def test_login_with_tier_goes_to_dashboard(self, app_client, mock_db):
        _register_and_choose_tier(app_client, tier="free")
        # Logout then login
        app_client.post("/logout")
        resp = _login_customer(app_client)
        assert resp.status_code in (302, 303)
        assert "/dashboard" in resp.headers.get("Location", "")

    def test_login_without_tier_goes_to_choose_plan(self, app_client, mock_db):
        _register_customer(app_client)
        # Logout then login (user still has no tier)
        app_client.post("/logout")
        resp = _login_customer(app_client)
        assert resp.status_code in (302, 303)
        assert "/choose-plan" in resp.headers.get("Location", "")

    def test_login_session_includes_tier(self, app_client, mock_db):
        _register_and_choose_tier(app_client, tier="premium")
        app_client.post("/logout")
        _login_customer(app_client)
        with app_client.session_transaction() as sess:
            user = sess.get("user", {})
            assert user.get("account_tier") == "premium"

    def test_admin_login_skips_tier(self, app_client, monkeypatch):
        import portal.app as _app
        monkeypatch.setattr(_app, "DEV_USERNAME", "admin")
        monkeypatch.setattr(_app, "DEV_PASSWORD", "adminpass")
        resp = app_client.post("/login", data={
            "username": "admin",
            "password": "adminpass",
        })
        assert resp.status_code in (302, 303)
        loc = resp.headers.get("Location", "")
        assert "/choose-plan" not in loc


# ===========================================================================
# 7. Dashboard tier gate (4 tests)
# ===========================================================================
class TestDashboardTierGate:
    """Test that /dashboard requires tier chosen."""

    def test_dashboard_without_tier_redirects_to_choose_plan(self, app_client):
        _register_customer(app_client)
        resp = app_client.get("/dashboard")
        assert resp.status_code in (302, 303)
        assert "/choose-plan" in resp.headers.get("Location", "")

    def test_dashboard_with_free_tier_shows_page(self, app_client):
        _register_and_choose_tier(app_client, tier="free")
        resp = app_client.get("/dashboard")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Dashboard" in html

    def test_dashboard_with_premium_tier_shows_page(self, app_client):
        _register_and_choose_tier(app_client, tier="premium")
        resp = app_client.get("/dashboard")
        assert resp.status_code == 200

    def test_dashboard_admin_bypasses_tier_check(self, app_client, monkeypatch):
        import portal.app as _app
        monkeypatch.setattr(_app, "DEV_USERNAME", "admin")
        monkeypatch.setattr(_app, "DEV_PASSWORD", "adminpass")
        app_client.post("/login", data={
            "username": "admin",
            "password": "adminpass",
        })
        resp = app_client.get("/dashboard")
        # Admin might get 200 or redirect elsewhere, but never to /choose-plan
        loc = resp.headers.get("Location", "")
        assert "/choose-plan" not in loc


# ===========================================================================
# 8. Route gating by tier (4 tests)
# ===========================================================================
class TestRouteGating:
    """Test that OCR upload is gated to premium, free can still use structured imports."""

    def test_import_get_accessible_with_any_tier(self, app_client):
        _register_and_choose_tier(app_client, tier="free")
        resp = app_client.get("/import")
        assert resp.status_code == 200

    def test_import_post_blocked_for_free_tier(self, app_client, tmp_path):
        _register_and_choose_tier(app_client, tier="free")
        # Try to POST an image upload (OCR) — should be blocked
        import io
        data = {"file": (io.BytesIO(b"fake image data"), "menu.jpg")}
        resp = app_client.post("/import", data=data, content_type="multipart/form-data",
                               follow_redirects=True)
        html = resp.data.decode()
        assert "Premium Package" in html

    def test_import_post_allowed_for_premium_tier(self, app_client, tmp_path, monkeypatch):
        _register_and_choose_tier(app_client, tier="premium")
        # Mock the OCR so it doesn't actually run
        import portal.app as _app
        monkeypatch.setattr(_app, "run_ocr_and_make_draft", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(_app, "create_import_job", lambda **kw: 999, raising=False)
        import io
        data = {"file": (io.BytesIO(b"fake image data"), "menu.jpg")}
        resp = app_client.post("/import", data=data, content_type="multipart/form-data")
        # Should not be blocked — either redirect to import view or process
        assert resp.status_code in (302, 303, 200)
        html_or_loc = resp.data.decode() + resp.headers.get("Location", "")
        assert "Premium Package" not in html_or_loc

    def test_choose_plan_page_shows_pricing(self, app_client):
        _register_customer(app_client)
        resp = app_client.get("/choose-plan")
        html = resp.data.decode()
        assert "$49.99" in html
        assert "$10" in html
