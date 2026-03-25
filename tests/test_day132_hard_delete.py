# tests/test_day132_hard_delete.py
"""
Day 132 — Sprint 13.1: Hard Account Deletion, Re-Registration & Tier Gating.

Deliverables:
  1. delete_user() hard-deletes user + tokens + restaurant links
  2. /account/delete uses hard delete instead of soft delete
  3. Re-registration with the same email works after deletion
  4. Verification tokens cleaned up on delete
  5. Password reset tokens cleaned up on delete
  6. User-restaurant links cleaned up on delete
  7. /api/menus/import blocks free-tier image uploads (API-level gate)
  8. /choose-plan redirects to /import (not /dashboard)
  9. Import page shows locked panels for free tier
  10. Import page shows unlocked panels with sparkle for lightning tier

32 tests across 8 classes:
  1. Hard delete removes user row (4)
  2. Hard delete cleans up tokens (4)
  3. Hard delete cleans up restaurant links (4)
  4. Portal account delete + re-registration (4)
  5. Re-registration after delete (4)
  6. API tier gate on image upload (4)
  7. Plan selection redirect + import landing (4)
  8. Import page tier UI (4)
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


def _create_user(mock_db, email="user@example.com", password="securepass1"):
    import storage.users as _users
    return _users.create_user(email, password, display_name="Test User")


def _register_and_choose_tier(client, tier="free", email="cust@example.com", password="securepass1"):
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
# 1. Hard delete removes user row (4 tests)
# =====================================================================
class TestHardDeleteRemovesUser:
    def test_delete_user_removes_from_db(self, mock_db):
        user = _create_user(mock_db)
        import storage.users as _users
        result = _users.delete_user(user["id"])
        assert result is True
        assert _users.get_user_by_id(user["id"]) is None

    def test_delete_user_email_gone(self, mock_db):
        user = _create_user(mock_db)
        import storage.users as _users
        _users.delete_user(user["id"])
        assert _users.get_user_by_email("user@example.com") is None

    def test_delete_nonexistent_returns_false(self, mock_db):
        import storage.users as _users
        result = _users.delete_user(99999)
        assert result is False

    def test_delete_user_not_in_list(self, mock_db):
        user = _create_user(mock_db)
        import storage.users as _users
        _users.delete_user(user["id"])
        users = _users.list_users(active_only=False)
        ids = [u["id"] for u in users]
        assert user["id"] not in ids


# =====================================================================
# 2. Hard delete cleans up tokens (4 tests)
# =====================================================================
class TestHardDeleteCleansTokens:
    def test_verification_tokens_removed(self, mock_db):
        user = _create_user(mock_db)
        import storage.users as _users
        _users.generate_verification_token(user["id"])
        _users.delete_user(user["id"])
        with mock_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM email_verification_tokens WHERE user_id = ?",
                (user["id"],)
            ).fetchone()[0]
        assert count == 0

    def test_password_reset_tokens_removed(self, mock_db):
        user = _create_user(mock_db)
        import storage.users as _users
        _users.generate_reset_token(user["email"])
        _users.delete_user(user["id"])
        with mock_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM password_reset_tokens WHERE user_id = ?",
                (user["id"],)
            ).fetchone()[0]
        assert count == 0

    def test_multiple_tokens_all_removed(self, mock_db):
        user = _create_user(mock_db)
        import storage.users as _users
        _users.generate_verification_token(user["id"])
        _users.generate_reset_token(user["email"])
        _users.delete_user(user["id"])
        with mock_db() as conn:
            v = conn.execute("SELECT COUNT(*) FROM email_verification_tokens WHERE user_id = ?", (user["id"],)).fetchone()[0]
            p = conn.execute("SELECT COUNT(*) FROM password_reset_tokens WHERE user_id = ?", (user["id"],)).fetchone()[0]
        assert v == 0 and p == 0

    def test_other_user_tokens_unaffected(self, mock_db):
        user1 = _create_user(mock_db, email="u1@example.com")
        user2 = _create_user(mock_db, email="u2@example.com")
        import storage.users as _users
        _users.generate_verification_token(user1["id"])
        _users.generate_verification_token(user2["id"])
        _users.delete_user(user1["id"])
        with mock_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM email_verification_tokens WHERE user_id = ?",
                (user2["id"],)
            ).fetchone()[0]
        assert count == 1


# =====================================================================
# 3. Hard delete cleans up restaurant links (4 tests)
# =====================================================================
class TestHardDeleteCleansRestaurantLinks:
    def test_user_restaurant_links_removed(self, mock_db):
        user = _create_user(mock_db)
        import storage.users as _users
        _users.link_user_restaurant(user["id"], 1, "owner")
        _users.delete_user(user["id"])
        with mock_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM user_restaurants WHERE user_id = ?",
                (user["id"],)
            ).fetchone()[0]
        assert count == 0

    def test_restaurant_still_exists_after_user_delete(self, mock_db):
        user = _create_user(mock_db)
        import storage.users as _users
        _users.link_user_restaurant(user["id"], 1, "owner")
        _users.delete_user(user["id"])
        with mock_db() as conn:
            rest = conn.execute("SELECT * FROM restaurants WHERE id = 1").fetchone()
        assert rest is not None

    def test_other_user_links_unaffected(self, mock_db):
        user1 = _create_user(mock_db, email="u1@example.com")
        user2 = _create_user(mock_db, email="u2@example.com")
        import storage.users as _users
        _users.link_user_restaurant(user1["id"], 1, "owner")
        _users.link_user_restaurant(user2["id"], 1, "manager")
        _users.delete_user(user1["id"])
        links = _users.get_user_restaurants(user2["id"])
        assert len(links) == 1

    def test_multiple_restaurant_links_all_removed(self, mock_db):
        user = _create_user(mock_db)
        import storage.users as _users
        with mock_db() as conn:
            conn.execute("INSERT INTO restaurants (id, name) VALUES (2, 'Second Place')")
            conn.commit()
        _users.link_user_restaurant(user["id"], 1, "owner")
        _users.link_user_restaurant(user["id"], 2, "manager")
        _users.delete_user(user["id"])
        with mock_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM user_restaurants WHERE user_id = ?",
                (user["id"],)
            ).fetchone()[0]
        assert count == 0


# =====================================================================
# 4. Portal account delete endpoint (4 tests)
# =====================================================================
class TestPortalAccountDelete:
    def test_delete_endpoint_redirects(self, app_client):
        _register_and_choose_tier(app_client)
        resp = app_client.post("/account/delete")
        assert resp.status_code in (302, 303)

    def test_delete_endpoint_clears_session(self, app_client):
        _register_and_choose_tier(app_client)
        app_client.post("/account/delete")
        resp = app_client.get("/dashboard", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_delete_endpoint_shows_flash(self, app_client):
        _register_and_choose_tier(app_client)
        resp = app_client.post("/account/delete", follow_redirects=True)
        assert b"deleted" in resp.data.lower()

    def test_delete_endpoint_user_gone_from_db(self, app_client, mock_db):
        _register_and_choose_tier(app_client, email="gone@example.com")
        app_client.post("/account/delete")
        import storage.users as _users
        assert _users.get_user_by_email("gone@example.com") is None


# =====================================================================
# 5. Re-registration after delete (4 tests)
# =====================================================================
class TestReRegistrationAfterDelete:
    def test_can_create_user_with_same_email(self, mock_db):
        import storage.users as _users
        user = _users.create_user("reuse@example.com", "securepass1")
        _users.delete_user(user["id"])
        user2 = _users.create_user("reuse@example.com", "securepass1")
        assert user2["email"] == "reuse@example.com"
        assert user2["id"] != user["id"]

    def test_new_user_has_fresh_state(self, mock_db):
        import storage.users as _users
        user = _users.create_user("reuse@example.com", "securepass1")
        _users.update_user(user["id"], email_verified=1)
        _users.delete_user(user["id"])
        user2 = _users.create_user("reuse@example.com", "securepass1")
        assert user2["email_verified"] is False or user2["email_verified"] == 0

    def test_register_delete_re_register_portal(self, app_client, mock_db):
        _register_and_choose_tier(app_client, email="flow@example.com")
        app_client.post("/account/delete")
        resp = app_client.post("/register", data={
            "email": "flow@example.com",
            "password": "securepass1",
            "confirm_password": "securepass1",
            "display_name": "Flow User 2",
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_re_registered_user_has_no_restaurants(self, app_client, mock_db):
        _register_and_choose_tier(app_client, email="rest@example.com")
        import storage.users as _users
        user = _users.get_user_by_email("rest@example.com")
        _users.link_user_restaurant(user["id"], 1, "owner")
        app_client.post("/account/delete")
        _register_and_choose_tier(app_client, email="rest@example.com")
        user2 = _users.get_user_by_email("rest@example.com")
        links = _users.get_user_restaurants(user2["id"])
        assert len(links) == 0


# =====================================================================
# 6. API tier gate on image upload (4 tests)
# =====================================================================
class TestApiTierGate:
    def test_free_tier_blocked_on_api_upload(self, app_client, mock_db):
        """Free-tier user gets 403 from /api/menus/import."""
        _register_and_choose_tier(app_client, tier="free", email="free@example.com")
        import io
        data = {"file": (io.BytesIO(b"fake image data"), "menu.png")}
        resp = app_client.post("/api/menus/import", data=data, content_type="multipart/form-data")
        assert resp.status_code == 403
        assert b"Premium" in resp.data

    def test_lightning_tier_allowed_api_upload(self, app_client, mock_db):
        """Lightning-tier user is not blocked (may still fail for other reasons, but not 403)."""
        _register_and_choose_tier(app_client, tier="lightning", email="pro@example.com")
        import io
        data = {"file": (io.BytesIO(b"fake image data"), "menu.png")}
        resp = app_client.post("/api/menus/import", data=data, content_type="multipart/form-data")
        assert resp.status_code != 403

    def test_free_tier_error_message(self, app_client, mock_db):
        """403 response includes a clear error message."""
        _register_and_choose_tier(app_client, tier="free", email="msg@example.com")
        import io, json
        data = {"file": (io.BytesIO(b"data"), "menu.jpg")}
        resp = app_client.post("/api/menus/import", data=data, content_type="multipart/form-data")
        body = json.loads(resp.data)
        assert "error" in body
        assert "Premium" in body["error"]

    def test_no_tier_blocked_on_api_upload(self, app_client, mock_db):
        """User with no tier set also gets 403."""
        app_client.post("/register", data={
            "email": "notier@example.com",
            "password": "securepass1",
            "confirm_password": "securepass1",
            "display_name": "No Tier",
        })
        import io
        data = {"file": (io.BytesIO(b"data"), "menu.png")}
        resp = app_client.post("/api/menus/import", data=data, content_type="multipart/form-data")
        assert resp.status_code == 403


# =====================================================================
# 7. Plan selection redirect + import landing (4 tests)
# =====================================================================
class TestPlanRedirectToImport:
    def test_choose_free_redirects_to_import(self, app_client, mock_db):
        """After choosing free plan, user lands on /import."""
        app_client.post("/register", data={
            "email": "redir@example.com",
            "password": "securepass1",
            "confirm_password": "securepass1",
            "display_name": "Redir",
        })
        resp = app_client.post("/choose-plan", data={"tier": "free"}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/import" in resp.headers.get("Location", "")

    def test_choose_lightning_redirects_to_import(self, app_client, mock_db):
        app_client.post("/register", data={
            "email": "redir2@example.com",
            "password": "securepass1",
            "confirm_password": "securepass1",
            "display_name": "Redir2",
        })
        resp = app_client.post("/choose-plan", data={"tier": "lightning"}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/import" in resp.headers.get("Location", "")

    def test_import_page_loads_after_free_plan(self, app_client, mock_db):
        _register_and_choose_tier(app_client, tier="free", email="load@example.com")
        resp = app_client.get("/import")
        assert resp.status_code == 200

    def test_free_flash_mentions_csv(self, app_client, mock_db):
        """Free plan flash message mentions CSV/Excel/JSON."""
        app_client.post("/register", data={
            "email": "flash@example.com",
            "password": "securepass1",
            "confirm_password": "securepass1",
            "display_name": "Flash",
        })
        resp = app_client.post("/choose-plan", data={"tier": "free"}, follow_redirects=True)
        assert b"CSV" in resp.data or b"csv" in resp.data


# =====================================================================
# 8. Import page tier UI (4 tests)
# =====================================================================
class TestImportPageTierUI:
    def test_free_tier_shows_locked_badges(self, app_client, mock_db):
        """Free-tier import page shows locked badges on image/PDF panels."""
        _register_and_choose_tier(app_client, tier="free", email="ui1@example.com")
        resp = app_client.get("/import")
        html = resp.data
        assert b"card-locked" in html
        assert b"Locked" in html

    def test_free_tier_no_upload_forms(self, app_client, mock_db):
        """Free-tier import page has no functional imgForm or pdfForm ids."""
        _register_and_choose_tier(app_client, tier="free", email="ui2@example.com")
        resp = app_client.get("/import")
        html = resp.data
        assert b'id="imgForm"' not in html
        assert b'id="pdfForm"' not in html

    def test_lightning_tier_shows_unlocked_badges(self, app_client, mock_db):
        """Lightning-tier import page shows unlocked badges."""
        _register_and_choose_tier(app_client, tier="lightning", email="ui3@example.com")
        resp = app_client.get("/import")
        html = resp.data
        assert b"card-unlocked" in html
        assert b"Unlocked" in html

    def test_lightning_tier_has_fireworks(self, app_client, mock_db):
        """Lightning-tier import page includes firework animation elements."""
        _register_and_choose_tier(app_client, tier="lightning", email="ui4@example.com")
        resp = app_client.get("/import")
        html = resp.data
        assert b"firework-canvas" in html
        assert b"fw-particle" in html
