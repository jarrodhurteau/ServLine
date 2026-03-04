"""
Day 95 -- Menu Health Dashboard & Phase 10 Capstone (Sprint 10.3 finale).

Builds on Days 93-94 scheduling & active switching to add conflict
detection, coverage analysis, and per-menu health scoring.

Covers:
  _time_overlaps():
  - full overlap
  - no overlap
  - adjacent (not overlapping)
  - one unbounded
  - both unbounded

  _days_overlap():
  - shared day
  - no shared day
  - one None (all days)
  - both None

  _date_ranges_overlap():
  - overlapping ranges
  - non-overlapping ranges
  - one unbounded
  - both unbounded
  - adjacent dates

  detect_schedule_conflicts():
  - no conflicts when no menus
  - no conflicts between two unscheduled menus
  - conflict between scheduled and unscheduled
  - conflict between two overlapping scheduled menus
  - no conflict when time ranges don't overlap
  - no conflict when day ranges don't overlap
  - no conflict when date ranges don't overlap
  - multiple conflicts detected
  - overlap_type is time for both timed
  - overlap_type is partial for sched vs unsched
  - overlap_type is full for both scheduled no time
  - deduplicates pairs

  analyze_schedule_coverage():
  - empty restaurant
  - all unscheduled gives 100 coverage
  - day coverage tracks all days for unscheduled
  - scheduled menu only covers its days
  - gaps detected for uncovered days
  - hour coverage counts menus per slot
  - scheduled_count and unscheduled_count correct
  - coverage_score 100 when unscheduled exists
  - coverage_score partial when only scheduled

  get_menu_health():
  - empty restaurant returns empty list
  - menu with no versions scores low
  - menu with version + items scores higher
  - menu with schedule scores higher
  - menu with type and description scores highest
  - issues list populated correctly
  - sorted by health_score desc
  - multiple versions give bonus

  get_phase10_summary():
  - returns all expected keys
  - total_menus correct
  - total_versions aggregated
  - grade A for healthy restaurant
  - grade D for empty restaurant
  - conflicts included
  - coverage included
  - menu_health included

  Flask menu health page:
  - page loads 200
  - page shows grade
  - page shows conflict count
  - page shows coverage score
  - page shows menu health table
  - 404 for missing restaurant

  API menu health endpoint:
  - returns JSON with ok=true
  - returns grade
  - returns conflict_count
  - returns coverage_score
  - returns menu_health list
  - empty restaurant returns zeros

  Schema continuity:
  - menus table has all expected columns
"""

from __future__ import annotations

import os
import sys
import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers
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
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            restaurant_id INTEGER,
            status TEXT NOT NULL DEFAULT 'editing',
            source TEXT,
            menu_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            name TEXT,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            category TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_item_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            label TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            kind TEXT DEFAULT 'size',
            position INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (item_id) REFERENCES draft_items(id) ON DELETE CASCADE
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
            source_draft_id INTEGER,
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


def _insert_version(menu_id, version_number=1, item_count=0, **kwargs):
    conn = _test_db_connect()
    cols = ["menu_id", "version_number", "item_count", "created_at"]
    vals = [menu_id, version_number, item_count, "2026-03-04T12:00:00"]
    for k, v in kwargs.items():
        cols.append(k)
        vals.append(v)
    placeholders = ",".join(["?"] * len(cols))
    cur = conn.execute(
        f"INSERT INTO menu_versions ({','.join(cols)}) VALUES ({placeholders})", vals
    )
    conn.commit()
    return cur.lastrowid


def _insert_version_item(version_id, name="Item", price_cents=500):
    conn = _test_db_connect()
    cur = conn.execute(
        "INSERT INTO menu_version_items (version_id, name, price_cents, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (version_id, name, price_cents),
    )
    conn.commit()
    return cur.lastrowid


# ===================================================================
# Tests: _time_overlaps
# ===================================================================

class TestTimeOverlaps:
    def test_full_overlap(self):
        from storage.menus import _time_overlaps
        assert _time_overlaps("11:00", "14:00", "12:00", "15:00") is True

    def test_no_overlap(self):
        from storage.menus import _time_overlaps
        assert _time_overlaps("06:00", "10:00", "14:00", "18:00") is False

    def test_adjacent_not_overlapping(self):
        from storage.menus import _time_overlaps
        # 10:00 is NOT < 10:00, so no overlap
        assert _time_overlaps("06:00", "10:00", "10:00", "14:00") is False

    def test_one_unbounded(self):
        from storage.menus import _time_overlaps
        assert _time_overlaps(None, None, "12:00", "14:00") is True

    def test_both_unbounded(self):
        from storage.menus import _time_overlaps
        assert _time_overlaps(None, None, None, None) is True


# ===================================================================
# Tests: _days_overlap
# ===================================================================

class TestDaysOverlap:
    def test_shared_day(self):
        from storage.menus import _days_overlap
        assert _days_overlap("mon,tue,wed", "wed,thu,fri") is True

    def test_no_shared_day(self):
        from storage.menus import _days_overlap
        assert _days_overlap("mon,tue", "sat,sun") is False

    def test_one_none(self):
        from storage.menus import _days_overlap
        assert _days_overlap(None, "mon,tue") is True

    def test_both_none(self):
        from storage.menus import _days_overlap
        assert _days_overlap(None, None) is True


# ===================================================================
# Tests: _date_ranges_overlap
# ===================================================================

class TestDateRangesOverlap:
    def test_overlapping(self):
        from storage.menus import _date_ranges_overlap
        assert _date_ranges_overlap("2026-01-01", "2026-06-30", "2026-03-01", "2026-09-30") is True

    def test_not_overlapping(self):
        from storage.menus import _date_ranges_overlap
        assert _date_ranges_overlap("2026-01-01", "2026-03-31", "2026-06-01", "2026-09-30") is False

    def test_one_unbounded(self):
        from storage.menus import _date_ranges_overlap
        assert _date_ranges_overlap(None, None, "2026-06-01", "2026-09-30") is True

    def test_both_unbounded(self):
        from storage.menus import _date_ranges_overlap
        assert _date_ranges_overlap(None, None, None, None) is True

    def test_adjacent_dates(self):
        from storage.menus import _date_ranges_overlap
        # Adjacent: to_a == from_b, should overlap (<=)
        assert _date_ranges_overlap("2026-01-01", "2026-03-31", "2026-03-31", "2026-06-30") is True


# ===================================================================
# Tests: detect_schedule_conflicts
# ===================================================================

class TestDetectScheduleConflicts:
    def test_no_conflicts_empty(self):
        from storage.menus import detect_schedule_conflicts
        rid = _insert_restaurant()
        assert detect_schedule_conflicts(rid) == []

    def test_no_conflict_two_unscheduled(self):
        from storage.menus import detect_schedule_conflicts
        rid = _insert_restaurant()
        _insert_menu(rid, "Menu A")
        _insert_menu(rid, "Menu B")
        assert detect_schedule_conflicts(rid) == []

    def test_conflict_scheduled_vs_unscheduled(self):
        from storage.menus import detect_schedule_conflicts
        rid = _insert_restaurant()
        _insert_menu(rid, "Always On")
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        conflicts = detect_schedule_conflicts(rid)
        assert len(conflicts) == 1
        assert conflicts[0]["overlap_type"] == "partial"

    def test_conflict_two_overlapping_timed(self):
        from storage.menus import detect_schedule_conflicts
        rid = _insert_restaurant()
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        _insert_menu(rid, "Brunch", active_start_time="10:00", active_end_time="13:00")
        conflicts = detect_schedule_conflicts(rid)
        assert len(conflicts) == 1
        assert conflicts[0]["overlap_type"] == "time"

    def test_no_conflict_non_overlapping_times(self):
        from storage.menus import detect_schedule_conflicts
        rid = _insert_restaurant()
        _insert_menu(rid, "Breakfast", active_start_time="06:00", active_end_time="10:00")
        _insert_menu(rid, "Dinner", active_start_time="17:00", active_end_time="22:00")
        assert detect_schedule_conflicts(rid) == []

    def test_no_conflict_different_days(self):
        from storage.menus import detect_schedule_conflicts
        rid = _insert_restaurant()
        _insert_menu(rid, "Weekday", active_days="mon,tue,wed,thu,fri",
                     active_start_time="11:00", active_end_time="14:00")
        _insert_menu(rid, "Weekend", active_days="sat,sun",
                     active_start_time="11:00", active_end_time="14:00")
        assert detect_schedule_conflicts(rid) == []

    def test_no_conflict_different_dates(self):
        from storage.menus import detect_schedule_conflicts
        rid = _insert_restaurant()
        _insert_menu(rid, "Spring", effective_from="2026-03-01", effective_to="2026-05-31")
        _insert_menu(rid, "Fall", effective_from="2026-09-01", effective_to="2026-11-30")
        assert detect_schedule_conflicts(rid) == []

    def test_multiple_conflicts(self):
        from storage.menus import detect_schedule_conflicts
        rid = _insert_restaurant()
        _insert_menu(rid, "A", active_start_time="11:00", active_end_time="14:00")
        _insert_menu(rid, "B", active_start_time="12:00", active_end_time="15:00")
        _insert_menu(rid, "C", active_start_time="13:00", active_end_time="16:00")
        conflicts = detect_schedule_conflicts(rid)
        # A-B overlap, A-C overlap, B-C overlap = 3 conflicts
        assert len(conflicts) == 3

    def test_overlap_type_time(self):
        from storage.menus import detect_schedule_conflicts
        rid = _insert_restaurant()
        _insert_menu(rid, "A", active_start_time="11:00", active_end_time="14:00")
        _insert_menu(rid, "B", active_start_time="13:00", active_end_time="16:00")
        conflicts = detect_schedule_conflicts(rid)
        assert conflicts[0]["overlap_type"] == "time"

    def test_overlap_type_partial(self):
        from storage.menus import detect_schedule_conflicts
        rid = _insert_restaurant()
        _insert_menu(rid, "Unscheduled")
        _insert_menu(rid, "Scheduled", season="summer")
        conflicts = detect_schedule_conflicts(rid)
        assert len(conflicts) == 1
        assert conflicts[0]["overlap_type"] == "partial"

    def test_overlap_type_full(self):
        from storage.menus import detect_schedule_conflicts
        rid = _insert_restaurant()
        _insert_menu(rid, "Summer A", season="summer")
        _insert_menu(rid, "Summer B", season="summer")
        conflicts = detect_schedule_conflicts(rid)
        assert len(conflicts) == 1
        assert conflicts[0]["overlap_type"] == "full"

    def test_deduplicates_pairs(self):
        from storage.menus import detect_schedule_conflicts
        rid = _insert_restaurant()
        _insert_menu(rid, "A", active_start_time="11:00", active_end_time="14:00")
        _insert_menu(rid, "B", active_start_time="12:00", active_end_time="15:00")
        conflicts = detect_schedule_conflicts(rid)
        # Should only have 1, not 2
        assert len(conflicts) == 1
        ids = {(c["menu_a"]["id"], c["menu_b"]["id"]) for c in conflicts}
        assert len(ids) == 1


# ===================================================================
# Tests: analyze_schedule_coverage
# ===================================================================

class TestAnalyzeScheduleCoverage:
    def test_empty_restaurant(self):
        from storage.menus import analyze_schedule_coverage
        rid = _insert_restaurant()
        result = analyze_schedule_coverage(rid)
        assert result["total_menus"] == 0
        assert result["coverage_score"] == 0

    def test_all_unscheduled_gives_100(self):
        from storage.menus import analyze_schedule_coverage
        rid = _insert_restaurant()
        _insert_menu(rid, "Menu A")
        _insert_menu(rid, "Menu B")
        result = analyze_schedule_coverage(rid)
        assert result["coverage_score"] == 100

    def test_day_coverage_all_days_for_unscheduled(self):
        from storage.menus import analyze_schedule_coverage
        rid = _insert_restaurant()
        _insert_menu(rid, "Always On")
        result = analyze_schedule_coverage(rid)
        for day, menus in result["day_coverage"].items():
            assert len(menus) >= 1, f"Day {day} should be covered"

    def test_scheduled_menu_covers_its_days(self):
        from storage.menus import analyze_schedule_coverage
        rid = _insert_restaurant()
        _insert_menu(rid, "Weekend Brunch", active_days="sat,sun")
        result = analyze_schedule_coverage(rid)
        assert "Weekend Brunch" in result["day_coverage"]["sat"]
        assert "Weekend Brunch" in result["day_coverage"]["sun"]
        assert "Weekend Brunch" not in result["day_coverage"]["mon"]

    def test_gaps_for_uncovered_days(self):
        from storage.menus import analyze_schedule_coverage
        rid = _insert_restaurant()
        _insert_menu(rid, "Weekend Only", active_days="sat,sun")
        result = analyze_schedule_coverage(rid)
        # mon-fri should be gaps
        gap_text = " ".join(result["gaps"])
        assert "Monday" in gap_text
        assert "Friday" in gap_text

    def test_hour_coverage_counts(self):
        from storage.menus import analyze_schedule_coverage
        rid = _insert_restaurant()
        _insert_menu(rid, "All Day")
        result = analyze_schedule_coverage(rid)
        # Unscheduled covers all hours
        for h, cnt in result["hour_coverage"].items():
            assert cnt >= 1

    def test_scheduled_and_unscheduled_counts(self):
        from storage.menus import analyze_schedule_coverage
        rid = _insert_restaurant()
        _insert_menu(rid, "Always")
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        result = analyze_schedule_coverage(rid)
        assert result["scheduled_count"] == 1
        assert result["unscheduled_count"] == 1

    def test_coverage_score_100_with_unscheduled(self):
        from storage.menus import analyze_schedule_coverage
        rid = _insert_restaurant()
        _insert_menu(rid, "Always")
        _insert_menu(rid, "Lunch", active_start_time="11:00", active_end_time="14:00")
        result = analyze_schedule_coverage(rid)
        assert result["coverage_score"] == 100

    def test_coverage_score_partial_scheduled_only(self):
        from storage.menus import analyze_schedule_coverage
        rid = _insert_restaurant()
        _insert_menu(rid, "Mon-Fri Lunch", active_days="mon,tue,wed,thu,fri",
                     active_start_time="11:00", active_end_time="14:00")
        result = analyze_schedule_coverage(rid)
        assert 0 < result["coverage_score"] < 100


# ===================================================================
# Tests: get_menu_health
# ===================================================================

class TestGetMenuHealth:
    def test_empty_restaurant(self):
        from storage.menus import get_menu_health
        rid = _insert_restaurant()
        assert get_menu_health(rid) == []

    def test_menu_no_versions_scores_low(self):
        from storage.menus import get_menu_health
        rid = _insert_restaurant()
        _insert_menu(rid, "Empty Menu")
        result = get_menu_health(rid)
        assert len(result) == 1
        assert result[0]["health_score"] < 50
        assert "No published versions" in result[0]["issues"]

    def test_menu_with_version_and_items(self):
        from storage.menus import get_menu_health
        rid = _insert_restaurant()
        mid = _insert_menu(rid, "Good Menu", menu_type="lunch")
        vid = _insert_version(mid, 1, item_count=10)
        _insert_version_item(vid, "Burger", 999)
        result = get_menu_health(rid)
        assert result[0]["has_versions"] is True
        assert result[0]["health_score"] >= 50

    def test_menu_with_schedule_scores_higher(self):
        from storage.menus import get_menu_health
        rid = _insert_restaurant()
        mid = _insert_menu(rid, "Scheduled", active_start_time="11:00", active_end_time="14:00")
        vid = _insert_version(mid, 1, item_count=5)
        _insert_version_item(vid, "Salad", 799)
        result = get_menu_health(rid)
        assert result[0]["has_schedule"] is True
        assert result[0]["health_score"] >= 75

    def test_full_health_menu(self):
        from storage.menus import get_menu_health
        rid = _insert_restaurant()
        mid = _insert_menu(rid, "Perfect", menu_type="dinner",
                           description="Our finest selections",
                           active_start_time="17:00", active_end_time="22:00")
        vid1 = _insert_version(mid, 1, item_count=10)
        _insert_version_item(vid1, "Steak", 2999)
        vid2 = _insert_version(mid, 2, item_count=12)
        _insert_version_item(vid2, "Steak", 3199)
        result = get_menu_health(rid)
        assert result[0]["health_score"] == 100

    def test_issues_populated(self):
        from storage.menus import get_menu_health
        rid = _insert_restaurant()
        _insert_menu(rid, "Bare Menu")
        result = get_menu_health(rid)
        issues = result[0]["issues"]
        assert "No published versions" in issues
        assert "No schedule set" in issues
        assert "No menu type set" in issues

    def test_sorted_by_health_desc(self):
        from storage.menus import get_menu_health
        rid = _insert_restaurant()
        mid_good = _insert_menu(rid, "Good", menu_type="lunch",
                                active_start_time="11:00", active_end_time="14:00")
        vid = _insert_version(mid_good, 1, item_count=5)
        _insert_version_item(vid, "Pasta", 1299)
        _insert_menu(rid, "Bad")
        result = get_menu_health(rid)
        assert result[0]["name"] == "Good"
        assert result[1]["name"] == "Bad"
        assert result[0]["health_score"] >= result[1]["health_score"]

    def test_multiple_versions_bonus(self):
        from storage.menus import get_menu_health
        rid = _insert_restaurant()
        mid = _insert_menu(rid, "Iterated", menu_type="lunch")
        vid1 = _insert_version(mid, 1, item_count=5)
        _insert_version_item(vid1, "Item A", 500)
        vid2 = _insert_version(mid, 2, item_count=6)
        _insert_version_item(vid2, "Item B", 600)
        result = get_menu_health(rid)
        # Should have version bonus (+10)
        assert result[0]["version_count"] == 2
        assert result[0]["health_score"] >= 60


# ===================================================================
# Tests: get_phase10_summary
# ===================================================================

class TestGetPhase10Summary:
    def test_returns_all_keys(self):
        from storage.menus import get_phase10_summary
        rid = _insert_restaurant()
        result = get_phase10_summary(rid)
        expected_keys = {
            "restaurant_id", "total_menus", "total_versions", "total_items",
            "total_pinned", "conflict_count", "conflicts", "coverage",
            "menu_health", "avg_health_score", "grade",
        }
        assert expected_keys <= set(result.keys())

    def test_total_menus_correct(self):
        from storage.menus import get_phase10_summary
        rid = _insert_restaurant()
        _insert_menu(rid, "A")
        _insert_menu(rid, "B")
        _insert_menu(rid, "C")
        result = get_phase10_summary(rid)
        assert result["total_menus"] == 3

    def test_total_versions_aggregated(self):
        from storage.menus import get_phase10_summary
        rid = _insert_restaurant()
        m1 = _insert_menu(rid, "M1")
        m2 = _insert_menu(rid, "M2")
        _insert_version(m1, 1, item_count=3)
        _insert_version(m1, 2, item_count=4)
        _insert_version(m2, 1, item_count=5)
        result = get_phase10_summary(rid)
        assert result["total_versions"] == 3

    def test_grade_a_for_healthy(self):
        from storage.menus import get_phase10_summary
        rid = _insert_restaurant()
        mid = _insert_menu(rid, "Perfect", menu_type="dinner",
                           description="Great",
                           active_start_time="17:00", active_end_time="22:00")
        vid1 = _insert_version(mid, 1, item_count=10)
        _insert_version_item(vid1, "Dish", 1500)
        vid2 = _insert_version(mid, 2, item_count=11)
        _insert_version_item(vid2, "Dish2", 1600)
        result = get_phase10_summary(rid)
        assert result["grade"] == "A"

    def test_grade_d_for_empty(self):
        from storage.menus import get_phase10_summary
        rid = _insert_restaurant()
        result = get_phase10_summary(rid)
        assert result["grade"] == "D"

    def test_conflicts_included(self):
        from storage.menus import get_phase10_summary
        rid = _insert_restaurant()
        _insert_menu(rid, "A", active_start_time="11:00", active_end_time="14:00")
        _insert_menu(rid, "B", active_start_time="12:00", active_end_time="15:00")
        result = get_phase10_summary(rid)
        assert result["conflict_count"] == 1
        assert len(result["conflicts"]) == 1

    def test_coverage_included(self):
        from storage.menus import get_phase10_summary
        rid = _insert_restaurant()
        _insert_menu(rid, "Always On")
        result = get_phase10_summary(rid)
        assert "coverage_score" in result["coverage"]
        assert result["coverage"]["coverage_score"] == 100

    def test_menu_health_included(self):
        from storage.menus import get_phase10_summary
        rid = _insert_restaurant()
        _insert_menu(rid, "Menu A")
        result = get_phase10_summary(rid)
        assert len(result["menu_health"]) == 1
        assert result["menu_health"][0]["name"] == "Menu A"


# ===================================================================
# Tests: Flask routes (menu health page + API)
# ===================================================================

class TestFlaskMenuHealthPage:
    @pytest.fixture
    def client(self, monkeypatch):
        """Create Flask test client with patched DB."""
        portal_dir = os.path.join(os.path.dirname(__file__), "..", "portal")
        if portal_dir not in sys.path:
            sys.path.insert(0, portal_dir)

        import importlib
        app_mod = importlib.import_module("app")

        monkeypatch.setattr(app_mod, "db_connect", _test_db_connect)
        if hasattr(app_mod, "menus_store"):
            monkeypatch.setattr("storage.menus.db_connect", _test_db_connect)
            monkeypatch.setattr("storage.drafts.db_connect", _test_db_connect)

        app_mod.app.config["TESTING"] = True
        app_mod.app.config["SECRET_KEY"] = "test-secret"
        client = app_mod.app.test_client()

        with client.session_transaction() as sess:
            sess["user"] = {"username": "admin", "role": "admin"}

        return client

    def test_page_loads_200(self, client):
        rid = _insert_restaurant("Test Bistro")
        resp = client.get(f"/restaurants/{rid}/menu_health")
        assert resp.status_code == 200

    def test_page_shows_grade(self, client):
        rid = _insert_restaurant("Test Bistro")
        resp = client.get(f"/restaurants/{rid}/menu_health")
        html = resp.data.decode()
        # Should show a grade (A, B, C, or D)
        assert "Grade" in html or "grade" in html.lower()

    def test_page_shows_conflict_count(self, client):
        rid = _insert_restaurant("Test Bistro")
        _insert_menu(rid, "A", active_start_time="11:00", active_end_time="14:00")
        _insert_menu(rid, "B", active_start_time="12:00", active_end_time="15:00")
        resp = client.get(f"/restaurants/{rid}/menu_health")
        html = resp.data.decode()
        assert "Conflicts" in html or "conflict" in html.lower()

    def test_page_shows_coverage(self, client):
        rid = _insert_restaurant("Test Bistro")
        _insert_menu(rid, "Always On")
        resp = client.get(f"/restaurants/{rid}/menu_health")
        html = resp.data.decode()
        assert "Coverage" in html or "coverage" in html.lower()

    def test_page_shows_health_table(self, client):
        rid = _insert_restaurant("Test Bistro")
        _insert_menu(rid, "Test Menu")
        resp = client.get(f"/restaurants/{rid}/menu_health")
        html = resp.data.decode()
        assert "Test Menu" in html
        assert "Health" in html or "health" in html.lower()

    def test_404_missing_restaurant(self, client):
        resp = client.get("/restaurants/9999/menu_health")
        assert resp.status_code == 404


class TestAPIMenuHealth:
    @pytest.fixture
    def client(self, monkeypatch):
        portal_dir = os.path.join(os.path.dirname(__file__), "..", "portal")
        if portal_dir not in sys.path:
            sys.path.insert(0, portal_dir)

        import importlib
        app_mod = importlib.import_module("app")

        monkeypatch.setattr(app_mod, "db_connect", _test_db_connect)
        if hasattr(app_mod, "menus_store"):
            monkeypatch.setattr("storage.menus.db_connect", _test_db_connect)
            monkeypatch.setattr("storage.drafts.db_connect", _test_db_connect)

        app_mod.app.config["TESTING"] = True
        app_mod.app.config["SECRET_KEY"] = "test-secret"
        client = app_mod.app.test_client()
        return client

    def test_returns_json_ok(self, client):
        rid = _insert_restaurant()
        resp = client.get(f"/api/restaurants/{rid}/menu_health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_returns_grade(self, client):
        rid = _insert_restaurant()
        resp = client.get(f"/api/restaurants/{rid}/menu_health")
        data = resp.get_json()
        assert "grade" in data

    def test_returns_conflict_count(self, client):
        rid = _insert_restaurant()
        _insert_menu(rid, "A", active_start_time="11:00", active_end_time="14:00")
        _insert_menu(rid, "B", active_start_time="12:00", active_end_time="15:00")
        resp = client.get(f"/api/restaurants/{rid}/menu_health")
        data = resp.get_json()
        assert data["conflict_count"] == 1

    def test_returns_coverage_score(self, client):
        rid = _insert_restaurant()
        _insert_menu(rid, "Always")
        resp = client.get(f"/api/restaurants/{rid}/menu_health")
        data = resp.get_json()
        assert data["coverage_score"] == 100

    def test_returns_menu_health_list(self, client):
        rid = _insert_restaurant()
        _insert_menu(rid, "Menu X")
        resp = client.get(f"/api/restaurants/{rid}/menu_health")
        data = resp.get_json()
        assert len(data["menu_health"]) == 1
        assert data["menu_health"][0]["name"] == "Menu X"

    def test_empty_restaurant_zeros(self, client):
        rid = _insert_restaurant()
        resp = client.get(f"/api/restaurants/{rid}/menu_health")
        data = resp.get_json()
        assert data["total_menus"] == 0
        assert data["avg_health_score"] == 0
        assert data["grade"] == "D"


# ===================================================================
# Tests: Schema continuity
# ===================================================================

class TestSchemaContinuity:
    def test_menus_has_all_columns(self):
        conn = _test_db_connect()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(menus)").fetchall()}
        expected = {
            "id", "restaurant_id", "name", "menu_type", "description",
            "season", "effective_from", "effective_to",
            "active_days", "active_start_time", "active_end_time",
            "active", "created_at", "updated_at",
        }
        assert expected <= cols

    def test_menu_versions_has_all_columns(self):
        conn = _test_db_connect()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(menu_versions)").fetchall()}
        expected = {
            "id", "menu_id", "version_number", "label", "source_draft_id",
            "item_count", "variant_count", "notes", "change_summary",
            "pinned", "created_by", "created_at",
        }
        assert expected <= cols

    def test_menu_activity_table_exists(self):
        conn = _test_db_connect()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "menu_activity" in tables
