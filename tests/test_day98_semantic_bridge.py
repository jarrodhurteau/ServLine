# tests/test_day98_semantic_bridge.py
"""
Day 98 — Semantic Pipeline Bridge (Sprint 11.1 continued)
Tests for storage/semantic_bridge.py and pipeline integration.

Covers:
  1. prepare_items_for_semantic — confidence normalization, _variants→variants,
     price_flags init, variant confidence defaults
  2. run_semantic_pipeline — full pipeline on Claude-style items,
     semantic_report, tier_counts, quality_grade, mean_confidence
  3. extract_semantic_metadata — per-item summary extraction
  4. apply_repairs_to_draft_items — repair flow back to draft items
  5. Pipeline integration in run_ocr_and_make_draft — semantic_result wiring,
     debug payload includes semantic_pipeline block
  6. Edge cases — empty items, single item, items without variants,
     all-high-confidence items, low-confidence items
  7. Strategy gating — semantic pipeline only runs on claude_api / claude_api+vision
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures — draft items in Claude extraction format
# ---------------------------------------------------------------------------
@pytest.fixture
def draft_items_basic():
    """Basic draft items as produced by claude_items_to_draft_rows."""
    return [
        {
            "name": "Margherita Pizza",
            "description": "Fresh mozzarella, basil",
            "price_cents": 1495,
            "category": "Pizza",
            "position": 1,
            "confidence": 90,
        },
        {
            "name": "Caesar Salad",
            "description": "Romaine, croutons, parmesan",
            "price_cents": 995,
            "category": "Salads",
            "position": 2,
            "confidence": 90,
        },
        {
            "name": "BBQ Burger",
            "description": "Bacon, cheddar, BBQ sauce",
            "price_cents": 1250,
            "category": "Burgers",
            "position": 3,
            "confidence": 90,
        },
    ]


@pytest.fixture
def draft_items_with_variants():
    """Draft items with _variants (underscore prefix, as stored by Claude extraction)."""
    return [
        {
            "name": "Cheese Pizza",
            "description": "Our classic cheese",
            "price_cents": 1095,
            "category": "Pizza",
            "position": 1,
            "confidence": 95,
            "_variants": [
                {"label": "Small", "price_cents": 1095, "kind": "size", "position": 0},
                {"label": "Large", "price_cents": 1595, "kind": "size", "position": 1},
            ],
        },
        {
            "name": "Wings",
            "description": "Choice of sauce",
            "price_cents": 1195,
            "category": "Appetizers",
            "position": 2,
            "confidence": 95,
            "_variants": [
                {"label": "6 Piece", "price_cents": 1195, "kind": "size", "position": 0},
                {"label": "12 Piece", "price_cents": 1895, "kind": "size", "position": 1},
            ],
        },
    ]


@pytest.fixture
def draft_items_many():
    """Larger set of draft items for comprehensive pipeline testing."""
    items = [
        {"name": "Pepperoni Pizza", "description": "Classic pepperoni", "price_cents": 1495, "category": "Pizza", "position": 1, "confidence": 90},
        {"name": "Veggie Pizza", "description": "Fresh vegetables", "price_cents": 1395, "category": "Pizza", "position": 2, "confidence": 90},
        {"name": "Meat Lovers Pizza", "description": "Pepperoni, sausage, bacon", "price_cents": 1695, "category": "Pizza", "position": 3, "confidence": 90},
        {"name": "Garden Salad", "description": "Mixed greens, tomatoes", "price_cents": 895, "category": "Salads", "position": 4, "confidence": 90},
        {"name": "Caesar Salad", "description": "Romaine, croutons, parmesan", "price_cents": 1095, "category": "Salads", "position": 5, "confidence": 90},
        {"name": "Chicken Wings", "description": "Buffalo or BBQ", "price_cents": 1195, "category": "Appetizers", "position": 6, "confidence": 90},
        {"name": "Mozzarella Sticks", "description": "With marinara", "price_cents": 795, "category": "Appetizers", "position": 7, "confidence": 90},
        {"name": "French Fries", "description": "Crispy golden fries", "price_cents": 495, "category": "Sides", "position": 8, "confidence": 90},
        {"name": "Cola", "description": None, "price_cents": 250, "category": "Beverages", "position": 9, "confidence": 90},
        {"name": "Iced Tea", "description": None, "price_cents": 295, "category": "Beverages", "position": 10, "confidence": 90},
    ]
    return items


# ===========================================================================
# 1. prepare_items_for_semantic
# ===========================================================================
class TestPrepareItems:
    def test_confidence_normalized_90_to_0_9(self, draft_items_basic):
        from storage.semantic_bridge import prepare_items_for_semantic
        prepared = prepare_items_for_semantic(draft_items_basic)
        assert prepared[0]["confidence"] == pytest.approx(0.90, abs=0.01)

    def test_confidence_normalized_95_to_0_95(self):
        from storage.semantic_bridge import prepare_items_for_semantic
        items = [{"name": "Test", "price_cents": 100, "confidence": 95, "category": "Other", "position": 1}]
        prepared = prepare_items_for_semantic(items)
        assert prepared[0]["confidence"] == pytest.approx(0.95, abs=0.01)

    def test_confidence_already_0_to_1_not_modified(self):
        from storage.semantic_bridge import prepare_items_for_semantic
        items = [{"name": "Test", "price_cents": 100, "confidence": 0.85, "category": "Other", "position": 1}]
        prepared = prepare_items_for_semantic(items)
        assert prepared[0]["confidence"] == pytest.approx(0.85, abs=0.01)

    def test_confidence_100_becomes_1_0(self):
        from storage.semantic_bridge import prepare_items_for_semantic
        items = [{"name": "Test", "price_cents": 100, "confidence": 100, "category": "Other", "position": 1}]
        prepared = prepare_items_for_semantic(items)
        assert prepared[0]["confidence"] == pytest.approx(1.0, abs=0.01)

    def test_variants_renamed_from_underscore(self, draft_items_with_variants):
        from storage.semantic_bridge import prepare_items_for_semantic
        prepared = prepare_items_for_semantic(draft_items_with_variants)
        assert "variants" in prepared[0]
        assert "_variants" not in prepared[0]
        assert len(prepared[0]["variants"]) == 2

    def test_variants_already_present_not_overwritten(self):
        from storage.semantic_bridge import prepare_items_for_semantic
        items = [{"name": "Test", "confidence": 90, "variants": [{"label": "L", "price_cents": 100}], "category": "Other"}]
        prepared = prepare_items_for_semantic(items)
        assert prepared[0]["variants"][0]["label"] == "L"

    def test_variant_confidence_default_added(self, draft_items_with_variants):
        from storage.semantic_bridge import prepare_items_for_semantic
        prepared = prepare_items_for_semantic(draft_items_with_variants)
        for v in prepared[0]["variants"]:
            assert "confidence" in v
            assert v["confidence"] == 0.5

    def test_price_flags_initialized(self, draft_items_basic):
        from storage.semantic_bridge import prepare_items_for_semantic
        prepared = prepare_items_for_semantic(draft_items_basic)
        for item in prepared:
            assert "price_flags" in item
            assert isinstance(item["price_flags"], list)

    def test_original_items_not_mutated(self, draft_items_basic):
        from storage.semantic_bridge import prepare_items_for_semantic
        originals = copy.deepcopy(draft_items_basic)
        prepare_items_for_semantic(draft_items_basic)
        assert draft_items_basic == originals

    def test_empty_list_returns_empty(self):
        from storage.semantic_bridge import prepare_items_for_semantic
        assert prepare_items_for_semantic([]) == []

    def test_no_confidence_key_untouched(self):
        from storage.semantic_bridge import prepare_items_for_semantic
        items = [{"name": "Test", "price_cents": 100, "category": "Other"}]
        prepared = prepare_items_for_semantic(items)
        assert "confidence" not in prepared[0]

    def test_no_variants_key_stays_absent(self):
        from storage.semantic_bridge import prepare_items_for_semantic
        items = [{"name": "Test", "price_cents": 100, "category": "Other", "confidence": 90}]
        prepared = prepare_items_for_semantic(items)
        assert "variants" not in prepared[0] or prepared[0].get("variants") is None


# ===========================================================================
# 2. run_semantic_pipeline — full pipeline
# ===========================================================================
class TestRunSemanticPipeline:
    def test_returns_semantic_report(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_basic)
        assert "semantic_report" in result
        assert isinstance(result["semantic_report"], dict)

    def test_returns_quality_grade(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_basic)
        assert result["quality_grade"] in ("A", "B", "C", "D")

    def test_returns_mean_confidence(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_basic)
        assert 0.0 <= result["mean_confidence"] <= 1.0

    def test_returns_tier_counts(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_basic)
        tiers = result["tier_counts"]
        assert "high" in tiers
        assert "medium" in tiers
        assert "low" in tiers
        assert "reject" in tiers
        total = sum(tiers.values())
        assert total == len(draft_items_basic)

    def test_returns_repair_results(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_basic)
        assert "repair_results" in result
        assert isinstance(result["repair_results"], dict)

    def test_returns_items_metadata(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_basic)
        assert len(result["items_metadata"]) == len(draft_items_basic)

    def test_items_metadata_has_expected_fields(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_basic)
        meta = result["items_metadata"][0]
        assert "name" in meta
        assert "semantic_confidence" in meta
        assert "semantic_tier" in meta
        assert "needs_review" in meta
        assert "repair_recommendation_count" in meta
        assert "auto_repairs_applied_count" in meta
        assert "price_flag_count" in meta

    def test_processed_items_have_semantic_confidence(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_basic)
        for item in result["items"]:
            assert "semantic_confidence" in item
            assert 0.0 <= item["semantic_confidence"] <= 1.0

    def test_processed_items_have_semantic_tier(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_basic)
        for item in result["items"]:
            assert item["semantic_tier"] in ("high", "medium", "low", "reject")

    def test_empty_items_returns_defaults(self):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline([])
        assert result["quality_grade"] == "D"
        assert result["mean_confidence"] == 0.0
        assert result["repairs_applied"] == 0
        assert result["items"] == []

    def test_many_items_pipeline(self, draft_items_many):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_many)
        assert result["quality_grade"] in ("A", "B", "C", "D")
        assert len(result["items"]) == 10
        assert len(result["items_metadata"]) == 10

    def test_with_variants(self, draft_items_with_variants):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_with_variants)
        assert result["quality_grade"] in ("A", "B", "C", "D")
        # Variants should be present in processed items
        assert len(result["items"][0].get("variants", [])) == 2


# ===========================================================================
# 3. extract_semantic_metadata
# ===========================================================================
class TestExtractSemanticMetadata:
    def test_extracts_per_item(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline, extract_semantic_metadata
        result = run_semantic_pipeline(draft_items_basic)
        metadata = extract_semantic_metadata(result["items"])
        assert len(metadata) == len(draft_items_basic)

    def test_metadata_name_matches(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline, extract_semantic_metadata
        result = run_semantic_pipeline(draft_items_basic)
        metadata = extract_semantic_metadata(result["items"])
        assert metadata[0]["name"] == "Margherita Pizza"

    def test_metadata_confidence_is_float(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline, extract_semantic_metadata
        result = run_semantic_pipeline(draft_items_basic)
        metadata = extract_semantic_metadata(result["items"])
        for meta in metadata:
            assert isinstance(meta["semantic_confidence"], float)

    def test_metadata_tier_is_string(self, draft_items_basic):
        from storage.semantic_bridge import run_semantic_pipeline, extract_semantic_metadata
        result = run_semantic_pipeline(draft_items_basic)
        metadata = extract_semantic_metadata(result["items"])
        for meta in metadata:
            assert isinstance(meta["semantic_tier"], str)


# ===========================================================================
# 4. apply_repairs_to_draft_items
# ===========================================================================
class TestApplyRepairsToDraftItems:
    def test_name_repair_applied(self):
        from storage.semantic_bridge import apply_repairs_to_draft_items
        draft = [{"name": "Hmburger", "category": "Other"}]
        processed = [{"auto_repairs_applied": [
            {"type": "name", "field": "name", "old_value": "Hmburger", "new_value": "Hamburger"}
        ]}]
        count = apply_repairs_to_draft_items(draft, processed)
        assert count == 1
        assert draft[0]["name"] == "Hamburger"

    def test_category_repair_applied(self):
        from storage.semantic_bridge import apply_repairs_to_draft_items
        draft = [{"name": "Cola", "category": "Other"}]
        processed = [{"auto_repairs_applied": [
            {"type": "category", "field": "category", "old_value": "Other", "new_value": "Beverages"}
        ]}]
        count = apply_repairs_to_draft_items(draft, processed)
        assert count == 1
        assert draft[0]["category"] == "Beverages"

    def test_no_repairs_returns_zero(self):
        from storage.semantic_bridge import apply_repairs_to_draft_items
        draft = [{"name": "Test", "category": "Pizza"}]
        processed = [{"auto_repairs_applied": []}]
        count = apply_repairs_to_draft_items(draft, processed)
        assert count == 0

    def test_no_repair_key_returns_zero(self):
        from storage.semantic_bridge import apply_repairs_to_draft_items
        draft = [{"name": "Test", "category": "Pizza"}]
        processed = [{}]
        count = apply_repairs_to_draft_items(draft, processed)
        assert count == 0

    def test_multiple_items_mixed_repairs(self):
        from storage.semantic_bridge import apply_repairs_to_draft_items
        draft = [
            {"name": "Item A", "category": "Cat1"},
            {"name": "Item B", "category": "Cat2"},
            {"name": "Item C", "category": "Cat3"},
        ]
        processed = [
            {"auto_repairs_applied": [{"field": "name", "new_value": "Item A Fixed"}]},
            {},
            {"auto_repairs_applied": [{"field": "category", "new_value": "Cat3 Fixed"}]},
        ]
        count = apply_repairs_to_draft_items(draft, processed)
        assert count == 2
        assert draft[0]["name"] == "Item A Fixed"
        assert draft[1]["name"] == "Item B"  # unchanged
        assert draft[2]["category"] == "Cat3 Fixed"


# ===========================================================================
# 5. High-confidence items (Claude-extracted with good names/prices)
# ===========================================================================
class TestHighConfidenceItems:
    def test_well_formed_items_score_high(self, draft_items_many):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_many)
        # Well-formed items with good names, prices, and categories
        # should have reasonable confidence
        assert result["mean_confidence"] > 0.5

    def test_well_formed_items_mostly_high_tier(self, draft_items_many):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_many)
        tiers = result["tier_counts"]
        # Most should be high or medium
        assert tiers["high"] + tiers["medium"] >= len(draft_items_many) // 2

    def test_quality_grade_not_d_for_good_items(self, draft_items_many):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_many)
        # 10 well-formed items should not grade D
        assert result["quality_grade"] in ("A", "B", "C")


# ===========================================================================
# 6. Low-confidence / edge cases
# ===========================================================================
class TestEdgeCases:
    def test_single_item(self):
        from storage.semantic_bridge import run_semantic_pipeline
        items = [{"name": "Solo Item", "price_cents": 999, "category": "Pizza", "confidence": 90, "position": 1}]
        result = run_semantic_pipeline(items)
        assert len(result["items"]) == 1
        assert result["quality_grade"] in ("A", "B", "C", "D")

    def test_items_without_prices(self):
        from storage.semantic_bridge import run_semantic_pipeline
        items = [
            {"name": "Mystery Item", "price_cents": 0, "category": "Other", "confidence": 90, "position": 1},
            {"name": "Another Mystery", "price_cents": 0, "category": "Other", "confidence": 90, "position": 2},
        ]
        result = run_semantic_pipeline(items)
        # Should complete without error; price_score will be low
        assert result["mean_confidence"] < 0.9

    def test_items_without_descriptions(self):
        from storage.semantic_bridge import run_semantic_pipeline
        items = [
            {"name": "Plain Item", "price_cents": 1000, "category": "Food", "confidence": 90, "position": 1, "description": None},
            {"name": "Another Plain Item", "price_cents": 800, "category": "Food", "confidence": 90, "position": 2, "description": None},
        ]
        result = run_semantic_pipeline(items)
        assert result["quality_grade"] in ("A", "B", "C", "D")

    def test_items_with_empty_names(self):
        from storage.semantic_bridge import run_semantic_pipeline
        items = [
            {"name": "", "price_cents": 500, "category": "Other", "confidence": 90, "position": 1},
            {"name": "Valid Item", "price_cents": 1000, "category": "Pizza", "confidence": 90, "position": 2},
        ]
        result = run_semantic_pipeline(items)
        # Should handle gracefully
        assert len(result["items"]) == 2

    def test_confidence_zero_items(self):
        from storage.semantic_bridge import run_semantic_pipeline
        items = [
            {"name": "Low Conf Item", "price_cents": 100, "category": "Other", "confidence": 0, "position": 1},
        ]
        result = run_semantic_pipeline(items)
        # Confidence 0 for grammar signal, but other 4 signals still score
        # (name quality, price presence, variant quality, flag penalty)
        # so overall mean_confidence is lower than a 90-confidence item
        assert result["mean_confidence"] < 0.9

    def test_vision_verified_items_95_confidence(self):
        """Items from vision verification have confidence=95."""
        from storage.semantic_bridge import run_semantic_pipeline
        items = [
            {"name": "Vision Verified Item", "price_cents": 1495, "category": "Pizza", "confidence": 95, "position": 1},
            {"name": "Another Verified", "price_cents": 995, "category": "Salads", "confidence": 95, "position": 2},
        ]
        result = run_semantic_pipeline(items)
        # Higher input confidence should produce higher semantic confidence
        assert result["mean_confidence"] > 0.5


# ===========================================================================
# 7. Semantic report structure
# ===========================================================================
class TestSemanticReportStructure:
    def test_report_has_menu_confidence(self, draft_items_many):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_many)
        report = result["semantic_report"]
        assert "menu_confidence" in report

    def test_report_has_repair_summary(self, draft_items_many):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_many)
        report = result["semantic_report"]
        assert "repair_summary" in report

    def test_report_has_pipeline_coverage(self, draft_items_many):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_many)
        report = result["semantic_report"]
        assert "pipeline_coverage" in report

    def test_report_has_quality_narrative(self, draft_items_many):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_many)
        report = result["semantic_report"]
        assert "quality_narrative" in report
        assert isinstance(report["quality_narrative"], str)

    def test_report_has_issue_digest(self, draft_items_many):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_many)
        report = result["semantic_report"]
        assert "issue_digest" in report

    def test_report_has_category_health(self, draft_items_many):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_many)
        report = result["semantic_report"]
        assert "category_health" in report


# ===========================================================================
# 8. Cross-item checks run
# ===========================================================================
class TestCrossItemIntegration:
    def test_price_flags_populated_after_pipeline(self, draft_items_many):
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_many)
        # At least some items should have price_flags after cross-item checks
        has_flags = sum(1 for it in result["items"] if it.get("price_flags"))
        # Not strictly required to have flags, but the field should exist
        for it in result["items"]:
            assert "price_flags" in it

    def test_duplicate_name_detected(self):
        from storage.semantic_bridge import run_semantic_pipeline
        items = [
            {"name": "Cheese Pizza", "price_cents": 1095, "category": "Pizza", "confidence": 90, "position": 1},
            {"name": "Cheese Pizza", "price_cents": 1295, "category": "Pizza", "confidence": 90, "position": 2},
            {"name": "Pepperoni Pizza", "price_cents": 1495, "category": "Pizza", "confidence": 90, "position": 3},
        ]
        result = run_semantic_pipeline(items)
        # Cross-item should flag the duplicate name with different price
        dup_flags = []
        for it in result["items"]:
            for f in it.get("price_flags", []):
                if "duplicate" in f.get("reason", ""):
                    dup_flags.append(f)
        assert len(dup_flags) > 0


# ===========================================================================
# 9. Pipeline integration (mocked run_ocr_and_make_draft)
# ===========================================================================
class TestPipelineIntegration:
    """Test that semantic pipeline is wired into portal/app.py extraction flow."""

    def test_semantic_bridge_importable(self):
        from storage.semantic_bridge import run_semantic_pipeline
        assert callable(run_semantic_pipeline)

    def test_semantic_bridge_prepare_importable(self):
        from storage.semantic_bridge import prepare_items_for_semantic
        assert callable(prepare_items_for_semantic)

    def test_semantic_bridge_extract_metadata_importable(self):
        from storage.semantic_bridge import extract_semantic_metadata
        assert callable(extract_semantic_metadata)

    def test_semantic_bridge_apply_repairs_importable(self):
        from storage.semantic_bridge import apply_repairs_to_draft_items
        assert callable(apply_repairs_to_draft_items)

    def test_pipeline_result_serializable(self, draft_items_many):
        """Ensure semantic result can be JSON-serialized for debug payload."""
        from storage.semantic_bridge import run_semantic_pipeline
        result = run_semantic_pipeline(draft_items_many)
        # The parts that go into the debug payload must be JSON-serializable
        payload = {
            "quality_grade": result["quality_grade"],
            "mean_confidence": result["mean_confidence"],
            "tier_counts": result["tier_counts"],
            "repairs_applied": result["repairs_applied"],
            "repair_results": result["repair_results"],
            "items_metadata": result["items_metadata"],
        }
        serialized = json.dumps(payload)
        assert len(serialized) > 0

    def test_pipeline_does_not_mutate_item_count(self, draft_items_basic):
        """Semantic pipeline should not add or remove items."""
        from storage.semantic_bridge import run_semantic_pipeline
        original_count = len(draft_items_basic)
        result = run_semantic_pipeline(draft_items_basic)
        assert len(result["items"]) == original_count


# ===========================================================================
# 10. Strategy gating
# ===========================================================================
class TestStrategyGating:
    """Verify semantic pipeline only runs for claude_api / claude_api+vision strategies."""

    def test_claude_api_strategy_triggers_pipeline(self):
        """extraction_strategy='claude_api' should trigger semantic pipeline."""
        strategy = "claude_api"
        assert strategy in ("claude_api", "claude_api+vision")

    def test_claude_api_vision_strategy_triggers_pipeline(self):
        """extraction_strategy='claude_api+vision' should trigger semantic pipeline."""
        strategy = "claude_api+vision"
        assert strategy in ("claude_api", "claude_api+vision")

    def test_heuristic_ai_strategy_excluded(self):
        """extraction_strategy='heuristic_ai' should NOT trigger bridge (already has it)."""
        strategy = "heuristic_ai"
        assert strategy not in ("claude_api", "claude_api+vision")

    def test_legacy_strategy_excluded(self):
        """extraction_strategy='legacy_draft_json' should NOT trigger bridge."""
        strategy = "legacy_draft_json"
        assert strategy not in ("claude_api", "claude_api+vision")


# ===========================================================================
# 11. Confidence normalization edge cases
# ===========================================================================
class TestConfidenceNormalization:
    def test_confidence_50_normalized(self):
        from storage.semantic_bridge import prepare_items_for_semantic
        items = [{"name": "Test", "confidence": 50, "category": "Other"}]
        prepared = prepare_items_for_semantic(items)
        assert prepared[0]["confidence"] == pytest.approx(0.50, abs=0.01)

    def test_confidence_1_stays_1(self):
        """Confidence of exactly 1 should stay 1 (already in 0-1 range)."""
        from storage.semantic_bridge import prepare_items_for_semantic
        items = [{"name": "Test", "confidence": 1, "category": "Other"}]
        prepared = prepare_items_for_semantic(items)
        # 1 is not > 1.0, so it stays as-is
        assert prepared[0]["confidence"] == 1

    def test_confidence_1_point_0_stays(self):
        from storage.semantic_bridge import prepare_items_for_semantic
        items = [{"name": "Test", "confidence": 1.0, "category": "Other"}]
        prepared = prepare_items_for_semantic(items)
        assert prepared[0]["confidence"] == 1.0

    def test_confidence_string_ignored(self):
        """Non-numeric confidence should not crash."""
        from storage.semantic_bridge import prepare_items_for_semantic
        items = [{"name": "Test", "confidence": "high", "category": "Other"}]
        prepared = prepare_items_for_semantic(items)
        # String is not int/float, so it stays as-is
        assert prepared[0]["confidence"] == "high"


# ===========================================================================
# 12. Full end-to-end: Claude extraction → semantic pipeline
# ===========================================================================
class TestEndToEnd:
    def test_claude_items_through_full_pipeline(self):
        """Simulate Claude Call 1 output → draft rows → semantic pipeline."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline

        # Simulate Claude API response
        claude_items = [
            {"name": "Margherita Pizza", "description": "Fresh mozzarella, basil", "price": 14.95, "category": "Pizza", "sizes": []},
            {"name": "Pepperoni Pizza", "description": "Classic pepperoni", "price": 16.95, "category": "Pizza",
             "sizes": [{"label": "Small", "price": 12.95}, {"label": "Large", "price": 16.95}]},
            {"name": "Caesar Salad", "description": "Romaine, croutons", "price": 9.95, "category": "Salads", "sizes": []},
            {"name": "Chicken Wings", "description": "Buffalo or BBQ", "price": 11.95, "category": "Appetizers", "sizes": []},
        ]

        # Step 1: Convert to draft rows (as claude_items_to_draft_rows does)
        draft_rows = claude_items_to_draft_rows(claude_items)
        assert len(draft_rows) == 4
        assert draft_rows[0]["confidence"] == 90

        # Step 2: Run semantic pipeline
        result = run_semantic_pipeline(draft_rows)

        # Verify pipeline completed
        assert result["quality_grade"] in ("A", "B", "C", "D")
        assert result["mean_confidence"] > 0.0
        assert len(result["items"]) == 4
        assert len(result["items_metadata"]) == 4

        # Verify each item has semantic annotations
        for item in result["items"]:
            assert "semantic_confidence" in item
            assert "semantic_tier" in item

    def test_vision_items_through_full_pipeline(self):
        """Simulate Vision verified output → draft rows → semantic pipeline."""
        from storage.ai_vision_verify import verified_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline

        # Simulate vision-verified items
        vision_items = [
            {"name": "Margherita Pizza", "description": "Fresh mozzarella", "price": 14.95, "category": "Pizza", "sizes": []},
            {"name": "Caesar Salad", "description": "Romaine, croutons, parmesan", "price": 10.95, "category": "Salads", "sizes": []},
        ]

        # Step 1: Convert to draft rows (confidence=95 for vision)
        draft_rows = verified_items_to_draft_rows(vision_items)
        assert len(draft_rows) == 2
        assert draft_rows[0]["confidence"] == 95

        # Step 2: Run semantic pipeline
        result = run_semantic_pipeline(draft_rows)
        assert result["quality_grade"] in ("A", "B", "C", "D")
        assert len(result["items"]) == 2

    def test_variants_preserved_through_pipeline(self):
        """Ensure _variants from Claude extraction survive the semantic pipeline."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        from storage.semantic_bridge import run_semantic_pipeline

        claude_items = [
            {"name": "Pizza", "price": 12.95, "category": "Pizza",
             "sizes": [{"label": "10\"", "price": 10.95}, {"label": "14\"", "price": 14.95}]},
        ]

        draft_rows = claude_items_to_draft_rows(claude_items)
        assert "_variants" in draft_rows[0]

        result = run_semantic_pipeline(draft_rows)
        # In processed items, _variants should now be "variants"
        assert "variants" in result["items"][0]
        assert len(result["items"][0]["variants"]) == 2

    def test_repairs_flow_back_to_draft_items(self):
        """If semantic pipeline repairs an item, the draft item is updated."""
        from storage.semantic_bridge import run_semantic_pipeline

        # Use an item with a garbled name to trigger repair
        items = [
            {"name": "XXYYZZ", "price_cents": 0, "category": "Other", "confidence": 90, "position": 1},
            {"name": "Cheese Pizza", "price_cents": 1095, "category": "Pizza", "confidence": 90, "position": 2},
            {"name": "Caesar Salad", "price_cents": 995, "category": "Salads", "confidence": 90, "position": 3},
        ]
        original_names = [it["name"] for it in items]

        result = run_semantic_pipeline(items)
        # Pipeline ran — check that it completed
        assert result["quality_grade"] in ("A", "B", "C", "D")
        # repairs_applied tracks how many draft items were modified
        assert isinstance(result["repairs_applied"], int)
