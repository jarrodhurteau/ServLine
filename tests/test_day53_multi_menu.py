# tests/test_day53_multi_menu.py
"""
Day 53: Multi-Menu Grammar Testing & Edge Case Hardening

Tests the grammar parser against patterns from a full restaurant menu OCR
(uploads/3d7419be_real_pizza_menu.ocr_used_psm3.txt) covering:
  - Pizza, Calzones, Appetizers, Wings, Burgers, Sandwiches, Wraps

Test groups:
  1. Broader description continuation detection
  2. Expanded info line & flavor list patterns
  3. Post-garble short noise cleanup
  4. W/ and Wi OCR normalization
  5. Contextual multi-pass classification
  6. Full multi-menu accuracy test
  7. Regression — Day 51 & 52 baseline cases

Run: python tests/test_day53_multi_menu.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.parsers.menu_grammar import (
    parse_menu_line,
    classify_menu_lines,
    ParsedMenuItem,
    _strip_ocr_garble,
    _strip_short_noise,
    _normalize_w_slash,
    _is_topping_or_info_line,
    _is_known_section_heading,
)


# ── Test Group 1: Description Continuation Detection ──

DESC_CONTINUATION_TESTS = [
    # (input, expected_line_type)
    # Lines starting lowercase with commas/and → description_only
    ("bacon, French Fries and pickles.", "description_only"),
    ("lettuce, tomato, mayo", "description_only"),
    ("mozzarella cheese, cheddar cheese and sour cream on the side", "description_only"),
    # Ingredient lists with expanded vocabulary
    ("Salsa and sour cream on side", "description_only"),
    ("1000 island Base, Hamburger, Pickles, Lettuce, Tomato, Mozzarella Cheese", "description_only"),
    ("Mozzarella Cheese and Blue Cheese Base", "description_only"),
    ("Mozzarella Cheese, BBQ Sauce and Ranch Dressing", "description_only"),
    ("Buffalo Chicken, Mozzarella Cheese and Blue Cheese Base", "description_only"),
    # Longer description lines (was limited to 8 words, now 14)
    ("Chicken, bacon, chips, mozzarella cheese, cheddar cheese and sour cream on the side", "description_only"),
    ("Marninare Sauce, Parmesan Cheese, Mozzarella Cheese, Homemade Fried Chicken", "description_only"),
    # Short topping lists still work
    ("pepperoni, sausage, mushrooms, onions", "description_only"),
    ("Ricotta, Parmesan, Mozzarella, Provolone", "description_only"),
]


# ── Test Group 2: Expanded Info Line Patterns ─────────

INFO_LINE_TESTS = [
    # (input, expected_line_type)
    # Original patterns still work
    ("Choice of Sauce: Red, White, Pesto or Alfredo", "info_line"),
    ("All calzones stuffed with ricotta and mozzarella.", "info_line"),
    ("All club sandwiches come with lettuce, tomato,", "info_line"),
    ("Served with side bleu cheese", "info_line"),
    # New: "Add X $Y" pattern
    ("Add Bacon $1 extra", "info_line"),
    # New: cross-reference pattern (matches _INFO_LINE_RE before keyword check)
    ("Calzone toppings same as pizza. Served with sauce on the side.", "info_line"),
    # New: option lines
    ("Naked or Breaded", "info_line"),
    ("White or Wheat", "info_line"),
    # New: ALL-CAPS flavor lists
    ("HOT, MILD, BBQ, HONEY BBQ, GARLIC ROMANO,", "info_line"),
    ("CAJUN, TERIYAKI, JACK DANIELS BBQ", "info_line"),
    # Topping lists still work
    ("PIZZA & CALZONE TOPPINGS", "topping_list"),
    ("MEAT TOPPINGS: Pepperoni - Chicken - Bacon", "topping_list"),
    # NOT info lines
    ("Cheese Pizza 12.99", "menu_item"),
    ("APPETIZERS", "heading"),
]


# ── Test Group 3: Post-Garble Short Noise Cleanup ────

SHORT_NOISE_TESTS = [
    # (input_to_strip_short_noise, expected_output_contains, expected_NOT_contains)
    # Post-garble residue (what _strip_short_noise sees after garble stripping)
    ("COMBINATION 00 recrevees 17.95", "COMBINATION", "recrevees"),
    ("GRILLED CHICKEN PIZZA 00 F590 ceoscoove 25.50 34.75", "GRILLED CHICKEN PIZZA", "ceoscoove"),
    ("BURGER PIZZA neta, eee NTS vesrcesee 25.50 34.75", "BURGER PIZZA", "vesrcesee"),
    # "00" noise and F590 mixed-digit noise removed
    ("CHEESE 00 F590 8.99", "CHEESE 8.99", "F590"),
    # Real words preserved
    ("Pepperoni, Sausage, Bacon, Ham & Hamburger", "Pepperoni", ""),
    ("Mozzarella Sticks 8.99", "Mozzarella Sticks", ""),
    ("BUFFALO CHICKEN Hot, Mild, BBQ", "BUFFALO CHICKEN", ""),
    # Price tokens preserved
    ("PIZZA 17.95 25.50 34.75", "17.95", ""),
]


# ── Test Group 4: W/ and Wi Normalization ─────────────

W_SLASH_TESTS = [
    # (input, expected_output_contains)
    ("5 PCS CHICKEN TENDERS W/ FRENCH FRIES", "with FRENCH FRIES"),
    ("Wi CHEESE", "with CHEESE"),
    ("W/FRIES 13.50", "with FRIES"),
    ("MEATBALL PARM W/ Onion, Pepper", "with Onion"),
    # Should NOT change non-W/ uses
    ("WINGS", "WINGS"),
    ("Wisconsin Cheese", "Wisconsin Cheese"),
]


# ── Test Group 5: Contextual Multi-Pass ──────────────

CONTEXTUAL_TESTS = [
    # Lines that are ALL-CAPS with no price, initially classified as heading,
    # but should be reclassified as menu_item by the contextual pass.
    # Format: (sequence_of_lines, index_to_check, expected_type)
    # Heading followed by description → reclassify as item
    (
        ["HAWAIIAN ..", "Ham, pineapple, mozzarella"],
        0, "menu_item",
    ),
    # Known section heading stays as heading
    (
        ["GOURMET PIZZA", "CHEESE 8.00 11.50 13.95"],
        0, "heading",
    ),
    # Cluster of 2+ non-known-section headings → reclassify
    (
        ["FRENCH FRIES", "CURLY FRIES", "ONION RINGS"],
        0, "menu_item",
    ),
    (
        ["FRENCH FRIES", "CURLY FRIES", "ONION RINGS"],
        1, "menu_item",
    ),
    (
        ["FRENCH FRIES", "CURLY FRIES", "ONION RINGS"],
        2, "menu_item",
    ),
    # Melt sandwich cluster
    (
        ["CHEESEBURGER MELT", "STEAK & CHEESE MELT", "GRILLED CHICKEN MELT"],
        0, "menu_item",
    ),
    # Heading between menu items → reclassify
    (
        ["Cheese Pizza 10.99", "VEGGIE", "Onion, Peppers, Mushroom, Olives"],
        1, "menu_item",
    ),
    # Known section heading at end of cluster stops cluster
    (
        ["WHITE TUNA MELT", "ROAST BEEF MELT"],
        0, "menu_item",
    ),
    # Single known heading stays
    (
        ["APPETIZERS", "GARLIC KNOTS 12 Pieces 5.99"],
        0, "heading",
    ),
]


# ── Test Group 6: Known Section Heading Detection ────

SECTION_HEADING_TESTS = [
    # (name, expected_is_known)
    ("GOURMET PIZZA", True),
    ("APPETIZERS", True),
    ("CALZONES", True),
    ("FRESH BUFFALO WINGS", True),
    ("CLUB SANDWICHES", True),
    ("MELT SANDWICHES", True),
    ("WRAPS CITY_", True),    # trailing underscore stripped
    ("BUILD YOUR OWN BURGER!", True),
    ("BUILD YOUR OWN CALZONE!", True),
    # NOT section headings
    ("FRENCH FRIES", False),
    ("CURLY FRIES", False),
    ("ONION RINGS", False),
    ("CHEESEBURGER MELT", False),
    ("WHITE TUNA MELT", False),
    ("HAWAIIAN", False),
]


# ── Test Group 7: Baseline Regression ────────────────

BASELINE_REGRESSION_TESTS = [
    # All critical Day 51 + Day 52 cases must still pass
    ("SPECIALTY PIZZAS", "heading", "SPECIALTY PIZZAS"),
    ("APPETIZERS", "heading", "APPETIZERS"),
    ("SALADS", "heading", "SALADS"),
    ("Margherita 12.99", "menu_item", "Margherita"),
    ("Cheese Pizza 10.99", "menu_item", "Cheese Pizza"),
    ("Meat Lovers - pepperoni, sausage, ham, bacon 15.99", "menu_item", "Meat Lovers"),
    ("BBQ Chicken Pizza 14.99", "menu_item", "BBQ Chicken Pizza"),
    ("pepperoni, sausage, mushrooms, onions", "description_only", ""),
    ("Garlic Knots 5.99", "menu_item", "Garlic Knots"),
    ("BEVERAGES", "heading", "BEVERAGES"),
    ("MEAT LOVERS Pepperoni, Sausage, Bacon, Ham & Hamburger", "menu_item", "MEAT LOVERS"),
    ("BUFFALO CHICKEN Hot, Mild, BBQ Honey BBQ", "menu_item", "BUFFALO CHICKEN"),
    ("CHEESE 8.00 11.50 13.95 22.50", "menu_item", "CHEESE"),
    (". 34.75", "price_only", ""),
    ("34.75", "price_only", ""),
    ('10"Mini 12" Sml 16"lrg Family Size', "size_header", ""),
    ("PIZZA & CALZONE TOPPINGS", "topping_list", ""),
    ("Choice of Sauce; Red, White, Pesto or Alfredo", "info_line", ""),
]


# ── Test runner ─────────────────────────────────────

@dataclass
class TestReport:
    total: int = 0
    passed: int = 0
    failures: List[str] = field(default_factory=list)

    def check(self, condition: bool, msg: str):
        self.total += 1
        if condition:
            self.passed += 1
        else:
            self.failures.append(msg)
            print(f"  FAIL: {msg}")


def run_desc_continuation_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 1: DESCRIPTION CONTINUATION DETECTION")
    print("=" * 60)

    for text, expected_type in DESC_CONTINUATION_TESTS:
        result = parse_menu_line(text)
        report.check(
            result.line_type == expected_type,
            f"{text[:60]!r} -> {result.line_type} (exp {expected_type})"
        )


def run_info_line_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 2: EXPANDED INFO LINE PATTERNS")
    print("=" * 60)

    for text, expected_type in INFO_LINE_TESTS:
        result = parse_menu_line(text)
        report.check(
            result.line_type == expected_type,
            f"{text[:60]!r} -> {result.line_type} (exp {expected_type})"
        )


def run_short_noise_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 3: POST-GARBLE SHORT NOISE CLEANUP")
    print("=" * 60)

    for text, expected_in, expected_not_in in SHORT_NOISE_TESTS:
        cleaned = _strip_short_noise(text)
        in_ok = expected_in in cleaned
        not_ok = expected_not_in == "" or expected_not_in not in cleaned
        report.check(
            in_ok and not_ok,
            f"{text[:50]!r} -> {cleaned[:50]!r} (want {expected_in!r}, not {expected_not_in!r})"
        )


def run_w_slash_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 4: W/ AND Wi NORMALIZATION")
    print("=" * 60)

    for text, expected_in in W_SLASH_TESTS:
        normalized = _normalize_w_slash(text)
        report.check(
            expected_in in normalized,
            f"{text!r} -> {normalized!r} (want {expected_in!r})"
        )


def run_contextual_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 5: CONTEXTUAL MULTI-PASS")
    print("=" * 60)

    for lines, idx, expected_type in CONTEXTUAL_TESTS:
        results = classify_menu_lines(lines)
        actual = results[idx].line_type
        report.check(
            actual == expected_type,
            f"lines={[l[:30] for l in lines]!r} idx={idx} -> {actual} (exp {expected_type})"
        )


def run_section_heading_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 6: KNOWN SECTION HEADING DETECTION")
    print("=" * 60)

    for name, expected in SECTION_HEADING_TESTS:
        actual = _is_known_section_heading(name)
        report.check(
            actual == expected,
            f"{name!r} -> {actual} (exp {expected})"
        )


def run_baseline_regression(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 7: BASELINE REGRESSION")
    print("=" * 60)

    for text, expected_type, expected_name in BASELINE_REGRESSION_TESTS:
        result = parse_menu_line(text)
        type_ok = result.line_type == expected_type
        name_ok = expected_name == "" or expected_name.lower() in result.item_name.lower()
        report.check(
            type_ok and name_ok,
            f"{text[:50]!r} -> type={result.line_type}(exp {expected_type}), "
            f"name={result.item_name[:30]!r}(exp {expected_name!r})"
        )


def run_full_multi_menu_accuracy(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 8: FULL MULTI-MENU ACCURACY")
    print("=" * 60)

    ocr_path = Path(__file__).parent.parent / "uploads" / "3d7419be_real_pizza_menu.ocr_used_psm3.txt"
    if not ocr_path.exists():
        print(f"  SKIP: {ocr_path} not found")
        return

    lines = ocr_path.read_text(encoding="utf-8").splitlines()

    # Single-pass stats
    stats_single = {"non_empty": 0}
    for line in lines:
        if not line.strip():
            continue
        stats_single["non_empty"] += 1
        result = parse_menu_line(line)
        lt = result.line_type
        stats_single[lt] = stats_single.get(lt, 0) + 1

    # Multi-pass stats
    results_multi = classify_menu_lines(lines)
    stats_multi = {"non_empty": 0}
    for r in results_multi:
        if not r.raw_text.strip():
            continue
        stats_multi["non_empty"] += 1
        lt = r.line_type
        stats_multi[lt] = stats_multi.get(lt, 0) + 1

    classified_single = stats_single["non_empty"] - stats_single.get("unknown", 0)
    classified_multi = stats_multi["non_empty"] - stats_multi.get("unknown", 0)
    rate_single = classified_single / max(stats_single["non_empty"], 1)
    rate_multi = classified_multi / max(stats_multi["non_empty"], 1)

    print(f"\n  [Multi-Menu Accuracy Report — {stats_single['non_empty']} non-empty lines]")
    print(f"  Single-pass classification: {rate_single:.1%}")
    print(f"  Multi-pass classification:  {rate_multi:.1%}")
    print(f"\n  Single-pass breakdown:")
    for k, v in sorted(stats_single.items()):
        if k != "non_empty":
            print(f"    {k}: {v}")
    print(f"\n  Multi-pass breakdown:")
    for k, v in sorted(stats_multi.items()):
        if k != "non_empty":
            print(f"    {k}: {v}")

    # Headings reclassified
    reclassified = stats_single.get("heading", 0) - stats_multi.get("heading", 0)
    print(f"\n  Headings reclassified to menu_item by context: {reclassified}")

    # Target: 100% classification, heading count reasonable
    report.check(
        rate_single >= 1.0,
        f"Single-pass classification {rate_single:.1%} < 100%"
    )
    report.check(
        rate_multi >= 1.0,
        f"Multi-pass classification {rate_multi:.1%} < 100%"
    )
    # Multi-pass should have more menu_items than single-pass
    report.check(
        stats_multi.get("menu_item", 0) > stats_single.get("menu_item", 0),
        f"Multi-pass menu_items ({stats_multi.get('menu_item', 0)}) should exceed "
        f"single-pass ({stats_single.get('menu_item', 0)})"
    )


def run_pizza_real_regression(report: TestReport):
    """Verify pizza_real OCR still at 100% classification."""
    print("\n" + "=" * 60)
    print("GROUP 9: PIZZA REAL OCR REGRESSION")
    print("=" * 60)

    ocr_path = Path(__file__).parent.parent / "fixtures" / "sample_menus" / "pizza_real_p01.ocr_used_psm3.txt"
    if not ocr_path.exists():
        print(f"  SKIP: {ocr_path} not found")
        return

    lines = ocr_path.read_text(encoding="utf-8").splitlines()
    non_empty = 0
    classified = 0
    for line in lines:
        if not line.strip():
            continue
        non_empty += 1
        result = parse_menu_line(line)
        if result.line_type != "unknown":
            classified += 1

    rate = classified / max(non_empty, 1)
    print(f"  pizza_real: {classified}/{non_empty} ({rate:.1%})")

    report.check(
        rate >= 1.0,
        f"pizza_real classification {rate:.1%} < 100%"
    )


def main():
    report = TestReport()

    run_desc_continuation_tests(report)
    run_info_line_tests(report)
    run_short_noise_tests(report)
    run_w_slash_tests(report)
    run_contextual_tests(report)
    run_section_heading_tests(report)
    run_baseline_regression(report)
    run_full_multi_menu_accuracy(report)
    run_pizza_real_regression(report)

    print("\n\n" + "=" * 60)
    print("DAY 53 MULTI-MENU GRAMMAR RESULTS")
    print("=" * 60)

    pct = (report.passed / max(report.total, 1)) * 100
    print(f"  TOTAL: {report.passed}/{report.total} ({pct:.0f}%)")

    if report.failures:
        print(f"\n  FAILURES ({len(report.failures)}):")
        for f in report.failures:
            print(f"    {f}")
    else:
        print("\n  All tests passed!")

    print("=" * 60)
    return 0 if not report.failures else 1


if __name__ == "__main__":
    sys.exit(main())
