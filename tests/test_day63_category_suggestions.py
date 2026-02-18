"""
Day 63: Sprint 8.3 -- Category Reassignment Suggestions (Neighbor-Based Smoothing)

Tests the new _check_category_suggestions() function in storage/cross_item.py.
Multi-signal scoring: neighbor agreement, keyword fit, price band, original
confidence.

Tests:
  1. Basic suggestion generation (neighbor agreement triggers)
  2. Keyword signal (favor current vs suggested)
  3. Price band signal (current vs suggested fit)
  4. Original confidence signal
  5. Confidence threshold behavior
  6. Edge cases (no category, single item, boundaries)
  7. Coexistence with existing checks
  8. Both-path compatibility (pipeline vs ai_ocr_helper)

Run: python tests/test_day63_category_suggestions.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.cross_item import (
    check_cross_item_consistency,
    _extract_item_name,
    _normalize_name,
    _extract_primary_price_cents,
    _keyword_match_count,
    _in_price_band,
    _SUGGESTION_WINDOW,
    _SUGGESTION_MIN_NEIGHBORS,
    _SUGGESTION_MIN_AGREEMENT,
    _SUGGESTION_MIN_CONFIDENCE,
    _SUGGESTION_KEYWORD_GUARD,
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


def _make_tb(
    text: str = "Test Item  10.99",
    grammar: Optional[Dict] = None,
    variants: Optional[List[Dict]] = None,
    category: Optional[str] = None,
    category_confidence: Optional[int] = None,
    meta: Optional[Dict] = None,
    price_flags: Optional[List[Dict]] = None,
    name: Optional[str] = None,
    price_candidates: Optional[List[Dict]] = None,
    price_cents: Optional[int] = None,
) -> Dict[str, Any]:
    tb: Dict[str, Any] = {
        "merged_text": text,
        "bbox": [0, 0, 100, 20],
        "lines": [{"text": text, "bbox": [0, 0, 100, 20], "words": []}],
    }
    if grammar is not None:
        tb["grammar"] = grammar
    if variants is not None:
        tb["variants"] = variants
    if category is not None:
        tb["category"] = category
    if category_confidence is not None:
        tb["category_confidence"] = category_confidence
    if meta is not None:
        tb["meta"] = meta
    if price_flags is not None:
        tb["price_flags"] = price_flags
    if name is not None:
        tb["name"] = name
    if price_candidates is not None:
        tb["price_candidates"] = price_candidates
    if price_cents is not None:
        tb["price_cents"] = price_cents
    return tb


def _make_variant(
    label: str = "M",
    price_cents: int = 1099,
    kind: str = "size",
    confidence: float = 0.80,
    normalized_size: Optional[str] = None,
    group_key: Optional[str] = None,
) -> Dict[str, Any]:
    v: Dict[str, Any] = {
        "label": label,
        "price_cents": price_cents,
        "kind": kind,
        "confidence": confidence,
    }
    if normalized_size is not None:
        v["normalized_size"] = normalized_size
    if group_key is not None:
        v["group_key"] = group_key
    return v


def _count_flags(tb: Dict[str, Any], reason: str) -> int:
    return sum(1 for f in tb.get("price_flags", []) if f.get("reason") == reason)


def _get_flag(tb: Dict[str, Any], reason: str) -> Optional[Dict]:
    for f in tb.get("price_flags", []):
        if f.get("reason") == reason:
            return f
    return None


# ---------------------------------------------------------------------------
# Group 1: Basic Suggestion
# ---------------------------------------------------------------------------

def run_basic_suggestion_tests(report: TestReport) -> None:
    print("\n--- Group 1: Basic Suggestion ---")

    # 1.1: Item in middle of different-category section gets suggestion
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1399)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1499)]),
        _make_tb(grammar={"parsed_name": "Mystery Item"}, category="Wings",
                 variants=[_make_variant(price_cents=1199)]),
        _make_tb(grammar={"parsed_name": "Hawaiian Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Supreme Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1599)]),
        _make_tb(grammar={"parsed_name": "BBQ Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1399)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.1 middle item gets suggestion",
                 _count_flags(blocks[3], "cross_item_category_suggestion") == 1,
                 f"flags: {blocks[3].get('price_flags')}")

    # 1.2: Suggested category matches dominant neighbor
    flag = _get_flag(blocks[3], "cross_item_category_suggestion")
    report.check("1.2 suggested category is Pizza",
                 flag is not None and flag["details"]["suggested_category"] == "Pizza",
                 f"details: {flag['details'] if flag else None}")

    # 1.3: Current category preserved in flag
    report.check("1.3 current_category is Wings",
                 flag is not None and flag["details"]["current_category"] == "Wings",
                 f"details: {flag['details'] if flag else None}")

    # 1.4: Item matching its neighbors gets NO suggestion
    report.check("1.4 matching items no suggestion",
                 _count_flags(blocks[0], "cross_item_category_suggestion") == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.5: Too few neighbors (2-item list)
    blocks2 = [
        _make_tb(category="Wings"),
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks2)
    report.check("1.5 too few neighbors",
                 _count_flags(blocks2[0], "cross_item_category_suggestion") == 0,
                 f"flags: {blocks2[0].get('price_flags')}")

    # 1.6: No dominant category (all different neighbors)
    blocks3 = [
        _make_tb(grammar={"parsed_name": "Alpha"}, category="Pizza"),
        _make_tb(grammar={"parsed_name": "Beta"}, category="Wings"),
        _make_tb(grammar={"parsed_name": "Gamma"}, category="Salads"),
        _make_tb(grammar={"parsed_name": "Delta"}, category="Pasta"),
        _make_tb(grammar={"parsed_name": "Epsilon"}, category="Beverages"),
        _make_tb(grammar={"parsed_name": "Zeta"}, category="Sides / Appetizers"),
        _make_tb(grammar={"parsed_name": "Eta"}, category="Desserts"),
    ]
    check_cross_item_consistency(blocks3)
    report.check("1.6 no dominant category",
                 _count_flags(blocks3[3], "cross_item_category_suggestion") == 0,
                 f"flags: {blocks3[3].get('price_flags')}")

    # 1.7: Severity is always "info"
    blocks4 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Generic Item"}, category="Wings"),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks4)
    flag4 = _get_flag(blocks4[3], "cross_item_category_suggestion")
    report.check("1.7 severity is info",
                 flag4 is not None and flag4["severity"] == "info",
                 f"flag: {flag4}")

    # 1.8: Flag details contain all required fields
    report.check("1.8 all detail fields present",
                 flag4 is not None
                 and "suggestion_confidence" in flag4["details"]
                 and "neighbor_agreement" in flag4["details"]
                 and "neighbor_count" in flag4["details"]
                 and "signals" in flag4["details"]
                 and isinstance(flag4["details"]["signals"], list),
                 f"details: {flag4['details'] if flag4 else None}")


# ---------------------------------------------------------------------------
# Group 2: Keyword Signal
# ---------------------------------------------------------------------------

def run_keyword_signal_tests(report: TestReport) -> None:
    print("\n--- Group 2: Keyword Signal ---")

    # 2.1: Keywords favor suggested -> higher confidence
    # "Pizza Special" has "pizza" keyword for Pizza category
    blocks = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Pizza Special"}, category="Wings",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    flag_kw = _get_flag(blocks[3], "cross_item_category_suggestion")
    report.check("2.1 keywords boost when favoring suggested",
                 flag_kw is not None and flag_kw["details"]["suggestion_confidence"] > 0.30,
                 f"flag: {flag_kw}")

    # 2.2: Keywords favor current -> guard suppresses
    # "Caesar Salad" has "salad" + "caesar" for Salads (2 matches -> guard fires)
    blocks2 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Caesar Salad"}, category="Salads",
                 variants=[_make_variant(price_cents=899)]),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks2)
    report.check("2.2 keyword guard suppresses suggestion",
                 _count_flags(blocks2[3], "cross_item_category_suggestion") == 0,
                 f"flags: {blocks2[3].get('price_flags')}")

    # 2.3: No keywords match either -> neutral (still gets suggestion from neighbors)
    blocks3 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Special Combo"}, category="Wings",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks3)
    flag3 = _get_flag(blocks3[3], "cross_item_category_suggestion")
    report.check("2.3 no keywords -> neutral, still gets suggestion",
                 flag3 is not None,
                 f"flags: {blocks3[3].get('price_flags')}")

    # 2.4: Both zero keywords -> neutral
    blocks4 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "House Platter"}, category="Wings",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks4)
    flag4 = _get_flag(blocks4[3], "cross_item_category_suggestion")
    report.check("2.4 both zero keywords -> neutral",
                 flag4 is not None,
                 f"flags: {blocks4[3].get('price_flags')}")

    # 2.5: _keyword_match_count helper: "cheese pizza" matches "pizza" for Pizza
    report.check("2.5 keyword count pizza",
                 _keyword_match_count("cheese pizza", "Pizza") >= 1,
                 f"count: {_keyword_match_count('cheese pizza', 'Pizza')}")

    # 2.6: _keyword_match_count: "buffalo wings" has multiple matches for Wings
    count = _keyword_match_count("buffalo wings", "Wings")
    report.check("2.6 keyword count wings",
                 count >= 2,
                 f"count: {count}")

    # 2.7: _keyword_match_count: empty name returns 0
    report.check("2.7 empty name zero", _keyword_match_count("", "Pizza") == 0)

    # 2.8: _keyword_match_count: unknown category returns 0
    report.check("2.8 unknown category zero",
                 _keyword_match_count("cheese pizza", "NonExistent") == 0)


# ---------------------------------------------------------------------------
# Group 3: Price Band Signal
# ---------------------------------------------------------------------------

def run_price_band_signal_tests(report: TestReport) -> None:
    print("\n--- Group 3: Price Band Signal ---")

    # 3.1: Price fits suggested but not current -> boosts
    # $1.99 drink categorized as Pizza, surrounded by Beverages
    blocks = [
        _make_tb(category="Beverages"), _make_tb(category="Beverages"),
        _make_tb(category="Beverages"),
        _make_tb(grammar={"parsed_name": "Special Drink"}, category="Pizza",
                 variants=[_make_variant(price_cents=199)]),
        _make_tb(category="Beverages"), _make_tb(category="Beverages"),
        _make_tb(category="Beverages"),
    ]
    check_cross_item_consistency(blocks)
    flag = _get_flag(blocks[3], "cross_item_category_suggestion")
    report.check("3.1 price fits suggested -> boost",
                 flag is not None and flag["details"]["suggestion_confidence"] > 0.30,
                 f"flag: {flag}")

    # 3.2: Price fits current but not suggested -> penalty can suppress
    # $12.99 pizza price categorized as Pizza surrounded by Beverages
    # base=0.40, price_penalty=-0.15, high_conf(default 50)=0.0 => 0.25 < 0.30
    blocks2 = [
        _make_tb(category="Beverages"), _make_tb(category="Beverages"),
        _make_tb(category="Beverages"),
        _make_tb(grammar={"parsed_name": "Expensive Item"}, category="Pizza",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(category="Beverages"), _make_tb(category="Beverages"),
        _make_tb(category="Beverages"),
    ]
    check_cross_item_consistency(blocks2)
    report.check("3.2 price fits current -> suppressed below threshold",
                 _count_flags(blocks2[3], "cross_item_category_suggestion") == 0,
                 f"flags: {blocks2[3].get('price_flags')}")

    # 3.3: Price fits both -> neutral
    # $9.99 fits Wings (699-2499) and Subs (699-1999)
    blocks3 = [
        _make_tb(category="Subs / Sandwiches"), _make_tb(category="Subs / Sandwiches"),
        _make_tb(category="Subs / Sandwiches"),
        _make_tb(grammar={"parsed_name": "Combo Item"}, category="Wings",
                 variants=[_make_variant(price_cents=999)]),
        _make_tb(category="Subs / Sandwiches"), _make_tb(category="Subs / Sandwiches"),
        _make_tb(category="Subs / Sandwiches"),
    ]
    check_cross_item_consistency(blocks3)
    flag3 = _get_flag(blocks3[3], "cross_item_category_suggestion")
    report.check("3.3 price fits both -> neutral, still gets suggestion",
                 flag3 is not None,
                 f"flag: {flag3}")

    # 3.4: No price -> signal skipped, still gets suggestion from neighbors
    blocks4 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "No Price Item"}, category="Wings"),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks4)
    flag4 = _get_flag(blocks4[3], "cross_item_category_suggestion")
    report.check("3.4 no price -> signal neutral",
                 flag4 is not None,
                 f"flag: {flag4}")

    # 3.5: _in_price_band: 1299 in Pizza band (799-3999)
    report.check("3.5 in_price_band pizza",
                 _in_price_band(1299, "Pizza") is True)

    # 3.6: _in_price_band: 199 NOT in Pizza band
    report.check("3.6 not in band pizza",
                 _in_price_band(199, "Pizza") is False)


# ---------------------------------------------------------------------------
# Group 4: Original Confidence Signal
# ---------------------------------------------------------------------------

def run_confidence_signal_tests(report: TestReport) -> None:
    print("\n--- Group 4: Original Confidence Signal ---")

    # 4.1: Low original confidence -> boosts suggestion
    blocks = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Unknown Item"}, category="Wings",
                 category_confidence=30,
                 variants=[_make_variant(price_cents=1199)]),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    flag_low = _get_flag(blocks[3], "cross_item_category_suggestion")
    report.check("4.1 low confidence boosts",
                 flag_low is not None and flag_low["details"]["suggestion_confidence"] > 0.30,
                 f"flag: {flag_low}")

    # 4.2: High original confidence -> penalizes suggestion
    blocks2 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Unknown Item"}, category="Wings",
                 category_confidence=90,
                 variants=[_make_variant(price_cents=1199)]),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks2)
    flag_high = _get_flag(blocks2[3], "cross_item_category_suggestion")
    # With high confidence: base 0.40 - 0.15 = 0.25 -> below threshold
    report.check("4.2 high confidence penalizes",
                 flag_high is None,
                 f"flag: {flag_high}")

    # 4.3: Missing category_confidence (ai_ocr_helper path) -> defaults to 50
    blocks3 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Unknown Item"}, category="Wings",
                 variants=[_make_variant(price_cents=1199)]),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks3)
    flag_default = _get_flag(blocks3[3], "cross_item_category_suggestion")
    report.check("4.3 missing confidence defaults to 50",
                 flag_default is not None,
                 f"flag: {flag_default}")

    # 4.4: Confidence exactly at 50 -> neutral (no boost or penalty)
    blocks4 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Unknown Item"}, category="Wings",
                 category_confidence=50,
                 variants=[_make_variant(price_cents=1199)]),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks4)
    flag_50 = _get_flag(blocks4[3], "cross_item_category_suggestion")
    report.check("4.4 confidence 50 is neutral",
                 flag_50 is not None,
                 f"flag: {flag_50}")

    # 4.5: Confidence 79 -> still neutral (not >= 80)
    blocks5 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Unknown Item"}, category="Wings",
                 category_confidence=79,
                 variants=[_make_variant(price_cents=1199)]),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks5)
    flag_79 = _get_flag(blocks5[3], "cross_item_category_suggestion")
    report.check("4.5 confidence 79 is neutral (not penalized)",
                 flag_79 is not None
                 and flag_50 is not None
                 and flag_79["details"]["suggestion_confidence"] == flag_50["details"]["suggestion_confidence"],
                 f"flag_79: {flag_79}, flag_50: {flag_50}")


# ---------------------------------------------------------------------------
# Group 5: Confidence Threshold
# ---------------------------------------------------------------------------

def run_threshold_tests(report: TestReport) -> None:
    print("\n--- Group 5: Confidence Threshold ---")

    # 5.1: Threshold constant value
    report.check("5.1 threshold is 0.30",
                 _SUGGESTION_MIN_CONFIDENCE == 0.30)

    # 5.2: Maximum confidence scenario (all signals align)
    # 100% neighbor agreement -> base 0.40
    # "coke" keyword for Beverages -> +0.20
    # price $1.99 fits Beverages not Pizza -> +0.15
    # low confidence 30 -> +0.10
    # Total: 0.85
    blocks = [
        _make_tb(category="Beverages"), _make_tb(category="Beverages"),
        _make_tb(category="Beverages"),
        _make_tb(grammar={"parsed_name": "Coke Special"}, category="Pizza",
                 category_confidence=30,
                 variants=[_make_variant(price_cents=199)]),
        _make_tb(category="Beverages"), _make_tb(category="Beverages"),
        _make_tb(category="Beverages"),
    ]
    check_cross_item_consistency(blocks)
    flag = _get_flag(blocks[3], "cross_item_category_suggestion")
    report.check("5.2 max confidence scenario",
                 flag is not None and flag["details"]["suggestion_confidence"] >= 0.70,
                 f"flag: {flag}")

    # 5.3: Just above threshold
    # 4 Pizza + 2 Salads neighbors = 67% agreement -> base=0.267
    # + low confidence (+0.10) = 0.367 (above 0.30)
    blocks3 = [
        _make_tb(category="Salads"),
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Unknown Item"}, category="Wings",
                 category_confidence=30,
                 variants=[_make_variant(price_cents=1199)]),
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Salads"),
    ]
    check_cross_item_consistency(blocks3)
    flag3 = _get_flag(blocks3[3], "cross_item_category_suggestion")
    report.check("5.3 just above threshold emits flag",
                 flag3 is not None
                 and flag3["details"]["suggestion_confidence"] >= _SUGGESTION_MIN_CONFIDENCE,
                 f"flag: {flag3}")

    # 5.4: Below threshold -> no flag
    # High confidence + price fits current -> penalties push below threshold
    blocks4 = [
        _make_tb(category="Beverages"), _make_tb(category="Beverages"),
        _make_tb(category="Beverages"),
        _make_tb(grammar={"parsed_name": "Expensive Item"}, category="Pizza",
                 category_confidence=95,
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(category="Beverages"), _make_tb(category="Beverages"),
        _make_tb(category="Beverages"),
    ]
    check_cross_item_consistency(blocks4)
    report.check("5.4 below threshold no flag",
                 _count_flags(blocks4[3], "cross_item_category_suggestion") == 0,
                 f"flags: {blocks4[3].get('price_flags')}")

    # 5.5: Constants check
    report.check("5.5 window is 3", _SUGGESTION_WINDOW == 3)


# ---------------------------------------------------------------------------
# Group 6: Edge Cases
# ---------------------------------------------------------------------------

def run_edge_case_tests(report: TestReport) -> None:
    print("\n--- Group 6: Edge Cases ---")

    # 6.1: Items with no category -> skipped
    blocks = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "No Category Item"}),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.1 no category skipped",
                 _count_flags(blocks[3], "cross_item_category_suggestion") == 0,
                 f"flags: {blocks[3].get('price_flags')}")

    # 6.2: Single item -> no suggestions (check_cross_item_consistency short-circuits)
    blocks2 = [_make_tb(category="Pizza")]
    check_cross_item_consistency(blocks2)
    report.check("6.2 single item no suggestion",
                 _count_flags(blocks2[0], "cross_item_category_suggestion") == 0,
                 f"flags: {blocks2[0].get('price_flags')}")

    # 6.3: All same category -> no suggestions
    blocks3 = [_make_tb(grammar={"parsed_name": f"Item {chr(65+i)}"}, category="Pizza")
               for i in range(7)]
    check_cross_item_consistency(blocks3)
    report.check("6.3 all same category no suggestion",
                 all(_count_flags(b, "cross_item_category_suggestion") == 0 for b in blocks3),
                 f"counts: {[_count_flags(b, 'cross_item_category_suggestion') for b in blocks3]}")

    # 6.4: Item at beginning (fewer left neighbors, but 3 right neighbors suffice)
    blocks4 = [
        _make_tb(grammar={"parsed_name": "Odd Item"}, category="Wings"),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks4)
    flag4 = _get_flag(blocks4[0], "cross_item_category_suggestion")
    report.check("6.4 beginning item with 3+ right neighbors",
                 flag4 is not None,
                 f"flag: {flag4}")

    # 6.5: Item at end (fewer right neighbors, but 3 left neighbors suffice)
    blocks5 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Odd Item"}, category="Wings"),
    ]
    check_cross_item_consistency(blocks5)
    flag5 = _get_flag(blocks5[6], "cross_item_category_suggestion")
    report.check("6.5 end item with 3+ left neighbors",
                 flag5 is not None,
                 f"flag: {flag5}")

    # 6.6: Empty item name -> no crash
    blocks6 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(text="", category="Wings"),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks6)
    # Just verifying no crash; may or may not get a suggestion
    report.check("6.6 empty name no crash",
                 True,
                 "")

    # 6.7: Zero-price items (price signal skipped)
    blocks7 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Free Item"}, category="Wings"),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks7)
    flag7 = _get_flag(blocks7[3], "cross_item_category_suggestion")
    report.check("6.7 zero price -> still gets suggestion from neighbors",
                 flag7 is not None,
                 f"flag: {flag7}")

    # 6.8: Uncategorized neighbors don't count toward minimum
    blocks8 = [
        _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "No Cat A"}),
        _make_tb(grammar={"parsed_name": "No Cat B"}),
        _make_tb(grammar={"parsed_name": "Test"}, category="Wings"),
        _make_tb(grammar={"parsed_name": "No Cat C"}),
        _make_tb(grammar={"parsed_name": "No Cat D"}),
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks8)
    # Only 2 categorized neighbors (idx 0 and 6), below minimum of 3
    report.check("6.8 uncategorized neighbors not counted",
                 _count_flags(blocks8[3], "cross_item_category_suggestion") == 0,
                 f"flags: {blocks8[3].get('price_flags')}")


# ---------------------------------------------------------------------------
# Group 7: Coexistence with Existing Checks
# ---------------------------------------------------------------------------

def run_coexistence_tests(report: TestReport) -> None:
    print("\n--- Group 7: Coexistence with Existing Checks ---")

    # 7.1: Isolated item also gets suggestion (both flags present)
    blocks = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Odd Item"}, category="Wings"),
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    report.check("7.1a isolation flag present",
                 _count_flags(blocks[3], "cross_item_category_isolated") == 1,
                 f"flags: {blocks[3].get('price_flags')}")
    report.check("7.1b suggestion flag also present",
                 _count_flags(blocks[3], "cross_item_category_suggestion") == 1,
                 f"flags: {blocks[3].get('price_flags')}")

    # 7.2: Suggestion without isolation (has one matching neighbor)
    blocks2 = [
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Wings"),  # one Wings neighbor prevents isolation
        _make_tb(grammar={"parsed_name": "Test Item"}, category="Wings",
                 variants=[_make_variant(price_cents=1199)]),
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks2)
    report.check("7.2a not isolated (has matching neighbor)",
                 _count_flags(blocks2[3], "cross_item_category_isolated") == 0,
                 f"flags: {blocks2[3].get('price_flags')}")
    report.check("7.2b but gets suggestion",
                 _count_flags(blocks2[3], "cross_item_category_suggestion") == 1,
                 f"flags: {blocks2[3].get('price_flags')}")

    # 7.3: All new flags use cross_item_ prefix
    blocks3 = [
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
        _make_tb(grammar={"parsed_name": "Test"}, category="Wings"),
        _make_tb(category="Pizza"), _make_tb(category="Pizza"), _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks3)
    all_flags = [f for b in blocks3 for f in b.get("price_flags", [])]
    report.check("7.3 all flags have cross_item_ prefix",
                 all(f["reason"].startswith("cross_item_") for f in all_flags),
                 f"reasons: {[f['reason'] for f in all_flags]}")

    # 7.4: Duplicate name + suggestion on same item
    blocks4 = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Pepperoni"}, category="Pizza",
                 variants=[_make_variant(price_cents=1399)]),
        _make_tb(grammar={"parsed_name": "Veggie"}, category="Pizza",
                 variants=[_make_variant(price_cents=1499)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Wings",
                 variants=[_make_variant(price_cents=1099)]),
        _make_tb(grammar={"parsed_name": "Supreme"}, category="Pizza",
                 variants=[_make_variant(price_cents=1599)]),
        _make_tb(grammar={"parsed_name": "Hawaiian"}, category="Pizza",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "BBQ"}, category="Pizza",
                 variants=[_make_variant(price_cents=1399)]),
    ]
    check_cross_item_consistency(blocks4)
    reasons = {f["reason"] for f in blocks4[3].get("price_flags", [])}
    report.check("7.4a duplicate name flag",
                 "cross_item_duplicate_name" in reasons,
                 f"reasons: {reasons}")
    report.check("7.4b suggestion flag also present",
                 "cross_item_category_suggestion" in reasons,
                 f"reasons: {reasons}")


# ---------------------------------------------------------------------------
# Group 8: Both-Path Compatibility
# ---------------------------------------------------------------------------

def run_both_path_tests(report: TestReport) -> None:
    print("\n--- Group 8: Both-Path Compatibility ---")

    # 8.1: Pipeline path (grammar + variants + category_confidence)
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza",
                 category_confidence=85, variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza",
                 category_confidence=90, variants=[_make_variant(price_cents=1399)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza",
                 category_confidence=88, variants=[_make_variant(price_cents=1499)]),
        _make_tb(grammar={"parsed_name": "Mystery Item"}, category="Wings",
                 category_confidence=25, variants=[_make_variant(price_cents=1199)]),
        _make_tb(grammar={"parsed_name": "Supreme Pizza"}, category="Pizza",
                 category_confidence=92, variants=[_make_variant(price_cents=1599)]),
        _make_tb(grammar={"parsed_name": "Hawaiian"}, category="Pizza",
                 category_confidence=80, variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "BBQ Pizza"}, category="Pizza",
                 category_confidence=85, variants=[_make_variant(price_cents=1399)]),
    ]
    check_cross_item_consistency(blocks)
    flag = _get_flag(blocks[3], "cross_item_category_suggestion")
    report.check("8.1 pipeline path with low confidence gets suggestion",
                 flag is not None and flag["details"]["suggestion_confidence"] > 0.30,
                 f"flag: {flag}")

    # 8.2: ai_ocr_helper path (name + price_candidates, no grammar, no confidence)
    blocks2 = [
        _make_tb(name="Cheese Pizza", category="Pizza",
                 price_candidates=[{"value": 12.99}]),
        _make_tb(name="Pepperoni Pizza", category="Pizza",
                 price_candidates=[{"value": 13.99}]),
        _make_tb(name="Veggie Pizza", category="Pizza",
                 price_candidates=[{"value": 14.99}]),
        _make_tb(name="Mystery Item", category="Wings",
                 price_candidates=[{"value": 11.99}]),
        _make_tb(name="Supreme Pizza", category="Pizza",
                 price_candidates=[{"value": 15.99}]),
        _make_tb(name="Hawaiian", category="Pizza",
                 price_candidates=[{"value": 12.99}]),
        _make_tb(name="BBQ Pizza", category="Pizza",
                 price_candidates=[{"value": 13.99}]),
    ]
    check_cross_item_consistency(blocks2)
    flag2 = _get_flag(blocks2[3], "cross_item_category_suggestion")
    report.check("8.2 ai_ocr_helper path works",
                 flag2 is not None,
                 f"flag: {flag2}")

    # 8.3: Mixed path items in same list
    blocks3 = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(name="Pepperoni Pizza", category="Pizza",
                 price_candidates=[{"value": 13.99}]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1499)]),
        _make_tb(name="Mystery Item", category="Wings",
                 price_candidates=[{"value": 11.99}]),
        _make_tb(grammar={"parsed_name": "Supreme Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1599)]),
        _make_tb(name="Hawaiian", category="Pizza",
                 price_candidates=[{"value": 12.99}]),
        _make_tb(grammar={"parsed_name": "BBQ Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1399)]),
    ]
    check_cross_item_consistency(blocks3)
    flag3 = _get_flag(blocks3[3], "cross_item_category_suggestion")
    report.check("8.3 mixed paths work",
                 flag3 is not None,
                 f"flag: {flag3}")

    # 8.4: High confidence + price penalty suppresses
    blocks4 = [
        _make_tb(category="Beverages"), _make_tb(category="Beverages"),
        _make_tb(category="Beverages"),
        _make_tb(grammar={"parsed_name": "Expensive Item"}, category="Pizza",
                 category_confidence=95,
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(category="Beverages"), _make_tb(category="Beverages"),
        _make_tb(category="Beverages"),
    ]
    check_cross_item_consistency(blocks4)
    report.check("8.4 high conf + price penalty suppresses",
                 _count_flags(blocks4[3], "cross_item_category_suggestion") == 0,
                 f"flags: {blocks4[3].get('price_flags')}")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main() -> None:
    report = TestReport()

    run_basic_suggestion_tests(report)
    run_keyword_signal_tests(report)
    run_price_band_signal_tests(report)
    run_confidence_signal_tests(report)
    run_threshold_tests(report)
    run_edge_case_tests(report)
    run_coexistence_tests(report)
    run_both_path_tests(report)

    print(f"\n{'=' * 60}")
    print(f"Day 63 Results: {report.passed}/{report.total} passed")
    if report.failures:
        print(f"\n{len(report.failures)} FAILURES:")
        for f in report.failures:
            print(f)
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
