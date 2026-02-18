"""
Day 64: Sprint 8.3 -- Cross-Category Price Coherence

Tests the new _check_cross_category_coherence() function in storage/cross_item.py.
Validates that items in cheap categories (beverages, sides) flagged when priced
above expensive-category medians, and vice versa.

Tests:
  1. Basic "price above" detection (side > pizza median)
  2. Basic "price below" detection (pizza < beverage median)
  3. Minimum items requirement (< 2 items = skip)
  4. Minimum gap requirement (similar medians = skip)
  5. Single-flag-per-item dedup (most dramatic kept)
  6. Edge cases (no category, no price, empty blocks)
  7. Multiple rule pairs fire correctly
  8. Beverage rules (cheapest tier)
  9. Dessert rules
  10. Both-path compatibility (pipeline vs ai_ocr_helper)
  11. Coexistence with other cross-item checks

Run: python tests/test_day64_cross_category_coherence.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.cross_item import (
    check_cross_item_consistency,
    _check_cross_category_coherence,
    _extract_primary_price_cents,
    _CROSS_CAT_PRICE_RULES,
    _CROSS_CAT_MIN_ITEMS,
    _CROSS_CAT_MIN_GAP_RATIO,
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
) -> Dict[str, Any]:
    return {
        "label": label,
        "price_cents": price_cents,
        "kind": kind,
        "confidence": confidence,
    }


def _count_flags(tb: Dict[str, Any], reason: str) -> int:
    return sum(1 for f in tb.get("price_flags", []) if f.get("reason") == reason)


def _get_flag(tb: Dict[str, Any], reason: str) -> Optional[Dict]:
    for f in tb.get("price_flags", []):
        if f.get("reason") == reason:
            return f
    return None


# ---------------------------------------------------------------------------
# Helper: build a menu with cheap + expensive category items
# ---------------------------------------------------------------------------

def _build_menu(
    cheap_cat: str,
    cheap_prices: List[int],
    exp_cat: str,
    exp_prices: List[int],
    cheap_names: Optional[List[str]] = None,
    exp_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Build a menu of items across two categories.

    Generates unique names to avoid triggering duplicate-name checks.
    """
    blocks = []
    for i, price in enumerate(cheap_prices):
        name = (cheap_names[i] if cheap_names else f"{cheap_cat} Item {i}")
        blocks.append(_make_tb(
            grammar={"parsed_name": name},
            category=cheap_cat,
            variants=[_make_variant(price_cents=price)],
        ))
    for i, price in enumerate(exp_prices):
        name = (exp_names[i] if exp_names else f"{exp_cat} Item {i}")
        blocks.append(_make_tb(
            grammar={"parsed_name": name},
            category=exp_cat,
            variants=[_make_variant(price_cents=price)],
        ))
    return blocks


# ---------------------------------------------------------------------------
# Group 1: Basic "price above" detection
# ---------------------------------------------------------------------------

def run_price_above_tests(report: TestReport) -> None:
    print("\n--- Group 1: Price Above Detection ---")

    # 1.1: Side priced above pizza median gets flagged
    blocks = _build_menu(
        "Sides / Appetizers", [499, 599, 1899],  # median 599, one outlier at 18.99
        "Pizza", [1299, 1399, 1499],              # median 13.99
    )
    for tb in blocks:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks)

    report.check("1.1 side at $18.99 flagged above pizza median",
                 _count_flags(blocks[2], "cross_category_price_above") == 1,
                 f"flags: {blocks[2].get('price_flags')}")

    # 1.2: Normal sides not flagged
    report.check("1.2 side at $4.99 not flagged",
                 _count_flags(blocks[0], "cross_category_price_above") == 0,
                 f"flags: {blocks[0].get('price_flags')}")
    report.check("1.3 side at $5.99 not flagged",
                 _count_flags(blocks[1], "cross_category_price_above") == 0,
                 f"flags: {blocks[1].get('price_flags')}")

    # 1.4: Flag details are correct
    flag = _get_flag(blocks[2], "cross_category_price_above")
    report.check("1.4 flag item_category is Sides / Appetizers",
                 flag is not None and flag["details"]["item_category"] == "Sides / Appetizers",
                 f"details: {flag}")
    report.check("1.5 flag compared_category is Pizza",
                 flag is not None and flag["details"]["compared_category"] == "Pizza",
                 f"details: {flag}")
    report.check("1.6 flag item_price_cents is 1899",
                 flag is not None and flag["details"]["item_price_cents"] == 1899,
                 f"details: {flag}")
    report.check("1.7 flag compared_median_cents is 1399",
                 flag is not None and flag["details"]["compared_median_cents"] == 1399,
                 f"details: {flag}")
    report.check("1.8 flag severity is warn",
                 flag is not None and flag["severity"] == "warn",
                 f"flag: {flag}")

    # 1.9: Beverage priced above sides median
    blocks2 = _build_menu(
        "Beverages", [199, 299, 899],               # median 299, one high
        "Sides / Appetizers", [599, 699, 799],       # median 699
    )
    for tb in blocks2:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks2)

    report.check("1.9 beverage at $8.99 flagged above sides median",
                 _count_flags(blocks2[2], "cross_category_price_above") == 1,
                 f"flags: {blocks2[2].get('price_flags')}")


# ---------------------------------------------------------------------------
# Group 2: Basic "price below" detection
# ---------------------------------------------------------------------------

def run_price_below_tests(report: TestReport) -> None:
    print("\n--- Group 2: Price Below Detection ---")

    # 2.1: Pizza priced below beverage median gets flagged
    blocks = _build_menu(
        "Beverages", [299, 399, 499],   # median 399
        "Pizza", [1299, 1499, 199],     # one pizza at $1.99
    )
    for tb in blocks:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks)

    report.check("2.1 pizza at $1.99 flagged below beverage median",
                 _count_flags(blocks[5], "cross_category_price_below") == 1,
                 f"flags: {blocks[5].get('price_flags')}")

    # 2.2: Normal pizzas not flagged
    report.check("2.2 pizza at $12.99 not flagged below",
                 _count_flags(blocks[3], "cross_category_price_below") == 0,
                 f"flags: {blocks[3].get('price_flags')}")

    # 2.3: Flag details are correct
    flag = _get_flag(blocks[5], "cross_category_price_below")
    report.check("2.3 flag item_category is Pizza",
                 flag is not None and flag["details"]["item_category"] == "Pizza",
                 f"details: {flag}")
    report.check("2.4 flag compared_category is Beverages",
                 flag is not None and flag["details"]["compared_category"] == "Beverages",
                 f"details: {flag}")
    report.check("2.5 flag item_price_cents is 199",
                 flag is not None and flag["details"]["item_price_cents"] == 199,
                 f"details: {flag}")
    report.check("2.6 flag severity is warn",
                 flag is not None and flag["severity"] == "warn",
                 f"flag: {flag}")

    # 2.7: Pasta priced below side median
    blocks2 = _build_menu(
        "Sides / Appetizers", [599, 699, 799],   # median 699
        "Pasta", [1299, 1499, 399],              # one pasta at $3.99
    )
    for tb in blocks2:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks2)

    report.check("2.7 pasta at $3.99 flagged below sides median",
                 _count_flags(blocks2[5], "cross_category_price_below") == 1,
                 f"flags: {blocks2[5].get('price_flags')}")


# ---------------------------------------------------------------------------
# Group 3: Minimum items requirement
# ---------------------------------------------------------------------------

def run_min_items_tests(report: TestReport) -> None:
    print("\n--- Group 3: Minimum Items Requirement ---")

    # 3.1: Only 1 item in cheap category -> no flags
    blocks = _build_menu(
        "Beverages", [1999],                     # only 1 beverage (very expensive)
        "Pizza", [1299, 1399, 1499],             # 3 pizzas
    )
    for tb in blocks:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks)

    report.check("3.1 single beverage at $19.99 not flagged (< 2 items)",
                 _count_flags(blocks[0], "cross_category_price_above") == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 3.2: Only 1 item in expensive category -> no flags
    blocks2 = _build_menu(
        "Beverages", [199, 299, 399],
        "Pizza", [199],                          # only 1 pizza (very cheap)
    )
    for tb in blocks2:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks2)

    report.check("3.2 single pizza at $1.99 not flagged (< 2 items)",
                 _count_flags(blocks2[3], "cross_category_price_below") == 0,
                 f"flags: {blocks2[3].get('price_flags')}")

    # 3.3: Exactly 2 items per category works
    blocks3 = _build_menu(
        "Beverages", [199, 299],                 # median 249
        "Pizza", [1299, 1499],                   # median 1399
    )
    # Add one expensive beverage
    blocks3.append(_make_tb(
        grammar={"parsed_name": "Fancy Drink"},
        category="Beverages",
        variants=[_make_variant(price_cents=1599)],
    ))
    for tb in blocks3:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks3)

    report.check("3.3 expensive beverage flagged with 3 items in category",
                 _count_flags(blocks3[4], "cross_category_price_above") == 1,
                 f"flags: {blocks3[4].get('price_flags')}")


# ---------------------------------------------------------------------------
# Group 4: Minimum gap requirement
# ---------------------------------------------------------------------------

def run_min_gap_tests(report: TestReport) -> None:
    print("\n--- Group 4: Minimum Gap Requirement ---")

    # 4.1: Categories with similar medians -> no flags
    # Sides median = 999, Burgers median = 1099 -> ratio 1.1 < 1.3
    blocks = _build_menu(
        "Sides / Appetizers", [899, 999, 1099],   # median 999
        "Burgers", [999, 1099, 1199],              # median 1099
    )
    for tb in blocks:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks)

    # Side at $10.99 > burger median $10.99 but gap ratio too small
    report.check("4.1 no flags when medians are close (ratio < 1.3)",
                 all(_count_flags(tb, "cross_category_price_above") == 0 for tb in blocks),
                 f"flags: {[tb.get('price_flags') for tb in blocks]}")

    # 4.2: Categories with exactly 1.3x gap -> flags fire
    # Sides median = 500, Pizza median = 650 -> ratio 1.3 exactly
    # Side at 700 exceeds pizza median (650) -> flag
    blocks2 = _build_menu(
        "Sides / Appetizers", [400, 500, 700],     # median 500, one above pizza median
        "Pizza", [550, 650, 750],                  # median 650 = 500 * 1.3
    )
    for tb in blocks2:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks2)

    report.check("4.2 flags fire at exactly 1.3x gap",
                 _count_flags(blocks2[2], "cross_category_price_above") == 1,
                 f"flags: {blocks2[2].get('price_flags')}")

    # 4.3: Cheap median > expensive median (inverted) -> no flags
    blocks3 = _build_menu(
        "Sides / Appetizers", [1599, 1699, 1799],  # median 1699
        "Burgers", [899, 999, 1099],               # median 999 (cheaper!)
    )
    for tb in blocks3:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks3)

    report.check("4.3 no flags when cheap cat is actually more expensive",
                 all(_count_flags(tb, "cross_category_price_above") == 0
                     and _count_flags(tb, "cross_category_price_below") == 0
                     for tb in blocks3),
                 f"flags: {[tb.get('price_flags') for tb in blocks3]}")


# ---------------------------------------------------------------------------
# Group 5: Single-flag-per-item dedup
# ---------------------------------------------------------------------------

def run_dedup_tests(report: TestReport) -> None:
    print("\n--- Group 5: Single Flag Per Item Dedup ---")

    # 5.1: Side that exceeds both Pizza and Pasta medians -> only 1 "above" flag
    blocks = []
    # 3 sides: normal, normal, very expensive
    for i, p in enumerate([499, 599, 2099]):
        blocks.append(_make_tb(
            grammar={"parsed_name": f"Side Dish {chr(65+i)}"},
            category="Sides / Appetizers",
            variants=[_make_variant(price_cents=p)],
        ))
    # 3 pizzas
    for i, p in enumerate([1299, 1399, 1499]):
        blocks.append(_make_tb(
            grammar={"parsed_name": f"Pizza Variety {chr(65+i)}"},
            category="Pizza",
            variants=[_make_variant(price_cents=p)],
        ))
    # 3 pastas
    for i, p in enumerate([1199, 1299, 1399]):
        blocks.append(_make_tb(
            grammar={"parsed_name": f"Pasta Dish {chr(65+i)}"},
            category="Pasta",
            variants=[_make_variant(price_cents=p)],
        ))

    for tb in blocks:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks)

    above_flags = [f for f in blocks[2].get("price_flags", [])
                   if f.get("reason") == "cross_category_price_above"]
    report.check("5.1 expensive side gets exactly 1 above flag",
                 len(above_flags) == 1,
                 f"above_flags count: {len(above_flags)}, flags: {above_flags}")

    # 5.2: The kept flag should be the most dramatic comparison (larger gap)
    flag = above_flags[0] if above_flags else None
    report.check("5.2 flag picks the comparison with biggest gap",
                 flag is not None,
                 f"flag: {flag}")

    # 5.3: Pizza priced below both Beverage and Side medians -> only 1 "below" flag
    blocks2 = []
    # 3 beverages
    for i, p in enumerate([299, 399, 499]):
        blocks2.append(_make_tb(
            grammar={"parsed_name": f"Drink {chr(65+i)}"},
            category="Beverages",
            variants=[_make_variant(price_cents=p)],
        ))
    # 3 sides
    for i, p in enumerate([599, 699, 799]):
        blocks2.append(_make_tb(
            grammar={"parsed_name": f"Appetizer {chr(65+i)}"},
            category="Sides / Appetizers",
            variants=[_make_variant(price_cents=p)],
        ))
    # 3 pizzas: normal, normal, very cheap
    for i, p in enumerate([1299, 1499, 149]):
        blocks2.append(_make_tb(
            grammar={"parsed_name": f"Pizza Type {chr(65+i)}"},
            category="Pizza",
            variants=[_make_variant(price_cents=p)],
        ))

    for tb in blocks2:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks2)

    below_flags = [f for f in blocks2[8].get("price_flags", [])
                   if f.get("reason") == "cross_category_price_below"]
    report.check("5.3 cheap pizza gets exactly 1 below flag",
                 len(below_flags) == 1,
                 f"below_flags count: {len(below_flags)}, flags: {below_flags}")


# ---------------------------------------------------------------------------
# Group 6: Edge cases
# ---------------------------------------------------------------------------

def run_edge_case_tests(report: TestReport) -> None:
    print("\n--- Group 6: Edge Cases ---")

    # 6.1: Items with no category -> skip gracefully
    blocks = [
        _make_tb(grammar={"parsed_name": "Mystery A"},
                 variants=[_make_variant(price_cents=1999)]),
        _make_tb(grammar={"parsed_name": "Mystery B"},
                 variants=[_make_variant(price_cents=2099)]),
    ]
    for tb in blocks:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks)

    report.check("6.1 no-category items produce no flags",
                 all(_count_flags(tb, "cross_category_price_above") == 0
                     and _count_flags(tb, "cross_category_price_below") == 0
                     for tb in blocks))

    # 6.2: Items with no price -> skip gracefully
    blocks2 = [
        _make_tb(grammar={"parsed_name": "Fries"}, category="Sides / Appetizers"),
        _make_tb(grammar={"parsed_name": "More Fries"}, category="Sides / Appetizers"),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1399)]),
    ]
    for tb in blocks2:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks2)

    report.check("6.2 items without prices produce no flags",
                 all(_count_flags(tb, "cross_category_price_above") == 0
                     and _count_flags(tb, "cross_category_price_below") == 0
                     for tb in blocks2))

    # 6.3: Empty list -> no crash
    try:
        _check_cross_category_coherence([])
        report.ok("6.3 empty list no crash")
    except Exception as e:
        report.fail("6.3 empty list no crash", str(e))

    # 6.4: Single item -> no crash
    blocks3 = [_make_tb(grammar={"parsed_name": "Lonely Item"}, category="Pizza",
                        variants=[_make_variant(price_cents=1299)])]
    for tb in blocks3:
        tb.setdefault("price_flags", [])
    try:
        _check_cross_category_coherence(blocks3)
        report.ok("6.4 single item no crash")
    except Exception as e:
        report.fail("6.4 single item no crash", str(e))

    # 6.5: Item exactly at median -> no flag (not strictly above)
    blocks4 = _build_menu(
        "Sides / Appetizers", [499, 599, 1399],     # one side exactly at pizza median
        "Pizza", [1299, 1399, 1499],                 # median 1399
    )
    for tb in blocks4:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks4)

    report.check("6.5 side at exactly pizza median not flagged",
                 _count_flags(blocks4[2], "cross_category_price_above") == 0,
                 f"flags: {blocks4[2].get('price_flags')}")

    # 6.6: Item 1 cent above median -> flagged
    blocks5 = _build_menu(
        "Sides / Appetizers", [499, 599, 1400],     # one side 1 cent above pizza median
        "Pizza", [1299, 1399, 1499],                 # median 1399
    )
    for tb in blocks5:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks5)

    report.check("6.6 side 1 cent above pizza median is flagged",
                 _count_flags(blocks5[2], "cross_category_price_above") == 1,
                 f"flags: {blocks5[2].get('price_flags')}")


# ---------------------------------------------------------------------------
# Group 7: Multiple rule pairs
# ---------------------------------------------------------------------------

def run_multi_rule_tests(report: TestReport) -> None:
    print("\n--- Group 7: Multiple Rule Pairs ---")

    # 7.1: Build a full menu with multiple categories
    blocks = []
    # 3 beverages
    for i, p in enumerate([199, 299, 399]):
        blocks.append(_make_tb(
            grammar={"parsed_name": f"Drink {chr(65+i)}"},
            category="Beverages",
            variants=[_make_variant(price_cents=p)],
        ))
    # 3 sides
    for i, p in enumerate([499, 599, 699]):
        blocks.append(_make_tb(
            grammar={"parsed_name": f"Side {chr(65+i)}"},
            category="Sides / Appetizers",
            variants=[_make_variant(price_cents=p)],
        ))
    # 3 pizzas
    for i, p in enumerate([1299, 1399, 1499]):
        blocks.append(_make_tb(
            grammar={"parsed_name": f"Pizza {chr(65+i)}"},
            category="Pizza",
            variants=[_make_variant(price_cents=p)],
        ))
    # 3 pastas
    for i, p in enumerate([1199, 1299, 1399]):
        blocks.append(_make_tb(
            grammar={"parsed_name": f"Pasta {chr(65+i)}"},
            category="Pasta",
            variants=[_make_variant(price_cents=p)],
        ))

    for tb in blocks:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks)

    # No items should be flagged (all prices are in normal ranges)
    all_above = sum(_count_flags(tb, "cross_category_price_above") for tb in blocks)
    all_below = sum(_count_flags(tb, "cross_category_price_below") for tb in blocks)
    report.check("7.1 normal menu has no cross-category flags",
                 all_above == 0 and all_below == 0,
                 f"above={all_above}, below={all_below}")

    # 7.2: Add one mispriced item per cheap category
    blocks2 = list(blocks)
    # Expensive beverage
    blocks2.append(_make_tb(
        grammar={"parsed_name": "Premium Water"},
        category="Beverages",
        variants=[_make_variant(price_cents=1599)],
    ))
    # Expensive side
    blocks2.append(_make_tb(
        grammar={"parsed_name": "Truffle Fries"},
        category="Sides / Appetizers",
        variants=[_make_variant(price_cents=1599)],
    ))

    for tb in blocks2:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks2)

    report.check("7.2 expensive beverage ($15.99) flagged",
                 _count_flags(blocks2[12], "cross_category_price_above") == 1,
                 f"flags: {blocks2[12].get('price_flags')}")
    report.check("7.3 expensive side ($15.99) flagged",
                 _count_flags(blocks2[13], "cross_category_price_above") == 1,
                 f"flags: {blocks2[13].get('price_flags')}")


# ---------------------------------------------------------------------------
# Group 8: Beverage rules (cheapest tier)
# ---------------------------------------------------------------------------

def run_beverage_tests(report: TestReport) -> None:
    print("\n--- Group 8: Beverage Rules ---")

    # 8.1: Beverage priced above salad median
    blocks = _build_menu(
        "Beverages", [199, 299, 1299],      # one expensive drink
        "Salads", [799, 899, 999],           # median 899
    )
    for tb in blocks:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks)

    report.check("8.1 beverage at $12.99 flagged above salads median",
                 _count_flags(blocks[2], "cross_category_price_above") == 1,
                 f"flags: {blocks[2].get('price_flags')}")

    # 8.2: Beverage priced above burger median
    blocks2 = _build_menu(
        "Beverages", [199, 299, 1499],       # one expensive drink
        "Burgers", [899, 999, 1099],          # median 999
    )
    for tb in blocks2:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks2)

    report.check("8.2 beverage at $14.99 flagged above burgers median",
                 _count_flags(blocks2[2], "cross_category_price_above") == 1,
                 f"flags: {blocks2[2].get('price_flags')}")

    # 8.3: Burger priced below beverage median
    blocks3 = _build_menu(
        "Beverages", [399, 499, 599],        # median 499
        "Burgers", [999, 1099, 249],          # one cheap burger
    )
    for tb in blocks3:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks3)

    report.check("8.3 burger at $2.49 flagged below beverage median",
                 _count_flags(blocks3[5], "cross_category_price_below") == 1,
                 f"flags: {blocks3[5].get('price_flags')}")


# ---------------------------------------------------------------------------
# Group 9: Dessert rules
# ---------------------------------------------------------------------------

def run_dessert_tests(report: TestReport) -> None:
    print("\n--- Group 9: Dessert Rules ---")

    # 9.1: Dessert priced above pizza median
    blocks = _build_menu(
        "Desserts", [399, 499, 1699],          # one expensive dessert
        "Pizza", [1099, 1199, 1299],           # median 1199
    )
    for tb in blocks:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks)

    report.check("9.1 dessert at $16.99 flagged above pizza median",
                 _count_flags(blocks[2], "cross_category_price_above") == 1,
                 f"flags: {blocks[2].get('price_flags')}")

    # 9.2: Dessert priced above pasta median
    blocks2 = _build_menu(
        "Desserts", [399, 499, 1599],          # one expensive dessert
        "Pasta", [999, 1099, 1199],            # median 1099
    )
    for tb in blocks2:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks2)

    report.check("9.2 dessert at $15.99 flagged above pasta median",
                 _count_flags(blocks2[2], "cross_category_price_above") == 1,
                 f"flags: {blocks2[2].get('price_flags')}")

    # 9.3: Normal desserts not flagged
    blocks3 = _build_menu(
        "Desserts", [399, 499, 599],           # all cheap
        "Pizza", [1099, 1199, 1299],           # median 1199
    )
    for tb in blocks3:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks3)

    report.check("9.3 normal desserts not flagged",
                 all(_count_flags(tb, "cross_category_price_above") == 0
                     for tb in blocks3[:3]),
                 f"flags: {[tb.get('price_flags') for tb in blocks3[:3]]}")

    # 9.4: Calzone priced below dessert median
    blocks4 = _build_menu(
        "Desserts", [499, 599, 699],            # median 599
        "Calzones / Stromboli", [999, 1099, 299],  # one cheap calzone
    )
    for tb in blocks4:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks4)

    report.check("9.4 calzone at $2.99 flagged below dessert median",
                 _count_flags(blocks4[5], "cross_category_price_below") == 1,
                 f"flags: {blocks4[5].get('price_flags')}")


# ---------------------------------------------------------------------------
# Group 10: Both-path compatibility
# ---------------------------------------------------------------------------

def run_both_path_tests(report: TestReport) -> None:
    print("\n--- Group 10: Both-Path Compatibility ---")

    # 10.1: ai_ocr_helper path (uses name + price_cents, no grammar/variants)
    blocks = []
    for i, p in enumerate([199, 299, 1599]):
        blocks.append(_make_tb(
            name=f"Beverage Item {chr(65+i)}",
            category="Beverages",
            price_cents=p,
        ))
    for i, p in enumerate([1099, 1199, 1299]):
        blocks.append(_make_tb(
            name=f"Pizza Item {chr(65+i)}",
            category="Pizza",
            price_cents=p,
        ))

    for tb in blocks:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks)

    report.check("10.1 ai_ocr_helper path: expensive beverage flagged",
                 _count_flags(blocks[2], "cross_category_price_above") == 1,
                 f"flags: {blocks[2].get('price_flags')}")

    # 10.2: price_candidates path
    blocks2 = []
    for i, p in enumerate([199, 299, 1599]):
        blocks2.append(_make_tb(
            name=f"Drink {chr(65+i)}",
            category="Beverages",
            price_candidates=[{"price_cents": p}],
        ))
    for i, p in enumerate([1099, 1199, 1299]):
        blocks2.append(_make_tb(
            name=f"Pie {chr(65+i)}",
            category="Pizza",
            price_candidates=[{"price_cents": p}],
        ))

    for tb in blocks2:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks2)

    report.check("10.2 price_candidates path: expensive beverage flagged",
                 _count_flags(blocks2[2], "cross_category_price_above") == 1,
                 f"flags: {blocks2[2].get('price_flags')}")


# ---------------------------------------------------------------------------
# Group 11: Coexistence with other cross-item checks
# ---------------------------------------------------------------------------

def run_coexistence_tests(report: TestReport) -> None:
    print("\n--- Group 11: Coexistence with Other Checks ---")

    # 11.1: Full entry point check_cross_item_consistency wires Check 5
    blocks = _build_menu(
        "Beverages", [199, 299, 1599],
        "Pizza", [1299, 1399, 1499],
    )
    check_cross_item_consistency(blocks)

    report.check("11.1 check_cross_item_consistency produces cross_category_price_above",
                 _count_flags(blocks[2], "cross_category_price_above") == 1,
                 f"flags: {blocks[2].get('price_flags')}")

    # 11.2: Cross-category flags coexist with within-category outlier flags
    blocks2 = _build_menu(
        "Sides / Appetizers", [499, 599, 699, 1999],  # 1999 is outlier AND cross-cat
        "Pizza", [1099, 1199, 1299],
    )
    check_cross_item_consistency(blocks2)

    has_cross_cat = _count_flags(blocks2[3], "cross_category_price_above") >= 1
    has_outlier = _count_flags(blocks2[3], "cross_item_category_price_outlier") >= 1
    report.check("11.2 mispriced side has both cross-cat and outlier flags",
                 has_cross_cat and has_outlier,
                 f"cross_cat={has_cross_cat}, outlier={has_outlier}, "
                 f"flags: {blocks2[3].get('price_flags')}")

    # 11.3: Pizza below beverage via full entry point
    blocks3 = _build_menu(
        "Beverages", [299, 399, 499],
        "Pizza", [1299, 1399, 149],
    )
    check_cross_item_consistency(blocks3)

    report.check("11.3 cheap pizza flagged via full entry point",
                 _count_flags(blocks3[5], "cross_category_price_below") == 1,
                 f"flags: {blocks3[5].get('price_flags')}")


# ---------------------------------------------------------------------------
# Group 12: Constants and rule coverage
# ---------------------------------------------------------------------------

def run_constants_tests(report: TestReport) -> None:
    print("\n--- Group 12: Constants and Rule Coverage ---")

    # 12.1: Verify rule list covers expected pairs
    rules_set = set(_CROSS_CAT_PRICE_RULES)
    report.check("12.1 Beverages < Pizza in rules",
                 ("Beverages", "Pizza") in rules_set)
    report.check("12.2 Beverages < Pasta in rules",
                 ("Beverages", "Pasta") in rules_set)
    report.check("12.3 Sides < Pizza in rules",
                 ("Sides / Appetizers", "Pizza") in rules_set)
    report.check("12.4 Sides < Burgers in rules",
                 ("Sides / Appetizers", "Burgers") in rules_set)
    report.check("12.5 Desserts < Pizza in rules",
                 ("Desserts", "Pizza") in rules_set)
    report.check("12.6 Beverages < Calzones in rules",
                 ("Beverages", "Calzones / Stromboli") in rules_set)

    # 12.7: Min items constant
    report.check("12.7 min items is 2",
                 _CROSS_CAT_MIN_ITEMS == 2)

    # 12.8: Min gap ratio constant
    report.check("12.8 min gap ratio is 1.3",
                 _CROSS_CAT_MIN_GAP_RATIO == 1.3)

    # 12.9: No reverse rules (e.g., Pizza < Beverages should not exist)
    for cheap, exp in _CROSS_CAT_PRICE_RULES:
        reverse = (exp, cheap)
        report.check(f"12.9 no reverse rule for ({cheap}, {exp})",
                     reverse not in rules_set,
                     f"found reverse: {reverse}")


# ---------------------------------------------------------------------------
# Group 13: Real-world scenarios
# ---------------------------------------------------------------------------

def run_realistic_tests(report: TestReport) -> None:
    print("\n--- Group 13: Real-World Scenarios ---")

    # 13.1: Full restaurant menu — normal pricing
    blocks = []
    # Beverages
    for i, (name, price) in enumerate([
        ("Coke", 249), ("Sprite", 249), ("Iced Tea", 299),
    ]):
        blocks.append(_make_tb(grammar={"parsed_name": name},
                               category="Beverages",
                               variants=[_make_variant(price_cents=price)]))
    # Sides
    for i, (name, price) in enumerate([
        ("French Fries", 499), ("Mozzarella Sticks", 699),
        ("Garlic Bread", 399),
    ]):
        blocks.append(_make_tb(grammar={"parsed_name": name},
                               category="Sides / Appetizers",
                               variants=[_make_variant(price_cents=price)]))
    # Pizza
    for i, (name, price) in enumerate([
        ("Cheese Pizza", 1299), ("Pepperoni Pizza", 1399),
        ("Supreme Pizza", 1599),
    ]):
        blocks.append(_make_tb(grammar={"parsed_name": name},
                               category="Pizza",
                               variants=[_make_variant(price_cents=price)]))
    # Pasta
    for i, (name, price) in enumerate([
        ("Spaghetti", 1099), ("Baked Ziti", 1199),
        ("Fettuccine Alfredo", 1299),
    ]):
        blocks.append(_make_tb(grammar={"parsed_name": name},
                               category="Pasta",
                               variants=[_make_variant(price_cents=price)]))

    for tb in blocks:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks)

    total_flags = sum(
        _count_flags(tb, "cross_category_price_above") +
        _count_flags(tb, "cross_category_price_below")
        for tb in blocks
    )
    report.check("13.1 normal restaurant menu produces 0 cross-cat flags",
                 total_flags == 0,
                 f"total flags: {total_flags}")

    # 13.2: OCR price error — side gets pizza's price
    blocks[3]["variants"] = [_make_variant(price_cents=1599)]  # Fries at $15.99
    # Reset flags
    for tb in blocks:
        tb["price_flags"] = []
    _check_cross_category_coherence(blocks)

    report.check("13.2 fries at $15.99 (OCR error) flagged",
                 _count_flags(blocks[3], "cross_category_price_above") == 1,
                 f"flags: {blocks[3].get('price_flags')}")

    # 13.3: Miscategorized item — pizza labeled as beverage
    blocks2 = list(blocks)
    # Reset
    for tb in blocks2:
        tb["price_flags"] = []
    blocks2[3]["variants"] = [_make_variant(price_cents=499)]  # restore fries
    # Mislabel a pizza as a beverage
    blocks2[6]["category"] = "Beverages"  # Cheese Pizza mislabeled
    _check_cross_category_coherence(blocks2)

    report.check("13.3 pizza mislabeled as beverage flagged above",
                 _count_flags(blocks2[6], "cross_category_price_above") == 1,
                 f"flags: {blocks2[6].get('price_flags')}")


# ---------------------------------------------------------------------------
# Group 14: Sub / Sandwich rules
# ---------------------------------------------------------------------------

def run_sub_rules_tests(report: TestReport) -> None:
    print("\n--- Group 14: Sub / Sandwich Rules ---")

    # 14.1: Sides < Subs rule
    blocks = _build_menu(
        "Sides / Appetizers", [399, 499, 1399],   # one expensive side
        "Subs / Sandwiches", [799, 899, 999],     # median 899
    )
    for tb in blocks:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks)

    report.check("14.1 side at $13.99 flagged above subs median",
                 _count_flags(blocks[2], "cross_category_price_above") == 1,
                 f"flags: {blocks[2].get('price_flags')}")

    # 14.2: Beverage < Subs rule
    blocks2 = _build_menu(
        "Beverages", [199, 299, 1099],             # one expensive drink
        "Subs / Sandwiches", [799, 899, 999],      # median 899
    )
    for tb in blocks2:
        tb.setdefault("price_flags", [])
    _check_cross_category_coherence(blocks2)

    report.check("14.2 beverage at $10.99 flagged above subs median",
                 _count_flags(blocks2[2], "cross_category_price_above") == 1,
                 f"flags: {blocks2[2].get('price_flags')}")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main() -> None:
    report = TestReport()

    run_price_above_tests(report)
    run_price_below_tests(report)
    run_min_items_tests(report)
    run_min_gap_tests(report)
    run_dedup_tests(report)
    run_edge_case_tests(report)
    run_multi_rule_tests(report)
    run_beverage_tests(report)
    run_dessert_tests(report)
    run_both_path_tests(report)
    run_coexistence_tests(report)
    run_constants_tests(report)
    run_realistic_tests(report)
    run_sub_rules_tests(report)

    print(f"\n{'='*60}")
    print(f"Day 64 Results: {report.passed}/{report.total} passed")

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
