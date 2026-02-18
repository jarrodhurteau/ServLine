"""
Day 68: Sprint 8.4 -- Confidence-Driven Auto-Repair Recommendations

Tests the repair recommendation generation in storage/semantic_confidence.py:
  1. Name quality recommendations
  2. Price missing recommendations
  3. Category suggestion recommendations
  4. Variant standardization recommendations
  5. Flag summary recommendations
  6. Priority system and tier mapping
  7. Menu-level repair summary
  8. Pipeline integration and edge cases

Run: python tests/test_day68_repair_recommendations.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.semantic_confidence import (
    score_semantic_confidence,
    classify_confidence_tiers,
    generate_repair_recommendations,
    compute_repair_summary,
    _REPAIR_THRESHOLD_NAME_QUALITY,
    _REPAIR_THRESHOLD_PRICE_SCORE,
    _REPAIR_THRESHOLD_VARIANT_SCORE,
    _REPAIR_THRESHOLD_FLAG_PENALTY,
    _TIER_TO_PRIORITY,
    _MIN_CATEGORY_SUGGESTION_CONFIDENCE,
)


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

@dataclass
class TestReport:
    total: int = 0
    passed: int = 0
    failures: List[str] = field(default_factory=list)

    def ok(self, name: str) -> None:
        self.total += 1
        self.passed += 1
        print(f"  PASS  {name}")

    def fail(self, name: str, msg: str = "") -> None:
        self.total += 1
        self.failures.append(f"FAIL: {name} -- {msg}")
        print(f"  FAIL  {name}  ({msg})")

    def check(self, name: str, condition: bool, msg: str = "") -> None:
        if condition:
            self.ok(name)
        else:
            self.fail(name, msg)


def _make_item(
    text: str = "Test Item  10.99",
    grammar: Optional[Dict] = None,
    variants: Optional[List[Dict]] = None,
    category: Optional[str] = None,
    price_flags: Optional[List[Dict]] = None,
    name: Optional[str] = None,
    confidence: Optional[float] = None,
    price_candidates: Optional[List[Dict]] = None,
    price_cents: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a minimal item dict for testing."""
    item: Dict[str, Any] = {
        "merged_text": text,
        "bbox": [0, 0, 100, 20],
        "lines": [{"text": text, "bbox": [0, 0, 100, 20], "words": []}],
    }
    if grammar is not None:
        item["grammar"] = grammar
    if variants is not None:
        item["variants"] = variants
    if category is not None:
        item["category"] = category
    if price_flags is not None:
        item["price_flags"] = price_flags
    if name is not None:
        item["name"] = name
    if confidence is not None:
        item["confidence"] = confidence
    if price_candidates is not None:
        item["price_candidates"] = price_candidates
    if price_cents is not None:
        item["price_cents"] = price_cents
    return item


def _make_tiered_item(
    tier: str = "low",
    name_quality_score: float = 1.0,
    price_score: float = 1.0,
    variant_score: float = 0.5,
    flag_penalty_score: float = 1.0,
    grammar_score: float = 0.5,
    price_flags: Optional[List[Dict]] = None,
    name: Optional[str] = None,
    category: Optional[str] = None,
    grammar: Optional[Dict] = None,
    text: str = "Test Item  10.99",
    variants: Optional[List[Dict]] = None,
    price_cents: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a pre-scored + tiered item for recommendation tests."""
    item: Dict[str, Any] = {
        "merged_text": text,
        "bbox": [0, 0, 100, 20],
        "lines": [{"text": text, "bbox": [0, 0, 100, 20], "words": []}],
        "semantic_tier": tier,
        "needs_review": tier != "high",
        "semantic_confidence": 0.50,
        "semantic_confidence_details": {
            "grammar_score": grammar_score,
            "name_quality_score": name_quality_score,
            "price_score": price_score,
            "variant_score": variant_score,
            "flag_penalty_score": flag_penalty_score,
            "final": 0.50,
        },
    }
    if price_flags is not None:
        item["price_flags"] = price_flags
    if name is not None:
        item["name"] = name
    if category is not None:
        item["category"] = category
    if grammar is not None:
        item["grammar"] = grammar
    if variants is not None:
        item["variants"] = variants
    if price_cents is not None:
        item["price_cents"] = price_cents
    return item


def _make_flag(
    reason: str = "test_flag",
    severity: str = "warn",
    details: Optional[Dict] = None,
) -> Dict[str, Any]:
    return {
        "severity": severity,
        "reason": reason,
        "details": details or {},
    }


def _has_rec_type(recs: list, rec_type: str) -> bool:
    return any(r.get("type") == rec_type for r in recs)


def _get_rec(recs: list, rec_type: str) -> Optional[Dict]:
    for r in recs:
        if r.get("type") == rec_type:
            return r
    return None


def _get_recs(recs: list, rec_type: str) -> List[Dict]:
    return [r for r in recs if r.get("type") == rec_type]


# ---------------------------------------------------------------------------
# Group 1: Name quality recommendations
# ---------------------------------------------------------------------------

def run_name_quality_tests(r: TestReport) -> None:
    print("\n--- Group 1: Name quality recommendations ---")

    # 1.1: High name_quality_score -> no name recommendation
    item = _make_tiered_item(tier="low", name_quality_score=0.80)
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("1.1 high name score -> no name rec",
            not _has_rec_type(recs, "name_quality") and not _has_rec_type(recs, "garbled_name"),
            f"got {[r['type'] for r in recs]}")

    # 1.2: Exactly at threshold -> no recommendation
    item = _make_tiered_item(tier="low", name_quality_score=0.60)
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("1.2 name score at threshold -> no name rec",
            not _has_rec_type(recs, "name_quality") and not _has_rec_type(recs, "garbled_name"),
            f"got {[r['type'] for r in recs]}")

    # 1.3: Just below threshold with short name -> recommendation
    item = _make_tiered_item(
        tier="low",
        name_quality_score=0.30,
        grammar={"parsed_name": "XY"},
        text="XY  5.99",
    )
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    name_recs = _get_recs(recs, "name_quality") + _get_recs(recs, "garbled_name")
    r.check("1.3 name score below threshold -> name rec",
            len(name_recs) >= 1,
            f"got {[r['type'] for r in recs]}")

    # 1.4: Garbled name -> garbled_name type
    item = _make_tiered_item(
        tier="reject",
        name_quality_score=0.20,
        grammar={"parsed_name": "eeeecccrrrvvvw"},
        text="eeeecccrrrvvvw  5.99",
    )
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("1.4 garbled name -> garbled_name type",
            _has_rec_type(recs, "garbled_name"),
            f"got {[r['type'] for r in recs]}")

    # 1.5: Garbled name message contains the name
    rec = _get_rec(recs, "garbled_name")
    r.check("1.5 garbled name message has name",
            rec is not None and "eeeecccrrrvvvw" in rec.get("message", ""),
            f"msg={rec.get('message') if rec else 'None'}")

    # 1.6: Short name (< 3 chars) -> name_quality
    item = _make_tiered_item(
        tier="low",
        name_quality_score=0.30,
        grammar={"parsed_name": "AB"},
        text="AB  5.99",
    )
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    rec = _get_rec(recs, "name_quality")
    r.check("1.6 short name -> name_quality rec",
            rec is not None and "short" in rec.get("message", "").lower(),
            f"got {[r['type'] for r in recs]}")

    # 1.7: All-caps name -> auto_fixable with title case
    item = _make_tiered_item(
        tier="medium",
        name_quality_score=0.59,
        grammar={"parsed_name": "CHICKEN WINGS"},
        text="CHICKEN WINGS  8.99",
    )
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    caps_recs = [r for r in recs if r.get("type") == "name_quality" and r.get("auto_fixable")]
    r.check("1.7 all-caps -> auto_fixable name_quality",
            len(caps_recs) >= 1,
            f"got {recs}")

    # 1.8: All-caps proposed fix is title-cased
    if caps_recs:
        r.check("1.8 all-caps proposed_fix is title case",
                caps_recs[0].get("proposed_fix") == "Chicken Wings",
                f"got {caps_recs[0].get('proposed_fix')}")
    else:
        r.fail("1.8 all-caps proposed_fix is title case", "no caps rec found")

    # 1.9: All-caps priority downgraded (medium -> suggested stays suggested)
    if caps_recs:
        r.check("1.9 all-caps priority stays suggested for medium tier",
                caps_recs[0].get("priority") == "suggested",
                f"got {caps_recs[0].get('priority')}")
    else:
        r.fail("1.9 all-caps priority downgrade", "no caps rec found")

    # 1.10: No name at all -> garbled_name
    item = _make_tiered_item(
        tier="reject",
        name_quality_score=0.10,
        text="",
    )
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("1.10 no name -> garbled_name rec",
            _has_rec_type(recs, "garbled_name"),
            f"got {[r['type'] for r in recs]}")

    # 1.11: Path B item with name quality issue
    item = _make_tiered_item(
        tier="low",
        name_quality_score=0.30,
        name="XY",
        text="XY  3.99",
    )
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    name_recs = _get_recs(recs, "name_quality") + _get_recs(recs, "garbled_name")
    r.check("1.11 path B short name -> rec",
            len(name_recs) >= 1,
            f"got {[r['type'] for r in recs]}")

    # 1.12: source_signal is correct
    for rec in name_recs:
        r.check("1.12 name rec source_signal",
                rec.get("source_signal") == "name_quality_score",
                f"got {rec.get('source_signal')}")
        break


# ---------------------------------------------------------------------------
# Group 2: Price missing recommendations
# ---------------------------------------------------------------------------

def run_price_missing_tests(r: TestReport) -> None:
    print("\n--- Group 2: Price missing recommendations ---")

    # 2.1: Item with price -> no recommendation
    item = _make_tiered_item(tier="low", price_score=1.0)
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("2.1 has price -> no price rec",
            not _has_rec_type(recs, "price_missing"),
            f"got {[r['type'] for r in recs]}")

    # 2.2: Price at threshold -> no recommendation
    item = _make_tiered_item(tier="low", price_score=0.50)
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("2.2 price at threshold -> no rec",
            not _has_rec_type(recs, "price_missing"),
            f"got {[r['type'] for r in recs]}")

    # 2.3: Price absent (0.30) -> recommendation
    item = _make_tiered_item(tier="low", price_score=0.30)
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("2.3 no price -> price_missing rec",
            _has_rec_type(recs, "price_missing"),
            f"got {[r['type'] for r in recs]}")

    # 2.4: price_missing is not auto_fixable
    rec = _get_rec(recs, "price_missing")
    r.check("2.4 price_missing not auto_fixable",
            rec is not None and rec.get("auto_fixable") is False,
            f"got {rec}")

    # 2.5: Message mentions manual price entry
    r.check("2.5 price_missing message",
            rec is not None and "price" in rec.get("message", "").lower(),
            f"msg={rec.get('message') if rec else 'None'}")

    # 2.6: source_signal is correct
    r.check("2.6 price_missing source_signal",
            rec is not None and rec.get("source_signal") == "price_score",
            f"got {rec.get('source_signal') if rec else 'None'}")

    # 2.7: Priority matches tier (reject -> critical)
    item = _make_tiered_item(tier="reject", price_score=0.30)
    generate_repair_recommendations([item])
    rec = _get_rec(item["repair_recommendations"], "price_missing")
    r.check("2.7 reject tier -> critical priority",
            rec is not None and rec.get("priority") == "critical",
            f"got {rec.get('priority') if rec else 'None'}")

    # 2.8: Priority matches tier (medium -> suggested)
    item = _make_tiered_item(tier="medium", price_score=0.30)
    generate_repair_recommendations([item])
    rec = _get_rec(item["repair_recommendations"], "price_missing")
    r.check("2.8 medium tier -> suggested priority",
            rec is not None and rec.get("priority") == "suggested",
            f"got {rec.get('priority') if rec else 'None'}")


# ---------------------------------------------------------------------------
# Group 3: Category suggestion recommendations
# ---------------------------------------------------------------------------

def run_category_suggestion_tests(r: TestReport) -> None:
    print("\n--- Group 3: Category suggestion recommendations ---")

    # 3.1: No category suggestion flag -> no recommendation
    item = _make_tiered_item(tier="low", price_flags=[])
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("3.1 no cat flag -> no cat rec",
            not _has_rec_type(recs, "category_reassignment"),
            f"got {[r['type'] for r in recs]}")

    # 3.2: Category suggestion with good confidence -> promoted
    item = _make_tiered_item(tier="low", price_flags=[{
        "severity": "info",
        "reason": "cross_item_category_suggestion",
        "details": {
            "current_category": "Sides",
            "suggested_category": "Pizza",
            "suggestion_confidence": 0.72,
            "signals": ["4/6 neighbors are Pizza", "price fits Pizza band"],
        },
    }])
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("3.2 cat suggestion promoted",
            _has_rec_type(recs, "category_reassignment"),
            f"got {[r['type'] for r in recs]}")

    # 3.3: auto_fixable with proposed_fix
    rec = _get_rec(recs, "category_reassignment")
    r.check("3.3 cat rec auto_fixable",
            rec is not None and rec.get("auto_fixable") is True,
            f"got {rec}")
    r.check("3.3b cat rec proposed_fix",
            rec is not None and rec.get("proposed_fix") == {"category": "Pizza"},
            f"got {rec.get('proposed_fix') if rec else 'None'}")

    # 3.4: Message includes category names
    r.check("3.4 cat rec message has categories",
            rec is not None and "Sides" in rec.get("message", "") and "Pizza" in rec.get("message", ""),
            f"msg={rec.get('message') if rec else 'None'}")

    # 3.5: Message includes signals
    r.check("3.5 cat rec message has signals",
            rec is not None and "neighbor" in rec.get("message", "").lower(),
            f"msg={rec.get('message') if rec else 'None'}")

    # 3.6: Low confidence (< 0.40) -> NOT promoted
    item = _make_tiered_item(tier="low", price_flags=[{
        "severity": "info",
        "reason": "cross_item_category_suggestion",
        "details": {
            "current_category": "Sides",
            "suggested_category": "Pizza",
            "suggestion_confidence": 0.35,
            "signals": ["weak signal"],
        },
    }])
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("3.6 low confidence -> not promoted",
            not _has_rec_type(recs, "category_reassignment"),
            f"got {[r['type'] for r in recs]}")

    # 3.7: Exactly at minimum confidence -> promoted
    item = _make_tiered_item(tier="low", price_flags=[{
        "severity": "info",
        "reason": "cross_item_category_suggestion",
        "details": {
            "current_category": "Sides",
            "suggested_category": "Entrees",
            "suggestion_confidence": 0.40,
            "signals": ["borderline signal"],
        },
    }])
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("3.7 exactly at min confidence -> promoted",
            _has_rec_type(recs, "category_reassignment"),
            f"got {[r['type'] for r in recs]}")

    # 3.8: Multiple cat suggestions -> strongest used
    item = _make_tiered_item(tier="low", price_flags=[
        {
            "severity": "info",
            "reason": "cross_item_category_suggestion",
            "details": {
                "current_category": "Sides",
                "suggested_category": "Wings",
                "suggestion_confidence": 0.55,
                "signals": ["weaker"],
            },
        },
        {
            "severity": "info",
            "reason": "cross_item_category_suggestion",
            "details": {
                "current_category": "Sides",
                "suggested_category": "Pizza",
                "suggestion_confidence": 0.80,
                "signals": ["stronger"],
            },
        },
    ])
    generate_repair_recommendations([item])
    rec = _get_rec(item["repair_recommendations"], "category_reassignment")
    r.check("3.8 multiple suggestions -> strongest",
            rec is not None and rec.get("proposed_fix") == {"category": "Pizza"},
            f"got {rec.get('proposed_fix') if rec else 'None'}")

    # 3.9: High-tier item -> no recommendation even with flag
    item = _make_tiered_item(tier="high", price_flags=[{
        "severity": "info",
        "reason": "cross_item_category_suggestion",
        "details": {
            "current_category": "Sides",
            "suggested_category": "Pizza",
            "suggestion_confidence": 0.90,
            "signals": ["strong"],
        },
    }])
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("3.9 high tier -> no rec even with flag",
            len(recs) == 0,
            f"got {len(recs)} recs")

    # 3.10: source_signal is correct
    item = _make_tiered_item(tier="low", price_flags=[{
        "severity": "info",
        "reason": "cross_item_category_suggestion",
        "details": {
            "current_category": "Sides",
            "suggested_category": "Pizza",
            "suggestion_confidence": 0.70,
            "signals": ["test"],
        },
    }])
    generate_repair_recommendations([item])
    rec = _get_rec(item["repair_recommendations"], "category_reassignment")
    r.check("3.10 cat rec source_signal",
            rec is not None and rec.get("source_signal") == "category_suggestion_flag",
            f"got {rec.get('source_signal') if rec else 'None'}")


# ---------------------------------------------------------------------------
# Group 4: Variant standardization recommendations
# ---------------------------------------------------------------------------

def run_variant_tests(r: TestReport) -> None:
    print("\n--- Group 4: Variant standardization recommendations ---")

    # 4.1: High variant_score -> no variant recommendation
    item = _make_tiered_item(tier="low", variant_score=0.80)
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("4.1 high variant score -> no variant rec",
            not _has_rec_type(recs, "variant_standardization"),
            f"got {[r['type'] for r in recs]}")

    # 4.2: Exactly at threshold -> no recommendation
    item = _make_tiered_item(tier="low", variant_score=0.50)
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("4.2 variant at threshold -> no rec",
            not _has_rec_type(recs, "variant_standardization"),
            f"got {[r['type'] for r in recs]}")

    # 4.3: Low variant_score + price_inversion flag -> specific message
    item = _make_tiered_item(
        tier="low",
        variant_score=0.30,
        price_flags=[_make_flag("variant_price_inversion", "warn")],
    )
    generate_repair_recommendations([item])
    rec = _get_rec(item["repair_recommendations"], "variant_standardization")
    r.check("4.3 price inversion -> specific msg",
            rec is not None and "out of order" in rec.get("message", "").lower(),
            f"msg={rec.get('message') if rec else 'None'}")

    # 4.4: Low variant_score + duplicate_variant flag
    item = _make_tiered_item(
        tier="low",
        variant_score=0.30,
        price_flags=[_make_flag("duplicate_variant", "warn")],
    )
    generate_repair_recommendations([item])
    rec = _get_rec(item["repair_recommendations"], "variant_standardization")
    r.check("4.4 duplicate variant -> specific msg",
            rec is not None and "duplicate" in rec.get("message", "").lower(),
            f"msg={rec.get('message') if rec else 'None'}")

    # 4.5: Low variant_score + zero_price_variant flag
    item = _make_tiered_item(
        tier="low",
        variant_score=0.30,
        price_flags=[_make_flag("zero_price_variant", "warn")],
    )
    generate_repair_recommendations([item])
    rec = _get_rec(item["repair_recommendations"], "variant_standardization")
    r.check("4.5 zero price variant -> specific msg",
            rec is not None and "$0.00" in rec.get("message", ""),
            f"msg={rec.get('message') if rec else 'None'}")

    # 4.6: Low variant_score + no specific flags -> generic message
    item = _make_tiered_item(tier="low", variant_score=0.30, price_flags=[])
    generate_repair_recommendations([item])
    rec = _get_rec(item["repair_recommendations"], "variant_standardization")
    r.check("4.6 no specific flags -> generic variant msg",
            rec is not None and "low" in rec.get("message", "").lower(),
            f"msg={rec.get('message') if rec else 'None'}")

    # 4.7: cross_item_variant_count_outlier flag
    item = _make_tiered_item(
        tier="low",
        variant_score=0.30,
        price_flags=[_make_flag("cross_item_variant_count_outlier", "info")],
    )
    generate_repair_recommendations([item])
    rec = _get_rec(item["repair_recommendations"], "variant_standardization")
    r.check("4.7 variant count outlier -> specific msg",
            rec is not None and "category" in rec.get("message", "").lower(),
            f"msg={rec.get('message') if rec else 'None'}")

    # 4.8: cross_item_variant_label_mismatch flag
    item = _make_tiered_item(
        tier="low",
        variant_score=0.30,
        price_flags=[_make_flag("cross_item_variant_label_mismatch", "info")],
    )
    generate_repair_recommendations([item])
    rec = _get_rec(item["repair_recommendations"], "variant_standardization")
    r.check("4.8 label mismatch -> specific msg",
            rec is not None and "label" in rec.get("message", "").lower(),
            f"msg={rec.get('message') if rec else 'None'}")

    # 4.9: auto_fixable is False for variant recs
    r.check("4.9 variant rec not auto_fixable",
            rec is not None and rec.get("auto_fixable") is False,
            f"got {rec}")

    # 4.10: Multiple variant flags -> one rec per unique flag reason
    item = _make_tiered_item(
        tier="low",
        variant_score=0.20,
        price_flags=[
            _make_flag("duplicate_variant", "warn"),
            _make_flag("zero_price_variant", "warn"),
            _make_flag("size_gap", "info"),
        ],
    )
    generate_repair_recommendations([item])
    vrecs = _get_recs(item["repair_recommendations"], "variant_standardization")
    r.check("4.10 multiple flags -> multiple variant recs",
            len(vrecs) == 3,
            f"got {len(vrecs)} variant recs")


# ---------------------------------------------------------------------------
# Group 5: Flag summary recommendations
# ---------------------------------------------------------------------------

def run_flag_summary_tests(r: TestReport) -> None:
    print("\n--- Group 5: Flag summary recommendations ---")

    # 5.1: High flag_penalty_score -> no flag recommendation
    item = _make_tiered_item(tier="low", flag_penalty_score=1.0)
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("5.1 high flag score -> no flag rec",
            not _has_rec_type(recs, "flag_attention"),
            f"got {[r['type'] for r in recs]}")

    # 5.2: At threshold -> no recommendation
    item = _make_tiered_item(tier="low", flag_penalty_score=0.70)
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("5.2 flag at threshold -> no rec",
            not _has_rec_type(recs, "flag_attention"),
            f"got {[r['type'] for r in recs]}")

    # 5.3: Below threshold with warn flags -> flag_attention
    item = _make_tiered_item(
        tier="low",
        flag_penalty_score=0.55,
        price_flags=[
            _make_flag("cross_item_category_price_outlier", "warn"),
            _make_flag("cross_category_price_above", "warn"),
            _make_flag("some_info_flag", "info"),
        ],
    )
    generate_repair_recommendations([item])
    rec = _get_rec(item["repair_recommendations"], "flag_attention")
    r.check("5.3 below threshold -> flag_attention rec",
            rec is not None,
            f"got {[r['type'] for r in item['repair_recommendations']]}")

    # 5.4: Message includes warn count
    r.check("5.4 flag rec message has warn count",
            rec is not None and "2 warning" in rec.get("message", ""),
            f"msg={rec.get('message') if rec else 'None'}")

    # 5.5: Message includes info count
    r.check("5.5 flag rec message has info count",
            rec is not None and "1 info" in rec.get("message", ""),
            f"msg={rec.get('message') if rec else 'None'}")

    # 5.6: Details has warn_count and info_count
    details = rec.get("details", {}) if rec else {}
    r.check("5.6 flag rec details has counts",
            details.get("warn_count") == 2 and details.get("info_count") == 1,
            f"got {details}")

    # 5.7: Top reasons includes warn reasons (up to 3)
    r.check("5.7 top_reasons has warn reasons",
            len(details.get("top_reasons", [])) == 2,
            f"got {details.get('top_reasons')}")

    # 5.8: Not auto_fixable
    r.check("5.8 flag rec not auto_fixable",
            rec is not None and rec.get("auto_fixable") is False,
            f"got {rec}")

    # 5.9: Only info flags but many -> still fires if below threshold
    item = _make_tiered_item(
        tier="low",
        flag_penalty_score=0.50,
        price_flags=[_make_flag(f"info_flag_{i}", "info") for i in range(10)],
    )
    generate_repair_recommendations([item])
    rec = _get_rec(item["repair_recommendations"], "flag_attention")
    r.check("5.9 many info flags -> flag_attention",
            rec is not None,
            f"got {[r['type'] for r in item['repair_recommendations']]}")

    # 5.10: No flags but low score (defensive) -> no crash, no rec
    item = _make_tiered_item(tier="low", flag_penalty_score=0.50, price_flags=[])
    generate_repair_recommendations([item])
    r.check("5.10 no flags + low score -> no flag rec",
            not _has_rec_type(item["repair_recommendations"], "flag_attention"),
            f"got {[r['type'] for r in item['repair_recommendations']]}")


# ---------------------------------------------------------------------------
# Group 6: Priority system and tier mapping
# ---------------------------------------------------------------------------

def run_priority_tests(r: TestReport) -> None:
    print("\n--- Group 6: Priority system and tier mapping ---")

    # 6.1: Reject tier -> all recommendations have priority "critical"
    item = _make_tiered_item(
        tier="reject",
        name_quality_score=0.30,
        price_score=0.30,
        grammar={"parsed_name": "AB"},
        text="AB",
    )
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    non_caps = [r for r in recs if not (r.get("type") == "name_quality" and "caps" in r.get("message", "").lower())]
    r.check("6.1 reject -> critical priority",
            all(r.get("priority") == "critical" for r in non_caps) and len(non_caps) > 0,
            f"priorities={[r.get('priority') for r in non_caps]}")

    # 6.2: Low tier -> all recs have priority "important"
    item = _make_tiered_item(
        tier="low",
        price_score=0.30,
    )
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("6.2 low -> important priority",
            all(r.get("priority") == "important" for r in recs) and len(recs) > 0,
            f"priorities={[r.get('priority') for r in recs]}")

    # 6.3: Medium tier -> all recs have priority "suggested"
    item = _make_tiered_item(
        tier="medium",
        price_score=0.30,
    )
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    r.check("6.3 medium -> suggested priority",
            all(r.get("priority") == "suggested" for r in recs) and len(recs) > 0,
            f"priorities={[r.get('priority') for r in recs]}")

    # 6.4: High tier -> empty list
    item = _make_tiered_item(tier="high")
    generate_repair_recommendations([item])
    r.check("6.4 high tier -> empty recs",
            item["repair_recommendations"] == [],
            f"got {item['repair_recommendations']}")

    # 6.5: Missing semantic_tier -> treated as reject
    item = _make_tiered_item(tier="low")
    del item["semantic_tier"]
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    # Should get priority "critical" since missing tier defaults to "reject"
    if recs:
        r.check("6.5 missing tier -> critical priority",
                recs[0].get("priority") == "critical",
                f"got {recs[0].get('priority')}")
    else:
        # No recs because all scores are at/above threshold -- that's OK too
        r.ok("6.5 missing tier -> no recs (all signals OK)")

    # 6.6: Recommendations sorted by priority
    item = _make_tiered_item(
        tier="low",
        name_quality_score=0.30,
        price_score=0.30,
        grammar={"parsed_name": "AB"},
        text="AB",
    )
    # Manually set a mixed priority scenario: low tier items all get "important"
    # so sorting is consistent
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    priorities = [r.get("priority") for r in recs]
    priority_vals = [{"critical": 0, "important": 1, "suggested": 2}.get(p, 9) for p in priorities]
    r.check("6.6 recs sorted by priority",
            priority_vals == sorted(priority_vals),
            f"priorities={priorities}")

    # 6.7: All-caps priority downgrade from critical -> important
    item = _make_tiered_item(
        tier="reject",
        name_quality_score=0.50,
        grammar={"parsed_name": "BUFFALO WINGS"},
        text="BUFFALO WINGS  9.99",
    )
    generate_repair_recommendations([item])
    caps_recs = [r for r in item["repair_recommendations"]
                 if r.get("type") == "name_quality" and "caps" in r.get("message", "").lower()]
    r.check("6.7 all-caps critical -> downgraded to suggested",
            len(caps_recs) >= 1 and caps_recs[0].get("priority") == "suggested",
            f"got {caps_recs}")

    # 6.8: Missing semantic_confidence_details -> no crash, empty recs
    item = {"semantic_tier": "low", "needs_review": True, "merged_text": "Test 9.99"}
    generate_repair_recommendations([item])
    r.check("6.8 missing details -> no crash",
            "repair_recommendations" in item,
            f"field missing")

    # 6.9: Item with multiple recommendation types
    item = _make_tiered_item(
        tier="reject",
        name_quality_score=0.30,
        price_score=0.30,
        variant_score=0.20,
        flag_penalty_score=0.40,
        grammar={"parsed_name": "AB"},
        text="AB",
        price_flags=[
            _make_flag("variant_price_inversion", "warn"),
            _make_flag("cross_item_category_price_outlier", "warn"),
            _make_flag("some_info", "info"),
        ],
    )
    generate_repair_recommendations([item])
    recs = item["repair_recommendations"]
    types = set(r.get("type") for r in recs)
    r.check("6.9 multiple rec types present",
            len(types) >= 3,
            f"types={types}")


# ---------------------------------------------------------------------------
# Group 7: Menu-level repair summary
# ---------------------------------------------------------------------------

def run_repair_summary_tests(r: TestReport) -> None:
    print("\n--- Group 7: Menu-level repair summary ---")

    # 7.1: Empty list -> zeroed summary
    summary = compute_repair_summary([])
    r.check("7.1 empty -> zeroed summary",
            summary["total_items"] == 0 and summary["total_recommendations"] == 0,
            f"got {summary}")

    # 7.2: All high-tier items -> 0 recommendations
    items = [_make_tiered_item(tier="high") for _ in range(5)]
    generate_repair_recommendations(items)
    summary = compute_repair_summary(items)
    r.check("7.2 all high -> 0 recommendations",
            summary["total_recommendations"] == 0 and summary["items_with_recommendations"] == 0,
            f"got recs={summary['total_recommendations']}")

    # 7.3: Mixed items -> correct total counts
    items = [
        _make_tiered_item(tier="high"),
        _make_tiered_item(tier="low", price_score=0.30),
        _make_tiered_item(tier="reject", price_score=0.30, name_quality_score=0.30,
                          grammar={"parsed_name": "AB"}, text="AB"),
    ]
    generate_repair_recommendations(items)
    summary = compute_repair_summary(items)
    r.check("7.3 mixed items -> items_with_recommendations",
            summary["items_with_recommendations"] == 2,
            f"got {summary['items_with_recommendations']}")
    r.check("7.3b total_items correct",
            summary["total_items"] == 3,
            f"got {summary['total_items']}")

    # 7.4: by_type counts correct
    total_by_type = sum(summary["by_type"].values())
    r.check("7.4 by_type sums to total",
            total_by_type == summary["total_recommendations"],
            f"by_type sum={total_by_type}, total={summary['total_recommendations']}")

    # 7.5: by_priority counts correct
    total_by_priority = sum(summary["by_priority"].values())
    r.check("7.5 by_priority sums to total",
            total_by_priority == summary["total_recommendations"],
            f"by_priority sum={total_by_priority}, total={summary['total_recommendations']}")

    # 7.6: auto_fixable_count
    r.check("7.6 auto_fixable_count >= 0",
            summary["auto_fixable_count"] >= 0,
            f"got {summary['auto_fixable_count']}")

    # 7.7: Category breakdown exists
    r.check("7.7 category_breakdown is dict",
            isinstance(summary["category_breakdown"], dict),
            f"got {type(summary['category_breakdown'])}")

    # 7.8: Summary has all expected keys
    expected_keys = {"total_items", "items_with_recommendations", "total_recommendations",
                     "by_priority", "by_type", "auto_fixable_count", "category_breakdown"}
    r.check("7.8 summary has all keys",
            expected_keys <= set(summary.keys()),
            f"missing {expected_keys - set(summary.keys())}")

    # 7.9: Category breakdown reflects item categories
    items = [
        _make_tiered_item(tier="low", price_score=0.30, category="Pizza"),
        _make_tiered_item(tier="low", price_score=0.30, category="Wings"),
        _make_tiered_item(tier="high", category="Pizza"),
    ]
    generate_repair_recommendations(items)
    summary = compute_repair_summary(items)
    r.check("7.9 category breakdown has Pizza and Wings",
            "Pizza" in summary["category_breakdown"] and "Wings" in summary["category_breakdown"],
            f"got {list(summary['category_breakdown'].keys())}")

    # 7.10: Category breakdown counts correct
    pizza = summary["category_breakdown"].get("Pizza", {})
    r.check("7.10 pizza category breakdown",
            pizza.get("items_with_recommendations") == 1 and pizza.get("recommendation_count") >= 1,
            f"got {pizza}")

    # 7.11: Summary does NOT mutate items
    items = [_make_tiered_item(tier="low", price_score=0.30)]
    generate_repair_recommendations(items)
    recs_before = list(items[0].get("repair_recommendations", []))
    compute_repair_summary(items)
    recs_after = list(items[0].get("repair_recommendations", []))
    r.check("7.11 summary does not mutate items",
            recs_before == recs_after,
            "items were mutated")

    # 7.12: by_priority has all three keys even if zero
    items = [_make_tiered_item(tier="high")]
    generate_repair_recommendations(items)
    summary = compute_repair_summary(items)
    r.check("7.12 by_priority has all keys",
            all(k in summary["by_priority"] for k in ("critical", "important", "suggested")),
            f"got {list(summary['by_priority'].keys())}")


# ---------------------------------------------------------------------------
# Group 8: Pipeline integration and edge cases
# ---------------------------------------------------------------------------

def run_pipeline_tests(r: TestReport) -> None:
    print("\n--- Group 8: Pipeline integration and edge cases ---")

    # 8.1: Full pipeline: score -> classify -> repair
    item = _make_item(
        text="Margherita Pizza  12.99",
        grammar={"parsed_name": "Margherita Pizza", "parse_confidence": 0.90},
        price_candidates=[{"price_cents": 1299, "value": 12.99}],
        category="Pizza",
    )
    score_semantic_confidence([item])
    classify_confidence_tiers([item])
    generate_repair_recommendations([item])
    r.check("8.1 full pipeline -> repair_recommendations exists",
            "repair_recommendations" in item,
            "missing field")

    # 8.2: Empty items list -> no crash
    items: list = []
    generate_repair_recommendations(items)
    r.check("8.2 empty list -> no crash",
            True, "")

    # 8.3: Single item -> works
    item = _make_tiered_item(tier="low", price_score=0.30)
    generate_repair_recommendations([item])
    r.check("8.3 single item -> has recs",
            "repair_recommendations" in item,
            "missing field")

    # 8.4: Bulk items -> all get field
    items = [_make_tiered_item(tier="medium") for _ in range(50)]
    generate_repair_recommendations(items)
    r.check("8.4 50 items -> all have field",
            all("repair_recommendations" in it for it in items),
            "some missing")

    # 8.5: Idempotent (calling twice -> same result)
    item = _make_tiered_item(tier="low", price_score=0.30, variant_score=0.30)
    generate_repair_recommendations([item])
    recs1 = list(item["repair_recommendations"])
    generate_repair_recommendations([item])
    recs2 = list(item["repair_recommendations"])
    r.check("8.5 idempotent",
            len(recs1) == len(recs2) and all(
                r1.get("type") == r2.get("type") for r1, r2 in zip(recs1, recs2)),
            f"recs1={len(recs1)}, recs2={len(recs2)}")

    # 8.6: Returns None (mutates in place)
    item = _make_tiered_item(tier="low")
    result = generate_repair_recommendations([item])
    r.check("8.6 returns None",
            result is None,
            f"got {result}")

    # 8.7: Path A item through full pipeline
    item = _make_item(
        text="eeeeccccrrrvvv  5.99",
        grammar={"parsed_name": "eeeeccccrrrvvv", "parse_confidence": 0.30},
        price_candidates=[{"price_cents": 599}],
    )
    score_semantic_confidence([item])
    classify_confidence_tiers([item])
    generate_repair_recommendations([item])
    r.check("8.7 path A garbled item has recs",
            len(item.get("repair_recommendations", [])) >= 1,
            f"got {item.get('repair_recommendations')}")

    # 8.8: Path B item through full pipeline
    item = _make_item(name="Pepperoni Pizza", confidence=0.90, price_cents=1299, category="Pizza")
    score_semantic_confidence([item])
    classify_confidence_tiers([item])
    generate_repair_recommendations([item])
    r.check("8.8 path B clean item",
            "repair_recommendations" in item,
            "missing field")

    # 8.9: Mixed Path A + Path B
    items = [
        _make_item(
            text="Good Item  10.99",
            grammar={"parsed_name": "Good Item", "parse_confidence": 0.90},
            price_candidates=[{"price_cents": 1099}],
        ),
        _make_item(name="Path B Item", confidence=0.85, price_cents=999),
    ]
    score_semantic_confidence(items)
    classify_confidence_tiers(items)
    generate_repair_recommendations(items)
    r.check("8.9 mixed paths -> all have field",
            all("repair_recommendations" in it for it in items),
            "some missing")

    # 8.10: Recommendation dict has all expected keys
    item = _make_tiered_item(tier="low", price_score=0.30)
    generate_repair_recommendations([item])
    for rec in item["repair_recommendations"]:
        has_keys = all(k in rec for k in ("type", "priority", "message", "auto_fixable", "source_signal"))
        r.check("8.10 rec has all required keys",
                has_keys,
                f"keys={list(rec.keys())}")
        break

    # 8.11: No mutation of upstream fields
    orig_flags = [_make_flag("test", "warn")]
    item = _make_tiered_item(
        tier="low",
        flag_penalty_score=0.50,
        price_flags=orig_flags,
    )
    details_before = dict(item.get("semantic_confidence_details", {}))
    flags_before = len(item.get("price_flags", []))
    generate_repair_recommendations([item])
    r.check("8.11 upstream fields not mutated",
            item.get("semantic_confidence_details") == details_before
            and len(item.get("price_flags", [])) == flags_before,
            "upstream mutated")

    # 8.12: Realistic restaurant menu simulation
    items = [
        # 5 high-quality items (no recs expected)
        _make_tiered_item(tier="high", category="Pizza"),
        _make_tiered_item(tier="high", category="Pizza"),
        _make_tiered_item(tier="high", category="Wings"),
        _make_tiered_item(tier="high", category="Sides"),
        _make_tiered_item(tier="high", category="Beverages"),
        # 3 medium items (suggested recs)
        _make_tiered_item(tier="medium", price_score=0.30, category="Pizza"),
        _make_tiered_item(tier="medium", variant_score=0.30, category="Wings",
                          price_flags=[_make_flag("zero_price_variant", "warn")]),
        _make_tiered_item(tier="medium", category="Sides"),
        # 2 low items (important recs)
        _make_tiered_item(tier="low", price_score=0.30, name_quality_score=0.30,
                          grammar={"parsed_name": "AB"}, text="AB", category="Pizza"),
        _make_tiered_item(tier="low", flag_penalty_score=0.40, category="Wings",
                          price_flags=[
                              _make_flag("cross_item_category_price_outlier", "warn"),
                              _make_flag("cross_category_price_above", "warn"),
                              _make_flag("info1", "info"),
                          ]),
    ]
    generate_repair_recommendations(items)
    summary = compute_repair_summary(items)

    r.check("8.12a realistic: total_items=10",
            summary["total_items"] == 10,
            f"got {summary['total_items']}")
    # High items have no recs, medium has >=2 with recs, low has 2 with recs
    r.check("8.12b realistic: items_with_recs >= 4",
            summary["items_with_recommendations"] >= 4,
            f"got {summary['items_with_recommendations']}")
    r.check("8.12c realistic: total_recs > 0",
            summary["total_recommendations"] > 0,
            f"got {summary['total_recommendations']}")
    r.check("8.12d realistic: category breakdown has Pizza",
            "Pizza" in summary["category_breakdown"],
            f"cats={list(summary['category_breakdown'].keys())}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    report = TestReport()

    run_name_quality_tests(report)
    run_price_missing_tests(report)
    run_category_suggestion_tests(report)
    run_variant_tests(report)
    run_flag_summary_tests(report)
    run_priority_tests(report)
    run_repair_summary_tests(report)
    run_pipeline_tests(report)

    print(f"\n{'=' * 60}")
    print(f"Day 68 Results: {report.passed}/{report.total} passed")

    if report.failures:
        print(f"\n{len(report.failures)} FAILURES:")
        for f in report.failures:
            print(f"  {f}")
        sys.exit(1)
    else:
        print("All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
