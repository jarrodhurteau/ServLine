# tests/test_day52_pizza_grammar.py
"""
Day 52: Pizza-Specific Grammar Rules — Real OCR Testing

Tests the grammar parser against patterns from real pizza menu OCR output
(fixtures/sample_menus/pizza_real_p01.ocr_used_psm3.txt).

Test groups:
  1. OCR dot-leader garble stripping
  2. ALL CAPS name + mixed-case description split
  3. Size grid header detection
  4. Topping / info line detection
  5. Price-only / orphaned price detection
  6. Multi-price handling
  7. Baseline grammar regression check
  8. Full real OCR accuracy test

Run: python tests/test_day52_pizza_grammar.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.parsers.menu_grammar import (
    parse_menu_line,
    parse_menu_block,
    ParsedMenuItem,
    _strip_ocr_garble,
    _is_garble_run,
    _is_size_header,
    _is_topping_or_info_line,
    _is_price_only_line,
    _split_caps_name_from_desc,
)


# ── Test Group 1: OCR Garble Stripping ──────────────

GARBLE_STRIP_TESTS = [
    # (input, expected_name_fragment, expected_preserved_fragment)
    # Real OCR lines — garble should be stripped, items/prices preserved
    (
        "CHEESE                                     coseeee 8.00 ...000- 11.50 o.sssseees13.95 ne22.50",
        "CHEESE",
        "8.00",
    ),
    (
        "MARGARITA Rcccccerccrrrerseessrsessstessesssssrressesrsorsrrsmrcermesees 34.75",
        "MARGARITA",
        "34.75",
    ),
    (
        "COMBINATION recssersessetsssnrreneerereessssr-orersareaeeeserrrtrttreeeet 17.95",
        "COMBINATION",
        "17.95",
    ),
    (
        "POTATO BACON PIZZA .........sssvssssssccsssscnnnsvessnescersensesrares 47.95",
        "POTATO BACON PIZZA",
        "47.95",
    ),
    (
        "MEATBALL PARM Onion, Pepper, Parmesan Cheese ...ssssssssssssessseesssseeesesees 14.75",
        "MEATBALL PARM",
        "14.75",
    ),
    (
        "STUFFED GRAPE LEAVES 8 PCS ....esesssssscsccscccssscscccccsesecesseceee",
        "STUFFED GRAPE LEAVES",
        "8 PCS",
    ),
    # Ensure real food words are NOT stripped
    (
        "Pepperoni, Sausage, Bacon, Ham & Hamburger",
        "Pepperoni",
        "Hamburger",
    ),
    (
        "Mozzarella Sticks 8.99",
        "Mozzarella Sticks",
        "8.99",
    ),
    (
        "BUFFALO CHICKEN Hot, Mild, BBQ Honey BBQ",
        "BUFFALO CHICKEN",
        "Mild",
    ),
    (
        "Ricotta, Parmesan, Mozzarella, Provolone",
        "Ricotta",
        "Provolone",
    ),
    (
        "GRILLED CHICKEN PIZZA .....essssssscsssssssssssssessooenssnveneessce 17.95 ......... 25.50... 34.75",
        "GRILLED CHICKEN PIZZA",
        "17.95",
    ),
    (
        "HONEY BBQ BACON CHEDDAR PIZZA .......esccssscsssssees 17.95......... 25.50......... 34,75",
        "HONEY BBQ BACON CHEDDAR PIZZA",
        "17.95",
    ),
]


# ── Test Group 2: ALL CAPS + Mixed-Case Split ──────

CAPS_SPLIT_TESTS = [
    # (input_after_cleaning, expected_line_type, expected_name, expected_desc_contains)
    ("MEAT LOVERS Pepperoni, Sausage, Bacon, Ham & Hamburger", "menu_item", "MEAT LOVERS", "Pepperoni"),
    ("BUFFALO CHICKEN Hot, Mild, BBQ Honey BBQ", "menu_item", "BUFFALO CHICKEN", "Hot"),
    ("ALFREDO PIZZA Broccoli & Chicken w/ Alfredo Sauce", "menu_item", "ALFREDO PIZZA", "Broccoli"),
    ("PESTO CHICKEN Grilled Chicken, Pesto Sauce, Tomato", "menu_item", "PESTO CHICKEN", "Grilled"),
    ("GYRO SPECIAL Gyro Meat, Tomatoes, Onions, Feta Cheese", "menu_item", "GYRO SPECIAL", "Gyro Meat"),
    ("PHILLY STEAK Steak, Onions, Peppers & Mushrooms", "menu_item", "PHILLY STEAK", "Steak"),
    ("POLLO CHICKEN Chicken, Broccoli, Tomatoes & Basil", "menu_item", "POLLO CHICKEN", "Chicken"),
    ("BURGER lettuce, tomato, mayo", "menu_item", "BURGER", "lettuce"),
    # These should NOT split — all caps, no mixed-case continuation
    ("GOURMET PIZZA", "heading", "GOURMET PIZZA", ""),
    ("APPETIZERS", "heading", "APPETIZERS", ""),
]


# ── Test Group 3: Size Header Detection ─────────────

SIZE_HEADER_TESTS = [
    # (input, expected_line_type)
    ('10"Mini 12" Sml 16"lrg Family Size', "size_header"),
    ("8 Slices 12 Slices 24 Slices", "size_header"),
    ('12" Sml 16"lrg Family Size', "size_header"),
    ("Cheese Pizza 12.99", "menu_item"),   # NOT a size header
    ("APPETIZERS", "heading"),             # NOT a size header
]


# ── Test Group 4: Topping / Info Line Detection ─────

TOPPING_INFO_TESTS = [
    # (input, expected_line_type)
    ("PIZZA & CALZONE TOPPINGS", "topping_list"),
    ("MEAT TOPPINGS: Pepperoni -Chicken - Bacon - Hamburger -Sausage - Meatball", "topping_list"),
    ("VEGGIE TOPPINGS: Spinach - Onions - Hot Peppers - Banana Peppers", "topping_list"),
    ("Choice of Sauce; Red, White, Pesto or Alfredo, Garlic Sauce, Ranch Sauce or Blue Cheese", "info_line"),
    ("All calzones stuffed with ricotta and mozzarella.", "info_line"),
    ("All club sandwiches come with lettuce, tomato,", "info_line"),
    ("Cheese Pizza 12.99", "menu_item"),   # NOT an info line
]


# ── Test Group 5: Price-Only Line Detection ─────────

PRICE_ONLY_TESTS = [
    # (input, expected_line_type, expected_price)
    (". 34.75", "price_only", 34.75),
    ("-- $4.75", "price_only", 4.75),
    ("34.75", "price_only", 34.75),
    (" 34.75", "price_only", 34.75),
    (". 34,75", "price_only", 34.75),
    ("» 34,75", "price_only", 34.75),
    ("Cheese Pizza 12.99", "menu_item", None),   # NOT price-only
    ("APPETIZERS", "heading", None),              # NOT price-only
]


# ── Test Group 6: Multi-Price Handling ──────────────

MULTI_PRICE_TESTS = [
    # (input, expected_name_contains, expected_price_count)
    ("CHEESE 8.00 11.50 13.95 22.50", "CHEESE", 4),
    ("HONEY BBQ BACON CHEDDAR PIZZA 17.95 25.50 34.75", "HONEY BBQ BACON CHEDDAR PIZZA", 3),
    ("Margherita 12.99", "Margherita", 1),
    ("STEAK DELIGHT PIZZA 17.95 25.50 34.75", "STEAK DELIGHT PIZZA", 3),
    ("BURGER PIZZA 17.95 25.50 34.75", "BURGER PIZZA", 3),
]


# ── Test Group 7: Baseline Grammar Regression ───────
# Same cases from test_phase8_baseline.py — must not regress

BASELINE_GRAMMAR_TESTS = [
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
    ("2 Liter Soda 3.99", "menu_item", ""),
    ("Chocolate Brownie 4.99", "menu_item", "Chocolate Brownie"),
    ("BEVERAGES", "heading", "BEVERAGES"),
    ("PASTA", "heading", "PASTA"),
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


def run_garble_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 1: OCR GARBLE STRIPPING")
    print("=" * 60)

    for text, expected_kept, expected_preserved in GARBLE_STRIP_TESTS:
        cleaned = _strip_ocr_garble(text)
        kept_ok = expected_kept in cleaned
        preserved_ok = expected_preserved in cleaned
        report.check(
            kept_ok and preserved_ok,
            f"{text[:50]!r} -> {cleaned[:60]!r} (want {expected_kept!r} and {expected_preserved!r})"
        )

    pct = (report.passed / max(report.total, 1)) * 100
    print(f"Garble: {report.passed}/{report.total} ({pct:.0f}%)")


def run_caps_split_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 2: ALL CAPS + MIXED-CASE SPLIT")
    print("=" * 60)

    for text, expected_type, expected_name, expected_desc in CAPS_SPLIT_TESTS:
        result = parse_menu_line(text)
        type_ok = result.line_type == expected_type
        name_ok = expected_name == "" or expected_name.lower() in result.item_name.lower()
        desc_ok = expected_desc == "" or expected_desc.lower() in result.description.lower()
        report.check(
            type_ok and name_ok and desc_ok,
            f"{text[:50]!r} -> type={result.line_type}(exp {expected_type}), "
            f"name={result.item_name[:30]!r}(exp {expected_name!r}), "
            f"desc={result.description[:30]!r}(exp {expected_desc!r})"
        )


def run_size_header_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 3: SIZE HEADER DETECTION")
    print("=" * 60)

    for text, expected_type in SIZE_HEADER_TESTS:
        result = parse_menu_line(text)
        report.check(
            result.line_type == expected_type,
            f"{text[:50]!r} -> {result.line_type} (exp {expected_type})"
        )


def run_topping_info_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 4: TOPPING / INFO LINE DETECTION")
    print("=" * 60)

    for text, expected_type in TOPPING_INFO_TESTS:
        result = parse_menu_line(text)
        report.check(
            result.line_type == expected_type,
            f"{text[:60]!r} -> {result.line_type} (exp {expected_type})"
        )


def run_price_only_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 5: PRICE-ONLY LINE DETECTION")
    print("=" * 60)

    for text, expected_type, expected_price in PRICE_ONLY_TESTS:
        result = parse_menu_line(text)
        type_ok = result.line_type == expected_type
        price_ok = True
        if expected_price is not None:
            price_ok = len(result.price_mentions) > 0 and abs(result.price_mentions[0] - expected_price) < 0.01
        report.check(
            type_ok and price_ok,
            f"{text!r} -> type={result.line_type}(exp {expected_type}), "
            f"prices={result.price_mentions}(exp {expected_price})"
        )


def run_multi_price_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 6: MULTI-PRICE HANDLING")
    print("=" * 60)

    for text, expected_name, expected_count in MULTI_PRICE_TESTS:
        result = parse_menu_line(text)
        name_ok = expected_name.lower() in result.item_name.lower()
        count_ok = len(result.price_mentions) == expected_count
        report.check(
            name_ok and count_ok,
            f"{text[:50]!r} -> name={result.item_name[:30]!r}(exp {expected_name!r}), "
            f"prices={len(result.price_mentions)}(exp {expected_count})"
        )


def run_baseline_regression(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 7: BASELINE GRAMMAR REGRESSION")
    print("=" * 60)

    for text, expected_type, expected_name in BASELINE_GRAMMAR_TESTS:
        result = parse_menu_line(text)
        type_ok = result.line_type == expected_type
        name_ok = expected_name == "" or expected_name.lower() in result.item_name.lower()
        report.check(
            type_ok and name_ok,
            f"{text[:50]!r} -> type={result.line_type}(exp {expected_type}), "
            f"name={result.item_name[:30]!r}(exp {expected_name!r})"
        )


def run_real_ocr_accuracy(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 8: FULL REAL OCR ACCURACY")
    print("=" * 60)

    ocr_path = Path(__file__).parent.parent / "fixtures" / "sample_menus" / "pizza_real_p01.ocr_used_psm3.txt"
    if not ocr_path.exists():
        print(f"  SKIP: {ocr_path} not found")
        return

    lines = ocr_path.read_text(encoding="utf-8").splitlines()

    stats = {
        "total_lines": 0,
        "non_empty": 0,
        "classified": 0,
        "menu_item": 0,
        "heading": 0,
        "size_header": 0,
        "topping_list": 0,
        "info_line": 0,
        "price_only": 0,
        "description_only": 0,
        "modifier_line": 0,
        "unknown": 0,
        "with_prices": 0,
        "with_name": 0,
    }

    for line in lines:
        stats["total_lines"] += 1
        if not line.strip():
            continue
        stats["non_empty"] += 1

        result = parse_menu_line(line)
        if result.line_type != "unknown":
            stats["classified"] += 1
        stats[result.line_type] = stats.get(result.line_type, 0) + 1
        if result.price_mentions:
            stats["with_prices"] += 1
        if result.item_name and result.line_type == "menu_item":
            stats["with_name"] += 1

    classification_rate = stats["classified"] / max(stats["non_empty"], 1)

    print(f"\n  [Real OCR Accuracy Report]")
    for k, v in stats.items():
        print(f"    {k}: {v}")
    print(f"    classification_rate: {classification_rate:.1%}")

    # Target: 75% classification rate
    report.check(
        classification_rate >= 0.75,
        f"Classification rate {classification_rate:.1%} < 75% target"
    )
    print(f"\n  Target: >= 75% classification | Actual: {classification_rate:.1%}")


def main():
    report = TestReport()

    run_garble_tests(report)
    run_caps_split_tests(report)
    run_size_header_tests(report)
    run_topping_info_tests(report)
    run_price_only_tests(report)
    run_multi_price_tests(report)
    run_baseline_regression(report)
    run_real_ocr_accuracy(report)

    print("\n\n" + "=" * 60)
    print("DAY 52 PIZZA GRAMMAR RESULTS")
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
