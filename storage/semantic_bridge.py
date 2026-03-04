# storage/semantic_bridge.py
"""
Semantic Pipeline Bridge — Day 98, Sprint 11.1.

Connects Claude-extracted draft items (from Call 1 / Call 2) to the Phase 8
semantic pipeline (cross-item checks, confidence scoring, tiers, repair
recommendations, auto-repairs, and quality reports).

The semantic pipeline was built for Path A (ocr_pipeline text_blocks) and
Path B (ai_ocr_helper items).  Draft items from Claude extraction use a
slightly different format (confidence 0-100, _variants prefix, no
price_flags).  This bridge converts, runs, and extracts results.

Usage:
    from storage.semantic_bridge import run_semantic_pipeline

    result = run_semantic_pipeline(draft_items)
    # result = {
    #     "semantic_report": {...},       # full Phase 8 quality report
    #     "repair_results":  {...},       # auto-repair summary
    #     "items":           [...],       # items with semantic annotations
    #     "tier_counts":     {...},       # high/medium/low/reject counts
    #     "mean_confidence": float,       # 0.0-1.0
    #     "quality_grade":   str,         # A/B/C/D
    # }
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prepare draft items for semantic pipeline
# ---------------------------------------------------------------------------
def prepare_items_for_semantic(
    draft_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert draft_items (from Claude extraction) to semantic-pipeline format.

    Changes made (on deep copies — original items are NOT mutated):
      - ``confidence`` normalized from 0-100 int to 0.0-1.0 float
      - ``_variants`` renamed to ``variants`` (semantic pipeline reads ``variants``)
      - ``price_flags`` initialized to ``[]`` if missing
      - ``variants[].confidence`` set to 0.5 default if missing (variant quality scorer needs it)
    """
    result: List[Dict[str, Any]] = []
    for item in draft_items:
        it = copy.deepcopy(item)

        # Normalize confidence: draft rows use 0-100, semantic pipeline expects 0-1
        conf = it.get("confidence")
        if conf is not None and isinstance(conf, (int, float)) and conf > 1.0:
            it["confidence"] = round(conf / 100.0, 4)

        # Rename _variants → variants (semantic pipeline reads "variants")
        if "_variants" in it and "variants" not in it:
            it["variants"] = it.pop("_variants")

        # Ensure variant confidence exists for variant quality scoring
        for v in (it.get("variants") or []):
            v.setdefault("confidence", 0.5)

        # price_flags will be initialized by cross_item.check_cross_item_consistency
        # but set it explicitly for safety
        it.setdefault("price_flags", [])

        result.append(it)

    return result


# ---------------------------------------------------------------------------
# Extract semantic results back into draft-item-compatible format
# ---------------------------------------------------------------------------
def extract_semantic_metadata(
    processed_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Extract semantic annotations from processed items into a summary list.

    Returns a list of dicts (one per item) with the semantic fields that
    the debug payload / UI can use:
      - semantic_confidence, semantic_tier, needs_review
      - repair_recommendations (count + types)
      - auto_repairs_applied (count)
      - price_flags (count)
    """
    result: List[Dict[str, Any]] = []
    for it in processed_items:
        meta: Dict[str, Any] = {
            "name": it.get("name", ""),
            "semantic_confidence": it.get("semantic_confidence"),
            "semantic_tier": it.get("semantic_tier"),
            "needs_review": it.get("needs_review", False),
            "repair_recommendation_count": len(it.get("repair_recommendations") or []),
            "auto_repairs_applied_count": len(it.get("auto_repairs_applied") or []),
            "price_flag_count": len(it.get("price_flags") or []),
        }
        result.append(meta)
    return result


# ---------------------------------------------------------------------------
# Apply semantic repairs back to draft items
# ---------------------------------------------------------------------------
def apply_repairs_to_draft_items(
    draft_items: List[Dict[str, Any]],
    processed_items: List[Dict[str, Any]],
) -> int:
    """Copy name/category repairs from processed items back to draft items.

    Matches by position (items are 1:1, same order).
    Returns the number of items that had repairs applied.
    """
    repaired = 0
    for draft, proc in zip(draft_items, processed_items):
        repairs = proc.get("auto_repairs_applied") or []
        if not repairs:
            continue
        for repair in repairs:
            field = repair.get("field")
            new_val = repair.get("new_value")
            if field == "name" and new_val:
                draft["name"] = new_val
            elif field == "category" and new_val:
                draft["category"] = new_val
        repaired += 1
    return repaired


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_semantic_pipeline(
    draft_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Run the full Phase 8 semantic pipeline on Claude-extracted draft items.

    Steps executed:
      1. Prepare items (normalize format)
      2. Cross-item consistency checks (Step 9.1)
      3. Semantic confidence scoring (Step 9.2)
      4. Confidence tier classification (Step 9.3)
      5. Repair recommendations (Step 9.4)
      6. Auto-repair execution (Step 9.5)
      7. Re-score + re-classify after repairs
      8. Generate semantic report (Step 9.6)

    Returns dict with:
      - semantic_report: full Phase 8 quality report
      - repair_results: auto-repair summary
      - items: processed items with all semantic annotations
      - items_metadata: per-item semantic summary for debug payload
      - tier_counts: {high, medium, low, reject}
      - mean_confidence: float 0.0-1.0
      - quality_grade: str A/B/C/D
      - repairs_applied: int count of items repaired
    """
    if not draft_items:
        return {
            "semantic_report": {},
            "repair_results": {},
            "items": [],
            "items_metadata": [],
            "tier_counts": {"high": 0, "medium": 0, "low": 0, "reject": 0},
            "mean_confidence": 0.0,
            "quality_grade": "D",
            "repairs_applied": 0,
        }

    # Lazy imports to avoid circular dependencies
    from .cross_item import check_cross_item_consistency
    from .semantic_confidence import (
        score_semantic_confidence,
        classify_confidence_tiers,
        compute_menu_confidence_summary,
        generate_repair_recommendations,
        apply_auto_repairs,
        generate_semantic_report,
    )

    # Step 0: Prepare items for semantic pipeline
    items = prepare_items_for_semantic(draft_items)

    # Step 9.1: Cross-item consistency checks
    check_cross_item_consistency(items)

    # Step 9.2: Semantic confidence scoring
    score_semantic_confidence(items)

    # Step 9.3: Confidence tier classification
    classify_confidence_tiers(items)

    # Step 9.4: Repair recommendations
    generate_repair_recommendations(items)

    # Step 9.5: Auto-repair execution
    repair_results = apply_auto_repairs(items)

    # Re-score after repairs
    score_semantic_confidence(items)
    classify_confidence_tiers(items)

    # Step 9.6: Semantic report
    semantic_report = generate_semantic_report(items, repair_results)

    # Apply repairs back to original draft items
    repairs_applied = apply_repairs_to_draft_items(draft_items, items)

    # Extract summary
    summary = compute_menu_confidence_summary(items)

    # Per-item metadata for debug payload
    items_metadata = extract_semantic_metadata(items)

    return {
        "semantic_report": semantic_report,
        "repair_results": repair_results,
        "items": items,
        "items_metadata": items_metadata,
        "tier_counts": summary.get("tier_counts", {}),
        "mean_confidence": summary.get("mean_confidence", 0.0),
        "quality_grade": summary.get("quality_grade", "D"),
        "repairs_applied": repairs_applied,
    }