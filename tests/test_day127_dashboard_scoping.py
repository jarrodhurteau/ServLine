# tests/test_day127_dashboard_scoping.py
"""
Day 127 — Sprint 13.1: Session Scoping & Customer Dashboard.

Deliverables:
  1. POST /restaurants auto-links customer user via link_user_restaurant()
  2. require_restaurant_access decorator — ownership check on restaurant routes
  3. /dashboard route — customer landing page with "My Restaurants"
  4. /restaurants scoped — customers see only their restaurants, admins see all
  5. Login/register redirect customers to /dashboard
  6. /account + /account/update + /account/change-password — account settings
  7. Nav: customer sees Dashboard + My Restaurants; admin sees Import/Imports/etc.
  8. Context processor: is_customer + show_admin role-aware

32 tests across 8 classes:
  1. Restaurant auto-link on create (4)
  2. require_restaurant_access decorator (4)
  3. Dashboard route (4)
  4. Scoped restaurant list (4)
  5. Login/register redirects (4)
  6. Account settings (4)
  7. Password change (4)
  8. Nav & context processor (4)
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
    created_at TEXT DEFAULT (datetime('now'))
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
    source_file_path TEXT,
    menu_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
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
    conn.execute("INSERT INTO restaurants (id, name) VALUES (1, 'Test Bistro')")
    conn.execute("INSERT INTO restaurants (id, name) VALUES (2, 'Other Place')")
    conn.execute("INSERT INTO restaurants (id, name) VALUES (3, 'Third Spot')")
    conn.commit()
    conn.close()

    import storage.drafts as _drafts
    import storage.users as _users
    monkeypatch.setattr(_drafts, "db_connect", _connect)
    monkeypatch.setattr(_users, "db_connect", _connect)
    return _connect


@pytest.fixture()
def users_mod(mock_db):
    import storage.users as _users
    return _users


@pytest.fixture()
def app_client(mock_db, monkeypatch):
    import portal.app as _app
    monkeypatch.setattr(_app, "db_connect", mock_db)
    import storage.users as _users
    monkeypatch.setattr(_app, "users_store", _users)
    _app.app.config["TESTING"] = True
    _app.app.config["WTF_CSRF_ENABLED"] = False
    return _app.app.test_client()


def _register_and_login(client, email="cust@example.com", password="securepass1"):
    """Helper: register a customer and return (client, session_data)."""
    client.post("/register", data={
        "email": email,
        "password": password,
        "confirm_password": password,
        "display_name": "Test Customer",
    })
    return client


def _admin_login(client):
    """Helper: log in as the legacy dev admin."""
    client.post("/login", data={"username": "admin", "password": "letmein"})
    return client


# ===========================================================================
# 1. Restaurant auto-link on create (4 tests)
# ===========================================================================
class TestRestaurantAutoLink:
    def test_create_restaurant_links_customer(self, app_client, users_mod):
        """POST /restaurants auto-links the customer to the new restaurant."""
        _register_and_login(app_client)
        app_client.post("/restaurants", data={"name": "My New Place"})
        # Find the user
        user = users_mod.get_user_by_email("cust@example.com")
        restaurants = users_mod.get_user_restaurants(user["id"])
        names = [r["restaurant_name"] for r in restaurants]
        assert "My New Place" in names

    def test_create_restaurant_sets_session_restaurant_id(self, app_client):
        """First restaurant created sets session restaurant_id."""
        _register_and_login(app_client)
        with app_client:
            app_client.post("/restaurants", data={"name": "First Rest"})
            from flask import session
            u = session.get("user", {})
            assert u.get("restaurant_id") is not None

    def test_admin_create_does_not_link(self, app_client, users_mod):
        """Admin creating a restaurant does not auto-link (no user_id in session)."""
        _admin_login(app_client)
        app_client.post("/restaurants", data={"name": "Admin Place"})
        # Admin has no user_id, so no link should exist
        users_list = users_mod.list_users(active_only=False)
        assert len(users_list) == 0  # no DB users created

    def test_create_restaurant_redirects_to_custom(self, app_client):
        """_redirect form field controls post-create redirect."""
        _register_and_login(app_client)
        resp = app_client.post("/restaurants", data={
            "name": "Redirect Test",
            "_redirect": "/dashboard",
        })
        assert resp.status_code == 302
        assert "/dashboard" in resp.headers.get("Location", "")


# ===========================================================================
# 2. require_restaurant_access decorator (4 tests)
# ===========================================================================
class TestRestaurantAccess:
    def test_admin_can_access_any_restaurant(self, app_client):
        """Admin role bypasses ownership check."""
        _admin_login(app_client)
        resp = app_client.get("/restaurants/1/menus")
        assert resp.status_code == 200

    def test_customer_blocked_from_unowned_restaurant(self, app_client):
        """Customer without link to restaurant gets 403."""
        _register_and_login(app_client)
        resp = app_client.get("/restaurants/1/menus")
        assert resp.status_code == 403

    def test_customer_can_access_owned_restaurant(self, app_client, users_mod):
        """Customer with link can access the restaurant."""
        _register_and_login(app_client)
        user = users_mod.get_user_by_email("cust@example.com")
        users_mod.link_user_restaurant(user["id"], 1, role="owner")
        resp = app_client.get("/restaurants/1/menus")
        assert resp.status_code == 200

    def test_unauthenticated_redirects_to_login(self, app_client):
        """Not logged in → redirect to /login."""
        resp = app_client.get("/restaurants/1/menus")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


# ===========================================================================
# 3. Dashboard route (4 tests)
# ===========================================================================
class TestDashboard:
    def test_dashboard_requires_login(self, app_client):
        resp = app_client.get("/dashboard")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_dashboard_renders_for_customer(self, app_client):
        _register_and_login(app_client)
        resp = app_client.get("/dashboard")
        assert resp.status_code == 200
        assert b"Dashboard" in resp.data

    def test_dashboard_shows_linked_restaurants(self, app_client, users_mod):
        _register_and_login(app_client)
        user = users_mod.get_user_by_email("cust@example.com")
        users_mod.link_user_restaurant(user["id"], 1, role="owner")
        resp = app_client.get("/dashboard")
        assert resp.status_code == 200
        assert b"Test Bistro" in resp.data

    def test_dashboard_empty_state(self, app_client):
        """New customer with no restaurants sees empty state."""
        _register_and_login(app_client)
        resp = app_client.get("/dashboard")
        assert resp.status_code == 200
        assert b"haven" in resp.data or b"Add Restaurant" in resp.data


# ===========================================================================
# 4. Scoped restaurant list (4 tests)
# ===========================================================================
class TestScopedRestaurantList:
    def test_customer_sees_only_own_restaurants(self, app_client, users_mod):
        _register_and_login(app_client)
        user = users_mod.get_user_by_email("cust@example.com")
        users_mod.link_user_restaurant(user["id"], 1, role="owner")
        resp = app_client.get("/restaurants")
        assert b"Test Bistro" in resp.data
        assert b"Other Place" not in resp.data

    def test_customer_no_restaurants_sees_empty(self, app_client):
        _register_and_login(app_client)
        resp = app_client.get("/restaurants")
        assert resp.status_code == 200
        assert b"No restaurants" in resp.data

    def test_admin_sees_all_restaurants(self, app_client):
        _admin_login(app_client)
        resp = app_client.get("/restaurants")
        assert b"Test Bistro" in resp.data
        assert b"Other Place" in resp.data
        assert b"Third Spot" in resp.data

    def test_restaurants_requires_login(self, app_client):
        resp = app_client.get("/restaurants")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


# ===========================================================================
# 5. Login/register redirects (4 tests)
# ===========================================================================
class TestAuthRedirects:
    def test_register_redirects_to_dashboard(self, app_client):
        resp = app_client.post("/register", data={
            "email": "redir@example.com",
            "password": "securepass1",
            "confirm_password": "securepass1",
        })
        assert resp.status_code == 302
        assert "/dashboard" in resp.headers.get("Location", "")

    def test_customer_login_redirects_to_dashboard(self, app_client, users_mod):
        users_mod.create_user("login@example.com", "securepass1")
        resp = app_client.post("/login", data={
            "username": "login@example.com",
            "password": "securepass1",
        })
        assert resp.status_code == 302
        assert "/dashboard" in resp.headers.get("Location", "")

    def test_admin_login_redirects_to_index(self, app_client):
        resp = app_client.post("/login", data={
            "username": "admin",
            "password": "letmein",
        })
        assert resp.status_code == 302
        loc = resp.headers.get("Location", "")
        # Admin goes to core.index (/)
        assert "/dashboard" not in loc

    def test_login_with_next_param_respected(self, app_client, users_mod):
        users_mod.create_user("next@example.com", "securepass1")
        resp = app_client.post("/login", data={
            "username": "next@example.com",
            "password": "securepass1",
            "next": "/restaurants",
        })
        assert resp.status_code == 302
        assert "/restaurants" in resp.headers.get("Location", "")


# ===========================================================================
# 6. Account settings (4 tests)
# ===========================================================================
class TestAccountSettings:
    def test_account_page_requires_login(self, app_client):
        resp = app_client.get("/account")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_account_page_renders(self, app_client):
        _register_and_login(app_client)
        resp = app_client.get("/account")
        assert resp.status_code == 200
        assert b"Account Settings" in resp.data

    def test_update_display_name(self, app_client, users_mod):
        _register_and_login(app_client)
        resp = app_client.post("/account/update", data={
            "display_name": "New Name",
        })
        assert resp.status_code == 302
        user = users_mod.get_user_by_email("cust@example.com")
        assert user["display_name"] == "New Name"

    def test_update_display_name_updates_session(self, app_client):
        _register_and_login(app_client)
        with app_client:
            app_client.post("/account/update", data={
                "display_name": "Updated Name",
            })
            from flask import session
            assert session["user"]["username"] == "Updated Name"


# ===========================================================================
# 7. Password change (4 tests)
# ===========================================================================
class TestPasswordChange:
    def test_change_password_success(self, app_client, users_mod):
        _register_and_login(app_client)
        resp = app_client.post("/account/change-password", data={
            "current_password": "securepass1",
            "new_password": "newpassword1",
            "confirm_password": "newpassword1",
        })
        assert resp.status_code == 302
        # Verify new password works
        result = users_mod.verify_password("cust@example.com", "newpassword1")
        assert result is not None

    def test_change_password_wrong_current(self, app_client):
        _register_and_login(app_client)
        resp = app_client.post("/account/change-password", data={
            "current_password": "wrongpassword",
            "new_password": "newpassword1",
            "confirm_password": "newpassword1",
        }, follow_redirects=True)
        assert b"incorrect" in resp.data.lower()

    def test_change_password_mismatch(self, app_client):
        _register_and_login(app_client)
        resp = app_client.post("/account/change-password", data={
            "current_password": "securepass1",
            "new_password": "newpassword1",
            "confirm_password": "differentpass",
        }, follow_redirects=True)
        assert b"do not match" in resp.data.lower()

    def test_change_password_too_short(self, app_client):
        _register_and_login(app_client)
        resp = app_client.post("/account/change-password", data={
            "current_password": "securepass1",
            "new_password": "short",
            "confirm_password": "short",
        }, follow_redirects=True)
        assert b"8" in resp.data


# ===========================================================================
# 8. Nav & context processor (4 tests)
# ===========================================================================
class TestNavContextProcessor:
    def test_customer_sees_dashboard_link(self, app_client):
        _register_and_login(app_client)
        resp = app_client.get("/dashboard")
        assert b"Dashboard" in resp.data
        assert b"My Restaurants" in resp.data

    def test_customer_does_not_see_admin_links(self, app_client):
        _register_and_login(app_client)
        resp = app_client.get("/dashboard")
        # Admin-only links should not appear
        assert b'href="/imports"' not in resp.data
        assert b"Recycle Bin" not in resp.data

    def test_admin_sees_admin_links(self, app_client):
        _admin_login(app_client)
        resp = app_client.get("/restaurants")
        assert b"Import" in resp.data
        assert b"Imports" in resp.data

    def test_customer_nav_shows_username(self, app_client):
        _register_and_login(app_client)
        resp = app_client.get("/dashboard")
        assert b"Test Customer" in resp.data
