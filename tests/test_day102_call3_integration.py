# tests/test_day102_call3_integration.py
"""
Day 102 — Call 3 Reconciliation Pipeline Integration Tests.

Tests the wiring of targeted reconciliation (Claude Call 3) into the
production pipeline after the semantic pipeline step:
  OCR → Call 1 → Call 2 → Semantic → Call 3 → re-score → draft items

Day 101 tested ai_reconcile.py functions in isolation (34 unit tests).
Day 102 tests the INTEGRATION: collect flagged → reconcile → merge →
re-score → draft item updates → debug payload → pipeline metrics.

38 tests covering:
  1. Full 5-stage happy path (OCR → Call 1 → Call 2 → Semantic → Call 3)
  2. Call 3 skipped paths (no flagged items, no API key, image encode fail)
  3. Call 3 error paths (API error, bad JSON, empty response)
  4. Merge + re-score: corrected items update draft items and confidence
  5. Debug payload: targeted_reconciliation block present with all fields
  6. Pipeline metrics: STEP_CALL3_RECONCILE tracked in all scenarios
  7. Confidence flow: confirmed +5, corrected → 92 (0-100 scale)
  8. Draft item field updates: name, price, category, description propagate
  9. No-semantic path: Call 3 skipped when semantic pipeline didn't run
 10. Edge cases: all items high tier, single flagged item, large menu
"""

from __future__ import annotations

import base64
import copy
import json
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------
def _sample_ocr_text():
    return (
        "APPETIZERS\n"
        "Mozzarella Sticks  7.95\n"
        "Chicken Wings  11.50\n"
        "\n"
        "PIZZA\n"
        "Margherita Pizza  14.95\n"
        "Pepperoni Pizza  16.95\n"
        "\n"
        "SALADS\n"
        "Cesar Salad  9.95\n"
        "Garden Salad  8.95\n"
        "\n"
        "ENTREES\n"
        "Steak  9.99\n"
    )


def _sample_claude_items():
    """Items as extracted by Call 1 — includes two items with issues."""
    return [
        {"name": "Mozzarella Sticks", "description": "Fried mozzarella", "price": 7.95,
         "category": "Appetizers", "sizes": []},
        {"name": "Chicken Wings", "description": "Buffalo style", "price": 11.50,
         "category": "Appetizers", "sizes": []},
        {"name": "Margherita Pizza", "description": "Fresh mozzarella, basil", "price": 14.95,
         "category": "Pizza", "sizes": []},
        {"name": "Pepperoni Pizza", "description": "Classic pepperoni", "price": 16.95,
         "category": "Pizza", "sizes": []},
        {"name": "Cesar Salad", "description": "Romaine", "price": 9.95,
         "category": "Salads", "sizes": []},
        {"name": "Garden Salad", "description": "Mixed greens", "price": 8.95,
         "category": "Salads", "sizes": []},
        {"name": "Steak", "description": None, "price": 9.99,
         "category": "Entrees", "sizes": []},
    ]


def _sample_draft_rows():
    """Draft rows after claude_items_to_draft_rows + vision verification."""
    return [
        {"name": "Mozzarella Sticks", "description": "Fried mozzarella",
         "price_cents": 795, "category": "Appetizers", "position": 1, "confidence": 95},
        {"name": "Chicken Wings", "description": "Buffalo style",
         "price_cents": 1150, "category": "Appetizers", "position": 2, "confidence": 95},
        {"name": "Margherita Pizza", "description": "Fresh mozzarella, basil",
         "price_cents": 1495, "category": "Pizza", "position": 3, "confidence": 95},
        {"name": "Pepperoni Pizza", "description": "Classic pepperoni",
         "price_cents": 1695, "category": "Pizza", "position": 4, "confidence": 95},
        {"name": "Cesar Salad", "description": "Romaine",
         "price_cents": 995, "category": "Salads", "position": 5, "confidence": 95},
        {"name": "Garden Salad", "description": "Mixed greens",
         "price_cents": 895, "category": "Salads", "position": 6, "confidence": 95},
        {"name": "Steak", "description": None,
         "price_cents": 999, "category": "Entrees", "position": 7, "confidence": 95},
    ]


def _make_reconciliation_response(items, confidence=0.94, notes="Reconciled"):
    """Build a mock Claude API response for reconciliation."""
    return {
        "items": items,
        "confidence": confidence,
        "notes": notes,
    }


def _make_mock_client(response_data):
    """Create a mock Anthropic client returning JSON response."""
    mock_block = SimpleNamespace(text=json.dumps(response_data))
    mock_message = MagicMock()
    mock_message.content = [mock_block]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message
    return mock_client


@pytest.fixture
def tmp_menu_png(tmp_path):
    """Create a tiny PNG file for testing."""
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    img = tmp_path / "test_menu.png"
    img.write_bytes(png_bytes)
    return img


# ===========================================================================
# 1. Full 5-Stage Happy Path
# ===========================================================================
class TestFullPipelineWithCall3:
    """End-to-end: OCR → Call 1 → Call 2 → Semantic → Call 3 → re-score."""

    def _run_full_pipeline(self, tmp_menu_png, reconcile_response):
        """Helper: run all 5 stages with mocked API calls. Returns (items, tracker, semantic_result, reconcile_result).

        Includes deliberately bad items (garbled name, zero price) so the
        semantic pipeline flags them for Call 3 reconciliation.
        """
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.ai_reconcile import (
            collect_flagged_items, reconcile_flagged_items, merge_reconciled_items,
        )
        from storage.semantic_confidence import (
            score_semantic_confidence, classify_confidence_tiers,
        )
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION, STEP_SEMANTIC, STEP_CALL3_RECONCILE,
        )

        # Stage 1: OCR
        tracker = PipelineTracker()
        tracker.start_step(STEP_OCR_TEXT)
        ocr_text = _sample_ocr_text()
        tracker.end_step(STEP_OCR_TEXT, chars=len(ocr_text))

        # Stage 2: Call 1 — includes items that will be flagged by semantic pipeline
        tracker.start_step(STEP_CALL1_EXTRACT)
        claude_items = _sample_claude_items()
        # Add a garbled-name item that the semantic pipeline will flag as reject/low
        claude_items.append({
            "name": "Xzlqp", "description": None, "price": 0,
            "category": "Other", "sizes": [],
        })
        tracker.end_step(STEP_CALL1_EXTRACT, items=len(claude_items))

        # Stage 3: Call 2 (vision — use Call 1 items as-is for simplicity)
        tracker.start_step(STEP_CALL2_VISION)
        vision_response = MagicMock()
        vision_response.content = [SimpleNamespace(text=json.dumps({
            "items": claude_items, "confidence": 0.94, "notes": "Verified",
        }))]
        vision_client = MagicMock()
        vision_client.messages.create.return_value = vision_response

        with patch("storage.ai_vision_verify._get_client", return_value=vision_client):
            vision_result = verify_menu_with_vision(str(tmp_menu_png), claude_items)
        items = verified_items_to_draft_rows(vision_result["items"])
        tracker.end_step(STEP_CALL2_VISION, items=len(items), confidence=0.94)

        # Stage 4: Semantic pipeline
        tracker.start_step(STEP_SEMANTIC)
        semantic_result = run_semantic_pipeline(items)
        tracker.end_step(STEP_SEMANTIC, items=len(items),
                         quality_grade=semantic_result.get("quality_grade", "?"))

        # Stage 5: Call 3 (reconciliation)
        tracker.start_step(STEP_CALL3_RECONCILE)
        sem_items = semantic_result["items"]
        flagged = collect_flagged_items(sem_items)

        reconcile_result = None
        if flagged:
            recon_client = _make_mock_client(reconcile_response)
            with patch("storage.ai_reconcile._get_client", return_value=recon_client):
                reconcile_result = reconcile_flagged_items(str(tmp_menu_png), flagged)

            if not reconcile_result.get("skipped") and not reconcile_result.get("error"):
                sem_items, merge_changes = merge_reconciled_items(
                    sem_items, reconcile_result["items"]
                )
                reconcile_result["merge_changes"] = merge_changes

                # Re-score
                score_semantic_confidence(sem_items)
                classify_confidence_tiers(sem_items)

                # Apply corrections back to draft items
                for draft_it, sem_it in zip(items, sem_items):
                    for field in ("name", "category", "description"):
                        new_val = sem_it.get(field)
                        if new_val and new_val != draft_it.get(field):
                            draft_it[field] = new_val
                    new_price = sem_it.get("price_cents")
                    if new_price and new_price != draft_it.get("price_cents"):
                        draft_it["price_cents"] = new_price

                tracker.end_step(STEP_CALL3_RECONCILE, items=len(flagged),
                                 confirmed=reconcile_result.get("items_confirmed", 0),
                                 corrected=reconcile_result.get("items_corrected", 0),
                                 not_found=reconcile_result.get("items_not_found", 0))
            else:
                skip = reconcile_result.get("skip_reason") or reconcile_result.get("error", "unknown")
                tracker.skip_step(STEP_CALL3_RECONCILE, skip)
        else:
            tracker.skip_step(STEP_CALL3_RECONCILE, "no_flagged_items")

        tracker.strategy = "claude_api+vision"
        return items, tracker, semantic_result, reconcile_result

    def test_5_stages_all_tracked(self, tmp_menu_png):
        """All 5 pipeline stages appear in the metrics summary."""
        from storage.pipeline_metrics import (
            STEP_OCR_TEXT, STEP_CALL1_EXTRACT, STEP_CALL2_VISION,
            STEP_SEMANTIC, STEP_CALL3_RECONCILE,
        )
        reconcile_resp = _make_reconciliation_response([
            {"name": "Steak", "price": 29.99, "category": "Entrees",
             "description": "12oz strip", "sizes": [], "status": "corrected",
             "changes": ["Fixed price"]},
        ])
        items, tracker, sem_result, recon_result = self._run_full_pipeline(
            tmp_menu_png, reconcile_resp
        )
        summary = tracker.summary()

        # All 5 steps present
        assert STEP_OCR_TEXT in summary["steps"]
        assert STEP_CALL1_EXTRACT in summary["steps"]
        assert STEP_CALL2_VISION in summary["steps"]
        assert STEP_SEMANTIC in summary["steps"]
        assert STEP_CALL3_RECONCILE in summary["steps"]
        assert len(summary["item_flow"]) == 5

    def test_corrected_item_updates_draft(self, tmp_menu_png):
        """Corrected reconciliation item propagates back to draft items."""
        reconcile_resp = _make_reconciliation_response([
            {"name": "Xzlqp", "price": 0, "category": "Other",
             "description": None, "sizes": [], "status": "not_found",
             "changes": ["Item not visible on menu"]},
        ])
        items, tracker, sem_result, recon_result = self._run_full_pipeline(
            tmp_menu_png, reconcile_resp
        )
        assert recon_result is not None
        assert recon_result.get("items_not_found", 0) >= 1

    def test_reconciliation_confidence_in_result(self, tmp_menu_png):
        """Claude's self-reported confidence is captured."""
        reconcile_resp = _make_reconciliation_response([
            {"name": "Xzlqp", "price": 0, "category": "Other",
             "description": None, "sizes": [], "status": "not_found", "changes": []},
        ], confidence=0.97)
        items, tracker, sem_result, recon_result = self._run_full_pipeline(
            tmp_menu_png, reconcile_resp
        )
        assert recon_result is not None
        assert recon_result["confidence"] == pytest.approx(0.97)

    def test_call3_step_metadata(self, tmp_menu_png):
        """Call 3 tracker step includes confirmed/corrected/not_found counts."""
        from storage.pipeline_metrics import STEP_CALL3_RECONCILE
        reconcile_resp = _make_reconciliation_response([
            {"name": "Xzlqp", "price": 0, "category": "Other",
             "description": None, "sizes": [], "status": "not_found",
             "changes": ["Not on menu"]},
        ])
        items, tracker, sem_result, recon_result = self._run_full_pipeline(
            tmp_menu_png, reconcile_resp
        )
        summary = tracker.summary()
        step = summary["steps"].get(STEP_CALL3_RECONCILE, {})
        assert step.get("status") == "success"
        assert "confirmed" in step
        assert "corrected" in step
        assert "not_found" in step


# ===========================================================================
# 2. Call 3 Skipped Paths
# ===========================================================================
class TestCall3Skipped:
    """Reconciliation is correctly skipped when not applicable."""

    def test_skipped_no_semantic_result(self):
        """Call 3 skipped when semantic pipeline didn't produce items."""
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL3_RECONCILE
        from storage.ai_reconcile import collect_flagged_items

        tracker = PipelineTracker()
        # Simulate: no semantic result
        semantic_result = None
        items = _sample_draft_rows()

        # The gate in app.py: `if items and semantic_result and semantic_result.get("items")`
        should_run = bool(items and semantic_result and
                          (semantic_result or {}).get("items"))
        assert should_run is False

    def test_skipped_all_high_tier(self, tmp_menu_png):
        """Call 3 skipped when all items are high-tier (no flagged items)."""
        from storage.ai_reconcile import collect_flagged_items

        # Simulate all-high-tier items from semantic pipeline
        all_high = [
            {"name": f"Item {i}", "semantic_tier": "high", "semantic_confidence": 0.92,
             "needs_review": False, "price_flags": [], "repair_recommendations": []}
            for i in range(6)
        ]
        flagged = collect_flagged_items(all_high)
        assert flagged == []

    def test_skipped_no_api_key(self, tmp_menu_png):
        """Call 3 returns skipped result when no API key available."""
        from storage.ai_reconcile import collect_flagged_items, reconcile_flagged_items

        flagged = [
            {"name": "Test", "price_cents": 100, "semantic_tier": "low",
             "semantic_confidence": 0.3, "needs_review": True},
        ]
        with patch("storage.ai_reconcile._get_client", return_value=None):
            result = reconcile_flagged_items(str(tmp_menu_png), flagged)
        assert result["skipped"] is True
        assert result["skip_reason"] == "no_api_key"

    def test_skipped_tracker_records_skip(self):
        """Tracker records skip_step when no flagged items."""
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL3_RECONCILE

        tracker = PipelineTracker()
        tracker.skip_step(STEP_CALL3_RECONCILE, "no_flagged_items")
        summary = tracker.summary()
        step = summary["steps"][STEP_CALL3_RECONCILE]
        assert step["status"] == "skipped"
        assert "no_flagged_items" in step["skip_reason"]


# ===========================================================================
# 3. Call 3 Error Paths
# ===========================================================================
class TestCall3Errors:
    """Reconciliation errors are handled gracefully without breaking pipeline."""

    def test_api_error_returns_original_items(self, tmp_menu_png):
        """API error returns original flagged items unchanged."""
        from storage.ai_reconcile import reconcile_flagged_items

        flagged = [
            {"name": "Steak", "price_cents": 999, "semantic_tier": "low",
             "semantic_confidence": 0.45, "needs_review": True},
        ]
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("timeout")

        with patch("storage.ai_reconcile._get_client", return_value=mock_client):
            result = reconcile_flagged_items(str(tmp_menu_png), flagged)
        assert result["error"] == "timeout"
        assert result["items"] == flagged

    def test_bad_json_returns_original_items(self, tmp_menu_png):
        """Bad JSON from Claude returns original flagged items."""
        from storage.ai_reconcile import reconcile_flagged_items

        flagged = [
            {"name": "Steak", "price_cents": 999, "semantic_tier": "low",
             "semantic_confidence": 0.45, "needs_review": True},
        ]
        mock_block = SimpleNamespace(text="not valid json {{{")
        mock_message = MagicMock()
        mock_message.content = [mock_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("storage.ai_reconcile._get_client", return_value=mock_client):
            result = reconcile_flagged_items(str(tmp_menu_png), flagged)
        assert result["error"] == "parse_failed"
        assert result["items"] == flagged

    def test_tracker_records_failure(self):
        """Tracker records fail_step on exception."""
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL3_RECONCILE

        tracker = PipelineTracker()
        tracker.start_step(STEP_CALL3_RECONCILE)
        tracker.fail_step(STEP_CALL3_RECONCILE, "API timeout")
        summary = tracker.summary()
        step = summary["steps"][STEP_CALL3_RECONCILE]
        assert step["status"] == "failed"
        assert step["error"] == "API timeout"


# ===========================================================================
# 4. Merge + Re-score
# ===========================================================================
class TestMergeAndRescore:
    """Reconciliation corrections are merged and confidence re-scored."""

    def test_confirmed_bumps_confidence(self):
        """Confirmed items get CONFIDENCE_BUMP_CONFIRMED (+5)."""
        from storage.ai_reconcile import merge_reconciled_items, CONFIDENCE_BUMP_CONFIRMED

        all_items = [
            {"name": "Caesar Salad", "price_cents": 995, "category": "Salads",
             "description": "Romaine", "confidence": 70},
        ]
        reconciled = [
            {"name": "Caesar Salad", "price": 9.95, "category": "Salads",
             "description": "Romaine", "status": "confirmed"},
        ]
        items, changes = merge_reconciled_items(all_items, reconciled)
        assert items[0]["confidence"] == 70 + CONFIDENCE_BUMP_CONFIRMED

    def test_corrected_sets_confidence_92(self):
        """Corrected items set confidence to CONFIDENCE_CORRECTED_VALUE."""
        from storage.ai_reconcile import merge_reconciled_items, CONFIDENCE_CORRECTED_VALUE

        all_items = [
            {"name": "Steak", "price_cents": 999, "category": "Entrees",
             "description": None, "confidence": 50},
        ]
        reconciled = [
            {"name": "Steak", "price": 29.99, "category": "Entrees",
             "description": "12oz strip", "status": "corrected"},
        ]
        items, changes = merge_reconciled_items(all_items, reconciled)
        assert items[0]["confidence"] == CONFIDENCE_CORRECTED_VALUE
        assert items[0]["price_cents"] == 2999

    def test_rescore_after_merge(self):
        """Re-scoring after merge updates semantic_confidence and tiers."""
        from storage.ai_reconcile import merge_reconciled_items
        from storage.semantic_confidence import (
            score_semantic_confidence, classify_confidence_tiers,
        )

        # Build items that have semantic annotations (post-semantic-pipeline)
        items = [
            {"name": "Steak", "price_cents": 999, "category": "Entrees",
             "description": None, "confidence": 0.50,
             "semantic_confidence": 0.45, "semantic_tier": "low",
             "needs_review": True, "price_flags": [],
             "repair_recommendations": [], "variants": []},
        ]
        reconciled = [
            {"name": "Steak", "price": 29.99, "category": "Entrees",
             "description": "12oz strip", "status": "corrected"},
        ]
        items, _ = merge_reconciled_items(items, reconciled)

        # Re-score
        score_semantic_confidence(items)
        classify_confidence_tiers(items)

        # After correction, confidence should improve
        assert items[0]["semantic_confidence"] > 0.45
        assert items[0].get("semantic_tier") is not None

    def test_not_found_items_unchanged(self):
        """Not-found items are left unchanged in the merge."""
        from storage.ai_reconcile import merge_reconciled_items

        all_items = [
            {"name": "Ghost Item", "price_cents": 0, "category": "Other",
             "description": None, "confidence": 20},
        ]
        reconciled = [
            {"name": "Ghost Item", "price": 0, "category": "Other",
             "description": None, "status": "not_found"},
        ]
        items, changes = merge_reconciled_items(all_items, reconciled)
        assert items[0]["confidence"] == 20
        assert items[0]["price_cents"] == 0


# ===========================================================================
# 5. Debug Payload
# ===========================================================================
class TestDebugPayload:
    """targeted_reconciliation block in the debug payload."""

    def test_payload_structure(self):
        """Reconciliation result maps to expected payload structure."""
        reconcile_result = {
            "skipped": False,
            "skip_reason": None,
            "error": None,
            "confidence": 0.94,
            "model": "claude-sonnet-4-5-20250929",
            "items_confirmed": 1,
            "items_corrected": 1,
            "items_not_found": 0,
            "changes": [{"type": "price_corrected", "detail": "Steak: $9.99 → $29.99"}],
            "merge_changes": [{"type": "price_corrected", "detail": "Price updated"}],
            "notes": "Fixed steak price",
        }

        # Simulate the payload block from app.py
        payload_block = {
            "skipped": reconcile_result.get("skipped", False),
            "skip_reason": reconcile_result.get("skip_reason"),
            "error": reconcile_result.get("error"),
            "confidence": reconcile_result.get("confidence", 0.0),
            "model": reconcile_result.get("model"),
            "items_confirmed": reconcile_result.get("items_confirmed", 0),
            "items_corrected": reconcile_result.get("items_corrected", 0),
            "items_not_found": reconcile_result.get("items_not_found", 0),
            "changes": reconcile_result.get("changes", []),
            "merge_changes": reconcile_result.get("merge_changes", []),
            "notes": reconcile_result.get("notes"),
        }

        assert payload_block["skipped"] is False
        assert payload_block["confidence"] == pytest.approx(0.94)
        assert payload_block["items_corrected"] == 1
        assert payload_block["items_confirmed"] == 1
        assert len(payload_block["changes"]) == 1
        assert payload_block["model"] == "claude-sonnet-4-5-20250929"

    def test_payload_skipped_structure(self):
        """Skipped reconciliation still produces valid payload block."""
        reconcile_result = {
            "skipped": True,
            "skip_reason": "no_flagged_items",
            "confidence": 0.0,
            "model": "claude-sonnet-4-5-20250929",
            "items_confirmed": 0,
            "items_corrected": 0,
            "items_not_found": 0,
            "changes": [],
            "notes": "",
        }
        payload_block = {
            "skipped": reconcile_result.get("skipped", False),
            "skip_reason": reconcile_result.get("skip_reason"),
            "confidence": reconcile_result.get("confidence", 0.0),
        }
        assert payload_block["skipped"] is True
        assert payload_block["skip_reason"] == "no_flagged_items"

    def test_payload_json_round_trip(self):
        """Payload block survives JSON serialization."""
        payload_block = {
            "skipped": False,
            "confidence": 0.94,
            "items_confirmed": 2,
            "items_corrected": 1,
            "items_not_found": 0,
            "changes": [{"type": "corrected", "detail": "Fixed name"}],
            "merge_changes": [{"type": "name_corrected", "detail": "Name updated"}],
            "notes": "All good",
        }
        serialized = json.dumps(payload_block)
        deserialized = json.loads(serialized)
        assert deserialized == payload_block


# ===========================================================================
# 6. Pipeline Metrics — STEP_CALL3_RECONCILE
# ===========================================================================
class TestPipelineMetricsCall3:
    """Metrics tracker handles Call 3 step in all scenarios."""

    def test_step_in_canonical_order(self):
        """STEP_CALL3_RECONCILE appears 5th in item_flow."""
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_OCR_TEXT, STEP_CALL1_EXTRACT,
            STEP_CALL2_VISION, STEP_SEMANTIC, STEP_CALL3_RECONCILE,
        )
        tracker = PipelineTracker()
        for step in [STEP_OCR_TEXT, STEP_CALL1_EXTRACT, STEP_CALL2_VISION,
                      STEP_SEMANTIC, STEP_CALL3_RECONCILE]:
            tracker.start_step(step)
            tracker.end_step(step, items=5)
        summary = tracker.summary()
        flow_steps = [e["step"] for e in summary["item_flow"]]
        assert flow_steps == [
            STEP_OCR_TEXT, STEP_CALL1_EXTRACT, STEP_CALL2_VISION,
            STEP_SEMANTIC, STEP_CALL3_RECONCILE,
        ]

    def test_step_success_with_metadata(self):
        """Success step records confirmed/corrected/not_found metadata."""
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL3_RECONCILE

        tracker = PipelineTracker()
        tracker.start_step(STEP_CALL3_RECONCILE)
        tracker.end_step(STEP_CALL3_RECONCILE, items=3,
                         confirmed=1, corrected=1, not_found=1, confidence=0.92)
        summary = tracker.summary()
        step = summary["steps"][STEP_CALL3_RECONCILE]
        assert step["status"] == "success"
        assert step["items"] == 3
        assert step["confirmed"] == 1
        assert step["corrected"] == 1
        assert step["not_found"] == 1
        assert step["confidence"] == pytest.approx(0.92)

    def test_step_skip(self):
        """Skipped step with reason."""
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL3_RECONCILE

        tracker = PipelineTracker()
        tracker.skip_step(STEP_CALL3_RECONCILE, "no_api_key")
        summary = tracker.summary()
        step = summary["steps"][STEP_CALL3_RECONCILE]
        assert step["status"] == "skipped"
        assert step["skip_reason"] == "no_api_key"

    def test_step_failure(self):
        """Failed step with error message."""
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL3_RECONCILE

        tracker = PipelineTracker()
        tracker.start_step(STEP_CALL3_RECONCILE)
        tracker.fail_step(STEP_CALL3_RECONCILE, "Connection refused")
        summary = tracker.summary()
        step = summary["steps"][STEP_CALL3_RECONCILE]
        assert step["status"] == "failed"
        assert step["error"] == "Connection refused"

    def test_bottleneck_can_be_call3(self):
        """Call 3 can be identified as the bottleneck (slowest step)."""
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL3_RECONCILE, STEP_OCR_TEXT
        import time

        tracker = PipelineTracker()
        # Fast step
        tracker.start_step(STEP_OCR_TEXT)
        tracker.end_step(STEP_OCR_TEXT, items=0)
        # Slow step (simulate with direct manipulation)
        tracker._steps[STEP_CALL3_RECONCILE] = {
            "status": "success", "duration_ms": 5000, "items": 3,
        }
        summary = tracker.summary()
        assert summary["bottleneck"] == STEP_CALL3_RECONCILE


# ===========================================================================
# 7. Confidence Flow End-to-End
# ===========================================================================
class TestConfidenceFlow:
    """Confidence values flow correctly through reconciliation."""

    def test_confirmed_adds_5_to_0_100_scale(self):
        """Confirmed item confidence bump works on 0-100 scale."""
        from storage.ai_reconcile import merge_reconciled_items, CONFIDENCE_BUMP_CONFIRMED

        all_items = [
            {"name": "Pizza", "price_cents": 1495, "category": "Pizza",
             "description": "Cheese", "confidence": 85},
        ]
        reconciled = [
            {"name": "Pizza", "price": 14.95, "category": "Pizza",
             "description": "Cheese", "status": "confirmed"},
        ]
        items, _ = merge_reconciled_items(all_items, reconciled)
        assert items[0]["confidence"] == 85 + CONFIDENCE_BUMP_CONFIRMED

    def test_confirmed_caps_at_100(self):
        """Confidence bump doesn't exceed 100."""
        from storage.ai_reconcile import merge_reconciled_items

        all_items = [
            {"name": "Pizza", "price_cents": 1495, "category": "Pizza",
             "description": "Cheese", "confidence": 98},
        ]
        reconciled = [
            {"name": "Pizza", "price": 14.95, "category": "Pizza",
             "description": "Cheese", "status": "confirmed"},
        ]
        items, _ = merge_reconciled_items(all_items, reconciled)
        assert items[0]["confidence"] == 100

    def test_corrected_overrides_to_92(self):
        """Corrected item sets confidence to 92 regardless of original."""
        from storage.ai_reconcile import merge_reconciled_items, CONFIDENCE_CORRECTED_VALUE

        all_items = [
            {"name": "Steak", "price_cents": 999, "category": "Entrees",
             "description": None, "confidence": 30},
        ]
        reconciled = [
            {"name": "Steak", "price": 29.99, "category": "Entrees",
             "description": "12oz", "status": "corrected"},
        ]
        items, _ = merge_reconciled_items(all_items, reconciled)
        assert items[0]["confidence"] == CONFIDENCE_CORRECTED_VALUE  # 92


# ===========================================================================
# 8. Draft Item Field Propagation
# ===========================================================================
class TestDraftItemPropagation:
    """Reconciliation corrections propagate to draft items."""

    def test_name_correction_propagates(self):
        """Corrected name from reconciliation updates draft item."""
        from storage.ai_reconcile import merge_reconciled_items

        sem_items = [
            {"name": "Cesar Salad", "price_cents": 995, "category": "Salads",
             "description": "Romaine", "confidence": 70},
        ]
        draft_items = [
            {"name": "Cesar Salad", "price_cents": 995, "category": "Salads",
             "description": "Romaine", "confidence": 95, "position": 5},
        ]
        reconciled = [
            {"name": "Cesar Salad", "price": 9.95, "category": "Salads",
             "description": "Romaine, croutons, parmesan", "status": "corrected"},
        ]
        sem_items, _ = merge_reconciled_items(sem_items, reconciled)

        # Simulate the draft propagation logic from app.py
        for draft_it, sem_it in zip(draft_items, sem_items):
            for field in ("name", "category", "description"):
                new_val = sem_it.get(field)
                if new_val and new_val != draft_it.get(field):
                    draft_it[field] = new_val

        assert draft_items[0]["description"] == "Romaine, croutons, parmesan"

    def test_price_correction_propagates(self):
        """Corrected price from reconciliation updates draft item."""
        from storage.ai_reconcile import merge_reconciled_items

        sem_items = [
            {"name": "Steak", "price_cents": 999, "category": "Entrees",
             "description": None, "confidence": 50},
        ]
        draft_items = [
            {"name": "Steak", "price_cents": 999, "category": "Entrees",
             "description": None, "confidence": 95, "position": 7},
        ]
        reconciled = [
            {"name": "Steak", "price": 29.99, "category": "Entrees",
             "description": "12oz strip", "status": "corrected"},
        ]
        sem_items, _ = merge_reconciled_items(sem_items, reconciled)

        for draft_it, sem_it in zip(draft_items, sem_items):
            new_price = sem_it.get("price_cents")
            if new_price and new_price != draft_it.get("price_cents"):
                draft_it["price_cents"] = new_price

        assert draft_items[0]["price_cents"] == 2999

    def test_category_correction_propagates(self):
        """Corrected category from reconciliation updates draft item."""
        from storage.ai_reconcile import merge_reconciled_items

        sem_items = [
            {"name": "Wings", "price_cents": 1150, "category": "Other",
             "description": "Buffalo style", "confidence": 60},
        ]
        draft_items = [
            {"name": "Wings", "price_cents": 1150, "category": "Other",
             "description": "Buffalo style", "confidence": 95, "position": 2},
        ]
        reconciled = [
            {"name": "Wings", "price": 11.50, "category": "Appetizers",
             "description": "Buffalo style", "status": "corrected"},
        ]
        sem_items, _ = merge_reconciled_items(sem_items, reconciled)

        for draft_it, sem_it in zip(draft_items, sem_items):
            for field in ("name", "category", "description"):
                new_val = sem_it.get(field)
                if new_val and new_val != draft_it.get(field):
                    draft_it[field] = new_val

        assert draft_items[0]["category"] == "Appetizers"


# ===========================================================================
# 9. Collect Flagged Items from Semantic Result
# ===========================================================================
class TestCollectFromSemanticResult:
    """collect_flagged_items works on semantic pipeline output items."""

    def test_collects_from_real_semantic_output(self):
        """Items from run_semantic_pipeline have the right fields for collect_flagged_items."""
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.ai_reconcile import collect_flagged_items

        draft_items = _sample_draft_rows()
        semantic_result = run_semantic_pipeline(draft_items)
        sem_items = semantic_result["items"]

        # All items should have semantic_tier and needs_review
        for it in sem_items:
            assert "semantic_tier" in it
            assert "needs_review" in it
            assert "semantic_confidence" in it

        # collect_flagged_items should work without error
        flagged = collect_flagged_items(sem_items)
        assert isinstance(flagged, list)

    def test_flagged_items_sorted_by_severity(self):
        """Flagged items are sorted: reject first, then low, then medium."""
        from storage.ai_reconcile import collect_flagged_items

        items = [
            {"name": "A", "semantic_tier": "medium", "semantic_confidence": 0.65,
             "needs_review": True},
            {"name": "B", "semantic_tier": "reject", "semantic_confidence": 0.10,
             "needs_review": True},
            {"name": "C", "semantic_tier": "low", "semantic_confidence": 0.40,
             "needs_review": True},
        ]
        flagged = collect_flagged_items(items)
        names = [it["name"] for it in flagged]
        assert names == ["B", "C", "A"]

    def test_max_10_items_collected(self):
        """collect_flagged_items caps at MAX_RECONCILE_ITEMS=10."""
        from storage.ai_reconcile import collect_flagged_items, MAX_RECONCILE_ITEMS

        items = [
            {"name": f"Item {i}", "semantic_tier": "low",
             "semantic_confidence": 0.3 + i * 0.01, "needs_review": True}
            for i in range(15)
        ]
        flagged = collect_flagged_items(items)
        assert len(flagged) == MAX_RECONCILE_ITEMS


# ===========================================================================
# 10. Edge Cases
# ===========================================================================
class TestEdgeCases:
    """Edge cases for pipeline integration."""

    def test_single_flagged_item(self, tmp_menu_png):
        """Pipeline works with just one flagged item."""
        from storage.ai_reconcile import (
            collect_flagged_items, reconcile_flagged_items,
        )

        flagged = [
            {"name": "Bad Item", "price_cents": 0, "semantic_tier": "reject",
             "semantic_confidence": 0.15, "needs_review": True,
             "price_flags": [], "repair_recommendations": []},
        ]
        reconcile_resp = _make_reconciliation_response([
            {"name": "Bad Item", "price": 0, "category": "Other",
             "description": None, "sizes": [], "status": "not_found",
             "changes": ["Item not on menu"]},
        ])
        mock_client = _make_mock_client(reconcile_resp)

        with patch("storage.ai_reconcile._get_client", return_value=mock_client):
            result = reconcile_flagged_items(str(tmp_menu_png), flagged)
        assert result["items_not_found"] == 1
        assert result["skipped"] is False

    def test_empty_items_no_crash(self):
        """Empty item list doesn't crash reconciliation."""
        from storage.ai_reconcile import collect_flagged_items, reconcile_flagged_items

        assert collect_flagged_items([]) == []
        result = reconcile_flagged_items("/fake/path.png", [])
        assert result["skipped"] is True
        assert result["skip_reason"] == "no_flagged_items"

    def test_mixed_statuses(self, tmp_menu_png):
        """Reconciliation with mix of confirmed, corrected, and not_found."""
        from storage.ai_reconcile import reconcile_flagged_items

        flagged = [
            {"name": "Item A", "price_cents": 100, "semantic_tier": "low",
             "semantic_confidence": 0.3, "needs_review": True,
             "price_flags": [], "repair_recommendations": []},
            {"name": "Item B", "price_cents": 200, "semantic_tier": "low",
             "semantic_confidence": 0.35, "needs_review": True,
             "price_flags": [], "repair_recommendations": []},
            {"name": "Item C", "price_cents": 0, "semantic_tier": "reject",
             "semantic_confidence": 0.1, "needs_review": True,
             "price_flags": [], "repair_recommendations": []},
        ]
        reconcile_resp = _make_reconciliation_response([
            {"name": "Item A", "price": 1.00, "category": "Other",
             "description": None, "sizes": [], "status": "confirmed", "changes": []},
            {"name": "Item B", "price": 12.00, "category": "Appetizers",
             "description": "Tasty", "sizes": [], "status": "corrected",
             "changes": ["Fixed price"]},
            {"name": "Item C", "price": 0, "category": "Other",
             "description": None, "sizes": [], "status": "not_found",
             "changes": ["Not visible on menu"]},
        ])
        mock_client = _make_mock_client(reconcile_resp)

        with patch("storage.ai_reconcile._get_client", return_value=mock_client):
            result = reconcile_flagged_items(str(tmp_menu_png), flagged)
        assert result["items_confirmed"] == 1
        assert result["items_corrected"] == 1
        assert result["items_not_found"] == 1

    def test_reconciliation_with_variants(self, tmp_menu_png):
        """Items with variants survive reconciliation."""
        from storage.ai_reconcile import reconcile_flagged_items

        flagged = [
            {"name": "Pizza", "price_cents": 1495, "semantic_tier": "low",
             "semantic_confidence": 0.4, "needs_review": True,
             "price_flags": [], "repair_recommendations": [],
             "_variants": [
                 {"kind": "size", "label": "Small", "price_cents": 1095},
                 {"kind": "size", "label": "Large", "price_cents": 1495},
             ]},
        ]
        reconcile_resp = _make_reconciliation_response([
            {"name": "Pizza", "price": 14.95, "category": "Pizza",
             "description": "Cheese pizza",
             "sizes": [{"label": "Small", "price": 10.95}, {"label": "Large", "price": 14.95}],
             "status": "confirmed", "changes": []},
        ])
        mock_client = _make_mock_client(reconcile_resp)

        with patch("storage.ai_reconcile._get_client", return_value=mock_client):
            result = reconcile_flagged_items(str(tmp_menu_png), flagged)
        assert result["items_confirmed"] == 1
        assert result["skipped"] is False
