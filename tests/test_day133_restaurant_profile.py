# tests/test_day133_restaurant_profile.py
"""
Day 133 — Sprint 13.2: Restaurant Profile Collection (zip_code + cuisine for price intelligence).

Deliverables:
  1. zip_code column added to restaurants table via migration
  2. update_restaurant() accepts and stores zip_code
  3. create_restaurant route accepts zip_code and cuisine_type
  4. Restaurant detail form includes zip_code field
  5. Import page shows profile banner when cuisine/zip missing
  6. Import page hides profile banner when profile complete
  7. Profile update from import page redirects back to import
  8. zip_code validation (strip, max 10 chars)

32 tests across 8 classes:
  1. Schema migration adds zip_code column (4)
  2. update_restaurant stores zip_code (4)
  3. Restaurant creation with zip_code (4)
  4. Restaurant detail page shows zip_code (4)
  5. Import page profile banner visibility (4)
  6. Profile update redirect from import (4)
  7. Zip code validation and edge cases (4)
  8. Cuisine + zip round-trip via update (4)
"""

from __future__ import annotations

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Schema (Day 133: includes zip_code column)
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


def _register_and_choose_tier(client, tier="lightning", email="cust@example.com", password="securepass1"):
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


# =====================================================================
# 1. Schema migration adds zip_code column (4 tests)
# =====================================================================
class TestSchemaMigration:
    def test_zip_code_column_added(self, mock_db):
        """_ensure_restaurant_columns adds zip_code."""
        import storage.users as _users
        _users._ensure_restaurant_columns()
        with mock_db() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(restaurants)").fetchall()}
        assert "zip_code" in cols

    def test_migration_idempotent(self, mock_db):
        """Running migration twice doesn't error."""
        import storage.users as _users
        _users._ensure_restaurant_columns()
        _users._ensure_restaurant_columns()  # second call should be fine
        with mock_db() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(restaurants)").fetchall()}
        assert "zip_code" in cols

    def test_zip_code_default_null(self, mock_db):
        """Existing restaurants have NULL zip_code after migration."""
        with mock_db() as conn:
            row = conn.execute("SELECT zip_code FROM restaurants WHERE id = 1").fetchone()
        assert row["zip_code"] is None

    def test_cuisine_type_still_present(self, mock_db):
        """Migration doesn't break existing cuisine_type column."""
        import storage.users as _users
        _users._ensure_restaurant_columns()
        with mock_db() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(restaurants)").fetchall()}
        assert "cuisine_type" in cols


# =====================================================================
# 2. update_restaurant stores zip_code (4 tests)
# =====================================================================
class TestUpdateRestaurantZipCode:
    def test_set_zip_code(self, mock_db):
        import storage.users as _users
        _users.update_restaurant(1, zip_code="10001")
        rest = _users.get_restaurant(1)
        assert rest["zip_code"] == "10001"

    def test_set_zip_code_with_hyphen(self, mock_db):
        import storage.users as _users
        _users.update_restaurant(1, zip_code="10001-2345")
        rest = _users.get_restaurant(1)
        assert rest["zip_code"] == "10001-2345"

    def test_clear_zip_code(self, mock_db):
        import storage.users as _users
        _users.update_restaurant(1, zip_code="10001")
        _users.update_restaurant(1, zip_code="")
        rest = _users.get_restaurant(1)
        assert rest["zip_code"] is None

    def test_zip_code_with_other_fields(self, mock_db):
        import storage.users as _users
        _users.update_restaurant(1, zip_code="90210", cuisine_type="italian")
        rest = _users.get_restaurant(1)
        assert rest["zip_code"] == "90210"
        assert rest["cuisine_type"] == "italian"


# =====================================================================
# 3. Restaurant creation with zip_code (4 tests)
# =====================================================================
class TestCreateRestaurantWithZip:
    def test_create_with_zip_and_cuisine(self, app_client, mock_db):
        _register_and_choose_tier(app_client, tier="lightning")
        resp = app_client.post("/restaurants", data={
            "name": "Pizza Palace",
            "zip_code": "60614",
            "cuisine_type": "pizza",
        }, follow_redirects=True)
        assert resp.status_code == 200
        with mock_db() as conn:
            row = conn.execute("SELECT zip_code, cuisine_type FROM restaurants WHERE name = 'Pizza Palace'").fetchone()
        assert row is not None
        assert row["zip_code"] == "60614"
        assert row["cuisine_type"] == "pizza"

    def test_create_without_zip(self, app_client, mock_db):
        _register_and_choose_tier(app_client, tier="lightning")
        app_client.post("/restaurants", data={"name": "No Zip Cafe"}, follow_redirects=True)
        with mock_db() as conn:
            row = conn.execute("SELECT zip_code FROM restaurants WHERE name = 'No Zip Cafe'").fetchone()
        assert row is not None
        assert row["zip_code"] is None

    def test_create_with_blank_zip_stores_null(self, app_client, mock_db):
        _register_and_choose_tier(app_client, tier="lightning")
        app_client.post("/restaurants", data={"name": "Blank Zip", "zip_code": ""}, follow_redirects=True)
        with mock_db() as conn:
            row = conn.execute("SELECT zip_code FROM restaurants WHERE name = 'Blank Zip'").fetchone()
        assert row is not None
        assert row["zip_code"] is None

    def test_create_with_cuisine_stored(self, app_client, mock_db):
        _register_and_choose_tier(app_client, tier="lightning")
        app_client.post("/restaurants", data={
            "name": "Thai Place", "cuisine_type": "thai",
        }, follow_redirects=True)
        with mock_db() as conn:
            row = conn.execute("SELECT cuisine_type FROM restaurants WHERE name = 'Thai Place'").fetchone()
        assert row is not None
        assert row["cuisine_type"] == "thai"


# =====================================================================
# 4. Restaurant detail page shows address breakdown (4 tests)
# =====================================================================
class TestRestaurantDetailFormLayout:
    def _setup_customer_with_restaurant(self, client, mock_db):
        """Register customer, link to restaurant #1."""
        _register_and_choose_tier(client, tier="lightning")
        import storage.users as _users
        with mock_db() as conn:
            uid = conn.execute("SELECT id FROM users ORDER BY id DESC LIMIT 1").fetchone()[0]
        _users.link_user_restaurant(uid, 1, role="owner")

    def test_label_says_restaurant_name(self, app_client, mock_db):
        self._setup_customer_with_restaurant(app_client, mock_db)
        resp = app_client.get("/restaurants/1/detail")
        html = resp.data.decode()
        assert "Restaurant Name" in html

    def test_address_breakdown_fields_present(self, app_client, mock_db):
        self._setup_customer_with_restaurant(app_client, mock_db)
        resp = app_client.get("/restaurants/1/detail")
        html = resp.data.decode()
        assert 'name="address_line2"' in html
        assert 'name="city"' in html
        assert 'name="state"' in html
        assert 'name="zip_code"' in html

    def test_cuisine_before_address(self, app_client, mock_db):
        """Cuisine type should appear before address in the form."""
        self._setup_customer_with_restaurant(app_client, mock_db)
        resp = app_client.get("/restaurants/1/detail")
        html = resp.data.decode()
        cuisine_pos = html.index('name="cuisine_type"')
        address_pos = html.index('name="address"')
        assert cuisine_pos < address_pos

    def test_state_maxlength_2(self, app_client, mock_db):
        self._setup_customer_with_restaurant(app_client, mock_db)
        resp = app_client.get("/restaurants/1/detail")
        html = resp.data.decode()
        assert 'maxlength="2"' in html


# =====================================================================
# 5. Import page profile banner visibility (4 tests)
# =====================================================================
class TestImportProfileBanner:
    def _setup_customer(self, client, mock_db, zip_code=None, cuisine_type=None):
        _register_and_choose_tier(client, tier="lightning")
        import storage.users as _users
        with mock_db() as conn:
            uid = conn.execute("SELECT id FROM users ORDER BY id DESC LIMIT 1").fetchone()[0]
        _users.link_user_restaurant(uid, 1, role="owner")
        # Set the session restaurant_id
        with client.session_transaction() as sess:
            u = sess.get("user", {})
            sess["user"] = {**u, "restaurant_id": 1}
        if zip_code or cuisine_type:
            kwargs = {}
            if zip_code:
                kwargs["zip_code"] = zip_code
            if cuisine_type:
                kwargs["cuisine_type"] = cuisine_type
            _users.update_restaurant(1, **kwargs)

    def test_banner_shown_when_no_cuisine_no_zip(self, app_client, mock_db):
        self._setup_customer(app_client, mock_db)
        resp = app_client.get("/import")
        html = resp.data.decode()
        assert "Complete Your Restaurant Profile" in html

    def test_banner_shown_when_cuisine_only(self, app_client, mock_db):
        self._setup_customer(app_client, mock_db, cuisine_type="pizza")
        resp = app_client.get("/import")
        html = resp.data.decode()
        assert "Complete Your Restaurant Profile" in html

    def test_banner_shown_when_zip_only(self, app_client, mock_db):
        self._setup_customer(app_client, mock_db, zip_code="10001")
        resp = app_client.get("/import")
        html = resp.data.decode()
        assert "Complete Your Restaurant Profile" in html

    def test_banner_hidden_when_both_set(self, app_client, mock_db):
        self._setup_customer(app_client, mock_db, zip_code="10001", cuisine_type="pizza")
        resp = app_client.get("/import")
        html = resp.data.decode()
        assert "Complete Your Restaurant Profile" not in html


# =====================================================================
# 6. Profile update redirect from import (4 tests)
# =====================================================================
class TestProfileUpdateRedirect:
    def _setup_customer(self, client, mock_db):
        _register_and_choose_tier(client, tier="lightning")
        import storage.users as _users
        with mock_db() as conn:
            uid = conn.execute("SELECT id FROM users ORDER BY id DESC LIMIT 1").fetchone()[0]
        _users.link_user_restaurant(uid, 1, role="owner")
        with client.session_transaction() as sess:
            u = sess.get("user", {})
            sess["user"] = {**u, "restaurant_id": 1}

    def test_update_with_redirect_to_import(self, app_client, mock_db):
        self._setup_customer(app_client, mock_db)
        resp = app_client.post("/restaurants/1/update", data={
            "name": "Test Bistro",
            "cuisine_type": "italian",
            "zip_code": "10001",
            "_redirect": "/import",
        })
        assert resp.status_code == 302
        assert "/import" in resp.headers["Location"]

    def test_update_without_redirect_goes_to_detail(self, app_client, mock_db):
        self._setup_customer(app_client, mock_db)
        resp = app_client.post("/restaurants/1/update", data={
            "name": "Test Bistro",
            "cuisine_type": "italian",
            "zip_code": "10001",
        })
        assert resp.status_code == 302
        assert "/restaurants/1" in resp.headers["Location"]

    def test_profile_saved_after_redirect(self, app_client, mock_db):
        self._setup_customer(app_client, mock_db)
        app_client.post("/restaurants/1/update", data={
            "name": "Test Bistro",
            "cuisine_type": "pizza",
            "zip_code": "60614",
            "_redirect": "/import",
        })
        import storage.users as _users
        rest = _users.get_restaurant(1)
        assert rest["cuisine_type"] == "pizza"
        assert rest["zip_code"] == "60614"

    def test_banner_gone_after_profile_update(self, app_client, mock_db):
        self._setup_customer(app_client, mock_db)
        app_client.post("/restaurants/1/update", data={
            "name": "Test Bistro",
            "cuisine_type": "mexican",
            "zip_code": "90210",
            "_redirect": "/import",
        }, follow_redirects=True)
        resp = app_client.get("/import")
        html = resp.data.decode()
        assert "Complete Your Restaurant Profile" not in html


# =====================================================================
# 7. Address field validation and edge cases (4 tests)
# =====================================================================
class TestAddressFieldValidation:
    def test_zip_whitespace_stripped(self, mock_db):
        import storage.users as _users
        _users.update_restaurant(1, zip_code="  10001  ")
        rest = _users.get_restaurant(1)
        assert rest["zip_code"] == "10001"

    def test_long_zip_truncated(self, mock_db):
        import storage.users as _users
        _users.update_restaurant(1, zip_code="12345678901234")
        rest = _users.get_restaurant(1)
        assert len(rest["zip_code"]) <= 10

    def test_state_uppercased_and_truncated(self, mock_db):
        import storage.users as _users
        _users.update_restaurant(1, state="california")
        rest = _users.get_restaurant(1)
        assert rest["state"] == "CA"

    def test_empty_address_fields_become_null(self, mock_db):
        import storage.users as _users
        _users.update_restaurant(1, address_line2="  ", city="", state="  ", zip_code="   ")
        rest = _users.get_restaurant(1)
        assert rest["address_line2"] is None
        assert rest["city"] is None
        assert rest["state"] is None
        assert rest["zip_code"] is None


# =====================================================================
# 8. Full address + cuisine round-trip (4 tests)
# =====================================================================
class TestFullAddressRoundTrip:
    def test_set_full_address(self, mock_db):
        import storage.users as _users
        _users.update_restaurant(1, address="123 Main St", address_line2="Suite 4",
                                 city="Chicago", state="il", zip_code="60614")
        rest = _users.get_restaurant(1)
        assert rest["address"] == "123 Main St"
        assert rest["address_line2"] == "Suite 4"
        assert rest["city"] == "Chicago"
        assert rest["state"] == "IL"
        assert rest["zip_code"] == "60614"

    def test_update_city_preserves_zip(self, mock_db):
        import storage.users as _users
        _users.update_restaurant(1, city="New York", state="ny", zip_code="10001")
        _users.update_restaurant(1, city="Brooklyn")
        rest = _users.get_restaurant(1)
        assert rest["city"] == "Brooklyn"
        assert rest["state"] == "NY"
        assert rest["zip_code"] == "10001"

    def test_address_with_cuisine(self, mock_db):
        import storage.users as _users
        _users.update_restaurant(1, cuisine_type="italian", city="Boston", zip_code="02101")
        rest = _users.get_restaurant(1)
        assert rest["cuisine_type"] == "italian"
        assert rest["city"] == "Boston"
        assert rest["zip_code"] == "02101"

    def test_get_restaurant_returns_all_address_fields(self, mock_db):
        import storage.users as _users
        _users.update_restaurant(1, address="456 Oak Ave", address_line2="Apt 2B",
                                 city="Miami", state="fl", zip_code="33101",
                                 cuisine_type="caribbean", website="https://food.com")
        rest = _users.get_restaurant(1)
        assert rest["name"] == "Test Bistro"
        assert rest["address"] == "456 Oak Ave"
        assert rest["address_line2"] == "Apt 2B"
        assert rest["city"] == "Miami"
        assert rest["state"] == "FL"
        assert rest["zip_code"] == "33101"
        assert rest["cuisine_type"] == "caribbean"
        assert rest["website"] == "https://food.com"
