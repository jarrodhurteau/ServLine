# tests/test_day128_restaurant_management.py
"""
Day 128 — Sprint 13.1: Restaurant Management & Multi-Restaurant Support.

Deliverables:
  1. Restaurant detail page — /restaurants/<id>/detail with stats & edit form
  2. Restaurant update — POST /restaurants/<id>/update (name, phone, address, cuisine_type, website)
  3. Restaurant delete — POST /restaurants/<id>/delete (soft-delete)
  4. Multi-restaurant switcher — POST /switch-restaurant
  5. storage/users.py — get_restaurant, update_restaurant, delete_restaurant, get_restaurant_stats
  6. Dashboard enhancements — item_count, cuisine_type, active badge, switcher
  7. restaurants.html — detail links for customers
  8. _ensure_restaurant_columns migration — cuisine_type, website, updated_at

32 tests across 8 classes:
  1. get_restaurant + get_restaurant_stats (4)
  2. update_restaurant storage function (4)
  3. delete_restaurant storage function (4)
  4. Restaurant detail page route (4)
  5. Restaurant update route (4)
  6. Restaurant delete route (4)
  7. Multi-restaurant switcher (4)
  8. Dashboard enhancements (4)
"""

from __future__ import annotations

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Schema (Day 128: includes cuisine_type, website, updated_at on restaurants)
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
    position INTEGER DEFAULT 0
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
    # Seed restaurants
    conn.execute("INSERT INTO restaurants (id, name, phone, address) VALUES (1, 'Test Bistro', '555-1234', '123 Main St')")
    conn.execute("INSERT INTO restaurants (id, name, phone, address, cuisine_type) VALUES (2, 'Sushi Palace', '555-5678', '456 Oak Ave', 'japanese')")
    conn.execute("INSERT INTO restaurants (id, name, active) VALUES (3, 'Closed Place', 0)")
    # Seed drafts + items for stats
    conn.execute("INSERT INTO drafts (id, restaurant_id, title, status) VALUES (10, 1, 'Draft A', 'editing')")
    conn.execute("INSERT INTO drafts (id, restaurant_id, title, status) VALUES (11, 1, 'Draft B', 'approved')")
    conn.execute("INSERT INTO draft_items (draft_id, name, price_cents) VALUES (10, 'Burger', 999)")
    conn.execute("INSERT INTO draft_items (draft_id, name, price_cents) VALUES (10, 'Fries', 499)")
    conn.execute("INSERT INTO draft_items (draft_id, name, price_cents) VALUES (11, 'Salad', 799)")
    # Seed menus
    conn.execute("INSERT INTO menus (id, restaurant_id, name, menu_type) VALUES (100, 1, 'Lunch Menu', 'lunch')")
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
    """Helper: register a customer and return client."""
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


def _link_customer_to_restaurant(users_mod, email, restaurant_id, role="owner"):
    """Helper: link a registered user to a restaurant."""
    user = users_mod.get_user_by_email(email)
    users_mod.link_user_restaurant(user["id"], restaurant_id, role=role)
    return user


# ===========================================================================
# 1. get_restaurant + get_restaurant_stats (4 tests)
# ===========================================================================
class TestGetRestaurant:
    def test_get_restaurant_by_id(self, users_mod, mock_db):
        """get_restaurant returns active restaurant dict."""
        rest = users_mod.get_restaurant(1)
        assert rest is not None
        assert rest["name"] == "Test Bistro"
        assert rest["phone"] == "555-1234"

    def test_get_restaurant_inactive_returns_none(self, users_mod, mock_db):
        """get_restaurant returns None for inactive restaurant."""
        rest = users_mod.get_restaurant(3)
        assert rest is None

    def test_get_restaurant_nonexistent(self, users_mod, mock_db):
        """get_restaurant returns None for non-existent id."""
        assert users_mod.get_restaurant(999) is None

    def test_get_restaurant_stats(self, users_mod, mock_db):
        """get_restaurant_stats returns correct counts."""
        stats = users_mod.get_restaurant_stats(1)
        assert stats["draft_count"] == 2
        assert stats["menu_count"] == 1
        assert stats["item_count"] == 3  # 2 in draft 10 + 1 in draft 11


# ===========================================================================
# 2. update_restaurant storage function (4 tests)
# ===========================================================================
class TestUpdateRestaurant:
    def test_update_name_and_phone(self, users_mod, mock_db):
        """update_restaurant changes name and phone."""
        result = users_mod.update_restaurant(1, name="New Bistro", phone="999-0000")
        assert result is True
        rest = users_mod.get_restaurant(1)
        assert rest["name"] == "New Bistro"
        assert rest["phone"] == "999-0000"

    def test_update_cuisine_type_valid(self, users_mod, mock_db):
        """update_restaurant sets valid cuisine type."""
        users_mod.update_restaurant(1, cuisine_type="italian")
        rest = users_mod.get_restaurant(1)
        assert rest["cuisine_type"] == "italian"

    def test_update_cuisine_type_invalid_defaults_other(self, users_mod, mock_db):
        """update_restaurant defaults invalid cuisine to 'other'."""
        users_mod.update_restaurant(1, cuisine_type="alien_food")
        rest = users_mod.get_restaurant(1)
        assert rest["cuisine_type"] == "other"

    def test_update_empty_name_raises(self, users_mod, mock_db):
        """update_restaurant raises ValueError for blank name."""
        with pytest.raises(ValueError, match="cannot be empty"):
            users_mod.update_restaurant(1, name="")


# ===========================================================================
# 3. delete_restaurant storage function (4 tests)
# ===========================================================================
class TestDeleteRestaurant:
    def test_soft_delete(self, users_mod, mock_db):
        """delete_restaurant sets active=0."""
        result = users_mod.delete_restaurant(1)
        assert result is True
        assert users_mod.get_restaurant(1) is None

    def test_delete_nonexistent(self, users_mod, mock_db):
        """delete_restaurant returns False for missing id."""
        assert users_mod.delete_restaurant(999) is False

    def test_delete_already_inactive(self, users_mod, mock_db):
        """delete_restaurant returns False for already-inactive restaurant."""
        assert users_mod.delete_restaurant(3) is False

    def test_delete_sets_updated_at(self, users_mod, mock_db):
        """delete_restaurant sets updated_at timestamp."""
        users_mod.delete_restaurant(2)
        with mock_db() as conn:
            row = conn.execute("SELECT updated_at FROM restaurants WHERE id = 2").fetchone()
        assert row["updated_at"] is not None


# ===========================================================================
# 4. Restaurant detail page route (4 tests)
# ===========================================================================
class TestRestaurantDetailPage:
    def test_requires_login(self, app_client):
        """GET /restaurants/1/detail redirects when not logged in."""
        resp = app_client.get("/restaurants/1/detail")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_customer_can_view_own(self, app_client, users_mod):
        """Customer with access can view restaurant detail."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        resp = app_client.get("/restaurants/1/detail")
        assert resp.status_code == 200
        assert b"Test Bistro" in resp.data

    def test_customer_blocked_from_other(self, app_client, users_mod):
        """Customer without access gets 403."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        resp = app_client.get("/restaurants/2/detail")
        assert resp.status_code == 403

    def test_shows_stats(self, app_client, users_mod):
        """Detail page shows draft/menu/item counts."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        resp = app_client.get("/restaurants/1/detail")
        html = resp.data.decode()
        assert "2" in html  # draft_count
        assert "1" in html  # menu_count
        assert "3" in html  # item_count


# ===========================================================================
# 5. Restaurant update route (4 tests)
# ===========================================================================
class TestRestaurantUpdateRoute:
    def test_update_success(self, app_client, users_mod):
        """POST /restaurants/1/update changes restaurant details."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        resp = app_client.post("/restaurants/1/update", data={
            "name": "Updated Bistro",
            "phone": "111-2222",
            "address": "789 New St",
            "cuisine_type": "italian",
            "website": "https://bistro.com",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Restaurant updated" in resp.data
        rest = users_mod.get_restaurant(1)
        assert rest["name"] == "Updated Bistro"
        assert rest["cuisine_type"] == "italian"
        assert rest["website"] == "https://bistro.com"

    def test_update_empty_name_flashes_error(self, app_client, users_mod):
        """POST with blank name shows error flash."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        resp = app_client.post("/restaurants/1/update", data={
            "name": "",
        }, follow_redirects=True)
        assert b"cannot be empty" in resp.data

    def test_update_blocked_for_non_owner(self, app_client, users_mod):
        """Customer without access gets 403 on update."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        resp = app_client.post("/restaurants/2/update", data={"name": "Hacked"})
        assert resp.status_code == 403

    def test_admin_can_update(self, app_client, users_mod):
        """Admin can update any restaurant."""
        _admin_login(app_client)
        resp = app_client.post("/restaurants/1/update", data={
            "name": "Admin Updated",
            "cuisine_type": "bbq",
        }, follow_redirects=True)
        assert resp.status_code == 200
        rest = users_mod.get_restaurant(1)
        assert rest["name"] == "Admin Updated"
        assert rest["cuisine_type"] == "bbq"


# ===========================================================================
# 6. Restaurant delete route (4 tests)
# ===========================================================================
class TestRestaurantDeleteRoute:
    def test_delete_success(self, app_client, users_mod):
        """POST /restaurants/1/delete soft-deletes and redirects to dashboard."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        resp = app_client.post("/restaurants/1/delete", follow_redirects=True)
        assert resp.status_code == 200
        assert b"Restaurant deleted" in resp.data
        assert users_mod.get_restaurant(1) is None

    def test_delete_clears_session_restaurant_id(self, app_client, users_mod):
        """Deleting the active restaurant clears session restaurant_id."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        # Set restaurant_id in session
        with app_client.session_transaction() as sess:
            sess["user"]["restaurant_id"] = 1
        app_client.post("/restaurants/1/delete")
        with app_client.session_transaction() as sess:
            assert sess["user"].get("restaurant_id") is None

    def test_delete_blocked_for_non_owner(self, app_client, users_mod):
        """Customer without access gets 403 on delete."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        resp = app_client.post("/restaurants/2/delete")
        assert resp.status_code == 403

    def test_admin_can_delete(self, app_client, users_mod):
        """Admin can delete any restaurant."""
        _admin_login(app_client)
        resp = app_client.post("/restaurants/1/delete", follow_redirects=True)
        assert resp.status_code == 200
        assert users_mod.get_restaurant(1) is None


# ===========================================================================
# 7. Multi-restaurant switcher (4 tests)
# ===========================================================================
class TestMultiRestaurantSwitcher:
    def test_switch_success(self, app_client, users_mod):
        """POST /switch-restaurant updates session restaurant_id."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 2)
        resp = app_client.post("/switch-restaurant", data={"restaurant_id": "2"}, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Sushi Palace" in resp.data
        with app_client.session_transaction() as sess:
            assert sess["user"]["restaurant_id"] == 2

    def test_switch_to_unowned_blocked(self, app_client, users_mod):
        """Cannot switch to a restaurant you don't own."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        resp = app_client.post("/switch-restaurant", data={"restaurant_id": "2"}, follow_redirects=True)
        assert b"do not have access" in resp.data

    def test_switch_invalid_id(self, app_client, users_mod):
        """Invalid restaurant_id shows error."""
        _register_and_login(app_client)
        resp = app_client.post("/switch-restaurant", data={"restaurant_id": "abc"}, follow_redirects=True)
        assert b"Invalid restaurant" in resp.data

    def test_switch_redirects_to_custom(self, app_client, users_mod):
        """_redirect form field controls post-switch redirect."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 2)
        resp = app_client.post("/switch-restaurant", data={
            "restaurant_id": "2",
            "_redirect": "/restaurants",
        })
        assert resp.status_code == 302
        assert "/restaurants" in resp.headers["Location"]


# ===========================================================================
# 8. Dashboard enhancements (4 tests)
# ===========================================================================
class TestDashboardEnhancements:
    def test_dashboard_shows_item_count(self, app_client, users_mod):
        """Dashboard cards show item count per restaurant."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        resp = app_client.get("/dashboard")
        html = resp.data.decode()
        assert "3 item" in html  # 3 items across 2 drafts

    def test_dashboard_shows_cuisine_type(self, app_client, users_mod):
        """Dashboard shows cuisine_type badge when set."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 2)
        resp = app_client.get("/dashboard")
        assert b"japanese" in resp.data

    def test_dashboard_shows_active_badge(self, app_client, users_mod):
        """Active restaurant shows 'active' badge."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        with app_client.session_transaction() as sess:
            sess["user"]["restaurant_id"] = 1
        resp = app_client.get("/dashboard")
        assert b"active" in resp.data

    def test_dashboard_switcher_shown_for_multiple(self, app_client, users_mod):
        """Restaurant switcher shown when user has 2+ restaurants."""
        _register_and_login(app_client)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 1)
        _link_customer_to_restaurant(users_mod, "cust@example.com", 2)
        resp = app_client.get("/dashboard")
        html = resp.data.decode()
        assert "switch-restaurant" in html
        assert "Active Restaurant" in html


# ===========================================================================
# Ensure migration function works
# ===========================================================================
class TestEnsureRestaurantColumns:
    def test_migration_idempotent(self, users_mod, mock_db):
        """_ensure_restaurant_columns can run multiple times safely."""
        users_mod._ensure_restaurant_columns()
        users_mod._ensure_restaurant_columns()
        with mock_db() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(restaurants)").fetchall()}
        assert "cuisine_type" in cols
        assert "website" in cols
        assert "updated_at" in cols
