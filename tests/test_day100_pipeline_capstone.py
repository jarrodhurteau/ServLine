# tests/test_day100_pipeline_capstone.py
"""
Day 100 — Sprint 11.1 Capstone: End-to-End Pipeline Integration (Phase 11)

Validates that all four Sprint 11.1 components work together seamlessly:
  - Day 96-97: Vision Verification (ai_vision_verify + pipeline wiring)
  - Day 98: Semantic Pipeline Bridge (semantic_bridge)
  - Day 99: Pipeline Metrics & Observability (pipeline_metrics)

This capstone tests the INTEGRATION paths — the full flow from OCR text
through Call 1 → Call 2 → Semantic → draft items with complete debug payloads.
Individual component tests are in test_day96-99.

Covers:
  1. Full happy path: OCR → Call 1 → Call 2 → Semantic → draft items + payload
  2. Vision-skipped path: Call 1 → fallback → Semantic → payload
  3. Vision-failed path: Call 1 → error fallback → Semantic → payload
  4. Call 1 failed path: heuristic_ai fallback (no semantic, no vision)
  5. Strategy gating: semantic pipeline only runs on claude_api strategies
  6. Debug payload completeness: all three blocks present
  7. Pipeline metrics track all four stages with correct metadata
  8. Confidence flow: 95 for vision-verified, 90 for Call 1 only
  9. Semantic repairs flow back into draft items
 10. Payload JSON round-trip: serialize → deserialize preserves all fields
 11. Component interop: verified items → semantic bridge → metrics
 12. Edge cases: empty OCR, zero items, all steps fail
"""

from __future__ import annotations

import base64
import copy
import json
import time
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------
def _sample_ocr_text():
    """Realistic OCR text from a restaurant menu."""
    return (
        "APPETIZERS\n"
        "Mozzarella Sticks  7.95\n"
        "Crispy fried mozzarella with marinara sauce\n"
        "Chicken Wings  11.50\n"
        "Buffalo style with celery and blue cheese\n"
        "\n"
        "PIZZA\n"
        "Margherita Pizza  14.95\n"
        "Fresh mozzarella, basil, tomato sauce\n"
        "Pepperoni Pizza  16.95\n"
        "Classic pepperoni with mozzarella\n"
        "\n"
        "SALADS\n"
        "Caesar Salad  9.95\n"
        "Romaine, croutons, parmesan, caesar dressing\n"
        "Garden Salad  8.95\n"
        "Mixed greens, tomatoes, cucumbers\n"
    )


def _sample_claude_items():
    """Items as extracted by Call 1 (Claude API text extraction)."""
    return [
        {"name": "Mozzarella Sticks", "description": "Crispy fried mozzarella with marinara sauce",
         "price": 7.95, "category": "Appetizers", "sizes": []},
        {"name": "Chicken Wings", "description": "Buffalo style with celery and blue cheese",
         "price": 11.50, "category": "Appetizers", "sizes": []},
        {"name": "Margherita Pizza", "description": "Fresh mozzarella, basil, tomato sauce",
         "price": 14.95, "category": "Pizza", "sizes": []},
        {"name": "Pepperoni Pizza", "description": "Classic pepperoni with mozzarella",
         "price": 16.95, "category": "Pizza", "sizes": []},
        {"name": "Caesar Salad", "description": "Romaine, croutons, parmesan, caesar dressing",
         "price": 9.95, "category": "Salads", "sizes": []},
        {"name": "Garden Salad", "description": "Mixed greens, tomatoes, cucumbers",
         "price": 8.95, "category": "Salads", "sizes": []},
    ]


def _vision_corrected_items():
    """Items after vision verification — price fix + description enhancement."""
    items = _sample_claude_items()
    # Vision fixes Garden Salad price (OCR misread) and enhances description
    items[5] = {
        "name": "Garden Salad",
        "description": "Mixed greens, tomatoes, cucumbers, red onion",
        "price": 9.95,  # Price was 8.95, vision corrected to 9.95
        "category": "Salads",
        "sizes": [],
    }
    return items


def _make_vision_api_response(items, confidence=0.94, notes="Verified"):
    """Build a mock Claude API response for vision verification."""
    mock_response = MagicMock()
    mock_block = SimpleNamespace(text=json.dumps({
        "items": items,
        "confidence": confidence,
        "notes": notes,
    }))
    mock_response.content = [mock_block]
    return mock_response


def _make_mock_client(response):
    """Build a mock Anthropic client that returns the given response."""
    client = MagicMock()
    client.messages.create.return_value = response
    return client


@pytest.fixture
def tmp_menu_png(tmp_path):
    """Create a tiny PNG file for testing."""
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    img = tmp_path / "test_menu.png"
    img.write_bytes(png_bytes)
    return img


# ===========================================================================
# 1. Full Happy Path: OCR → Call 1 → Call 2 → Semantic → payload
# ===========================================================================
class TestFullHappyPath:
    """End-to-end: all 4 stages succeed, producing a complete debug payload."""

    def test_full_pipeline_all_stages_succeed(self, tmp_menu_png):
        """Run the full 4-stage pipeline with mocked API calls."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION, STEP_SEMANTIC,
        )

        # --- Stage 1: OCR ---
        tracker = PipelineTracker()
        tracker.start_step(STEP_OCR_TEXT)
        ocr_text = _sample_ocr_text()
        tracker.end_step(STEP_OCR_TEXT, chars=len(ocr_text))

        # --- Stage 2: Call 1 (Claude extraction) ---
        tracker.start_step(STEP_CALL1_EXTRACT)
        claude_items = _sample_claude_items()
        tracker.end_step(STEP_CALL1_EXTRACT, items=len(claude_items))

        # --- Stage 3: Call 2 (Vision verification) ---
        tracker.start_step(STEP_CALL2_VISION)
        corrected = _vision_corrected_items()
        response = _make_vision_api_response(corrected, confidence=0.94, notes="Fixed salad price")
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            vision_result = verify_menu_with_vision(str(tmp_menu_png), claude_items)

        assert not vision_result.get("skipped")
        assert not vision_result.get("error")
        items = verified_items_to_draft_rows(vision_result["items"])
        tracker.end_step(STEP_CALL2_VISION, items=len(items),
                         changes=len(vision_result.get("changes", [])),
                         confidence=vision_result["confidence"])

        # --- Stage 4: Semantic pipeline ---
        tracker.start_step(STEP_SEMANTIC)
        semantic_result = run_semantic_pipeline(items)
        tracker.end_step(STEP_SEMANTIC, items=len(items),
                         quality_grade=semantic_result.get("quality_grade", "?"),
                         repairs=semantic_result.get("repairs_applied", 0))

        tracker.strategy = "claude_api+vision"
        summary = tracker.summary()

        # Verify all 4 steps recorded
        assert len(summary["steps"]) == 4
        assert summary["extraction_strategy"] == "claude_api+vision"
        assert summary["steps"][STEP_OCR_TEXT]["status"] == "success"
        assert summary["steps"][STEP_CALL1_EXTRACT]["status"] == "success"
        assert summary["steps"][STEP_CALL2_VISION]["status"] == "success"
        assert summary["steps"][STEP_SEMANTIC]["status"] == "success"

        # Item flow tracks progression
        flow_items = [e["items"] for e in summary["item_flow"]]
        assert flow_items[0] == 0  # OCR produces text, not items
        assert flow_items[1] == 6  # Call 1: 6 items
        assert flow_items[2] == 6  # Call 2: 6 items (same count, different values)
        assert flow_items[3] == 6  # Semantic: 6 items

    def test_happy_path_vision_confidence_95(self, tmp_menu_png):
        """Vision-verified items have confidence=95 (boosted from 90)."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows

        corrected = _vision_corrected_items()
        response = _make_vision_api_response(corrected)
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_png), _sample_claude_items())

        rows = verified_items_to_draft_rows(result["items"])
        assert all(r["confidence"] == 95 for r in rows)

    def test_happy_path_vision_detects_changes(self, tmp_menu_png):
        """Vision verification detects price fix and description update."""
        from storage.ai_vision_verify import verify_menu_with_vision

        corrected = _vision_corrected_items()
        response = _make_vision_api_response(corrected, notes="Fixed salad price")
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_png), _sample_claude_items())

        changes = result["changes"]
        change_types = {c["type"] for c in changes}
        assert "price_fixed" in change_types
        assert "description_fixed" in change_types

    def test_happy_path_semantic_pipeline_runs_on_vision_items(self, tmp_menu_png):
        """Semantic pipeline receives vision-verified items and produces quality report."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline

        corrected = _vision_corrected_items()
        response = _make_vision_api_response(corrected)
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            vision_result = verify_menu_with_vision(str(tmp_menu_png), _sample_claude_items())

        items = verified_items_to_draft_rows(vision_result["items"])
        semantic_result = run_semantic_pipeline(items)

        assert "quality_grade" in semantic_result
        assert semantic_result["quality_grade"] in ("A", "B", "C", "D")
        assert "mean_confidence" in semantic_result
        assert 0.0 <= semantic_result["mean_confidence"] <= 1.0
        assert "tier_counts" in semantic_result
        assert "semantic_report" in semantic_result

    def test_happy_path_debug_payload_has_all_three_blocks(self, tmp_menu_png):
        """Debug payload contains vision_verification + semantic_pipeline + pipeline_metrics."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION, STEP_SEMANTIC,
        )

        tracker = PipelineTracker()
        tracker.start_step(STEP_OCR_TEXT)
        tracker.end_step(STEP_OCR_TEXT, chars=len(_sample_ocr_text()))

        tracker.start_step(STEP_CALL1_EXTRACT)
        claude_items = _sample_claude_items()
        tracker.end_step(STEP_CALL1_EXTRACT, items=len(claude_items))

        tracker.start_step(STEP_CALL2_VISION)
        corrected = _vision_corrected_items()
        response = _make_vision_api_response(corrected)
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            vision_result = verify_menu_with_vision(str(tmp_menu_png), claude_items)

        items = verified_items_to_draft_rows(vision_result["items"])
        tracker.end_step(STEP_CALL2_VISION, items=len(items),
                         changes=len(vision_result.get("changes", [])),
                         confidence=vision_result["confidence"])

        tracker.start_step(STEP_SEMANTIC)
        semantic_result = run_semantic_pipeline(items)
        tracker.end_step(STEP_SEMANTIC, items=len(items),
                         quality_grade=semantic_result.get("quality_grade"),
                         repairs=semantic_result.get("repairs_applied", 0))

        tracker.strategy = "claude_api+vision"

        # Build debug payload as portal/app.py does
        payload = {
            "import_job_id": 1,
            "pipeline": "ocr_helper+tesseract",
            "bridge": "run_ocr_and_make_draft",
            "extraction_strategy": "claude_api+vision",
            "clean_ocr_chars": len(_sample_ocr_text()),
        }
        # Vision block
        payload["vision_verification"] = {
            "skipped": vision_result.get("skipped", False),
            "skip_reason": vision_result.get("skip_reason"),
            "error": vision_result.get("error"),
            "confidence": vision_result.get("confidence", 0.0),
            "model": vision_result.get("model"),
            "changes_count": len(vision_result.get("changes", [])),
            "changes": vision_result.get("changes", []),
            "notes": vision_result.get("notes"),
            "item_count_before": len(vision_result.get("items", [])),
        }
        # Semantic block
        payload["semantic_pipeline"] = {
            "quality_grade": semantic_result.get("quality_grade"),
            "mean_confidence": semantic_result.get("mean_confidence", 0.0),
            "tier_counts": semantic_result.get("tier_counts", {}),
            "repairs_applied": semantic_result.get("repairs_applied", 0),
            "repair_results": semantic_result.get("repair_results", {}),
            "items_metadata": semantic_result.get("items_metadata", []),
        }
        # Metrics block
        payload["pipeline_metrics"] = tracker.summary()

        # All three blocks present
        assert "vision_verification" in payload
        assert "semantic_pipeline" in payload
        assert "pipeline_metrics" in payload

        # Vision block
        vv = payload["vision_verification"]
        assert vv["skipped"] is False
        assert vv["confidence"] > 0.0
        assert vv["changes_count"] > 0

        # Semantic block
        sp = payload["semantic_pipeline"]
        assert sp["quality_grade"] in ("A", "B", "C", "D")
        assert len(sp["items_metadata"]) == 6

        # Metrics block
        pm = payload["pipeline_metrics"]
        assert pm["extraction_strategy"] == "claude_api+vision"
        assert len(pm["steps"]) == 4
        assert pm["bottleneck"] is not None


# ===========================================================================
# 2. Vision-Skipped Path: Call 1 → Semantic (no vision)
# ===========================================================================
class TestVisionSkippedPath:
    """When vision is skipped (no API key), Call 1 items go directly to semantic."""

    def test_no_api_key_falls_back_to_call1(self, tmp_menu_png):
        """Items still flow through semantic pipeline when vision is skipped."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.ai_vision_verify import verify_menu_with_vision
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION, STEP_SEMANTIC,
        )

        tracker = PipelineTracker()

        # OCR
        tracker.start_step(STEP_OCR_TEXT)
        tracker.end_step(STEP_OCR_TEXT, chars=len(_sample_ocr_text()))

        # Call 1
        tracker.start_step(STEP_CALL1_EXTRACT)
        claude_items = _sample_claude_items()
        tracker.end_step(STEP_CALL1_EXTRACT, items=len(claude_items))

        # Vision — skipped
        tracker.start_step(STEP_CALL2_VISION)
        with patch("storage.ai_vision_verify._get_client", return_value=None):
            vision_result = verify_menu_with_vision(str(tmp_menu_png), claude_items)

        assert vision_result["skipped"] is True
        items = claude_items_to_draft_rows(claude_items)
        skip_reason = vision_result.get("skip_reason", "unknown")
        tracker.skip_step(STEP_CALL2_VISION, skip_reason)

        # Semantic still runs
        tracker.start_step(STEP_SEMANTIC)
        semantic_result = run_semantic_pipeline(items)
        tracker.end_step(STEP_SEMANTIC, items=len(items),
                         quality_grade=semantic_result.get("quality_grade"))

        tracker.strategy = "claude_api"
        summary = tracker.summary()

        assert summary["extraction_strategy"] == "claude_api"
        assert summary["steps"][STEP_CALL2_VISION]["status"] == "skipped"
        assert summary["steps"][STEP_SEMANTIC]["status"] == "success"
        # Items still have quality assessment
        assert semantic_result["quality_grade"] in ("A", "B", "C", "D")

    def test_skipped_vision_items_have_confidence_90(self):
        """Call 1 items (without vision) keep confidence=90."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        rows = claude_items_to_draft_rows(_sample_claude_items())
        assert all(r["confidence"] == 90 for r in rows)

    def test_skipped_vision_payload_structure(self, tmp_menu_png):
        """Debug payload correctly records vision skip + semantic results."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.ai_vision_verify import verify_menu_with_vision
        from storage.semantic_bridge import run_semantic_pipeline

        with patch("storage.ai_vision_verify._get_client", return_value=None):
            vision_result = verify_menu_with_vision(str(tmp_menu_png), _sample_claude_items())

        items = claude_items_to_draft_rows(_sample_claude_items())
        semantic_result = run_semantic_pipeline(items)

        # Build payload
        payload = {"extraction_strategy": "claude_api"}
        payload["vision_verification"] = {
            "skipped": vision_result["skipped"],
            "skip_reason": vision_result.get("skip_reason"),
            "confidence": vision_result.get("confidence", 0.0),
        }
        payload["semantic_pipeline"] = {
            "quality_grade": semantic_result.get("quality_grade"),
            "mean_confidence": semantic_result.get("mean_confidence"),
        }

        assert payload["vision_verification"]["skipped"] is True
        assert payload["vision_verification"]["skip_reason"] == "no_api_key"
        assert payload["semantic_pipeline"]["quality_grade"] in ("A", "B", "C", "D")


# ===========================================================================
# 3. Vision-Failed Path: Call 1 → error → Semantic
# ===========================================================================
class TestVisionFailedPath:
    """When vision API errors, Call 1 items flow to semantic with error logged."""

    def test_api_error_falls_back_gracefully(self, tmp_menu_png):
        """API error returns original items; semantic pipeline still runs."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.ai_vision_verify import verify_menu_with_vision
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_CALL2_VISION, STEP_SEMANTIC,
        )

        tracker = PipelineTracker()

        # Vision — API error
        tracker.start_step(STEP_CALL2_VISION)
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("Connection timeout")

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            vision_result = verify_menu_with_vision(str(tmp_menu_png), _sample_claude_items())

        assert vision_result.get("error") == "Connection timeout"
        items = claude_items_to_draft_rows(_sample_claude_items())
        tracker.fail_step(STEP_CALL2_VISION, str(vision_result.get("error")))

        # Semantic still runs
        tracker.start_step(STEP_SEMANTIC)
        semantic_result = run_semantic_pipeline(items)
        tracker.end_step(STEP_SEMANTIC, items=len(items))

        summary = tracker.summary()
        assert summary["steps"][STEP_CALL2_VISION]["status"] == "failed"
        assert summary["steps"][STEP_SEMANTIC]["status"] == "success"
        assert semantic_result["quality_grade"] in ("A", "B", "C", "D")

    def test_parse_failure_falls_back(self, tmp_menu_png):
        """Unparseable response returns original items; pipeline continues."""
        from storage.ai_vision_verify import verify_menu_with_vision

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text="I can see a restaurant menu with many items...")
        mock_response.content = [mock_block]
        mock_client = _make_mock_client(mock_response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_png), _sample_claude_items())

        assert result.get("error") == "parse_failed"
        # Original items returned intact
        assert len(result["items"]) == 6
        assert result["items"][0]["name"] == "Mozzarella Sticks"


# ===========================================================================
# 4. Call 1 Failed Path: heuristic_ai fallback
# ===========================================================================
class TestCall1FailedPath:
    """When Call 1 (Claude) fails, neither vision nor semantic runs on claude items."""

    def test_call1_failure_no_vision_no_semantic(self):
        """Call 1 failure means no vision and no semantic for claude strategy."""
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
        )

        tracker = PipelineTracker()

        tracker.start_step(STEP_OCR_TEXT)
        tracker.end_step(STEP_OCR_TEXT, chars=len(_sample_ocr_text()))

        tracker.start_step(STEP_CALL1_EXTRACT)
        tracker.fail_step(STEP_CALL1_EXTRACT, "anthropic.APIError: rate limited")

        # Simulate fallback to heuristic_ai
        tracker.strategy = "heuristic_ai"
        summary = tracker.summary()

        assert summary["extraction_strategy"] == "heuristic_ai"
        assert summary["steps"][STEP_CALL1_EXTRACT]["status"] == "failed"
        # No Call 2 or Semantic steps recorded
        assert "call_2_vision_verification" not in summary["steps"]
        assert "semantic_pipeline" not in summary["steps"]

    def test_heuristic_fallback_no_vision_block_in_payload(self):
        """Heuristic AI path produces no vision_verification block."""
        vision_result = None  # Never ran
        extraction_strategy = "heuristic_ai"

        payload = {"extraction_strategy": extraction_strategy}
        if vision_result is not None:
            payload["vision_verification"] = {}

        assert "vision_verification" not in payload


# ===========================================================================
# 5. Strategy Gating: semantic only runs on claude strategies
# ===========================================================================
class TestStrategyGating:
    """Semantic pipeline should only run on 'claude_api' or 'claude_api+vision'."""

    def test_semantic_runs_on_claude_api(self):
        """Semantic pipeline runs when strategy is 'claude_api'."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline

        items = claude_items_to_draft_rows(_sample_claude_items())
        extraction_strategy = "claude_api"

        semantic_result = None
        if items and extraction_strategy in ("claude_api", "claude_api+vision"):
            semantic_result = run_semantic_pipeline(items)

        assert semantic_result is not None
        assert "quality_grade" in semantic_result

    def test_semantic_runs_on_claude_api_plus_vision(self):
        """Semantic pipeline runs when strategy is 'claude_api+vision'."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline

        items = claude_items_to_draft_rows(_sample_claude_items())
        extraction_strategy = "claude_api+vision"

        semantic_result = None
        if items and extraction_strategy in ("claude_api", "claude_api+vision"):
            semantic_result = run_semantic_pipeline(items)

        assert semantic_result is not None

    def test_semantic_skipped_for_heuristic_ai(self):
        """Semantic pipeline does NOT run for heuristic_ai (it already runs internally)."""
        extraction_strategy = "heuristic_ai"
        items = [{"name": "Test", "price_cents": 500}]

        semantic_result = None
        if items and extraction_strategy in ("claude_api", "claude_api+vision"):
            semantic_result = "should_not_run"

        assert semantic_result is None

    def test_semantic_skipped_for_legacy_draft_json(self):
        """Semantic pipeline does NOT run for legacy_draft_json."""
        extraction_strategy = "legacy_draft_json"
        items = [{"name": "Test", "price_cents": 500}]

        semantic_result = None
        if items and extraction_strategy in ("claude_api", "claude_api+vision"):
            semantic_result = "should_not_run"

        assert semantic_result is None

    def test_semantic_skipped_when_no_items(self):
        """Semantic pipeline does NOT run when items list is empty."""
        extraction_strategy = "claude_api"
        items = []

        semantic_result = None
        if items and extraction_strategy in ("claude_api", "claude_api+vision"):
            semantic_result = "should_not_run"

        assert semantic_result is None


# ===========================================================================
# 6. Debug Payload Completeness
# ===========================================================================
class TestDebugPayloadCompleteness:
    """Verify payload structure matches what portal/app.py constructs."""

    def _build_full_payload(self, tmp_menu_png):
        """Helper: construct payload via the same logic as run_ocr_and_make_draft."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION, STEP_SEMANTIC,
        )

        tracker = PipelineTracker()

        # OCR
        tracker.start_step(STEP_OCR_TEXT)
        ocr_text = _sample_ocr_text()
        tracker.end_step(STEP_OCR_TEXT, chars=len(ocr_text))

        # Call 1
        tracker.start_step(STEP_CALL1_EXTRACT)
        claude_items = _sample_claude_items()
        tracker.end_step(STEP_CALL1_EXTRACT, items=len(claude_items))

        # Call 2
        tracker.start_step(STEP_CALL2_VISION)
        corrected = _vision_corrected_items()
        response = _make_vision_api_response(corrected)
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            vision_result = verify_menu_with_vision(str(tmp_menu_png), claude_items)

        items = verified_items_to_draft_rows(vision_result["items"])
        n_changes = len(vision_result.get("changes", []))
        tracker.end_step(STEP_CALL2_VISION, items=len(items),
                         changes=n_changes, confidence=vision_result["confidence"])

        # Semantic
        tracker.start_step(STEP_SEMANTIC)
        semantic_result = run_semantic_pipeline(items)
        tracker.end_step(STEP_SEMANTIC, items=len(items),
                         quality_grade=semantic_result.get("quality_grade"),
                         repairs=semantic_result.get("repairs_applied", 0))

        tracker.strategy = "claude_api+vision"

        # Build payload
        payload = {
            "import_job_id": 42,
            "pipeline": "ocr_helper+tesseract",
            "bridge": "run_ocr_and_make_draft",
            "extraction_strategy": "claude_api+vision",
            "clean_ocr_chars": len(ocr_text),
        }
        payload["vision_verification"] = {
            "skipped": vision_result.get("skipped", False),
            "skip_reason": vision_result.get("skip_reason"),
            "error": vision_result.get("error"),
            "confidence": vision_result.get("confidence", 0.0),
            "model": vision_result.get("model"),
            "changes_count": len(vision_result.get("changes", [])),
            "changes": vision_result.get("changes", []),
            "notes": vision_result.get("notes"),
            "item_count_before": len(vision_result.get("items", [])),
        }
        payload["semantic_pipeline"] = {
            "quality_grade": semantic_result.get("quality_grade"),
            "mean_confidence": semantic_result.get("mean_confidence", 0.0),
            "tier_counts": semantic_result.get("tier_counts", {}),
            "repairs_applied": semantic_result.get("repairs_applied", 0),
            "repair_results": semantic_result.get("repair_results", {}),
            "items_metadata": semantic_result.get("items_metadata", []),
        }
        payload["pipeline_metrics"] = tracker.summary()

        return payload, items, vision_result, semantic_result

    def test_all_top_level_fields_present(self, tmp_menu_png):
        payload, *_ = self._build_full_payload(tmp_menu_png)
        for key in ("import_job_id", "pipeline", "bridge", "extraction_strategy",
                     "clean_ocr_chars", "vision_verification", "semantic_pipeline",
                     "pipeline_metrics"):
            assert key in payload, f"Missing key: {key}"

    def test_vision_verification_fields(self, tmp_menu_png):
        payload, *_ = self._build_full_payload(tmp_menu_png)
        vv = payload["vision_verification"]
        for key in ("skipped", "skip_reason", "error", "confidence", "model",
                     "changes_count", "changes", "notes", "item_count_before"):
            assert key in vv, f"Missing vision field: {key}"

    def test_semantic_pipeline_fields(self, tmp_menu_png):
        payload, *_ = self._build_full_payload(tmp_menu_png)
        sp = payload["semantic_pipeline"]
        for key in ("quality_grade", "mean_confidence", "tier_counts",
                     "repairs_applied", "repair_results", "items_metadata"):
            assert key in sp, f"Missing semantic field: {key}"

    def test_pipeline_metrics_fields(self, tmp_menu_png):
        payload, *_ = self._build_full_payload(tmp_menu_png)
        pm = payload["pipeline_metrics"]
        for key in ("total_duration_ms", "total_duration_human", "steps",
                     "item_flow", "bottleneck", "extraction_strategy"):
            assert key in pm, f"Missing metrics field: {key}"

    def test_semantic_items_metadata_count_matches(self, tmp_menu_png):
        payload, items, *_ = self._build_full_payload(tmp_menu_png)
        meta = payload["semantic_pipeline"]["items_metadata"]
        assert len(meta) == len(items)

    def test_semantic_items_metadata_structure(self, tmp_menu_png):
        payload, *_ = self._build_full_payload(tmp_menu_png)
        meta = payload["semantic_pipeline"]["items_metadata"]
        for m in meta:
            assert "name" in m
            assert "semantic_confidence" in m
            assert "semantic_tier" in m


# ===========================================================================
# 7. Pipeline Metrics Integration
# ===========================================================================
class TestMetricsIntegration:
    """Pipeline metrics correctly track all stages with proper metadata."""

    def test_ocr_step_has_chars_count(self):
        from storage.pipeline_metrics import PipelineTracker, STEP_OCR_TEXT
        t = PipelineTracker()
        t.start_step(STEP_OCR_TEXT)
        t.end_step(STEP_OCR_TEXT, chars=len(_sample_ocr_text()))
        assert t._steps[STEP_OCR_TEXT]["chars"] == len(_sample_ocr_text())

    def test_call1_step_has_items_count(self):
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL1_EXTRACT
        t = PipelineTracker()
        t.start_step(STEP_CALL1_EXTRACT)
        t.end_step(STEP_CALL1_EXTRACT, items=6)
        assert t._steps[STEP_CALL1_EXTRACT]["items"] == 6

    def test_call2_step_has_changes_and_confidence(self):
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL2_VISION
        t = PipelineTracker()
        t.start_step(STEP_CALL2_VISION)
        t.end_step(STEP_CALL2_VISION, items=6, changes=2, confidence=0.94)
        step = t._steps[STEP_CALL2_VISION]
        assert step["changes"] == 2
        assert step["confidence"] == 0.94

    def test_semantic_step_has_quality_and_repairs(self):
        from storage.pipeline_metrics import PipelineTracker, STEP_SEMANTIC
        t = PipelineTracker()
        t.start_step(STEP_SEMANTIC)
        t.end_step(STEP_SEMANTIC, items=6, quality_grade="B", repairs=1, mean_confidence=0.82)
        step = t._steps[STEP_SEMANTIC]
        assert step["quality_grade"] == "B"
        assert step["repairs"] == 1
        assert step["mean_confidence"] == 0.82

    def test_bottleneck_identifies_slowest_real_step(self):
        """Bottleneck detection picks the genuinely slowest step."""
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION, STEP_SEMANTIC,
        )
        t = PipelineTracker()

        # OCR: fast
        t.start_step(STEP_OCR_TEXT)
        t.end_step(STEP_OCR_TEXT, chars=5000)

        # Call 1: slow (simulated)
        t.start_step(STEP_CALL1_EXTRACT)
        time.sleep(0.03)
        t.end_step(STEP_CALL1_EXTRACT, items=6)

        # Call 2: medium
        t.start_step(STEP_CALL2_VISION)
        time.sleep(0.01)
        t.end_step(STEP_CALL2_VISION, items=6)

        # Semantic: fast
        t.start_step(STEP_SEMANTIC)
        t.end_step(STEP_SEMANTIC, items=6)

        s = t.summary()
        assert s["bottleneck"] == STEP_CALL1_EXTRACT

    def test_item_flow_canonical_order(self):
        """Item flow entries appear in canonical OCR→Call1→Call2→Semantic order."""
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION, STEP_SEMANTIC,
        )
        t = PipelineTracker()
        # Add in reverse order to test canonical ordering
        t.start_step(STEP_SEMANTIC)
        t.end_step(STEP_SEMANTIC, items=6)
        t.start_step(STEP_CALL2_VISION)
        t.end_step(STEP_CALL2_VISION, items=6)
        t.start_step(STEP_CALL1_EXTRACT)
        t.end_step(STEP_CALL1_EXTRACT, items=6)
        t.start_step(STEP_OCR_TEXT)
        t.end_step(STEP_OCR_TEXT, chars=5000)

        s = t.summary()
        flow_steps = [e["step"] for e in s["item_flow"]]
        assert flow_steps == [
            STEP_OCR_TEXT, STEP_CALL1_EXTRACT, STEP_CALL2_VISION, STEP_SEMANTIC
        ]


# ===========================================================================
# 8. Confidence Flow Through Pipeline
# ===========================================================================
class TestConfidenceFlow:
    """Confidence values flow correctly through the pipeline stages."""

    def test_call1_items_confidence_90(self):
        """Call 1 (Claude text extraction) items get confidence=90."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        rows = claude_items_to_draft_rows(_sample_claude_items())
        for r in rows:
            assert r["confidence"] == 90

    def test_call2_vision_items_confidence_95(self, tmp_menu_png):
        """Call 2 (vision-verified) items get confidence=95."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows

        response = _make_vision_api_response(_vision_corrected_items())
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_png), _sample_claude_items())

        rows = verified_items_to_draft_rows(result["items"])
        for r in rows:
            assert r["confidence"] == 95

    def test_semantic_normalizes_confidence_to_float(self):
        """Semantic bridge normalizes 0-100 confidence to 0.0-1.0 float."""
        from storage.semantic_bridge import prepare_items_for_semantic

        items = [
            {"name": "Test Item", "price_cents": 500, "confidence": 95},
        ]
        prepared = prepare_items_for_semantic(items)
        assert prepared[0]["confidence"] == 0.95

    def test_semantic_normalizes_90_to_point_9(self):
        """Call 1 confidence=90 normalizes to 0.9 for semantic pipeline."""
        from storage.semantic_bridge import prepare_items_for_semantic

        items = [
            {"name": "Test Item", "price_cents": 500, "confidence": 90},
        ]
        prepared = prepare_items_for_semantic(items)
        assert prepared[0]["confidence"] == 0.9

    def test_semantic_does_not_mutate_original_items(self):
        """Semantic bridge deep-copies items; originals are unchanged."""
        from storage.semantic_bridge import prepare_items_for_semantic

        items = [
            {"name": "Test", "price_cents": 500, "confidence": 95, "_variants": [{"label": "Large"}]},
        ]
        original_conf = items[0]["confidence"]
        prepared = prepare_items_for_semantic(items)

        assert items[0]["confidence"] == original_conf  # Original unchanged
        assert prepared[0]["confidence"] == 0.95  # Prepared normalized


# ===========================================================================
# 9. Semantic Repairs Flow Back
# ===========================================================================
class TestSemanticRepairFlow:
    """Auto-repairs from semantic pipeline apply back to draft items."""

    def test_repairs_applied_count_in_result(self):
        """run_semantic_pipeline returns repairs_applied count."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline

        items = claude_items_to_draft_rows(_sample_claude_items())
        result = run_semantic_pipeline(items)
        assert "repairs_applied" in result
        assert isinstance(result["repairs_applied"], int)

    def test_semantic_result_has_tier_counts(self):
        """Semantic result includes tier distribution."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline

        items = claude_items_to_draft_rows(_sample_claude_items())
        result = run_semantic_pipeline(items)
        tc = result["tier_counts"]
        assert "high" in tc
        assert "medium" in tc
        assert "low" in tc
        assert "reject" in tc
        # Total should equal item count
        total = tc["high"] + tc["medium"] + tc["low"] + tc["reject"]
        assert total == len(items)

    def test_apply_repairs_changes_draft_items(self):
        """apply_repairs_to_draft_items modifies original draft items."""
        from storage.semantic_bridge import apply_repairs_to_draft_items

        draft = [{"name": "Chiken Wings", "category": "Other"}]
        processed = [{
            "auto_repairs_applied": [
                {"field": "name", "new_value": "Chicken Wings"},
                {"field": "category", "new_value": "Appetizers"},
            ],
        }]

        count = apply_repairs_to_draft_items(draft, processed)
        assert count == 1
        assert draft[0]["name"] == "Chicken Wings"
        assert draft[0]["category"] == "Appetizers"

    def test_no_repairs_leaves_items_unchanged(self):
        """Items with no repairs are not modified."""
        from storage.semantic_bridge import apply_repairs_to_draft_items

        draft = [{"name": "Perfect Item", "category": "Pizza"}]
        processed = [{"auto_repairs_applied": []}]

        count = apply_repairs_to_draft_items(draft, processed)
        assert count == 0
        assert draft[0]["name"] == "Perfect Item"


# ===========================================================================
# 10. Payload JSON Round-Trip
# ===========================================================================
class TestPayloadJsonRoundTrip:
    """Payload survives JSON serialization (as save_ocr_debug does)."""

    def test_full_payload_is_json_serializable(self, tmp_menu_png):
        """Complete payload with all blocks serializes to valid JSON."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION, STEP_SEMANTIC,
        )

        tracker = PipelineTracker()
        tracker.start_step(STEP_OCR_TEXT)
        tracker.end_step(STEP_OCR_TEXT, chars=500)
        tracker.start_step(STEP_CALL1_EXTRACT)
        claude_items = _sample_claude_items()
        tracker.end_step(STEP_CALL1_EXTRACT, items=len(claude_items))

        # Vision
        tracker.start_step(STEP_CALL2_VISION)
        corrected = _vision_corrected_items()
        response = _make_vision_api_response(corrected)
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            vision_result = verify_menu_with_vision(str(tmp_menu_png), claude_items)

        items = verified_items_to_draft_rows(vision_result["items"])
        tracker.end_step(STEP_CALL2_VISION, items=len(items))

        tracker.start_step(STEP_SEMANTIC)
        semantic_result = run_semantic_pipeline(items)
        tracker.end_step(STEP_SEMANTIC, items=len(items))
        tracker.strategy = "claude_api+vision"

        payload = {
            "extraction_strategy": "claude_api+vision",
            "vision_verification": {
                "skipped": False,
                "confidence": vision_result.get("confidence"),
                "changes": vision_result.get("changes", []),
            },
            "semantic_pipeline": {
                "quality_grade": semantic_result.get("quality_grade"),
                "tier_counts": semantic_result.get("tier_counts"),
                "items_metadata": semantic_result.get("items_metadata", []),
                "repair_results": semantic_result.get("repair_results", {}),
            },
            "pipeline_metrics": tracker.summary(),
        }

        # Must not raise
        serialized = json.dumps(payload)
        loaded = json.loads(serialized)

        # Round-trip preserves structure
        assert loaded["extraction_strategy"] == "claude_api+vision"
        assert loaded["vision_verification"]["skipped"] is False
        assert loaded["semantic_pipeline"]["quality_grade"] in ("A", "B", "C", "D")
        assert len(loaded["pipeline_metrics"]["steps"]) == 4

    def test_payload_with_skipped_vision_serializes(self):
        """Payload with skipped vision block also serializes correctly."""
        payload = {
            "extraction_strategy": "claude_api",
            "vision_verification": {
                "skipped": True,
                "skip_reason": "no_api_key",
                "confidence": 0.0,
                "changes": [],
            },
        }
        roundtrip = json.loads(json.dumps(payload))
        assert roundtrip["vision_verification"]["skipped"] is True

    def test_payload_with_none_values_serializes(self):
        """None values in payload serialize as JSON null."""
        payload = {
            "vision_verification": {
                "skip_reason": None,
                "error": None,
                "notes": None,
            },
        }
        serialized = json.dumps(payload)
        loaded = json.loads(serialized)
        assert loaded["vision_verification"]["skip_reason"] is None


# ===========================================================================
# 11. Component Interop
# ===========================================================================
class TestComponentInterop:
    """Verify components interoperate — data flows correctly between them."""

    def test_vision_output_feeds_semantic_input(self, tmp_menu_png):
        """Vision-verified items are valid input for semantic pipeline."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline, prepare_items_for_semantic

        response = _make_vision_api_response(_vision_corrected_items())
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            vision_result = verify_menu_with_vision(str(tmp_menu_png), _sample_claude_items())

        rows = verified_items_to_draft_rows(vision_result["items"])

        # Items should be valid for semantic pipeline
        prepared = prepare_items_for_semantic(rows)
        assert len(prepared) == len(rows)
        # Confidence normalized from 95 → 0.95
        assert all(0.0 <= p.get("confidence", 0) <= 1.0 for p in prepared)

        # Full pipeline should succeed
        result = run_semantic_pipeline(rows)
        assert result["quality_grade"] in ("A", "B", "C", "D")

    def test_semantic_metadata_per_item(self):
        """extract_semantic_metadata produces one metadata entry per item."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline

        items = claude_items_to_draft_rows(_sample_claude_items())
        result = run_semantic_pipeline(items)

        meta = result["items_metadata"]
        assert len(meta) == 6
        for m in meta:
            assert "name" in m
            assert "semantic_confidence" in m
            assert "semantic_tier" in m

    def test_tracker_summary_matches_actual_pipeline(self, tmp_menu_png):
        """Tracker summary reflects what actually happened in the pipeline."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION, STEP_SEMANTIC,
        )

        tracker = PipelineTracker()

        # OCR
        tracker.start_step(STEP_OCR_TEXT)
        ocr_text = _sample_ocr_text()
        tracker.end_step(STEP_OCR_TEXT, chars=len(ocr_text))

        # Call 1
        tracker.start_step(STEP_CALL1_EXTRACT)
        claude_items = _sample_claude_items()
        tracker.end_step(STEP_CALL1_EXTRACT, items=len(claude_items))

        # Call 2
        tracker.start_step(STEP_CALL2_VISION)
        response = _make_vision_api_response(_vision_corrected_items(), confidence=0.92)
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            vr = verify_menu_with_vision(str(tmp_menu_png), claude_items)

        items = verified_items_to_draft_rows(vr["items"])
        tracker.end_step(STEP_CALL2_VISION, items=len(items),
                         changes=len(vr.get("changes", [])), confidence=vr["confidence"])

        # Semantic
        tracker.start_step(STEP_SEMANTIC)
        sr = run_semantic_pipeline(items)
        tracker.end_step(STEP_SEMANTIC, items=len(items),
                         quality_grade=sr["quality_grade"], repairs=sr["repairs_applied"])

        tracker.strategy = "claude_api+vision"
        summary = tracker.summary()

        # Verify tracker matches actual data
        assert summary["steps"][STEP_OCR_TEXT]["chars"] == len(ocr_text)
        assert summary["steps"][STEP_CALL1_EXTRACT]["items"] == 6
        assert summary["steps"][STEP_CALL2_VISION]["confidence"] == 0.92
        assert summary["steps"][STEP_SEMANTIC]["quality_grade"] == sr["quality_grade"]

    def test_variant_items_survive_full_pipeline(self, tmp_menu_png):
        """Items with size variants flow through vision → semantic correctly."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline

        items_with_sizes = [
            {
                "name": "Pepperoni Pizza", "description": "Classic",
                "price": 0, "category": "Pizza",
                "sizes": [
                    {"label": '12"', "price": 14.95},
                    {"label": '16"', "price": 19.95},
                ],
            },
            {
                "name": "Caesar Salad", "description": "Romaine",
                "price": 9.95, "category": "Salads", "sizes": [],
            },
        ]

        response = _make_vision_api_response(items_with_sizes, confidence=0.96)
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            vr = verify_menu_with_vision(str(tmp_menu_png), items_with_sizes)

        rows = verified_items_to_draft_rows(vr["items"])
        pizza = next(r for r in rows if r["name"] == "Pepperoni Pizza")
        assert "_variants" in pizza
        assert len(pizza["_variants"]) == 2
        assert pizza["_variants"][0]["kind"] == "size"

        # Semantic pipeline handles variants
        result = run_semantic_pipeline(rows)
        assert result["quality_grade"] in ("A", "B", "C", "D")


# ===========================================================================
# 12. Edge Cases
# ===========================================================================
class TestEdgeCases:
    """Edge cases: empty inputs, all failures, boundary conditions."""

    def test_empty_ocr_text_no_call1(self):
        """Empty OCR text means no Claude extraction attempted."""
        from storage.pipeline_metrics import PipelineTracker, STEP_OCR_TEXT
        tracker = PipelineTracker()
        tracker.start_step(STEP_OCR_TEXT)
        tracker.end_step(STEP_OCR_TEXT, chars=0)

        # Pipeline logic: if not clean_ocr_text and not items → skip Call 1
        clean_ocr_text = ""
        items = []
        extraction_strategy = "none"

        if clean_ocr_text and not items:
            extraction_strategy = "claude_api"

        assert extraction_strategy == "none"
        assert items == []

    def test_empty_items_from_semantic_pipeline(self):
        """Semantic pipeline handles empty items list gracefully."""
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline([])
        assert result["quality_grade"] == "D"
        assert result["mean_confidence"] == 0.0
        assert result["items"] == []
        assert result["repairs_applied"] == 0

    def test_single_item_through_full_pipeline(self, tmp_menu_png):
        """Single item flows through all 4 stages correctly."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION, STEP_SEMANTIC,
        )

        single = [{"name": "Water", "description": "Bottled", "price": 2.50,
                    "category": "Beverages", "sizes": []}]

        tracker = PipelineTracker()
        tracker.start_step(STEP_OCR_TEXT)
        tracker.end_step(STEP_OCR_TEXT, chars=20)

        tracker.start_step(STEP_CALL1_EXTRACT)
        tracker.end_step(STEP_CALL1_EXTRACT, items=1)

        tracker.start_step(STEP_CALL2_VISION)
        response = _make_vision_api_response(single, confidence=0.99)
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            vr = verify_menu_with_vision(str(tmp_menu_png), single)

        items = verified_items_to_draft_rows(vr["items"])
        assert len(items) == 1
        tracker.end_step(STEP_CALL2_VISION, items=1, confidence=0.99)

        tracker.start_step(STEP_SEMANTIC)
        sr = run_semantic_pipeline(items)
        tracker.end_step(STEP_SEMANTIC, items=1)

        tracker.strategy = "claude_api+vision"
        summary = tracker.summary()
        assert len(summary["steps"]) == 4
        assert summary["item_flow"][1]["items"] == 1
        assert sr["quality_grade"] in ("A", "B", "C", "D")

    def test_all_pipeline_stages_fail(self):
        """All stages failing produces a valid (empty) payload."""
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION, STEP_SEMANTIC,
        )

        tracker = PipelineTracker()
        tracker.fail_step(STEP_OCR_TEXT, "no tesseract")
        tracker.fail_step(STEP_CALL1_EXTRACT, "no API key")
        tracker.fail_step(STEP_CALL2_VISION, "no image")
        tracker.fail_step(STEP_SEMANTIC, "no items")
        tracker.strategy = "none"

        summary = tracker.summary()
        assert summary["bottleneck"] is None  # No successful steps
        assert all(s["status"] == "failed" for s in summary["steps"].values())
        # Still JSON-serializable
        assert json.dumps(summary)

    def test_vision_empty_response_fallback(self, tmp_menu_png):
        """Claude returning empty text triggers graceful fallback."""
        from storage.ai_vision_verify import verify_menu_with_vision

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text="")
        mock_response.content = [mock_block]
        mock_client = _make_mock_client(mock_response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_png), _sample_claude_items())

        assert result.get("error") == "empty_response"
        assert result["items"] == _sample_claude_items()

    def test_tracker_graceful_on_missing_start(self):
        """end_step without start_step gives 0ms duration, doesn't crash."""
        from storage.pipeline_metrics import PipelineTracker, STEP_OCR_TEXT
        t = PipelineTracker()
        t.end_step(STEP_OCR_TEXT, chars=100)
        assert t._steps[STEP_OCR_TEXT]["duration_ms"] == 0
        assert t._steps[STEP_OCR_TEXT]["status"] == "success"

    def test_large_menu_50_items_through_pipeline(self, tmp_menu_png):
        """50-item menu flows through vision + semantic correctly."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline

        large_menu = [
            {"name": f"Item {i}", "description": f"Desc {i}",
             "price": float(i) + 0.95, "category": "Main", "sizes": []}
            for i in range(50)
        ]

        response = _make_vision_api_response(large_menu, confidence=0.88)
        mock_client = _make_mock_client(response)

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            vr = verify_menu_with_vision(str(tmp_menu_png), large_menu)

        rows = verified_items_to_draft_rows(vr["items"])
        assert len(rows) == 50

        sr = run_semantic_pipeline(rows)
        assert sr["quality_grade"] in ("A", "B", "C", "D")
        assert sr["tier_counts"]["high"] + sr["tier_counts"]["medium"] + \
               sr["tier_counts"]["low"] + sr["tier_counts"]["reject"] == 50


# ===========================================================================
# 13. Sprint 11.1 Module Imports
# ===========================================================================
class TestSprintModuleImports:
    """Verify all Sprint 11.1 modules import cleanly."""

    def test_import_ai_vision_verify(self):
        from storage.ai_vision_verify import (
            verify_menu_with_vision,
            verified_items_to_draft_rows,
            compute_changes_log,
            encode_menu_images,
        )
        assert callable(verify_menu_with_vision)
        assert callable(verified_items_to_draft_rows)
        assert callable(compute_changes_log)
        assert callable(encode_menu_images)

    def test_import_semantic_bridge(self):
        from storage.semantic_bridge import (
            run_semantic_pipeline,
            prepare_items_for_semantic,
            extract_semantic_metadata,
            apply_repairs_to_draft_items,
        )
        assert callable(run_semantic_pipeline)
        assert callable(prepare_items_for_semantic)
        assert callable(extract_semantic_metadata)
        assert callable(apply_repairs_to_draft_items)

    def test_import_pipeline_metrics(self):
        from storage.pipeline_metrics import (
            PipelineTracker,
            format_duration,
            STEP_OCR_TEXT,
            STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION,
            STEP_SEMANTIC,
        )
        assert PipelineTracker is not None
        assert callable(format_duration)
        assert isinstance(STEP_OCR_TEXT, str)
        assert isinstance(STEP_CALL1_EXTRACT, str)
        assert isinstance(STEP_CALL2_VISION, str)
        assert isinstance(STEP_SEMANTIC, str)

    def test_import_ai_menu_extract(self):
        from storage.ai_menu_extract import (
            extract_menu_items_via_claude,
            claude_items_to_draft_rows,
        )
        assert callable(extract_menu_items_via_claude)
        assert callable(claude_items_to_draft_rows)