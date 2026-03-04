"""
Day 93 -- Seasonal Menu Management & Daypart Scheduling (Phase 10, Sprint 10.3).

Schema additions for scheduling fields on menus table, storage functions for
setting/querying schedules, Flask route for schedule management, template UI.

Covers:
  set_menu_schedule():
  - set season returns True
  - set season persisted in DB
  - invalid season defaults to None
  - set effective dates
  - set active days normalized
  - invalid days stripped
  - set time range
  - invalid time format set to None
  - invalid date format set to None
  - set all fields at once

  clear_menu_schedule():
  - clear returns True
  - clear resets all fields to NULL
  - clear on unscheduled menu returns True
  - clear updates timestamp

  get_scheduled_menus():
  - all menus returned when no filters
  - date filter includes matching
  - date filter excludes outside range
  - date filter includes unscheduled menus
  - time filter includes matching
  - time filter excludes outside range
  - time filter includes unscheduled menus
  - day of week filter includes matching
  - day of week filter excludes non-matching
  - combined date+time+day filters

  get_seasonal_menus():
  - filter by season
  - no season filter returns all with season
  - no seasonal menus returns empty
  - excludes inactive menus
  - invalid season returns empty

  get_menu_schedule_summary():
  - season only
  - date range only
  - days only
  - time range only
  - all fields combined
  - no schedule returns None

  Flask schedule route:
  - set schedule redirects
  - set schedule flash message
  - set schedule persists season
  - set schedule persists dates
  - set schedule persists days from checkboxes
  - set schedule persists times
  - clear schedule resets all
  - clear schedule flash message
  - schedule 404 missing menu

  Template content:
  - schedule form visible in detail
  - schedule summary shown when set
  - season select has options
  - clear button shown when scheduled
  - clear button hidden when no schedule
  - schedule badge in menus list

  Schedule activity:
  - set schedule records activity
  - clear schedule records activity
  - activity action is schedule_updated

  Schema migration:
  - menus has schedule columns
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 92)
# ---------------------------------------------------------------------------
_TEST_CONN: Optional[sqlite3.Connection] = None


def _make_test_db() -> sqlite3.Connection:
    """Create in-memory SQLite DB with full schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            address TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            menu_type TEXT,
            description TEXT,
            season TEXT,
            effective_from TEXT,
            effective_to TEXT,
            active_days TEXT,
            active_start_time TEXT,
            active_end_time TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            is_available INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE
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
            menu_id INTEGER,
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

    # Phase 10 tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_versions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id         INTEGER NOT NULL,
            version_number  INTEGER NOT NULL DEFAULT 1,
            label           TEXT,
            source_draft_id INTEGER,
            item_count      INTEGER NOT NULL DEFAULT 0,
            variant_count   INTEGER NOT NULL DEFAULT 0,
            notes           TEXT,
            created_by      TEXT,
            change_summary  TEXT,
            pinned          INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE,
            FOREIGN KEY (source_draft_id) REFERENCES drafts(id) ON DELETE SET NULL,
            UNIQUE (menu_id, version_number)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_version_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id  INTEGER NOT NULL,
            name        TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            category    TEXT,
            position    INTEGER,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (version_id) REFERENCES menu_versions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_version_item_variants (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id     INTEGER NOT NULL,
            label       TEXT NOT NULL,
            price_cents INTEGER NOT NULL DEFAULT 0,
            kind        TEXT DEFAULT 'size',
            position    INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (item_id) REFERENCES menu_version_items(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_activity (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id     INTEGER NOT NULL,
            version_id  INTEGER,
            action      TEXT NOT NULL,
            detail      TEXT,
            actor       TEXT,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE,
            FOREIGN KEY (version_id) REFERENCES menu_versions(id) ON DELETE SET NULL
        )
    """)

    conn.commit()
    return conn


def _test_db_connect():
    global _TEST_CONN
    if _TEST_CONN is None:
        _TEST_CONN = _make_test_db()
    return _TEST_CONN


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    """Redirect all DB access to in-memory test DB."""
    global _TEST_CONN
    _TEST_CONN = _make_test_db()
    monkeypatch.setattr("storage.drafts.db_connect", _test_db_connect)
    monkeypatch.setattr("storage.menus.db_connect", _test_db_connect)
    yield
    _TEST_CONN = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _seed_restaurant(name: str = "Test Diner") -> int:
    conn = _test_db_connect()
    cur = conn.execute(
        "INSERT INTO restaurants (name, created_at) VALUES (?, datetime('now'))",
        (name,),
    )
    conn.commit()
    return cur.lastrowid


def _seed_menu(restaurant_id: int, name: str = "Main Menu", **kwargs) -> int:
    conn = _test_db_connect()
    cur = conn.execute(
        "INSERT INTO menus (restaurant_id, name, active, created_at) "
        "VALUES (?, ?, 1, datetime('now'))",
        (restaurant_id, name),
    )
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def client(monkeypatch):
    """Flask test client with session auth."""
    from portal import app as app_mod
    import storage.menus as menus_mod

    monkeypatch.setattr(app_mod, "menus_store", menus_mod)
    monkeypatch.setattr(app_mod, "db_connect", lambda: _test_db_connect())

    app_mod.app.config["TESTING"] = True
    app_mod.app.config["SECRET_KEY"] = "test"
    with app_mod.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = {"username": "admin", "role": "admin", "name": "Admin"}
        yield c


# ===================================================================
# TestSetMenuSchedule
# ===================================================================
class TestSetMenuSchedule:
    def test_set_season_returns_true(self):
        from storage.menus import set_menu_schedule
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        assert set_menu_schedule(mid, season="summer") is True

    def test_set_season_persisted_in_db(self):
        from storage.menus import set_menu_schedule, get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, season="winter")
        menu = get_menu(mid)
        assert menu["season"] == "winter"

    def test_invalid_season_defaults_to_none(self):
        from storage.menus import set_menu_schedule, get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, season="monsoon")
        menu = get_menu(mid)
        assert menu["season"] is None

    def test_set_effective_dates(self):
        from storage.menus import set_menu_schedule, get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, effective_from="2026-06-01", effective_to="2026-08-31")
        menu = get_menu(mid)
        assert menu["effective_from"] == "2026-06-01"
        assert menu["effective_to"] == "2026-08-31"

    def test_set_active_days_normalized(self):
        from storage.menus import set_menu_schedule, get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, active_days="Mon, WED, fri")
        menu = get_menu(mid)
        assert menu["active_days"] == "mon,wed,fri"

    def test_invalid_days_stripped(self):
        from storage.menus import set_menu_schedule, get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, active_days="mon,xyz,tue")
        menu = get_menu(mid)
        assert menu["active_days"] == "mon,tue"

    def test_set_time_range(self):
        from storage.menus import set_menu_schedule, get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, active_start_time="11:00", active_end_time="14:00")
        menu = get_menu(mid)
        assert menu["active_start_time"] == "11:00"
        assert menu["active_end_time"] == "14:00"

    def test_invalid_time_format_set_to_none(self):
        from storage.menus import set_menu_schedule, get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, active_start_time="11am", active_end_time="2pm")
        menu = get_menu(mid)
        assert menu["active_start_time"] is None
        assert menu["active_end_time"] is None

    def test_invalid_date_format_set_to_none(self):
        from storage.menus import set_menu_schedule, get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, effective_from="June 1", effective_to="Aug 31")
        menu = get_menu(mid)
        assert menu["effective_from"] is None
        assert menu["effective_to"] is None

    def test_set_all_fields_at_once(self):
        from storage.menus import set_menu_schedule, get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(
            mid,
            season="summer",
            effective_from="2026-06-01",
            effective_to="2026-08-31",
            active_days="mon,wed,fri",
            active_start_time="11:00",
            active_end_time="14:00",
        )
        menu = get_menu(mid)
        assert menu["season"] == "summer"
        assert menu["effective_from"] == "2026-06-01"
        assert menu["effective_to"] == "2026-08-31"
        assert menu["active_days"] == "mon,wed,fri"
        assert menu["active_start_time"] == "11:00"
        assert menu["active_end_time"] == "14:00"


# ===================================================================
# TestClearMenuSchedule
# ===================================================================
class TestClearMenuSchedule:
    def test_clear_returns_true(self):
        from storage.menus import set_menu_schedule, clear_menu_schedule
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, season="fall")
        assert clear_menu_schedule(mid) is True

    def test_clear_resets_all_fields_to_null(self):
        from storage.menus import set_menu_schedule, clear_menu_schedule, get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(
            mid, season="fall", effective_from="2026-09-01",
            active_days="mon,tue", active_start_time="08:00",
        )
        clear_menu_schedule(mid)
        menu = get_menu(mid)
        assert menu["season"] is None
        assert menu["effective_from"] is None
        assert menu["effective_to"] is None
        assert menu["active_days"] is None
        assert menu["active_start_time"] is None
        assert menu["active_end_time"] is None

    def test_clear_on_unscheduled_menu_returns_true(self):
        from storage.menus import clear_menu_schedule
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        assert clear_menu_schedule(mid) is True

    def test_clear_updates_timestamp(self):
        from storage.menus import clear_menu_schedule, get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        clear_menu_schedule(mid)
        menu = get_menu(mid)
        assert menu["updated_at"] is not None


# ===================================================================
# TestGetScheduledMenus
# ===================================================================
class TestGetScheduledMenus:
    def test_all_menus_returned_when_no_filters(self):
        from storage.menus import get_scheduled_menus
        rid = _seed_restaurant()
        _seed_menu(rid, "A")
        _seed_menu(rid, "B")
        results = get_scheduled_menus(rid)
        assert len(results) == 2

    def test_date_filter_includes_matching(self):
        from storage.menus import set_menu_schedule, get_scheduled_menus
        rid = _seed_restaurant()
        mid = _seed_menu(rid, "Summer Special")
        set_menu_schedule(mid, effective_from="2026-06-01", effective_to="2026-08-31")
        results = get_scheduled_menus(rid, date="2026-07-15")
        assert any(m["id"] == mid for m in results)

    def test_date_filter_excludes_outside_range(self):
        from storage.menus import set_menu_schedule, get_scheduled_menus
        rid = _seed_restaurant()
        mid = _seed_menu(rid, "Summer Only")
        set_menu_schedule(mid, effective_from="2026-06-01", effective_to="2026-08-31")
        results = get_scheduled_menus(rid, date="2026-12-01")
        assert not any(m["id"] == mid for m in results)

    def test_date_filter_includes_unscheduled_menus(self):
        from storage.menus import set_menu_schedule, get_scheduled_menus
        rid = _seed_restaurant()
        always = _seed_menu(rid, "Always On")
        summer = _seed_menu(rid, "Summer")
        set_menu_schedule(summer, effective_from="2026-06-01", effective_to="2026-08-31")
        results = get_scheduled_menus(rid, date="2026-07-15")
        ids = {m["id"] for m in results}
        assert always in ids
        assert summer in ids

    def test_time_filter_includes_matching(self):
        from storage.menus import set_menu_schedule, get_scheduled_menus
        rid = _seed_restaurant()
        mid = _seed_menu(rid, "Lunch")
        set_menu_schedule(mid, active_start_time="11:00", active_end_time="14:00")
        results = get_scheduled_menus(rid, time="12:30")
        assert any(m["id"] == mid for m in results)

    def test_time_filter_excludes_outside_range(self):
        from storage.menus import set_menu_schedule, get_scheduled_menus
        rid = _seed_restaurant()
        mid = _seed_menu(rid, "Lunch")
        set_menu_schedule(mid, active_start_time="11:00", active_end_time="14:00")
        results = get_scheduled_menus(rid, time="08:00")
        assert not any(m["id"] == mid for m in results)

    def test_time_filter_includes_unscheduled_menus(self):
        from storage.menus import set_menu_schedule, get_scheduled_menus
        rid = _seed_restaurant()
        always = _seed_menu(rid, "Always")
        lunch = _seed_menu(rid, "Lunch")
        set_menu_schedule(lunch, active_start_time="11:00", active_end_time="14:00")
        results = get_scheduled_menus(rid, time="12:00")
        ids = {m["id"] for m in results}
        assert always in ids
        assert lunch in ids

    def test_day_of_week_filter_includes_matching(self):
        from storage.menus import set_menu_schedule, get_scheduled_menus
        rid = _seed_restaurant()
        mid = _seed_menu(rid, "Weekday Menu")
        set_menu_schedule(mid, active_days="mon,tue,wed,thu,fri")
        results = get_scheduled_menus(rid, day_of_week="wed")
        assert any(m["id"] == mid for m in results)

    def test_day_of_week_filter_excludes_non_matching(self):
        from storage.menus import set_menu_schedule, get_scheduled_menus
        rid = _seed_restaurant()
        mid = _seed_menu(rid, "Weekday Menu")
        set_menu_schedule(mid, active_days="mon,tue,wed,thu,fri")
        results = get_scheduled_menus(rid, day_of_week="sat")
        assert not any(m["id"] == mid for m in results)

    def test_combined_date_time_day_filters(self):
        from storage.menus import set_menu_schedule, get_scheduled_menus
        rid = _seed_restaurant()
        lunch = _seed_menu(rid, "Summer Weekday Lunch")
        set_menu_schedule(
            lunch,
            effective_from="2026-06-01", effective_to="2026-08-31",
            active_start_time="11:00", active_end_time="14:00",
            active_days="mon,tue,wed,thu,fri",
        )
        # Wednesday July 15 at noon — should match
        results = get_scheduled_menus(rid, date="2026-07-15", time="12:00", day_of_week="wed")
        assert any(m["id"] == lunch for m in results)
        # Saturday — day mismatch
        results = get_scheduled_menus(rid, date="2026-07-18", time="12:00", day_of_week="sat")
        assert not any(m["id"] == lunch for m in results)


# ===================================================================
# TestGetSeasonalMenus
# ===================================================================
class TestGetSeasonalMenus:
    def test_filter_by_season(self):
        from storage.menus import set_menu_schedule, get_seasonal_menus
        rid = _seed_restaurant()
        mid = _seed_menu(rid, "Summer Menu")
        set_menu_schedule(mid, season="summer")
        results = get_seasonal_menus(rid, season="summer")
        assert len(results) == 1
        assert results[0]["id"] == mid

    def test_no_season_filter_returns_all_with_season(self):
        from storage.menus import set_menu_schedule, get_seasonal_menus
        rid = _seed_restaurant()
        s1 = _seed_menu(rid, "Summer")
        s2 = _seed_menu(rid, "Winter")
        _seed_menu(rid, "Regular")  # no season
        set_menu_schedule(s1, season="summer")
        set_menu_schedule(s2, season="winter")
        results = get_seasonal_menus(rid)
        assert len(results) == 2

    def test_no_seasonal_menus_returns_empty(self):
        from storage.menus import get_seasonal_menus
        rid = _seed_restaurant()
        _seed_menu(rid, "Regular")
        results = get_seasonal_menus(rid, season="spring")
        assert results == []

    def test_excludes_inactive_menus(self):
        from storage.menus import set_menu_schedule, get_seasonal_menus
        rid = _seed_restaurant()
        mid = _seed_menu(rid, "Summer")
        set_menu_schedule(mid, season="summer")
        # Soft-delete the menu
        conn = _test_db_connect()
        conn.execute("UPDATE menus SET active=0 WHERE id=?", (mid,))
        conn.commit()
        results = get_seasonal_menus(rid, season="summer")
        assert results == []

    def test_invalid_season_returns_empty(self):
        from storage.menus import set_menu_schedule, get_seasonal_menus
        rid = _seed_restaurant()
        mid = _seed_menu(rid, "Summer")
        set_menu_schedule(mid, season="summer")
        results = get_seasonal_menus(rid, season="monsoon")
        assert results == []


# ===================================================================
# TestGetMenuScheduleSummary
# ===================================================================
class TestGetMenuScheduleSummary:
    def test_season_only(self):
        from storage.menus import get_menu_schedule_summary
        result = get_menu_schedule_summary({"season": "summer"})
        assert result == "Summer"

    def test_date_range_only(self):
        from storage.menus import get_menu_schedule_summary
        result = get_menu_schedule_summary({
            "effective_from": "2026-06-01", "effective_to": "2026-08-31",
        })
        assert "2026-06-01" in result
        assert "2026-08-31" in result

    def test_days_only(self):
        from storage.menus import get_menu_schedule_summary
        result = get_menu_schedule_summary({"active_days": "mon,wed,fri"})
        assert result == "MON,WED,FRI"

    def test_time_range_only(self):
        from storage.menus import get_menu_schedule_summary
        result = get_menu_schedule_summary({
            "active_start_time": "11:00", "active_end_time": "14:00",
        })
        assert "11:00" in result
        assert "14:00" in result

    def test_all_fields(self):
        from storage.menus import get_menu_schedule_summary
        result = get_menu_schedule_summary({
            "season": "summer",
            "effective_from": "2026-06-01",
            "effective_to": "2026-08-31",
            "active_days": "mon,wed,fri",
            "active_start_time": "11:00",
            "active_end_time": "14:00",
        })
        assert "Summer" in result
        assert "2026-06-01" in result
        assert "MON,WED,FRI" in result
        assert "11:00" in result

    def test_no_schedule_returns_none(self):
        from storage.menus import get_menu_schedule_summary
        result = get_menu_schedule_summary({})
        assert result is None


# ===================================================================
# TestFlaskScheduleRoute
# ===================================================================
class TestFlaskScheduleRoute:
    def test_set_schedule_redirects(self, client):
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        resp = client.post(f"/menus/{mid}/schedule", data={"season": "summer"})
        assert resp.status_code == 302

    def test_set_schedule_flash_message(self, client):
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        resp = client.post(
            f"/menus/{mid}/schedule",
            data={"season": "summer"},
            follow_redirects=True,
        )
        assert b"Schedule updated" in resp.data

    def test_set_schedule_persists_season(self, client):
        from storage.menus import get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        client.post(f"/menus/{mid}/schedule", data={"season": "winter"})
        menu = get_menu(mid)
        assert menu["season"] == "winter"

    def test_set_schedule_persists_dates(self, client):
        from storage.menus import get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        client.post(f"/menus/{mid}/schedule", data={
            "effective_from": "2026-06-01",
            "effective_to": "2026-08-31",
        })
        menu = get_menu(mid)
        assert menu["effective_from"] == "2026-06-01"
        assert menu["effective_to"] == "2026-08-31"

    def test_set_schedule_persists_days_from_checkboxes(self, client):
        from storage.menus import get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        resp = client.post(f"/menus/{mid}/schedule", data={
            "active_days": ["mon", "wed", "fri"],
        }, follow_redirects=True)
        assert resp.status_code == 200
        menu = get_menu(mid)
        assert menu["active_days"] == "mon,wed,fri"

    def test_set_schedule_persists_times(self, client):
        from storage.menus import get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        client.post(f"/menus/{mid}/schedule", data={
            "active_start_time": "11:00",
            "active_end_time": "14:00",
        })
        menu = get_menu(mid)
        assert menu["active_start_time"] == "11:00"
        assert menu["active_end_time"] == "14:00"

    def test_clear_schedule_resets_all(self, client):
        from storage.menus import set_menu_schedule, get_menu
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, season="fall", active_days="mon,tue")
        client.post(f"/menus/{mid}/schedule", data={"clear": "1"})
        menu = get_menu(mid)
        assert menu["season"] is None
        assert menu["active_days"] is None

    def test_clear_schedule_flash_message(self, client):
        from storage.menus import set_menu_schedule
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, season="fall")
        resp = client.post(
            f"/menus/{mid}/schedule",
            data={"clear": "1"},
            follow_redirects=True,
        )
        assert b"Schedule cleared" in resp.data

    def test_schedule_404_missing_menu(self, client):
        resp = client.post("/menus/9999/schedule", data={"season": "summer"})
        assert resp.status_code == 404


# ===================================================================
# TestTemplateContent
# ===================================================================
class TestTemplateContent:
    def test_schedule_form_visible_in_detail(self, client):
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        resp = client.get(f"/menus/{mid}/detail")
        assert b"scheduleForm" in resp.data
        assert b"Set Schedule" in resp.data

    def test_schedule_summary_shown_when_set(self, client):
        from storage.menus import set_menu_schedule
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, season="summer")
        resp = client.get(f"/menus/{mid}/detail")
        assert b"Summer" in resp.data
        assert b"Edit Schedule" in resp.data

    def test_season_select_has_options(self, client):
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        resp = client.get(f"/menus/{mid}/detail")
        assert b"spring" in resp.data.lower()
        assert b"summer" in resp.data.lower()
        assert b"fall" in resp.data.lower()
        assert b"winter" in resp.data.lower()

    def test_clear_button_shown_when_scheduled(self, client):
        from storage.menus import set_menu_schedule
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, season="fall")
        resp = client.get(f"/menus/{mid}/detail")
        assert b"Clear Schedule" in resp.data

    def test_clear_button_hidden_when_no_schedule(self, client):
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        resp = client.get(f"/menus/{mid}/detail")
        assert b"Clear Schedule" not in resp.data

    def test_schedule_badge_in_menus_list(self, client):
        from storage.menus import set_menu_schedule
        rid = _seed_restaurant()
        mid = _seed_menu(rid, "Summer Special")
        set_menu_schedule(mid, season="summer")
        resp = client.get(f"/restaurants/{rid}/menus")
        assert b"season-summer" in resp.data


# ===================================================================
# TestScheduleActivity
# ===================================================================
class TestScheduleActivity:
    def test_set_schedule_records_activity(self, client):
        from storage.menus import list_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        client.post(f"/menus/{mid}/schedule", data={"season": "summer"})
        acts = list_menu_activity(mid)
        assert len(acts) >= 1
        assert acts[0]["action"] == "schedule_updated"

    def test_clear_schedule_records_activity(self, client):
        from storage.menus import set_menu_schedule, list_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        set_menu_schedule(mid, season="fall")
        client.post(f"/menus/{mid}/schedule", data={"clear": "1"})
        acts = list_menu_activity(mid)
        assert any(a["detail"] == "Cleared schedule" for a in acts)

    def test_activity_action_is_schedule_updated(self, client):
        from storage.menus import list_menu_activity
        rid = _seed_restaurant()
        mid = _seed_menu(rid)
        client.post(f"/menus/{mid}/schedule", data={
            "season": "winter",
            "active_days": ["mon", "fri"],
        })
        acts = list_menu_activity(mid)
        assert all(a["action"] == "schedule_updated" for a in acts)


# ===================================================================
# TestSchemaMigration
# ===================================================================
class TestSchemaMigration:
    def test_menus_has_schedule_columns(self):
        conn = _test_db_connect()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(menus)").fetchall()}
        for col in ("season", "effective_from", "effective_to",
                     "active_days", "active_start_time", "active_end_time"):
            assert col in cols, f"Missing column: {col}"
