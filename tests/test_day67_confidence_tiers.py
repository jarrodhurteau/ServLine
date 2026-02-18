"""
Day 67: Sprint 8.4 -- Confidence Tiers + Menu-Level Aggregation

Tests the confidence tier classification and menu-level summary in
storage/semantic_confidence.py:
  1. Tier classification (_tier_for_score)
  2. classify_confidence_tiers (per-item tier + needs_review)
  3. compute_menu_confidence_summary (menu-wide stats)
  4. Category-level breakdowns
  5. Quality grade assignment
  6. Edge cases
  7. Pipeline integration (Day 66 → Day 67 chaining)

Run: python tests/test_day67_confidence_tiers.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.semantic_confidence import (
    score_semantic_confidence,
    classify_confidence_tiers,
    compute_menu_confidence_summary,
    _tier_for_score,
    _TIER_HIGH,
    _TIER_MEDIUM,
    _TIER_LOW,
    _GRADE_A_THRESHOLD,
    _GRADE_B_THRESHOLD,
    _GRADE_C_THRESHOLD,
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


def _make_scored_item(
    sc: float,
    tier: Optional[str] = None,
    needs_review: Optional[bool] = None,
    category: str = "Uncategorized",
) -> Dict[str, Any]:
    """Build a pre-scored item for summary tests (skip Day 66 scoring)."""
    item: Dict[str, Any] = {
        "semantic_confidence": sc,
        "category": category,
    }
    if tier is not None:
        item["semantic_tier"] = tier
    if needs_review is not None:
        item["needs_review"] = needs_review
    return item


def _make_variant(
    label: str = "M",
    price_cents: int = 1099,
    kind: str = "size",
    confidence: float = 0.80,
) -> Dict[str, Any]:
    return {
        "label": label,
        "price_cents": price_cents,
        "kind": kind,
        "confidence": confidence,
    }


def _approx(a: float, b: float, tol: float = 0.001) -> bool:
    return abs(a - b) < tol


# ---------------------------------------------------------------------------
# Group 1: _tier_for_score
# ---------------------------------------------------------------------------

def run_tier_for_score_tests(r: TestReport) -> None:
    print("\n--- Group 1: _tier_for_score ---")

    # 1.1: High tier (exactly 0.80)
    r.check("1.1 score 0.80 -> high",
            _tier_for_score(0.80) == "high",
            f"got {_tier_for_score(0.80)}")

    # 1.2: High tier (0.95)
    r.check("1.2 score 0.95 -> high",
            _tier_for_score(0.95) == "high",
            f"got {_tier_for_score(0.95)}")

    # 1.3: High tier (1.0)
    r.check("1.3 score 1.0 -> high",
            _tier_for_score(1.0) == "high",
            f"got {_tier_for_score(1.0)}")

    # 1.4: Medium tier (exactly 0.60)
    r.check("1.4 score 0.60 -> medium",
            _tier_for_score(0.60) == "medium",
            f"got {_tier_for_score(0.60)}")

    # 1.5: Medium tier (0.79)
    r.check("1.5 score 0.79 -> medium",
            _tier_for_score(0.79) == "medium",
            f"got {_tier_for_score(0.79)}")

    # 1.6: Medium tier (0.70)
    r.check("1.6 score 0.70 -> medium",
            _tier_for_score(0.70) == "medium",
            f"got {_tier_for_score(0.70)}")

    # 1.7: Low tier (exactly 0.40)
    r.check("1.7 score 0.40 -> low",
            _tier_for_score(0.40) == "low",
            f"got {_tier_for_score(0.40)}")

    # 1.8: Low tier (0.59)
    r.check("1.8 score 0.59 -> low",
            _tier_for_score(0.59) == "low",
            f"got {_tier_for_score(0.59)}")

    # 1.9: Reject tier (0.39)
    r.check("1.9 score 0.39 -> reject",
            _tier_for_score(0.39) == "reject",
            f"got {_tier_for_score(0.39)}")

    # 1.10: Reject tier (0.0)
    r.check("1.10 score 0.0 -> reject",
            _tier_for_score(0.0) == "reject",
            f"got {_tier_for_score(0.0)}")

    # 1.11: Reject tier (0.10)
    r.check("1.11 score 0.10 -> reject",
            _tier_for_score(0.10) == "reject",
            f"got {_tier_for_score(0.10)}")

    # 1.12: Boundary 0.7999 -> medium (not high)
    r.check("1.12 score 0.7999 -> medium",
            _tier_for_score(0.7999) == "medium",
            f"got {_tier_for_score(0.7999)}")

    # 1.13: Boundary 0.5999 -> low (not medium)
    r.check("1.13 score 0.5999 -> low",
            _tier_for_score(0.5999) == "low",
            f"got {_tier_for_score(0.5999)}")

    # 1.14: Boundary 0.3999 -> reject (not low)
    r.check("1.14 score 0.3999 -> reject",
            _tier_for_score(0.3999) == "reject",
            f"got {_tier_for_score(0.3999)}")


# ---------------------------------------------------------------------------
# Group 2: classify_confidence_tiers
# ---------------------------------------------------------------------------

def run_classify_tiers_tests(r: TestReport) -> None:
    print("\n--- Group 2: classify_confidence_tiers ---")

    # 2.1: High-confidence item
    item = _make_item(
        name="Cheese Pizza Deluxe",
        grammar={"parsed_name": "Cheese Pizza Deluxe", "parse_confidence": 1.0},
        variants=[_make_variant(confidence=1.0, price_cents=1099)],
    )
    score_semantic_confidence([item])
    classify_confidence_tiers([item])
    r.check("2.1 high item -> tier=high",
            item.get("semantic_tier") == "high",
            f"tier={item.get('semantic_tier')}, sc={item.get('semantic_confidence')}")
    r.check("2.1b high item -> needs_review=False",
            item.get("needs_review") is False,
            f"needs_review={item.get('needs_review')}")

    # 2.2: Medium-confidence item
    item = _make_item(
        name="Cheese Pizza Deluxe",
        grammar={"parsed_name": "Cheese Pizza Deluxe", "parse_confidence": 0.55},
        variants=[_make_variant(confidence=0.5, price_cents=1099)],
    )
    score_semantic_confidence([item])
    classify_confidence_tiers([item])
    r.check("2.2 medium item -> tier=medium",
            item.get("semantic_tier") == "medium",
            f"tier={item.get('semantic_tier')}, sc={item.get('semantic_confidence')}")
    r.check("2.2b medium item -> needs_review=True",
            item.get("needs_review") is True,
            f"needs_review={item.get('needs_review')}")

    # 2.3: Low-confidence item (garbled name, no price, low grammar)
    item = _make_item(
        name="Taco",
        grammar={"parsed_name": "Taco", "parse_confidence": 0.30},
    )
    score_semantic_confidence([item])
    classify_confidence_tiers([item])
    r.check("2.3 low item -> tier=low",
            item.get("semantic_tier") == "low",
            f"tier={item.get('semantic_tier')}, sc={item.get('semantic_confidence')}")
    r.check("2.3b low item -> needs_review=True",
            item.get("needs_review") is True,
            f"needs_review={item.get('needs_review')}")

    # 2.4: Reject-confidence item
    item = _make_item(
        name="",
        text="",
        grammar={"parsed_name": "", "parse_confidence": 0.0},
        variants=[_make_variant(confidence=0.0, price_cents=0)],
        price_flags=[{"severity": "warn", "reason": "t"}] * 7,
    )
    score_semantic_confidence([item])
    classify_confidence_tiers([item])
    r.check("2.4 reject item -> tier=reject",
            item.get("semantic_tier") == "reject",
            f"tier={item.get('semantic_tier')}, sc={item.get('semantic_confidence')}")
    r.check("2.4b reject item -> needs_review=True",
            item.get("needs_review") is True,
            f"needs_review={item.get('needs_review')}")

    # 2.5: Missing semantic_confidence -> reject + review
    item = {"name": "Test"}
    classify_confidence_tiers([item])
    r.check("2.5 missing sc -> tier=reject",
            item.get("semantic_tier") == "reject",
            f"tier={item.get('semantic_tier')}")
    r.check("2.5b missing sc -> needs_review=True",
            item.get("needs_review") is True,
            f"needs_review={item.get('needs_review')}")

    # 2.6: Empty list (no crash)
    classify_confidence_tiers([])
    r.check("2.6 empty list no crash", True)

    # 2.7: Multiple items classified independently
    item_a = _make_item(
        name="Good Pizza Name",
        grammar={"parsed_name": "Good Pizza Name", "parse_confidence": 0.95},
        variants=[_make_variant(confidence=0.9, price_cents=1099)],
    )
    item_b = _make_item(
        name="X",
        grammar={"parsed_name": "X", "parse_confidence": 0.2},
    )
    score_semantic_confidence([item_a, item_b])
    classify_confidence_tiers([item_a, item_b])
    r.check("2.7 multiple items: a=high, b=low/reject",
            item_a["semantic_tier"] == "high" and item_b["semantic_tier"] in ("low", "reject"),
            f"a_tier={item_a['semantic_tier']}, b_tier={item_b['semantic_tier']}")

    # 2.8: Pre-set semantic_confidence (no Day 66 scoring needed)
    item = {"semantic_confidence": 0.75}
    classify_confidence_tiers([item])
    r.check("2.8 pre-set sc=0.75 -> medium",
            item["semantic_tier"] == "medium",
            f"tier={item.get('semantic_tier')}")

    # 2.9: Pre-set sc=0.80 -> high (exact boundary)
    item = {"semantic_confidence": 0.80}
    classify_confidence_tiers([item])
    r.check("2.9 pre-set sc=0.80 -> high",
            item["semantic_tier"] == "high",
            f"tier={item.get('semantic_tier')}")

    # 2.10: Pre-set sc=0.40 -> low (exact boundary)
    item = {"semantic_confidence": 0.40}
    classify_confidence_tiers([item])
    r.check("2.10 pre-set sc=0.40 -> low",
            item["semantic_tier"] == "low",
            f"tier={item.get('semantic_tier')}")

    # 2.11: Idempotent (classify twice same result)
    item = {"semantic_confidence": 0.85}
    classify_confidence_tiers([item])
    first_tier = item["semantic_tier"]
    classify_confidence_tiers([item])
    r.check("2.11 idempotent",
            item["semantic_tier"] == first_tier,
            f"first={first_tier}, second={item['semantic_tier']}")

    # 2.12: sc=None explicitly set
    item = {"semantic_confidence": None}
    classify_confidence_tiers([item])
    r.check("2.12 sc=None -> reject",
            item["semantic_tier"] == "reject",
            f"tier={item.get('semantic_tier')}")


# ---------------------------------------------------------------------------
# Group 3: compute_menu_confidence_summary basics
# ---------------------------------------------------------------------------

def run_summary_basics_tests(r: TestReport) -> None:
    print("\n--- Group 3: Menu summary basics ---")

    # 3.1: Empty list
    summary = compute_menu_confidence_summary([])
    r.check("3.1 empty list total=0",
            summary["total_items"] == 0,
            f"total={summary['total_items']}")
    r.check("3.1b empty grade=D",
            summary["quality_grade"] == "D",
            f"grade={summary['quality_grade']}")

    # 3.2: Single high item
    items = [_make_scored_item(0.90, "high", False)]
    summary = compute_menu_confidence_summary(items)
    r.check("3.2 single item total=1",
            summary["total_items"] == 1,
            f"total={summary['total_items']}")
    r.check("3.2b mean=0.90",
            _approx(summary["mean_confidence"], 0.90),
            f"mean={summary['mean_confidence']}")
    r.check("3.2c median=0.90",
            _approx(summary["median_confidence"], 0.90),
            f"median={summary['median_confidence']}")
    r.check("3.2d stdev=0.0 (single item)",
            _approx(summary["stdev_confidence"], 0.0),
            f"stdev={summary['stdev_confidence']}")
    r.check("3.2e tier_counts high=1",
            summary["tier_counts"]["high"] == 1,
            f"got {summary['tier_counts']}")
    r.check("3.2f needs_review=0",
            summary["needs_review_count"] == 0,
            f"got {summary['needs_review_count']}")
    r.check("3.2g grade=A (100% high)",
            summary["quality_grade"] == "A",
            f"grade={summary['quality_grade']}")

    # 3.3: Mixed tiers
    items = [
        _make_scored_item(0.95, "high", False),
        _make_scored_item(0.85, "high", False),
        _make_scored_item(0.70, "medium", True),
        _make_scored_item(0.50, "low", True),
        _make_scored_item(0.30, "reject", True),
    ]
    summary = compute_menu_confidence_summary(items)
    r.check("3.3 total=5",
            summary["total_items"] == 5,
            f"total={summary['total_items']}")
    r.check("3.3b tier_counts correct",
            summary["tier_counts"] == {"high": 2, "medium": 1, "low": 1, "reject": 1},
            f"got {summary['tier_counts']}")
    r.check("3.3c needs_review=3",
            summary["needs_review_count"] == 3,
            f"got {summary['needs_review_count']}")
    expected_mean = (0.95 + 0.85 + 0.70 + 0.50 + 0.30) / 5
    r.check("3.3d mean correct",
            _approx(summary["mean_confidence"], expected_mean),
            f"got {summary['mean_confidence']}, expected {expected_mean}")
    r.check("3.3e median=0.70",
            _approx(summary["median_confidence"], 0.70),
            f"got {summary['median_confidence']}")

    # 3.4: All high items -> grade A
    items = [_make_scored_item(0.90, "high", False) for _ in range(10)]
    summary = compute_menu_confidence_summary(items)
    r.check("3.4 all high -> grade A",
            summary["quality_grade"] == "A",
            f"grade={summary['quality_grade']}")

    # 3.5: 70% high -> grade B
    items = ([_make_scored_item(0.90, "high", False)] * 7 +
             [_make_scored_item(0.50, "low", True)] * 3)
    summary = compute_menu_confidence_summary(items)
    r.check("3.5 70% high -> grade B",
            summary["quality_grade"] == "B",
            f"grade={summary['quality_grade']}")

    # 3.6: 50% high -> grade C
    items = ([_make_scored_item(0.90, "high", False)] * 5 +
             [_make_scored_item(0.50, "low", True)] * 5)
    summary = compute_menu_confidence_summary(items)
    r.check("3.6 50% high -> grade C",
            summary["quality_grade"] == "C",
            f"grade={summary['quality_grade']}")

    # 3.7: 30% high -> grade D
    items = ([_make_scored_item(0.90, "high", False)] * 3 +
             [_make_scored_item(0.50, "low", True)] * 7)
    summary = compute_menu_confidence_summary(items)
    r.check("3.7 30% high -> grade D",
            summary["quality_grade"] == "D",
            f"grade={summary['quality_grade']}")

    # 3.8: Exactly 80% high -> grade A
    items = ([_make_scored_item(0.90, "high", False)] * 8 +
             [_make_scored_item(0.50, "low", True)] * 2)
    summary = compute_menu_confidence_summary(items)
    r.check("3.8 exactly 80% high -> grade A",
            summary["quality_grade"] == "A",
            f"grade={summary['quality_grade']}")

    # 3.9: Exactly 60% high -> grade B
    items = ([_make_scored_item(0.90, "high", False)] * 6 +
             [_make_scored_item(0.50, "low", True)] * 4)
    summary = compute_menu_confidence_summary(items)
    r.check("3.9 exactly 60% high -> grade B",
            summary["quality_grade"] == "B",
            f"grade={summary['quality_grade']}")

    # 3.10: Exactly 40% high -> grade C
    items = ([_make_scored_item(0.90, "high", False)] * 4 +
             [_make_scored_item(0.50, "low", True)] * 6)
    summary = compute_menu_confidence_summary(items)
    r.check("3.10 exactly 40% high -> grade C",
            summary["quality_grade"] == "C",
            f"grade={summary['quality_grade']}")

    # 3.11: stdev > 0 with spread
    items = [
        _make_scored_item(0.95, "high", False),
        _make_scored_item(0.30, "reject", True),
    ]
    summary = compute_menu_confidence_summary(items)
    r.check("3.11 stdev > 0",
            summary["stdev_confidence"] > 0.0,
            f"stdev={summary['stdev_confidence']}")

    # 3.12: All same score -> stdev=0
    items = [_make_scored_item(0.75, "medium", True) for _ in range(5)]
    summary = compute_menu_confidence_summary(items)
    r.check("3.12 all same score -> stdev=0",
            _approx(summary["stdev_confidence"], 0.0),
            f"stdev={summary['stdev_confidence']}")

    # 3.13: Rounding to 4 decimals
    items = [
        _make_scored_item(0.333333, "reject", True),
        _make_scored_item(0.666666, "medium", True),
    ]
    summary = compute_menu_confidence_summary(items)
    r.check("3.13 mean rounded to 4 decimals",
            summary["mean_confidence"] == round((0.333333 + 0.666666) / 2, 4),
            f"mean={summary['mean_confidence']}")


# ---------------------------------------------------------------------------
# Group 4: Category-level breakdowns
# ---------------------------------------------------------------------------

def run_category_breakdown_tests(r: TestReport) -> None:
    print("\n--- Group 4: Category breakdowns ---")

    # 4.1: Single category
    items = [
        _make_scored_item(0.90, "high", False, "Pizza"),
        _make_scored_item(0.85, "high", False, "Pizza"),
        _make_scored_item(0.70, "medium", True, "Pizza"),
    ]
    summary = compute_menu_confidence_summary(items)
    cat_sum = summary["category_summary"]
    r.check("4.1 single category present",
            "Pizza" in cat_sum,
            f"keys={list(cat_sum.keys())}")
    r.check("4.1b count=3",
            cat_sum["Pizza"]["count"] == 3,
            f"count={cat_sum['Pizza']['count']}")
    expected_mean = round((0.90 + 0.85 + 0.70) / 3, 4)
    r.check("4.1c mean correct",
            _approx(cat_sum["Pizza"]["mean"], expected_mean),
            f"mean={cat_sum['Pizza']['mean']}, expected={expected_mean}")
    r.check("4.1d needs_review=1",
            cat_sum["Pizza"]["needs_review_count"] == 1,
            f"got {cat_sum['Pizza']['needs_review_count']}")

    # 4.2: Multiple categories
    items = [
        _make_scored_item(0.90, "high", False, "Pizza"),
        _make_scored_item(0.85, "high", False, "Pizza"),
        _make_scored_item(0.50, "low", True, "Wings"),
        _make_scored_item(0.45, "low", True, "Wings"),
        _make_scored_item(0.95, "high", False, "Salads"),
    ]
    summary = compute_menu_confidence_summary(items)
    cat_sum = summary["category_summary"]
    r.check("4.2 three categories present",
            set(cat_sum.keys()) == {"Pizza", "Wings", "Salads"},
            f"keys={list(cat_sum.keys())}")
    r.check("4.2b Pizza count=2",
            cat_sum["Pizza"]["count"] == 2,
            f"count={cat_sum['Pizza']['count']}")
    r.check("4.2c Wings count=2",
            cat_sum["Wings"]["count"] == 2,
            f"count={cat_sum['Wings']['count']}")
    r.check("4.2d Salads count=1",
            cat_sum["Salads"]["count"] == 1,
            f"count={cat_sum['Salads']['count']}")

    # 4.3: Category tier_counts
    items = [
        _make_scored_item(0.90, "high", False, "Pizza"),
        _make_scored_item(0.70, "medium", True, "Pizza"),
        _make_scored_item(0.30, "reject", True, "Pizza"),
    ]
    summary = compute_menu_confidence_summary(items)
    cat_tiers = summary["category_summary"]["Pizza"]["tier_counts"]
    r.check("4.3 Pizza tier_counts",
            cat_tiers == {"high": 1, "medium": 1, "low": 0, "reject": 1},
            f"got {cat_tiers}")

    # 4.4: Missing category -> Uncategorized
    items = [
        _make_scored_item(0.80, "high", False),  # default = Uncategorized
    ]
    # Override to remove category
    items[0].pop("category", None)
    summary = compute_menu_confidence_summary(items)
    r.check("4.4 missing category -> Uncategorized",
            "Uncategorized" in summary["category_summary"],
            f"keys={list(summary['category_summary'].keys())}")

    # 4.5: Empty string category -> Uncategorized
    items = [{"semantic_confidence": 0.80, "semantic_tier": "high",
              "needs_review": False, "category": ""}]
    summary = compute_menu_confidence_summary(items)
    r.check("4.5 empty category -> Uncategorized",
            "Uncategorized" in summary["category_summary"],
            f"keys={list(summary['category_summary'].keys())}")

    # 4.6: Category needs_review counts match total
    items = [
        _make_scored_item(0.90, "high", False, "Pizza"),
        _make_scored_item(0.50, "low", True, "Pizza"),
        _make_scored_item(0.30, "reject", True, "Wings"),
    ]
    summary = compute_menu_confidence_summary(items)
    total_cat_review = sum(
        c["needs_review_count"]
        for c in summary["category_summary"].values()
    )
    r.check("4.6 category review counts sum to total",
            total_cat_review == summary["needs_review_count"],
            f"cat_sum={total_cat_review}, total={summary['needs_review_count']}")

    # 4.7: Category tier counts sum to category count
    items = [
        _make_scored_item(0.90, "high", False, "Subs"),
        _make_scored_item(0.70, "medium", True, "Subs"),
        _make_scored_item(0.50, "low", True, "Subs"),
        _make_scored_item(0.20, "reject", True, "Subs"),
    ]
    summary = compute_menu_confidence_summary(items)
    cat = summary["category_summary"]["Subs"]
    tier_sum = sum(cat["tier_counts"].values())
    r.check("4.7 tier counts sum to category count",
            tier_sum == cat["count"],
            f"tier_sum={tier_sum}, count={cat['count']}")

    # 4.8: Categories sorted alphabetically
    items = [
        _make_scored_item(0.90, "high", False, "Wings"),
        _make_scored_item(0.90, "high", False, "Pizza"),
        _make_scored_item(0.90, "high", False, "Appetizers"),
    ]
    summary = compute_menu_confidence_summary(items)
    cats = list(summary["category_summary"].keys())
    r.check("4.8 categories sorted alphabetically",
            cats == sorted(cats),
            f"order={cats}")


# ---------------------------------------------------------------------------
# Group 5: Quality grade edge cases
# ---------------------------------------------------------------------------

def run_quality_grade_tests(r: TestReport) -> None:
    print("\n--- Group 5: Quality grade edge cases ---")

    # 5.1: All reject -> grade D
    items = [_make_scored_item(0.10, "reject", True) for _ in range(5)]
    summary = compute_menu_confidence_summary(items)
    r.check("5.1 all reject -> D",
            summary["quality_grade"] == "D",
            f"grade={summary['quality_grade']}")

    # 5.2: All medium -> grade D (0% high)
    items = [_make_scored_item(0.70, "medium", True) for _ in range(5)]
    summary = compute_menu_confidence_summary(items)
    r.check("5.2 all medium -> D (0% high)",
            summary["quality_grade"] == "D",
            f"grade={summary['quality_grade']}")

    # 5.3: 79% high -> grade B (just below A threshold)
    items = ([_make_scored_item(0.90, "high", False)] * 79 +
             [_make_scored_item(0.50, "low", True)] * 21)
    summary = compute_menu_confidence_summary(items)
    r.check("5.3 79% high -> B",
            summary["quality_grade"] == "B",
            f"grade={summary['quality_grade']}")

    # 5.4: 59% high -> grade C (just below B threshold)
    items = ([_make_scored_item(0.90, "high", False)] * 59 +
             [_make_scored_item(0.50, "low", True)] * 41)
    summary = compute_menu_confidence_summary(items)
    r.check("5.4 59% high -> C",
            summary["quality_grade"] == "C",
            f"grade={summary['quality_grade']}")

    # 5.5: 39% high -> grade D (just below C threshold)
    items = ([_make_scored_item(0.90, "high", False)] * 39 +
             [_make_scored_item(0.50, "low", True)] * 61)
    summary = compute_menu_confidence_summary(items)
    r.check("5.5 39% high -> D",
            summary["quality_grade"] == "D",
            f"grade={summary['quality_grade']}")

    # 5.6: Single high item -> grade A
    items = [_make_scored_item(0.90, "high", False)]
    summary = compute_menu_confidence_summary(items)
    r.check("5.6 single high -> A",
            summary["quality_grade"] == "A",
            f"grade={summary['quality_grade']}")

    # 5.7: Single reject item -> grade D
    items = [_make_scored_item(0.10, "reject", True)]
    summary = compute_menu_confidence_summary(items)
    r.check("5.7 single reject -> D",
            summary["quality_grade"] == "D",
            f"grade={summary['quality_grade']}")


# ---------------------------------------------------------------------------
# Group 6: Edge cases
# ---------------------------------------------------------------------------

def run_edge_case_tests(r: TestReport) -> None:
    print("\n--- Group 6: Edge cases ---")

    # 6.1: Item missing semantic_tier (only sc) in summary
    items = [{"semantic_confidence": 0.90}]
    summary = compute_menu_confidence_summary(items)
    r.check("6.1 missing tier defaults to reject in summary",
            summary["tier_counts"]["reject"] == 1,
            f"tiers={summary['tier_counts']}")

    # 6.2: Item missing needs_review (only sc) in summary
    items = [{"semantic_confidence": 0.90}]
    summary = compute_menu_confidence_summary(items)
    r.check("6.2 missing needs_review defaults to True",
            summary["needs_review_count"] == 1,
            f"review={summary['needs_review_count']}")

    # 6.3: Item missing semantic_confidence in summary
    items = [{"name": "Test"}]
    summary = compute_menu_confidence_summary(items)
    r.check("6.3 missing sc defaults to 0.0",
            _approx(summary["mean_confidence"], 0.0),
            f"mean={summary['mean_confidence']}")

    # 6.4: Large list (100 items)
    items = [_make_scored_item(0.90, "high", False, "Pizza") for _ in range(100)]
    summary = compute_menu_confidence_summary(items)
    r.check("6.4 100 items total correct",
            summary["total_items"] == 100,
            f"total={summary['total_items']}")
    r.check("6.4b 100 items grade A",
            summary["quality_grade"] == "A",
            f"grade={summary['quality_grade']}")

    # 6.5: Summary has all expected top-level keys
    items = [_make_scored_item(0.80, "high", False)]
    summary = compute_menu_confidence_summary(items)
    expected_keys = {
        "total_items", "mean_confidence", "median_confidence",
        "stdev_confidence", "tier_counts", "needs_review_count",
        "quality_grade", "category_summary",
    }
    r.check("6.5 all expected keys present",
            set(summary.keys()) == expected_keys,
            f"missing={expected_keys - set(summary.keys())}, extra={set(summary.keys()) - expected_keys}")

    # 6.6: tier_counts has all 4 tiers
    r.check("6.6 tier_counts has all 4 keys",
            set(summary["tier_counts"].keys()) == {"high", "medium", "low", "reject"},
            f"got {set(summary['tier_counts'].keys())}")

    # 6.7: Category summary entry has expected keys
    cat = summary["category_summary"].get("Uncategorized", {})
    expected_cat_keys = {"count", "mean", "needs_review_count", "tier_counts"}
    r.check("6.7 category entry has expected keys",
            set(cat.keys()) == expected_cat_keys,
            f"got {set(cat.keys())}")

    # 6.8: Tier counts sum to total_items
    items = [
        _make_scored_item(0.90, "high", False),
        _make_scored_item(0.70, "medium", True),
        _make_scored_item(0.50, "low", True),
        _make_scored_item(0.20, "reject", True),
    ]
    summary = compute_menu_confidence_summary(items)
    tier_sum = sum(summary["tier_counts"].values())
    r.check("6.8 tier counts sum to total",
            tier_sum == summary["total_items"],
            f"tier_sum={tier_sum}, total={summary['total_items']}")

    # 6.9: needs_review_count matches tier-based expectation
    r.check("6.9 needs_review = medium + low + reject",
            summary["needs_review_count"] == 3,
            f"review={summary['needs_review_count']}")


# ---------------------------------------------------------------------------
# Group 7: Pipeline integration (Day 66 → Day 67 chaining)
# ---------------------------------------------------------------------------

def run_pipeline_integration_tests(r: TestReport) -> None:
    print("\n--- Group 7: Pipeline integration ---")

    # 7.1: Full pipeline: score → classify → summary
    items = [
        _make_item(
            name="Pepperoni Pizza",
            grammar={"parsed_name": "Pepperoni Pizza", "parse_confidence": 0.92},
            variants=[
                _make_variant(label="S", price_cents=999, confidence=0.85),
                _make_variant(label="M", price_cents=1299, confidence=0.85),
                _make_variant(label="L", price_cents=1599, confidence=0.85),
            ],
            category="Pizza",
        ),
        _make_item(
            name="Cheese Pizza",
            grammar={"parsed_name": "Cheese Pizza", "parse_confidence": 0.95},
            variants=[_make_variant(price_cents=1099, confidence=0.90)],
            category="Pizza",
        ),
        _make_item(
            name="Garden Salad",
            grammar={"parsed_name": "Garden Salad", "parse_confidence": 0.88},
            variants=[_make_variant(price_cents=799, confidence=0.80)],
            category="Salads",
        ),
        _make_item(
            name="X",
            grammar={"parsed_name": "X", "parse_confidence": 0.20},
            category="Unknown",
        ),
    ]
    # Step 9.2: score
    score_semantic_confidence(items)
    # Step 9.3: classify
    classify_confidence_tiers(items)

    # All items should have tier + needs_review
    all_have_tier = all("semantic_tier" in it for it in items)
    all_have_review = all("needs_review" in it for it in items)
    r.check("7.1 all items have tier",
            all_have_tier,
            "missing semantic_tier")
    r.check("7.1b all items have needs_review",
            all_have_review,
            "missing needs_review")

    # 7.2: Summary from pipeline output
    summary = compute_menu_confidence_summary(items)
    r.check("7.2 summary total=4",
            summary["total_items"] == 4,
            f"total={summary['total_items']}")
    r.check("7.2b categories present",
            set(summary["category_summary"].keys()) == {"Pizza", "Salads", "Unknown"},
            f"cats={list(summary['category_summary'].keys())}")

    # 7.3: Good items score high, bad items score low
    pizza_items = [it for it in items if it.get("category") == "Pizza"]
    unknown_items = [it for it in items if it.get("category") == "Unknown"]
    r.check("7.3 pizza items scored well",
            all(it["semantic_tier"] == "high" for it in pizza_items),
            f"tiers={[it['semantic_tier'] for it in pizza_items]}")
    r.check("7.3b bad item scores low",
            unknown_items[0]["semantic_tier"] in ("low", "reject"),
            f"tier={unknown_items[0]['semantic_tier']}")

    # 7.4: Path B pipeline
    items_b = [
        {"name": "Cheese Pizza", "confidence": 0.90, "category": "Pizza",
         "variants": [_make_variant(price_cents=1099, confidence=0.88)]},
        {"name": "Soda", "confidence": 0.85, "category": "Beverages",
         "price_candidates": [{"value": 2.50}]},
    ]
    score_semantic_confidence(items_b)
    classify_confidence_tiers(items_b)
    summary_b = compute_menu_confidence_summary(items_b)
    r.check("7.4 Path B pipeline works",
            summary_b["total_items"] == 2,
            f"total={summary_b['total_items']}")
    r.check("7.4b both items have tiers",
            all("semantic_tier" in it for it in items_b),
            "missing tiers")

    # 7.5: Mixed Path A + Path B
    item_a = _make_item(
        name="Wings",
        grammar={"parsed_name": "Wings", "parse_confidence": 0.85},
        variants=[_make_variant(price_cents=899, confidence=0.80)],
        category="Wings",
    )
    item_b = {"name": "Fries", "confidence": 0.90, "category": "Sides",
              "variants": [_make_variant(price_cents=499, confidence=0.75)]}
    mixed = [item_a, item_b]
    score_semantic_confidence(mixed)
    classify_confidence_tiers(mixed)
    summary_m = compute_menu_confidence_summary(mixed)
    r.check("7.5 mixed paths work",
            summary_m["total_items"] == 2,
            f"total={summary_m['total_items']}")
    r.check("7.5b two categories in summary",
            len(summary_m["category_summary"]) == 2,
            f"cats={list(summary_m['category_summary'].keys())}")

    # 7.6: Realistic restaurant menu simulation (15 items)
    items_real = []
    for i in range(8):
        items_real.append(_make_item(
            name=f"Specialty Pizza {i+1}",
            grammar={"parsed_name": f"Specialty Pizza {i+1}", "parse_confidence": 0.90},
            variants=[_make_variant(price_cents=1299, confidence=0.85)],
            category="Pizza",
        ))
    for i in range(4):
        items_real.append(_make_item(
            name=f"Chicken Dish {i+1}",
            grammar={"parsed_name": f"Chicken Dish {i+1}", "parse_confidence": 0.82},
            variants=[_make_variant(price_cents=999, confidence=0.78)],
            category="Entrees",
        ))
    for i in range(3):
        items_real.append(_make_item(
            name=f"Soda {i+1}",
            grammar={"parsed_name": f"Soda {i+1}", "parse_confidence": 0.75},
            variants=[_make_variant(price_cents=299, confidence=0.70)],
            category="Beverages",
        ))
    score_semantic_confidence(items_real)
    classify_confidence_tiers(items_real)
    summary_real = compute_menu_confidence_summary(items_real)
    r.check("7.6 realistic menu: 15 items",
            summary_real["total_items"] == 15,
            f"total={summary_real['total_items']}")
    r.check("7.6b has 3 categories",
            len(summary_real["category_summary"]) == 3,
            f"cats={list(summary_real['category_summary'].keys())}")
    r.check("7.6c grade is reasonable (B or above)",
            summary_real["quality_grade"] in ("A", "B"),
            f"grade={summary_real['quality_grade']}")

    # 7.7: No mutation of semantic_confidence by classify
    item = _make_item(
        name="Test Pizza",
        grammar={"parsed_name": "Test Pizza", "parse_confidence": 0.90},
        variants=[_make_variant(price_cents=1099, confidence=0.85)],
    )
    score_semantic_confidence([item])
    original_sc = item["semantic_confidence"]
    classify_confidence_tiers([item])
    r.check("7.7 classify doesn't mutate semantic_confidence",
            item["semantic_confidence"] == original_sc,
            f"before={original_sc}, after={item['semantic_confidence']}")

    # 7.8: Summary doesn't mutate items
    items = [_make_scored_item(0.90, "high", False, "Pizza")]
    original = dict(items[0])
    compute_menu_confidence_summary(items)
    r.check("7.8 summary doesn't mutate items",
            items[0] == original,
            "item was mutated")

    # 7.9: Constants consistency
    r.check("7.9 tier thresholds consistent",
            _TIER_HIGH > _TIER_MEDIUM > _TIER_LOW > 0,
            f"high={_TIER_HIGH}, medium={_TIER_MEDIUM}, low={_TIER_LOW}")

    # 7.10: Grade thresholds consistent
    r.check("7.10 grade thresholds consistent",
            _GRADE_A_THRESHOLD > _GRADE_B_THRESHOLD > _GRADE_C_THRESHOLD > 0,
            f"A={_GRADE_A_THRESHOLD}, B={_GRADE_B_THRESHOLD}, C={_GRADE_C_THRESHOLD}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    report = TestReport()

    run_tier_for_score_tests(report)
    run_classify_tiers_tests(report)
    run_summary_basics_tests(report)
    run_category_breakdown_tests(report)
    run_quality_grade_tests(report)
    run_edge_case_tests(report)
    run_pipeline_integration_tests(report)

    print(f"\n{'=' * 60}")
    print(f"Day 67 Results: {report.passed}/{report.total} passed")

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
