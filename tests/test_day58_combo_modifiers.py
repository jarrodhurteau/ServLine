# tests/test_day58_combo_modifiers.py
"""
Day 58: Sprint 8.2 — Combo Modifier Detection & Variant Labeling

Tests:
  1. Combo vocabulary (is_combo_food, extract_combo_hints)
  2. Grammar normalization (_normalize_w_slash with WIFRIES patterns)
  3. Grammar combo_hints extraction via parse_menu_line
  4. Variant kind detection (kind="combo" in variant_engine)
  5. Variant label building (_build_variants_from_text with combo patterns)
  6. Variant enrichment (_enrich_variant with kind_hint)
  7. Integration tests (full pipeline on real menu lines)
  8. Edge cases & regression guards

Run: python tests/test_day58_combo_modifiers.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.parsers.combo_vocab import (
    COMBO_FOODS,
    is_combo_food,
    extract_combo_hints,
)
from storage.parsers.menu_grammar import (
    _normalize_w_slash,
    parse_menu_line,
    enrich_grammar_on_text_blocks,
)
from storage.variant_engine import (
    _infer_variant_kind_and_normalized_size,
    _build_group_key,
    _enrich_variant,
    enrich_variants_on_text_blocks,
)
from storage.ocr_pipeline import (
    _build_variants_from_text,
    _find_price_candidates_with_positions,
    annotate_prices_and_variants_on_text_blocks,
)


# ==============================================================
# Group 1: Combo Vocabulary
# ==============================================================

# (token, expected)
IS_COMBO_FOOD_TESTS = [
    # Positive — fried sides
    ("fries", True),
    ("french fries", True),
    ("curly fries", True),
    ("waffle fries", True),
    ("sweet potato fries", True),
    ("steak fries", True),
    ("seasoned fries", True),
    ("onion rings", True),
    ("tater tots", True),
    ("tots", True),
    # Positive — cheese / drinks / soup
    ("cheese", True),
    ("extra cheese", True),
    ("drink", True),
    ("soda", True),
    ("beverage", True),
    ("soup", True),
    ("side soup", True),
    # Positive — salads & slaws
    ("coleslaw", True),
    ("cole slaw", True),
    ("slaw", True),
    ("side salad", True),
    ("garden salad", True),
    # Positive — carbs
    ("rice", True),
    ("mashed potatoes", True),
    ("baked potato", True),
    # Positive — bread
    ("garlic bread", True),
    ("breadsticks", True),
    # Negative — toppings (should NOT match)
    ("pepperoni", False),
    ("sausage", False),
    ("mushrooms", False),
    ("olives", False),
    ("chicken", False),
    ("bacon", False),
    # Negative — categories / generic words
    ("pizza", False),
    ("burger", False),
    ("wings", False),
    ("sandwich", False),
    ("calzone", False),
    ("small", False),
    ("large", False),
    ("hot", False),
    ("bbq", False),
]

EXTRACT_HINTS_TESTS = [
    # (text, expected_hints)
    ("9.95 with FRIES 13.50", ["fries"]),
    ("5.00 w/ fries 17.95", ["fries"]),
    ("with Cheese 8.95", ["cheese"]),
    ("W/ Drink included", ["drink"]),
    ("w/ coleslaw and w/ fries", ["coleslaw", "fries"]),
    ("W/ ONION RINGS 12.99", ["onion rings"]),
    # Negative
    ("9.95 13.50", []),
    ("pepperoni pizza 12.99", []),
    ("with pepperoni", []),  # pepperoni not a combo food
    ("plain text no prices", []),
    ("W/ chicken", []),  # chicken not a combo food
]


# ==============================================================
# Group 2: Grammar Normalization
# ==============================================================

NORMALIZE_W_SLASH_TESTS = [
    # (input, expected_contains)  — we check substring inclusion since
    # full normalization may add spaces differently
    # Standard patterns (existing)
    ("W/ FRIES", "with FRIES"),
    ("w/ fries", "with fries"),
    ("Wi CHEESE", "with CHEESE"),
    ("Wi Bread", "with Bread"),
    # Day 58: no-space patterns
    ("WIFRIES", "with FRIES"),
    ("WICHEESE", "with CHEESE"),
    ("WiSoda", "with Soda"),
    ("WIDRINK", "with DRINK"),
    ("WiSoup", "with Soup"),
    ("WiChips", "with Chips"),
    # Day 58 fix: WI/ pattern (OCR reads W/ as WI/)
    ("WI/FRIES", "with FRIES"),
    ("WI/CHEESE", "with CHEESE"),
    ("wi/fries", "with fries"),
    ("9.95 WI/FRIES 13.50", "with FRIES"),
    # Should NOT change non-combo words
    ("WICHITA", "WICHITA"),  # city name, not combo
    ("WINTER", "WINTER"),  # not a WI+food pattern
    ("WIDE", "WIDE"),
    ("WINE", "WINE"),
    # Multi-pattern in one line
    ("9.95 WIFRIES 13.50", "with FRIES"),
    # Preserve everything else
    ("Small Pizza", "Small Pizza"),
    ('10" Large', '10" Large'),
    ("Pepperoni 12.99", "Pepperoni 12.99"),
]


# ==============================================================
# Group 3: Grammar combo_hints via parse_menu_line
# ==============================================================

PARSE_COMBO_HINTS_TESTS = [
    # (line_text, expected_hints)
    # After normalization: "with FRIES" triggers combo hint
    ("9.95 W/FRIES 13.50", ["fries"]),
    ("5.00 W/FRIES 17.95", ["fries"]),
    ("14.95 WIFRIES 16.95", ["fries"]),
    ("CURLY FRIES 6.00 Wi CHEESE 8.95", ["cheese"]),
    ("5 PCS CHICKEN TENDERS W/ FRENCH FRIES 14.95", ["french fries"]),
    ("W/ ONION RINGS 12.99", ["onion rings"]),
    ("9.95 WI/FRIES 13.50", ["fries"]),  # OCR WI/ pattern
    # No combo hints
    ("Pepperoni Pizza 12.99", []),
    ("Small 9.99 Large 15.99", []),
    ("BUFFALO CHICKEN Hot Mild BBQ 9.95", []),
]


# ==============================================================
# Group 4: Variant Kind Detection
# ==============================================================

VARIANT_KIND_TESTS = [
    # (label, expected_kind, expected_norm_size)
    # Combo variants
    ("W/Fries", "combo", None),
    ("w/fries", "combo", None),
    ("with Cheese", "combo", None),
    ("W/Drink", "combo", None),
    ("W/Onion Rings", "combo", None),
    ("W/Coleslaw", "combo", None),
    ("W/Soda", "combo", None),
    ("W/Soup", "combo", None),
    ("W/Rice", "combo", None),
    ("W/Chips", "combo", None),
    # Standalone combo foods as labels
    ("Fries", "combo", None),
    ("Cheese", "combo", None),
    ("Onion Rings", "combo", None),
    ("Coleslaw", "combo", None),
    ("Soda", "combo", None),
    ("Soup", "combo", None),
    # Size variants (regression — must still work)
    ("Small", "size", "S"),
    ("Medium", "size", "M"),
    ("Large", "size", "L"),
    ("XL", "size", "XL"),
    ('10"', "size", "10in"),
    ('16"', "size", "16in"),
    ("6pc", "size", "6pc"),
    ("12 pcs", "size", "12pc"),
    ("Family", "size", "Family"),
    ("Personal", "size", "Personal"),
    # Flavor variants (regression)
    ("Hot", "flavor", None),
    ("BBQ", "flavor", None),
    ("Mild", "flavor", None),
    ("Honey Mustard", "flavor", None),
    # Style variants (regression)
    ("Boneless", "style", None),
    ("Bone-in", "style", None),
    ("Thin Crust", "style", None),
    ("Deep Dish", "style", None),
    # Other / existing substring matches
    ("Pepperoni", "flavor", None),  # "pepper" in _FLAVOR_TOKENS
    ("Chicken Parm", "flavor", None),  # "parm" in _FLAVOR_TOKENS
    ("Deluxe Special", "size", "Deluxe"),  # "deluxe" in SIZE_WORD_MAP
]


# ==============================================================
# Group 5: Variant Label Building
# ==============================================================

# Each: (text, grammar_combo_hints, expected_variant_tuples)
# expected_variant_tuples: [(label, kind_hint_or_None, price_cents)]
VARIANT_BUILD_TESTS = [
    # Basic inline combo pricing
    (
        "9.95 with FRIES 13.50",
        ["fries"],
        [("", None, 995), ("W/Fries", "combo", 1350)],
    ),
    (
        "5.00 with FRIES 17.95",
        ["fries"],
        [("", None, 500), ("W/Fries", "combo", 1795)],
    ),
    # No-space normalized: "WIFRIES" -> "with FRIES" before variant building
    (
        "14.95 with FRIES 16.95",
        ["fries"],
        [("", None, 1495), ("W/Fries", "combo", 1695)],
    ),
    # Cheese add-on
    (
        "CURLY FRIES 6.00 with CHEESE 8.95",
        ["cheese"],
        [("FRIES", "combo", 600), ("W/Cheese", "combo", 895)],
    ),
    # Size variants (regression: must still work)
    # Note: backward walk collects up to 2 tokens before each price,
    # so second price gets "Small Large" label. This is existing behavior.
    (
        "Small 9.99 Large 15.99",
        [],
        [("Small", None, 999), ("Small Large", None, 1599)],
    ),
    # Three prices with combo
    (
        "6.00 with FRIES 9.00 with CHEESE 10.50",
        ["fries", "cheese"],
        [("", None, 600), ("W/Fries", "combo", 900), ("W/Cheese", "combo", 1050)],
    ),
    # Combo food without "with" prefix but with grammar hints
    (
        "WINGS 9.95 FRIES 13.50",
        ["fries"],
        [("WINGS", None, 995), ("W/Fries", "combo", 1350)],
    ),
]


# ==============================================================
# Group 6: Variant Enrichment (_enrich_variant with kind_hint)
# ==============================================================

ENRICH_VARIANT_TESTS = [
    # (variant_dict_before, expected_kind_after)
    # Combo with W/ label
    ({"label": "W/Fries", "price_cents": 1350, "confidence": 0.9}, "combo"),
    ({"label": "W/Cheese", "price_cents": 895, "confidence": 0.9}, "combo"),
    # Combo via kind_hint override
    ({"label": "FRIES", "price_cents": 1350, "confidence": 0.9, "kind_hint": "combo"}, "combo"),
    # Standalone food auto-detected
    ({"label": "Fries", "price_cents": 1350, "confidence": 0.9}, "combo"),
    ({"label": "Cheese", "price_cents": 895, "confidence": 0.9}, "combo"),
    # Empty label with kind_hint
    ({"label": "", "price_cents": 995, "confidence": 0.9, "kind_hint": "combo"}, "combo"),
    # Empty label without kind_hint -> other
    ({"label": "", "price_cents": 995, "confidence": 0.9}, "other"),
    # Size labels (regression)
    ({"label": "Small", "price_cents": 999, "confidence": 0.9}, "size"),
    ({"label": "Large", "price_cents": 1599, "confidence": 0.9}, "size"),
    ({"label": '10"', "price_cents": 1099, "confidence": 0.9}, "size"),
    # Flavor labels (regression)
    ({"label": "Hot", "price_cents": 999, "confidence": 0.9}, "flavor"),
    ({"label": "BBQ", "price_cents": 999, "confidence": 0.9}, "flavor"),
    # Style labels (regression)
    ({"label": "Boneless", "price_cents": 999, "confidence": 0.9}, "style"),
]

GROUP_KEY_TESTS = [
    # (kind, label, normalized_size, expected_key)
    ("combo", "W/Fries", None, "combo:w/fries"),
    ("combo", "W/Cheese", None, "combo:w/cheese"),
    ("combo", "Fries", None, "combo:fries"),
    ("size", "Small", "S", "size:S"),
    ("flavor", "Hot", None, "flavor:hot"),
    ("style", "Boneless", None, "style:boneless"),
    ("other", "Mystery", None, None),
]


# ==============================================================
# Group 7: Integration Tests
# ==============================================================

def _make_tb(text, grammar=None, variants=None):
    """Helper to create a text_block dict."""
    tb = {"text": text, "merged_text": text}
    if grammar:
        tb["grammar"] = grammar
    if variants:
        tb["variants"] = variants
    return tb


INTEGRATION_LINES = [
    # Real-ish menu lines -> expected combo variant count after full pipeline
    # Line with combo pricing
    {
        "text": "9.95 with FRIES 13.50",
        "min_variants": 2,
        "expect_combo": True,
    },
    # Size variant line (regression)
    {
        "text": "Small 9.99 Medium 12.99 Large 15.99",
        "min_variants": 3,
        "expect_combo": False,
    },
    # Single price, no variants
    {
        "text": "Pepperoni Pizza 12.99",
        "min_variants": 0,
        "expect_combo": False,
    },
    # Cheese add-on column
    {
        "text": "FRENCH FRIES 6.00 with CHEESE 8.95",
        "min_variants": 2,
        "expect_combo": True,
    },
]


# ==============================================================
# Group 8: Edge Cases
# ==============================================================

EDGE_CASE_TESTS = [
    # Empty / missing
    ("", [], []),
    # Single price — no variants
    ("Cheese Pizza 12.99", [], []),
    # "with" without food — incomplete, should not create combo
    ("9.95 with 13.50", [], 2),  # 2 variants, but neither is combo
    # OCR garble shouldn't break
    ("...  9.95 with FRIES   13.50 ...", ["fries"], 2),
]


# ==============================================================
# Helpers
# ==============================================================

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


# ==============================================================
# Test Runners
# ==============================================================

def run_combo_vocab_tests(report: TestReport):
    """Group 1: Combo vocabulary tests."""
    print("\n--Group 1: Combo Vocabulary --")

    for token, expected in IS_COMBO_FOOD_TESTS:
        result = is_combo_food(token)
        report.check(
            result == expected,
            f"is_combo_food('{token}') = {result}, expected {expected}",
        )

    for text, expected_hints in EXTRACT_HINTS_TESTS:
        hints = extract_combo_hints(text)
        report.check(
            hints == expected_hints,
            f"extract_combo_hints('{text}') = {hints}, expected {expected_hints}",
        )

    # Verify vocabulary size is reasonable
    report.check(
        len(COMBO_FOODS) >= 25,
        f"COMBO_FOODS has {len(COMBO_FOODS)} entries, expected >= 25",
    )

    print(f"  Group 1: {report.total} tests checked")


def run_normalization_tests(report: TestReport):
    """Group 2: Grammar normalization tests."""
    print("\n--Group 2: Grammar Normalization --")

    for input_text, expected_substr in NORMALIZE_W_SLASH_TESTS:
        result = _normalize_w_slash(input_text)
        report.check(
            expected_substr in result,
            f"_normalize_w_slash('{input_text}') = '{result}', "
            f"expected to contain '{expected_substr}'",
        )

    print(f"  Group 2: {report.total} tests checked")


def run_grammar_combo_hints_tests(report: TestReport):
    """Group 3: Grammar combo_hints extraction."""
    print("\n--Group 3: Grammar combo_hints --")

    for line_text, expected_hints in PARSE_COMBO_HINTS_TESTS:
        parsed = parse_menu_line(line_text)
        report.check(
            parsed.combo_hints == expected_hints,
            f"parse_menu_line('{line_text}').combo_hints = {parsed.combo_hints}, "
            f"expected {expected_hints}",
        )

    print(f"  Group 3: {report.total} tests checked")


def run_variant_kind_tests(report: TestReport):
    """Group 4: Variant kind detection."""
    print("\n--Group 4: Variant Kind Detection --")

    for label, expected_kind, expected_norm in VARIANT_KIND_TESTS:
        kind, norm = _infer_variant_kind_and_normalized_size(label)
        report.check(
            kind == expected_kind,
            f"kind('{label}') = '{kind}', expected '{expected_kind}'",
        )
        report.check(
            norm == expected_norm,
            f"norm_size('{label}') = {norm}, expected {expected_norm}",
        )

    print(f"  Group 4: {report.total} tests checked")


def run_variant_build_tests(report: TestReport):
    """Group 5: Variant label building."""
    print("\n--Group 5: Variant Label Building --")

    for text, combo_hints, expected in VARIANT_BUILD_TESTS:
        priced = _find_price_candidates_with_positions(text)
        grammar = {"combo_hints": combo_hints} if combo_hints else None
        variants = _build_variants_from_text(text, priced, grammar)

        report.check(
            len(variants) == len(expected),
            f"'{text}': got {len(variants)} variants, expected {len(expected)}",
        )
        if len(variants) != len(expected):
            continue

        for i, (exp_label, exp_hint, exp_price) in enumerate(expected):
            v = variants[i]
            report.check(
                v["label"] == exp_label,
                f"'{text}' v[{i}].label = '{v['label']}', expected '{exp_label}'",
            )
            actual_hint = v.get("kind_hint")
            report.check(
                actual_hint == exp_hint,
                f"'{text}' v[{i}].kind_hint = {actual_hint}, expected {exp_hint}",
            )
            report.check(
                v["price_cents"] == exp_price,
                f"'{text}' v[{i}].price_cents = {v['price_cents']}, expected {exp_price}",
            )

    print(f"  Group 5: {report.total} tests checked")


def run_enrich_variant_tests(report: TestReport):
    """Group 6: Variant enrichment."""
    print("\n--Group 6: Variant Enrichment --")

    for variant_in, expected_kind in ENRICH_VARIANT_TESTS:
        v = dict(variant_in)  # copy
        _enrich_variant(v)
        report.check(
            v["kind"] == expected_kind,
            f"_enrich_variant({variant_in}) -> kind='{v['kind']}', expected '{expected_kind}'",
        )

    for kind, label, norm_size, expected_key in GROUP_KEY_TESTS:
        key = _build_group_key(kind, label, norm_size)
        report.check(
            key == expected_key,
            f"_build_group_key('{kind}', '{label}', {norm_size}) = {key}, expected {expected_key}",
        )

    print(f"  Group 6: {report.total} tests checked")


def run_integration_tests(report: TestReport):
    """Group 7: Full pipeline integration."""
    print("\n--Group 7: Integration Tests --")

    for tc in INTEGRATION_LINES:
        text = tc["text"]
        # Build text block and run through pipeline steps
        tbs = [_make_tb(text)]

        # Step 6.5: Grammar enrichment
        enrich_grammar_on_text_blocks(tbs)

        # Step 7: Price + variant annotation
        annotate_prices_and_variants_on_text_blocks(tbs)

        # Step 8: Variant enrichment
        enrich_variants_on_text_blocks(tbs)

        tb = tbs[0]
        variants = tb.get("variants", [])
        n = len(variants)
        min_v = tc["min_variants"]

        report.check(
            n >= min_v,
            f"'{text}': {n} variants, expected >= {min_v}",
        )

        if tc["expect_combo"]:
            has_combo = any(v.get("kind") == "combo" for v in variants)
            report.check(
                has_combo,
                f"'{text}': expected at least one combo variant, "
                f"got kinds={[v.get('kind') for v in variants]}",
            )
        else:
            no_combo = all(v.get("kind") != "combo" for v in variants)
            report.check(
                no_combo,
                f"'{text}': expected no combo variants, "
                f"got kinds={[v.get('kind') for v in variants]}",
            )

    # Combo variant group_key integration
    tbs = [_make_tb("6.00 with FRIES 9.00")]
    enrich_grammar_on_text_blocks(tbs)
    annotate_prices_and_variants_on_text_blocks(tbs)
    enrich_variants_on_text_blocks(tbs)
    variants = tbs[0].get("variants", [])
    combo_vs = [v for v in variants if v.get("kind") == "combo"]
    for v in combo_vs:
        report.check(
            v.get("group_key", "").startswith("combo:"),
            f"combo variant '{v.get('label')}' group_key = '{v.get('group_key')}', "
            f"expected 'combo:...'",
        )

    # Grammar enrichment combo_hints propagation
    tbs = [_make_tb("9.95 W/FRIES 13.50")]
    enrich_grammar_on_text_blocks(tbs)
    grammar = tbs[0].get("grammar", {})
    report.check(
        "combo_hints" in grammar,
        f"Grammar should have combo_hints key after enrichment",
    )
    report.check(
        grammar.get("combo_hints") == ["fries"],
        f"Grammar combo_hints = {grammar.get('combo_hints')}, expected ['fries']",
    )

    # Regression: size grid patterns still produce size variants
    tbs = [_make_tb("Small 9.99 Medium 12.99 Large 15.99")]
    enrich_grammar_on_text_blocks(tbs)
    annotate_prices_and_variants_on_text_blocks(tbs)
    enrich_variants_on_text_blocks(tbs)
    variants = tbs[0].get("variants", [])
    size_vs = [v for v in variants if v.get("kind") == "size"]
    report.check(
        len(size_vs) >= 2,
        f"Size regression: expected >= 2 size variants, got {len(size_vs)}",
    )

    # Regression: flavor variants
    tbs = [_make_tb("Hot 8.99 BBQ 8.99")]
    enrich_grammar_on_text_blocks(tbs)
    annotate_prices_and_variants_on_text_blocks(tbs)
    enrich_variants_on_text_blocks(tbs)
    variants = tbs[0].get("variants", [])
    flavor_vs = [v for v in variants if v.get("kind") == "flavor"]
    report.check(
        len(flavor_vs) >= 1,
        f"Flavor regression: expected >= 1 flavor variant, got {len(flavor_vs)}",
    )

    # Multiple combos in one line
    tbs = [_make_tb("6.00 with FRIES 9.00 with CHEESE 10.50")]
    enrich_grammar_on_text_blocks(tbs)
    annotate_prices_and_variants_on_text_blocks(tbs)
    enrich_variants_on_text_blocks(tbs)
    variants = tbs[0].get("variants", [])
    combo_vs = [v for v in variants if v.get("kind") == "combo"]
    report.check(
        len(combo_vs) >= 2,
        f"Multi-combo: expected >= 2 combo variants, got {len(combo_vs)}",
    )

    # WIFRIES normalization through full pipeline
    tbs = [_make_tb("14.95 WIFRIES 16.95")]
    enrich_grammar_on_text_blocks(tbs)
    annotate_prices_and_variants_on_text_blocks(tbs)
    enrich_variants_on_text_blocks(tbs)
    variants = tbs[0].get("variants", [])
    report.check(
        len(variants) >= 2,
        f"WIFRIES pipeline: expected >= 2 variants, got {len(variants)}",
    )
    combo_vs = [v for v in variants if v.get("kind") == "combo"]
    report.check(
        len(combo_vs) >= 1,
        f"WIFRIES pipeline: expected >= 1 combo variant, got {len(combo_vs)}",
    )

    # Wi CHEESE normalization through full pipeline
    tbs = [_make_tb("CURLY FRIES 6.00 Wi CHEESE 8.95")]
    enrich_grammar_on_text_blocks(tbs)
    annotate_prices_and_variants_on_text_blocks(tbs)
    enrich_variants_on_text_blocks(tbs)
    variants = tbs[0].get("variants", [])
    report.check(
        len(variants) >= 2,
        f"Wi CHEESE pipeline: expected >= 2 variants, got {len(variants)}",
    )
    combo_vs = [v for v in variants if v.get("kind") == "combo"]
    report.check(
        len(combo_vs) >= 1,
        f"Wi CHEESE pipeline: expected >= 1 combo variant, got {len(combo_vs)}",
    )

    # WI/FRIES (slash between WI and food) — real OCR fixture pattern
    tbs = [_make_tb("9.95 WI/FRIES 13.50")]
    enrich_grammar_on_text_blocks(tbs)
    annotate_prices_and_variants_on_text_blocks(tbs)
    enrich_variants_on_text_blocks(tbs)
    variants = tbs[0].get("variants", [])
    report.check(
        len(variants) >= 2,
        f"WI/FRIES pipeline: expected >= 2 variants, got {len(variants)}",
    )
    combo_vs = [v for v in variants if v.get("kind") == "combo"]
    report.check(
        len(combo_vs) >= 1,
        f"WI/FRIES pipeline: expected >= 1 combo variant, got {len(combo_vs)}",
    )

    # Price ordering: base < combo (basic sanity)
    tbs = [_make_tb("9.95 with FRIES 13.50")]
    enrich_grammar_on_text_blocks(tbs)
    annotate_prices_and_variants_on_text_blocks(tbs)
    enrich_variants_on_text_blocks(tbs)
    variants = tbs[0].get("variants", [])
    if len(variants) >= 2:
        p0 = variants[0].get("price_cents", 0)
        p1 = variants[1].get("price_cents", 0)
        report.check(
            p0 < p1,
            f"Combo pricing: base {p0} should be < combo {p1}",
        )

    print(f"  Group 7: {report.total} tests checked")


def run_edge_case_tests(report: TestReport):
    """Group 8: Edge cases."""
    print("\n--Group 8: Edge Cases --")

    # Empty text
    priced = _find_price_candidates_with_positions("")
    variants = _build_variants_from_text("", priced)
    report.check(len(variants) == 0, "Empty text -> 0 variants")

    # Single price — no variants
    text = "Cheese Pizza 12.99"
    priced = _find_price_candidates_with_positions(text)
    variants = _build_variants_from_text(text, priced)
    report.check(len(variants) == 0, "Single price -> 0 variants")

    # "with" but no food after it
    text = "9.95 with 13.50"
    priced = _find_price_candidates_with_positions(text)
    variants = _build_variants_from_text(text, priced)
    # Should produce 2 variants but none with kind_hint="combo"
    if len(variants) >= 2:
        combo_count = sum(1 for v in variants if v.get("kind_hint") == "combo")
        report.check(
            combo_count == 0,
            f"'with' without food: expected 0 combo kind_hints, got {combo_count}",
        )

    # OCR garble around combo
    text = "...  9.95 with FRIES   13.50 ..."
    priced = _find_price_candidates_with_positions(text)
    variants = _build_variants_from_text(text, priced, {"combo_hints": ["fries"]})
    report.check(
        len(variants) >= 2,
        f"Garbled combo: expected >= 2 variants, got {len(variants)}",
    )
    if variants:
        combo_count = sum(1 for v in variants if v.get("kind_hint") == "combo")
        report.check(
            combo_count >= 1,
            f"Garbled combo: expected >= 1 combo, got {combo_count}",
        )

    # Normalize preserves non-WI words
    for word in ["WICHITA", "WINTER", "WIDE", "WINE", "WISH"]:
        result = _normalize_w_slash(word)
        report.check(
            word.lower() in result.lower() or "with" not in result.lower(),
            f"_normalize_w_slash('{word}') should NOT convert to 'with ...'",
        )

    # Combo food is case-insensitive
    report.check(is_combo_food("FRIES"), "is_combo_food('FRIES') case-insensitive")
    report.check(is_combo_food("Fries"), "is_combo_food('Fries') case-insensitive")
    report.check(is_combo_food("fRiEs"), "is_combo_food('fRiEs') case-insensitive")
    report.check(is_combo_food("  fries  "), "is_combo_food('  fries  ') with spaces")

    # is_combo_food rejects empty / whitespace
    report.check(not is_combo_food(""), "is_combo_food('') -> False")
    report.check(not is_combo_food("   "), "is_combo_food('   ') -> False")

    # extract_combo_hints with empty text
    report.check(
        extract_combo_hints("") == [],
        "extract_combo_hints('') -> []",
    )

    # Kind inference for edge-case labels
    kind, _ = _infer_variant_kind_and_normalized_size("")
    report.check(kind is not None, "Empty label kind should not be None")

    kind, _ = _infer_variant_kind_and_normalized_size("W/")
    report.check(kind == "other", f"'W/' alone -> kind='other', got '{kind}'")

    kind, _ = _infer_variant_kind_and_normalized_size("with")
    report.check(kind == "other", f"'with' alone -> kind='other', got '{kind}'")

    # Variant with no label and kind_hint
    v = {"label": "", "price_cents": 995, "confidence": 0.9, "kind_hint": "combo"}
    _enrich_variant(v)
    report.check(v["kind"] == "combo", "Empty label + kind_hint='combo' -> kind='combo'")

    # Variant with no label and no kind_hint
    v = {"label": "", "price_cents": 995, "confidence": 0.9}
    _enrich_variant(v)
    report.check(v["kind"] == "other", "Empty label, no hint -> kind='other'")

    print(f"  Group 8: {report.total} tests checked")


# ==============================================================
# Main
# ==============================================================

def main():
    report = TestReport()

    run_combo_vocab_tests(report)
    run_normalization_tests(report)
    run_grammar_combo_hints_tests(report)
    run_variant_kind_tests(report)
    run_variant_build_tests(report)
    run_enrich_variant_tests(report)
    run_integration_tests(report)
    run_edge_case_tests(report)

    print("\n" + "=" * 60)
    print("DAY 58 SPRINT 8.2 RESULTS")
    print("=" * 60)
    print(f"  TOTAL: {report.passed}/{report.total} ({100 * report.passed / max(report.total, 1):.0f}%)")

    if report.failures:
        print(f"\n  {len(report.failures)} FAILURES:")
        for f in report.failures:
            print(f"    - {f}")
        print("=" * 60)
        return 1
    else:
        print(f"\n  All tests passed!")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
