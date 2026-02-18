# tests/test_day62_fuzzy_names.py
"""
Day 62: Sprint 8.3 -- Fuzzy Name Matching for Cross-Item Consistency

Tests:
  1. Core fuzzy matching (OCR typos, space variations, character dropout)
  2. Threshold boundary tests (above/below 0.82)
  3. Short name safety (min length enforcement)
  4. Interaction with exact matching (exact takes priority)
  5. Price-aware fuzzy matching (info vs warn)
  6. Real OCR scenarios (multi-path, grammar vs name fields)
  7. Edge cases (single item, unicode, long names)
  8. _name_similarity helper unit tests

Run: python tests/test_day62_fuzzy_names.py
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
    _name_similarity,
    _FUZZY_THRESHOLD,
    _FUZZY_MIN_LEN,
)


# ==================================================================
# Test Infrastructure
# ==================================================================

@dataclass
class TestReport:
    """Accumulates test results."""
    total: int = 0
    passed: int = 0
    failures: List[str] = field(default_factory=list)

    def ok(self, name: str) -> None:
        self.total += 1
        self.passed += 1

    def fail(self, name: str, msg: str) -> None:
        self.total += 1
        self.failures.append(f"  FAIL: {name} -- {msg}")

    def check(self, name: str, condition: bool, msg: str = "") -> None:
        if condition:
            self.ok(name)
        else:
            self.fail(name, msg or "assertion failed")


def _make_tb(
    text: str = "Test Item  10.99",
    grammar: Optional[Dict] = None,
    variants: Optional[List[Dict]] = None,
    category: Optional[str] = None,
    meta: Optional[Dict] = None,
    price_flags: Optional[List[Dict]] = None,
    name: Optional[str] = None,
    price_candidates: Optional[List[Dict]] = None,
    price_cents: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a minimal text_block for testing."""
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
    normalized_size: Optional[str] = None,
    group_key: Optional[str] = None,
    confidence: float = 0.80,
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
    return sum(1 for f in (tb.get("price_flags") or []) if f.get("reason") == reason)


def _get_flag(tb: Dict[str, Any], reason: str) -> Optional[Dict]:
    for f in (tb.get("price_flags") or []):
        if f.get("reason") == reason:
            return f
    return None


def _count_any_fuzzy(tb: Dict[str, Any]) -> int:
    return sum(1 for f in (tb.get("price_flags") or [])
               if f.get("reason", "").startswith("cross_item_fuzzy"))


# ==================================================================
# Group 1: Core Fuzzy Matching
# ==================================================================

def run_core_fuzzy_tests(report: TestReport) -> None:
    print("\n--- Group 1: Core Fuzzy Matching ---")

    # 1.1: OCR typo — "BUFALO WINGS" vs "BUFFALO WINGS" -> fuzzy match
    blocks = [
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Bufalo Wings"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.1 OCR typo bufalo/buffalo",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.2: OCR variant — "MARGHERITA" vs "MARGARITA"
    blocks = [
        _make_tb(grammar={"parsed_name": "Margherita"}, variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Margarita"}, variants=[_make_variant(price_cents=1499)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.2 margherita/margarita",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.3: Space variation — "CHEESEBURGER" vs "CHEESE BURGER"
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheeseburger"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Burger"}, variants=[_make_variant(price_cents=1199)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.3 cheeseburger/cheese burger",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.4: Character dropout — "CHICKEN WINGS" vs "CHICKE WINGS"
    blocks = [
        _make_tb(grammar={"parsed_name": "Chicken Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Chicke Wings"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.4 chicken/chicke dropout",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.5: Transposition — "CALZONE" vs "CAZLONE"
    blocks = [
        _make_tb(grammar={"parsed_name": "Calzone"}, variants=[_make_variant(price_cents=799)]),
        _make_tb(grammar={"parsed_name": "Cazlone"}, variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.5 calzone/cazlone transposition",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.6: Double letter OCR — "PEPPERONI" vs "PEPERONI"
    blocks = [
        _make_tb(grammar={"parsed_name": "Pepperoni"}, variants=[_make_variant(price_cents=1199)]),
        _make_tb(grammar={"parsed_name": "Peperoni"}, variants=[_make_variant(price_cents=1399)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.6 pepperoni/peperoni",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.7: Mozzarella spelling — "MOZZARELLA STICKS" vs "MOZZARELA STICKS"
    blocks = [
        _make_tb(grammar={"parsed_name": "Mozzarella Sticks"}, variants=[_make_variant(price_cents=699)]),
        _make_tb(grammar={"parsed_name": "Mozzarela Sticks"}, variants=[_make_variant(price_cents=899)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.7 mozzarella/mozzarela",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.8: Caesar salad OCR — "CAESAR SALAD" vs "CEASAR SALAD"
    blocks = [
        _make_tb(grammar={"parsed_name": "Caesar Salad"}, variants=[_make_variant(price_cents=799)]),
        _make_tb(grammar={"parsed_name": "Ceasar Salad"}, variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.8 caesar/ceasar",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.9: Philly cheesesteak — single vs double L
    blocks = [
        _make_tb(grammar={"parsed_name": "Philly Cheesesteak"}, variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Phily Cheesesteak"}, variants=[_make_variant(price_cents=1499)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.9 philly/phily",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.10: Jalapeno OCR — "JALAPENO" vs "JALEPENO"
    blocks = [
        _make_tb(grammar={"parsed_name": "Jalapeno Poppers"}, variants=[_make_variant(price_cents=699)]),
        _make_tb(grammar={"parsed_name": "Jalepeno Poppers"}, variants=[_make_variant(price_cents=899)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.10 jalapeno/jalepeno",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.11: Space removal — "BREAD STICKS" vs "BREADSTICKS"
    blocks = [
        _make_tb(grammar={"parsed_name": "Bread Sticks"}, variants=[_make_variant(price_cents=499)]),
        _make_tb(grammar={"parsed_name": "Breadsticks"}, variants=[_make_variant(price_cents=699)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.11 bread sticks/breadsticks",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.12: Quesadilla OCR — "QUESADILLA" vs "QUESIDILLA"
    blocks = [
        _make_tb(grammar={"parsed_name": "Quesadilla"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Quesidilla"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.12 quesadilla/quesidilla",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.13: Both items get flagged (bidirectional)
    blocks = [
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Bufalo Wings"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.13 bidirectional flagging",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1
                 and _count_flags(blocks[1], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {[_count_any_fuzzy(b) for b in blocks]}")

    # 1.14: Different names should NOT fuzzy match — "PIZZA" vs "PASTA"
    blocks = [
        _make_tb(grammar={"parsed_name": "Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Pasta"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.14 pizza/pasta no match",
                 _count_any_fuzzy(blocks[0]) == 0 and _count_any_fuzzy(blocks[1]) == 0,
                 f"flags: {[b.get('price_flags') for b in blocks]}")

    # 1.15: Different items — "BUFFALO WINGS" vs "GARLIC WINGS" should NOT match
    blocks = [
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Garlic Wings"}, variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.15 buffalo/garlic wings no match",
                 _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.16: Completely different items — no match
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Garden Salad"}, variants=[_make_variant(price_cents=799)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.16 cheese pizza/garden salad no match",
                 _count_any_fuzzy(blocks[0]) == 0 and _count_any_fuzzy(blocks[1]) == 0,
                 f"flags: {[b.get('price_flags') for b in blocks]}")

    # 1.17: Singular vs plural — "CHICKEN WING" vs "CHICKEN WINGS"
    blocks = [
        _make_tb(grammar={"parsed_name": "Chicken Wing"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Chicken Wings"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.17 singular/plural fuzzy match",
                 _count_any_fuzzy(blocks[0]) >= 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.18: Nachos OCR — "NACHOS" vs "NACHIS"
    blocks = [
        _make_tb(grammar={"parsed_name": "Nachos"}, variants=[_make_variant(price_cents=599)]),
        _make_tb(grammar={"parsed_name": "Nachis"}, variants=[_make_variant(price_cents=799)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.18 nachos/nachis",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.19: Burger OCR — "BURGER" vs "BURGAR"
    blocks = [
        _make_tb(grammar={"parsed_name": "Burger"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Burgar"}, variants=[_make_variant(price_cents=1199)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.19 burger/burgar",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.20: Chicken OCR — "CHICKEN" vs "CHICKAN"
    blocks = [
        _make_tb(grammar={"parsed_name": "Chicken"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Chickan"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.20 chicken/chickan",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")


# ==================================================================
# Group 2: Threshold Boundary Tests
# ==================================================================

def run_threshold_boundary_tests(report: TestReport) -> None:
    print("\n--- Group 2: Threshold Boundary Tests ---")

    # 2.1: Threshold constant value
    report.check("2.1 threshold is 0.82", _FUZZY_THRESHOLD == 0.82)

    # 2.2: Min length constant value
    report.check("2.2 min length is 4", _FUZZY_MIN_LEN == 4)

    # 2.3: Just above threshold (0.842) — "MARGHERITA" vs "MARGARITA" -> match
    sim = _name_similarity("margherita", "margarita")
    report.check("2.3 margherita sim above threshold",
                 sim >= _FUZZY_THRESHOLD,
                 f"sim={sim:.3f}, threshold={_FUZZY_THRESHOLD}")

    # 2.4: Just below threshold — "SALAD" vs "SALED" (0.800) -> no match
    sim = _name_similarity("salad", "saled")
    report.check("2.4 salad/saled below threshold",
                 sim < _FUZZY_THRESHOLD,
                 f"sim={sim:.3f}")

    blocks = [
        _make_tb(grammar={"parsed_name": "Salad"}, variants=[_make_variant(price_cents=699)]),
        _make_tb(grammar={"parsed_name": "Saled"}, variants=[_make_variant(price_cents=899)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("2.5 below-threshold pair not flagged",
                 _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 2.6: 4-char transposition below threshold — "WRAP" vs "WARP" (0.750) -> no match
    sim = _name_similarity("wrap", "warp")
    report.check("2.6 wrap/warp below threshold",
                 sim < _FUZZY_THRESHOLD,
                 f"sim={sim:.3f}")

    blocks = [
        _make_tb(grammar={"parsed_name": "Wrap"}, variants=[_make_variant(price_cents=599)]),
        _make_tb(grammar={"parsed_name": "Warp"}, variants=[_make_variant(price_cents=799)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("2.7 wrap/warp not flagged",
                 _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 2.8: "TACO" vs "TACE" (0.750) -> no match
    blocks = [
        _make_tb(grammar={"parsed_name": "Taco"}, variants=[_make_variant(price_cents=399)]),
        _make_tb(grammar={"parsed_name": "Tace"}, variants=[_make_variant(price_cents=499)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("2.8 taco/tace not flagged",
                 _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 2.9: "GYRO" vs "GIRO" (0.750) -> no match
    blocks = [
        _make_tb(grammar={"parsed_name": "Gyro"}, variants=[_make_variant(price_cents=699)]),
        _make_tb(grammar={"parsed_name": "Giro"}, variants=[_make_variant(price_cents=899)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("2.9 gyro/giro not flagged",
                 _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 2.10: "SOUP" vs "SOOP" (0.750) -> no match
    blocks = [
        _make_tb(grammar={"parsed_name": "Soup"}, variants=[_make_variant(price_cents=499)]),
        _make_tb(grammar={"parsed_name": "Soop"}, variants=[_make_variant(price_cents=599)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("2.10 soup/soop not flagged",
                 _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 2.11: Very different names — ratio far below threshold
    sim = _name_similarity("cheese pizza", "buffalo wings")
    report.check("2.11 very different names low sim",
                 sim < 0.50,
                 f"sim={sim:.3f}")

    # 2.12: Identical names — ratio 1.0 (handled by exact match, not fuzzy)
    sim = _name_similarity("chicken wings", "chicken wings")
    report.check("2.12 identical names ratio 1.0", sim == 1.0)

    # 2.13: High similarity (0.96) well above threshold
    sim = _name_similarity("buffalo wings", "bufalo wings")
    report.check("2.13 high sim above threshold",
                 sim > _FUZZY_THRESHOLD + 0.10,
                 f"sim={sim:.3f}")

    # 2.14: French fries/frie — very different lengths, no match (0.50)
    blocks = [
        _make_tb(grammar={"parsed_name": "French Fries"}, variants=[_make_variant(price_cents=399)]),
        _make_tb(grammar={"parsed_name": "Frie"}, variants=[_make_variant(price_cents=299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("2.14 french fries/frie no match",
                 _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")


# ==================================================================
# Group 3: Short Name Safety
# ==================================================================

def run_short_name_tests(report: TestReport) -> None:
    print("\n--- Group 3: Short Name Safety ---")

    # 3.1: 3-char name with 1-char diff — below min length, NOT fuzzy-matched
    blocks = [
        _make_tb(grammar={"parsed_name": "Sub"}, variants=[_make_variant(price_cents=599)]),
        _make_tb(grammar={"parsed_name": "Sup"}, variants=[_make_variant(price_cents=799)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.1 3-char names not fuzzy matched",
                 _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 3.2: 3-char identical names — still exact matched (exact has min 3)
    blocks = [
        _make_tb(grammar={"parsed_name": "Sub"}, variants=[_make_variant(price_cents=599)]),
        _make_tb(grammar={"parsed_name": "Sub"}, variants=[_make_variant(price_cents=599)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.2 3-char exact still works",
                 _count_flags(blocks[0], "cross_item_exact_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 3.3: "BBQ" vs "BBA" — 3 chars, no fuzzy check
    blocks = [
        _make_tb(grammar={"parsed_name": "BBQ"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "BBA"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.3 bbq/bba no fuzzy",
                 _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 3.4: 4-char name eligible for fuzzy (meets min length)
    # "TACO" similarity checks will be attempted, just may not match
    report.check("3.4 min length is 4", _FUZZY_MIN_LEN == 4)

    # 3.5: 2-char names — below exact match min (3), skipped entirely
    blocks = [
        _make_tb(grammar={"parsed_name": "AB"}, variants=[_make_variant(price_cents=599)]),
        _make_tb(grammar={"parsed_name": "AB"}, variants=[_make_variant(price_cents=599)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.5 2-char names skipped entirely",
                 _count_flags(blocks[0], "cross_item_exact_duplicate") == 0
                 and _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 3.6: Empty names skipped
    blocks = [
        _make_tb(grammar={"parsed_name": ""}, text=""),
        _make_tb(grammar={"parsed_name": ""}, text=""),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.6 empty names no flags",
                 _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 3.7: 1-char names skipped
    blocks = [
        _make_tb(grammar={"parsed_name": "A"}, variants=[_make_variant(price_cents=100)]),
        _make_tb(grammar={"parsed_name": "B"}, variants=[_make_variant(price_cents=200)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.7 1-char names no flags",
                 _count_any_fuzzy(blocks[0]) == 0
                 and _count_flags(blocks[0], "cross_item_duplicate_name") == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 3.8: 4-char name with high similarity — "WING" vs "WINGS" = 0.889 -> matches
    blocks = [
        _make_tb(grammar={"parsed_name": "Wing"}, variants=[_make_variant(price_cents=799)]),
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.8 wing/wings 4+ chars fuzzy match",
                 _count_any_fuzzy(blocks[0]) >= 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 3.9: Longer names more tolerant of differences
    blocks = [
        _make_tb(grammar={"parsed_name": "Mediterranean Chicken Salad"}, variants=[_make_variant(price_cents=1099)]),
        _make_tb(grammar={"parsed_name": "Mediteranean Chicken Salad"}, variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.9 long name OCR typo detected",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")


# ==================================================================
# Group 4: Interaction with Exact Matching
# ==================================================================

def run_exact_interaction_tests(report: TestReport) -> None:
    print("\n--- Group 4: Interaction with Exact Matching ---")

    # 4.1: Exact duplicates still detected as exact (NOT downgraded to fuzzy)
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("4.1 exact still exact",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1
                 and _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 4.2: Exact same-price still detected as exact
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("4.2 exact same-price still info",
                 _count_flags(blocks[0], "cross_item_exact_duplicate") == 1
                 and _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 4.3: Mix of exact and fuzzy duplicates in same menu
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=1299)]),  # exact dup of [0]
        _make_tb(grammar={"parsed_name": "Cheeze Pizza"}, variants=[_make_variant(price_cents=1499)]),  # fuzzy dup of both
    ]
    check_cross_item_consistency(blocks)
    report.check("4.3a exact dup found",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags[0]: {blocks[0].get('price_flags')}")
    report.check("4.3b fuzzy dup found on typo",
                 _count_any_fuzzy(blocks[2]) >= 1,
                 f"flags[2]: {blocks[2].get('price_flags')}")

    # 4.4: Three items — two exact, one fuzzy — all flagged correctly
    blocks = [
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=1099)]),
        _make_tb(grammar={"parsed_name": "Wigns"}, variants=[_make_variant(price_cents=1199)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("4.4a exact pair flagged",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags[0]: {blocks[0].get('price_flags')}")
    # "wigns" vs "wings" similarity = 0.80 — just below 0.82 threshold
    # So fuzzy should NOT match here
    sim = _name_similarity("wings", "wigns")
    if sim >= _FUZZY_THRESHOLD:
        report.check("4.4b fuzzy dup found on typo",
                     _count_any_fuzzy(blocks[2]) >= 1,
                     f"flags[2]: {blocks[2].get('price_flags')}")
    else:
        report.check("4.4b wigns below threshold so no fuzzy",
                     _count_any_fuzzy(blocks[2]) == 0,
                     f"sim={sim:.3f}, flags[2]: {blocks[2].get('price_flags')}")

    # 4.5: Items in exact groups don't get spurious fuzzy flags with each other
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("4.5 no fuzzy within exact group",
                 all(_count_any_fuzzy(b) == 0 for b in blocks),
                 f"fuzzy flags: {[_count_any_fuzzy(b) for b in blocks]}")

    # 4.6: Normalized forms match exactly — uses exact, not fuzzy
    # "The Pizza" and "Pizza" normalize to "pizza" (exact match)
    blocks = [
        _make_tb(grammar={"parsed_name": "The Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Pizza"}, variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("4.6 prefix-stripped exact match",
                 _count_flags(blocks[0], "cross_item_exact_duplicate") == 1
                 and _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 4.7: Fuzzy match across two different exact groups
    # Group A: "Wings" x2, Group B: "Wigns" x2 — fuzzy between groups
    blocks = [
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Bufalo Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Bufalo Wings"}, variants=[_make_variant(price_cents=899)]),
    ]
    check_cross_item_consistency(blocks)
    # Items 0,1 should have exact dup flags
    report.check("4.7a exact group A",
                 _count_flags(blocks[0], "cross_item_exact_duplicate") == 1,
                 f"flags[0]: {blocks[0].get('price_flags')}")
    # Items 2,3 should have exact dup flags
    report.check("4.7b exact group B",
                 _count_flags(blocks[2], "cross_item_exact_duplicate") == 1,
                 f"flags[2]: {blocks[2].get('price_flags')}")
    # Cross-group fuzzy: items in group A should fuzzy-match items in group B
    report.check("4.7c cross-group fuzzy",
                 _count_any_fuzzy(blocks[0]) >= 1,
                 f"flags[0]: {blocks[0].get('price_flags')}")


# ==================================================================
# Group 5: Price-Aware Fuzzy Matching
# ==================================================================

def run_price_aware_tests(report: TestReport) -> None:
    print("\n--- Group 5: Price-Aware Fuzzy ---")

    # 5.1: Fuzzy match, same price -> info
    blocks = [
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Bufalo Wings"}, variants=[_make_variant(price_cents=899)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("5.1 same price -> info",
                 _count_flags(blocks[0], "cross_item_fuzzy_exact_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 5.2: Fuzzy match, different price -> warn
    blocks = [
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Bufalo Wings"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("5.2 diff price -> warn",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 5.3: Severity check — info for same price
    blocks = [
        _make_tb(grammar={"parsed_name": "Caesar Salad"}, variants=[_make_variant(price_cents=799)]),
        _make_tb(grammar={"parsed_name": "Ceasar Salad"}, variants=[_make_variant(price_cents=799)]),
    ]
    check_cross_item_consistency(blocks)
    flag = _get_flag(blocks[0], "cross_item_fuzzy_exact_duplicate")
    report.check("5.3 severity is info",
                 flag is not None and flag["severity"] == "info",
                 f"flag: {flag}")

    # 5.4: Severity check — warn for different price
    blocks = [
        _make_tb(grammar={"parsed_name": "Caesar Salad"}, variants=[_make_variant(price_cents=799)]),
        _make_tb(grammar={"parsed_name": "Ceasar Salad"}, variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    flag = _get_flag(blocks[0], "cross_item_fuzzy_duplicate")
    report.check("5.4 severity is warn",
                 flag is not None and flag["severity"] == "warn",
                 f"flag: {flag}")

    # 5.5: Zero-price fuzzy match -> treated as different prices (warn)
    blocks = [
        _make_tb(grammar={"parsed_name": "Mozzarella Sticks"}, variants=[_make_variant(price_cents=699)]),
        _make_tb(grammar={"parsed_name": "Mozzarela Sticks"}),  # no price -> 0
    ]
    check_cross_item_consistency(blocks)
    report.check("5.5 zero-price is warn",
                 _count_flags(blocks[0], "cross_item_fuzzy_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 5.6: Both zero-price -> same price (info)
    blocks = [
        _make_tb(grammar={"parsed_name": "Mozzarella Sticks"}),
        _make_tb(grammar={"parsed_name": "Mozzarela Sticks"}),
    ]
    check_cross_item_consistency(blocks)
    report.check("5.6 both zero -> info",
                 _count_flags(blocks[0], "cross_item_fuzzy_exact_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 5.7: Flag details contain similarity ratio
    blocks = [
        _make_tb(grammar={"parsed_name": "Pepperoni"}, variants=[_make_variant(price_cents=1199)]),
        _make_tb(grammar={"parsed_name": "Peperoni"}, variants=[_make_variant(price_cents=1399)]),
    ]
    check_cross_item_consistency(blocks)
    flag = _get_flag(blocks[0], "cross_item_fuzzy_duplicate")
    report.check("5.7 similarity in details",
                 flag is not None
                 and "similarity" in flag["details"]
                 and isinstance(flag["details"]["similarity"], float)
                 and flag["details"]["similarity"] >= _FUZZY_THRESHOLD,
                 f"flag: {flag}")

    # 5.8: Flag details contain matched_name
    report.check("5.8 matched_name in details",
                 flag is not None and flag["details"]["matched_name"] == "peperoni",
                 f"details: {flag['details'] if flag else None}")

    # 5.9: Flag details contain matched_index
    report.check("5.9 matched_index in details",
                 flag is not None and flag["details"]["matched_index"] == 1,
                 f"details: {flag['details'] if flag else None}")

    # 5.10: Flag details contain this_name
    report.check("5.10 this_name in details",
                 flag is not None and flag["details"]["this_name"] == "pepperoni",
                 f"details: {flag['details'] if flag else None}")

    # 5.11: Flag details contain prices
    report.check("5.11 prices in details",
                 flag is not None
                 and flag["details"]["this_price_cents"] == 1199
                 and flag["details"]["matched_price_cents"] == 1399,
                 f"details: {flag['details'] if flag else None}")


# ==================================================================
# Group 6: Real OCR Scenarios
# ==================================================================

def run_real_ocr_tests(report: TestReport) -> None:
    print("\n--- Group 6: Real OCR Scenarios ---")

    # 6.1: Multi-item menu with one fuzzy pair among many unique items
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1099)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1199)]),
        _make_tb(grammar={"parsed_name": "Peperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1299)]),  # fuzzy of [1]
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1399)]),
        _make_tb(grammar={"parsed_name": "Hawaiian"}, category="Pizza", variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.1a fuzzy pair found",
                 _count_any_fuzzy(blocks[1]) >= 1,
                 f"flags[1]: {blocks[1].get('price_flags')}")
    report.check("6.1b non-fuzzy items clean",
                 _count_any_fuzzy(blocks[0]) == 0
                 and _count_any_fuzzy(blocks[3]) == 0
                 and _count_any_fuzzy(blocks[4]) == 0,
                 f"flags: {[_count_any_fuzzy(b) for b in blocks]}")

    # 6.2: Items from grammar path (pipeline) fuzzy-match items from name path (ai_ocr_helper)
    blocks = [
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(name="Bufalo Wings", price_candidates=[{"value": 10.99}]),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.2 cross-path fuzzy match",
                 _count_any_fuzzy(blocks[0]) >= 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 6.3: merged_text fallback fuzzy match
    blocks = [
        _make_tb(text="Mozzarella Sticks 6.99", variants=[_make_variant(price_cents=699)]),
        _make_tb(text="Mozzarela Sticks 8.99", variants=[_make_variant(price_cents=899)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.3 merged_text fallback fuzzy",
                 _count_any_fuzzy(blocks[0]) >= 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 6.4: Fuzzy with prefix stripping — "Our Buffalo Wings" vs "Bufalo Wings"
    blocks = [
        _make_tb(grammar={"parsed_name": "Our Buffalo Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Bufalo Wings"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    # "Our Buffalo Wings" normalizes to "buffalo wings", "Bufalo Wings" normalizes to "bufalo wings"
    report.check("6.4 prefix-stripped then fuzzy",
                 _count_any_fuzzy(blocks[0]) >= 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 6.5: Realistic duplicate across sections — same item appears in two categories
    blocks = [
        _make_tb(grammar={"parsed_name": "Garlic Bread"}, category="Appetizers", variants=[_make_variant(price_cents=499)]),
        _make_tb(grammar={"parsed_name": "Cheese Sticks"}, category="Appetizers", variants=[_make_variant(price_cents=699)]),
        _make_tb(grammar={"parsed_name": "Garlic Bred"}, category="Sides", variants=[_make_variant(price_cents=499)]),  # OCR typo
        _make_tb(grammar={"parsed_name": "French Fries"}, category="Sides", variants=[_make_variant(price_cents=399)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.5 cross-section fuzzy dup",
                 _count_any_fuzzy(blocks[0]) >= 1,
                 f"flags[0]: {blocks[0].get('price_flags')}")

    # 6.6: Multiple fuzzy pairs in one menu
    blocks = [
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Bufalo Wings"}, variants=[_make_variant(price_cents=1099)]),
        _make_tb(grammar={"parsed_name": "Caesar Salad"}, variants=[_make_variant(price_cents=799)]),
        _make_tb(grammar={"parsed_name": "Ceasar Salad"}, variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.6a first pair flagged",
                 _count_any_fuzzy(blocks[0]) >= 1,
                 f"flags[0]: {blocks[0].get('price_flags')}")
    report.check("6.6b second pair flagged",
                 _count_any_fuzzy(blocks[2]) >= 1,
                 f"flags[2]: {blocks[2].get('price_flags')}")

    # 6.7: No cross-match between different fuzzy pairs
    # "Buffalo Wings"/"Bufalo Wings" should not fuzzy-match "Caesar Salad"/"Ceasar Salad"
    flag0 = _get_flag(blocks[0], "cross_item_fuzzy_duplicate") or _get_flag(blocks[0], "cross_item_fuzzy_exact_duplicate")
    report.check("6.7 no cross-pair contamination",
                 flag0 is not None and flag0["details"]["matched_index"] == 1,
                 f"flag: {flag0}")

    # 6.8: Clean menu with no similar names -> no fuzzy flags
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=1099)]),
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Caesar Salad"}, variants=[_make_variant(price_cents=799)]),
        _make_tb(grammar={"parsed_name": "French Fries"}, variants=[_make_variant(price_cents=399)]),
        _make_tb(grammar={"parsed_name": "Coke"}, variants=[_make_variant(price_cents=199)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.8 clean menu no fuzzy flags",
                 all(_count_any_fuzzy(b) == 0 for b in blocks),
                 f"fuzzy: {[_count_any_fuzzy(b) for b in blocks]}")

    # 6.9: Existing price_flags preserved alongside fuzzy flags
    existing_flag = {"severity": "warn", "reason": "variant_price_inversion", "details": {}}
    blocks = [
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, variants=[_make_variant(price_cents=1199)],
                 price_flags=[existing_flag]),
        _make_tb(grammar={"parsed_name": "Peperoni Pizza"}, variants=[_make_variant(price_cents=1399)]),
    ]
    check_cross_item_consistency(blocks)
    reasons = {f["reason"] for f in blocks[0].get("price_flags", [])}
    report.check("6.9 existing flags preserved",
                 "variant_price_inversion" in reasons and any("fuzzy" in r for r in reasons),
                 f"reasons: {reasons}")

    # 6.10: All flags have cross_item_ prefix
    all_flags = [f for b in blocks for f in b.get("price_flags", []) if f["reason"] != "variant_price_inversion"]
    report.check("6.10 all flags have cross_item_ prefix",
                 all(f["reason"].startswith("cross_item_") for f in all_flags),
                 f"reasons: {[f['reason'] for f in all_flags]}")


# ==================================================================
# Group 7: Edge Cases
# ==================================================================

def run_edge_case_tests(report: TestReport) -> None:
    print("\n--- Group 7: Edge Cases ---")

    # 7.1: Single item -> no flags
    blocks = [_make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)])]
    check_cross_item_consistency(blocks)
    report.check("7.1 single item no flags",
                 len(blocks[0].get("price_flags", [])) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 7.2: All items identical -> exact match only, no fuzzy
    blocks = [
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=899)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("7.2 all identical -> exact only",
                 all(_count_flags(b, "cross_item_exact_duplicate") == 1 for b in blocks)
                 and all(_count_any_fuzzy(b) == 0 for b in blocks),
                 f"exact: {[_count_flags(b, 'cross_item_exact_duplicate') for b in blocks]}, "
                 f"fuzzy: {[_count_any_fuzzy(b) for b in blocks]}")

    # 7.3: Very long names (50+ chars)
    blocks = [
        _make_tb(grammar={"parsed_name": "Super Deluxe Mediterranean Chicken Salad With Extra Feta"},
                 variants=[_make_variant(price_cents=1499)]),
        _make_tb(grammar={"parsed_name": "Super Deluxe Mediteranean Chicken Salad With Extra Feta"},
                 variants=[_make_variant(price_cents=1699)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("7.3 long names fuzzy match",
                 _count_any_fuzzy(blocks[0]) >= 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 7.4: Empty text_blocks -> no crash
    blocks: List[Dict[str, Any]] = []
    check_cross_item_consistency(blocks)
    report.check("7.4 empty list no crash", True)

    # 7.5: Large menu (50 items) no crash — use very distinct names
    distinct_names = [
        "Cheese Pizza", "Buffalo Wings", "Caesar Salad", "French Fries", "Onion Rings",
        "Garlic Bread", "Mozzarella Sticks", "Chicken Tenders", "Veggie Burger", "Fish Tacos",
        "Lobster Bisque", "Clam Chowder", "Grilled Salmon", "Ribeye Steak", "Lamb Chops",
        "Shrimp Cocktail", "Crab Cakes", "Tuna Tartare", "Duck Confit", "Pork Belly",
        "Mushroom Risotto", "Pasta Primavera", "Fettuccine Alfredo", "Spaghetti Bolognese", "Penne Vodka",
        "Margherita Flatbread", "Hawaiian Poke", "Beef Carpaccio", "Bruschetta", "Antipasto Platter",
        "Calamari Fritti", "Spring Rolls", "Edamame", "Gyoza Dumplings", "Wonton Soup",
        "Pad Thai Noodles", "Kung Pao Chicken", "General Tso Tofu", "Orange Beef", "Mango Curry",
        "Tikka Masala", "Naan Bread", "Basmati Rice", "Hummus Platter", "Falafel Wrap",
        "Shawarma Plate", "Tabouleh Salad", "Baba Ghanoush", "Dolma Grape Leaves", "Spanakopita",
    ]
    blocks = [
        _make_tb(grammar={"parsed_name": n}, variants=[_make_variant(price_cents=500 + i * 100)])
        for i, n in enumerate(distinct_names)
    ]
    check_cross_item_consistency(blocks)
    report.check("7.5 50-item menu no crash", True)

    # 7.6: Names with special characters
    blocks = [
        _make_tb(grammar={"parsed_name": "Mac & Cheese"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Mac & Cheeze"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("7.6 special chars fuzzy match",
                 _count_any_fuzzy(blocks[0]) >= 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 7.7: Numbers in names — "12 PIECE WINGS" vs "12 PEICE WINGS"
    blocks = [
        _make_tb(grammar={"parsed_name": "12 Piece Wings"}, variants=[_make_variant(price_cents=1499)]),
        _make_tb(grammar={"parsed_name": "12 Peice Wings"}, variants=[_make_variant(price_cents=1699)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("7.7 numbers in names fuzzy match",
                 _count_any_fuzzy(blocks[0]) >= 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 7.8: Apostrophes — "JOEY'S SPECIAL" vs "JOEYS SPECIAL"
    blocks = [
        _make_tb(grammar={"parsed_name": "Joey's Special"}, variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Joeys Special"}, variants=[_make_variant(price_cents=1499)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("7.8 apostrophe variation",
                 _count_any_fuzzy(blocks[0]) >= 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 7.9: Fuzzy match does not fire between truly different items with shared suffix
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=1099)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("7.9 cheese pizza/veggie pizza no fuzzy",
                 _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 7.10: Items with only whitespace differences handled by exact (normalization)
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese  Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("7.10 whitespace normalized -> exact",
                 _count_flags(blocks[0], "cross_item_exact_duplicate") == 1
                 and _count_any_fuzzy(blocks[0]) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 7.11: Fuzzy + other cross-item flags can coexist
    blocks = [
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1199)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1399)]),
        _make_tb(grammar={"parsed_name": "Peperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=100)]),  # fuzzy + outlier
    ]
    check_cross_item_consistency(blocks)
    report.check("7.11 fuzzy + outlier coexist",
                 _count_any_fuzzy(blocks[3]) >= 1
                 and _count_flags(blocks[3], "cross_item_category_price_outlier") == 1,
                 f"flags[3]: {blocks[3].get('price_flags')}")


# ==================================================================
# Group 8: _name_similarity Helper Unit Tests
# ==================================================================

def run_similarity_helper_tests(report: TestReport) -> None:
    print("\n--- Group 8: _name_similarity Helper ---")

    # 8.1: Identical strings -> 1.0
    report.check("8.1 identical = 1.0", _name_similarity("pizza", "pizza") == 1.0)

    # 8.2: Empty strings -> 1.0 (SequenceMatcher considers both empty as identical)
    report.check("8.2 both empty = 1.0", _name_similarity("", "") == 1.0)

    # 8.3: One empty -> 0.0
    report.check("8.3 one empty = 0.0", _name_similarity("pizza", "") == 0.0)

    # 8.4: Completely different -> low ratio
    sim = _name_similarity("abcdef", "xyz")
    report.check("8.4 completely different low sim",
                 sim < 0.3,
                 f"sim={sim:.3f}")

    # 8.5: One char difference in 7 chars -> high ratio
    sim = _name_similarity("chicken", "chickan")
    report.check("8.5 one char diff high sim",
                 0.80 < sim < 1.0,
                 f"sim={sim:.3f}")

    # 8.6: Symmetry — sim(a,b) == sim(b,a)
    sim_ab = _name_similarity("buffalo", "bufalo")
    sim_ba = _name_similarity("bufalo", "buffalo")
    report.check("8.6 symmetry", sim_ab == sim_ba,
                 f"ab={sim_ab:.3f}, ba={sim_ba:.3f}")

    # 8.7: Return type is float
    report.check("8.7 return type is float",
                 isinstance(_name_similarity("a", "b"), float))

    # 8.8: Range is [0.0, 1.0]
    sim = _name_similarity("test", "different")
    report.check("8.8 range check",
                 0.0 <= sim <= 1.0,
                 f"sim={sim}")


# ==================================================================
# Main
# ==================================================================

def main() -> None:
    report = TestReport()

    run_core_fuzzy_tests(report)
    run_threshold_boundary_tests(report)
    run_short_name_tests(report)
    run_exact_interaction_tests(report)
    run_price_aware_tests(report)
    run_real_ocr_tests(report)
    run_edge_case_tests(report)
    run_similarity_helper_tests(report)

    print(f"\n{'=' * 60}")
    print(f"Day 62 Results: {report.passed}/{report.total} passed")
    if report.failures:
        print(f"\n{len(report.failures)} FAILURES:")
        for f in report.failures:
            print(f)
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
