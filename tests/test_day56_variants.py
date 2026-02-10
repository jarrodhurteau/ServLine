# tests/test_day56_variants.py
"""
Day 56: Sprint 8.2 — Size Grid Context & Grammar-to-Variant Bridge

Tests:
  1. Shared size vocabulary (size_vocab.py)
  2. Size header column parsing (_parse_size_header_columns)
  3. Size grid context propagation (apply_size_grid_context)
  4. Grid-to-variant mapping (_build_variants_from_grid)
  5. Section heading grid expiration
  6. Real OCR integration
  7. Regression guards

Run: python tests/test_day56_variants.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.parsers.size_vocab import (
    SIZE_WORD_MAP,
    SIZE_WORDS,
    SIZE_WORD_RE,
    NUMERIC_SIZE_RE,
    normalize_size_token,
)
from storage.variant_engine import (
    _parse_size_header_columns,
    _extract_size_grid,
    _build_variants_from_grid,
    apply_size_grid_context,
    SizeGridContext,
    SizeGridColumn,
    enrich_variants_on_text_blocks,
    classify_raw_variant,
)
from storage.parsers.menu_grammar import (
    enrich_grammar_on_text_blocks,
    classify_menu_lines,
    _is_size_header,
)


# ══════════════════════════════════════════════════════════════
# Test Data
# ══════════════════════════════════════════════════════════════

# Group 1: Shared vocabulary
NORMALIZE_TOKEN_TESTS = [
    # (input, expected)
    ("small", "S"),
    ("sm", "S"),
    ("sml", "S"),
    ("medium", "M"),
    ("med", "M"),
    ("large", "L"),
    ("lg", "L"),
    ("lrg", "L"),
    ("xlarge", "XL"),
    ("xl", "XL"),
    ("x-large", "XL"),
    ("extra large", "XL"),
    ("family", "Family"),
    ("party", "Party"),
    ("half", "Half"),
    ("whole", "Whole"),
    ("slice", "Slice"),
    ("personal", "Personal"),
    ("individual", "Personal"),
    ("regular", "Regular"),
    ("deluxe", "Deluxe"),
    ("mini", "Mini"),
    ("single", "Single"),
    ("double", "Double"),
    ("triple", "Triple"),
    # Numeric sizes
    ('10"', '10"'),
    ('14"', '14"'),
    ("12\u00b0", '12"'),     # degree symbol OCR
    ("16\u201d", '16"'),     # smart quote
    ("6pc", "6pc"),
    ("12pcs", "12pc"),
    ("24ct", "24pc"),
    # Passthrough for unknown
    ("BBQ", "BBQ"),
    ("Cheese", "Cheese"),
]

# Group 2: Column parsing
COLUMN_PARSE_TESTS = [
    # (header_text, expected_count, expected_labels)
    ('10"Mini 12" Sml 16"lrg Family Size', 4,
     ['10" Mini', '12" S', '16" L', 'Family']),
    ("Regular Deluxe", 2, ["Regular", "Deluxe"]),
    ('12" Sml   16"lrg  Family Size', 3,
     ['12" S', '16" L', 'Family']),
    ("Small Medium Large", 3, ["S", "M", "L"]),
    ("Personal Family Party", 3, ["Personal", "Family", "Party"]),
    # Actual OCR with degree symbol (Smt is OCR typo for Sml — not a known
    # qualifier so 12" stays standalone; column COUNT is what matters)
    ('10\u00b0Mini 12" Smt    16"trg Family Size', 4,
     ['10" Mini', '12"', '16" trg', 'Family']),
    # Gourmet OCR
    ('12\u00b0 Sml         16"lrg Family Size', 3,
     ['12" S', '16" L', 'Family']),
    # Single token -> too few for grid
    ("Small", 1, ["S"]),
    # Empty
    ("", 0, []),
    # With slice counts (should be skipped as info)
    ('10" 12" 16" Family', 4, ['10"', '12"', '16"', 'Family']),
]

# Group 3: Grid-to-variant mapping
GRID_MAPPING_TESTS = [
    # (grid_labels, prices_dollars, expected_variant_count, expected_first_label, expected_last_label)
    # Perfect 1:1
    (['10" Mini', '12" S', '16" L', 'Family'],
     [8.00, 11.50, 13.95, 22.50], 4, '10" Mini', 'Family'),
    # Fewer prices -> right-align (skip first column)
    (['10" Mini', '12" S', '16" L', 'Family'],
     [17.95, 25.50, 34.75], 3, '12" S', 'Family'),
    # 2 prices, 4 columns -> right-align (skip first 2)
    (['10" Mini', '12" S', '16" L', 'Family'],
     [25.50, 34.75], 2, '16" L', 'Family'),
    # Regular/Deluxe 2-column
    (['Regular', 'Deluxe'],
     [8.95, 11.50], 2, 'Regular', 'Deluxe'),
    # More prices than columns -> no grid applied
    (['Regular', 'Deluxe'],
     [8.95, 11.50, 14.99], 0, None, None),
    # Single price -> no variants
    (['10" Mini', '12" S', '16" L', 'Family'],
     [8.00], 0, None, None),
]

# Group 5: Section heading expiration
KNOWN_HEADINGS = [
    "PIZZA", "CALZONES", "WINGS", "BURGERS", "SALADS",
    "SANDWICHES", "PASTA", "DESSERTS", "BEVERAGES",
    "BUILD YOUR OWN BURGER!", "MELT SANDWICHES",
    "GOURMET PIZZA", "GOURMET PIZZAS",
]

NON_EXPIRING_TYPES = [
    "info_line", "topping_list", "description_only", "price_only",
]


# ══════════════════════════════════════════════════════════════
# Test Runner
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


# ── Group 1: Shared Vocabulary ────────────────────────

def run_vocabulary_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 1: SHARED SIZE VOCABULARY")
    print("=" * 60)

    # 1a. normalize_size_token
    for raw, expected in NORMALIZE_TOKEN_TESTS:
        result = normalize_size_token(raw)
        report.check(
            result == expected,
            f"normalize_size_token({raw!r}) = {result!r}, expected {expected!r}"
        )

    # 1b. SIZE_WORDS is a superset of old grammar _SIZE_WORDS
    old_grammar_words = {
        "small", "sm", "sml", "medium", "med", "md",
        "large", "lg", "lrg", "x-large", "xlarge", "xl", "extra large",
        "personal", "family", "party", "half", "whole", "slice",
        "single", "double", "triple", "regular", "deluxe",
    }
    for w in old_grammar_words:
        report.check(
            w in SIZE_WORDS,
            f"Old grammar word {w!r} missing from SIZE_WORDS"
        )

    # 1c. SIZE_WORDS is a superset of old variant_engine _SIZE_WORD_MAP
    old_variant_keys = {
        "xs", "x-small", "extra small", "small", "sm", "sml",
        "medium", "med", "md", "large", "lg", "xlarge", "x-large",
        "extra large", "xl", "xxl", "half", "whole", "slice",
        "personal", "family", "party", "party size", "family size",
        "individual", "single", "double", "triple",
    }
    for w in old_variant_keys:
        report.check(
            w in SIZE_WORDS,
            f"Old variant key {w!r} missing from SIZE_WORDS"
        )

    # 1d. SIZE_WORD_RE matches known words
    for w in ["small", "large", "family", "regular", "deluxe", "mini"]:
        m = SIZE_WORD_RE.search(f"test {w} test")
        report.check(
            m is not None and m.group(1).lower() == w,
            f"SIZE_WORD_RE failed to match {w!r}"
        )

    # 1e. NUMERIC_SIZE_RE matches patterns
    # Note: \b at end means quote/degree must be followed by a word char or preceded
    # by one for boundary. Test with realistic context (quote+word or pc/in suffixes).
    numeric_tests = [
        ('10"Mini', "10"), ('14"lrg', "14"),
        ("6pc", "6"), ("12pcs", "12"),
        ("16in", "16"), ("10inch", "10"), ("24ct", "24"),
    ]
    for text, expected_num in numeric_tests:
        m = NUMERIC_SIZE_RE.search(text)
        report.check(
            m is not None and m.group(1) == expected_num,
            f"NUMERIC_SIZE_RE failed on {text!r}"
        )


# ── Group 2: Column Parsing ──────────────────────────

def run_column_parse_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 2: SIZE HEADER COLUMN PARSING")
    print("=" * 60)

    for header_text, expected_count, expected_labels in COLUMN_PARSE_TESTS:
        cols = _parse_size_header_columns(header_text)
        report.check(
            len(cols) == expected_count,
            f"Column count for {header_text[:40]!r}: got {len(cols)}, expected {expected_count}"
        )
        if expected_count > 0 and len(cols) == expected_count:
            actual_labels = [c.normalized for c in cols]
            report.check(
                actual_labels == expected_labels,
                f"Labels for {header_text[:40]!r}: got {actual_labels}, expected {expected_labels}"
            )

    # Position ordering
    cols = _parse_size_header_columns('10"Mini 12" Sml 16"lrg Family Size')
    for i, c in enumerate(cols):
        report.check(
            c.position == i,
            f"Column {i} position mismatch: got {c.position}"
        )

    # Coalescing: numeric + word should merge
    cols = _parse_size_header_columns('10"Mini')
    report.check(
        len(cols) == 1 and '10"' in cols[0].normalized and 'Mini' in cols[0].normalized,
        f"10\"Mini coalescing: got {[c.normalized for c in cols]}"
    )


# ── Group 3: Grid-to-Variant Mapping ─────────────────

def run_grid_mapping_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 3: GRID-TO-VARIANT MAPPING")
    print("=" * 60)

    for labels, prices, expected_count, expected_first, expected_last in GRID_MAPPING_TESTS:
        grid = SizeGridContext(
            columns=[SizeGridColumn(raw_label=l, normalized=l, position=i)
                     for i, l in enumerate(labels)],
            source_line_index=0,
        )
        grammar_prices = prices
        variants = _build_variants_from_grid(grid, grammar_prices, [], [])

        report.check(
            len(variants) == expected_count,
            f"Variant count for {len(prices)} prices / {len(labels)} cols: "
            f"got {len(variants)}, expected {expected_count}"
        )
        if expected_count > 0 and len(variants) == expected_count:
            report.check(
                variants[0]["label"] == expected_first,
                f"First variant label: got {variants[0]['label']!r}, expected {expected_first!r}"
            )
            report.check(
                variants[-1]["label"] == expected_last,
                f"Last variant label: got {variants[-1]['label']!r}, expected {expected_last!r}"
            )
            # All variants should have price_cents > 0
            for v in variants:
                report.check(
                    v["price_cents"] > 0,
                    f"Variant {v['label']!r} has zero/negative price_cents"
                )

    # Price conversion: dollars to cents
    grid = SizeGridContext(
        columns=[SizeGridColumn("S", "S", 0), SizeGridColumn("L", "L", 1)],
        source_line_index=0,
    )
    variants = _build_variants_from_grid(grid, [8.99, 12.50], [], [])
    report.check(
        len(variants) == 2 and variants[0]["price_cents"] == 899 and variants[1]["price_cents"] == 1250,
        f"Price conversion: got {[v.get('price_cents') for v in variants]}"
    )

    # Confidence: perfect mapping = 0.85, right-aligned = 0.75
    grid4 = SizeGridContext(
        columns=[SizeGridColumn(l, l, i) for i, l in enumerate(["A", "B", "C", "D"])],
        source_line_index=0,
    )
    perfect = _build_variants_from_grid(grid4, [1.0, 2.0, 3.0, 4.0], [], [])
    report.check(
        all(v["confidence"] == 0.85 for v in perfect),
        f"Perfect mapping confidence: got {[v['confidence'] for v in perfect]}"
    )
    right_align = _build_variants_from_grid(grid4, [2.0, 3.0, 4.0], [], [])
    report.check(
        all(v["confidence"] == 0.75 for v in right_align),
        f"Right-align confidence: got {[v['confidence'] for v in right_align]}"
    )


# ── Group 4: Grid Context Propagation ────────────────

def _make_tb(text: str, grammar: Dict[str, Any],
             price_candidates: Optional[List[Dict]] = None,
             variants: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """Helper to build a text_block dict for testing."""
    tb: Dict[str, Any] = {"merged_text": text, "grammar": grammar}
    if price_candidates is not None:
        tb["price_candidates"] = price_candidates
    if variants is not None:
        tb["variants"] = variants
    return tb


def run_context_propagation_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 4: SIZE GRID CONTEXT PROPAGATION")
    print("=" * 60)

    # 4a. Basic: size_header -> menu_item gets grid variants
    blocks = [
        _make_tb('10"Mini 12" Sml 16"lrg Family Size',
                 {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
        _make_tb("CHEESE 8.00 11.50 13.95 22.50",
                 {"line_type": "menu_item", "price_mentions": [8.0, 11.5, 13.95, 22.5],
                  "parsed_name": "CHEESE"}),
    ]
    apply_size_grid_context(blocks)
    report.check(
        "variants" in blocks[1] and len(blocks[1]["variants"]) == 4,
        f"4a: CHEESE should get 4 variants, got {len(blocks[1].get('variants', []))}"
    )
    if blocks[1].get("variants"):
        report.check(
            '10"' in blocks[1]["variants"][0]["label"],
            f"4a: First variant label should contain 10\", got {blocks[1]['variants'][0]['label']!r}"
        )
        report.check(
            blocks[1]["variants"][0]["price_cents"] == 800,
            f"4a: First price should be 800 cents, got {blocks[1]['variants'][0].get('price_cents')}"
        )
        report.check(
            blocks[1].get("meta", {}).get("size_grid_applied") is True,
            "4a: meta.size_grid_applied should be True"
        )

    # 4b. Grid applies to multiple items
    blocks = [
        _make_tb('Regular Deluxe',
                 {"line_type": "size_header", "size_mentions": ["regular", "deluxe"],
                  "parsed_name": ""}),
        _make_tb("Hamburger 8.95 11.50",
                 {"line_type": "menu_item", "price_mentions": [8.95, 11.5],
                  "parsed_name": "Hamburger"}),
        _make_tb("Cheeseburger 9.50 12.25",
                 {"line_type": "menu_item", "price_mentions": [9.5, 12.25],
                  "parsed_name": "Cheeseburger"}),
    ]
    apply_size_grid_context(blocks)
    report.check(
        len(blocks[1].get("variants", [])) == 2 and len(blocks[2].get("variants", [])) == 2,
        f"4b: Both items should get 2 variants"
    )
    if blocks[1].get("variants"):
        report.check(
            blocks[1]["variants"][0]["label"] == "Regular",
            f"4b: Hamburger first variant should be 'Regular', got {blocks[1]['variants'][0]['label']!r}"
        )

    # 4c. Grid expires at known section heading
    blocks = [
        _make_tb('Regular Deluxe',
                 {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
        _make_tb("Item1 8.95 11.50",
                 {"line_type": "menu_item", "price_mentions": [8.95, 11.5],
                  "parsed_name": "Item1"}),
        _make_tb("CALZONES",
                 {"line_type": "heading", "parsed_name": "CALZONES",
                  "size_mentions": [], "price_mentions": []}),
        _make_tb("Veggie 9.50 14.75",
                 {"line_type": "menu_item", "price_mentions": [9.5, 14.75],
                  "parsed_name": "Veggie"}),
    ]
    apply_size_grid_context(blocks)
    report.check(
        len(blocks[1].get("variants", [])) == 2,
        "4c: Item1 (before heading) should get 2 variants"
    )
    report.check(
        len(blocks[3].get("variants", [])) == 0,
        f"4c: Veggie (after CALZONES heading) should get 0 variants, got {len(blocks[3].get('variants', []))}"
    )

    # 4d. New size_header replaces old grid
    blocks = [
        _make_tb('10"Mini 12" Sml 16"lrg Family Size',
                 {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
        _make_tb("CHEESE 8.00 11.50 13.95 22.50",
                 {"line_type": "menu_item", "price_mentions": [8.0, 11.5, 13.95, 22.5],
                  "parsed_name": "CHEESE"}),
        _make_tb('12" Sml 16"lrg Family Size',
                 {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
        _make_tb("GOURMET 17.95 25.50 34.75",
                 {"line_type": "menu_item", "price_mentions": [17.95, 25.5, 34.75],
                  "parsed_name": "GOURMET"}),
    ]
    apply_size_grid_context(blocks)
    report.check(
        len(blocks[1].get("variants", [])) == 4,
        "4d: CHEESE should get 4 variants (first grid)"
    )
    report.check(
        len(blocks[3].get("variants", [])) == 3,
        f"4d: GOURMET should get 3 variants (second grid), got {len(blocks[3].get('variants', []))}"
    )

    # 4e. Info lines don't expire grid
    blocks = [
        _make_tb('Regular Deluxe',
                 {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
        _make_tb("Served with fries",
                 {"line_type": "info_line", "size_mentions": [], "parsed_name": "",
                  "price_mentions": []}),
        _make_tb("Item1 8.95 11.50",
                 {"line_type": "menu_item", "price_mentions": [8.95, 11.5],
                  "parsed_name": "Item1"}),
    ]
    apply_size_grid_context(blocks)
    report.check(
        len(blocks[2].get("variants", [])) == 2,
        "4e: Item after info_line should still get grid variants"
    )

    # 4f. Topping lists don't expire grid
    blocks = [
        _make_tb('Regular Deluxe',
                 {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
        _make_tb("Pepperoni, Sausage, Mushroom",
                 {"line_type": "topping_list", "size_mentions": [], "parsed_name": "",
                  "price_mentions": []}),
        _make_tb("Item1 8.95 11.50",
                 {"line_type": "menu_item", "price_mentions": [8.95, 11.5],
                  "parsed_name": "Item1"}),
    ]
    apply_size_grid_context(blocks)
    report.check(
        len(blocks[2].get("variants", [])) == 2,
        "4f: Item after topping_list should still get grid variants"
    )

    # 4g. Single-price items are NOT affected by grid
    blocks = [
        _make_tb('Regular Deluxe',
                 {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
        _make_tb("Side Salad 4.99",
                 {"line_type": "menu_item", "price_mentions": [4.99],
                  "parsed_name": "Side Salad"}),
    ]
    apply_size_grid_context(blocks)
    report.check(
        len(blocks[1].get("variants", [])) == 0,
        "4g: Single-price item should not get grid variants"
    )

    # 4h. No grammar key -> skip gracefully
    blocks = [
        {"merged_text": "Some text"},  # no grammar key
        _make_tb("Item1 8.95 11.50",
                 {"line_type": "menu_item", "price_mentions": [8.95, 11.5],
                  "parsed_name": "Item1"}),
    ]
    apply_size_grid_context(blocks)  # should not crash
    report.check(
        len(blocks[1].get("variants", [])) == 0,
        "4h: No grid without size_header -> no variants"
    )


# ── Group 5: Section Heading Expiration ──────────────

def run_heading_expiration_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 5: SECTION HEADING GRID EXPIRATION")
    print("=" * 60)

    for heading in KNOWN_HEADINGS:
        blocks = [
            _make_tb('Regular Deluxe',
                     {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
            _make_tb(heading,
                     {"line_type": "heading", "parsed_name": heading,
                      "size_mentions": [], "price_mentions": []}),
            _make_tb("Item1 8.95 11.50",
                     {"line_type": "menu_item", "price_mentions": [8.95, 11.5],
                      "parsed_name": "Item1"}),
        ]
        apply_size_grid_context(blocks)
        report.check(
            len(blocks[2].get("variants", [])) == 0,
            f"Grid should expire after {heading!r} heading"
        )

    # Non-expiring line types keep grid alive
    for lt in NON_EXPIRING_TYPES:
        blocks = [
            _make_tb('Regular Deluxe',
                     {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
            _make_tb("some text",
                     {"line_type": lt, "size_mentions": [], "parsed_name": "",
                      "price_mentions": []}),
            _make_tb("Item1 8.95 11.50",
                     {"line_type": "menu_item", "price_mentions": [8.95, 11.5],
                      "parsed_name": "Item1"}),
        ]
        apply_size_grid_context(blocks)
        report.check(
            len(blocks[2].get("variants", [])) == 2,
            f"Grid should survive {lt!r} line type"
        )


# ── Group 6: Real OCR Integration ────────────────────

def run_real_ocr_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 6: REAL OCR INTEGRATION")
    print("=" * 60)

    # Test with actual OCR lines through full grammar+grid pipeline
    pizza_lines = [
        "PIZZA",
        '10\u00b0Mini 12" Smt    16"trg Family Size',
        "8 Slices   12 Slices   24 Slices",
        "CHEESE 8.00 11.50 13.95 22.50",
        "PEPPERONI 9.50 12.50 15.50 24.50",
        "MUSHROOM 9.50 12.50 15.50 24.50",
    ]

    # Build text_blocks with grammar
    text_blocks = [{"merged_text": line, "text": line} for line in pizza_lines]
    enrich_grammar_on_text_blocks(text_blocks)

    # Verify grammar detected the size header
    report.check(
        text_blocks[1]["grammar"]["line_type"] == "size_header",
        f"6a: Line 1 should be size_header, got {text_blocks[1]['grammar']['line_type']!r}"
    )

    # Now simulate the price annotation step (normally done by ocr_pipeline)
    for tb in text_blocks:
        prices = tb["grammar"].get("price_mentions", [])
        if len(prices) >= 2:
            tb["price_candidates"] = [
                {"price_cents": int(round(p * 100)), "confidence": 0.85}
                for p in prices
            ]

    # Apply grid context
    apply_size_grid_context(text_blocks)

    # CHEESE should have 4 variants
    cheese_tb = text_blocks[3]
    cheese_variants = cheese_tb.get("variants", [])
    report.check(
        len(cheese_variants) == 4,
        f"6b: CHEESE should have 4 variants, got {len(cheese_variants)}"
    )
    if len(cheese_variants) == 4:
        report.check(
            cheese_variants[0]["price_cents"] == 800,
            f"6b: CHEESE first variant price should be 800, got {cheese_variants[0].get('price_cents')}"
        )
        report.check(
            cheese_variants[3]["price_cents"] == 2250,
            f"6b: CHEESE last variant price should be 2250, got {cheese_variants[3].get('price_cents')}"
        )
        # Labels should contain size info from the header
        report.check(
            '10"' in cheese_variants[0]["label"],
            f"6b: First label should reference 10\", got {cheese_variants[0]['label']!r}"
        )
        report.check(
            "Family" in cheese_variants[3]["label"],
            f"6b: Last label should be Family, got {cheese_variants[3]['label']!r}"
        )

    # PEPPERONI should also have 4 variants
    pepperoni_tb = text_blocks[4]
    report.check(
        len(pepperoni_tb.get("variants", [])) == 4,
        f"6c: PEPPERONI should have 4 variants, got {len(pepperoni_tb.get('variants', []))}"
    )

    # 6d. Enrichment should add kind/normalized_size to grid variants
    enrich_variants_on_text_blocks(text_blocks)
    if cheese_variants:
        report.check(
            cheese_variants[0].get("kind") == "size",
            f"6d: After enrichment, first variant kind should be 'size', got {cheese_variants[0].get('kind')!r}"
        )
        report.check(
            cheese_variants[0].get("normalized_size") is not None,
            f"6d: After enrichment, first variant should have normalized_size"
        )

    # 6e. Burger section with Regular/Deluxe
    burger_lines = [
        "BUILD YOUR OWN BURGER!",
        "Regular Deluxe",
        "Hamburger 8.95 11.50",
        "Cheeseburger 9.50 12.25",
        "Bacon Burger 10.50 13.50",
    ]
    burger_blocks = [{"merged_text": line, "text": line} for line in burger_lines]
    enrich_grammar_on_text_blocks(burger_blocks)

    # Simulate prices
    for tb in burger_blocks:
        prices = tb["grammar"].get("price_mentions", [])
        if len(prices) >= 2:
            tb["price_candidates"] = [
                {"price_cents": int(round(p * 100)), "confidence": 0.85}
                for p in prices
            ]

    apply_size_grid_context(burger_blocks)

    for idx in [2, 3, 4]:
        variants = burger_blocks[idx].get("variants", [])
        report.check(
            len(variants) == 2,
            f"6e: Burger item {idx} should have 2 variants, got {len(variants)}"
        )
        if len(variants) == 2:
            report.check(
                variants[0]["label"] == "Regular",
                f"6e: First variant should be 'Regular', got {variants[0]['label']!r}"
            )
            report.check(
                variants[1]["label"] == "Deluxe",
                f"6e: Second variant should be 'Deluxe', got {variants[1]['label']!r}"
            )

    # 6f. Right-aligned gourmet (3 prices, 4-column grid)
    gourmet_lines = [
        "PIZZA",
        '10"Mini 12" Sml 16"lrg Family Size',
        "GOURMET PIZZA",
        '12" Sml 16"lrg Family Size',
        "BBQ CHICKEN 17.95 25.50 34.75",
    ]
    gourmet_blocks = [{"merged_text": line, "text": line} for line in gourmet_lines]
    enrich_grammar_on_text_blocks(gourmet_blocks)
    for tb in gourmet_blocks:
        prices = tb["grammar"].get("price_mentions", [])
        if len(prices) >= 2:
            tb["price_candidates"] = [
                {"price_cents": int(round(p * 100)), "confidence": 0.85}
                for p in prices
            ]
    apply_size_grid_context(gourmet_blocks)

    bbq = gourmet_blocks[4]
    bbq_variants = bbq.get("variants", [])
    report.check(
        len(bbq_variants) == 3,
        f"6f: BBQ CHICKEN should have 3 variants, got {len(bbq_variants)}"
    )
    if len(bbq_variants) == 3:
        report.check(
            bbq_variants[0]["price_cents"] == 1795,
            f"6f: First price should be 1795, got {bbq_variants[0].get('price_cents')}"
        )
        # Labels come from second grid (3 columns)
        report.check(
            '12"' in bbq_variants[0]["label"],
            f"6f: First label should contain 12\", got {bbq_variants[0]['label']!r}"
        )


# ── Group 7: Regression Guards ───────────────────────

def run_regression_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 7: REGRESSION GUARDS")
    print("=" * 60)

    # 7a. classify_raw_variant still works with shared vocab
    variant_tests = [
        ("Small", "size"), ("Large", "size"), ("10\"", "size"),
        ("6pc", "size"), ("Family", "size"), ("Regular", "size"),
        ("Hot", "flavor"), ("BBQ", "flavor"), ("Honey BBQ", "flavor"),
        ("Thin Crust", "style"), ("Boneless", "style"), ("Deep Dish", "style"),
    ]
    for label, expected_kind in variant_tests:
        v = classify_raw_variant(label, 9.99)
        report.check(
            v.get("kind") == expected_kind,
            f"classify_raw_variant({label!r}).kind = {v.get('kind')!r}, expected {expected_kind!r}"
        )

    # 7b. Items without grammar key are unaffected
    blocks = [
        {"merged_text": "Pepperoni Pizza 14.99", "variants": [
            {"label": "L", "price_cents": 1499, "confidence": 0.9}
        ]},
    ]
    apply_size_grid_context(blocks)
    report.check(
        blocks[0]["variants"][0]["label"] == "L",
        "7b: Existing variant should be unchanged without grammar"
    )

    # 7c. Single-price items never get grid variants
    blocks = [
        _make_tb('Regular Deluxe',
                 {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
        _make_tb("Side Salad 4.99",
                 {"line_type": "menu_item", "price_mentions": [4.99],
                  "parsed_name": "Side Salad"}),
    ]
    apply_size_grid_context(blocks)
    report.check(
        "variants" not in blocks[1] or len(blocks[1].get("variants", [])) == 0,
        "7c: Single-price item should have no variants"
    )

    # 7d. Empty text_blocks
    apply_size_grid_context([])  # should not crash
    report.check(True, "7d: Empty blocks handled gracefully")

    # 7e. Grid meta traceability
    blocks = [
        _make_tb('Regular Deluxe',
                 {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
        _make_tb("Item1 8.95 11.50",
                 {"line_type": "menu_item", "price_mentions": [8.95, 11.5],
                  "parsed_name": "Item1"}),
    ]
    apply_size_grid_context(blocks)
    meta = blocks[1].get("meta", {})
    report.check(
        meta.get("size_grid_applied") is True,
        "7e: meta.size_grid_applied should be True"
    )
    report.check(
        meta.get("size_grid_source") == 0,
        f"7e: meta.size_grid_source should be 0, got {meta.get('size_grid_source')}"
    )

    # 7f. Grid variants override backward-token-walk variants
    blocks = [
        _make_tb('Regular Deluxe',
                 {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
        _make_tb("Item1 8.95 11.50",
                 {"line_type": "menu_item", "price_mentions": [8.95, 11.5],
                  "parsed_name": "Item1"},
                 variants=[
                     {"label": "Item1", "price_cents": 895, "confidence": 0.8},
                     {"label": "11.50", "price_cents": 1150, "confidence": 0.8},
                 ]),
    ]
    apply_size_grid_context(blocks)
    variants = blocks[1].get("variants", [])
    report.check(
        len(variants) == 2 and variants[0]["label"] == "Regular",
        f"7f: Grid should override backward-token-walk, got {[v['label'] for v in variants]}"
    )

    # 7g. Existing enrichment pipeline still works after grid
    blocks = [
        _make_tb('Regular Deluxe',
                 {"line_type": "size_header", "size_mentions": [], "parsed_name": ""}),
        _make_tb("Item1 8.95 11.50",
                 {"line_type": "menu_item", "price_mentions": [8.95, 11.5],
                  "parsed_name": "Item1"}),
    ]
    apply_size_grid_context(blocks)
    enrich_variants_on_text_blocks(blocks)
    variants = blocks[1].get("variants", [])
    if len(variants) == 2:
        report.check(
            variants[0].get("kind") == "size",
            f"7g: Regular should be kind='size', got {variants[0].get('kind')!r}"
        )
        report.check(
            variants[0].get("normalized_size") == "Regular",
            f"7g: Regular normalized_size should be 'Regular', got {variants[0].get('normalized_size')!r}"
        )
        report.check(
            variants[0].get("group_key") == "size:Regular",
            f"7g: Regular group_key should be 'size:Regular', got {variants[0].get('group_key')!r}"
        )

    # 7h. Price fallback: use price_candidates when grammar_prices empty
    grid = SizeGridContext(
        columns=[SizeGridColumn("S", "S", 0), SizeGridColumn("L", "L", 1)],
        source_line_index=0,
    )
    variants = _build_variants_from_grid(
        grid, [],
        [{"price_cents": 899, "confidence": 0.9}, {"price_cents": 1299, "confidence": 0.9}],
        []
    )
    report.check(
        len(variants) == 2 and variants[0]["price_cents"] == 899,
        f"7h: Should fall back to price_candidates, got {len(variants)} variants"
    )

    # 7i. Price fallback: use existing_variants when grammar_prices and candidates empty
    variants = _build_variants_from_grid(
        grid, [], [],
        [{"label": "Old", "price_cents": 799, "confidence": 0.8},
         {"label": "Old2", "price_cents": 1199, "confidence": 0.8}]
    )
    report.check(
        len(variants) == 2 and variants[0]["label"] == "S",
        f"7i: Should use grid labels over old labels, got {[v['label'] for v in variants]}"
    )


# ── Group 8: Full File Regression ────────────────────

def run_full_file_tests(report: TestReport):
    print("\n" + "=" * 60)
    print("GROUP 8: FULL FILE REGRESSION (existing test suites)")
    print("=" * 60)

    # Just verify existing test suites still pass by importing and running key functions
    from storage.parsers.menu_grammar import parse_menu_line, confidence_tier

    # Sample of Day 51-55 baseline cases that should still work
    baseline_cases = [
        ("PEPPERONI PIZZA 14.99", "menu_item"),
        ("PIZZA", "heading"),
        ("BBQ Chicken Pizza 14.99", "menu_item"),
        ("Served with fries", "info_line"),
        ('10"Mini 12" Sml 16"lrg Family Size', "size_header"),
        ("Regular Deluxe", "size_header"),
        ("CHEESE 8.00 11.50 13.95 22.50", "menu_item"),
    ]

    for text, expected_type in baseline_cases:
        result = parse_menu_line(text)
        report.check(
            result.line_type == expected_type,
            f"Regression: {text[:40]!r} should be {expected_type!r}, got {result.line_type!r}"
        )

    # Confidence tier still works
    for score, expected in [(0.85, "high"), (0.70, "medium"), (0.45, "low"), (0.30, "unknown")]:
        tier = confidence_tier(score)
        report.check(
            tier == expected,
            f"Regression: confidence_tier({score}) = {tier!r}, expected {expected!r}"
        )


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════

def main():
    report = TestReport()

    run_vocabulary_tests(report)
    run_column_parse_tests(report)
    run_grid_mapping_tests(report)
    run_context_propagation_tests(report)
    run_heading_expiration_tests(report)
    run_real_ocr_tests(report)
    run_regression_tests(report)
    run_full_file_tests(report)

    print("\n" + "=" * 60)
    print("DAY 56 SPRINT 8.2 RESULTS")
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
