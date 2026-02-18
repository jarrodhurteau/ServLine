# storage/semantic_confidence.py
"""
Semantic Confidence Scoring -- Sprint 8.4 Days 66-70

Day 66: Computes a unified per-item semantic_confidence score (0.0-1.0) by
aggregating five independent signal sources:

  1. Grammar/parse confidence (or fallback item confidence for Path B)
  2. Name quality (length, garble detection, capitalization)
  3. Price presence (has at least one price)
  4. Variant quality (average variant confidence)
  5. Flag penalty (severity-weighted deductions from price_flags)

Day 67: Confidence tier classification + menu-level aggregation:
  - classify_confidence_tiers(items): per-item semantic_tier + needs_review
  - compute_menu_confidence_summary(items): menu-wide statistics, tier
    distribution, category breakdowns, and overall quality grade

Day 68: Confidence-driven auto-repair recommendations:
  - generate_repair_recommendations(items): per-item actionable repair
    suggestions driven by confidence signal breakdowns and existing flags
  - compute_repair_summary(items): menu-level repair statistics

Day 69: Auto-repair execution engine:
  - apply_auto_repairs(items): executes auto-fixable recommendations,
    updates item fields (name, category), records audit trail per item,
    returns summary of repairs applied

Day 70: Semantic quality report (Phase 8 capstone):
  - generate_semantic_report(items, repair_results): unified quality report
    combining menu confidence, repair summary, pipeline coverage,
    issue digest, category health ranking, and quality narrative

Polymorphic: works with both Path A (text_block dicts from ocr_pipeline)
and Path B (flat item dicts from ai_ocr_helper).

Entry functions:
  score_semantic_confidence(items)        — Step 9.2
  classify_confidence_tiers(items)        — Step 9.3
  generate_repair_recommendations(items)  — Step 9.4
  apply_auto_repairs(items)              — Step 9.5
  generate_semantic_report(items)        — Step 9.6

Pipeline placement: Steps 9.2-9.6, after check_cross_item_consistency (9.1).
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Signal weights (must sum to 1.0)
_W_GRAMMAR = 0.30
_W_NAME = 0.20
_W_PRICE = 0.20
_W_VARIANT = 0.15
_W_FLAGS = 0.15

# Flag penalty values per severity
_FLAG_PENALTY_WARN = 0.15
_FLAG_PENALTY_INFO = 0.05
_FLAG_PENALTY_AUTOFIX = 0.02

# Name quality thresholds
_NAME_SHORT_THRESHOLD = 3   # names < 3 chars get 0.3
_NAME_MEDIUM_THRESHOLD = 6  # names 3-5 chars get 0.6, 6+ get 1.0

# Price presence scores
_PRICE_PRESENT_SCORE = 1.0
_PRICE_ABSENT_SCORE = 0.3

# Neutral defaults when signal is unavailable
_DEFAULT_VARIANT_SCORE = 0.5
_DEFAULT_GRAMMAR_SCORE = 0.5

# Garble detection constants (mirrors menu_grammar._is_garble_run pattern)
_GARBLE_CHARS = set("secrnotvw")
_TRIPLE_REPEAT_RE = re.compile(r"(.)\1{2,}", re.IGNORECASE)

# Day 67: Confidence tier thresholds (match menu_grammar.confidence_tier)
_TIER_HIGH = 0.80
_TIER_MEDIUM = 0.60
_TIER_LOW = 0.40

# Quality grade thresholds (% of items in "high" tier)
_GRADE_A_THRESHOLD = 0.80  # ≥80% high
_GRADE_B_THRESHOLD = 0.60  # ≥60% high
_GRADE_C_THRESHOLD = 0.40  # ≥40% high

# Day 68: Repair recommendation constants
_REPAIR_THRESHOLD_NAME_QUALITY = 0.60
_REPAIR_THRESHOLD_PRICE_SCORE = 0.50
_REPAIR_THRESHOLD_VARIANT_SCORE = 0.50
_REPAIR_THRESHOLD_FLAG_PENALTY = 0.70

_TIER_TO_PRIORITY = {
    "reject": "critical",
    "low": "important",
    "medium": "suggested",
    "high": None,  # No recommendations for high-tier items
}

_PRIORITY_ORDER = {"critical": 0, "important": 1, "suggested": 2}

# Variant-related flag reasons for specific messages
_VARIANT_FLAG_MESSAGES = {
    "variant_price_inversion": "Size prices are out of order. Verify pricing.",
    "duplicate_variant": "Duplicate variant labels detected. Remove duplicates.",
    "zero_price_variant": "Some size variants have $0.00 price. Add missing prices.",
    "mixed_variant_kinds": "Item has mixed variant types. Verify variant structure.",
    "size_gap": "Missing intermediate size variant.",
    "grid_incomplete": "Variant grid is incomplete for this item.",
    "grid_count_outlier": "Variant count differs from similar items.",
    "cross_item_variant_count_outlier": "Fewer variants than category norm.",
    "cross_item_variant_label_mismatch": "Variant labels differ from category standard.",
}

# Minimum suggestion confidence to promote category suggestion to recommendation
_MIN_CATEGORY_SUGGESTION_CONFIDENCE = 0.40


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_name(item: Dict[str, Any]) -> str:
    """Extract item name from either Path A or Path B item dict."""
    grammar = item.get("grammar") or {}
    parsed = grammar.get("parsed_name")
    if parsed and parsed.strip():
        return parsed.strip()
    name = item.get("name")
    if name and name.strip():
        return name.strip()
    merged = item.get("merged_text") or ""
    # Strip price tokens for cleaner name
    merged = re.sub(r"\$?\d+\.\d{2}", "", merged)
    return merged.strip()


def _score_grammar(item: Dict[str, Any]) -> float:
    """Read grammar parse_confidence or fall back to item confidence."""
    grammar = item.get("grammar") or {}
    pc = grammar.get("parse_confidence")
    if pc is not None:
        return float(pc)
    # Path B fallback: item-level confidence
    conf = item.get("confidence")
    if conf is not None:
        return float(conf)
    return _DEFAULT_GRAMMAR_SCORE


def _is_name_garbled(name: str) -> bool:
    """Check if a name looks like OCR garble (not a real menu item name)."""
    alpha = [c for c in name if c.isalpha()]
    if len(alpha) < 4:
        return False
    garble_ratio = sum(1 for c in alpha if c.lower() in _GARBLE_CHARS) / len(alpha)
    unique_ratio = len(set(c.lower() for c in alpha)) / len(alpha)
    has_triple = bool(_TRIPLE_REPEAT_RE.search(name))
    signals = sum([
        has_triple,
        garble_ratio >= 0.60,
        unique_ratio <= 0.40,
    ])
    return signals >= 2


def _score_name_quality(item: Dict[str, Any]) -> float:
    """Score name quality based on length, garble, and capitalization."""
    name = _extract_name(item)
    if not name:
        return 0.1  # No name at all

    # Signal 1: Length
    if len(name) < _NAME_SHORT_THRESHOLD:
        length_score = 0.3
    elif len(name) < _NAME_MEDIUM_THRESHOLD:
        length_score = 0.6
    else:
        length_score = 1.0

    # Signal 2: Garble check
    if _is_name_garbled(name):
        garble_score = 0.2
    else:
        garble_score = 1.0

    # Signal 3: All-caps penalty (small ding -- OCR often produces all-caps)
    if name == name.upper() and len(name) > 2:
        caps_score = 0.9
    else:
        caps_score = 1.0

    # Combine as minimum of signals (weakest link)
    return min(length_score, garble_score, caps_score)


def _score_price_presence(item: Dict[str, Any]) -> float:
    """Score 1.0 if item has at least one positive price, 0.3 otherwise."""
    # Check variants for prices
    variants = item.get("variants") or []
    for v in variants:
        pc = v.get("price_cents", 0)
        if isinstance(pc, (int, float)) and pc > 0:
            return _PRICE_PRESENT_SCORE

    # Check price_candidates
    candidates = item.get("price_candidates") or []
    for cand in candidates:
        cents = cand.get("price_cents", 0)
        if isinstance(cents, (int, float)) and cents > 0:
            return _PRICE_PRESENT_SCORE
        val = cand.get("value", 0)
        if isinstance(val, (int, float)) and val > 0:
            return _PRICE_PRESENT_SCORE

    # Check direct price_cents on item
    direct = item.get("price_cents", 0)
    if isinstance(direct, (int, float)) and direct > 0:
        return _PRICE_PRESENT_SCORE

    return _PRICE_ABSENT_SCORE


def _score_variant_quality(item: Dict[str, Any]) -> float:
    """Average variant confidence; 0.5 default if no variants."""
    variants = item.get("variants") or []
    if not variants:
        return _DEFAULT_VARIANT_SCORE
    confs = [float(v.get("confidence", 0.5)) for v in variants]
    return sum(confs) / len(confs)


def _score_flag_penalty(item: Dict[str, Any]) -> float:
    """1.0 minus severity-weighted penalties from price_flags, capped at 0.0."""
    flags = item.get("price_flags") or []
    if not flags:
        return 1.0
    total_penalty = 0.0
    for flag in flags:
        severity = flag.get("severity", "info")
        if severity == "warn":
            total_penalty += _FLAG_PENALTY_WARN
        elif severity == "info":
            total_penalty += _FLAG_PENALTY_INFO
        elif severity == "auto_fix":
            total_penalty += _FLAG_PENALTY_AUTOFIX
        else:
            # Unknown severity — treat as info
            total_penalty += _FLAG_PENALTY_INFO
    return max(0.0, 1.0 - total_penalty)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def score_semantic_confidence(items: list) -> None:
    """Compute unified semantic_confidence for each item.

    Reads 5 signal sources (all read-only on upstream data),
    computes weighted aggregation, writes two fields per item:
      - semantic_confidence: float 0.0-1.0
      - semantic_confidence_details: dict with signal audit trail

    Pipeline placement: Step 9.2, after check_cross_item_consistency (9.1).
    Mutates items in place.
    """
    for item in items:
        grammar_raw = _score_grammar(item)
        name_raw = _score_name_quality(item)
        price_raw = _score_price_presence(item)
        variant_raw = _score_variant_quality(item)
        flag_raw = _score_flag_penalty(item)

        weighted_grammar = grammar_raw * _W_GRAMMAR
        weighted_name = name_raw * _W_NAME
        weighted_price = price_raw * _W_PRICE
        weighted_variant = variant_raw * _W_VARIANT
        weighted_flags = flag_raw * _W_FLAGS

        raw_score = (weighted_grammar + weighted_name + weighted_price
                     + weighted_variant + weighted_flags)
        final = max(0.0, min(1.0, round(raw_score, 4)))

        item["semantic_confidence"] = final
        item["semantic_confidence_details"] = {
            "grammar_score": round(grammar_raw, 4),
            "grammar_weight": _W_GRAMMAR,
            "grammar_weighted": round(weighted_grammar, 4),
            "name_quality_score": round(name_raw, 4),
            "name_quality_weight": _W_NAME,
            "name_quality_weighted": round(weighted_name, 4),
            "price_score": round(price_raw, 4),
            "price_weight": _W_PRICE,
            "price_weighted": round(weighted_price, 4),
            "variant_score": round(variant_raw, 4),
            "variant_weight": _W_VARIANT,
            "variant_weighted": round(weighted_variant, 4),
            "flag_penalty_score": round(flag_raw, 4),
            "flag_penalty_weight": _W_FLAGS,
            "flag_penalty_weighted": round(weighted_flags, 4),
            "final": final,
        }


# ---------------------------------------------------------------------------
# Day 67: Confidence tier classification
# ---------------------------------------------------------------------------

def _tier_for_score(score: float) -> str:
    """Map a semantic_confidence score to a tier label."""
    if score >= _TIER_HIGH:
        return "high"
    elif score >= _TIER_MEDIUM:
        return "medium"
    elif score >= _TIER_LOW:
        return "low"
    return "reject"


def classify_confidence_tiers(items: list) -> None:
    """Classify each item into a confidence tier and flag for review.

    Reads ``semantic_confidence`` (must be set by score_semantic_confidence
    first) and writes two new fields per item:
      - semantic_tier: str ("high" / "medium" / "low" / "reject")
      - needs_review: bool (True unless tier is "high")

    Pipeline placement: Step 9.3, immediately after score_semantic_confidence.
    Mutates items in place.
    """
    for item in items:
        sc = item.get("semantic_confidence")
        if sc is None:
            # Defensive: score wasn't computed yet
            item["semantic_tier"] = "reject"
            item["needs_review"] = True
            continue
        tier = _tier_for_score(float(sc))
        item["semantic_tier"] = tier
        item["needs_review"] = tier != "high"


# ---------------------------------------------------------------------------
# Day 67: Menu-level confidence aggregation
# ---------------------------------------------------------------------------

def _extract_category(item: Dict[str, Any]) -> str:
    """Get category name from item, defaulting to 'Uncategorized'."""
    cat = item.get("category")
    if cat and str(cat).strip():
        return str(cat).strip()
    return "Uncategorized"


def compute_menu_confidence_summary(items: list) -> Dict[str, Any]:
    """Compute menu-wide confidence statistics from scored + tiered items.

    Should be called after both score_semantic_confidence() and
    classify_confidence_tiers(). Returns a summary dict (does NOT mutate items).

    Returns dict with:
      - total_items: int
      - mean_confidence: float (rounded to 4 decimals)
      - median_confidence: float (rounded to 4 decimals)
      - stdev_confidence: float (rounded to 4 decimals, 0.0 if <2 items)
      - tier_counts: {high: int, medium: int, low: int, reject: int}
      - needs_review_count: int
      - quality_grade: str ("A" / "B" / "C" / "D")
      - category_summary: {category: {count, mean, needs_review_count, tier_counts}}
    """
    if not items:
        return {
            "total_items": 0,
            "mean_confidence": 0.0,
            "median_confidence": 0.0,
            "stdev_confidence": 0.0,
            "tier_counts": {"high": 0, "medium": 0, "low": 0, "reject": 0},
            "needs_review_count": 0,
            "quality_grade": "D",
            "category_summary": {},
        }

    scores = [float(it.get("semantic_confidence", 0.0)) for it in items]
    tiers = [it.get("semantic_tier", "reject") for it in items]
    reviews = [it.get("needs_review", True) for it in items]

    tier_counts = {"high": 0, "medium": 0, "low": 0, "reject": 0}
    for t in tiers:
        tier_counts[t] = tier_counts.get(t, 0) + 1

    total = len(items)
    mean_conf = statistics.mean(scores)
    median_conf = statistics.median(scores)
    stdev_conf = statistics.stdev(scores) if total >= 2 else 0.0
    review_count = sum(1 for r in reviews if r)

    # Quality grade based on % of items in "high" tier
    high_ratio = tier_counts["high"] / total
    if high_ratio >= _GRADE_A_THRESHOLD:
        grade = "A"
    elif high_ratio >= _GRADE_B_THRESHOLD:
        grade = "B"
    elif high_ratio >= _GRADE_C_THRESHOLD:
        grade = "C"
    else:
        grade = "D"

    # Category-level breakdown
    cat_data: Dict[str, Dict[str, Any]] = {}
    for item in items:
        cat = _extract_category(item)
        if cat not in cat_data:
            cat_data[cat] = {
                "scores": [],
                "needs_review_count": 0,
                "tier_counts": {"high": 0, "medium": 0, "low": 0, "reject": 0},
            }
        bucket = cat_data[cat]
        bucket["scores"].append(float(item.get("semantic_confidence", 0.0)))
        if item.get("needs_review", True):
            bucket["needs_review_count"] += 1
        tier = item.get("semantic_tier", "reject")
        bucket["tier_counts"][tier] = bucket["tier_counts"].get(tier, 0) + 1

    category_summary = {}
    for cat, bucket in sorted(cat_data.items()):
        cat_scores = bucket["scores"]
        category_summary[cat] = {
            "count": len(cat_scores),
            "mean": round(statistics.mean(cat_scores), 4),
            "needs_review_count": bucket["needs_review_count"],
            "tier_counts": bucket["tier_counts"],
        }

    return {
        "total_items": total,
        "mean_confidence": round(mean_conf, 4),
        "median_confidence": round(median_conf, 4),
        "stdev_confidence": round(stdev_conf, 4),
        "tier_counts": tier_counts,
        "needs_review_count": review_count,
        "quality_grade": grade,
        "category_summary": category_summary,
    }


# ---------------------------------------------------------------------------
# Day 68: Confidence-driven auto-repair recommendations
# ---------------------------------------------------------------------------

def _try_ocr_correction(name: str) -> Optional[str]:
    """Attempt OCR correction via menu_corrections; return corrected or None."""
    try:
        from .menu_corrections import correct_menu_item
        corrected = correct_menu_item(name)
        if corrected and corrected != name:
            return corrected
    except Exception:
        pass
    return None


def _build_name_recommendations(
    item: Dict[str, Any],
    priority: str,
    details: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build name-quality repair recommendations."""
    name_score = details.get("name_quality_score", 1.0)
    if name_score >= _REPAIR_THRESHOLD_NAME_QUALITY:
        return []

    name = _extract_name(item)
    recs: List[Dict[str, Any]] = []

    if not name:
        recs.append({
            "type": "garbled_name",
            "priority": priority,
            "message": "No item name found. Manual entry required.",
            "auto_fixable": False,
            "source_signal": "name_quality_score",
        })
        return recs

    # Check garble first (most severe)
    if _is_name_garbled(name):
        # Try OCR correction
        corrected = _try_ocr_correction(name)
        if corrected:
            recs.append({
                "type": "garbled_name",
                "priority": priority,
                "message": f"Name appears garbled: '{name}'. Suggested correction: '{corrected}'.",
                "auto_fixable": True,
                "proposed_fix": corrected,
                "source_signal": "name_quality_score",
            })
        else:
            recs.append({
                "type": "garbled_name",
                "priority": priority,
                "message": f"Name appears garbled: '{name}'. Manual rename recommended.",
                "auto_fixable": False,
                "source_signal": "name_quality_score",
            })
        return recs

    # Short name
    if len(name) < _NAME_SHORT_THRESHOLD:
        recs.append({
            "type": "name_quality",
            "priority": priority,
            "message": f"Name is very short ({len(name)} chars): '{name}'. Consider expanding or verifying.",
            "auto_fixable": False,
            "source_signal": "name_quality_score",
        })

    # All-caps (less severe — downgrade priority one step)
    if name == name.upper() and len(name) > 2:
        downgraded = "suggested" if priority in ("critical", "important") else priority
        recs.append({
            "type": "name_quality",
            "priority": downgraded,
            "message": f"Name is all-caps: '{name}'. Consider title-casing for readability.",
            "auto_fixable": True,
            "proposed_fix": name.title(),
            "source_signal": "name_quality_score",
        })

    # If nothing specific but score is still low, try OCR correction
    if not recs:
        corrected = _try_ocr_correction(name)
        if corrected:
            recs.append({
                "type": "name_quality",
                "priority": priority,
                "message": f"Possible OCR error in name: '{name}'. Suggested: '{corrected}'.",
                "auto_fixable": True,
                "proposed_fix": corrected,
                "source_signal": "name_quality_score",
            })

    return recs


def _build_price_recommendation(
    item: Dict[str, Any],
    priority: str,
    details: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Build price-missing repair recommendation."""
    price_score = details.get("price_score", 1.0)
    if price_score >= _REPAIR_THRESHOLD_PRICE_SCORE:
        return None
    return {
        "type": "price_missing",
        "priority": priority,
        "message": "No price found for this item. Manual price entry recommended.",
        "auto_fixable": False,
        "source_signal": "price_score",
    }


def _build_category_recommendation(
    item: Dict[str, Any],
    priority: str,
) -> Optional[Dict[str, Any]]:
    """Promote strongest category suggestion flag to repair recommendation."""
    flags = item.get("price_flags") or []
    best_flag = None
    best_conf = -1.0
    for flag in flags:
        if flag.get("reason") != "cross_item_category_suggestion":
            continue
        d = flag.get("details") or {}
        conf = d.get("suggestion_confidence", d.get("confidence", 0.0))
        if conf >= _MIN_CATEGORY_SUGGESTION_CONFIDENCE and conf > best_conf:
            best_conf = conf
            best_flag = flag

    if best_flag is None:
        return None

    d = best_flag.get("details") or {}
    current = d.get("current_category", "Unknown")
    suggested = d.get("suggested_category", "Unknown")
    signals = d.get("signals") or []
    signal_str = "; ".join(signals[:3]) if signals else "neighbor analysis"

    return {
        "type": "category_reassignment",
        "priority": priority,
        "message": f"Consider moving from '{current}' to '{suggested}' ({best_conf:.0%} confidence). Signals: {signal_str}.",
        "auto_fixable": True,
        "proposed_fix": {"category": suggested},
        "source_signal": "category_suggestion_flag",
    }


def _build_variant_recommendations(
    item: Dict[str, Any],
    priority: str,
    details: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build variant standardization recommendations."""
    variant_score = details.get("variant_score", 1.0)
    if variant_score >= _REPAIR_THRESHOLD_VARIANT_SCORE:
        return []

    flags = item.get("price_flags") or []
    recs: List[Dict[str, Any]] = []
    seen_types: set = set()

    for flag in flags:
        reason = flag.get("reason", "")
        msg = _VARIANT_FLAG_MESSAGES.get(reason)
        if msg and reason not in seen_types:
            seen_types.add(reason)
            recs.append({
                "type": "variant_standardization",
                "priority": priority,
                "message": msg,
                "auto_fixable": False,
                "source_signal": "variant_score",
            })

    # Generic fallback if no specific variant flags found
    if not recs:
        recs.append({
            "type": "variant_standardization",
            "priority": priority,
            "message": "Variant quality is low. Review variant labels and prices.",
            "auto_fixable": False,
            "source_signal": "variant_score",
        })

    return recs


def _build_flag_summary_recommendation(
    item: Dict[str, Any],
    priority: str,
    details: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Build flag-attention recommendation summarizing outstanding flags."""
    flag_score = details.get("flag_penalty_score", 1.0)
    if flag_score >= _REPAIR_THRESHOLD_FLAG_PENALTY:
        return None

    flags = item.get("price_flags") or []
    if not flags:
        return None

    n_warn = sum(1 for f in flags if f.get("severity") == "warn")
    n_info = sum(1 for f in flags if f.get("severity") == "info")
    # Collect unique warn reasons (top 3)
    warn_reasons = []
    seen = set()
    for f in flags:
        if f.get("severity") == "warn":
            r = f.get("reason", "unknown")
            if r not in seen:
                seen.add(r)
                warn_reasons.append(r)
            if len(warn_reasons) >= 3:
                break

    parts = []
    if n_warn:
        parts.append(f"{n_warn} warning(s)")
    if n_info:
        parts.append(f"{n_info} info flag(s)")
    count_str = " and ".join(parts)
    reason_str = ""
    if warn_reasons:
        reason_str = f" Top issues: {', '.join(warn_reasons)}."

    return {
        "type": "flag_attention",
        "priority": priority,
        "message": f"Item has {count_str} requiring attention.{reason_str}",
        "auto_fixable": False,
        "source_signal": "flag_penalty_score",
        "details": {
            "warn_count": n_warn,
            "info_count": n_info,
            "top_reasons": warn_reasons,
        },
    }


# ---------------------------------------------------------------------------
# Day 68: Public entry points
# ---------------------------------------------------------------------------

def generate_repair_recommendations(items: list) -> None:
    """Generate per-item repair recommendations based on confidence signals.

    Reads semantic_confidence_details, semantic_tier, needs_review, and
    price_flags.  Writes ``repair_recommendations`` list per item.

    Pipeline placement: Step 9.4, after classify_confidence_tiers (9.3).
    Mutates items in place.
    """
    for item in items:
        tier = item.get("semantic_tier", "reject")
        priority = _TIER_TO_PRIORITY.get(tier)

        # High-tier items: no recommendations needed
        if priority is None:
            item["repair_recommendations"] = []
            continue

        details = item.get("semantic_confidence_details") or {}
        recommendations: List[Dict[str, Any]] = []

        # 1. Name quality
        recommendations.extend(_build_name_recommendations(item, priority, details))

        # 2. Price missing
        price_rec = _build_price_recommendation(item, priority, details)
        if price_rec:
            recommendations.append(price_rec)

        # 3. Category suggestion (from flags)
        cat_rec = _build_category_recommendation(item, priority)
        if cat_rec:
            recommendations.append(cat_rec)

        # 4. Variant standardization
        recommendations.extend(_build_variant_recommendations(item, priority, details))

        # 5. Flag summary
        flag_rec = _build_flag_summary_recommendation(item, priority, details)
        if flag_rec:
            recommendations.append(flag_rec)

        # Sort by priority (critical first)
        recommendations.sort(key=lambda r: _PRIORITY_ORDER.get(r.get("priority", "suggested"), 2))

        item["repair_recommendations"] = recommendations


def compute_repair_summary(items: list) -> Dict[str, Any]:
    """Compute menu-level repair statistics from recommendation-annotated items.

    Should be called after generate_repair_recommendations().
    Returns a summary dict (does NOT mutate items).
    """
    if not items:
        return {
            "total_items": 0,
            "items_with_recommendations": 0,
            "total_recommendations": 0,
            "by_priority": {"critical": 0, "important": 0, "suggested": 0},
            "by_type": {},
            "auto_fixable_count": 0,
            "category_breakdown": {},
        }

    total_recs = 0
    items_with = 0
    by_priority: Dict[str, int] = {"critical": 0, "important": 0, "suggested": 0}
    by_type: Dict[str, int] = {}
    auto_fixable = 0
    cat_data: Dict[str, Dict[str, int]] = {}

    for item in items:
        recs = item.get("repair_recommendations") or []
        if recs:
            items_with += 1
        total_recs += len(recs)

        cat = _extract_category(item)
        if cat not in cat_data:
            cat_data[cat] = {"items_with_recommendations": 0, "recommendation_count": 0}
        if recs:
            cat_data[cat]["items_with_recommendations"] += 1
        cat_data[cat]["recommendation_count"] += len(recs)

        for rec in recs:
            p = rec.get("priority", "suggested")
            by_priority[p] = by_priority.get(p, 0) + 1
            t = rec.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
            if rec.get("auto_fixable"):
                auto_fixable += 1

    return {
        "total_items": len(items),
        "items_with_recommendations": items_with,
        "total_recommendations": total_recs,
        "by_priority": by_priority,
        "by_type": by_type,
        "auto_fixable_count": auto_fixable,
        "category_breakdown": {k: v for k, v in sorted(cat_data.items())},
    }


# ---------------------------------------------------------------------------
# Day 69: Auto-repair execution engine
# ---------------------------------------------------------------------------

def _apply_name_fix(item: Dict[str, Any], proposed_fix: str) -> List[Dict[str, Any]]:
    """Apply a name fix to item, returning audit trail entries."""
    if not proposed_fix or not isinstance(proposed_fix, str):
        return []

    repairs = []
    grammar = item.get("grammar")

    # Path A: update grammar.parsed_name
    if grammar and isinstance(grammar, dict) and grammar.get("parsed_name"):
        old_val = grammar["parsed_name"]
        if old_val != proposed_fix:
            grammar["parsed_name"] = proposed_fix
            repairs.append({
                "type": "name",
                "field": "grammar.parsed_name",
                "old_value": old_val,
                "new_value": proposed_fix,
            })

    # Path B: update item["name"]
    if "name" in item:
        old_val = item["name"]
        if old_val != proposed_fix:
            item["name"] = proposed_fix
            repairs.append({
                "type": "name",
                "field": "name",
                "old_value": old_val,
                "new_value": proposed_fix,
            })

    # If neither field existed, set name as fallback
    if not repairs:
        old_val = _extract_name(item)
        item["name"] = proposed_fix
        repairs.append({
            "type": "name",
            "field": "name",
            "old_value": old_val,
            "new_value": proposed_fix,
        })

    return repairs


def _apply_category_fix(item: Dict[str, Any], proposed_fix: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Apply a category fix to item, returning audit trail entries."""
    if not isinstance(proposed_fix, dict) or "category" not in proposed_fix:
        return []

    new_cat = proposed_fix["category"]
    old_cat = item.get("category", "Uncategorized")
    if old_cat == new_cat:
        return []

    item["category"] = new_cat
    return [{
        "type": "category",
        "field": "category",
        "old_value": old_cat,
        "new_value": new_cat,
    }]


def apply_auto_repairs(items: list) -> Dict[str, Any]:
    """Execute auto-fixable repair recommendations on items.

    Walks each item's ``repair_recommendations``.  For each rec with
    ``auto_fixable: True``, applies the ``proposed_fix`` to the item's
    fields and records an audit trail.

    Per-item writes:
      - ``auto_repairs_applied``: list of {type, field, old_value, new_value}
      - Sets ``rec["applied"] = True`` on executed recommendations

    Returns summary dict:
      - total_items_repaired: int
      - repairs_applied: int
      - by_type: {name: int, category: int}

    Pipeline placement: Step 9.5, after generate_repair_recommendations (9.4).
    Mutates items in place.

    Note: Caller should re-run score_semantic_confidence() and
    classify_confidence_tiers() after this to reflect repaired quality.
    """
    total_items_repaired = 0
    total_repairs = 0
    by_type: Dict[str, int] = {}

    for item in items:
        recs = item.get("repair_recommendations") or []
        item_repairs: List[Dict[str, Any]] = []

        for rec in recs:
            if not rec.get("auto_fixable"):
                continue
            if rec.get("applied"):
                continue  # Already applied (idempotency)

            proposed = rec.get("proposed_fix")
            if proposed is None:
                continue

            rec_type = rec.get("type", "unknown")
            repairs: List[Dict[str, Any]] = []

            if rec_type in ("garbled_name", "name_quality") and isinstance(proposed, str):
                repairs = _apply_name_fix(item, proposed)
            elif rec_type == "category_reassignment" and isinstance(proposed, dict):
                repairs = _apply_category_fix(item, proposed)

            if repairs:
                rec["applied"] = True
                item_repairs.extend(repairs)

        item["auto_repairs_applied"] = item_repairs
        if item_repairs:
            total_items_repaired += 1
            total_repairs += len(item_repairs)
            for r in item_repairs:
                rtype = r["type"]
                by_type[rtype] = by_type.get(rtype, 0) + 1

    return {
        "total_items_repaired": total_items_repaired,
        "repairs_applied": total_repairs,
        "by_type": by_type,
    }


# ---------------------------------------------------------------------------
# Day 70: Semantic Quality Report (Phase 8 Capstone)
# ---------------------------------------------------------------------------

_WORST_ITEMS_LIMIT = 10
_TOP_ISSUES_LIMIT = 6
_COMMON_FLAGS_LIMIT = 8


def _pipeline_coverage(items: list) -> Dict[str, Any]:
    """Compute what percentage of items have each pipeline signal."""
    total = len(items)
    if total == 0:
        return {}

    checks = {
        "has_grammar": lambda it: bool(it.get("grammar")),
        "has_semantic_confidence": lambda it: it.get("semantic_confidence") is not None,
        "has_semantic_tier": lambda it: it.get("semantic_tier") is not None,
        "has_price_flags": lambda it: bool(it.get("price_flags")),
        "has_variants": lambda it: bool(it.get("variants")),
        "has_repair_recommendations": lambda it: bool(it.get("repair_recommendations")),
        "has_auto_repairs": lambda it: bool(it.get("auto_repairs_applied")),
    }

    coverage: Dict[str, Any] = {}
    for key, check_fn in checks.items():
        count = sum(1 for it in items if check_fn(it))
        coverage[key] = {
            "count": count,
            "pct": round(count / total, 4),
        }
    return coverage


def _issue_digest(items: list) -> Dict[str, Any]:
    """Build top-issues, worst-items, and common-flags digests."""
    # --- Top recommendation types ---
    type_counts: Dict[str, int] = {}
    for item in items:
        for rec in (item.get("repair_recommendations") or []):
            t = rec.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

    total_recs = sum(type_counts.values())
    top_issues = []
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        top_issues.append({
            "type": t,
            "count": c,
            "pct": round(c / total_recs, 4) if total_recs else 0.0,
        })
    top_issues = top_issues[:_TOP_ISSUES_LIMIT]

    # --- Worst items (lowest semantic_confidence) ---
    scored = [
        it for it in items
        if it.get("semantic_confidence") is not None
    ]
    scored.sort(key=lambda it: it.get("semantic_confidence", 0.0))
    worst_items = []
    for it in scored[:_WORST_ITEMS_LIMIT]:
        rec_count = len(it.get("repair_recommendations") or [])
        worst_items.append({
            "name": _extract_name(it),
            "confidence": it.get("semantic_confidence", 0.0),
            "tier": it.get("semantic_tier", "reject"),
            "category": _extract_category(it),
            "issue_count": rec_count,
        })

    # --- Common price_flags reasons ---
    reason_counts: Dict[str, Dict[str, Any]] = {}
    for item in items:
        for flag in (item.get("price_flags") or []):
            reason = flag.get("reason", "unknown")
            severity = flag.get("severity", "info")
            if reason not in reason_counts:
                reason_counts[reason] = {"count": 0, "severity": severity}
            reason_counts[reason]["count"] += 1

    common_flags = []
    for reason, data in sorted(reason_counts.items(), key=lambda x: -x[1]["count"]):
        common_flags.append({
            "reason": reason,
            "count": data["count"],
            "severity": data["severity"],
        })
    common_flags = common_flags[:_COMMON_FLAGS_LIMIT]

    return {
        "top_issues": top_issues,
        "worst_items": worst_items,
        "common_flags": common_flags,
    }


def _category_health(items: list) -> List[Dict[str, Any]]:
    """Rank categories by mean confidence, worst first."""
    cat_data: Dict[str, Dict[str, Any]] = {}
    for item in items:
        cat = _extract_category(item)
        if cat not in cat_data:
            cat_data[cat] = {"scores": [], "needs_review": 0, "total": 0}
        sc = item.get("semantic_confidence", 0.0)
        cat_data[cat]["scores"].append(float(sc))
        cat_data[cat]["total"] += 1
        if item.get("needs_review", True):
            cat_data[cat]["needs_review"] += 1

    health: List[Dict[str, Any]] = []
    for cat, data in cat_data.items():
        scores = data["scores"]
        count = data["total"]
        mean = statistics.mean(scores) if scores else 0.0
        nr_pct = data["needs_review"] / count if count else 0.0
        # Per-category grade using same thresholds
        high_count = sum(1 for s in scores if s >= _TIER_HIGH)
        high_ratio = high_count / count if count else 0.0
        if high_ratio >= _GRADE_A_THRESHOLD:
            grade = "A"
        elif high_ratio >= _GRADE_B_THRESHOLD:
            grade = "B"
        elif high_ratio >= _GRADE_C_THRESHOLD:
            grade = "C"
        else:
            grade = "D"
        health.append({
            "category": cat,
            "count": count,
            "mean_confidence": round(mean, 4),
            "needs_review_pct": round(nr_pct, 4),
            "grade": grade,
        })

    # Sort worst first (lowest mean confidence)
    health.sort(key=lambda h: h["mean_confidence"])
    return health


def _quality_narrative(
    menu_summary: Dict[str, Any],
    repair_summary: Dict[str, Any],
    repair_results: Optional[Dict[str, Any]],
    category_health: List[Dict[str, Any]],
) -> str:
    """Build a human-readable quality assessment narrative."""
    total = menu_summary.get("total_items", 0)
    if total == 0:
        return "No items to evaluate."

    grade = menu_summary.get("quality_grade", "D")
    mean_conf = menu_summary.get("mean_confidence", 0.0)
    tier_counts = menu_summary.get("tier_counts", {})
    high = tier_counts.get("high", 0)
    reject = tier_counts.get("reject", 0)
    review = menu_summary.get("needs_review_count", 0)

    parts: List[str] = []

    # Grade assessment
    grade_desc = {
        "A": "Excellent",
        "B": "Good",
        "C": "Fair",
        "D": "Poor",
    }
    parts.append(
        f"Menu quality grade: {grade} ({grade_desc.get(grade, 'Unknown')}). "
        f"{total} items with {mean_conf:.0%} average confidence."
    )

    # Tier breakdown
    parts.append(
        f"{high} items high-confidence, {reject} rejected, {review} need review."
    )

    # Repairs
    total_recs = repair_summary.get("total_recommendations", 0)
    auto_fixable = repair_summary.get("auto_fixable_count", 0)
    if total_recs > 0:
        parts.append(
            f"{total_recs} repair recommendations generated ({auto_fixable} auto-fixable)."
        )

    if repair_results:
        applied = repair_results.get("repairs_applied", 0)
        if applied > 0:
            parts.append(f"{applied} auto-repairs applied.")

    # Weakest category
    if category_health:
        worst_cat = category_health[0]
        if worst_cat["mean_confidence"] < _TIER_MEDIUM:
            parts.append(
                f"Weakest category: {worst_cat['category']} "
                f"({worst_cat['mean_confidence']:.0%} avg, grade {worst_cat['grade']})."
            )

    return " ".join(parts)


def generate_semantic_report(
    items: list,
    repair_results: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate comprehensive semantic quality report for a menu.

    Combines all Phase 8 Sprint 8.4 signals into a unified report:
      - Menu confidence summary (Day 67)
      - Repair summary (Day 68)
      - Auto-repair results (Day 69)
      - Pipeline coverage metrics
      - Item-level issue digest
      - Category health ranking
      - Quality narrative

    Args:
        items: List of item dicts (after full pipeline processing).
        repair_results: Optional dict from apply_auto_repairs() return value.

    Returns report dict (does NOT mutate items).
    """
    menu_conf = compute_menu_confidence_summary(items)
    repair_summ = compute_repair_summary(items)
    coverage = _pipeline_coverage(items)
    digest = _issue_digest(items)
    cat_health = _category_health(items)
    narrative = _quality_narrative(menu_conf, repair_summ, repair_results, cat_health)

    return {
        "menu_confidence": menu_conf,
        "repair_summary": repair_summ,
        "auto_repair_results": repair_results or {
            "total_items_repaired": 0,
            "repairs_applied": 0,
            "by_type": {},
        },
        "pipeline_coverage": coverage,
        "issue_digest": digest,
        "category_health": cat_health,
        "quality_narrative": narrative,
    }
