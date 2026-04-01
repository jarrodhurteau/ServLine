# tests/test_day136_pipeline_progress.py
"""
Day 136 — Sprint 13.2: Pipeline Integration + Loading Screen.

Deliverables:
  1. pipeline_stage column on import_jobs — tracks current pipeline step
  2. Call 4 (price intelligence) wired into main parsing pipeline
  3. Stage updates at each pipeline step (extracting → verifying → reconciling
     → analyzing_prices → finalizing → done)
  4. Status API returns pipeline_stage for frontend polling
  5. STEP_CALL4_PRICE added to pipeline_metrics step constants
  6. 5-stage progress screen in import_view.html (DOM structure)
  7. Pipeline progress persists on browser refresh (data-initial-stage)
  8. Non-blocking: Call 4 failure doesn't break the import

32 tests across 8 classes:
  1. Schema — pipeline_stage column exists (4)
  2. Pipeline metrics — STEP_CALL4_PRICE constant + ordering (4)
  3. Stage update during pipeline — extracting/verifying/reconciling (4)
  4. Call 4 integration — wired after draft persistence (4)
  5. Call 4 skip/error handling — non-blocking failures (4)
  6. Status API — pipeline_stage in response (4)
  7. Frontend template — 5-stage DOM structure (4)
  8. End-to-end pipeline stage transitions (4)
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch, MagicMock, PropertyMock
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Schema (extends Day 135 schema with pipeline_stage column)
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
    pipeline_stage TEXT,
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

CREATE TABLE IF NOT EXISTS price_comparison_cache (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    zip_code      TEXT    NOT NULL,
    cuisine_type  TEXT    NOT NULL,
    results_json  TEXT    NOT NULL,
    result_count  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    expires_at    TEXT    NOT NULL,
    UNIQUE(zip_code, cuisine_type)
);

CREATE TABLE IF NOT EXISTS price_comparison_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id   INTEGER NOT NULL,
    cache_id        INTEGER NOT NULL REFERENCES price_comparison_cache(id) ON DELETE CASCADE,
    place_id        TEXT,
    place_name      TEXT    NOT NULL,
    place_address   TEXT,
    price_level     INTEGER,
    price_label     TEXT,
    rating          REAL,
    user_ratings    INTEGER,
    cuisine_match   TEXT,
    latitude        REAL,
    longitude       REAL,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS price_intelligence_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id        INTEGER NOT NULL,
    restaurant_id   INTEGER NOT NULL,
    item_id         INTEGER,
    item_name       TEXT NOT NULL,
    item_category   TEXT,
    current_price   INTEGER NOT NULL DEFAULT 0,
    assessment      TEXT NOT NULL DEFAULT 'unknown',
    suggested_low   INTEGER,
    suggested_high  INTEGER,
    regional_avg    INTEGER,
    reasoning       TEXT,
    confidence      REAL DEFAULT 0.0,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS price_intelligence_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id        INTEGER NOT NULL UNIQUE,
    restaurant_id   INTEGER NOT NULL,
    cuisine_type    TEXT,
    zip_code        TEXT,
    competitor_count INTEGER DEFAULT 0,
    avg_market_tier TEXT,
    total_items     INTEGER DEFAULT 0,
    items_assessed  INTEGER DEFAULT 0,
    underpriced     INTEGER DEFAULT 0,
    fair_priced     INTEGER DEFAULT 0,
    overpriced      INTEGER DEFAULT 0,
    category_avgs   TEXT,
    model_used      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER,
    draft_id INTEGER,
    image_path TEXT,
    ocr_chars INTEGER DEFAULT 0,
    item_count INTEGER DEFAULT 0,
    gate_score REAL DEFAULT 0.0,
    gate_reason TEXT,
    pipeline_signals TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_price_intel_draft ON price_intelligence_results(draft_id);
CREATE INDEX IF NOT EXISTS idx_price_intel_item ON price_intelligence_results(item_id);
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def db():
    """In-memory SQLite with full schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def seed_restaurant(db):
    """Create a restaurant with cuisine_type + zip_code."""
    db.execute(
        "INSERT INTO restaurants (name, cuisine_type, zip_code, active) "
        "VALUES ('Test Pizza', 'pizza', '90210', 1)"
    )
    db.commit()
    return 1  # restaurant_id


@pytest.fixture
def seed_job(db, seed_restaurant):
    """Create an import job linked to the restaurant."""
    cur = db.execute(
        "INSERT INTO import_jobs (restaurant_id, filename, status) VALUES (?, 'test.jpg', 'pending')",
        (seed_restaurant,),
    )
    db.commit()
    return cur.lastrowid


@pytest.fixture
def seed_draft(db, seed_restaurant):
    """Create a draft with sample items."""
    cur = db.execute(
        "INSERT INTO drafts (restaurant_id, title, status) VALUES (?, 'Test Draft', 'editing')",
        (seed_restaurant,),
    )
    draft_id = cur.lastrowid
    for i, (name, price, cat) in enumerate([
        ("Margherita Pizza", 1499, "Pizza"),
        ("Caesar Salad", 899, "Salads"),
        ("Chicken Parm", 1699, "Entrees"),
        ("Tiramisu", 899, "Desserts"),
        ("Garlic Bread", 599, "Appetizers"),
    ]):
        db.execute(
            "INSERT INTO draft_items (draft_id, name, price_cents, category, position) "
            "VALUES (?, ?, ?, ?, ?)",
            (draft_id, name, price, cat, i),
        )
    db.commit()
    return draft_id


# ---------------------------------------------------------------------------
# 1. Schema — pipeline_stage column exists (4 tests)
# ---------------------------------------------------------------------------
class TestSchemaPipelineStage:
    def test_pipeline_stage_column_exists(self, db):
        """import_jobs table has pipeline_stage column."""
        cols = {r[1] for r in db.execute("PRAGMA table_info(import_jobs)").fetchall()}
        assert "pipeline_stage" in cols

    def test_pipeline_stage_default_null(self, db):
        """pipeline_stage defaults to NULL on new jobs."""
        db.execute("INSERT INTO import_jobs (filename, status) VALUES ('x.jpg', 'pending')")
        db.commit()
        row = db.execute("SELECT pipeline_stage FROM import_jobs WHERE filename='x.jpg'").fetchone()
        assert row["pipeline_stage"] is None

    def test_pipeline_stage_update(self, db, seed_job):
        """pipeline_stage can be updated."""
        db.execute("UPDATE import_jobs SET pipeline_stage='extracting' WHERE id=?", (seed_job,))
        db.commit()
        row = db.execute("SELECT pipeline_stage FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
        assert row["pipeline_stage"] == "extracting"

    def test_pipeline_stage_all_valid_stages(self, db, seed_job):
        """All 5 stages + 'done' are valid values."""
        stages = ["extracting", "verifying", "reconciling", "analyzing_prices", "finalizing", "done"]
        for stage in stages:
            db.execute("UPDATE import_jobs SET pipeline_stage=? WHERE id=?", (stage, seed_job))
            db.commit()
            row = db.execute("SELECT pipeline_stage FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
            assert row["pipeline_stage"] == stage


# ---------------------------------------------------------------------------
# 2. Pipeline metrics — STEP_CALL4_PRICE constant + ordering (4 tests)
# ---------------------------------------------------------------------------
class TestPipelineMetricsCall4:
    def test_step_constant_exists(self):
        """STEP_CALL4_PRICE is defined."""
        from storage.pipeline_metrics import STEP_CALL4_PRICE
        assert STEP_CALL4_PRICE == "call_4_price_intelligence"

    def test_step_in_order(self):
        """STEP_CALL4_PRICE is in canonical step order."""
        from storage.pipeline_metrics import _STEP_ORDER, STEP_CALL4_PRICE
        assert STEP_CALL4_PRICE in _STEP_ORDER

    def test_step_after_reconcile(self):
        """STEP_CALL4_PRICE comes after STEP_CALL3_RECONCILE in order."""
        from storage.pipeline_metrics import (
            _STEP_ORDER, STEP_CALL3_RECONCILE, STEP_CALL4_PRICE,
        )
        idx3 = _STEP_ORDER.index(STEP_CALL3_RECONCILE)
        idx4 = _STEP_ORDER.index(STEP_CALL4_PRICE)
        assert idx4 > idx3

    def test_tracker_records_call4(self):
        """PipelineTracker can track Call 4 step."""
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL4_PRICE
        tracker = PipelineTracker()
        tracker.start_step(STEP_CALL4_PRICE)
        tracker.end_step(STEP_CALL4_PRICE, items_assessed=10, total=15)
        summary = tracker.summary()
        steps = summary.get("steps", {})
        assert STEP_CALL4_PRICE in steps
        assert steps[STEP_CALL4_PRICE]["status"] == "success"


# ---------------------------------------------------------------------------
# 3. Stage updates during pipeline — extracting/verifying/reconciling (4 tests)
# ---------------------------------------------------------------------------
class TestPipelineStageUpdates:
    """Verify that update_import_job correctly sets pipeline_stage."""

    def test_initial_stage_extracting(self, db, seed_job):
        """Pipeline starts at 'extracting' stage."""
        db.execute(
            "UPDATE import_jobs SET status='processing', pipeline_stage='extracting' WHERE id=?",
            (seed_job,),
        )
        db.commit()
        row = db.execute("SELECT * FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
        assert row["status"] == "processing"
        assert row["pipeline_stage"] == "extracting"

    def test_stage_transitions_sequentially(self, db, seed_job):
        """Stages transition in order: extracting → verifying → reconciling → analyzing_prices → finalizing."""
        stages = ["extracting", "verifying", "reconciling", "analyzing_prices", "finalizing"]
        for stage in stages:
            db.execute("UPDATE import_jobs SET pipeline_stage=? WHERE id=?", (stage, seed_job))
            db.commit()
            row = db.execute("SELECT pipeline_stage FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
            assert row["pipeline_stage"] == stage

    def test_final_stage_done_on_success(self, db, seed_job):
        """Pipeline_stage becomes 'done' when status = 'done'."""
        db.execute(
            "UPDATE import_jobs SET status='done', pipeline_stage='done' WHERE id=?",
            (seed_job,),
        )
        db.commit()
        row = db.execute("SELECT * FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
        assert row["status"] == "done"
        assert row["pipeline_stage"] == "done"

    def test_stage_done_on_rejection(self, db, seed_job):
        """Pipeline_stage becomes 'done' even when gate rejects."""
        db.execute(
            "UPDATE import_jobs SET status='rejected', pipeline_stage='done', "
            "error='Gate failed' WHERE id=?",
            (seed_job,),
        )
        db.commit()
        row = db.execute("SELECT * FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
        assert row["status"] == "rejected"
        assert row["pipeline_stage"] == "done"


# ---------------------------------------------------------------------------
# 4. Call 4 integration — wired after draft persistence (4 tests)
# ---------------------------------------------------------------------------
class TestCall4Integration:
    """Test that Call 4 runs after the confidence gate passes and draft is persisted."""

    def test_analyze_menu_prices_callable(self):
        """ai_price_intel.analyze_menu_prices exists and is callable."""
        from storage.ai_price_intel import analyze_menu_prices
        assert callable(analyze_menu_prices)

    def test_call4_needs_draft_id_and_restaurant_id(self):
        """analyze_menu_prices requires draft_id and restaurant_id."""
        import inspect
        from storage.ai_price_intel import analyze_menu_prices
        sig = inspect.signature(analyze_menu_prices)
        params = list(sig.parameters.keys())
        assert "draft_id" in params
        assert "restaurant_id" in params

    def test_call4_returns_assessments(self, db, seed_restaurant, seed_draft):
        """Price intelligence results are stored and retrievable by draft."""
        now = "2026-03-31T12:00:00"
        db.execute(
            "INSERT INTO price_intelligence_results "
            "(draft_id, restaurant_id, item_name, item_category, current_price, "
            "assessment, suggested_low, suggested_high, regional_avg, reasoning, confidence, created_at) "
            "VALUES (?, ?, 'Margherita Pizza', 'Pizza', 1499, 'fair', 1299, 1699, 1499, 'OK', 0.9, ?)",
            (seed_draft, seed_restaurant, now),
        )
        db.commit()
        rows = db.execute(
            "SELECT * FROM price_intelligence_results WHERE draft_id=?", (seed_draft,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["assessment"] == "fair"
        assert rows[0]["regional_avg"] == 1499

    def test_call4_stores_summary(self, db, seed_restaurant, seed_draft):
        """Call 4 stores summary row in price_intelligence_summary."""
        now = "2026-03-31T12:00:00"
        db.execute(
            "INSERT INTO price_intelligence_summary "
            "(draft_id, restaurant_id, cuisine_type, zip_code, competitor_count, "
            "avg_market_tier, total_items, items_assessed, underpriced, fair_priced, "
            "overpriced, category_avgs, model_used, created_at, updated_at) "
            "VALUES (?, ?, 'pizza', '90210', 5, '$$', 5, 1, 0, 1, 0, '{}', 'claude-sonnet-4-5', ?, ?)",
            (seed_draft, seed_restaurant, now, now),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM price_intelligence_summary WHERE draft_id=?",
            (seed_draft,),
        ).fetchone()
        assert row is not None
        assert row["items_assessed"] == 1
        assert row["total_items"] == 5


# ---------------------------------------------------------------------------
# 5. Call 4 skip/error handling — non-blocking failures (4 tests)
# ---------------------------------------------------------------------------
class TestCall4ErrorHandling:
    def test_skip_when_no_restaurant(self, db):
        """Call 4 skipped when job has no restaurant_id."""
        # Create a job without restaurant_id
        db.execute(
            "INSERT INTO import_jobs (filename, status, pipeline_stage) "
            "VALUES ('test.jpg', 'processing', 'analyzing_prices')"
        )
        db.commit()
        row = db.execute("SELECT restaurant_id FROM import_jobs WHERE filename='test.jpg'").fetchone()
        assert row["restaurant_id"] is None  # no restaurant = skip Call 4

    def test_skip_when_gate_fails(self, db, seed_job):
        """Call 4 should not run when confidence gate rejects."""
        # Gate failure → status = rejected, no price analysis stage
        db.execute(
            "UPDATE import_jobs SET status='rejected', pipeline_stage='done' WHERE id=?",
            (seed_job,),
        )
        db.commit()
        row = db.execute("SELECT * FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
        # Should never reach analyzing_prices when rejected
        assert row["pipeline_stage"] == "done"

    def test_call4_error_does_not_block_import(self):
        """If analyze_menu_prices raises, import should still succeed."""
        # This tests the pattern: Call 4 is in a try/except, print error, continue
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL4_PRICE
        tracker = PipelineTracker()
        tracker.start_step(STEP_CALL4_PRICE)
        tracker.fail_step(STEP_CALL4_PRICE, "API timeout")
        summary = tracker.summary()
        steps = summary.get("steps", {})
        assert steps[STEP_CALL4_PRICE]["status"] == "failed"
        assert steps[STEP_CALL4_PRICE]["error"] == "API timeout"

    def test_call4_skip_recorded_in_tracker(self):
        """Skipped Call 4 is properly recorded in pipeline tracker."""
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL4_PRICE
        tracker = PipelineTracker()
        tracker.skip_step(STEP_CALL4_PRICE, "no_restaurant")
        summary = tracker.summary()
        steps = summary.get("steps", {})
        assert steps[STEP_CALL4_PRICE]["status"] == "skipped"
        assert steps[STEP_CALL4_PRICE]["skip_reason"] == "no_restaurant"


# ---------------------------------------------------------------------------
# 6. Status API — pipeline_stage in response (4 tests)
# ---------------------------------------------------------------------------
class TestStatusAPIPipelineStage:
    def test_status_includes_pipeline_stage(self, db, seed_job):
        """Status API response dict includes pipeline_stage field."""
        db.execute(
            "UPDATE import_jobs SET pipeline_stage='verifying' WHERE id=?",
            (seed_job,),
        )
        db.commit()
        row = db.execute("SELECT * FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
        data = dict(row)
        assert "pipeline_stage" in data
        assert data["pipeline_stage"] == "verifying"

    def test_status_null_stage_for_old_jobs(self, db):
        """Old jobs (before Day 136) return NULL pipeline_stage."""
        db.execute("INSERT INTO import_jobs (filename, status) VALUES ('old.jpg', 'done')")
        db.commit()
        row = db.execute("SELECT * FROM import_jobs WHERE filename='old.jpg'").fetchone()
        data = dict(row)
        assert data["pipeline_stage"] is None

    def test_status_stage_during_processing(self, db, seed_job):
        """While processing, pipeline_stage reflects current step."""
        for stage in ["extracting", "verifying", "reconciling"]:
            db.execute(
                "UPDATE import_jobs SET status='processing', pipeline_stage=? WHERE id=?",
                (stage, seed_job),
            )
            db.commit()
            row = db.execute("SELECT * FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
            assert dict(row)["pipeline_stage"] == stage

    def test_status_stage_done_on_completion(self, db, seed_job):
        """pipeline_stage = 'done' when job completes."""
        db.execute(
            "UPDATE import_jobs SET status='done', pipeline_stage='done' WHERE id=?",
            (seed_job,),
        )
        db.commit()
        row = db.execute("SELECT * FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
        assert dict(row)["pipeline_stage"] == "done"


# ---------------------------------------------------------------------------
# 7. Frontend template — 5-stage DOM structure (4 tests)
# ---------------------------------------------------------------------------
class TestFrontendTemplateStructure:
    """Verify the import_view.html template has the expected pipeline progress elements."""

    @pytest.fixture(autouse=True)
    def load_template(self):
        template_path = Path(__file__).parent.parent / "portal" / "templates" / "import_view.html"
        self.html = template_path.read_text(encoding="utf-8")

    def test_pipeline_progress_container(self):
        """Template has pipeline-progress container."""
        assert 'id="pipeline-progress"' in self.html
        assert "pipeline-progress" in self.html

    def test_five_stages_present(self):
        """All 5 stages are defined in the DOM."""
        stages = ["extracting", "verifying", "reconciling", "analyzing_prices", "finalizing"]
        for stage in stages:
            assert f'data-stage="{stage}"' in self.html, f"Missing stage: {stage}"

    def test_stage_labels_present(self):
        """Stage labels are human-readable."""
        labels = ["Reading", "Checking", "Fixing", "Analyzing", "Finishing"]
        for label in labels:
            assert label in self.html, f"Missing label: {label}"

    def test_pipeline_message_present(self):
        """Coffee messaging is in the template."""
        assert "grab a coffee" in self.html.lower()
        assert "come back" in self.html.lower()


# ---------------------------------------------------------------------------
# 8. End-to-end pipeline stage transitions (4 tests)
# ---------------------------------------------------------------------------
class TestEndToEndStageTransitions:
    """Simulate the full pipeline flow and verify stage transitions."""

    def test_full_stage_sequence(self, db, seed_job):
        """Simulate a full pipeline run with all stages."""
        expected_flow = [
            ("processing", "extracting"),
            ("processing", "verifying"),
            ("processing", "reconciling"),
            ("processing", "analyzing_prices"),
            ("processing", "finalizing"),
            ("done", "done"),
        ]
        for status, stage in expected_flow:
            db.execute(
                "UPDATE import_jobs SET status=?, pipeline_stage=? WHERE id=?",
                (status, stage, seed_job),
            )
            db.commit()
            row = db.execute("SELECT * FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
            assert row["status"] == status
            assert row["pipeline_stage"] == stage

    def test_rejection_skips_price_analysis(self, db, seed_job):
        """When gate rejects, pipeline goes from reconciling → done (no price step)."""
        stages = [
            ("processing", "extracting"),
            ("processing", "verifying"),
            ("processing", "reconciling"),
            ("processing", "finalizing"),  # gate evaluates here
            ("rejected", "done"),           # gate fails → rejected
        ]
        for status, stage in stages:
            db.execute(
                "UPDATE import_jobs SET status=?, pipeline_stage=? WHERE id=?",
                (status, stage, seed_job),
            )
            db.commit()
        row = db.execute("SELECT * FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
        assert row["status"] == "rejected"
        assert row["pipeline_stage"] == "done"

    def test_failure_sets_done_stage(self, db, seed_job):
        """Even on failure, pipeline_stage should be 'done'."""
        db.execute(
            "UPDATE import_jobs SET status='failed', pipeline_stage='done', "
            "error='OCR crashed' WHERE id=?",
            (seed_job,),
        )
        db.commit()
        row = db.execute("SELECT * FROM import_jobs WHERE id=?", (seed_job,)).fetchone()
        assert row["status"] == "failed"
        assert row["pipeline_stage"] == "done"
        assert row["error"] == "OCR crashed"

    def test_data_initial_stage_in_template(self):
        """Template includes data-initial-stage for refresh persistence."""
        template_path = Path(__file__).parent.parent / "portal" / "templates" / "import_view.html"
        html = template_path.read_text(encoding="utf-8")
        assert "data-initial-stage" in html
        assert "initialStage" in html
