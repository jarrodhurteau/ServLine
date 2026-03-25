# tests/test_day126_user_accounts.py
"""
Day 126 — Sprint 13.1: User Accounts & Auth Foundation.

Deliverables:
  1. storage/users.py — users table, user_restaurants table, full CRUD
  2. Password hashing (werkzeug) — create, verify, change
  3. User↔restaurant association — link, unlink, role management
  4. Portal endpoints — /register (GET+POST), updated /login (POST), /logout
  5. Validation — email format, password length, duplicate email, role enum

32 tests across 8 classes:
  1. Email validation (3)
  2. Password validation (2)
  3. User CRUD — create, get, update, deactivate (6)
  4. Password hashing & verification (4)
  5. User↔restaurant association (6)
  6. Portal registration endpoint (4)
  7. Portal login endpoint — legacy admin + DB user (4)
  8. Portal logout & session (3)
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS restaurants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
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
    source_file_path TEXT,
    menu_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER,
    file_name TEXT,
    status TEXT DEFAULT 'pending',
    error TEXT,
    draft_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_db(monkeypatch, tmp_path):
    """In-memory SQLite with users schema, patched into storage.users + storage.drafts."""
    db_path = tmp_path / "test.db"

    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    # seed schema
    conn = _connect()
    conn.executescript(_SCHEMA_SQL)
    conn.execute("INSERT INTO restaurants (id, name) VALUES (1, 'Test Bistro')")
    conn.execute("INSERT INTO restaurants (id, name) VALUES (2, 'Second Place')")
    conn.commit()
    conn.close()

    import storage.drafts as _drafts
    import storage.users as _users
    monkeypatch.setattr(_drafts, "db_connect", _connect)
    monkeypatch.setattr(_users, "db_connect", _connect)
    return _connect


@pytest.fixture()
def users_mod(mock_db):
    """Return the users module (already patched)."""
    import storage.users as _users
    return _users


@pytest.fixture()
def app_client(mock_db, monkeypatch):
    """Flask test client with patched DB."""
    import portal.app as _app
    monkeypatch.setattr(_app, "db_connect", mock_db)
    import storage.users as _users
    monkeypatch.setattr(_app, "users_store", _users)
    _app.app.config["TESTING"] = True
    _app.app.config["WTF_CSRF_ENABLED"] = False
    return _app.app.test_client()


# ===========================================================================
# 1. Email validation (3 tests)
# ===========================================================================
class TestEmailValidation:
    def test_valid_email(self, users_mod):
        assert users_mod.validate_email("user@example.com") is None

    def test_invalid_email_no_at(self, users_mod):
        err = users_mod.validate_email("not-an-email")
        assert err is not None
        assert "format" in err.lower() or "invalid" in err.lower()

    def test_empty_email(self, users_mod):
        err = users_mod.validate_email("")
        assert err is not None
        assert "required" in err.lower()


# ===========================================================================
# 2. Password validation (2 tests)
# ===========================================================================
class TestPasswordValidation:
    def test_valid_password(self, users_mod):
        assert users_mod.validate_password("securepass123") is None

    def test_short_password(self, users_mod):
        err = users_mod.validate_password("short")
        assert err is not None
        assert "8" in err


# ===========================================================================
# 3. User CRUD (6 tests)
# ===========================================================================
class TestUserCRUD:
    def test_create_user(self, users_mod):
        user = users_mod.create_user("alice@example.com", "password123")
        assert user["id"] is not None
        assert user["email"] == "alice@example.com"
        assert user["active"] is True

    def test_create_user_with_display_name(self, users_mod):
        user = users_mod.create_user("bob@example.com", "password123",
                                     display_name="Bob Smith")
        assert user["display_name"] == "Bob Smith"

    def test_create_duplicate_email_raises(self, users_mod):
        users_mod.create_user("dup@example.com", "password123")
        with pytest.raises(ValueError, match="already exists"):
            users_mod.create_user("dup@example.com", "otherpass123")

    def test_get_user_by_email(self, users_mod):
        users_mod.create_user("find@example.com", "password123")
        user = users_mod.get_user_by_email("find@example.com")
        assert user is not None
        assert user["email"] == "find@example.com"

    def test_get_user_by_email_case_insensitive(self, users_mod):
        users_mod.create_user("case@example.com", "password123")
        user = users_mod.get_user_by_email("CASE@Example.COM")
        assert user is not None

    def test_deactivate_user(self, users_mod):
        user = users_mod.create_user("deact@example.com", "password123")
        assert users_mod.deactivate_user(user["id"]) is True
        fetched = users_mod.get_user_by_id(user["id"])
        assert fetched["active"] == 0


# ===========================================================================
# 4. Password hashing & verification (4 tests)
# ===========================================================================
class TestPasswordAuth:
    def test_verify_correct_password(self, users_mod):
        users_mod.create_user("auth@example.com", "mypassword1")
        result = users_mod.verify_password("auth@example.com", "mypassword1")
        assert result is not None
        assert result["email"] == "auth@example.com"

    def test_verify_wrong_password(self, users_mod):
        users_mod.create_user("auth2@example.com", "mypassword1")
        result = users_mod.verify_password("auth2@example.com", "wrongpass1")
        assert result is None

    def test_verify_nonexistent_email(self, users_mod):
        result = users_mod.verify_password("nobody@example.com", "whatever1")
        assert result is None

    def test_change_password(self, users_mod):
        user = users_mod.create_user("chg@example.com", "oldpass123")
        users_mod.change_password(user["id"], "newpass1234")
        assert users_mod.verify_password("chg@example.com", "newpass1234") is not None
        assert users_mod.verify_password("chg@example.com", "oldpass123") is None


# ===========================================================================
# 5. User↔restaurant association (6 tests)
# ===========================================================================
class TestUserRestaurants:
    def test_link_user_restaurant(self, users_mod):
        user = users_mod.create_user("link@example.com", "password123")
        result = users_mod.link_user_restaurant(user["id"], 1)
        assert result["role"] == "owner"

    def test_link_duplicate_raises(self, users_mod):
        user = users_mod.create_user("dup_link@example.com", "password123")
        users_mod.link_user_restaurant(user["id"], 1)
        with pytest.raises(ValueError, match="already linked"):
            users_mod.link_user_restaurant(user["id"], 1)

    def test_invalid_role_raises(self, users_mod):
        user = users_mod.create_user("role@example.com", "password123")
        with pytest.raises(ValueError, match="Invalid role"):
            users_mod.link_user_restaurant(user["id"], 1, role="superadmin")

    def test_get_user_restaurants(self, users_mod):
        user = users_mod.create_user("multi@example.com", "password123")
        users_mod.link_user_restaurant(user["id"], 1, role="owner")
        users_mod.link_user_restaurant(user["id"], 2, role="manager")
        restaurants = users_mod.get_user_restaurants(user["id"])
        assert len(restaurants) == 2
        assert restaurants[0]["restaurant_name"] == "Test Bistro"

    def test_unlink_user_restaurant(self, users_mod):
        user = users_mod.create_user("unlink@example.com", "password123")
        users_mod.link_user_restaurant(user["id"], 1)
        assert users_mod.unlink_user_restaurant(user["id"], 1) is True
        assert len(users_mod.get_user_restaurants(user["id"])) == 0

    def test_user_owns_restaurant(self, users_mod):
        user = users_mod.create_user("owns@example.com", "password123")
        assert users_mod.user_owns_restaurant(user["id"], 1) is False
        users_mod.link_user_restaurant(user["id"], 1)
        assert users_mod.user_owns_restaurant(user["id"], 1) is True


# ===========================================================================
# 6. Portal registration endpoint (4 tests)
# ===========================================================================
class TestRegisterEndpoint:
    def test_register_get_page(self, app_client):
        resp = app_client.get("/register")
        assert resp.status_code == 200
        assert b"Create Account" in resp.data

    def test_register_success(self, app_client):
        resp = app_client.post("/register", data={
            "email": "new@example.com",
            "password": "securepass1",
            "confirm_password": "securepass1",
            "display_name": "New User",
        })
        # Should redirect (302) to index on success
        assert resp.status_code == 302

    def test_register_password_mismatch(self, app_client):
        resp = app_client.post("/register", data={
            "email": "mm@example.com",
            "password": "securepass1",
            "confirm_password": "differentpass",
        })
        # Should redirect back to register
        assert resp.status_code == 302
        assert "/register" in resp.headers.get("Location", "")

    def test_register_duplicate_email(self, app_client):
        app_client.post("/register", data={
            "email": "dup2@example.com",
            "password": "securepass1",
            "confirm_password": "securepass1",
        })
        resp = app_client.post("/register", data={
            "email": "dup2@example.com",
            "password": "securepass2",
            "confirm_password": "securepass2",
        })
        assert resp.status_code == 302
        assert "/register" in resp.headers.get("Location", "")


# ===========================================================================
# 7. Portal login endpoint (4 tests)
# ===========================================================================
class TestLoginEndpoint:
    def test_login_legacy_admin(self, app_client):
        resp = app_client.post("/login", data={
            "username": "admin",
            "password": "letmein",
        })
        assert resp.status_code == 302  # redirect to index

    def test_login_db_user(self, app_client, users_mod):
        users_mod.create_user("dbuser@example.com", "password123",
                              display_name="DB User")
        resp = app_client.post("/login", data={
            "username": "dbuser@example.com",
            "password": "password123",
        })
        assert resp.status_code == 302  # redirect to index

    def test_login_wrong_password(self, app_client, users_mod):
        users_mod.create_user("wrong@example.com", "password123")
        resp = app_client.post("/login", data={
            "username": "wrong@example.com",
            "password": "badpassword1",
        })
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_login_deactivated_user(self, app_client, users_mod):
        user = users_mod.create_user("dead@example.com", "password123")
        users_mod.deactivate_user(user["id"])
        resp = app_client.post("/login", data={
            "username": "dead@example.com",
            "password": "password123",
        })
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


# ===========================================================================
# 8. Portal logout & session (3 tests)
# ===========================================================================
class TestLogoutSession:
    def test_logout_redirects(self, app_client):
        # Login first
        app_client.post("/login", data={
            "username": "admin", "password": "letmein"
        })
        resp = app_client.post("/logout")
        assert resp.status_code == 302

    def test_login_sets_session_user_id(self, app_client, users_mod):
        users_mod.create_user("sess@example.com", "password123")
        with app_client:
            app_client.post("/login", data={
                "username": "sess@example.com",
                "password": "password123",
            })
            from flask import session as flask_session
            user_data = flask_session.get("user")
            assert user_data is not None
            assert user_data["user_id"] is not None
            assert user_data["role"] == "customer"

    def test_register_auto_login_session(self, app_client):
        with app_client:
            app_client.post("/register", data={
                "email": "auto@example.com",
                "password": "securepass1",
                "confirm_password": "securepass1",
            })
            from flask import session as flask_session
            user_data = flask_session.get("user")
            assert user_data is not None
            assert user_data["email"] == "auto@example.com"
            assert user_data["role"] == "customer"
