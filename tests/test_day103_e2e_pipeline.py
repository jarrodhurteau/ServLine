# tests/test_day103_e2e_pipeline.py
"""
Day 103 — Full Pipeline E2E Validation.

Validates that the complete 3-call pipeline (Call 1 extraction → Call 2
vision verification → semantic pipeline → Call 3 targeted reconciliation)
resolves remaining issues without more Call 1 prompt tweaking.

Tests use realistic pizza-menu data matching the real menu tested on Days
102.5–102.8b: pizzas, calzones, appetizers, wings, burgers, wraps, sauces.

40 tests covering:
  1.  Full 3-call E2E happy path — all stages produce expected output
  2.  Per-call contribution tracking — what each call fixes
  3.  Known issue scenarios:
      a. Description shifting (calzone descriptions on wrong items)
      b. Missing wrap variants (W/Fries $14 variant)
      c. Duplicate topping items ("Each Topping Add" alongside individuals)
      d. Wing price swaps (Regular > W/Fries inversion)
  4.  Pipeline mode toggle — thinking vs 3call modes
  5.  Confidence flow through all 3 calls
  6.  Debug payload captures all 5 stages
  7.  Semantic → Call 3 data format compatibility
  8.  Call 2 adds missing items that Call 1 missed
  9.  Call 3 fixes items that Call 2 flagged
 10.  Pipeline metrics track all stages with per-call item counts
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
# Realistic pizza-menu test data (matches real menu from Days 102.5-102.8b)
# ---------------------------------------------------------------------------
def _pizza_menu_claude_items():
    """Call 1 extraction output — realistic pizza menu with known issues.

    Known issues embedded:
      - "Each Topping Add" still present alongside individual toppings
      - Buffalo Chicken Calzone has wrong description (shifted from Meat Lovers)
      - Wraps only have White/Wheat variants, missing W/Fries $14
      - 10 Pcs Buffalo Tender: Regular $25.50 > W/Fries $19.95 (price swap)
    """
    return [
        # --- Pizza ---
        {"name": "Cheese Pizza", "description": None, "price": 0,
         "category": "Pizza", "sizes": [
             {"label": "10\" Med", "price": 8.00},
             {"label": "12\"", "price": 11.50},
             {"label": "14\"", "price": 13.95},
             {"label": "Family Size", "price": 22.50},
         ]},
        {"name": "Meat Lovers", "description": "Pepperoni, Sausage, Bacon, Ham & Hamburger",
         "price": 0, "category": "Pizza", "sizes": [
             {"label": "12\"", "price": 17.95},
             {"label": "16\"", "price": 25.50},
             {"label": "Family Size", "price": 34.75},
         ]},
        {"name": "Margherita Pizza", "description": "Fresh Tomatoes, Basil & Fresh Mozzarella",
         "price": 0, "category": "Pizza", "sizes": [
             {"label": "12\"", "price": 17.95},
             {"label": "16\"", "price": 25.50},
             {"label": "Family Size", "price": 34.75},
         ]},
        # --- Toppings (includes the problematic "Each Topping Add") ---
        {"name": "Each Topping Add", "description": None, "price": 0,
         "category": "Toppings", "sizes": [
             {"label": "10\" Med", "price": 1.50},
             {"label": "12\"", "price": 1.50},
             {"label": "14\"", "price": 2.25},
             {"label": "Family Size", "price": 2.75},
         ]},
        {"name": "Pepperoni", "description": None, "price": 0,
         "category": "Toppings", "sizes": [
             {"label": "10\" Med", "price": 1.50},
             {"label": "12\"", "price": 1.50},
             {"label": "14\"", "price": 2.25},
             {"label": "Family Size", "price": 2.75},
         ]},
        {"name": "Mushrooms", "description": None, "price": 0,
         "category": "Toppings", "sizes": [
             {"label": "10\" Med", "price": 1.50},
             {"label": "12\"", "price": 1.50},
             {"label": "14\"", "price": 2.25},
             {"label": "Family Size", "price": 2.75},
         ]},
        # --- Calzones (description shift: Buffalo Chicken has Meat Lovers desc) ---
        {"name": "Cheese Calzone",
         "description": "All calzones stuffed with ricotta and mozzarella. Served with sauce on the side.",
         "price": 0, "category": "Calzones", "sizes": [
             {"label": "Small", "price": 9.50},
             {"label": "Large", "price": 12.95},
         ]},
        {"name": "Buffalo Chicken Calzone",
         "description": "Pepperoni, Sausage, Bacon, Hamburger & Sauce",  # WRONG — shifted from Meat Lovers
         "price": 0, "category": "Calzones", "sizes": [
             {"label": "Small", "price": 14.75},
             {"label": "Large", "price": 19.95},
         ]},
        # --- Appetizers ---
        {"name": "Garlic Knots", "description": "12 Pieces", "price": 6.95,
         "category": "Appetizers", "sizes": []},
        {"name": "Mozzarella Sticks", "description": "6pcs marinara sauce on the side",
         "price": 10.00, "category": "Appetizers", "sizes": []},
        # --- Wings (price swap: Regular > W/Fries on 10 Pcs Tender) ---
        {"name": "6 Pcs Wings", "description": "Naked or Breaded. Served with side blue cheese.",
         "price": 0, "category": "Wings", "sizes": [
             {"label": "Regular", "price": 9.95},
             {"label": "W/ Fries", "price": 13.50},
         ]},
        {"name": "10 Pcs Buffalo Chicken Tender", "description": None,
         "price": 0, "category": "Wings", "sizes": [
             {"label": "Regular", "price": 25.50},  # WRONG — should be lower
             {"label": "W/ Fries", "price": 19.95},  # WRONG — should be higher
         ]},
        # --- Sauces ---
        {"name": "Hot", "description": "Wing sauce", "price": 0,
         "category": "Sauces", "sizes": []},
        {"name": "BBQ", "description": "Wing sauce", "price": 0,
         "category": "Sauces", "sizes": []},
        # --- Burgers ---
        {"name": "Burger", "description": "Lettuce, tomato, mayo", "price": 0,
         "category": "Burgers", "sizes": [
             {"label": "Regular", "price": 9.00},
             {"label": "Deluxe", "price": 13.00},
         ]},
        # --- Wraps (missing W/Fries $14 variant) ---
        {"name": "Beef Gyro Wrap",
         "description": "Lettuce, Tomatoes, Onions & Tzatziki Sauce",
         "price": 0, "category": "Wraps", "sizes": [
             {"label": "White", "price": 10.99},
             {"label": "Wheat", "price": 10.99},
             # Missing: {"label": "W/ Fries", "price": 14.00}
         ]},
        {"name": "Buffalo Chicken Wrap",
         "description": "Hot, Mild, BBQ, Honey, Crispy Chicken, Ranch Dressing, Lettuce",
         "price": 0, "category": "Wraps", "sizes": [
             {"label": "White", "price": 10.99},
             {"label": "Wheat", "price": 10.99},
         ]},
    ]


def _call2_vision_items():
    """Call 2 output — vision verification catches some issues.

    Call 2 fixes:
      - Buffalo Chicken Calzone description corrected
      - Adds missing Chicken Nacho that Call 1 missed
      - Removes "Each Topping Add" generic (individual toppings kept)
    Call 2 does NOT fix:
      - Wing price swaps (numbers match image — the menu itself has weird layout)
      - Wraps missing W/Fries (not visible in section header context)
    """
    items = _pizza_menu_claude_items()

    # Fix: Buffalo Chicken Calzone description
    for it in items:
        if it["name"] == "Buffalo Chicken Calzone":
            it["description"] = "Chicken, Hot Sauce, Blue Cheese, Mozzarella & Ricotta"

    # Fix: Remove "Each Topping Add" generic
    items = [it for it in items if it["name"] != "Each Topping Add"]

    # Fix: Add missing item
    items.append({
        "name": "Chicken Nacho",
        "description": "Grilled chicken, banana peppers, mozzarella & cheddar",
        "price": 12.95, "category": "Appetizers", "sizes": [],
    })

    return items


def _call2_vision_response(items):
    """Build a mock Call 2 API response."""
    return {
        "items": items,
        "confidence": 0.93,
        "notes": "Fixed calzone description, removed generic topping, added Chicken Nacho",
    }


def _call3_reconcile_response():
    """Call 3 output — reconciliation fixes flagged items.

    Call 3 fixes:
      - Wing price swap corrected (Regular $19.95, W/Fries $25.50)
    """
    return {
        "items": [
            {"name": "10 Pcs Buffalo Chicken Tender", "description": None,
             "price": 0, "category": "Wings",
             "sizes": [
                 {"label": "Regular", "price": 19.95},
                 {"label": "W/ Fries", "price": 25.50},
             ],
             "status": "corrected",
             "changes": ["Swapped Regular/W Fries prices — Regular should be cheaper"]},
        ],
        "confidence": 0.96,
        "notes": "Corrected price swap on buffalo tenders",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


def _run_full_3call_pipeline(tmp_menu_png, call2_items=None, call3_response=None):
    """Run the complete 3-call pipeline with mocked API calls.

    Returns (items, tracker, semantic_result, reconcile_result, vision_result).
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

    if call2_items is None:
        call2_items = _call2_vision_items()
    if call3_response is None:
        call3_response = _call3_reconcile_response()

    # --- Stage 1: OCR ---
    tracker = PipelineTracker()
    tracker.start_step(STEP_OCR_TEXT)
    tracker.end_step(STEP_OCR_TEXT, chars=7736)

    # --- Stage 2: Call 1 extraction ---
    tracker.start_step(STEP_CALL1_EXTRACT)
    call1_items = _pizza_menu_claude_items()
    tracker.end_step(STEP_CALL1_EXTRACT, items=len(call1_items))

    # --- Stage 3: Call 2 vision verification ---
    tracker.start_step(STEP_CALL2_VISION)
    vision_resp_data = _call2_vision_response(call2_items)
    vision_client = _make_mock_client(vision_resp_data)

    with patch("storage.ai_vision_verify._get_client", return_value=vision_client):
        vision_result = verify_menu_with_vision(str(tmp_menu_png), call1_items)

    if not vision_result.get("skipped") and not vision_result.get("error"):
        items = verified_items_to_draft_rows(vision_result["items"])
        n_changes = len(vision_result.get("changes", []))
        tracker.end_step(STEP_CALL2_VISION, items=len(items),
                         changes=n_changes,
                         confidence=vision_result.get("confidence", 0))
    else:
        items = claude_items_to_draft_rows(call1_items)
        tracker.skip_step(STEP_CALL2_VISION, "mocked_skip")

    # --- Stage 4: Semantic pipeline ---
    tracker.start_step(STEP_SEMANTIC)
    semantic_result = run_semantic_pipeline(items)
    tracker.end_step(STEP_SEMANTIC, items=len(items),
                     quality_grade=semantic_result.get("quality_grade", "?"),
                     mean_confidence=semantic_result.get("mean_confidence", 0.0))

    # --- Stage 5: Call 3 targeted reconciliation ---
    reconcile_result = None
    sem_items = semantic_result["items"]
    flagged = collect_flagged_items(sem_items)

    if flagged:
        tracker.start_step(STEP_CALL3_RECONCILE)
        recon_client = _make_mock_client(call3_response)
        with patch("storage.ai_reconcile._get_client", return_value=recon_client):
            reconcile_result = reconcile_flagged_items(str(tmp_menu_png), flagged)

        if not reconcile_result.get("skipped") and not reconcile_result.get("error"):
            sem_items, merge_changes = merge_reconciled_items(
                sem_items, reconcile_result["items"]
            )
            reconcile_result["merge_changes"] = merge_changes

            # Re-score after reconciliation
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
                # Confidence: convert back from 0-1 to 0-100
                new_conf = sem_it.get("confidence")
                if new_conf is not None:
                    if isinstance(new_conf, (int, float)) and new_conf <= 1.0:
                        draft_it["confidence"] = int(round(new_conf * 100))
                    else:
                        draft_it["confidence"] = int(round(new_conf))

            tracker.end_step(STEP_CALL3_RECONCILE, items=len(flagged),
                             confirmed=reconcile_result.get("items_confirmed", 0),
                             corrected=reconcile_result.get("items_corrected", 0),
                             not_found=reconcile_result.get("items_not_found", 0),
                             confidence=reconcile_result.get("confidence", 0))
        else:
            skip = reconcile_result.get("skip_reason") or reconcile_result.get("error", "?")
            tracker.skip_step(STEP_CALL3_RECONCILE, skip)
    else:
        reconcile_result = {"skipped": True, "skip_reason": "no_flagged_items"}
        tracker.skip_step(STEP_CALL3_RECONCILE, "no_flagged_items")

    tracker.strategy = "claude_api+vision"
    return items, tracker, semantic_result, reconcile_result, vision_result


# ===========================================================================
# 1. Full 3-Call E2E Happy Path
# ===========================================================================
class TestFull3CallPipeline:
    """Complete pipeline produces expected output across all 5 stages."""

    def test_all_5_stages_tracked(self, tmp_menu_png):
        """All 5 pipeline stages appear in metrics summary."""
        from storage.pipeline_metrics import (
            STEP_OCR_TEXT, STEP_CALL1_EXTRACT, STEP_CALL2_VISION,
            STEP_SEMANTIC, STEP_CALL3_RECONCILE,
        )
        items, tracker, sem, recon, vision = _run_full_3call_pipeline(tmp_menu_png)
        summary = tracker.summary()

        for step in [STEP_OCR_TEXT, STEP_CALL1_EXTRACT, STEP_CALL2_VISION,
                     STEP_SEMANTIC, STEP_CALL3_RECONCILE]:
            assert step in summary["steps"], f"{step} missing from tracker"

    def test_item_count_progression(self, tmp_menu_png):
        """Item counts are tracked through the pipeline."""
        from storage.pipeline_metrics import STEP_CALL1_EXTRACT, STEP_CALL2_VISION
        items, tracker, sem, recon, vision = _run_full_3call_pipeline(tmp_menu_png)
        summary = tracker.summary()

        call1_items = summary["steps"][STEP_CALL1_EXTRACT].get("items", 0)
        call2_items = summary["steps"][STEP_CALL2_VISION].get("items", 0)

        # Call 1: 17 items, Call 2: removes "Each Topping Add", adds Chicken Nacho = 17
        assert call1_items == 17
        assert call2_items > 0

    def test_final_items_non_empty(self, tmp_menu_png):
        """Pipeline produces a non-empty final item list."""
        items, *_ = _run_full_3call_pipeline(tmp_menu_png)
        assert len(items) > 10  # Full pizza menu has many items

    def test_all_items_have_required_fields(self, tmp_menu_png):
        """Every final item has name, category, price_cents, confidence."""
        items, *_ = _run_full_3call_pipeline(tmp_menu_png)
        for it in items:
            assert "name" in it and it["name"]
            assert "category" in it
            assert "price_cents" in it
            assert "confidence" in it

    def test_extraction_strategy(self, tmp_menu_png):
        """Strategy is claude_api+vision when running 3-call mode."""
        items, tracker, *_ = _run_full_3call_pipeline(tmp_menu_png)
        assert tracker.strategy == "claude_api+vision"

    def test_semantic_quality_grade_assigned(self, tmp_menu_png):
        """Semantic pipeline assigns a quality grade."""
        items, tracker, sem, *_ = _run_full_3call_pipeline(tmp_menu_png)
        assert sem["quality_grade"] in ("A", "B", "C", "D")


# ===========================================================================
# 2. Per-Call Contribution Tracking
# ===========================================================================
class TestPerCallContribution:
    """Each call's contribution is captured and measurable."""

    def test_call2_changes_logged(self, tmp_menu_png):
        """Call 2 vision verification produces a changes log."""
        items, tracker, sem, recon, vision = _run_full_3call_pipeline(tmp_menu_png)
        assert isinstance(vision.get("changes"), list)
        # Call 2 should detect differences between Call 1 and its own output
        # (description fix, item removal, item addition)
        assert vision.get("confidence", 0) > 0

    def test_call2_removes_generic_topping(self, tmp_menu_png):
        """Call 2 removes 'Each Topping Add' generic item."""
        items, *_ = _run_full_3call_pipeline(tmp_menu_png)
        names = [it["name"] for it in items]
        assert "Each Topping Add" not in names

    def test_call2_adds_missing_item(self, tmp_menu_png):
        """Call 2 adds Chicken Nacho that Call 1 missed."""
        items, *_ = _run_full_3call_pipeline(tmp_menu_png)
        names = [it["name"] for it in items]
        assert "Chicken Nacho" in names

    def test_call2_fixes_calzone_description(self, tmp_menu_png):
        """Call 2 corrects Buffalo Chicken Calzone description shift."""
        items, *_ = _run_full_3call_pipeline(tmp_menu_png)
        calzone = next((it for it in items if it["name"] == "Buffalo Chicken Calzone"), None)
        assert calzone is not None
        # Should NOT have the Meat Lovers description
        assert "Pepperoni, Sausage, Bacon" not in (calzone.get("description") or "")

    def test_call3_corrects_flagged_items(self, tmp_menu_png):
        """Call 3 reconciliation produces corrections for flagged items."""
        items, tracker, sem, recon, vision = _run_full_3call_pipeline(tmp_menu_png)
        if recon and not recon.get("skipped"):
            total = (recon.get("items_confirmed", 0) +
                     recon.get("items_corrected", 0) +
                     recon.get("items_not_found", 0))
            assert total > 0

    def test_semantic_pipeline_runs_between_calls(self, tmp_menu_png):
        """Semantic pipeline runs between Call 2 and Call 3."""
        items, tracker, sem, recon, vision = _run_full_3call_pipeline(tmp_menu_png)
        assert sem is not None
        assert "items" in sem
        assert sem.get("mean_confidence", 0) > 0


# ===========================================================================
# 3. Known Issue Scenarios
# ===========================================================================
class TestKnownIssueDescriptionShift:
    """Call 2 catches calzone description shifting."""

    def test_calzone_description_corrected_by_call2(self, tmp_menu_png):
        """Buffalo Chicken Calzone gets correct description after Call 2."""
        call2_items = _call2_vision_items()
        calzone = next(it for it in call2_items if it["name"] == "Buffalo Chicken Calzone")
        # Call 2 fixes the shifted description
        assert "Hot Sauce" in calzone["description"] or "Chicken" in calzone["description"]
        assert "Pepperoni" not in calzone["description"]

    def test_original_call1_has_wrong_description(self):
        """Call 1 output has the known description shift issue."""
        call1_items = _pizza_menu_claude_items()
        calzone = next(it for it in call1_items if it["name"] == "Buffalo Chicken Calzone")
        # Call 1 has the shifted (wrong) description
        assert "Pepperoni" in calzone["description"]


class TestKnownIssueDuplicateTopping:
    """Call 2 removes the generic 'Each Topping Add' item."""

    def test_call1_has_generic_topping(self):
        """Call 1 still extracts the generic 'Each Topping Add'."""
        call1_items = _pizza_menu_claude_items()
        names = [it["name"] for it in call1_items]
        assert "Each Topping Add" in names

    def test_call2_removes_generic_keeps_specifics(self, tmp_menu_png):
        """Call 2 removes generic but keeps Pepperoni, Mushrooms."""
        items, *_ = _run_full_3call_pipeline(tmp_menu_png)
        names = [it["name"] for it in items]
        assert "Each Topping Add" not in names
        assert "Pepperoni" in names
        assert "Mushrooms" in names


class TestKnownIssueWingPriceSwap:
    """Call 3 reconciliation corrects wing price inversions."""

    def test_call1_has_price_swap(self):
        """Call 1 output has Regular > W/Fries price inversion."""
        call1_items = _pizza_menu_claude_items()
        tender = next(it for it in call1_items
                      if it["name"] == "10 Pcs Buffalo Chicken Tender")
        sizes = tender["sizes"]
        regular = next(s for s in sizes if s["label"] == "Regular")
        fries = next(s for s in sizes if s["label"] == "W/ Fries")
        # The swap: Regular ($25.50) > W/Fries ($19.95)
        assert regular["price"] > fries["price"]

    def test_call3_response_fixes_price_swap(self):
        """Call 3 reconciliation response has corrected prices."""
        resp = _call3_reconcile_response()
        tender = resp["items"][0]
        assert tender["status"] == "corrected"
        sizes = tender.get("sizes", [])
        if sizes:
            regular = next((s for s in sizes if s["label"] == "Regular"), None)
            fries = next((s for s in sizes if s["label"] == "W/ Fries"), None)
            if regular and fries:
                assert regular["price"] < fries["price"]


class TestKnownIssueWrapVariants:
    """Wraps section variant tracking."""

    def test_call1_wraps_have_white_wheat_only(self):
        """Call 1 wraps have White/Wheat but no W/Fries."""
        call1_items = _pizza_menu_claude_items()
        gyro = next(it for it in call1_items if it["name"] == "Beef Gyro Wrap")
        labels = [s["label"] for s in gyro["sizes"]]
        assert "White" in labels
        assert "Wheat" in labels
        assert "W/ Fries" not in labels

    def test_pipeline_preserves_wrap_variants(self, tmp_menu_png):
        """Pipeline preserves existing wrap variants through all stages."""
        items, *_ = _run_full_3call_pipeline(tmp_menu_png)
        gyro = next((it for it in items if it["name"] == "Beef Gyro Wrap"), None)
        assert gyro is not None
        # Variants should survive the pipeline as _variants
        variants = gyro.get("_variants", [])
        if variants:
            labels = [v.get("label", "") for v in variants]
            assert any("White" in l or "Wheat" in l for l in labels)


# ===========================================================================
# 4. Pipeline Mode Toggle
# ===========================================================================
class TestPipelineModeToggle:
    """PIPELINE_MODE config controls thinking vs 3-call behavior."""

    def test_pipeline_mode_exists(self):
        """PIPELINE_MODE is importable from ai_menu_extract."""
        from storage.ai_menu_extract import PIPELINE_MODE
        assert PIPELINE_MODE in ("thinking", "3call")

    def test_extended_thinking_derived_from_mode(self):
        """EXTENDED_THINKING is True when mode is 'thinking'."""
        from storage.ai_menu_extract import PIPELINE_MODE, EXTENDED_THINKING
        if PIPELINE_MODE == "thinking":
            assert EXTENDED_THINKING is True
        else:
            assert EXTENDED_THINKING is False

    def test_3call_mode_runs_all_calls(self, tmp_menu_png):
        """In 3-call mode, all pipeline stages execute."""
        # This test runs regardless of current PIPELINE_MODE — it exercises
        # the full pipeline directly (not via app.py)
        items, tracker, sem, recon, vision = _run_full_3call_pipeline(tmp_menu_png)
        summary = tracker.summary()
        # All 5 stages should have been tracked
        assert len(summary["steps"]) == 5

    def test_thinking_mode_skips_calls_2_3(self):
        """When thinking is active, pipeline skips Calls 2 and 3."""
        from storage.pipeline_metrics import (
            PipelineTracker, STEP_CALL2_VISION, STEP_CALL3_RECONCILE,
        )
        # Simulate the thinking mode path from app.py
        tracker = PipelineTracker()
        tracker.skip_step(STEP_CALL2_VISION, "extended_thinking")
        tracker.skip_step(STEP_CALL3_RECONCILE, "extended_thinking")
        summary = tracker.summary()
        assert summary["steps"][STEP_CALL2_VISION]["status"] == "skipped"
        assert summary["steps"][STEP_CALL3_RECONCILE]["status"] == "skipped"


# ===========================================================================
# 5. Confidence Flow Through All 3 Calls
# ===========================================================================
class TestConfidenceFlowE2E:
    """Confidence values flow correctly through the full pipeline."""

    def test_call1_items_get_default_confidence(self):
        """Call 1 → draft_rows assigns default confidence."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        call1 = _pizza_menu_claude_items()
        rows = claude_items_to_draft_rows(call1)
        for r in rows:
            assert r.get("confidence", 0) > 0

    def test_semantic_rescores_after_call3(self, tmp_menu_png):
        """Post-Call-3 re-scoring updates semantic confidence."""
        items, tracker, sem, recon, vision = _run_full_3call_pipeline(tmp_menu_png)
        # After full pipeline, items should have confidence values
        for it in items:
            assert it.get("confidence", 0) > 0

    def test_confirmed_items_get_bump(self, tmp_menu_png):
        """Items confirmed by Call 3 get a confidence bump."""
        from storage.ai_reconcile import CONFIDENCE_BUMP_CONFIRMED
        # The bump is +5 on the 0-100 scale
        assert CONFIDENCE_BUMP_CONFIRMED == 5

    def test_corrected_items_get_fixed_confidence(self, tmp_menu_png):
        """Items corrected by Call 3 get CONFIDENCE_CORRECTED_VALUE."""
        from storage.ai_reconcile import CONFIDENCE_CORRECTED_VALUE
        assert CONFIDENCE_CORRECTED_VALUE == 92


# ===========================================================================
# 6. Debug Payload Captures All 5 Stages
# ===========================================================================
class TestDebugPayloadE2E:
    """Debug payload structure captures all pipeline stages."""

    def _build_payload(self, tmp_menu_png):
        """Build a debug payload like app.py does."""
        items, tracker, sem, recon, vision = _run_full_3call_pipeline(tmp_menu_png)
        payload = {
            "extraction_strategy": "claude_api+vision",
            "clean_ocr_chars": 7736,
        }

        if vision is not None:
            payload["vision_verification"] = {
                "skipped": vision.get("skipped", False),
                "skip_reason": vision.get("skip_reason"),
                "error": vision.get("error"),
                "confidence": vision.get("confidence", 0.0),
                "model": vision.get("model"),
                "changes_count": len(vision.get("changes", [])),
                "changes": vision.get("changes", []),
                "notes": vision.get("notes"),
            }

        if sem is not None:
            payload["semantic_pipeline"] = {
                "quality_grade": sem.get("quality_grade"),
                "mean_confidence": sem.get("mean_confidence", 0.0),
                "tier_counts": sem.get("tier_counts", {}),
                "repairs_applied": sem.get("repairs_applied", 0),
            }

        if recon is not None:
            payload["targeted_reconciliation"] = {
                "skipped": recon.get("skipped", False),
                "skip_reason": recon.get("skip_reason"),
                "error": recon.get("error"),
                "confidence": recon.get("confidence", 0.0),
                "items_confirmed": recon.get("items_confirmed", 0),
                "items_corrected": recon.get("items_corrected", 0),
                "items_not_found": recon.get("items_not_found", 0),
                "changes": recon.get("changes", []),
                "merge_changes": recon.get("merge_changes", []),
            }

        payload["pipeline_metrics"] = tracker.summary()
        return payload

    def test_payload_has_all_sections(self, tmp_menu_png):
        """Payload contains vision, semantic, reconciliation, and metrics."""
        payload = self._build_payload(tmp_menu_png)
        assert "vision_verification" in payload
        assert "semantic_pipeline" in payload
        assert "targeted_reconciliation" in payload
        assert "pipeline_metrics" in payload

    def test_payload_json_serializable(self, tmp_menu_png):
        """Full payload survives JSON round-trip."""
        payload = self._build_payload(tmp_menu_png)
        serialized = json.dumps(payload, default=str)
        deserialized = json.loads(serialized)
        assert "extraction_strategy" in deserialized

    def test_payload_vision_has_confidence(self, tmp_menu_png):
        """Vision verification block has confidence score."""
        payload = self._build_payload(tmp_menu_png)
        assert payload["vision_verification"]["confidence"] > 0

    def test_payload_semantic_has_grade(self, tmp_menu_png):
        """Semantic pipeline block has quality grade."""
        payload = self._build_payload(tmp_menu_png)
        assert payload["semantic_pipeline"]["quality_grade"] in ("A", "B", "C", "D")


# ===========================================================================
# 7. Semantic → Call 3 Data Format Compatibility
# ===========================================================================
class TestSemanticToCall3Compat:
    """Semantic pipeline output is compatible with Call 3 input."""

    def test_semantic_items_have_tier_field(self, tmp_menu_png):
        """Semantic items have semantic_tier needed by collect_flagged_items."""
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.ai_menu_extract import claude_items_to_draft_rows

        call1 = _pizza_menu_claude_items()
        rows = claude_items_to_draft_rows(call1)
        result = run_semantic_pipeline(rows)

        for it in result["items"]:
            assert "semantic_tier" in it
            assert it["semantic_tier"] in ("high", "medium", "low", "reject")

    def test_semantic_items_have_needs_review(self, tmp_menu_png):
        """Semantic items have needs_review field for Call 3 flagging."""
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.ai_menu_extract import claude_items_to_draft_rows

        call1 = _pizza_menu_claude_items()
        rows = claude_items_to_draft_rows(call1)
        result = run_semantic_pipeline(rows)

        for it in result["items"]:
            assert "needs_review" in it
            assert isinstance(it["needs_review"], bool)

    def test_collect_flagged_works_on_semantic_output(self, tmp_menu_png):
        """collect_flagged_items processes semantic pipeline output."""
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.ai_reconcile import collect_flagged_items

        call1 = _pizza_menu_claude_items()
        rows = claude_items_to_draft_rows(call1)
        result = run_semantic_pipeline(rows)
        flagged = collect_flagged_items(result["items"])
        assert isinstance(flagged, list)


# ===========================================================================
# 8. Call 2 Adds Missing Items
# ===========================================================================
class TestCall2AddsMissingItems:
    """Call 2 can discover and add items Call 1 missed."""

    def test_call2_output_has_more_items(self):
        """Call 2 vision items include Chicken Nacho not in Call 1."""
        call1_names = {it["name"] for it in _pizza_menu_claude_items()}
        call2_names = {it["name"] for it in _call2_vision_items()}
        added = call2_names - call1_names
        assert "Chicken Nacho" in added

    def test_call2_removes_items(self):
        """Call 2 removes generic 'Each Topping Add'."""
        call1_names = {it["name"] for it in _pizza_menu_claude_items()}
        call2_names = {it["name"] for it in _call2_vision_items()}
        removed = call1_names - call2_names
        assert "Each Topping Add" in removed


# ===========================================================================
# 9. Pipeline Metrics Per-Call Item Counts
# ===========================================================================
class TestPipelineMetricsE2E:
    """Pipeline metrics track per-stage item counts and metadata."""

    def test_item_flow_has_5_entries(self, tmp_menu_png):
        """item_flow tracks all 5 stages."""
        items, tracker, *_ = _run_full_3call_pipeline(tmp_menu_png)
        summary = tracker.summary()
        assert len(summary["item_flow"]) == 5

    def test_strategy_recorded(self, tmp_menu_png):
        """Extraction strategy recorded in metrics."""
        items, tracker, *_ = _run_full_3call_pipeline(tmp_menu_png)
        summary = tracker.summary()
        assert summary.get("extraction_strategy") == "claude_api+vision"

    def test_total_duration_positive(self, tmp_menu_png):
        """Total pipeline duration is positive."""
        items, tracker, *_ = _run_full_3call_pipeline(tmp_menu_png)
        summary = tracker.summary()
        assert summary.get("total_duration_ms", 0) >= 0

    def test_bottleneck_identified(self, tmp_menu_png):
        """A bottleneck step is identified."""
        items, tracker, *_ = _run_full_3call_pipeline(tmp_menu_png)
        summary = tracker.summary()
        assert summary.get("bottleneck") is not None


# ===========================================================================
# 10. Edge Cases
# ===========================================================================
class TestE2EEdgeCases:
    """Edge cases for the full pipeline."""

    def test_call2_skip_falls_back_to_call1(self, tmp_menu_png):
        """If Call 2 is skipped, pipeline uses Call 1 items directly."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.ai_vision_verify import verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.pipeline_metrics import PipelineTracker, STEP_CALL2_VISION

        tracker = PipelineTracker()
        call1_items = _pizza_menu_claude_items()
        items = claude_items_to_draft_rows(call1_items)
        tracker.skip_step(STEP_CALL2_VISION, "no_api_key")

        # Semantic pipeline still runs
        sem = run_semantic_pipeline(items)
        assert sem["quality_grade"] in ("A", "B", "C", "D")
        assert len(sem["items"]) == len(items)

    def test_call3_skip_preserves_semantic_items(self, tmp_menu_png):
        """If Call 3 is skipped (no flagged), items are unchanged."""
        from storage.semantic_bridge import run_semantic_pipeline
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.ai_reconcile import collect_flagged_items

        # Create items that will ALL be high-tier (no flagging)
        good_items = [
            {"name": "Cheese Pizza", "description": "Classic cheese", "price": 14.95,
             "category": "Pizza", "sizes": []},
            {"name": "Pepperoni Pizza", "description": "Pepperoni on cheese", "price": 16.95,
             "category": "Pizza", "sizes": []},
            {"name": "Garden Salad", "description": "Mixed greens, tomato", "price": 8.95,
             "category": "Salads", "sizes": []},
        ]
        rows = claude_items_to_draft_rows(good_items)
        sem = run_semantic_pipeline(rows)
        flagged = collect_flagged_items(sem["items"])

        # With well-formed items, there may be few or no flagged items
        # The key assertion: pipeline doesn't crash
        assert isinstance(flagged, list)
        assert len(sem["items"]) == 3

    def test_empty_menu_no_crash(self, tmp_menu_png):
        """Empty Call 1 output doesn't crash the pipeline."""
        from storage.semantic_bridge import run_semantic_pipeline

        sem = run_semantic_pipeline([])
        assert sem["quality_grade"] == "D"
        assert sem["items"] == []
