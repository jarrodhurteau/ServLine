# tests/test_day54_components.py
"""
Day 54: Item Component Detection & Multi-Column Merge Handling

Tests the grammar parser's ability to:
  - Tokenize description strings into individual components
  - Classify tokens as toppings, sauces, preparation methods, or flavor options
  - Detect multi-column merge artifacts in OCR output
  - Maintain backward compatibility with all 244 existing tests

Test groups:
  1. Description tokenization
  2. Sauce detection
  3. Topping extraction
  4. Preparation method detection
  5. Flavor options detection
  6. Full component integration (parse_menu_line end-to-end)
  7. Multi-column merge detection
  8. Column merge in classify_menu_lines
  9. Regression — Day 51-53 baseline cases
  10. Full-file accuracy regression

Run: python tests/test_day54_components.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.parsers.menu_grammar import (
    parse_menu_line,
    parse_menu_block,
    classify_menu_lines,
    parse_items,
    ParsedMenuItem,
    ItemComponents,
    _tokenize_description,
    _classify_components,
    _extract_components,
    detect_column_merge,
)


# ── Test Group 1: Description Tokenization ────────────

TOKENIZATION_TESTS = [
    # (input, expected_tokens)
    ("Pepperoni, Sausage, Bacon, Ham & Hamburger",
     ["Pepperoni", "Sausage", "Bacon", "Ham", "Hamburger"]),
    ("Broccoli & Chicken w/ Alfredo Sauce",
     ["Broccoli", "Chicken", "Alfredo Sauce"]),
    ("lettuce, tomato, mayo",
     ["lettuce", "tomato", "mayo"]),
    ("Steak, Onions, Peppers & Mushrooms",
     ["Steak", "Onions", "Peppers", "Mushrooms"]),
    # Single token
    ("pepperoni", ["pepperoni"]),
    # Semicolon separator
    ("Hot; Mild; BBQ", ["Hot", "Mild", "BBQ"]),
    # "and" separator
    ("ham and pineapple", ["ham", "pineapple"]),
    # Mixed separators
    ("Pepperoni, Sausage & Mushrooms and Onions",
     ["Pepperoni", "Sausage", "Mushrooms", "Onions"]),
    # w/ prefix stripping
    ("w/ marinara sauce", ["marinara sauce"]),
    # "or" separator
    ("Hot or Mild", ["Hot", "Mild"]),
    # Dot stripping
    ("pepperoni, sausage, bacon.", ["pepperoni", "sausage", "bacon"]),
]


# ── Test Group 2: Sauce Detection ────────────────────

SAUCE_DETECTION_TESTS = [
    # (item_name, description, expected_sauce)
    ("ALFREDO PIZZA", "Broccoli & Chicken w/ Alfredo Sauce", "alfredo"),
    ("PESTO CHICKEN", "Grilled Chicken, Pesto Sauce, Tomato", "pesto"),
    ("CHEESE PIZZA", "Pepperoni, Sausage, Mushrooms", None),
    ("BBQ CHICKEN", "BBQ Chicken, Bacon, Cheddar Cheese, BBQ Sauce", "bbq"),
    ("BUFFALO CHICKEN BLUE", "Buffalo Chicken, Mozzarella Cheese and Blue Cheese Base", "blue cheese"),
    ("GARLIC STEAK", "Olive Oil, Garlic Sauce, Mozzarella Cheese, Steak", "olive oil"),
    ("PLAIN BURGER", "Lettuce, Tomato, Pickles", None),
    ("RANCH CHICKEN", "Grilled Chicken, Ranch Dressing, Bacon", "ranch"),
]


# ── Test Group 3: Topping Extraction ─────────────────

TOPPING_EXTRACTION_TESTS = [
    # (description, item_name, expected_toppings_subset)
    # Items that should appear in toppings list
    ("Pepperoni, Sausage, Bacon, Ham & Hamburger", "",
     ["Pepperoni", "Sausage", "Bacon", "Ham", "Hamburger"]),
    ("Broccoli & Chicken w/ Alfredo Sauce", "",
     ["Broccoli", "Chicken"]),  # alfredo sauce → sauce, not toppings
    ("Onion, Peppers, Mushroom, Broccoli, Tomato, Olives", "",
     ["Onion", "Peppers", "Mushroom", "Broccoli", "Tomato", "Olives"]),
    ("Gyro Meat, Tomatoes, Onions, Feta Cheese", "",
     ["Tomatoes", "Onions"]),
    # Sauce tokens should NOT appear in toppings
    ("Grilled Chicken, Pesto Sauce, Tomato", "",
     ["Tomato"]),  # pesto sauce → sauce, grilled → prep, chicken → topping but prep takes it
]


# ── Test Group 4: Preparation Method Detection ───────

PREPARATION_TESTS = [
    # (description, expected_preparation)
    ("Grilled Chicken, Pesto Sauce, Tomato", "grilled"),
    ("Crispy Chicken, Ranch Dressing, Bacon, Lettuce", "crispy"),
    ("Pepperoni, Sausage, Bacon", None),
    ("Fried Chicken, Marinara, Mozzarella", "fried"),
    ("Smoked Turkey, Swiss, Lettuce", "smoked"),
]


# ── Test Group 5: Flavor Options Detection ────────────

FLAVOR_OPTION_TESTS = [
    # (item_name, description, expected_flavor_options_count, expected_first_flavor)
    # All tokens are flavors → flavor_options list
    ("BUFFALO CHICKEN", "Hot, Mild, BBQ Honey BBQ",
     3, "hot"),  # "Hot", "Mild", "BBQ Honey BBQ" (3 tokens from split)
    ("MEAT LOVERS", "Pepperoni, Sausage, Bacon",
     0, None),  # these are toppings, not flavors
    ("ALFREDO PIZZA", "Broccoli & Chicken w/ Alfredo Sauce",
     0, None),  # toppings + sauce, not flavor options
]


# ── Test Group 6: Full Component Integration ──────────

FULL_COMPONENT_TESTS = [
    # (input_line, expect_components, expect_topping_count_ge, expect_sauce)
    ("MEAT LOVERS Pepperoni, Sausage, Bacon, Ham & Hamburger 17.95",
     True, 5, None),
    ("ALFREDO PIZZA Broccoli & Chicken w/ Alfredo Sauce 14.75",
     True, 1, "alfredo"),  # Chicken may go to prep-topping or topping
    # Note: "Meat Lovers - pepperoni..." has the dash stripped by noise cleanup,
    # so separator split is not triggered and no description/components are extracted.
    # Components require the description path to be hit via separator or CAPS split.
    ("Meat Lovers - pepperoni, sausage, ham, bacon 15.99",
     False, 0, None),
    ("APPETIZERS", False, 0, None),  # heading, no components
    ("34.75", False, 0, None),  # price_only, no components
    ("CHEESE 8.00 11.50 13.95 22.50", False, 0, None),  # no description
    # Description-only lines get components too
    ("pepperoni, sausage, mushrooms, onions", True, 4, None),
    ("lettuce, tomato, mayo", True, 2, "mayo"),  # mayo is a sauce/condiment
]


# ── Test Group 7: Multi-Column Merge Detection ────────

COLUMN_MERGE_TESTS = [
    # (input, expected_segment_count_or_none)
    # 3-item grid merges (from real OCR)
    ("BLT                         CHEESEBURGER       MANHATTAN CLUB",
     3),
    ("TURKEY                      ROAST BEEF              turkey & ham",
     3),
    ("HAM                         CHICKEN CUTLET STEAK & CHEESE",
     2),  # only 1 gap of 5+; "CHICKEN CUTLET STEAK & CHEESE" stays together
    # 2-segment: item + orphaned modifier
    ("CURLY FRIES                                                          Wi CHEESE",
     2),
    # Info + column headers
    ("Add Bacon $1 extra                              Regular Deluxe",
     2),
    # Normal lines: should NOT detect merge (None)
    ("Meat Lovers - pepperoni, sausage, ham, bacon 15.99", None),
    ("CHEESE 8.00 11.50 13.95 22.50", None),
    ("Pepperoni, Sausage, Bacon, Ham & Hamburger", None),
    ("SPECIALTY PIZZAS", None),
    ("BUFFALO CHICKEN Hot, Mild, BBQ Honey BBQ", None),
    # Short lines without big gaps
    ("Cheese Pizza 12.99", None),
    ("pepperoni, sausage, mushrooms, onions", None),
]


# ── Test Group 8: Column Merge in classify_menu_lines ─

CLASSIFY_COLUMN_MERGE_TESTS = [
    # (input_lines, expected_multi_column_indices)
    (
        ["CLUB SANDWICHES", "",
         "BLT                         CHEESEBURGER       MANHATTAN CLUB",
         "TURKEY                      ROAST BEEF              turkey & ham"],
        [2, 3],
    ),
    # Normal lines: no multi_column
    (
        ["APPETIZERS", "GARLIC KNOTS 12 Pieces 5.99"],
        [],
    ),
    # Single merged line in context
    (
        ["FRENCH FRIES", "CURLY FRIES                                                          Wi CHEESE"],
        [1],
    ),
]


# ── Test Group 9: parse_items Integration ─────────────

PARSE_ITEMS_TESTS = [
    # (name, description, expect_grammar_components)
    ("Meat Lovers", "pepperoni, sausage, ham, bacon", True),
    ("Cheese Pizza", "", False),
]


# ── Test Group 10: Baseline Regression ────────────────

REGRESSION_TESTS = [
    # All critical Day 51-53 cases
    ("SPECIALTY PIZZAS", "heading", "SPECIALTY PIZZAS"),
    ("APPETIZERS", "heading", "APPETIZERS"),
    ("Margherita 12.99", "menu_item", "Margherita"),
    ("Meat Lovers - pepperoni, sausage, ham, bacon 15.99", "menu_item", "Meat Lovers"),
    ("MEAT LOVERS Pepperoni, Sausage, Bacon, Ham & Hamburger", "menu_item", "MEAT LOVERS"),
    ("BUFFALO CHICKEN Hot, Mild, BBQ Honey BBQ", "menu_item", "BUFFALO CHICKEN"),
    ("pepperoni, sausage, mushrooms, onions", "description_only", ""),
    ("CHEESE 8.00 11.50 13.95 22.50", "menu_item", "CHEESE"),
    ("34.75", "price_only", ""),
    (". 34.75", "price_only", ""),
    ('10"Mini 12" Sml 16"lrg Family Size', "size_header", ""),
    ("PIZZA & CALZONE TOPPINGS", "topping_list", ""),
    ("Choice of Sauce; Red, White, Pesto or Alfredo", "info_line", ""),
    ("HOT, MILD, BBQ, HONEY BBQ, GARLIC ROMANO,", "info_line", ""),
    ("Naked or Breaded", "info_line", ""),
    ("BEVERAGES", "heading", "BEVERAGES"),
    ("BBQ Chicken Pizza 14.99", "menu_item", "BBQ Chicken Pizza"),
    ("Garlic Knots 5.99", "menu_item", "Garlic Knots"),
    # Day 53: description continuations
    ("bacon, French Fries and pickles.", "description_only", ""),
    ("lettuce, tomato, mayo", "description_only", ""),
    ("mozzarella cheese, cheddar cheese and sour cream on the side", "description_only", ""),
]


# ── Test runner ───────────────────────────────────────

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


def run_tokenization_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 1: DESCRIPTION TOKENIZATION")
    print("=" * 60)

    for desc, expected in TOKENIZATION_TESTS:
        tokens = _tokenize_description(desc)
        report.check(
            tokens == expected,
            f"{desc[:50]!r} -> {tokens!r} (exp {expected!r})"
        )


def run_sauce_detection_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 2: SAUCE DETECTION")
    print("=" * 60)

    for item_name, desc, expected_sauce in SAUCE_DETECTION_TESTS:
        tokens = _tokenize_description(desc)
        comp = _classify_components(tokens, item_name)
        report.check(
            comp.sauce == expected_sauce,
            f"{item_name}: {desc[:40]!r} -> sauce={comp.sauce!r} (exp {expected_sauce!r})"
        )


def run_topping_extraction_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 3: TOPPING EXTRACTION")
    print("=" * 60)

    for desc, item_name, expected_subset in TOPPING_EXTRACTION_TESTS:
        tokens = _tokenize_description(desc)
        comp = _classify_components(tokens, item_name)
        # Check that expected toppings are a subset (case-insensitive)
        actual_lower = [t.lower() for t in comp.toppings]
        for exp in expected_subset:
            report.check(
                exp.lower() in actual_lower,
                f"{desc[:40]!r} -> toppings={comp.toppings!r} missing {exp!r}"
            )


def run_preparation_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 4: PREPARATION METHOD DETECTION")
    print("=" * 60)

    for desc, expected_prep in PREPARATION_TESTS:
        tokens = _tokenize_description(desc)
        comp = _classify_components(tokens)
        report.check(
            comp.preparation == expected_prep,
            f"{desc[:40]!r} -> prep={comp.preparation!r} (exp {expected_prep!r})"
        )


def run_flavor_option_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 5: FLAVOR OPTIONS DETECTION")
    print("=" * 60)

    for item_name, desc, exp_count, exp_first in FLAVOR_OPTION_TESTS:
        tokens = _tokenize_description(desc)
        comp = _classify_components(tokens, item_name)
        count_ok = len(comp.flavor_options) >= exp_count
        first_ok = exp_first is None or (comp.flavor_options and comp.flavor_options[0] == exp_first)
        report.check(
            count_ok and first_ok,
            f"{item_name}: {desc[:30]!r} -> flavors={comp.flavor_options!r} "
            f"(exp count>={exp_count}, first={exp_first!r})"
        )


def run_full_component_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 6: FULL COMPONENT INTEGRATION")
    print("=" * 60)

    for text, expect_comp, expect_topping_ge, expect_sauce in FULL_COMPONENT_TESTS:
        result = parse_menu_line(text)
        has_comp = result.components is not None
        report.check(
            has_comp == expect_comp,
            f"{text[:50]!r} -> has_components={has_comp} (exp {expect_comp})"
        )
        if expect_comp and has_comp:
            topping_count = len(result.components.toppings)
            report.check(
                topping_count >= expect_topping_ge,
                f"{text[:50]!r} -> {topping_count} toppings (exp >={expect_topping_ge})"
            )
            report.check(
                result.components.sauce == expect_sauce,
                f"{text[:50]!r} -> sauce={result.components.sauce!r} (exp {expect_sauce!r})"
            )


def run_column_merge_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 7: MULTI-COLUMN MERGE DETECTION")
    print("=" * 60)

    for text, expected in COLUMN_MERGE_TESTS:
        segments = detect_column_merge(text)
        if expected is None:
            report.check(
                segments is None,
                f"{text[:50]!r} -> segments={segments!r} (exp None)"
            )
        else:
            report.check(
                segments is not None and len(segments) == expected,
                f"{text[:50]!r} -> {len(segments) if segments else 0} segs (exp {expected})"
            )


def run_classify_column_merge_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 8: COLUMN MERGE IN classify_menu_lines")
    print("=" * 60)

    for lines, expected_indices in CLASSIFY_COLUMN_MERGE_TESTS:
        results = classify_menu_lines(lines)
        actual_indices = [i for i, r in enumerate(results) if r.line_type == "multi_column"]
        report.check(
            actual_indices == expected_indices,
            f"lines[0]={lines[0][:30]!r} -> multi_column at {actual_indices} (exp {expected_indices})"
        )
        # Verify column_segments are populated for multi_column lines
        for idx in expected_indices:
            report.check(
                results[idx].column_segments is not None and len(results[idx].column_segments) >= 2,
                f"line {idx} should have column_segments, got {results[idx].column_segments}"
            )


def run_parse_items_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 9: parse_items INTEGRATION")
    print("=" * 60)

    for name, desc, expect_comp in PARSE_ITEMS_TESTS:
        items = [{"name": name, "description": desc}]
        result = parse_items(items)
        grammar = result[0].get("grammar", {})
        has_comp = grammar.get("components") is not None
        report.check(
            has_comp == expect_comp,
            f"{name!r}: grammar.components={'present' if has_comp else 'None'} (exp {'present' if expect_comp else 'None'})"
        )
        if expect_comp and has_comp:
            report.check(
                len(grammar["components"]["toppings"]) > 0,
                f"{name!r}: components.toppings should be non-empty"
            )


def run_regression_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 10: BASELINE REGRESSION")
    print("=" * 60)

    for text, expected_type, expected_name in REGRESSION_TESTS:
        result = parse_menu_line(text)
        type_ok = result.line_type == expected_type
        name_ok = expected_name == "" or expected_name.lower() in result.item_name.lower()
        report.check(
            type_ok and name_ok,
            f"{text[:50]!r} -> type={result.line_type}(exp {expected_type}), "
            f"name={result.item_name[:30]!r}(exp {expected_name!r})"
        )


def run_full_file_regression(report: TestReport):
    """Verify both OCR files still at 100% classification (excluding multi_column)."""
    print("\n" + "=" * 60)
    print("GROUP 11: FULL-FILE ACCURACY REGRESSION")
    print("=" * 60)

    files = [
        Path(__file__).parent.parent / "fixtures" / "sample_menus" / "pizza_real_p01.ocr_used_psm3.txt",
        Path(__file__).parent.parent / "uploads" / "3d7419be_real_pizza_menu.ocr_used_psm3.txt",
    ]

    for ocr_path in files:
        if not ocr_path.exists():
            print(f"  SKIP: {ocr_path.name} not found")
            continue

        lines = ocr_path.read_text(encoding="utf-8").splitlines()

        # Single-pass
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
        print(f"  {ocr_path.name}: single-pass {classified}/{non_empty} ({rate:.1%})")
        report.check(rate >= 1.0, f"{ocr_path.name} single-pass {rate:.1%} < 100%")

        # Multi-pass
        results_multi = classify_menu_lines(lines)
        non_empty_m = 0
        classified_m = 0
        multi_col = 0
        for r in results_multi:
            if not r.raw_text.strip():
                continue
            non_empty_m += 1
            if r.line_type != "unknown":
                classified_m += 1
            if r.line_type == "multi_column":
                multi_col += 1
        rate_m = classified_m / max(non_empty_m, 1)
        print(f"  {ocr_path.name}: multi-pass {classified_m}/{non_empty_m} ({rate_m:.1%}), {multi_col} multi_column")
        report.check(rate_m >= 1.0, f"{ocr_path.name} multi-pass {rate_m:.1%} < 100%")


def main():
    report = TestReport()

    run_tokenization_tests(report)
    run_sauce_detection_tests(report)
    run_topping_extraction_tests(report)
    run_preparation_tests(report)
    run_flavor_option_tests(report)
    run_full_component_tests(report)
    run_column_merge_tests(report)
    run_classify_column_merge_tests(report)
    run_parse_items_tests(report)
    run_regression_tests(report)
    run_full_file_regression(report)

    print("\n\n" + "=" * 60)
    print("DAY 54 COMPONENT & COLUMN MERGE RESULTS")
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
