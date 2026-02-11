# tests/test_day57_price_validation.py
"""
Day 57: Sprint 8.2 — Variant Price Validation

Tests:
  1. Size ordinal mapping (size_ordinal)
  2. Size track classification (size_track)
  3. Correct ordering — no flags expected
  4. Price inversions — flags expected
  5. Mixed variant types (size + flavor)
  6. Edge cases (empty, single, missing fields)
  7. Integration with full pipeline (grid + enrich + validate)
  8. Regression guards

Run: python tests/test_day57_price_validation.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.parsers.size_vocab import (
    size_ordinal,
    size_track,
    normalize_size_token,
)
from storage.variant_engine import (
    validate_variant_prices,
    enrich_variants_on_text_blocks,
    apply_size_grid_context,
    _parse_size_header_columns,
)
from storage.parsers.menu_grammar import (
    enrich_grammar_on_text_blocks,
)


# ══════════════════════════════════════════════════════════════
# Test Data
# ══════════════════════════════════════════════════════════════

# Group 1: Size ordinal mapping
ORDINAL_TESTS = [
    # (normalized_size, expected_ordinal)
    # Word sizes
    ("XS", 10), ("Mini", 15), ("S", 20), ("Personal", 25),
    ("Regular", 30), ("M", 35), ("L", 40), ("Deluxe", 45),
    ("XL", 50), ("XXL", 55),
    # Portions
    ("Slice", 110), ("Half", 120), ("Whole", 130),
    ("Family", 140), ("Party", 150),
    # Multiplicities
    ("Single", 210), ("Double", 220), ("Triple", 230),
    # Numeric inches
    ("6in", 6), ("8in", 8), ("10in", 10), ("12in", 12),
    ("14in", 14), ("16in", 16), ("18in", 18), ("20in", 20),
    # Piece counts
    ("6pc", 306), ("10pc", 310), ("12pc", 312),
    ("24pc", 324), ("50pc", 350),
    # Unknown -> None
    ("BBQ", None), ("Cheese", None), ("", None),
    ("Hot", None), ("Thin Crust", None),
]

# Group 2: Size track classification
TRACK_TESTS = [
    # (normalized_size, expected_track)
    ("10in", "inch"), ("12in", "inch"), ("16in", "inch"),
    ("6pc", "piece"), ("12pc", "piece"), ("24pc", "piece"),
    ("XS", "word"), ("S", "word"), ("M", "word"), ("L", "word"),
    ("XL", "word"), ("XXL", "word"), ("Mini", "word"),
    ("Personal", "word"), ("Regular", "word"), ("Deluxe", "word"),
    ("Slice", "portion"), ("Half", "portion"), ("Whole", "portion"),
    ("Family", "portion"), ("Party", "portion"),
    ("Single", "multiplicity"), ("Double", "multiplicity"),
    ("Triple", "multiplicity"),
    ("BBQ", None), ("", None), ("Hot", None),
]

# Group 3: Correct ordering — no flags expected
# Each entry: (description, list of variant dicts)
VALID_ORDERING_TESTS = [
    ("S < M < L ascending", [
        {"label": "Small", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "S"},
        {"label": "Medium", "price_cents": 1199, "confidence": 0.85,
         "kind": "size", "normalized_size": "M"},
        {"label": "Large", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
    ]),
    ("S == M < L (equal prices allowed)", [
        {"label": "Small", "price_cents": 999, "confidence": 0.85,
         "kind": "size", "normalized_size": "S"},
        {"label": "Medium", "price_cents": 999, "confidence": 0.85,
         "kind": "size", "normalized_size": "M"},
        {"label": "Large", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
    ]),
    ("10in < 12in < 16in ascending", [
        {"label": '10"', "price_cents": 800, "confidence": 0.85,
         "kind": "size", "normalized_size": "10in"},
        {"label": '12"', "price_cents": 1150, "confidence": 0.85,
         "kind": "size", "normalized_size": "12in"},
        {"label": '16"', "price_cents": 1395, "confidence": 0.85,
         "kind": "size", "normalized_size": "16in"},
    ]),
    ("10in < 12in < 16in < Family (inch + grid)", [
        {"label": '10" Mini', "price_cents": 800, "confidence": 0.85,
         "kind": "size", "normalized_size": "10in"},
        {"label": '12" S', "price_cents": 1150, "confidence": 0.85,
         "kind": "size", "normalized_size": "12in"},
        {"label": '16" L', "price_cents": 1395, "confidence": 0.85,
         "kind": "size", "normalized_size": "16in"},
    ]),
    ("Half < Whole", [
        {"label": "Half", "price_cents": 599, "confidence": 0.85,
         "kind": "size", "normalized_size": "Half"},
        {"label": "Whole", "price_cents": 999, "confidence": 0.85,
         "kind": "size", "normalized_size": "Whole"},
    ]),
    ("Slice < Half < Whole < Family", [
        {"label": "Slice", "price_cents": 350, "confidence": 0.85,
         "kind": "size", "normalized_size": "Slice"},
        {"label": "Half", "price_cents": 599, "confidence": 0.85,
         "kind": "size", "normalized_size": "Half"},
        {"label": "Whole", "price_cents": 999, "confidence": 0.85,
         "kind": "size", "normalized_size": "Whole"},
        {"label": "Family", "price_cents": 1899, "confidence": 0.85,
         "kind": "size", "normalized_size": "Family"},
    ]),
    ("6pc < 12pc < 24pc", [
        {"label": "6pc", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "6pc"},
        {"label": "12pc", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "12pc"},
        {"label": "24pc", "price_cents": 2499, "confidence": 0.85,
         "kind": "size", "normalized_size": "24pc"},
    ]),
    ("Single < Double < Triple", [
        {"label": "Single", "price_cents": 599, "confidence": 0.85,
         "kind": "size", "normalized_size": "Single"},
        {"label": "Double", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "Double"},
        {"label": "Triple", "price_cents": 1199, "confidence": 0.85,
         "kind": "size", "normalized_size": "Triple"},
    ]),
    ("Regular < Deluxe", [
        {"label": "Regular", "price_cents": 1099, "confidence": 0.85,
         "kind": "size", "normalized_size": "Regular"},
        {"label": "Deluxe", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "Deluxe"},
    ]),
]

# Group 4: Price inversions — flags expected
# Each entry: (description, variants, expected_inversion_count)
INVERSION_TESTS = [
    ("L cheaper than M", [
        {"label": "Small", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "S"},
        {"label": "Medium", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "M"},
        {"label": "Large", "price_cents": 1199, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
    ], 1),
    ("M cheaper than S", [
        {"label": "Small", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "S"},
        {"label": "Medium", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "M"},
        {"label": "Large", "price_cents": 1699, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
    ], 1),
    ("Both M<S and L<M (two inversions)", [
        {"label": "Small", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "S"},
        {"label": "Medium", "price_cents": 1199, "confidence": 0.85,
         "kind": "size", "normalized_size": "M"},
        {"label": "Large", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
    ], 2),
    ("16in < 12in (inch inversion)", [
        {"label": '12"', "price_cents": 1800, "confidence": 0.85,
         "kind": "size", "normalized_size": "12in"},
        {"label": '16"', "price_cents": 1200, "confidence": 0.85,
         "kind": "size", "normalized_size": "16in"},
    ], 1),
    ("Whole < Half (portion inversion)", [
        {"label": "Half", "price_cents": 999, "confidence": 0.85,
         "kind": "size", "normalized_size": "Half"},
        {"label": "Whole", "price_cents": 599, "confidence": 0.85,
         "kind": "size", "normalized_size": "Whole"},
    ], 1),
    ("24pc < 12pc (piece count inversion)", [
        {"label": "12pc", "price_cents": 2499, "confidence": 0.85,
         "kind": "size", "normalized_size": "12pc"},
        {"label": "24pc", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "24pc"},
    ], 1),
    ("Family < Slice (multi-step portion inversion)", [
        {"label": "Slice", "price_cents": 1599, "confidence": 0.85,
         "kind": "size", "normalized_size": "Slice"},
        {"label": "Half", "price_cents": 999, "confidence": 0.85,
         "kind": "size", "normalized_size": "Half"},
        {"label": "Whole", "price_cents": 599, "confidence": 0.85,
         "kind": "size", "normalized_size": "Whole"},
    ], 2),
    ("Double < Single (multiplicity inversion)", [
        {"label": "Single", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "Single"},
        {"label": "Double", "price_cents": 599, "confidence": 0.85,
         "kind": "size", "normalized_size": "Double"},
    ], 1),
    ("XL < L (word size inversion)", [
        {"label": "Large", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
        {"label": "XL", "price_cents": 1299, "confidence": 0.85,
         "kind": "size", "normalized_size": "XL"},
    ], 1),
    ("4-size grid fully inverted: 10in>12in>16in>18in", [
        {"label": '10"', "price_cents": 2500, "confidence": 0.85,
         "kind": "size", "normalized_size": "10in"},
        {"label": '12"', "price_cents": 2000, "confidence": 0.85,
         "kind": "size", "normalized_size": "12in"},
        {"label": '16"', "price_cents": 1500, "confidence": 0.85,
         "kind": "size", "normalized_size": "16in"},
        {"label": '18"', "price_cents": 1000, "confidence": 0.85,
         "kind": "size", "normalized_size": "18in"},
    ], 3),
]

# Group 5: Mixed variant types — only size validated
MIXED_TYPE_TESTS = [
    ("Sizes correct + flavors present -> no flag", [
        {"label": "Small", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "S"},
        {"label": "Large", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
        {"label": "Hot", "price_cents": 899, "confidence": 0.85,
         "kind": "flavor"},
    ], 0),
    ("Flavor-only variants -> no validation", [
        {"label": "Hot", "price_cents": 1099, "confidence": 0.85,
         "kind": "flavor"},
        {"label": "Mild", "price_cents": 899, "confidence": 0.85,
         "kind": "flavor"},
    ], 0),
    ("Single size + flavors -> no validation", [
        {"label": "Large", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
        {"label": "BBQ", "price_cents": 1499, "confidence": 0.85,
         "kind": "flavor"},
    ], 0),
    ("Style-only variants -> no validation", [
        {"label": "Thin Crust", "price_cents": 1199, "confidence": 0.85,
         "kind": "style"},
        {"label": "Deep Dish", "price_cents": 1499, "confidence": 0.85,
         "kind": "style"},
    ], 0),
    ("Sizes inverted + flavor present -> flag only sizes", [
        {"label": "Large", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
        {"label": "Small", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "S"},
        {"label": "BBQ", "price_cents": 1099, "confidence": 0.85,
         "kind": "flavor"},
    ], 1),
    ("Mixed tracks: inches + portions — each validated separately", [
        {"label": '10"', "price_cents": 800, "confidence": 0.85,
         "kind": "size", "normalized_size": "10in"},
        {"label": '16"', "price_cents": 1400, "confidence": 0.85,
         "kind": "size", "normalized_size": "16in"},
        {"label": "Half", "price_cents": 599, "confidence": 0.85,
         "kind": "size", "normalized_size": "Half"},
        {"label": "Whole", "price_cents": 999, "confidence": 0.85,
         "kind": "size", "normalized_size": "Whole"},
    ], 0),
]

# Group 6: Edge cases
EDGE_CASE_TESTS = [
    # (description, text_blocks, expected_total_flags)
    ("Empty text_blocks", [], 0),
    ("Block with no variants", [{"merged_text": "Pizza 14.99"}], 0),
    ("Block with single variant", [{"merged_text": "Pizza", "variants": [
        {"label": "Large", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
    ]}], 0),
    ("Variants without kind field (pre-enrichment)", [{"merged_text": "Pizza", "variants": [
        {"label": "Small", "price_cents": 899, "confidence": 0.85},
        {"label": "Large", "price_cents": 1499, "confidence": 0.85},
    ]}], 0),
    ("Variants with zero price_cents (skipped)", [{"merged_text": "Pizza", "variants": [
        {"label": "Small", "price_cents": 0, "confidence": 0.85,
         "kind": "size", "normalized_size": "S"},
        {"label": "Large", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
    ]}], 0),
    ("Variants with missing normalized_size (skipped)", [{"merged_text": "Pizza", "variants": [
        {"label": "Small", "price_cents": 899, "confidence": 0.85,
         "kind": "size"},
        {"label": "Large", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
    ]}], 0),
    ("Variants with kind=other (skipped)", [{"merged_text": "Pizza", "variants": [
        {"label": "Option A", "price_cents": 1499, "confidence": 0.85,
         "kind": "other", "normalized_size": "S"},
        {"label": "Option B", "price_cents": 899, "confidence": 0.85,
         "kind": "other", "normalized_size": "L"},
    ]}], 0),
    ("Multiple blocks — only flagged block gets price_flags", [
        {"merged_text": "Cheese Pizza", "variants": [
            {"label": "Small", "price_cents": 899, "confidence": 0.85,
             "kind": "size", "normalized_size": "S"},
            {"label": "Large", "price_cents": 1499, "confidence": 0.85,
             "kind": "size", "normalized_size": "L"},
        ]},
        {"merged_text": "Pepperoni Pizza", "variants": [
            {"label": "Small", "price_cents": 1499, "confidence": 0.85,
             "kind": "size", "normalized_size": "S"},
            {"label": "Large", "price_cents": 899, "confidence": 0.85,
             "kind": "size", "normalized_size": "L"},
        ]},
    ], 1),  # only second block flagged
]


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

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


def _make_tb(text: str, grammar: Optional[Dict] = None,
             variants: Optional[List[Dict]] = None) -> Dict[str, Any]:
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
    return tb


def _count_inversion_flags(tb: Dict[str, Any]) -> int:
    """Count variant_price_inversion flags on a text_block."""
    flags = tb.get("price_flags") or []
    return sum(1 for f in flags if f.get("reason") == "variant_price_inversion")


def _count_inversions_in_flag(flag: Dict[str, Any]) -> int:
    """Count individual inversions within a single flag's details."""
    return len(flag.get("details", {}).get("inversions", []))


# ══════════════════════════════════════════════════════════════
# Group 1: Size Ordinal Mapping
# ══════════════════════════════════════════════════════════════

def run_ordinal_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 1: SIZE ORDINAL MAPPING")
    print("=" * 60)

    for ns, expected in ORDINAL_TESTS:
        result = size_ordinal(ns)
        report.check(
            result == expected,
            f"size_ordinal({ns!r}) = {result!r}, expected {expected!r}"
        )

    # Verify ordering within each track
    # Word sizes: XS < Mini < S < Personal < Regular < M < L < Deluxe < XL < XXL
    word_chain = ["XS", "Mini", "S", "Personal", "Regular", "M", "L", "Deluxe", "XL", "XXL"]
    for i in range(len(word_chain) - 1):
        a, b = word_chain[i], word_chain[i + 1]
        oa, ob = size_ordinal(a), size_ordinal(b)
        report.check(
            oa is not None and ob is not None and oa < ob,
            f"Word order: {a}({oa}) should be < {b}({ob})"
        )

    # Portions: Slice < Half < Whole < Family < Party
    portion_chain = ["Slice", "Half", "Whole", "Family", "Party"]
    for i in range(len(portion_chain) - 1):
        a, b = portion_chain[i], portion_chain[i + 1]
        oa, ob = size_ordinal(a), size_ordinal(b)
        report.check(
            oa is not None and ob is not None and oa < ob,
            f"Portion order: {a}({oa}) should be < {b}({ob})"
        )

    # Multiplicities: Single < Double < Triple
    mult_chain = ["Single", "Double", "Triple"]
    for i in range(len(mult_chain) - 1):
        a, b = mult_chain[i], mult_chain[i + 1]
        oa, ob = size_ordinal(a), size_ordinal(b)
        report.check(
            oa is not None and ob is not None and oa < ob,
            f"Multiplicity order: {a}({oa}) should be < {b}({ob})"
        )

    # Numeric inches: natural order
    for a, b in [(6, 8), (8, 10), (10, 12), (12, 14), (14, 16), (16, 18), (18, 20)]:
        oa = size_ordinal(f"{a}in")
        ob = size_ordinal(f"{b}in")
        report.check(
            oa is not None and ob is not None and oa < ob,
            f"Inch order: {a}in({oa}) should be < {b}in({ob})"
        )

    # Piece counts: natural order
    for a, b in [(6, 10), (10, 12), (12, 24), (24, 50)]:
        oa = size_ordinal(f"{a}pc")
        ob = size_ordinal(f"{b}pc")
        report.check(
            oa is not None and ob is not None and oa < ob,
            f"Piece order: {a}pc({oa}) should be < {b}pc({ob})"
        )


# ══════════════════════════════════════════════════════════════
# Group 2: Size Track Classification
# ══════════════════════════════════════════════════════════════

def run_track_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 2: SIZE TRACK CLASSIFICATION")
    print("=" * 60)

    for ns, expected in TRACK_TESTS:
        result = size_track(ns)
        report.check(
            result == expected,
            f"size_track({ns!r}) = {result!r}, expected {expected!r}"
        )


# ══════════════════════════════════════════════════════════════
# Group 3: Correct Ordering — No Flags
# ══════════════════════════════════════════════════════════════

def run_valid_ordering_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 3: CORRECT ORDERING — NO FLAGS")
    print("=" * 60)

    for desc, variants in VALID_ORDERING_TESTS:
        tb = _make_tb("Test Item", variants=variants)
        validate_variant_prices([tb])
        flag_count = _count_inversion_flags(tb)
        report.check(
            flag_count == 0,
            f"[{desc}] expected 0 inversion flags, got {flag_count}"
        )


# ══════════════════════════════════════════════════════════════
# Group 4: Price Inversions — Flags Expected
# ══════════════════════════════════════════════════════════════

def run_inversion_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 4: PRICE INVERSIONS — FLAGS EXPECTED")
    print("=" * 60)

    for desc, variants, expected_inversions in INVERSION_TESTS:
        tb = _make_tb("Test Item", variants=variants)
        validate_variant_prices([tb])

        # Check that we have inversion flags
        flags = [f for f in (tb.get("price_flags") or [])
                 if f.get("reason") == "variant_price_inversion"]
        report.check(
            len(flags) > 0,
            f"[{desc}] expected inversion flag(s), got none"
        )

        # Check total inversion count across all flags
        total_inversions = sum(_count_inversions_in_flag(f) for f in flags)
        report.check(
            total_inversions == expected_inversions,
            f"[{desc}] expected {expected_inversions} inversions, got {total_inversions}"
        )

        # Verify flag structure
        for f in flags:
            report.check(
                f.get("severity") == "warn",
                f"[{desc}] flag severity should be 'warn', got {f.get('severity')!r}"
            )
            details = f.get("details", {})
            report.check(
                "track" in details,
                f"[{desc}] flag details should contain 'track'"
            )
            report.check(
                "inversions" in details,
                f"[{desc}] flag details should contain 'inversions'"
            )
            report.check(
                "expected_order" in details,
                f"[{desc}] flag details should contain 'expected_order'"
            )
            report.check(
                "actual_prices_cents" in details,
                f"[{desc}] flag details should contain 'actual_prices_cents'"
            )

        # Verify inversion detail structure
        if flags:
            for inv in flags[0]["details"]["inversions"]:
                report.check(
                    "smaller_size" in inv and "larger_size" in inv,
                    f"[{desc}] inversion should have smaller_size and larger_size"
                )
                report.check(
                    "smaller_price_cents" in inv and "larger_price_cents" in inv,
                    f"[{desc}] inversion should have price_cents fields"
                )
                report.check(
                    inv["smaller_price_cents"] > inv["larger_price_cents"],
                    f"[{desc}] smaller_size price should be > larger_size price in inversion"
                )


# ══════════════════════════════════════════════════════════════
# Group 5: Mixed Variant Types
# ══════════════════════════════════════════════════════════════

def run_mixed_type_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 5: MIXED VARIANT TYPES")
    print("=" * 60)

    for desc, variants, expected_flags in MIXED_TYPE_TESTS:
        tb = _make_tb("Test Item", variants=variants)
        validate_variant_prices([tb])
        flag_count = _count_inversion_flags(tb)
        report.check(
            flag_count == expected_flags,
            f"[{desc}] expected {expected_flags} inversion flag(s), got {flag_count}"
        )


# ══════════════════════════════════════════════════════════════
# Group 6: Edge Cases
# ══════════════════════════════════════════════════════════════

def run_edge_case_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 6: EDGE CASES")
    print("=" * 60)

    for desc, blocks, expected_total in EDGE_CASE_TESTS:
        # Deep copy blocks to avoid mutation issues
        import copy
        test_blocks = copy.deepcopy(blocks)
        validate_variant_prices(test_blocks)

        total_flags = sum(_count_inversion_flags(b) for b in test_blocks)
        report.check(
            total_flags == expected_total,
            f"[{desc}] expected {expected_total} total inversion flag(s), got {total_flags}"
        )

    # Special check: multiple blocks — only second flagged
    import copy
    multi_blocks = copy.deepcopy(EDGE_CASE_TESTS[-1][1])
    validate_variant_prices(multi_blocks)
    report.check(
        _count_inversion_flags(multi_blocks[0]) == 0,
        "Multi-block: first block (correct order) should have 0 flags"
    )
    report.check(
        _count_inversion_flags(multi_blocks[1]) == 1,
        "Multi-block: second block (inverted) should have 1 flag"
    )


# ══════════════════════════════════════════════════════════════
# Group 7: Integration with Full Pipeline
# ══════════════════════════════════════════════════════════════

def run_integration_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 7: FULL PIPELINE INTEGRATION")
    print("=" * 60)

    # 7a. Grid with correct prices -> no flags
    blocks = [
        _make_tb('10"Mini 12" Sml 16"lrg Family Size', grammar={
            "line_type": "size_header",
            "confidence": 0.9,
            "parsed_name": '10"Mini 12" Sml 16"lrg Family Size',
            "size_mentions": ['10"', '12"', '16"', "Family"],
        }),
        _make_tb("CHEESE 8.00 11.50 13.95 22.50", grammar={
            "line_type": "menu_item",
            "confidence": 0.85,
            "parsed_name": "CHEESE",
            "price_mentions": [8.0, 11.5, 13.95, 22.5],
        }),
    ]
    apply_size_grid_context(blocks)
    enrich_variants_on_text_blocks(blocks)
    validate_variant_prices(blocks)
    report.check(
        _count_inversion_flags(blocks[1]) == 0,
        "7a. Correct grid prices -> no inversion flags"
    )

    # 7b. Grid with inverted prices (OCR error: 10" costs more than 16")
    blocks = [
        _make_tb('10" 16"', grammar={
            "line_type": "size_header",
            "confidence": 0.9,
            "parsed_name": '10" 16"',
            "size_mentions": ['10"', '16"'],
        }),
        _make_tb("CHEESE 18.00 12.00", grammar={
            "line_type": "menu_item",
            "confidence": 0.85,
            "parsed_name": "CHEESE",
            "price_mentions": [18.0, 12.0],
        }),
    ]
    apply_size_grid_context(blocks)
    enrich_variants_on_text_blocks(blocks)
    validate_variant_prices(blocks)
    report.check(
        _count_inversion_flags(blocks[1]) == 1,
        "7b. Inverted grid prices -> 1 inversion flag"
    )
    flags = [f for f in (blocks[1].get("price_flags") or [])
             if f.get("reason") == "variant_price_inversion"]
    if flags:
        inv = flags[0]["details"]["inversions"][0]
        report.check(
            inv["smaller_price_cents"] == 1800 and inv["larger_price_cents"] == 1200,
            f"7b. Inversion details: 10\"=$18.00 > 16\"=$12.00"
        )
        report.check(
            flags[0]["details"]["track"] == "inch",
            "7b. Track should be 'inch'"
        )

    # 7c. Right-aligned grid (3 prices, 4 columns) with correct order
    blocks = [
        _make_tb('10"Mini 12" Sml 16"lrg Family Size', grammar={
            "line_type": "size_header",
            "confidence": 0.9,
            "parsed_name": '10"Mini 12" Sml 16"lrg Family Size',
            "size_mentions": ['10"', '12"', '16"', "Family"],
        }),
        _make_tb("GOURMET 14.75 16.95 25.95", grammar={
            "line_type": "menu_item",
            "confidence": 0.85,
            "parsed_name": "GOURMET",
            "price_mentions": [14.75, 16.95, 25.95],
        }),
    ]
    apply_size_grid_context(blocks)
    enrich_variants_on_text_blocks(blocks)
    validate_variant_prices(blocks)
    report.check(
        _count_inversion_flags(blocks[1]) == 0,
        "7c. Right-aligned grid (3 prices ascending) -> no flags"
    )

    # 7d. Regular/Deluxe correct order
    blocks = [
        _make_tb("Regular Deluxe", grammar={
            "line_type": "size_header",
            "confidence": 0.9,
            "parsed_name": "Regular Deluxe",
            "size_mentions": ["Regular", "Deluxe"],
        }),
        _make_tb("BURGER 10.99 13.99", grammar={
            "line_type": "menu_item",
            "confidence": 0.85,
            "parsed_name": "BURGER",
            "price_mentions": [10.99, 13.99],
        }),
    ]
    apply_size_grid_context(blocks)
    enrich_variants_on_text_blocks(blocks)
    validate_variant_prices(blocks)
    report.check(
        _count_inversion_flags(blocks[1]) == 0,
        "7d. Regular/Deluxe correct order -> no flags"
    )

    # 7e. Regular/Deluxe inverted
    blocks = [
        _make_tb("Regular Deluxe", grammar={
            "line_type": "size_header",
            "confidence": 0.9,
            "parsed_name": "Regular Deluxe",
            "size_mentions": ["Regular", "Deluxe"],
        }),
        _make_tb("BURGER 15.99 10.99", grammar={
            "line_type": "menu_item",
            "confidence": 0.85,
            "parsed_name": "BURGER",
            "price_mentions": [15.99, 10.99],
        }),
    ]
    apply_size_grid_context(blocks)
    enrich_variants_on_text_blocks(blocks)
    validate_variant_prices(blocks)
    report.check(
        _count_inversion_flags(blocks[1]) == 1,
        "7e. Regular/Deluxe inverted -> 1 flag"
    )

    # 7f. Multiple items under same grid — one correct, one inverted
    blocks = [
        _make_tb("Small Medium Large", grammar={
            "line_type": "size_header",
            "confidence": 0.9,
            "parsed_name": "Small Medium Large",
            "size_mentions": ["Small", "Medium", "Large"],
        }),
        _make_tb("CHEESE 8.99 10.99 12.99", grammar={
            "line_type": "menu_item",
            "confidence": 0.85,
            "parsed_name": "CHEESE",
            "price_mentions": [8.99, 10.99, 12.99],
        }),
        _make_tb("PEPPERONI 14.99 10.99 12.99", grammar={
            "line_type": "menu_item",
            "confidence": 0.85,
            "parsed_name": "PEPPERONI",
            "price_mentions": [14.99, 10.99, 12.99],
        }),
    ]
    apply_size_grid_context(blocks)
    enrich_variants_on_text_blocks(blocks)
    validate_variant_prices(blocks)
    report.check(
        _count_inversion_flags(blocks[1]) == 0,
        "7f. Cheese (correct order) -> no flags"
    )
    report.check(
        _count_inversion_flags(blocks[2]) == 1,
        "7f. Pepperoni (S=$14.99 > M=$10.99) -> 1 flag"
    )


# ══════════════════════════════════════════════════════════════
# Group 8: Regression Guards
# ══════════════════════════════════════════════════════════════

def run_regression_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 8: REGRESSION GUARDS")
    print("=" * 60)

    # 8a. validate_variant_prices on empty list is a no-op
    validate_variant_prices([])
    report.check(True, "8a. Empty list does not crash")

    # 8b. validate_variant_prices does not mutate variants themselves
    variants = [
        {"label": "Small", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "S"},
        {"label": "Large", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
    ]
    import copy
    original = copy.deepcopy(variants)
    tb = _make_tb("Test", variants=variants)
    validate_variant_prices([tb])
    for i, v in enumerate(variants):
        report.check(
            v["label"] == original[i]["label"] and
            v["price_cents"] == original[i]["price_cents"] and
            v["kind"] == original[i]["kind"],
            f"8b. Variant {i} should not be mutated"
        )

    # 8c. price_flags does not already exist -> created fresh
    tb = _make_tb("Test", variants=[
        {"label": "Small", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "S"},
        {"label": "Large", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
    ])
    assert "price_flags" not in tb
    validate_variant_prices([tb])
    report.check(
        isinstance(tb.get("price_flags"), list) and len(tb["price_flags"]) == 1,
        "8c. price_flags created fresh when inversion found"
    )

    # 8d. price_flags already exists -> appended to, not overwritten
    tb = _make_tb("Test", variants=[
        {"label": "Small", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "S"},
        {"label": "Large", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "L"},
    ])
    tb["price_flags"] = [{"severity": "info", "reason": "existing_flag", "details": {}}]
    validate_variant_prices([tb])
    report.check(
        len(tb["price_flags"]) == 2,
        "8d. Existing price_flags preserved, new flag appended"
    )
    report.check(
        tb["price_flags"][0]["reason"] == "existing_flag",
        "8d. Original flag still at index 0"
    )

    # 8e. Correctly-ordered real pizza data should produce no flags
    # Simulate real data: 10"=$8, 12"=$11.50, 16"=$13.95, Family=$22.50
    real_variants = [
        {"label": '10" Mini', "price_cents": 800, "confidence": 0.85,
         "kind": "size", "normalized_size": "10in"},
        {"label": '12" S', "price_cents": 1150, "confidence": 0.85,
         "kind": "size", "normalized_size": "12in"},
        {"label": '16" L', "price_cents": 1395, "confidence": 0.85,
         "kind": "size", "normalized_size": "16in"},
    ]
    tb = _make_tb("CHEESE", variants=real_variants)
    validate_variant_prices([tb])
    report.check(
        _count_inversion_flags(tb) == 0,
        "8e. Real pizza data (correct order) -> no flags"
    )


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════

def main():
    report = TestReport()

    run_ordinal_tests(report)
    run_track_tests(report)
    run_valid_ordering_tests(report)
    run_inversion_tests(report)
    run_mixed_type_tests(report)
    run_edge_case_tests(report)
    run_integration_tests(report)
    run_regression_tests(report)

    print("\n" + "=" * 60)
    print("DAY 57 SPRINT 8.2 RESULTS")
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
