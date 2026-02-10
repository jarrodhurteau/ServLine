# tests/test_day55_integration.py
"""
Day 55: Sprint 8.1 Finale — Pipeline Integration, Fallback OCR & Confidence

Tests the grammar parser's:
  1. Pipeline integration (enrich_grammar_on_text_blocks)
  2. OCR typo normalization (88Q→BBQ, piZzA→PIZZA, etc.)
  3. Confidence tier mapping
  4. Fallback OCR hardening (degraded Tesseract output)
  5. Size header / dimension line detection improvements
  6. Regression — Day 51-54 baseline cases
  7. Full-file accuracy (primary + fallback OCR files)

Run: python tests/test_day55_integration.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.parsers.menu_grammar import (
    parse_menu_line,
    parse_menu_block,
    classify_menu_lines,
    parse_items,
    ParsedMenuItem,
    ItemComponents,
    detect_column_merge,
    enrich_grammar_on_text_blocks,
    confidence_tier,
    _normalize_ocr_typos,
    _is_size_header,
    _is_topping_or_info_line,
    _parsed_to_dict,
)


# ── Test Group 1: OCR Typo Normalization ──────────────

OCR_TYPO_TESTS = [
    # (input, expected_substring)
    ("BUFFALO CHICKEN Hot, Mild, 88Q Honey BBQ", "BBQ Honey BBQ"),
    ("Mozzarella Cheese, 88Q Sauce and Ranch", "BBQ Sauce"),
    ("BUFFALO CHICKEN Hot, Mild, 880 or Teriyaki", "BBQ or Teriyaki"),
    ("Honey 8BQ", "Honey BBQ"),
    ("B8Q Chicken Wings", "BBQ Chicken Wings"),
    ("88q sauce on the side", "BBQ sauce on the side"),
    ("piZzA", "PIZZA"),
    ("12\" Smt 16\"lrg", 'Sml'),
    ("[a1 4 PCS FRIED CHICKEN", "4 PCS FRIED CHICKEN"),
    ("WI/FRIES 13.50", "W/FRIES 13.50"),
    ("Chicken, Broccoli, Tomatoes & Basi!", "Basil"),
    # Preserve real content
    ("BBQ Chicken Pizza", "BBQ Chicken Pizza"),
    ("Large Pepperoni Pizza 14.99", "Large Pepperoni Pizza 14.99"),
]


# ── Test Group 2: Confidence Tier Mapping ─────────────

CONFIDENCE_TIER_TESTS = [
    (0.95, "high"),
    (0.85, "high"),
    (0.80, "high"),
    (0.79, "medium"),
    (0.75, "medium"),
    (0.65, "medium"),
    (0.60, "medium"),
    (0.59, "low"),
    (0.52, "low"),
    (0.45, "low"),
    (0.40, "low"),
    (0.39, "unknown"),
    (0.20, "unknown"),
    (0.0, "unknown"),
]


# ── Test Group 3: Size Header / Dimension Detection ───

SIZE_HEADER_TESTS = [
    # (input, expected_is_size_header)
    ("Regular Deluxe", True),
    ("10\" Mini  12\" Sml  16\" Lrg  Family Size", True),
    ("Small Medium Large", True),
    ("8 Slices   12 Slices   24 Slices", True),
    # Not size headers
    ("PIZZA", False),
    ("MEAT LOVERS Pepperoni, Sausage", False),
    ("Cheese Pizza 12.99", False),
]

DIMENSION_LINE_TESTS = [
    # (input, expected_is_info, expected_type)
    ('17x26"', True, "info_line"),
    ("17x24", True, "info_line"),
    ("17 x 26\"", True, "info_line"),
    # Not dimension lines
    ("PIZZA", False, ""),
    ("Cheese 12.99", False, ""),
]


# ── Test Group 4: Pipeline Integration ────────────────

PIPELINE_INTEGRATION_TESTS = [
    # Simulate text_blocks with merged_text keys
    (
        [
            {"merged_text": "PIZZA", "role": "heading"},
            {"merged_text": "Cheese Pizza 12.99", "role": "item"},
            {"merged_text": "Pepperoni, Sausage, Mushrooms", "role": "description"},
            {"merged_text": "APPETIZERS", "role": "heading"},
            {"merged_text": "Garlic Knots 5.99", "role": "item"},
        ],
        [
            ("heading", "high"),
            ("menu_item", "medium"),
            ("description_only", "medium"),
            ("heading", "high"),
            ("menu_item", "medium"),
        ],
    ),
]


# ── Test Group 5: Fallback OCR Line Classification ────

FALLBACK_OCR_TESTS = [
    # Lines from the fallback OCR file with expected classifications
    # (input, expected_line_type, expected_name_substring)
    ("piZzA", "heading", "PIZZA"),
    # Single-pass: garble-stripped → menu_item (multi_column only in classify_menu_lines)
    ("CHEESE                                     coseeee 8.00 ...000- 11.50 o.sssseees13.95 ne22.50",
     "menu_item", "CHEESE"),
    ("Choice of Sauce; Red, White, Pesto or Alfredo, Garlic Sauce, Ranch Sauce or Blue Cheese",
     "info_line", ""),
    ("GOURMET PIZZA", "heading", "GOURMET PIZZA"),
    ("MEAT LOVERS Pepperoni, Sausage, Bacon, Ham & Hamburger.17.95",
     "menu_item", "MEAT LOVERS"),
    ("Pepperoni, Hamburger, Sausage, Peppers, Onions & Mushrooms",
     "description_only", ""),
    ("BUFFALO CHICKEN Hot, Mild, 88Q Honey BBQ...",
     "menu_item", "BUFFALO CHICKEN"),
    ("ALFREDO PIZZA Broccoli & Chicken w/ Alfredo Sauce",
     "menu_item", "ALFREDO PIZZA"),
    ("POLLO CHICKEN Chicken, Broccoli, Tomatoes & Basi!",
     "menu_item", "POLLO CHICKEN"),
    ('17x26"', "info_line", ""),
    ("Regular Deluxe", "size_header", "Regular Deluxe"),
    ("PIZZA & CALZONE TOPPINGS", "topping_list", ""),
    ("8 Slices   12 Slices   24 Slices", "size_header", ""),
    ("MEAT TOPPINGS: Pepperoni -Chicken - Bacon - Hamburger -Sausage - Meatball",
     "topping_list", ""),
    ("Naked or Breaded", "info_line", ""),
    ("HOT, MILD, BBQ, HONEY BBQ, GARLIC ROMANO,", "info_line", ""),
    (". 34.75", "price_only", ""),
    (". $4.75", "price_only", ""),
    ("-- $4.75", "price_only", ""),
    ("» 34,75", "price_only", ""),
    ("BUFFALO CHICKEN Hot, Mild, 880 or Teriyaki with side of Bleu Cheese",
     "menu_item", "BUFFALO CHICKEN"),
    ("[a1 4 PCS FRIED CHICKEN W/ FRENCH FRIES...",
     "menu_item", "FRIED CHICKEN"),
    ("9.95 WI/FRIES 13.50", "menu_item", ""),
    ("STEAK & CHEESE steak, Lettuce, Tomato, Mayo & American Cheese",
     "menu_item", "STEAK"),
    ("HONEY MUSTARD CHICKEN WITH BACON tertuce, Tomato, Mayo, Crispy or Grilled Chicken",
     "menu_item", "HONEY MUSTARD CHICKEN"),
]


# ── Test Group 6: enrich_grammar_on_text_blocks Details ──

ENRICH_DETAIL_TESTS = [
    # Test that grammar dict has all required keys
    {"merged_text": "MEAT LOVERS Pepperoni, Sausage, Bacon 17.95"},
    {"merged_text": "APPETIZERS"},
    {"merged_text": ""},
    {"merged_text": "bacon, lettuce, tomato and mayo"},
]


# ── Test Group 7: Fallback OCR Component Detection ────

FALLBACK_COMPONENT_TESTS = [
    # (fallback OCR text, expect_has_components, expect_sauce_or_none, expect_min_toppings)
    ("BUFFALO CHICKEN Hot, Mild, 88Q Honey BBQ...",
     True, None, 0),  # flavor options, not toppings
    ("ALFREDO PIZZA Broccoli & Chicken w/ Alfredo Sauce",
     True, "alfredo", 1),
    ("PESTO CHICKEN Grilled Chicken, Pesto Sauce, Tomato...",
     True, "pesto", 1),
    ("STEAK & CHEESE steak, Lettuce, Tomato, Mayo & American Cheese",
     True, "mayo", 2),
]


# ── Test Group 8: Regression (Day 51-54 baseline) ─────

REGRESSION_TESTS = [
    # Day 51 baselines
    ("Pepperoni Pizza 12.99", "menu_item", "Pepperoni Pizza"),
    ("PIZZA", "heading", "PIZZA"),
    ("extra cheese", "menu_item", "extra cheese"),
    # Day 52 baselines
    ("10\" Mini  12\" Sml  16\" Lrg  Family Size", "size_header", ""),
    ("PIZZA & CALZONE TOPPINGS", "topping_list", ""),
    ("Served with side bleu cheese", "info_line", ""),
    (". 34.75", "price_only", ""),
    ("MEAT LOVERS Pepperoni, Sausage, Bacon, Ham & Hamburger", "menu_item", "MEAT LOVERS"),
    # Day 53 baselines
    ("bacon, French Fries and pickles.", "description_only", ""),
    ("lettuce, tomato, mayo", "description_only", ""),
    ("HOT, MILD, BBQ, HONEY BBQ, GARLIC ROMANO,", "info_line", ""),
    ("Naked or Breaded", "info_line", ""),
    ("All club sandwiches come with lettuce, tomato,", "info_line", ""),
    # Day 54 baselines
    ("BBQ Chicken Pizza 14.99", "menu_item", "BBQ Chicken Pizza"),
    ("Garlic Knots 5.99", "menu_item", "Garlic Knots"),
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


def run_ocr_typo_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 1: OCR TYPO NORMALIZATION")
    print("=" * 60)

    for raw, expected_sub in OCR_TYPO_TESTS:
        result = _normalize_ocr_typos(raw)
        report.check(
            expected_sub in result,
            f"{raw[:50]!r} -> {result[:50]!r} (missing {expected_sub!r})"
        )


def run_confidence_tier_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 2: CONFIDENCE TIER MAPPING")
    print("=" * 60)

    for score, expected in CONFIDENCE_TIER_TESTS:
        actual = confidence_tier(score)
        report.check(
            actual == expected,
            f"confidence_tier({score}) -> {actual!r} (exp {expected!r})"
        )


def run_size_header_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 3a: SIZE HEADER DETECTION")
    print("=" * 60)

    for text, expected in SIZE_HEADER_TESTS:
        actual = _is_size_header(text)
        report.check(
            actual == expected,
            f"_is_size_header({text[:40]!r}) -> {actual} (exp {expected})"
        )


def run_dimension_line_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 3b: DIMENSION LINE DETECTION")
    print("=" * 60)

    for text, exp_is_info, exp_type in DIMENSION_LINE_TESTS:
        is_info, info_type = _is_topping_or_info_line(text)
        report.check(
            is_info == exp_is_info,
            f"_is_topping_or_info_line({text[:30]!r}) -> is_info={is_info} (exp {exp_is_info})"
        )
        if exp_is_info:
            report.check(
                info_type == exp_type,
                f"  type={info_type!r} (exp {exp_type!r})"
            )


def run_pipeline_integration_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 4: PIPELINE INTEGRATION (enrich_grammar_on_text_blocks)")
    print("=" * 60)

    for text_blocks, expected_types in PIPELINE_INTEGRATION_TESTS:
        # Make copies to avoid mutation issues
        tbs = [dict(tb) for tb in text_blocks]
        enrich_grammar_on_text_blocks(tbs)

        for i, (tb, (exp_type, exp_tier)) in enumerate(zip(tbs, expected_types)):
            grammar = tb.get("grammar")
            report.check(
                grammar is not None,
                f"text_block[{i}] should have 'grammar' key"
            )
            if grammar:
                report.check(
                    grammar["line_type"] == exp_type,
                    f"text_block[{i}] ({tb['merged_text'][:30]!r}): "
                    f"line_type={grammar['line_type']!r} (exp {exp_type!r})"
                )
                report.check(
                    grammar["confidence_tier"] == exp_tier,
                    f"text_block[{i}]: tier={grammar['confidence_tier']!r} (exp {exp_tier!r})"
                )

    # Test empty list
    empty: List[Dict[str, Any]] = []
    enrich_grammar_on_text_blocks(empty)
    report.check(len(empty) == 0, "empty text_blocks should remain empty")


def run_fallback_ocr_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 5: FALLBACK OCR LINE CLASSIFICATION")
    print("=" * 60)

    for text, exp_type, exp_name in FALLBACK_OCR_TESTS:
        result = parse_menu_line(text)
        type_ok = result.line_type == exp_type
        name_ok = (exp_name == "" or
                   exp_name.lower() in result.item_name.lower())
        report.check(
            type_ok,
            f"{text[:60]!r} -> type={result.line_type!r} (exp {exp_type!r})"
        )
        if exp_name:
            report.check(
                name_ok,
                f"  name={result.item_name[:40]!r} (exp contains {exp_name!r})"
            )


def run_enrich_detail_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 6: enrich_grammar_on_text_blocks DETAIL")
    print("=" * 60)

    tbs = [dict(tb) for tb in ENRICH_DETAIL_TESTS]
    enrich_grammar_on_text_blocks(tbs)

    required_keys = {
        "parsed_name", "parsed_description", "modifiers",
        "size_mentions", "price_mentions", "line_type",
        "parse_confidence", "confidence_tier", "components",
        "column_segments",
    }

    for i, tb in enumerate(tbs):
        grammar = tb.get("grammar", {})
        actual_keys = set(grammar.keys())
        missing = required_keys - actual_keys
        report.check(
            not missing,
            f"text_block[{i}]: missing grammar keys: {missing}"
        )
        # Confidence tier should be a valid tier
        tier = grammar.get("confidence_tier", "")
        report.check(
            tier in ("high", "medium", "low", "unknown"),
            f"text_block[{i}]: invalid tier {tier!r}"
        )

    # Test the menu_item block has components
    grammar_item = tbs[0].get("grammar", {})
    report.check(
        grammar_item.get("components") is not None,
        "MEAT LOVERS block should have components"
    )
    if grammar_item.get("components"):
        report.check(
            len(grammar_item["components"]["toppings"]) >= 2,
            f"MEAT LOVERS: toppings count={len(grammar_item['components']['toppings'])} (exp >=2)"
        )


def run_fallback_component_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 7: FALLBACK OCR COMPONENT DETECTION")
    print("=" * 60)

    for text, exp_comp, exp_sauce, exp_min_toppings in FALLBACK_COMPONENT_TESTS:
        result = parse_menu_line(text)
        has_comp = result.components is not None
        report.check(
            has_comp == exp_comp,
            f"{text[:50]!r} -> has_components={has_comp} (exp {exp_comp})"
        )
        if exp_comp and has_comp:
            report.check(
                result.components.sauce == exp_sauce,
                f"  sauce={result.components.sauce!r} (exp {exp_sauce!r})"
            )
            report.check(
                len(result.components.toppings) >= exp_min_toppings,
                f"  toppings={len(result.components.toppings)} (exp >={exp_min_toppings})"
            )


def run_regression_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 8: BASELINE REGRESSION (Days 51-54)")
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


def run_full_file_accuracy(report: TestReport):
    """Verify all 4 OCR files (primary + fallback) at 100% classification."""
    print("\n" + "=" * 60)
    print("GROUP 9: FULL-FILE ACCURACY (PRIMARY + FALLBACK OCR)")
    print("=" * 60)

    files = [
        # Primary OCR files (Day 52-54 validated)
        Path(__file__).parent.parent / "fixtures" / "sample_menus" / "pizza_real_p01.ocr_used_psm3.txt",
        Path(__file__).parent.parent / "uploads" / "3d7419be_real_pizza_menu.ocr_used_psm3.txt",
        # Fallback OCR files (Day 55 new)
        Path(__file__).parent.parent / "fixtures" / "sample_menus" / "pizza_real_p01.ocr_fallback.txt",
        Path(__file__).parent.parent / "uploads" / "3d7419be_real_pizza_menu.ocr_fallback.txt",
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
        type_counts: Counter = Counter()
        tier_counts: Counter = Counter()
        for r in results_multi:
            if not r.raw_text.strip():
                continue
            non_empty_m += 1
            if r.line_type != "unknown":
                classified_m += 1
            if r.line_type == "multi_column":
                multi_col += 1
            type_counts[r.line_type] += 1
            tier_counts[confidence_tier(r.confidence)] += 1
        rate_m = classified_m / max(non_empty_m, 1)
        print(f"  {ocr_path.name}: multi-pass {classified_m}/{non_empty_m} ({rate_m:.1%}), {multi_col} multi_column")
        report.check(rate_m >= 1.0, f"{ocr_path.name} multi-pass {rate_m:.1%} < 100%")

        # Verify tier distribution — no "unknown" tier for non-empty classified lines
        unknown_tier = tier_counts.get("unknown", 0)
        report.check(
            unknown_tier == 0,
            f"{ocr_path.name}: {unknown_tier} lines with 'unknown' confidence tier"
        )


def run_pipeline_enrich_full_file(report: TestReport):
    """Test enrich_grammar_on_text_blocks on simulated pipeline text_blocks from real OCR."""
    print("\n" + "=" * 60)
    print("GROUP 10: PIPELINE ENRICH ON REAL OCR TEXT BLOCKS")
    print("=" * 60)

    ocr_path = Path(__file__).parent.parent / "fixtures" / "sample_menus" / "pizza_real_p01.ocr_used_psm3.txt"
    if not ocr_path.exists():
        print("  SKIP: pizza_real_p01.ocr_used_psm3.txt not found")
        return

    lines = ocr_path.read_text(encoding="utf-8").splitlines()
    non_empty_lines = [l for l in lines if l.strip()]

    # Simulate text_blocks (just merged_text key)
    text_blocks: List[Dict[str, Any]] = [
        {"merged_text": line} for line in non_empty_lines
    ]

    enrich_grammar_on_text_blocks(text_blocks)

    # Every text_block should have a grammar key
    enriched = sum(1 for tb in text_blocks if "grammar" in tb)
    report.check(
        enriched == len(text_blocks),
        f"enriched {enriched}/{len(text_blocks)} text_blocks"
    )

    # All should have valid line_type and confidence_tier
    for i, tb in enumerate(text_blocks):
        grammar = tb.get("grammar", {})
        lt = grammar.get("line_type", "")
        report.check(
            lt != "unknown",
            f"text_block[{i}] ({tb['merged_text'][:30]!r}): line_type='unknown'"
        )

    # Count items with components
    with_components = sum(
        1 for tb in text_blocks
        if tb.get("grammar", {}).get("components") is not None
    )
    print(f"  {len(text_blocks)} text_blocks enriched, {with_components} with components")
    report.check(
        with_components >= 20,
        f"Expected >=20 blocks with components, got {with_components}"
    )


def main():
    report = TestReport()

    run_ocr_typo_tests(report)
    run_confidence_tier_tests(report)
    run_size_header_tests(report)
    run_dimension_line_tests(report)
    run_pipeline_integration_tests(report)
    run_fallback_ocr_tests(report)
    run_enrich_detail_tests(report)
    run_fallback_component_tests(report)
    run_regression_tests(report)
    run_full_file_accuracy(report)
    run_pipeline_enrich_full_file(report)

    print("\n\n" + "=" * 60)
    print("DAY 55 SPRINT 8.1 FINALE RESULTS")
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
