"""
Day 94 -- Active Menu Switching & Rotation (Phase 10, Sprint 10.3).

Builds on Day 93 scheduling to resolve which menus are "live" right now.
Specificity scoring ranks overlapping schedules.  Rotation timeline shows
full-day menu progression.  Next-transition predicts upcoming switches.

Covers:
  _schedule_field_count():
  - zero fields returns 0
  - season only returns 1
  - all fields returns 4
  - partial date range returns 1
  - partial time range returns 1

  score_menu_specificity():
  - unscheduled menu scores 0
  - season only scores 15
  - full date range scores 25
  - partial date range scores 10
  - active days scores 20
  - full time range scores 30
  - partial time range scores 10
  - menu_type adds 10
  - all fields combined scores 100
  - partial combination

  get_active_menus():
  - returns all menus when no schedules
  - includes specificity_score field
  - includes is_scheduled field
  - sorted by specificity desc
  - scheduled menu ranked above unscheduled
  - filters by date
  - filters by time
  - filters by day of week
  - excludes outside time window
  - empty restaurant returns empty list

  get_menu_rotation():
  - all-day menus in All Day slot
  - timed menus grouped by window
  - mixed timed and all-day
  - slots sorted by start time
  - no menus returns empty list
  - date filter applies
  - day of week filter applies

  get_next_transition():
  - returns soonest start transition
  - returns soonest end transition
  - returns None when no transitions
  - skips past transitions
  - mixed starts and ends picks soonest
  - filters by date range
  - filters by day of week

  get_active_menu_summary():
  - returns all fields
  - primary_menu is highest specificity
  - empty restaurant returns None primary
  - includes rotation
  - includes next_transition

  Flask active menus page:
  - page loads 200
  - page shows active count
  - page shows primary menu name
  - page shows rotation timeline
  - page shows next transition
  - page with date/time query params
  - 404 for missing restaurant

  API active menus endpoint:
  - returns JSON with active menus
  - returns primary menu
  - returns next transition
  - accepts date/time/day params
  - empty restaurant returns empty

  Template content:
  - active_menus template has query form
  - active_menus template has rotation section
  - menus list has Active Now link

  Schema continuity:
  - menus table has all schedule columns
"""

from __future__ import annotations

import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 93)
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
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id INTEGER NOT NULL,
            version_number INTEGER NOT NULL,
            label TEXT,
            item_count INTEGER NOT NULL DEFAULT 0,
            variant_count INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            change_summary TEXT,
            pinned INTEGER NOT NULL DEFAULT 0,
            created_by TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(menu_id, version_number),
            FOREIGN KEY (menu_id) REFERENCES menus(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_version_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id INTEGER NOT NULL,
            name TEXT,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            category TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (version_id) REFERENCES menu_versions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_version_item_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            label TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            kind TEXT DEFAULT 'size',
            position INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (item_id) REFERENCES menu_version_items(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_id INTEGER NOT NULL,
            version_id INTEGER,
            action TEXT NOT NULL,
            detail TEXT,
            actor TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
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
    global _TEST_CONN
    _TEST_CONN = _make_test_db()
    monkeypatch.setattr("storage.menus.db_connect", _test_db_connect)
    monkeypatch.setattr("storage.drafts.db_connect", _test_db_connect)
    yield
    _TEST_CONN = None


def _insert_restaurant(name="Test Restaurant"):
    conn = _test_db_connect()
    cur = conn.execute("INSERT INTO restaurants (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def _insert_menu(rest_id, name="Menu", **kwargs):
    conn = _test_db_connect()
    cols = ["restaurant_id", "name"]
    vals = [rest_id, name]
    for k, v in kwargs.items():
        cols.append(k)
        vals.append(v)
    placeholders = ",".join(["?"] * len(cols))
    cur = conn.execute(
        f"INSERT INTO menus ({','.join(cols)}) VALUES ({placeholders})", vals
    )
    conn.commit()
    return cur.lastrowid


# ===================================================================
# Tests: _schedule_field_count
# ===================================================================

class TestScheduleFieldCount:
    def test_zero_fields(self):
        from storage.menus import _schedule_field_count
        assert _schedule_field_count({}) == 0

    def test_season_only(self):
        from storage.menus import _schedule_field_count
        assert _schedule_field_count({"season": "summer"}) == 1

    def test_all_fields(self):
        from storage.menus import _schedule_field_count
        m = {
            "season": "winter",
            "effective_from": "2026-01-01",
            "active_days": "mon,tue",
            "active_start_time": "08:00",
        }
        assert _schedule_field_count(m) == 4

    def test_partial_date_range(self):
        from storage.menus import _schedule_field_count
        assert _schedule_field_count({"effective_from": "2026-01-01"}) == 1

    def test_partial_time_range(self):
        from storage.menus import _schedule_field_count
        assert _schedule_field_count({"active_end_time": "14:00"}) == 1


# ===================================================================
# Tests: score_menu_specificity
# ===================================================================

class TestScoreMenuSpecificity:
    def test_unscheduled_scores_zero(self):
        from storage.menus import score_menu_specificity
        assert score_menu_specificity({}) == 0

    def test_season_only(self):
        from storage.menus import score_menu_specificity
        assert score_menu_specificity({"season": "summer"}) == 15

    def test_full_date_range(self):
        from storage.menus import score_menu_specificity
        m = {"effective_from": "2026-06-01", "effective_to": "2026-08-31"}
        assert score_menu_specificity(m) == 25

    def test_partial_date_range(self):
        from storage.menus import score_menu_specificity
        assert score_menu_specificity({"effective_from": "2026-06-01"}) == 10

    def test_active_days(self):
        from storage.menus import score_menu_specificity
        assert score_menu_specificity({"active_days": "mon,tue"}) == 20

    def test_full_time_range(self):
        from storage.menus import score_menu_specificity
        m = {"active_start_time": "11:00", "active_end_time": "14:00"}
        assert score_menu_specificity(m) == 30

    def test_partial_time_range(self):
        from storage.menus import score_menu_specificity
        assert score_menu_specificity({"active_start_time": "11:00"}) == 10

    def test_menu_type_adds_10(self):
        from storage.menus import score_menu_specificity
        assert score_menu_specificity({"menu_type": "lunch"}) == 10

    def test_all_fields_combined(self):
        from storage.menus import score_menu_specificity
        m = {
            "season": "summer",
            "effective_from": "2026-06-01",
            "effective_to": "2026-08-31",
            "active_days": "mon,tue,wed,thu,fri",
            "active_start_time": "11:00",
            "active_end_time": "14:00",
            "menu_type": "lunch",
        }
        assert score_menu_specificity(m) == 100

    def test_partial_combination(self):
        from storage.menus import score_menu_specificity
        m = {"season": "fall", "active_days": "sat,sun"}
        assert score_menu_specificity(m) == 35  # 15 + 20


# ===================================================================
# Tests: get_active_menus
# ===================================================================

class TestGetActiveMenus:
    def test_returns_all_when_no_schedules(self):
        from storage.menus import get_active_menus
        rid = _insert_restaurant()
        _insert_menu(rid, "Menu A")
        _insert_menu(rid, "Menu B")
        result = get_active_menus(rid, now_date="2026-03-04", now_time="12:00", now_day="wed")
        assert len(result) == 2

    def test_includes_specificity_score(self):
        from storage.menus import get_active_menus
        rid = _insert_restaurant()
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        result = get_active_menus(rid, now_date="2026-03-04", now_time="12:00", now_day="wed")
        assert "specificity_score" in result[0]
        assert result[0]["specificity_score"] == 30

    def test_includes_is_scheduled_field(self):
        from storage.menus import get_active_menus
        rid = _insert_restaurant()
        _insert_menu(rid, "Always On")
        result = get_active_menus(rid, now_date="2026-03-04", now_time="12:00", now_day="wed")
        assert result[0]["is_scheduled"] is False

    def test_sorted_by_specificity_desc(self):
        from storage.menus import get_active_menus
        rid = _insert_restaurant()
        _insert_menu(rid, "Generic")  # score 0
        _insert_menu(rid, "Lunch Special",
                     active_start_time="11:00", active_end_time="14:00",
                     active_days="mon,tue,wed,thu,fri")  # score 50
        result = get_active_menus(rid, now_date="2026-03-04", now_time="12:00", now_day="wed")
        assert result[0]["name"] == "Lunch Special"
        assert result[1]["name"] == "Generic"

    def test_scheduled_ranked_above_unscheduled(self):
        from storage.menus import get_active_menus
        rid = _insert_restaurant()
        _insert_menu(rid, "Always")
        _insert_menu(rid, "Timed", active_start_time="08:00", active_end_time="22:00")
        result = get_active_menus(rid, now_date="2026-03-04", now_time="12:00", now_day="wed")
        assert result[0]["name"] == "Timed"
        assert result[0]["is_scheduled"] is True

    def test_filters_by_date(self):
        from storage.menus import get_active_menus
        rid = _insert_restaurant()
        _insert_menu(rid, "Summer", effective_from="2026-06-01", effective_to="2026-08-31")
        result = get_active_menus(rid, now_date="2026-03-04", now_time="12:00", now_day="wed")
        assert len(result) == 0

    def test_filters_by_time(self):
        from storage.menus import get_active_menus
        rid = _insert_restaurant()
        _insert_menu(rid, "Breakfast", active_start_time="06:00", active_end_time="10:00")
        result = get_active_menus(rid, now_date="2026-03-04", now_time="12:00", now_day="wed")
        assert len(result) == 0

    def test_filters_by_day(self):
        from storage.menus import get_active_menus
        rid = _insert_restaurant()
        _insert_menu(rid, "Weekend", active_days="sat,sun")
        result = get_active_menus(rid, now_date="2026-03-04", now_time="12:00", now_day="wed")
        assert len(result) == 0

    def test_excludes_outside_time_window(self):
        from storage.menus import get_active_menus
        rid = _insert_restaurant()
        _insert_menu(rid, "Dinner", active_start_time="17:00", active_end_time="22:00")
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        result = get_active_menus(rid, now_date="2026-03-04", now_time="12:00", now_day="wed")
        assert len(result) == 1
        assert result[0]["name"] == "Lunch"

    def test_empty_restaurant(self):
        from storage.menus import get_active_menus
        rid = _insert_restaurant()
        result = get_active_menus(rid, now_date="2026-03-04", now_time="12:00", now_day="wed")
        assert result == []


# ===================================================================
# Tests: get_menu_rotation
# ===================================================================

class TestGetMenuRotation:
    def test_all_day_slot(self):
        from storage.menus import get_menu_rotation
        rid = _insert_restaurant()
        _insert_menu(rid, "Always On")
        slots = get_menu_rotation(rid, date="2026-03-04")
        assert len(slots) == 1
        assert slots[0]["label"] == "All Day"
        assert len(slots[0]["menus"]) == 1

    def test_timed_menus_grouped(self):
        from storage.menus import get_menu_rotation
        rid = _insert_restaurant()
        _insert_menu(rid, "Lunch A", active_start_time="11:00", active_end_time="14:00")
        _insert_menu(rid, "Lunch B", active_start_time="11:00", active_end_time="14:00")
        slots = get_menu_rotation(rid, date="2026-03-04")
        timed = [s for s in slots if s["start_time"] is not None]
        assert len(timed) == 1
        assert len(timed[0]["menus"]) == 2

    def test_mixed_timed_and_allday(self):
        from storage.menus import get_menu_rotation
        rid = _insert_restaurant()
        _insert_menu(rid, "Always")
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        slots = get_menu_rotation(rid, date="2026-03-04")
        assert len(slots) == 2
        labels = [s["label"] for s in slots]
        assert "All Day" in labels
        assert "11:00 - 14:00" in labels

    def test_slots_sorted_by_start(self):
        from storage.menus import get_menu_rotation
        rid = _insert_restaurant()
        _insert_menu(rid, "Dinner", active_start_time="17:00", active_end_time="22:00")
        _insert_menu(rid, "Breakfast", active_start_time="06:00", active_end_time="10:00")
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        slots = get_menu_rotation(rid, date="2026-03-04")
        times = [s["start_time"] for s in slots if s["start_time"]]
        assert times == ["06:00", "11:00", "17:00"]

    def test_no_menus_returns_empty(self):
        from storage.menus import get_menu_rotation
        rid = _insert_restaurant()
        assert get_menu_rotation(rid, date="2026-03-04") == []

    def test_date_filter_excludes(self):
        from storage.menus import get_menu_rotation
        rid = _insert_restaurant()
        _insert_menu(rid, "Summer Only",
                     effective_from="2026-06-01", effective_to="2026-08-31",
                     active_start_time="11:00", active_end_time="14:00")
        slots = get_menu_rotation(rid, date="2026-03-04")
        assert len(slots) == 0

    def test_day_filter_applies(self):
        from storage.menus import get_menu_rotation
        rid = _insert_restaurant()
        # 2026-03-04 is a Wednesday
        _insert_menu(rid, "Weekend Brunch",
                     active_days="sat,sun",
                     active_start_time="10:00", active_end_time="15:00")
        slots = get_menu_rotation(rid, date="2026-03-04")
        assert len(slots) == 0


# ===================================================================
# Tests: get_next_transition
# ===================================================================

class TestGetNextTransition:
    def test_returns_soonest_start(self):
        from storage.menus import get_next_transition
        rid = _insert_restaurant()
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        _insert_menu(rid, "Dinner", active_start_time="17:00", active_end_time="22:00")
        result = get_next_transition(rid, now_date="2026-03-04", now_time="10:00")
        assert result is not None
        assert result["time"] == "11:00"
        assert result["type"] == "starts"
        assert "Lunch" in result["label"]

    def test_returns_soonest_end(self):
        from storage.menus import get_next_transition
        rid = _insert_restaurant()
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        result = get_next_transition(rid, now_date="2026-03-04", now_time="12:00")
        assert result is not None
        assert result["time"] == "14:00"
        assert result["type"] == "ends"

    def test_returns_none_when_no_transitions(self):
        from storage.menus import get_next_transition
        rid = _insert_restaurant()
        _insert_menu(rid, "Always On")  # no time constraints
        result = get_next_transition(rid, now_date="2026-03-04", now_time="12:00")
        assert result is None

    def test_skips_past_transitions(self):
        from storage.menus import get_next_transition
        rid = _insert_restaurant()
        _insert_menu(rid, "Breakfast", active_start_time="06:00", active_end_time="10:00")
        _insert_menu(rid, "Dinner", active_start_time="17:00", active_end_time="22:00")
        result = get_next_transition(rid, now_date="2026-03-04", now_time="15:00")
        assert result is not None
        assert result["time"] == "17:00"
        assert "Dinner" in result["label"]

    def test_mixed_picks_soonest(self):
        from storage.menus import get_next_transition
        rid = _insert_restaurant()
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        _insert_menu(rid, "Dinner", active_start_time="13:00", active_end_time="22:00")
        # At 12:30: Dinner starts at 13:00, Lunch ends at 14:00 → 13:00 is soonest
        result = get_next_transition(rid, now_date="2026-03-04", now_time="12:30")
        assert result["time"] == "13:00"

    def test_filters_by_date_range(self):
        from storage.menus import get_next_transition
        rid = _insert_restaurant()
        _insert_menu(rid, "Summer Lunch",
                     effective_from="2026-06-01", effective_to="2026-08-31",
                     active_start_time="11:00", active_end_time="14:00")
        result = get_next_transition(rid, now_date="2026-03-04", now_time="10:00")
        assert result is None  # outside date range

    def test_filters_by_day(self):
        from storage.menus import get_next_transition
        rid = _insert_restaurant()
        # 2026-03-04 is Wednesday
        _insert_menu(rid, "Weekend Brunch",
                     active_days="sat,sun",
                     active_start_time="10:00", active_end_time="15:00")
        result = get_next_transition(rid, now_date="2026-03-04", now_time="08:00")
        assert result is None


# ===================================================================
# Tests: get_active_menu_summary
# ===================================================================

class TestGetActiveMenuSummary:
    def test_returns_all_fields(self):
        from storage.menus import get_active_menu_summary
        rid = _insert_restaurant()
        _insert_menu(rid, "Test")
        summary = get_active_menu_summary(
            rid, now_date="2026-03-04", now_time="12:00", now_day="wed"
        )
        assert "active_menus" in summary
        assert "active_count" in summary
        assert "primary_menu" in summary
        assert "next_transition" in summary
        assert "rotation" in summary

    def test_primary_is_highest_specificity(self):
        from storage.menus import get_active_menu_summary
        rid = _insert_restaurant()
        _insert_menu(rid, "Generic")
        _insert_menu(rid, "Specific",
                     active_start_time="11:00", active_end_time="14:00",
                     active_days="mon,tue,wed,thu,fri")
        summary = get_active_menu_summary(
            rid, now_date="2026-03-04", now_time="12:00", now_day="wed"
        )
        assert summary["primary_menu"]["name"] == "Specific"

    def test_empty_restaurant(self):
        from storage.menus import get_active_menu_summary
        rid = _insert_restaurant()
        summary = get_active_menu_summary(
            rid, now_date="2026-03-04", now_time="12:00", now_day="wed"
        )
        assert summary["primary_menu"] is None
        assert summary["active_count"] == 0

    def test_includes_rotation(self):
        from storage.menus import get_active_menu_summary
        rid = _insert_restaurant()
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        summary = get_active_menu_summary(
            rid, now_date="2026-03-04", now_time="12:00", now_day="wed"
        )
        assert len(summary["rotation"]) > 0

    def test_includes_next_transition(self):
        from storage.menus import get_active_menu_summary
        rid = _insert_restaurant()
        _insert_menu(rid, "Dinner", active_start_time="17:00", active_end_time="22:00")
        summary = get_active_menu_summary(
            rid, now_date="2026-03-04", now_time="12:00", now_day="wed"
        )
        assert summary["next_transition"] is not None
        assert summary["next_transition"]["time"] == "17:00"


# ===================================================================
# Tests: Flask active menus page
# ===================================================================

class TestFlaskActiveMenusPage:
    @pytest.fixture
    def client(self, monkeypatch):
        import importlib
        import portal.app as app_mod
        monkeypatch.setattr(app_mod, "db_connect", _test_db_connect)
        import storage.menus as menus_mod
        monkeypatch.setattr(app_mod, "menus_store", menus_mod)
        app_mod.app.config["TESTING"] = True
        app_mod.app.config["SECRET_KEY"] = "test"
        with app_mod.app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = {"username": "admin", "role": "admin"}
            yield c

    def test_page_loads_200(self, client):
        rid = _insert_restaurant("Ristorante")
        _insert_menu(rid, "Lunch")
        resp = client.get(f"/restaurants/{rid}/active_menus")
        assert resp.status_code == 200

    def test_page_shows_active_count(self, client):
        rid = _insert_restaurant("Ristorante")
        _insert_menu(rid, "Menu A")
        _insert_menu(rid, "Menu B")
        resp = client.get(f"/restaurants/{rid}/active_menus")
        html = resp.data.decode()
        assert "active-count-badge" in html
        assert ">2<" in html

    def test_page_shows_primary_name(self, client):
        rid = _insert_restaurant("Ristorante")
        _insert_menu(rid, "Prime Lunch",
                     active_start_time="11:00", active_end_time="14:00")
        resp = client.get(f"/restaurants/{rid}/active_menus?time=12:00")
        html = resp.data.decode()
        assert "Prime Lunch" in html
        assert "primary-label" in html

    def test_page_shows_rotation(self, client):
        rid = _insert_restaurant("Ristorante")
        _insert_menu(rid, "Breakfast", active_start_time="06:00", active_end_time="10:00")
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        resp = client.get(f"/restaurants/{rid}/active_menus")
        html = resp.data.decode()
        assert "rotation-timeline" in html
        assert "06:00" in html

    def test_page_shows_next_transition(self, client):
        rid = _insert_restaurant("Ristorante")
        _insert_menu(rid, "Dinner", active_start_time="17:00", active_end_time="22:00")
        resp = client.get(f"/restaurants/{rid}/active_menus?time=12:00")
        html = resp.data.decode()
        assert "Next Transition" in html
        assert "17:00" in html

    def test_page_with_query_params(self, client):
        rid = _insert_restaurant("Ristorante")
        _insert_menu(rid, "Summer",
                     effective_from="2026-06-01", effective_to="2026-08-31")
        # Query for a summer date
        resp = client.get(f"/restaurants/{rid}/active_menus?date=2026-07-15&time=12:00")
        html = resp.data.decode()
        assert "Summer" in html

    def test_404_missing_restaurant(self, client):
        resp = client.get("/restaurants/9999/active_menus")
        assert resp.status_code == 404


# ===================================================================
# Tests: API active menus endpoint
# ===================================================================

class TestAPIActiveMenus:
    @pytest.fixture
    def client(self, monkeypatch):
        import portal.app as app_mod
        monkeypatch.setattr(app_mod, "db_connect", _test_db_connect)
        import storage.menus as menus_mod
        monkeypatch.setattr(app_mod, "menus_store", menus_mod)
        app_mod.app.config["TESTING"] = True
        app_mod.app.config["SECRET_KEY"] = "test"
        with app_mod.app.test_client() as c:
            yield c

    def test_returns_json(self, client):
        rid = _insert_restaurant("Pizzeria")
        _insert_menu(rid, "Main")
        resp = client.get(f"/api/restaurants/{rid}/active_menus")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["active_count"] == 1
        assert len(data["active_menus"]) == 1

    def test_returns_primary_menu(self, client):
        rid = _insert_restaurant("Pizzeria")
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        resp = client.get(f"/api/restaurants/{rid}/active_menus?time=12:00")
        data = resp.get_json()
        assert data["primary_menu"] is not None
        assert data["primary_menu"]["name"] == "Lunch"

    def test_returns_next_transition(self, client):
        rid = _insert_restaurant("Pizzeria")
        _insert_menu(rid, "Dinner", active_start_time="17:00", active_end_time="22:00")
        resp = client.get(f"/api/restaurants/{rid}/active_menus?time=12:00&date=2026-03-04")
        data = resp.get_json()
        assert data["next_transition"] is not None
        assert data["next_transition"]["time"] == "17:00"

    def test_accepts_params(self, client):
        rid = _insert_restaurant("Pizzeria")
        _insert_menu(rid, "Weekend",
                     active_days="sat,sun",
                     active_start_time="10:00", active_end_time="15:00")
        resp = client.get(f"/api/restaurants/{rid}/active_menus?day=sat&time=12:00&date=2026-03-07")
        data = resp.get_json()
        assert data["active_count"] == 1

    def test_empty_restaurant(self, client):
        rid = _insert_restaurant("Empty")
        resp = client.get(f"/api/restaurants/{rid}/active_menus")
        data = resp.get_json()
        assert data["active_count"] == 0
        assert data["primary_menu"] is None


# ===================================================================
# Tests: Template content
# ===================================================================

class TestTemplateContent:
    @pytest.fixture
    def client(self, monkeypatch):
        import portal.app as app_mod
        monkeypatch.setattr(app_mod, "db_connect", _test_db_connect)
        import storage.menus as menus_mod
        monkeypatch.setattr(app_mod, "menus_store", menus_mod)
        app_mod.app.config["TESTING"] = True
        app_mod.app.config["SECRET_KEY"] = "test"
        with app_mod.app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user"] = {"username": "admin", "role": "admin"}
            yield c

    def test_active_menus_has_query_form(self, client):
        rid = _insert_restaurant("R")
        resp = client.get(f"/restaurants/{rid}/active_menus")
        html = resp.data.decode()
        assert 'name="date"' in html
        assert 'name="time"' in html

    def test_active_menus_has_rotation_section(self, client):
        rid = _insert_restaurant("R")
        _insert_menu(rid, "Test", active_start_time="11:00", active_end_time="14:00")
        resp = client.get(f"/restaurants/{rid}/active_menus")
        html = resp.data.decode()
        assert "Day Rotation" in html

    def test_menus_list_has_active_now_link(self, client):
        rid = _insert_restaurant("R")
        _insert_menu(rid, "Test")
        resp = client.get(f"/restaurants/{rid}/menus")
        html = resp.data.decode()
        assert "Active Now" in html
        assert f"/restaurants/{rid}/active_menus" in html


# ===================================================================
# Tests: Schema continuity
# ===================================================================

class TestSchemaContinuity:
    def test_menus_has_all_schedule_columns(self):
        conn = _test_db_connect()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(menus)").fetchall()}
        expected = {
            "id", "restaurant_id", "name", "menu_type", "description",
            "season", "effective_from", "effective_to",
            "active_days", "active_start_time", "active_end_time",
            "active", "created_at", "updated_at",
        }
        assert expected.issubset(cols)
