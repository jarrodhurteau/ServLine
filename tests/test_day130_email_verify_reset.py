# tests/test_day130_email_verify_reset.py
"""
Day 130 — Sprint 13.1: Email Verification & Password Reset.

Deliverables:
  1. email_verification_tokens table + CRUD
  2. password_reset_tokens table + CRUD
  3. generate/verify email verification tokens
  4. generate/validate/consume password reset tokens
  5. /verify-email/<token> route
  6. /resend-verification route
  7. /forgot-password (GET + POST)
  8. /reset-password/<token> (GET + POST)
  9. Registration auto-generates verification token
  10. Account page shows verification status badge
  11. Login page has "Forgot password?" link

32 tests across 8 classes:
  1. Email verification token CRUD (4)
  2. Password reset token CRUD (4)
  3. Reset token edge cases (4)
  4. Portal: email verification flow (4)
  5. Portal: resend verification (4)
  6. Portal: forgot password flow (4)
  7. Portal: reset password flow (4)
  8. Portal: registration + UI integration (4)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

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
    """Register a customer and log in."""
    client.post("/register", data={
        "email": email,
        "password": password,
        "confirm_password": password,
        "display_name": "Test Customer",
    })
    return client


def _create_user_directly(mock_db, email="direct@example.com", password="securepass1"):
    """Create a user directly via users_store (no portal)."""
    import storage.users as _users
    return _users.create_user(email, password, display_name="Direct User")


# ===========================================================================
# 1. Email verification token CRUD (4 tests)
# ===========================================================================
class TestEmailVerificationTokenCRUD:
    """Test generate/verify/revoke email verification tokens."""

    def test_generate_token_returns_raw_string(self, mock_db):
        import storage.users as _users
        user = _users.create_user("test@x.com", "password123")
        token = _users.generate_verification_token(user["id"])
        assert isinstance(token, str)
        assert len(token) > 20  # urlsafe tokens are long

    def test_verify_valid_token_marks_email_verified(self, mock_db):
        import storage.users as _users
        user = _users.create_user("test@x.com", "password123")
        token = _users.generate_verification_token(user["id"])
        result = _users.verify_email_token(token)
        assert result is not None
        assert result["id"] == user["id"]
        assert result["email_verified"] == 1

    def test_verify_invalid_token_returns_none(self, mock_db):
        import storage.users as _users
        result = _users.verify_email_token("totally-bogus-token")
        assert result is None

    def test_generate_replaces_previous_token(self, mock_db):
        import storage.users as _users
        user = _users.create_user("test@x.com", "password123")
        token1 = _users.generate_verification_token(user["id"])
        token2 = _users.generate_verification_token(user["id"])
        assert token1 != token2
        # Old token should no longer work
        assert _users.verify_email_token(token1) is None
        # New token should work
        assert _users.verify_email_token(token2) is not None


# ===========================================================================
# 2. Password reset token CRUD (4 tests)
# ===========================================================================
class TestPasswordResetTokenCRUD:
    """Test generate/validate/consume password reset tokens."""

    def test_generate_reset_token_for_valid_email(self, mock_db):
        import storage.users as _users
        _users.create_user("test@x.com", "password123")
        token = _users.generate_reset_token("test@x.com")
        assert isinstance(token, str)
        assert len(token) > 20

    def test_generate_reset_token_for_unknown_email_returns_none(self, mock_db):
        import storage.users as _users
        token = _users.generate_reset_token("nobody@x.com")
        assert token is None

    def test_validate_reset_token_returns_user_id(self, mock_db):
        import storage.users as _users
        user = _users.create_user("test@x.com", "password123")
        token = _users.generate_reset_token("test@x.com")
        uid = _users.validate_reset_token(token)
        assert uid == user["id"]

    def test_consume_reset_token_changes_password(self, mock_db):
        import storage.users as _users
        _users.create_user("test@x.com", "password123")
        token = _users.generate_reset_token("test@x.com")
        ok = _users.consume_reset_token(token, "newpassword9")
        assert ok is True
        # Old password should fail
        assert _users.verify_password("test@x.com", "password123") is None
        # New password should work
        assert _users.verify_password("test@x.com", "newpassword9") is not None


# ===========================================================================
# 3. Reset token edge cases (4 tests)
# ===========================================================================
class TestResetTokenEdgeCases:
    """Test expiry, used tokens, inactive users, short passwords."""

    def test_expired_token_is_invalid(self, mock_db, monkeypatch):
        import storage.users as _users
        _users.create_user("test@x.com", "password123")
        token = _users.generate_reset_token("test@x.com")
        # Manually expire the token by setting expires_at in the past
        hashed = _users._hash_token(token)
        past = (datetime.now() - timedelta(hours=2)).isoformat(sep=" ", timespec="seconds")
        conn = mock_db()
        conn.execute("UPDATE password_reset_tokens SET expires_at = ? WHERE token_hash = ?",
                      (past, hashed))
        conn.commit()
        conn.close()
        assert _users.validate_reset_token(token) is None

    def test_used_token_is_invalid(self, mock_db):
        import storage.users as _users
        _users.create_user("test@x.com", "password123")
        token = _users.generate_reset_token("test@x.com")
        _users.consume_reset_token(token, "newpassword9")
        # Token should be used up
        assert _users.validate_reset_token(token) is None

    def test_consume_with_short_password_raises(self, mock_db):
        import storage.users as _users
        _users.create_user("test@x.com", "password123")
        token = _users.generate_reset_token("test@x.com")
        with pytest.raises(ValueError, match="at least 8"):
            _users.consume_reset_token(token, "short")

    def test_inactive_user_gets_no_reset_token(self, mock_db):
        import storage.users as _users
        user = _users.create_user("test@x.com", "password123")
        _users.deactivate_user(user["id"])
        token = _users.generate_reset_token("test@x.com")
        assert token is None


# ===========================================================================
# 4. Portal: email verification flow (4 tests)
# ===========================================================================
class TestPortalEmailVerification:
    """Test /verify-email/<token> route."""

    def test_valid_token_verifies_and_redirects(self, app_client, mock_db):
        import storage.users as _users
        _register_customer(app_client)
        user = _users.get_user_by_email("cust@example.com")
        token = _users.generate_verification_token(user["id"])
        resp = app_client.get(f"/verify-email/{token}", follow_redirects=True)
        html = resp.data.decode("utf-8")
        assert "Email verified successfully" in html

    def test_verified_user_has_email_verified_in_db(self, app_client, mock_db):
        import storage.users as _users
        _register_customer(app_client)
        user = _users.get_user_by_email("cust@example.com")
        token = _users.generate_verification_token(user["id"])
        app_client.get(f"/verify-email/{token}", follow_redirects=True)
        user_after = _users.get_user_by_id(user["id"])
        assert user_after["email_verified"] == 1

    def test_invalid_token_shows_error(self, app_client, mock_db):
        resp = app_client.get("/verify-email/bogus-token", follow_redirects=True)
        html = resp.data.decode("utf-8")
        assert "Invalid or expired" in html

    def test_token_consumed_after_use(self, app_client, mock_db):
        import storage.users as _users
        _register_customer(app_client)
        user = _users.get_user_by_email("cust@example.com")
        token = _users.generate_verification_token(user["id"])
        app_client.get(f"/verify-email/{token}", follow_redirects=True)
        # Second attempt should fail
        resp = app_client.get(f"/verify-email/{token}", follow_redirects=True)
        html = resp.data.decode("utf-8")
        assert "Invalid or expired" in html


# ===========================================================================
# 5. Portal: resend verification (4 tests)
# ===========================================================================
class TestPortalResendVerification:
    """Test /resend-verification route."""

    def test_resend_generates_new_token(self, app_client, mock_db):
        import storage.users as _users
        _register_customer(app_client)
        resp = app_client.post("/resend-verification", follow_redirects=True)
        html = resp.data.decode("utf-8")
        assert "Verification link:" in html

    def test_resend_requires_login(self, app_client, mock_db):
        resp = app_client.post("/resend-verification")
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("Location", "")

    def test_resend_skips_if_already_verified(self, app_client, mock_db):
        import storage.users as _users
        _register_customer(app_client)
        user = _users.get_user_by_email("cust@example.com")
        _users.update_user(user["id"], email_verified=1)
        resp = app_client.post("/resend-verification", follow_redirects=True)
        html = resp.data.decode("utf-8")
        assert "already verified" in html

    def test_resend_invalidates_old_token(self, app_client, mock_db):
        import storage.users as _users
        _register_customer(app_client)
        user = _users.get_user_by_email("cust@example.com")
        old_token = _users.generate_verification_token(user["id"])
        app_client.post("/resend-verification", follow_redirects=True)
        # Old token should no longer work
        assert _users.verify_email_token(old_token) is None


# ===========================================================================
# 6. Portal: forgot password flow (4 tests)
# ===========================================================================
class TestPortalForgotPassword:
    """Test /forgot-password (GET + POST)."""

    def test_get_shows_form(self, app_client, mock_db):
        resp = app_client.get("/forgot-password")
        html = resp.data.decode("utf-8")
        assert "Forgot Password" in html
        assert 'name="email"' in html

    def test_post_with_valid_email_shows_link(self, app_client, mock_db):
        import storage.users as _users
        _users.create_user("test@x.com", "password123")
        resp = app_client.post("/forgot-password", data={"email": "test@x.com"},
                               follow_redirects=True)
        html = resp.data.decode("utf-8")
        assert "reset-password/" in html

    def test_post_with_unknown_email_no_leak(self, app_client, mock_db):
        resp = app_client.post("/forgot-password", data={"email": "nobody@x.com"},
                               follow_redirects=True)
        html = resp.data.decode("utf-8")
        # Should show generic message, not "email not found"
        assert "If an account" in html
        assert "reset-password/" not in html

    def test_post_with_empty_email_shows_error(self, app_client, mock_db):
        resp = app_client.post("/forgot-password", data={"email": ""},
                               follow_redirects=True)
        html = resp.data.decode("utf-8")
        assert "enter your email" in html


# ===========================================================================
# 7. Portal: reset password flow (4 tests)
# ===========================================================================
class TestPortalResetPassword:
    """Test /reset-password/<token> (GET + POST)."""

    def test_get_with_valid_token_shows_form(self, app_client, mock_db):
        import storage.users as _users
        _users.create_user("test@x.com", "password123")
        token = _users.generate_reset_token("test@x.com")
        resp = app_client.get(f"/reset-password/{token}")
        html = resp.data.decode("utf-8")
        assert "Reset Password" in html
        assert 'name="new_password"' in html

    def test_get_with_invalid_token_redirects(self, app_client, mock_db):
        resp = app_client.get("/reset-password/bogus-token", follow_redirects=True)
        html = resp.data.decode("utf-8")
        assert "Invalid or expired" in html

    def test_post_resets_password_and_redirects_to_login(self, app_client, mock_db):
        import storage.users as _users
        _users.create_user("test@x.com", "password123")
        token = _users.generate_reset_token("test@x.com")
        resp = app_client.post(f"/reset-password/{token}", data={
            "new_password": "brandnewpw1",
            "confirm_password": "brandnewpw1",
        }, follow_redirects=True)
        html = resp.data.decode("utf-8")
        assert "Password reset successfully" in html
        # Can log in with new password
        assert _users.verify_password("test@x.com", "brandnewpw1") is not None

    def test_post_mismatch_passwords_shows_error(self, app_client, mock_db):
        import storage.users as _users
        _users.create_user("test@x.com", "password123")
        token = _users.generate_reset_token("test@x.com")
        resp = app_client.post(f"/reset-password/{token}", data={
            "new_password": "brandnewpw1",
            "confirm_password": "different99",
        }, follow_redirects=True)
        html = resp.data.decode("utf-8")
        assert "do not match" in html


# ===========================================================================
# 8. Portal: registration + UI integration (4 tests)
# ===========================================================================
class TestPortalRegistrationAndUI:
    """Test registration auto-token + login forgot-password link + account badge."""

    def test_registration_generates_verification_token(self, app_client, mock_db):
        resp = app_client.post("/register", data={
            "email": "new@example.com",
            "password": "securepass1",
            "confirm_password": "securepass1",
            "display_name": "New User",
        }, follow_redirects=True)
        html = resp.data.decode("utf-8")
        assert "verify-email/" in html

    def test_login_page_has_forgot_password_link(self, app_client, mock_db):
        resp = app_client.get("/login")
        html = resp.data.decode("utf-8")
        assert "forgot-password" in html

    def test_account_page_shows_unverified_badge(self, app_client, mock_db):
        _register_customer(app_client)
        resp = app_client.get("/account")
        html = resp.data.decode("utf-8")
        assert "Unverified" in html

    def test_account_page_shows_verified_badge_after_verification(self, app_client, mock_db):
        import storage.users as _users
        _register_customer(app_client)
        user = _users.get_user_by_email("cust@example.com")
        token = _users.generate_verification_token(user["id"])
        app_client.get(f"/verify-email/{token}", follow_redirects=True)
        resp = app_client.get("/account")
        html = resp.data.decode("utf-8")
        assert "Verified" in html
