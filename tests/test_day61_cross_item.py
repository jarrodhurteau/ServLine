# tests/test_day61_cross_item.py
"""
Day 61: Sprint 8.3 -- Cross-Item Consistency (Foundation)

Tests:
  1. Duplicate name detection (exact duplicates, different prices)
  2. Category price outlier detection (IQR-based)
  3. Category isolation detection (lone outlier categories)
  4. Name extraction and normalisation helpers
  5. Primary price extraction helper
  6. Both-path compatibility (pipeline + ai_ocr_helper)
  7. Integration with existing pipeline steps
  8. Edge cases and regressions

Run: python tests/test_day61_cross_item.py
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


# ==================================================================
# Group 1: Duplicate Name Detection
# ==================================================================

def run_duplicate_name_tests(report: TestReport) -> None:
    print("\n--- Group 1: Duplicate Name Detection ---")

    # 1.1: Two items same name, different prices -> warn
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.1 diff-price dup flagged",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1
                 and _count_flags(blocks[1], "cross_item_duplicate_name") == 1,
                 f"flags: {[b.get('price_flags') for b in blocks]}")

    # 1.2: Two items same name, same prices -> info
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.2 same-price dup flagged as exact",
                 _count_flags(blocks[0], "cross_item_exact_duplicate") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.3: Three items same name, mixed prices -> warn
    blocks = [
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.3 three mixed-price dups -> warn",
                 all(_count_flags(b, "cross_item_duplicate_name") == 1 for b in blocks),
                 f"flags: {[_count_flags(b, 'cross_item_duplicate_name') for b in blocks]}")

    # 1.4: Unique names -> no flags
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.4 unique names no flags",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 0
                 and _count_flags(blocks[1], "cross_item_duplicate_name") == 0,
                 f"flags: {[b.get('price_flags') for b in blocks]}")

    # 1.5: "The Pepperoni Pizza" and "Pepperoni Pizza" match
    blocks = [
        _make_tb(grammar={"parsed_name": "The Pepperoni Pizza"}, variants=[_make_variant(price_cents=1099)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.5 'The' prefix normalised",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.6: "OUR BURGER" and "Our Burger" match (case + prefix)
    blocks = [
        _make_tb(grammar={"parsed_name": "OUR BURGER"}, variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Our Burger"}, variants=[_make_variant(price_cents=1499)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.6 'Our'+case normalised",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.7: Trailing punctuation stripped
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza:"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.7 trailing colon stripped",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.8: Short names (<3 chars) skipped
    blocks = [
        _make_tb(grammar={"parsed_name": "M"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "M"}, variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.8 short names skipped",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 0
                 and _count_flags(blocks[0], "cross_item_exact_duplicate") == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.9: Items without names skipped
    blocks = [
        _make_tb(text="", grammar={"parsed_name": ""}),
        _make_tb(text="", grammar={"parsed_name": ""}),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.9 empty names skipped",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.10: grammar.parsed_name preferred over merged_text
    blocks = [
        _make_tb(text="Garbled Text 9.99", grammar={"parsed_name": "Cheese Pizza"},
                 variants=[_make_variant(price_cents=999)]),
        _make_tb(text="Different Garble 12.99", grammar={"parsed_name": "Cheese Pizza"},
                 variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.10 grammar.parsed_name used",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.11: 'name' field (ai_ocr_helper path) works
    blocks = [
        _make_tb(name="Cheese Pizza", variants=[_make_variant(price_cents=999)]),
        _make_tb(name="Cheese Pizza", variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.11 name field works",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.12: Zero-price items in dup group -> detected as warn (prices differ: 499 vs 0)
    blocks = [
        _make_tb(grammar={"parsed_name": "Garlic Bread"}, variants=[_make_variant(price_cents=499)]),
        _make_tb(grammar={"parsed_name": "Garlic Bread"}),  # no price -> price_cents=0
    ]
    check_cross_item_consistency(blocks)
    report.check("1.12 zero-price dup detected as warn",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.13: Flag details contain other_indices
    blocks = [
        _make_tb(grammar={"parsed_name": "Test Item"}, variants=[_make_variant(price_cents=500)]),
        _make_tb(grammar={"parsed_name": "Test Item"}, variants=[_make_variant(price_cents=700)]),
    ]
    check_cross_item_consistency(blocks)
    flag = _get_flag(blocks[0], "cross_item_duplicate_name")
    report.check("1.13 other_indices in details",
                 flag is not None and flag["details"]["other_indices"] == [1],
                 f"flag: {flag}")

    # 1.14: Flag details contain other_prices_cents
    report.check("1.14 other_prices in details",
                 flag is not None and flag["details"]["other_prices_cents"] == [700],
                 f"flag: {flag}")

    # 1.15: Mixed grammar/no-grammar items still group
    blocks = [
        _make_tb(grammar={"parsed_name": "Chicken Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(name="Chicken Wings", variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.15 mixed paths group",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.16: Whitespace variations normalised
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese  Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.16 whitespace normalised",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.17: "Fresh Garden Salad" matches "Garden Salad"
    blocks = [
        _make_tb(grammar={"parsed_name": "Fresh Garden Salad"}, variants=[_make_variant(price_cents=799)]),
        _make_tb(grammar={"parsed_name": "Garden Salad"}, variants=[_make_variant(price_cents=899)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.17 'Fresh' prefix stripped",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 1.18: Fallback to merged_text with price stripping
    blocks = [
        _make_tb(text="Cheese Pizza 9.99", variants=[_make_variant(price_cents=999)]),
        _make_tb(text="Cheese Pizza 12.99", variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("1.18 merged_text fallback with price strip",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")


# ==================================================================
# Group 2: Category Price Outlier Detection
# ==================================================================

def run_category_price_outlier_tests(report: TestReport) -> None:
    print("\n--- Group 2: Category Price Outlier Detection ---")

    # 2.1: Pizza group with one cheap outlier -> flagged
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1399)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1499)]),
        _make_tb(grammar={"parsed_name": "Meat Lovers"}, category="Pizza", variants=[_make_variant(price_cents=1599)]),
        _make_tb(grammar={"parsed_name": "Margherita"}, category="Pizza", variants=[_make_variant(price_cents=200)]),  # outlier
    ]
    check_cross_item_consistency(blocks)
    report.check("2.1 cheap outlier flagged",
                 _count_flags(blocks[4], "cross_item_category_price_outlier") == 1,
                 f"flags: {blocks[4].get('price_flags')}")
    report.check("2.1 normal items not flagged",
                 all(_count_flags(b, "cross_item_category_price_outlier") == 0 for b in blocks[:4]),
                 f"flags: {[_count_flags(b, 'cross_item_category_price_outlier') for b in blocks[:4]]}")

    # 2.2: All similar prices -> no flags
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1399)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1499)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("2.2 similar prices no flags",
                 all(_count_flags(b, "cross_item_category_price_outlier") == 0 for b in blocks),
                 f"flags: {[b.get('price_flags') for b in blocks]}")

    # 2.3: Beverages group with expensive outlier
    blocks = [
        _make_tb(grammar={"parsed_name": "Coke"}, category="Beverages", variants=[_make_variant(price_cents=199)]),
        _make_tb(grammar={"parsed_name": "Sprite"}, category="Beverages", variants=[_make_variant(price_cents=249)]),
        _make_tb(grammar={"parsed_name": "Water"}, category="Beverages", variants=[_make_variant(price_cents=299)]),
        _make_tb(grammar={"parsed_name": "Fancy Juice"}, category="Beverages", variants=[_make_variant(price_cents=2500)]),  # outlier
    ]
    check_cross_item_consistency(blocks)
    report.check("2.3 expensive outlier flagged",
                 _count_flags(blocks[3], "cross_item_category_price_outlier") == 1,
                 f"flags: {blocks[3].get('price_flags')}")

    # 2.4: Category with <3 items -> no outlier check
    blocks = [
        _make_tb(grammar={"parsed_name": "Caesar Salad"}, category="Salads", variants=[_make_variant(price_cents=799)]),
        _make_tb(grammar={"parsed_name": "Garden Salad"}, category="Salads", variants=[_make_variant(price_cents=5000)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("2.4 <3 items no check",
                 all(_count_flags(b, "cross_item_category_price_outlier") == 0 for b in blocks),
                 f"flags: {[b.get('price_flags') for b in blocks]}")

    # 2.5: Exactly 3 items -> check runs
    blocks = [
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, category="Wings", variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "BBQ Wings"}, category="Wings", variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Garlic Wings"}, category="Wings", variants=[_make_variant(price_cents=5000)]),  # outlier
    ]
    check_cross_item_consistency(blocks)
    report.check("2.5 exactly 3 items, outlier flagged",
                 _count_flags(blocks[2], "cross_item_category_price_outlier") == 1,
                 f"flags: {blocks[2].get('price_flags')}")

    # 2.6: $0 items skipped in grouping
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1399)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1499)]),
        _make_tb(grammar={"parsed_name": "Mystery Pizza"}, category="Pizza"),  # no price
    ]
    check_cross_item_consistency(blocks)
    report.check("2.6 $0 items skipped",
                 _count_flags(blocks[3], "cross_item_category_price_outlier") == 0,
                 f"flags: {blocks[3].get('price_flags')}")

    # 2.7: None category skipped
    blocks = [
        _make_tb(variants=[_make_variant(price_cents=100)]),
        _make_tb(variants=[_make_variant(price_cents=200)]),
        _make_tb(variants=[_make_variant(price_cents=5000)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("2.7 None category skipped",
                 all(_count_flags(b, "cross_item_category_price_outlier") == 0 for b in blocks),
                 f"flags: {[b.get('price_flags') for b in blocks]}")

    # 2.8: Flag details contain correct fields
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1399)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1499)]),
        _make_tb(grammar={"parsed_name": "Margherita"}, category="Pizza", variants=[_make_variant(price_cents=200)]),
    ]
    check_cross_item_consistency(blocks)
    flag = _get_flag(blocks[3], "cross_item_category_price_outlier")
    report.check("2.8 flag has details",
                 flag is not None
                 and "category" in flag["details"]
                 and "item_price_cents" in flag["details"]
                 and "category_median_cents" in flag["details"]
                 and "category_mad_cents" in flag["details"],
                 f"flag: {flag}")

    # 2.9: "below" direction for cheap item
    report.check("2.9 direction is below",
                 flag is not None and flag["details"]["direction"] == "below",
                 f"direction: {flag['details'].get('direction') if flag else None}")

    # 2.10: "above" direction for expensive item
    blocks = [
        _make_tb(grammar={"parsed_name": "Fries"}, category="Sides", variants=[_make_variant(price_cents=399)]),
        _make_tb(grammar={"parsed_name": "Onion Rings"}, category="Sides", variants=[_make_variant(price_cents=449)]),
        _make_tb(grammar={"parsed_name": "Mozzarella Sticks"}, category="Sides", variants=[_make_variant(price_cents=499)]),
        _make_tb(grammar={"parsed_name": "Lobster Tail"}, category="Sides", variants=[_make_variant(price_cents=3500)]),
    ]
    check_cross_item_consistency(blocks)
    flag = _get_flag(blocks[3], "cross_item_category_price_outlier")
    report.check("2.10 direction is above",
                 flag is not None and flag["details"]["direction"] == "above",
                 f"flag: {flag}")

    # 2.11: Different categories analysed independently
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1399)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1499)]),
        _make_tb(grammar={"parsed_name": "Coke"}, category="Beverages", variants=[_make_variant(price_cents=199)]),
        _make_tb(grammar={"parsed_name": "Sprite"}, category="Beverages", variants=[_make_variant(price_cents=249)]),
        _make_tb(grammar={"parsed_name": "Water"}, category="Beverages", variants=[_make_variant(price_cents=299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("2.11 categories independent",
                 all(_count_flags(b, "cross_item_category_price_outlier") == 0 for b in blocks),
                 f"flags: {[_count_flags(b, 'cross_item_category_price_outlier') for b in blocks]}")

    # 2.12: Legitimate wide range within IQR -> no flag
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza", variants=[_make_variant(price_cents=799)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Meat Lovers"}, category="Pizza", variants=[_make_variant(price_cents=1599)]),
        _make_tb(grammar={"parsed_name": "Supreme"}, category="Pizza", variants=[_make_variant(price_cents=1899)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("2.12 wide but legitimate range",
                 all(_count_flags(b, "cross_item_category_price_outlier") == 0 for b in blocks),
                 f"flags: {[_count_flags(b, 'cross_item_category_price_outlier') for b in blocks]}")

    # 2.13: Tight cluster with single outlier -> only outlier flagged
    blocks = [
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, category="Wings", variants=[_make_variant(price_cents=1000)]),
        _make_tb(grammar={"parsed_name": "BBQ Wings"}, category="Wings", variants=[_make_variant(price_cents=1000)]),
        _make_tb(grammar={"parsed_name": "Garlic Wings"}, category="Wings", variants=[_make_variant(price_cents=1000)]),
        _make_tb(grammar={"parsed_name": "Honey Wings"}, category="Wings", variants=[_make_variant(price_cents=1000)]),
        _make_tb(grammar={"parsed_name": "Gold Wings"}, category="Wings", variants=[_make_variant(price_cents=5000)]),  # outlier
    ]
    check_cross_item_consistency(blocks)
    report.check("2.13 tight cluster outlier",
                 _count_flags(blocks[4], "cross_item_category_price_outlier") == 1
                 and all(_count_flags(b, "cross_item_category_price_outlier") == 0 for b in blocks[:4]),
                 f"flags: {[_count_flags(b, 'cross_item_category_price_outlier') for b in blocks]}")

    # 2.14: Multiple outliers flagged
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1300)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1300)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1300)]),
        _make_tb(grammar={"parsed_name": "Margherita"}, category="Pizza", variants=[_make_variant(price_cents=100)]),   # outlier low
        _make_tb(grammar={"parsed_name": "Truffle Pizza"}, category="Pizza", variants=[_make_variant(price_cents=9999)]),  # outlier high
    ]
    check_cross_item_consistency(blocks)
    report.check("2.14 multiple outliers",
                 _count_flags(blocks[3], "cross_item_category_price_outlier") == 1
                 and _count_flags(blocks[4], "cross_item_category_price_outlier") == 1,
                 f"flags: idx3={blocks[3].get('price_flags')}, idx4={blocks[4].get('price_flags')}")

    # 2.15: All identical prices (IQR=1 min) -> tight threshold, no outlier
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1000)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1000)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1000)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("2.15 identical prices no outlier",
                 all(_count_flags(b, "cross_item_category_price_outlier") == 0 for b in blocks),
                 f"flags: {[b.get('price_flags') for b in blocks]}")

    # 2.16: Variant-based price extraction
    blocks = [
        _make_tb(category="Pizza", variants=[
            _make_variant(label="S", price_cents=899),
            _make_variant(label="L", price_cents=1499),
        ]),
        _make_tb(category="Pizza", variants=[
            _make_variant(label="S", price_cents=999),
            _make_variant(label="L", price_cents=1599),
        ]),
        _make_tb(category="Pizza", variants=[
            _make_variant(label="S", price_cents=1099),
            _make_variant(label="L", price_cents=1699),
        ]),
    ]
    check_cross_item_consistency(blocks)
    # All use lowest variant price: 899, 999, 1099 -> no outlier
    report.check("2.16 variant min price used",
                 all(_count_flags(b, "cross_item_category_price_outlier") == 0 for b in blocks),
                 f"flags: {[b.get('price_flags') for b in blocks]}")

    # 2.17: price_candidates-based extraction
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza", price_candidates=[{"price_cents": 1299}]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza", price_candidates=[{"price_cents": 1399}]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza", price_candidates=[{"price_cents": 1499}]),
        _make_tb(grammar={"parsed_name": "Margherita"}, category="Pizza", price_candidates=[{"price_cents": 200}]),  # outlier
    ]
    check_cross_item_consistency(blocks)
    report.check("2.17 price_candidates extraction",
                 _count_flags(blocks[3], "cross_item_category_price_outlier") == 1,
                 f"flags: {blocks[3].get('price_flags')}")

    # 2.18: Large category (10 items) realistic distribution
    prices = [1099, 1199, 1299, 1299, 1399, 1399, 1499, 1599, 1699, 1799]
    names = ["Cheese", "Pepperoni", "Veggie", "Hawaiian", "Meat Lovers",
             "Supreme", "BBQ Chicken", "Buffalo", "Margherita", "White Pizza"]
    blocks = [
        _make_tb(grammar={"parsed_name": n}, category="Pizza",
                 variants=[_make_variant(price_cents=p)])
        for n, p in zip(names, prices)
    ]
    check_cross_item_consistency(blocks)
    report.check("2.18 large category no false positives",
                 all(_count_flags(b, "cross_item_category_price_outlier") == 0 for b in blocks),
                 f"flagged: {[i for i, b in enumerate(blocks) if _count_flags(b, 'cross_item_category_price_outlier')]}")


# ==================================================================
# Group 3: Category Isolation Detection
# ==================================================================

def run_category_isolation_tests(report: TestReport) -> None:
    print("\n--- Group 3: Category Isolation Detection ---")

    # 3.1: Single "Salad" surrounded by 4 "Pizza" -> isolated
    blocks = [
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Salads"),   # isolated
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.1 isolated item flagged",
                 _count_flags(blocks[2], "cross_item_category_isolated") == 1,
                 f"flags: {blocks[2].get('price_flags')}")
    report.check("3.1 non-isolated not flagged",
                 all(_count_flags(b, "cross_item_category_isolated") == 0 for b in [blocks[0], blocks[1], blocks[3], blocks[4]]),
                 f"flags: {[_count_flags(b, 'cross_item_category_isolated') for b in blocks]}")

    # 3.2: Item at beginning with fewer back-neighbours
    blocks = [
        _make_tb(category="Salads"),   # idx 0: neighbours = [1, 2] = Pizza, Pizza -> isolated
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.2 beginning isolation",
                 _count_flags(blocks[0], "cross_item_category_isolated") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 3.3: Item at end with fewer forward-neighbours
    blocks = [
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Salads"),   # idx 3: neighbours = [1, 2] = Pizza, Pizza -> isolated
    ]
    check_cross_item_consistency(blocks)
    report.check("3.3 end isolation",
                 _count_flags(blocks[3], "cross_item_category_isolated") == 1,
                 f"flags: {blocks[3].get('price_flags')}")

    # 3.4: Only 1 categorised neighbour -> no flag (insufficient evidence)
    blocks = [
        _make_tb(category="Salads"),   # idx 0: only neighbour idx 1 = Pizza (1 cat neighbour)
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.4 insufficient neighbours",
                 _count_flags(blocks[0], "cross_item_category_isolated") == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 3.5: Item matching at least 1 neighbour -> no flag
    blocks = [
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),    # matches left
        _make_tb(category="Wings"),
        _make_tb(category="Wings"),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.5 matching neighbour no flag",
                 _count_flags(blocks[1], "cross_item_category_isolated") == 0,
                 f"flags: {blocks[1].get('price_flags')}")

    # 3.6: Item matching all neighbours -> no flag
    blocks = [
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.6 all matching no flags",
                 all(_count_flags(b, "cross_item_category_isolated") == 0 for b in blocks),
                 f"flags: {[_count_flags(b, 'cross_item_category_isolated') for b in blocks]}")

    # 3.7: Items without category not counted as neighbours
    blocks = [
        _make_tb(category="Pizza"),
        _make_tb(),                    # no category
        _make_tb(category="Salads"),   # neighbours: idx 0 = Pizza, idx 3 = Pizza -> isolated
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.7 uncategorised neighbours skipped",
                 _count_flags(blocks[2], "cross_item_category_isolated") == 1,
                 f"flags: {blocks[2].get('price_flags')}")

    # 3.8: Flag details contain dominant_neighbor_category
    flag = _get_flag(blocks[2], "cross_item_category_isolated")
    report.check("3.8 dominant neighbour in details",
                 flag is not None and flag["details"]["dominant_neighbor_category"] == "Pizza",
                 f"flag: {flag}")

    # 3.9: Flag details contain position_index
    report.check("3.9 position_index in details",
                 flag is not None and flag["details"]["position_index"] == 2,
                 f"flag: {flag}")

    # 3.10: All-different neighbours still isolated
    blocks = [
        _make_tb(category="Pizza"),
        _make_tb(category="Wings"),
        _make_tb(category="Salads"),   # idx 2: neighbours = Pizza, Wings, Beverages, Sides -> all different
        _make_tb(category="Beverages"),
        _make_tb(category="Sides"),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.10 all-different neighbours still isolated",
                 _count_flags(blocks[2], "cross_item_category_isolated") == 1,
                 f"flags: {blocks[2].get('price_flags')}")

    # 3.11: Two isolated items both flagged
    blocks = [
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Salads"),   # isolated
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Wings"),    # isolated
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.11 two isolated items flagged",
                 _count_flags(blocks[2], "cross_item_category_isolated") == 1
                 and _count_flags(blocks[5], "cross_item_category_isolated") == 1,
                 f"flags: idx2={_count_flags(blocks[2], 'cross_item_category_isolated')}, "
                 f"idx5={_count_flags(blocks[5], 'cross_item_category_isolated')}")

    # 3.12: Section boundary (Pizza->Wings transition) -> no spurious flags
    blocks = [
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Wings"),
        _make_tb(category="Wings"),
        _make_tb(category="Wings"),
    ]
    check_cross_item_consistency(blocks)
    # boundary items (idx 2,3) have mixed neighbours, but at least one matches
    report.check("3.12 section boundary no spurious flags",
                 all(_count_flags(b, "cross_item_category_isolated") == 0 for b in blocks),
                 f"flags: {[_count_flags(b, 'cross_item_category_isolated') for b in blocks]}")

    # 3.13: Single-item menu -> no flags
    blocks = [_make_tb(category="Pizza")]
    check_cross_item_consistency(blocks)
    report.check("3.13 single item no flags",
                 _count_flags(blocks[0], "cross_item_category_isolated") == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 3.14: Three-item menu [A, B, A] -> B isolated
    blocks = [
        _make_tb(category="Pizza"),
        _make_tb(category="Salads"),   # neighbours: Pizza, Pizza -> isolated
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.14 three-item [A,B,A] -> B isolated",
                 _count_flags(blocks[1], "cross_item_category_isolated") == 1,
                 f"flags: {blocks[1].get('price_flags')}")

    # 3.15: Item with no category not flagged
    blocks = [
        _make_tb(category="Pizza"),
        _make_tb(),                    # no category -> not flagged
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    report.check("3.15 no category not flagged",
                 _count_flags(blocks[1], "cross_item_category_isolated") == 0,
                 f"flags: {blocks[1].get('price_flags')}")


# ==================================================================
# Group 4: Name Extraction and Normalisation Helpers
# ==================================================================

def run_name_extraction_tests(report: TestReport) -> None:
    print("\n--- Group 4: Name Extraction Helpers ---")

    # 4.1: From grammar.parsed_name
    tb = {"grammar": {"parsed_name": "Cheese Pizza"}, "merged_text": "garble"}
    report.check("4.1 grammar.parsed_name", _extract_item_name(tb) == "Cheese Pizza")

    # 4.2: From name field
    tb = {"name": "Pepperoni Pizza", "merged_text": "garble"}
    report.check("4.2 name field", _extract_item_name(tb) == "Pepperoni Pizza")

    # 4.3: Fallback to merged_text with price stripped
    tb = {"merged_text": "Cheese Pizza 12.99"}
    report.check("4.3 merged_text fallback",
                 _extract_item_name(tb) == "Cheese Pizza",
                 f"got: '{_extract_item_name(tb)}'")

    # 4.4: Empty grammar -> fallback
    tb = {"grammar": {}, "merged_text": "Cheese Pizza"}
    report.check("4.4 empty grammar fallback",
                 _extract_item_name(tb) == "Cheese Pizza",
                 f"got: '{_extract_item_name(tb)}'")

    # 4.5: _normalize_name lowercase
    report.check("4.5 lowercase", _normalize_name("CHEESE PIZZA") == "cheese pizza")

    # 4.6: Strips common prefixes
    report.check("4.6a strip The", _normalize_name("The Garden Salad") == "garden salad")
    report.check("4.6b strip Our", _normalize_name("Our Famous Wings") == "famous wings")
    report.check("4.6c strip Homemade", _normalize_name("Homemade Meatballs") == "meatballs")
    report.check("4.6d strip Fresh", _normalize_name("Fresh Garden Salad") == "garden salad")
    report.check("4.6e strip Classic", _normalize_name("Classic Caesar Salad") == "caesar salad")

    # 4.7: Collapses whitespace
    report.check("4.7 whitespace collapse",
                 _normalize_name("Cheese   Pizza") == "cheese pizza")

    # 4.8: Strips trailing punctuation
    report.check("4.8a trailing colon", _normalize_name("Pizza:") == "pizza")
    report.check("4.8b trailing dot", _normalize_name("Pizza.") == "pizza")
    report.check("4.8c trailing dash", _normalize_name("Pizza -") == "pizza")

    # 4.9: Combined transformations
    report.check("4.9 combined",
                 _normalize_name("  The  CHEESE  Pizza:  ") == "cheese pizza")

    # 4.10: Empty string
    report.check("4.10 empty string", _normalize_name("") == "")

    # 4.11: No text at all
    tb = {}
    report.check("4.11 no text", _extract_item_name(tb) == "")

    # 4.12: Price stripping: "$12.99 Cheese Pizza"
    tb = {"merged_text": "$12.99 Cheese Pizza"}
    report.check("4.12 price prefix stripped",
                 _extract_item_name(tb) == "Cheese Pizza",
                 f"got: '{_extract_item_name(tb)}'")


# ==================================================================
# Group 5: Primary Price Extraction Helper
# ==================================================================

def run_price_extraction_tests(report: TestReport) -> None:
    print("\n--- Group 5: Price Extraction Helper ---")

    # 5.1: From variants (takes minimum)
    tb = {"variants": [
        {"price_cents": 1299, "label": "L"},
        {"price_cents": 899, "label": "S"},
    ]}
    report.check("5.1 variant min price", _extract_primary_price_cents(tb) == 899)

    # 5.2: Mixed valid/invalid variant prices
    tb = {"variants": [
        {"price_cents": 0, "label": "S"},
        {"price_cents": 1099, "label": "M"},
    ]}
    report.check("5.2 skip zero variant", _extract_primary_price_cents(tb) == 1099)

    # 5.3: From price_candidates with price_cents
    tb = {"price_candidates": [{"price_cents": 1299}]}
    report.check("5.3 price_candidates cents", _extract_primary_price_cents(tb) == 1299)

    # 5.4: From price_candidates with value (ai_ocr_helper dollars)
    tb = {"price_candidates": [{"value": 12.99}]}
    report.check("5.4 price_candidates value", _extract_primary_price_cents(tb) == 1299)

    # 5.5: From direct price_cents
    tb = {"price_cents": 1099}
    report.check("5.5 direct price_cents", _extract_primary_price_cents(tb) == 1099)

    # 5.6: Priority: variants over price_candidates
    tb = {
        "variants": [{"price_cents": 899}],
        "price_candidates": [{"price_cents": 1299}],
    }
    report.check("5.6 variants priority", _extract_primary_price_cents(tb) == 899)

    # 5.7: No price data -> 0
    tb = {}
    report.check("5.7 no price data", _extract_primary_price_cents(tb) == 0)

    # 5.8: Zero-only prices -> 0
    tb = {"variants": [{"price_cents": 0}]}
    report.check("5.8 zero-only", _extract_primary_price_cents(tb) == 0)

    # 5.9: Float prices converted
    tb = {"variants": [{"price_cents": 12.99}]}
    report.check("5.9 float to int", _extract_primary_price_cents(tb) == 12)

    # 5.10: None values handled
    tb = {"variants": [{"price_cents": None}]}
    report.check("5.10 None handled", _extract_primary_price_cents(tb) == 0)

    # 5.11: Multiple variants, picks smallest positive
    tb = {"variants": [
        {"price_cents": 1599},
        {"price_cents": 1299},
        {"price_cents": 899},
        {"price_cents": 1099},
    ]}
    report.check("5.11 picks smallest", _extract_primary_price_cents(tb) == 899)

    # 5.12: Empty variants -> falls through to price_candidates
    tb = {
        "variants": [],
        "price_candidates": [{"price_cents": 1399}],
    }
    report.check("5.12 empty variants fallthrough", _extract_primary_price_cents(tb) == 1399)


# ==================================================================
# Group 6: Both-Path Compatibility
# ==================================================================

def run_both_path_tests(report: TestReport) -> None:
    print("\n--- Group 6: Both-Path Compatibility ---")

    # 6.1: Pipeline text_blocks with grammar + variants
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.1 pipeline path works",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 6.2: ai_ocr_helper items with name + price_candidates
    blocks = [
        _make_tb(name="Cheese Pizza", category="Pizza",
                 price_candidates=[{"value": 9.99}]),
        _make_tb(name="Cheese Pizza", category="Pizza",
                 price_candidates=[{"value": 12.99}]),
        _make_tb(name="Pepperoni Pizza", category="Pizza",
                 price_candidates=[{"value": 10.99}]),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.2 ai_ocr_helper path works",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 6.3: Mixed paths still work
    blocks = [
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(name="Wings", price_candidates=[{"value": 10.99}]),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.3 mixed paths group",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 6.4: Items with no grammar AND no name -> skipped for name checks
    blocks = [
        _make_tb(text="ab"),  # too short after strip
        _make_tb(text="ab"),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.4 no name gracefully skipped",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 6.5: Items with no price data -> skipped for price checks
    blocks = [
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.5 no price skipped for outlier check",
                 all(_count_flags(b, "cross_item_category_price_outlier") == 0 for b in blocks),
                 f"flags: {[b.get('price_flags') for b in blocks]}")

    # 6.6: Existing price_flags preserved
    existing_flag = {"severity": "warn", "reason": "variant_price_inversion", "details": {}}
    blocks = [
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=899)],
                 price_flags=[existing_flag]),
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("6.6 existing flags preserved",
                 any(f["reason"] == "variant_price_inversion" for f in blocks[0].get("price_flags", []))
                 and _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 6.7: Items without pre-existing price_flags get list created
    blocks = [
        _make_tb(grammar={"parsed_name": "Test"}, category="Pizza"),
        _make_tb(grammar={"parsed_name": "Test2"}, category="Pizza"),
    ]
    # No price_flags key initially
    assert "price_flags" not in blocks[0]
    check_cross_item_consistency(blocks)
    report.check("6.7 price_flags list created",
                 isinstance(blocks[0].get("price_flags"), list),
                 f"type: {type(blocks[0].get('price_flags'))}")

    # 6.8: Empty text_blocks list -> no crash
    blocks: List[Dict[str, Any]] = []
    check_cross_item_consistency(blocks)
    report.check("6.8 empty list no crash", True)


# ==================================================================
# Group 7: Integration with Full Pipeline
# ==================================================================

def run_integration_tests(report: TestReport) -> None:
    print("\n--- Group 7: Integration ---")

    # 7.1: Cross-item flags co-exist with within-item flags
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=999)],
                 price_flags=[{"severity": "warn", "reason": "duplicate_variant", "details": {}}]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    reasons = {f["reason"] for f in blocks[0].get("price_flags", [])}
    report.check("7.1 cross-item + within-item coexist",
                 "duplicate_variant" in reasons and "cross_item_duplicate_name" in reasons,
                 f"reasons: {reasons}")

    # 7.2: Realistic 10-item menu across 3 categories
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1099)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1199)]),
        _make_tb(grammar={"parsed_name": "Meat Lovers"}, category="Pizza",
                 variants=[_make_variant(price_cents=1399)]),
        _make_tb(grammar={"parsed_name": "Hawaiian"}, category="Pizza",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Buffalo Wings"}, category="Wings",
                 variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "BBQ Wings"}, category="Wings",
                 variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Garlic Wings"}, category="Wings",
                 variants=[_make_variant(price_cents=1099)]),
        _make_tb(grammar={"parsed_name": "Coke"}, category="Beverages",
                 variants=[_make_variant(price_cents=199)]),
        _make_tb(grammar={"parsed_name": "Sprite"}, category="Beverages",
                 variants=[_make_variant(price_cents=199)]),
        _make_tb(grammar={"parsed_name": "Water"}, category="Beverages",
                 variants=[_make_variant(price_cents=149)]),
    ]
    check_cross_item_consistency(blocks)
    total_flags = sum(len(b.get("price_flags", [])) for b in blocks)
    report.check("7.2 clean menu no flags",
                 total_flags == 0,
                 f"total flags: {total_flags}")

    # 7.3: All cross-item flags have cross_item_ prefix
    blocks = [
        _make_tb(grammar={"parsed_name": "Wings"}, category="Pizza",
                 variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Wings"}, category="Pizza",
                 variants=[_make_variant(price_cents=1099)]),
        _make_tb(grammar={"parsed_name": "Other"}, category="Pizza",
                 variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    all_reasons = [f["reason"] for b in blocks for f in b.get("price_flags", [])]
    report.check("7.3 all flags have cross_item_ prefix",
                 all(r.startswith("cross_item_") for r in all_reasons),
                 f"reasons: {all_reasons}")

    # 7.4: Dup name + category outlier on same item
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=1299)]),
        _make_tb(grammar={"parsed_name": "Pepperoni"}, category="Pizza",
                 variants=[_make_variant(price_cents=1399)]),
        _make_tb(grammar={"parsed_name": "Veggie"}, category="Pizza",
                 variants=[_make_variant(price_cents=1499)]),
        _make_tb(grammar={"parsed_name": "Supreme"}, category="Pizza",
                 variants=[_make_variant(price_cents=1599)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza",
                 variants=[_make_variant(price_cents=100)]),  # dup name + price outlier
    ]
    check_cross_item_consistency(blocks)
    report.check("7.4 dup + outlier on same item",
                 _count_flags(blocks[4], "cross_item_duplicate_name") == 1
                 and _count_flags(blocks[4], "cross_item_category_price_outlier") == 1,
                 f"flags: {blocks[4].get('price_flags')}")

    # 7.5: Category isolation + category outlier can coexist
    blocks = [
        _make_tb(category="Pizza", variants=[_make_variant(price_cents=1299)]),
        _make_tb(category="Pizza", variants=[_make_variant(price_cents=1399)]),
        _make_tb(category="Salads", variants=[_make_variant(price_cents=799)]),  # isolated
        _make_tb(category="Pizza", variants=[_make_variant(price_cents=1499)]),
        _make_tb(category="Pizza", variants=[_make_variant(price_cents=1599)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("7.5 isolation flag present",
                 _count_flags(blocks[2], "cross_item_category_isolated") == 1,
                 f"flags: {blocks[2].get('price_flags')}")

    # 7.6: Large menu runs without errors
    blocks = [
        _make_tb(grammar={"parsed_name": f"Item {i}"}, category="Pizza",
                 variants=[_make_variant(price_cents=1000 + i * 50)])
        for i in range(25)
    ]
    check_cross_item_consistency(blocks)
    report.check("7.6 25-item menu no crash", True)

    # 7.7: Severity levels correct
    blocks = [
        _make_tb(grammar={"parsed_name": "Test"}, variants=[_make_variant(price_cents=500)]),
        _make_tb(grammar={"parsed_name": "Test"}, variants=[_make_variant(price_cents=700)]),
    ]
    check_cross_item_consistency(blocks)
    flag = _get_flag(blocks[0], "cross_item_duplicate_name")
    report.check("7.7 dup name severity is warn",
                 flag is not None and flag["severity"] == "warn",
                 f"severity: {flag.get('severity') if flag else None}")

    # 7.8: Exact dup severity is info
    blocks = [
        _make_tb(grammar={"parsed_name": "Test"}, variants=[_make_variant(price_cents=500)]),
        _make_tb(grammar={"parsed_name": "Test"}, variants=[_make_variant(price_cents=500)]),
    ]
    check_cross_item_consistency(blocks)
    flag = _get_flag(blocks[0], "cross_item_exact_duplicate")
    report.check("7.8 exact dup severity is info",
                 flag is not None and flag["severity"] == "info",
                 f"severity: {flag.get('severity') if flag else None}")


# ==================================================================
# Group 8: Edge Cases and Regressions
# ==================================================================

def run_edge_case_tests(report: TestReport) -> None:
    print("\n--- Group 8: Edge Cases ---")

    # 8.1: Single text_block -> no checks run
    blocks = [_make_tb(grammar={"parsed_name": "Pizza"}, category="Pizza",
                       variants=[_make_variant(price_cents=999)])]
    check_cross_item_consistency(blocks)
    report.check("8.1 single block no checks",
                 len(blocks[0].get("price_flags", [])) == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 8.2: All items same name and price -> exact_duplicate
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, variants=[_make_variant(price_cents=999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("8.2 all same -> exact_duplicate",
                 all(_count_flags(b, "cross_item_exact_duplicate") == 1 for b in blocks),
                 f"flags: {[_count_flags(b, 'cross_item_exact_duplicate') for b in blocks]}")

    # 8.3: All same category, huge variance -> outliers flagged
    blocks = [
        _make_tb(grammar={"parsed_name": "Cheese Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1000)]),
        _make_tb(grammar={"parsed_name": "Pepperoni Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1000)]),
        _make_tb(grammar={"parsed_name": "Veggie Pizza"}, category="Pizza", variants=[_make_variant(price_cents=1000)]),
        _make_tb(grammar={"parsed_name": "Exotic Pizza"}, category="Pizza", variants=[_make_variant(price_cents=99999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("8.3 huge variance flagged",
                 _count_flags(blocks[3], "cross_item_category_price_outlier") == 1,
                 f"flags: {blocks[3].get('price_flags')}")

    # 8.4: All None categories -> no category checks
    blocks = [
        _make_tb(variants=[_make_variant(price_cents=999)]),
        _make_tb(variants=[_make_variant(price_cents=1099)]),
        _make_tb(variants=[_make_variant(price_cents=1199)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("8.4 all None categories ok",
                 all(_count_flags(b, "cross_item_category_price_outlier") == 0
                     and _count_flags(b, "cross_item_category_isolated") == 0
                     for b in blocks),
                 f"flags: {[b.get('price_flags') for b in blocks]}")

    # 8.5: Single category -> no isolation possible
    blocks = [
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
        _make_tb(category="Pizza"),
    ]
    check_cross_item_consistency(blocks)
    report.check("8.5 single category no isolation",
                 all(_count_flags(b, "cross_item_category_isolated") == 0 for b in blocks),
                 f"flags: {[_count_flags(b, 'cross_item_category_isolated') for b in blocks]}")

    # 8.6: Name with only whitespace -> empty, skipped
    blocks = [
        _make_tb(grammar={"parsed_name": "   "}, text="   "),
        _make_tb(grammar={"parsed_name": "   "}, text="   "),
    ]
    check_cross_item_consistency(blocks)
    report.check("8.6 whitespace-only names skipped",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 0,
                 f"flags: {blocks[0].get('price_flags')}")

    # 8.7: Grammar with empty parsed_name -> falls through to merged_text
    blocks = [
        _make_tb(text="Cheese Pizza 9.99", grammar={"parsed_name": ""},
                 variants=[_make_variant(price_cents=999)]),
        _make_tb(text="Cheese Pizza 12.99", grammar={"parsed_name": ""},
                 variants=[_make_variant(price_cents=1299)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("8.7 empty parsed_name falls through",
                 _count_flags(blocks[0], "cross_item_duplicate_name") == 1,
                 f"flags: {blocks[0].get('price_flags')}")

    # 8.8: Category with exactly 2 items below threshold
    blocks = [
        _make_tb(category="Salads", variants=[_make_variant(price_cents=799)]),
        _make_tb(category="Salads", variants=[_make_variant(price_cents=9999)]),
    ]
    check_cross_item_consistency(blocks)
    report.check("8.8 two-item category no outlier",
                 all(_count_flags(b, "cross_item_category_price_outlier") == 0 for b in blocks),
                 f"flags: {[b.get('price_flags') for b in blocks]}")

    # 8.9: Robust to string prices and missing keys
    blocks = [
        _make_tb(variants=[{"price_cents": "not_a_number"}]),
        _make_tb(variants=[{"label": "S"}]),  # missing price_cents
        _make_tb(variants=[{}]),
    ]
    check_cross_item_consistency(blocks)
    report.check("8.9 bad data no crash", True)

    # 8.10: group_size in details
    blocks = [
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=899)]),
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=999)]),
        _make_tb(grammar={"parsed_name": "Wings"}, variants=[_make_variant(price_cents=1099)]),
    ]
    check_cross_item_consistency(blocks)
    flag = _get_flag(blocks[0], "cross_item_duplicate_name")
    report.check("8.10 group_size in details",
                 flag is not None and flag["details"]["group_size"] == 3,
                 f"flag: {flag}")


# ==================================================================
# Main
# ==================================================================

def main() -> None:
    report = TestReport()

    run_duplicate_name_tests(report)
    run_category_price_outlier_tests(report)
    run_category_isolation_tests(report)
    run_name_extraction_tests(report)
    run_price_extraction_tests(report)
    run_both_path_tests(report)
    run_integration_tests(report)
    run_edge_case_tests(report)

    print(f"\n{'=' * 60}")
    print(f"Day 61 Results: {report.passed}/{report.total} passed")
    if report.failures:
        print(f"\n{len(report.failures)} FAILURES:")
        for f in report.failures:
            print(f)
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
