# tests/test_phase8_baseline.py
"""
Phase 8 Baseline Metrics — Day 51

Measures the current state of semantic parsing across all Phase 8 targets:
  1. Category accuracy (phrase-level keywords)
  2. Variant detection rate (portion/crust/flavor coverage)
  3. Grammar parser success rate (line_type classification)
  4. Long-name rescue effectiveness
  5. Price validation coverage

Run: python tests/test_phase8_baseline.py
"""

import sys
from pathlib import Path
from typing import Dict, Any, List
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.parsers.menu_grammar import parse_menu_line, parse_menu_block, ParsedMenuItem
from storage.category_infer import infer_category_for_text, CATEGORY_PHRASES
from storage.variant_engine import classify_raw_variant
from storage.ai_cleanup import _rescue_long_name, normalize_draft_items


# ── Test data: realistic menu lines from validated Day 50 menus ──

GRAMMAR_TEST_CASES = [
    # (input_text, expected_line_type, expected_name_fragment)
    ("SPECIALTY PIZZAS", "heading", "SPECIALTY PIZZAS"),
    ("APPETIZERS", "heading", "APPETIZERS"),
    ("Wings", "heading", "Wings"),
    ("SALADS", "heading", "SALADS"),
    ("Margherita 12.99", "menu_item", "Margherita"),
    ("Hawaiian 13.99", "menu_item", "Hawaiian"),
    ("Cheese Pizza 10.99", "menu_item", "Cheese Pizza"),
    ("Meat Lovers - pepperoni, sausage, ham, bacon 15.99", "menu_item", "Meat Lovers"),
    ("BBQ Chicken Pizza 14.99", "menu_item", "BBQ Chicken Pizza"),
    ("pepperoni, sausage, mushrooms, onions", "description_only", ""),
    ("Garlic Knots 5.99", "menu_item", "Garlic Knots"),
    ("Caesar Salad 8.99", "menu_item", "Caesar Salad"),
    ("Baked Ziti 12.99", "menu_item", "Baked Ziti"),
    ("Chicken Parm Sub 9.99", "menu_item", "Chicken Parm Sub"),
    ("2 Liter Soda 3.99", "menu_item", ""),  # size stripped from name
    ("Chocolate Brownie 4.99", "menu_item", "Chocolate Brownie"),
    ("BEVERAGES", "heading", "BEVERAGES"),
    ("PASTA", "heading", "PASTA"),
]

CATEGORY_TEST_CASES = [
    # (name, description, expected_category)
    ("Cheese Pizza", "", "Pizza"),
    ("Margherita", "", "Pizza"),
    ("Buffalo Chicken Pizza", "", "Pizza"),
    ("BBQ Chicken Pizza", "bbq sauce, chicken, onion, cilantro", "Pizza"),
    ("Chicken Wings", "", "Wings"),
    ("Boneless Wings", "honey bbq", "Wings"),
    ("Buffalo Wings", "", "Wings"),
    ("Caesar Salad", "", "Salads"),
    ("Grilled Chicken Salad", "romaine, tomato, cucumber", "Salads"),
    ("Baked Ziti", "", "Pasta"),
    ("Fettuccine Alfredo", "", "Pasta"),
    ("Spaghetti and Meatballs", "", "Pasta"),
    ("Mozzarella Sticks", "", "Sides / Appetizers"),
    ("French Fries", "", "Sides / Appetizers"),
    ("Garlic Knots", "", "Sides / Appetizers"),
    ("Bacon Cheeseburger", "", "Burgers"),
    ("Classic Burger", "", "Burgers"),
    ("Philly Cheesesteak", "", "Subs / Sandwiches"),
    ("Italian Sub", "", "Subs / Sandwiches"),
    ("Meatball Sub", "", "Subs / Sandwiches"),
    ("Cheesecake", "", "Desserts"),
    ("Tiramisu", "", "Desserts"),
    ("2 Liter Coke", "", "Beverages"),
    ("Iced Tea", "", "Beverages"),
    ("Calzone", "ricotta, mozzarella", "Calzones / Stromboli"),
    ("Stromboli", "", "Calzones / Stromboli"),
]

VARIANT_TEST_CASES = [
    # (label, expected_kind)
    ("Small", "size"),
    ("Large", "size"),
    ("XL", "size"),
    ("Half", "size"),
    ("Whole", "size"),
    ("Slice", "size"),
    ("Family", "size"),
    ("Party", "size"),
    ("Personal", "size"),
    ("Single", "size"),
    ("Double", "size"),
    ("Triple", "size"),
    ('10"', "size"),
    ('14"', "size"),
    ("6pc", "size"),
    ("12pc", "size"),
    ("24pc", "size"),
    ("Thin Crust", "style"),
    ("Deep Dish", "style"),
    ("Hand Tossed", "style"),
    ("Brooklyn", "style"),
    ("Sicilian", "style"),
    ("Pan", "style"),
    ("Gluten Free", "style"),
    ("Cauliflower Crust", "style"),
    ("Flatbread", "style"),
    ("Bone-in", "style"),
    ("Boneless", "style"),
    ("Grilled", "style"),
    ("Fried", "style"),
    ("Breaded", "style"),
    ("Hot", "flavor"),
    ("Mild", "flavor"),
    ("BBQ", "flavor"),
    ("Honey BBQ", "flavor"),
    ("Garlic Parm", "flavor"),
    ("Buffalo", "flavor"),
    ("Teriyaki", "flavor"),
    ("Lemon Pepper", "flavor"),
    ("Mango Habanero", "flavor"),
    ("Sweet Chili", "flavor"),
    ("Sriracha", "flavor"),
]

LONG_NAME_TEST_CASES = [
    # (long_name, existing_desc, should_split: bool, expected_head_contains)
    (
        "Supreme Pizza with pepperoni sausage mushrooms onions green peppers and black olives",
        "",
        True,
        "Supreme Pizza",
    ),
    (
        "Meat Lovers Pizza topped with pepperoni sausage ham bacon and ground beef",
        "",
        True,
        "Meat Lovers Pizza",
    ),
    (
        "Hawaiian Pizza (ham, pineapple, mozzarella cheese on our fresh dough)",
        "",
        True,
        "Hawaiian Pizza",
    ),
    (
        "Calzones Stuffed With Ricotta - pepperoni sausage mushrooms onions and peppers baked golden",
        "",
        True,
        "Calzones Stuffed With Ricotta",
    ),
    (
        "Cheese Pizza",
        "",
        False,
        "Cheese Pizza",
    ),
    (
        "BBQ Chicken",
        "bbq sauce, chicken, onion",
        False,
        "BBQ Chicken",
    ),
]


@dataclass
class MetricsReport:
    """Collects pass/fail counts for each metric category."""
    grammar_total: int = 0
    grammar_pass: int = 0
    grammar_failures: List[str] = field(default_factory=list)

    category_total: int = 0
    category_pass: int = 0
    category_failures: List[str] = field(default_factory=list)

    variant_total: int = 0
    variant_pass: int = 0
    variant_failures: List[str] = field(default_factory=list)

    longname_total: int = 0
    longname_pass: int = 0
    longname_failures: List[str] = field(default_factory=list)


def run_grammar_tests(report: MetricsReport):
    """Test grammar parser line_type classification."""
    print("\n" + "=" * 60)
    print("GRAMMAR PARSER TESTS")
    print("=" * 60)

    for text, expected_type, expected_name in GRAMMAR_TEST_CASES:
        report.grammar_total += 1
        result = parse_menu_line(text)

        type_ok = result.line_type == expected_type
        name_ok = expected_name == "" or expected_name.lower() in result.item_name.lower()

        if type_ok and name_ok:
            report.grammar_pass += 1
        else:
            msg = "  FAIL: %r -> type=%s (exp %s), name=%r (exp %r)" % (
                text[:50], result.line_type, expected_type, result.item_name[:40], expected_name
            )
            report.grammar_failures.append(msg)
            print(msg)

    pct = (report.grammar_pass / max(report.grammar_total, 1)) * 100
    print("Grammar: %d/%d (%.0f%%)" % (report.grammar_pass, report.grammar_total, pct))


def run_category_tests(report: MetricsReport):
    """Test category inference with phrase-level keywords."""
    print("\n" + "=" * 60)
    print("CATEGORY INFERENCE TESTS")
    print("=" * 60)

    for name, desc, expected_cat in CATEGORY_TEST_CASES:
        report.category_total += 1
        guess = infer_category_for_text(name, desc)

        if guess.category == expected_cat:
            report.category_pass += 1
        else:
            msg = "  FAIL: %r -> %s (exp %s) [conf=%d, reason=%s]" % (
                name, guess.category, expected_cat, guess.confidence, guess.reason
            )
            report.category_failures.append(msg)
            print(msg)

    pct = (report.category_pass / max(report.category_total, 1)) * 100
    print("Category: %d/%d (%.0f%%)" % (report.category_pass, report.category_total, pct))


def run_variant_tests(report: MetricsReport):
    """Test variant engine kind classification."""
    print("\n" + "=" * 60)
    print("VARIANT ENGINE TESTS")
    print("=" * 60)

    for label, expected_kind in VARIANT_TEST_CASES:
        report.variant_total += 1
        v = classify_raw_variant(label)

        if v.get("kind") == expected_kind:
            report.variant_pass += 1
        else:
            msg = "  FAIL: %r -> kind=%s (exp %s)" % (
                label, v.get("kind"), expected_kind
            )
            report.variant_failures.append(msg)
            print(msg)

    pct = (report.variant_pass / max(report.variant_total, 1)) * 100
    print("Variant: %d/%d (%.0f%%)" % (report.variant_pass, report.variant_total, pct))


def run_longname_tests(report: MetricsReport):
    """Test long-name rescue heuristics."""
    print("\n" + "=" * 60)
    print("LONG-NAME RESCUE TESTS")
    print("=" * 60)

    for name, desc, should_split, expected_head in LONG_NAME_TEST_CASES:
        report.longname_total += 1
        head, tail = _rescue_long_name(name, desc)

        did_split = bool(tail)
        head_ok = expected_head.lower() in head.lower()

        if did_split == should_split and head_ok:
            report.longname_pass += 1
        else:
            msg = "  FAIL: %r -> split=%s (exp %s), head=%r (exp %r)" % (
                name[:50], did_split, should_split, head[:40], expected_head
            )
            report.longname_failures.append(msg)
            print(msg)

    pct = (report.longname_pass / max(report.longname_total, 1)) * 100
    print("Long-name: %d/%d (%.0f%%)" % (report.longname_pass, report.longname_total, pct))


def print_final_report(report: MetricsReport):
    """Print the full baseline metrics summary."""
    print("\n\n" + "=" * 60)
    print("PHASE 8 BASELINE METRICS — Day 51")
    print("=" * 60)

    metrics = [
        ("Grammar parse", report.grammar_pass, report.grammar_total, report.grammar_failures),
        ("Category inference", report.category_pass, report.category_total, report.category_failures),
        ("Variant detection", report.variant_pass, report.variant_total, report.variant_failures),
        ("Long-name rescue", report.longname_pass, report.longname_total, report.longname_failures),
    ]

    total_pass = 0
    total_count = 0

    for label, passed, total, failures in metrics:
        pct = (passed / max(total, 1)) * 100
        status = "OK" if pct >= 90 else "WARN" if pct >= 75 else "FAIL"
        print("  [%4s] %-22s %d/%d (%.0f%%)" % (status, label, passed, total, pct))
        total_pass += passed
        total_count += total

    overall = (total_pass / max(total_count, 1)) * 100
    print("\n  OVERALL: %d/%d (%.0f%%)" % (total_pass, total_count, overall))

    # Print any failures
    all_failures = []
    for _, _, _, failures in metrics:
        all_failures.extend(failures)

    if all_failures:
        print("\n  FAILURES:")
        for f in all_failures:
            print(f)
    else:
        print("\n  All tests passed!")

    print("\n" + "=" * 60)
    print("Phase 8 targets: category 90%+, variant 85%+, parse 95%+")
    print("=" * 60)


def main():
    report = MetricsReport()

    run_grammar_tests(report)
    run_category_tests(report)
    run_variant_tests(report)
    run_longname_tests(report)
    print_final_report(report)


if __name__ == "__main__":
    main()
