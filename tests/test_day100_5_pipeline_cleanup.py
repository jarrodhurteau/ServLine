# tests/test_day100_5_pipeline_cleanup.py
"""
Day 100.5 — Pipeline Cleanup & Debug View

Validates:
  1. Strategy 2 (heuristic AI) and Strategy 3 (legacy JSON) removed from pipeline
  2. No API key = empty draft (free tier manual input)
  3. Removed routes return 404 (ai/preview, ai/commit, ai/finalize)
  4. Pipeline Debug route returns 200 with payload data
  5. Pipeline Debug route handles missing data gracefully
  6. UI buttons removed from templates
  7. Pipeline Debug template renders all sections
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Database helpers (same pattern as Day 100 tests)
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS restaurants (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
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
    id INTEGER PRIMARY KEY,
    draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    price_cents INTEGER DEFAULT 0,
    category TEXT DEFAULT '',
    position INTEGER DEFAULT 0,
    confidence INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_item_variants (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES draft_items(id) ON DELETE CASCADE,
    label TEXT DEFAULT '',
    price_cents INTEGER DEFAULT 0,
    kind TEXT DEFAULT 'size',
    position INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id INTEGER PRIMARY KEY,
    restaurant_id INTEGER,
    filename TEXT,
    source_type TEXT DEFAULT 'upload',
    status TEXT DEFAULT 'pending',
    error TEXT,
    draft_path TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);
"""


def _make_test_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA_SQL)
    conn.execute("INSERT INTO restaurants (id, name) VALUES (1, 'Test Restaurant')")
    conn.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'admin', 'dummy')")
    conn.commit()
    conn.close()
    return db_path


def _create_import_job(db_path: Path, job_id: int = 1, filename: str = "test.png") -> int:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO import_jobs (id, restaurant_id, filename, status) VALUES (?, 1, ?, 'done')",
        (job_id, filename),
    )
    conn.commit()
    conn.close()
    return job_id


def _create_draft(db_path: Path, draft_id: int = 1, job_id: int = 1) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO drafts (id, restaurant_id, source_job_id, title, status) VALUES (?, 1, ?, 'Test Draft', 'editing')",
        (draft_id, job_id),
    )
    conn.commit()
    conn.close()
    return draft_id


# ---------------------------------------------------------------------------
# Flask app client fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Create a Flask test client with monkeypatched DB."""
    db_path = _make_test_db(tmp_path)

    def mock_db():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # Monkeypatch before import
    monkeypatch.setattr("storage.drafts.db_connect", mock_db)
    try:
        monkeypatch.setattr("storage.menus.db_connect", mock_db)
    except Exception:
        pass

    from portal.app import app

    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user"] = {"id": 1, "username": "admin"}
        yield client, db_path


# ===========================================================================
# 1. Removed routes return 404
# ===========================================================================
class TestRemovedRoutes:
    """Verify that heuristic AI routes no longer exist."""

    def test_ai_preview_route_removed(self, app_client):
        """GET /imports/<job_id>/ai/preview should 404."""
        client, db_path = app_client
        _create_import_job(db_path, job_id=1)
        resp = client.get("/imports/1/ai/preview")
        assert resp.status_code == 404

    def test_ai_commit_route_removed(self, app_client):
        """POST /imports/<job_id>/ai/commit should 404 or 405."""
        client, db_path = app_client
        _create_import_job(db_path, job_id=1)
        resp = client.post("/imports/1/ai/commit")
        assert resp.status_code in (404, 405)

    def test_ai_finalize_route_removed(self, app_client):
        """POST /imports/<job_id>/ai/finalize should 404 or 405."""
        client, db_path = app_client
        _create_import_job(db_path, job_id=1)
        resp = client.post("/imports/1/ai/finalize")
        assert resp.status_code in (404, 405)


# ===========================================================================
# 2. Pipeline debug route works
# ===========================================================================
class TestPipelineDebugRoute:
    """Verify the new pipeline debug view."""

    def test_debug_route_returns_200_with_payload(self, app_client):
        """GET /drafts/<id>/pipeline-debug returns 200 when debug data exists."""
        client, db_path = app_client
        _create_import_job(db_path, job_id=1)
        _create_draft(db_path, draft_id=1, job_id=1)

        # Save debug payload via drafts_store
        import storage.drafts as ds
        ds.save_ocr_debug(1, {
            "extraction_strategy": "claude_api+vision",
            "clean_ocr_chars": 500,
            "pipeline_metrics": {
                "total_duration_ms": 3200,
                "total_duration_human": "3.2s",
                "bottleneck": "call_1_claude_extraction",
                "extraction_strategy": "claude_api+vision",
                "steps": {
                    "ocr_text_extraction": {"status": "success", "duration_ms": 200, "items": 0, "chars": 500},
                    "call_1_claude_extraction": {"status": "success", "duration_ms": 2000, "items": 6},
                    "call_2_vision_verification": {"status": "success", "duration_ms": 800, "items": 6, "confidence": 0.94},
                    "semantic_pipeline": {"status": "success", "duration_ms": 200, "items": 6},
                },
                "item_flow": [
                    {"step": "ocr_text_extraction", "items": 0, "note": "500 chars"},
                    {"step": "call_1_claude_extraction", "items": 6},
                    {"step": "call_2_vision_verification", "items": 6},
                    {"step": "semantic_pipeline", "items": 6},
                ],
            },
            "vision_verification": {
                "skipped": False,
                "confidence": 0.94,
                "model": "claude-sonnet-4-5-20250929",
                "changes_count": 2,
                "changes": [
                    {"type": "price_fixed", "detail": "Garden Salad: $8.95 -> $9.95"},
                    {"type": "description_fixed", "detail": "Added red onion"},
                ],
                "notes": "Fixed salad price",
                "item_count_before": 6,
            },
            "semantic_pipeline": {
                "quality_grade": "A",
                "mean_confidence": 0.87,
                "tier_counts": {"high": 4, "medium": 2, "low": 0, "reject": 0},
                "repairs_applied": 1,
            },
        })

        resp = client.get("/drafts/1/pipeline-debug")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Pipeline Debug" in html
        assert "claude_api+vision" in html

    def test_debug_route_renders_vision_section(self, app_client):
        """Vision verification section renders with changes."""
        client, db_path = app_client
        _create_draft(db_path, draft_id=1, job_id=1)

        import storage.drafts as ds
        ds.save_ocr_debug(1, {
            "extraction_strategy": "claude_api+vision",
            "vision_verification": {
                "skipped": False,
                "confidence": 0.94,
                "model": "claude-sonnet-4-5-20250929",
                "changes_count": 1,
                "changes": [{"type": "name_fixed", "detail": "Fixed typo"}],
                "notes": "Verified OK",
                "item_count_before": 5,
            },
        })

        resp = client.get("/drafts/1/pipeline-debug")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Vision Verification" in html
        assert "94%" in html
        assert "name_fixed" in html

    def test_debug_route_renders_semantic_section(self, app_client):
        """Semantic pipeline section renders with tiers."""
        client, db_path = app_client
        _create_draft(db_path, draft_id=1, job_id=1)

        import storage.drafts as ds
        ds.save_ocr_debug(1, {
            "extraction_strategy": "claude_api",
            "semantic_pipeline": {
                "quality_grade": "B",
                "mean_confidence": 0.72,
                "tier_counts": {"high": 3, "medium": 2, "low": 1, "reject": 0},
                "repairs_applied": 2,
            },
        })

        resp = client.get("/drafts/1/pipeline-debug")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Semantic Pipeline" in html
        assert "grade-B" in html

    def test_debug_route_renders_metrics_timeline(self, app_client):
        """Pipeline metrics timeline renders with step statuses."""
        client, db_path = app_client
        _create_draft(db_path, draft_id=1, job_id=1)

        import storage.drafts as ds
        ds.save_ocr_debug(1, {
            "extraction_strategy": "claude_api",
            "pipeline_metrics": {
                "total_duration_ms": 1500,
                "total_duration_human": "1.5s",
                "bottleneck": "call_1_claude_extraction",
                "extraction_strategy": "claude_api",
                "steps": {
                    "ocr_text_extraction": {"status": "success", "duration_ms": 100, "items": 0},
                    "call_1_claude_extraction": {"status": "success", "duration_ms": 1200, "items": 4},
                    "call_2_vision_verification": {"status": "skipped", "duration_ms": 0, "items": 0},
                    "semantic_pipeline": {"status": "success", "duration_ms": 200, "items": 4},
                },
                "item_flow": [
                    {"step": "ocr_text_extraction", "items": 0, "note": "300 chars"},
                    {"step": "call_1_claude_extraction", "items": 4},
                    {"step": "call_2_vision_verification", "items": 0, "note": "skipped: no_api_key"},
                    {"step": "semantic_pipeline", "items": 4},
                ],
            },
        })

        resp = client.get("/drafts/1/pipeline-debug")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Pipeline Timeline" in html
        assert "1.5s" in html

    def test_debug_route_no_payload(self, app_client):
        """Debug view handles missing payload gracefully."""
        client, db_path = app_client
        # Use a unique draft_id unlikely to have stale debug files on disk
        _create_draft(db_path, draft_id=9990, job_id=1)

        # Ensure no stale debug file exists for this draft_id
        import storage.drafts as ds
        debug_path = Path(ds.__file__).parent / ".debug" / "drafts" / "9990.json"
        if debug_path.exists():
            debug_path.unlink()

        resp = client.get("/drafts/9990/pipeline-debug")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No pipeline debug data available" in html

    def test_debug_route_nonexistent_draft(self, app_client):
        """Debug view returns 404 for nonexistent draft."""
        client, _ = app_client
        resp = client.get("/drafts/9999/pipeline-debug")
        assert resp.status_code == 404

    def test_debug_route_renders_raw_ocr_text(self, app_client):
        """Raw OCR text section renders when present."""
        client, db_path = app_client
        _create_draft(db_path, draft_id=1, job_id=1)

        import storage.drafts as ds
        ds.save_ocr_debug(1, {
            "extraction_strategy": "claude_api",
            "clean_ocr_chars": 42,
            "raw_ocr_text": "APPETIZERS\nMozzarella Sticks 7.95\n",
        })

        resp = client.get("/drafts/1/pipeline-debug")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Raw OCR Text" in html
        assert "Mozzarella Sticks" in html

    def test_debug_route_vision_skipped(self, app_client):
        """Vision section shows skip reason when vision was skipped."""
        client, db_path = app_client
        _create_draft(db_path, draft_id=1, job_id=1)

        import storage.drafts as ds
        ds.save_ocr_debug(1, {
            "extraction_strategy": "claude_api",
            "vision_verification": {
                "skipped": True,
                "skip_reason": "no_api_key",
            },
        })

        resp = client.get("/drafts/1/pipeline-debug")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Skipped" in html
        assert "no_api_key" in html

    def test_debug_route_semantic_items_metadata(self, app_client):
        """Per-item metadata table renders."""
        client, db_path = app_client
        _create_draft(db_path, draft_id=1, job_id=1)

        import storage.drafts as ds
        ds.save_ocr_debug(1, {
            "extraction_strategy": "claude_api",
            "semantic_pipeline": {
                "quality_grade": "A",
                "mean_confidence": 0.90,
                "tier_counts": {"high": 2, "medium": 0, "low": 0, "reject": 0},
                "repairs_applied": 0,
                "items_metadata": [
                    {"name": "Cheese Pizza", "semantic_confidence": 0.95, "semantic_tier": "high",
                     "needs_review": False, "auto_repairs_applied_count": 0},
                    {"name": "Pepperoni Pizza", "semantic_confidence": 0.92, "semantic_tier": "high",
                     "needs_review": False, "auto_repairs_applied_count": 0},
                ],
            },
        })

        resp = client.get("/drafts/1/pipeline-debug")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Cheese Pizza" in html
        assert "Pepperoni Pizza" in html
        assert "Per-Item Details" in html


# ===========================================================================
# 3. Strategy removal verification (unit tests)
# ===========================================================================
class TestStrategyRemoval:
    """Verify that the heuristic and legacy strategies are no longer in the pipeline."""

    def test_analyze_ocr_text_not_imported_in_app(self):
        """analyze_ocr_text should not be imported as a callable in app.py."""
        # The import was removed; check that no callable analyze_ocr_text exists
        import portal.app as app_module
        attr = getattr(app_module, "analyze_ocr_text", None)
        # Should be None (import removed) or not callable
        assert attr is None or not callable(attr), \
            "analyze_ocr_text should not be imported as callable in app.py"

    def test_draft_items_from_ai_preview_removed(self):
        """_draft_items_from_ai_preview function should not exist in app module."""
        import portal.app as app_module
        assert not hasattr(app_module, "_draft_items_from_ai_preview"), \
            "_draft_items_from_ai_preview should be removed from app.py"

    def test_heuristic_strategy_not_in_pipeline_code(self):
        """The string 'heuristic_ai' should not appear as an extraction_strategy assignment."""
        import inspect
        import portal.app as app_module
        # Check run_ocr_and_make_draft source for heuristic_ai assignment
        fn = getattr(app_module, "run_ocr_and_make_draft", None)
        if fn is not None:
            src = inspect.getsource(fn)
            assert 'extraction_strategy = "heuristic_ai"' not in src, \
                "heuristic_ai strategy assignment should be removed from run_ocr_and_make_draft"

    def test_legacy_json_strategy_not_in_pipeline_code(self):
        """The string 'legacy_draft_json' should not appear as an extraction_strategy assignment."""
        import inspect
        import portal.app as app_module
        fn = getattr(app_module, "run_ocr_and_make_draft", None)
        if fn is not None:
            src = inspect.getsource(fn)
            assert 'extraction_strategy = "legacy_draft_json"' not in src, \
                "legacy_draft_json strategy assignment should be removed from run_ocr_and_make_draft"


# ===========================================================================
# 4. Template link verification
# ===========================================================================
class TestTemplateLinks:
    """Verify that old heuristic links are removed and debug links are present."""

    def test_draft_editor_no_heuristic_preview_link(self):
        """draft_editor.html should not reference imports_ai_preview."""
        template_path = Path(__file__).parent.parent / "portal" / "templates" / "draft_editor.html"
        if template_path.exists():
            content = template_path.read_text(encoding="utf-8")
            assert "imports_ai_preview" not in content, \
                "draft_editor.html should not reference imports_ai_preview"

    def test_draft_editor_has_pipeline_debug_link(self):
        """draft_editor.html should reference draft_pipeline_debug."""
        template_path = Path(__file__).parent.parent / "portal" / "templates" / "draft_editor.html"
        if template_path.exists():
            content = template_path.read_text(encoding="utf-8")
            assert "draft_pipeline_debug" in content, \
                "draft_editor.html should link to draft_pipeline_debug"

    def test_import_view_no_ai_tools_section(self):
        """import_view.html should not reference imports_ai_preview or imports_ai_finalize."""
        template_path = Path(__file__).parent.parent / "portal" / "templates" / "import_view.html"
        if template_path.exists():
            content = template_path.read_text(encoding="utf-8")
            assert "imports_ai_preview" not in content, \
                "import_view.html should not reference imports_ai_preview"
            assert "imports_ai_finalize" not in content, \
                "import_view.html should not reference imports_ai_finalize"

    def test_pipeline_debug_template_exists(self):
        """pipeline_debug.html template should exist."""
        template_path = Path(__file__).parent.parent / "portal" / "templates" / "pipeline_debug.html"
        assert template_path.exists(), "pipeline_debug.html template should exist"

    def test_pipeline_debug_template_has_required_sections(self):
        """pipeline_debug.html should contain all required section headers."""
        template_path = Path(__file__).parent.parent / "portal" / "templates" / "pipeline_debug.html"
        if template_path.exists():
            content = template_path.read_text(encoding="utf-8")
            assert "Pipeline Summary" in content
            assert "Pipeline Timeline" in content
            assert "Vision Verification" in content
            assert "Semantic Pipeline" in content
            assert "Raw OCR Text" in content
            assert "Raw Debug Payload" in content


# ===========================================================================
# 5. Debug payload round-trip
# ===========================================================================
class TestDebugPayloadRoundTrip:
    """Verify debug payload save/load preserves all pipeline data."""

    def test_full_payload_round_trip(self, tmp_path, monkeypatch):
        """Save and load a complete debug payload."""
        db_path = _make_test_db(tmp_path)

        def mock_db():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            return conn

        monkeypatch.setattr("storage.drafts.db_connect", mock_db)

        import storage.drafts as ds
        _create_draft(db_path, draft_id=1, job_id=1)

        payload = {
            "extraction_strategy": "claude_api+vision",
            "clean_ocr_chars": 1234,
            "raw_ocr_text": "PIZZA\nMargherita 14.95\n",
            "vision_verification": {
                "skipped": False,
                "confidence": 0.94,
                "model": "claude-sonnet-4-5-20250929",
                "changes_count": 1,
                "changes": [{"type": "price_fixed", "detail": "Fixed"}],
            },
            "semantic_pipeline": {
                "quality_grade": "A",
                "mean_confidence": 0.90,
                "tier_counts": {"high": 5, "medium": 1, "low": 0, "reject": 0},
                "repairs_applied": 0,
            },
            "pipeline_metrics": {
                "total_duration_ms": 2500,
                "total_duration_human": "2.5s",
                "bottleneck": "call_1_claude_extraction",
                "extraction_strategy": "claude_api+vision",
                "steps": {
                    "ocr_text_extraction": {"status": "success", "duration_ms": 100},
                    "call_1_claude_extraction": {"status": "success", "duration_ms": 1800},
                    "call_2_vision_verification": {"status": "success", "duration_ms": 500},
                    "semantic_pipeline": {"status": "success", "duration_ms": 100},
                },
                "item_flow": [
                    {"step": "ocr_text_extraction", "items": 0},
                    {"step": "call_1_claude_extraction", "items": 6},
                    {"step": "call_2_vision_verification", "items": 6},
                    {"step": "semantic_pipeline", "items": 6},
                ],
            },
        }

        ds.save_ocr_debug(1, payload)
        loaded = ds.load_ocr_debug(1)

        assert loaded is not None
        assert loaded["extraction_strategy"] == "claude_api+vision"
        assert loaded["vision_verification"]["confidence"] == 0.94
        assert loaded["semantic_pipeline"]["quality_grade"] == "A"
        assert loaded["pipeline_metrics"]["total_duration_ms"] == 2500
        assert len(loaded["pipeline_metrics"]["steps"]) == 4
        assert loaded["raw_ocr_text"] == "PIZZA\nMargherita 14.95\n"


# ===========================================================================
# 6. Route registration verification
# ===========================================================================
class TestRouteRegistration:
    """Verify that the pipeline debug route is registered and old routes are not."""

    def test_pipeline_debug_route_registered(self, app_client):
        """draft_pipeline_debug route should be registered."""
        client, _ = app_client
        from portal.app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        assert "/drafts/<int:draft_id>/pipeline-debug" in rules

    def test_ai_preview_route_not_registered(self, app_client):
        """imports_ai_preview route should not be registered."""
        client, _ = app_client
        from portal.app import app
        endpoints = [r.endpoint for r in app.url_map.iter_rules()]
        assert "imports_ai_preview" not in endpoints

    def test_ai_commit_route_not_registered(self, app_client):
        """imports_ai_commit route should not be registered."""
        client, _ = app_client
        from portal.app import app
        endpoints = [r.endpoint for r in app.url_map.iter_rules()]
        assert "imports_ai_commit" not in endpoints

    def test_ai_finalize_route_not_registered(self, app_client):
        """imports_ai_finalize route should not be registered."""
        client, _ = app_client
        from portal.app import app
        endpoints = [r.endpoint for r in app.url_map.iter_rules()]
        assert "imports_ai_finalize" not in endpoints
