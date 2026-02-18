"""
Day 65: Sprint 8.3 -- Cross-Item Variant Pattern Enforcement

Tests the three new cross-item checks in storage/cross_item.py:
  6. _check_variant_count_consistency() -- variant count vs category mode
  7. _check_variant_label_consistency() -- size label set vs dominant set
  8. _check_variant_price_steps() -- price step vs category median step

Groups:
  1. Variant count consistency (Check 6)
  2. Variant label consistency (Check 7)
  3. Price step consistency (Check 8)
  4. Integration tests (full entry point)
  5. Edge cases

Run: python tests/test_day65_variant_patterns.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.cross_item import (
    check_cross_item_consistency,
    _check_variant_count_consistency,
    _check_variant_label_consistency,
    _check_variant_price_steps,
    _VARIANT_COUNT_MIN_ITEMS,
    _VARIANT_COUNT_MIN_GAP,
    _VARIANT_LABEL_MIN_ITEMS,
    _VARIANT_LABEL_MIN_AGREEMENT,
    _PRICE_STEP_MIN_ITEMS,
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
    normalized_size: Optional[str] = None,
    group_key: Optional[str] = None,
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


def _sml_variants(s_price: int = 899, m_price: int = 1099, l_price: int = 1299):
    """Build standard S/M/L size variants."""
    return [
        _make_variant("S", s_price, "size", 0.80, "S", "size:S"),
        _make_variant("M", m_price, "size", 0.80, "M", "size:M"),
        _make_variant("L", l_price, "size", 0.80, "L", "size:L"),
    ]


def _count_flags(tb: Dict[str, Any], reason: str) -> int:
    return sum(1 for f in tb.get("price_flags", []) if f.get("reason") == reason)


def _get_flag(tb: Dict[str, Any], reason: str) -> Optional[Dict]:
    for f in tb.get("price_flags", []):
        if f.get("reason") == reason:
            return f
    return None


def _init_flags(blocks: List[Dict[str, Any]]) -> None:
    for tb in blocks:
        tb.setdefault("price_flags", [])


# ---------------------------------------------------------------------------
# Group 1: Variant Count Consistency (Check 6)
# ---------------------------------------------------------------------------

def run_variant_count_tests(report: TestReport) -> None:
    print("\n--- Group 1: Variant Count Consistency ---")

    # 1.1: All items same variant count -- no flag
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(4)
    ]
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    report.check("1.1 all same count -- no flag",
                 all(_count_flags(tb, "cross_item_variant_count_outlier") == 0
                     for tb in blocks))

    # 1.2: One item with 2 variants when mode is 4 -- flag (gap=2)
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=[
                     _make_variant("S", 899, "size", 0.80, "S"),
                     _make_variant("M", 1099, "size", 0.80, "M"),
                     _make_variant("L", 1299, "size", 0.80, "L"),
                     _make_variant("XL", 1499, "size", 0.80, "XL"),
                 ])
        for i in range(3)
    ]
    # One item with only 2 variants
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Outlier"}, category="Pizza",
        variants=[
            _make_variant("S", 899, "size", 0.80, "S"),
            _make_variant("M", 1099, "size", 0.80, "M"),
        ],
    ))
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    report.check("1.2 item with 2 vs mode 4 -- flagged (gap=2)",
                 _count_flags(blocks[3], "cross_item_variant_count_outlier") == 1,
                 f"flags: {blocks[3].get('price_flags')}")

    # 1.3: One item with 2 variants when mode is 3 -- no flag (gap=1)
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Short"}, category="Pizza",
        variants=[
            _make_variant("S", 899, "size", 0.80, "S"),
            _make_variant("M", 1099, "size", 0.80, "M"),
        ],
    ))
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    report.check("1.3 item with 2 vs mode 3 -- no flag (gap=1)",
                 _count_flags(blocks[3], "cross_item_variant_count_outlier") == 0,
                 f"flags: {blocks[3].get('price_flags')}")

    # 1.4: Fewer than 3 multi-variant items -- no flag
    blocks = [
        _make_tb(grammar={"parsed_name": "Pizza A"}, category="Pizza",
                 variants=_sml_variants()),
        _make_tb(grammar={"parsed_name": "Pizza B"}, category="Pizza",
                 variants=_sml_variants()),
    ]
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    report.check("1.4 only 2 multi-variant items -- no flag",
                 all(_count_flags(tb, "cross_item_variant_count_outlier") == 0
                     for tb in blocks))

    # 1.5: Items with 0 variants excluded from comparison
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza No Variants"}, category="Pizza",
    ))
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    report.check("1.5 item with 0 variants -- not flagged (excluded)",
                 _count_flags(blocks[3], "cross_item_variant_count_outlier") == 0)

    # 1.6: Items with 1 variant excluded from comparison
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Single"}, category="Pizza",
        variants=[_make_variant("M", 1099, "size", 0.80, "M")],
    ))
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    report.check("1.6 item with 1 variant -- not flagged (excluded)",
                 _count_flags(blocks[3], "cross_item_variant_count_outlier") == 0)

    # 1.7: Two categories, outlier only in one -- correct scoping
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Outlier"}, category="Pizza",
        variants=[_make_variant("S", 899), _make_variant("L", 1299)],
    ))
    # Pasta category -- all have 2 variants (mode=2, no gap)
    for i in range(3):
        blocks.append(_make_tb(
            grammar={"parsed_name": f"Pasta {chr(65+i)}"}, category="Pasta",
            variants=[_make_variant("S", 899), _make_variant("L", 1299)],
        ))
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    # Pizza outlier should NOT be flagged (mode=3 in pizza, gap=1)
    report.check("1.7 pizza outlier gap=1 not flagged",
                 _count_flags(blocks[3], "cross_item_variant_count_outlier") == 0)
    # Pasta items should not be flagged (mode=2, all have 2)
    report.check("1.7b pasta items no flags",
                 all(_count_flags(blocks[i], "cross_item_variant_count_outlier") == 0
                     for i in range(4, 7)))

    # 1.8: Multiple outliers in same category -- both flagged
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=[
                     _make_variant("S", 899, "size", 0.80, "S"),
                     _make_variant("M", 1099, "size", 0.80, "M"),
                     _make_variant("L", 1299, "size", 0.80, "L"),
                     _make_variant("XL", 1499, "size", 0.80, "XL"),
                 ])
        for i in range(4)
    ]
    # Two outliers with only 2 variants
    for label in ["Outlier A", "Outlier B"]:
        blocks.append(_make_tb(
            grammar={"parsed_name": f"Pizza {label}"}, category="Pizza",
            variants=[_make_variant("S", 899), _make_variant("M", 1099)],
        ))
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    report.check("1.8 both outliers flagged",
                 _count_flags(blocks[4], "cross_item_variant_count_outlier") == 1
                 and _count_flags(blocks[5], "cross_item_variant_count_outlier") == 1,
                 f"flags4: {blocks[4].get('price_flags')}, flags5: {blocks[5].get('price_flags')}")

    # 1.9: Flag details are correct
    flag = _get_flag(blocks[4], "cross_item_variant_count_outlier")
    report.check("1.9 flag details correct",
                 flag is not None
                 and flag["details"]["category"] == "Pizza"
                 and flag["details"]["item_variant_count"] == 2
                 and flag["details"]["category_mode_count"] == 4
                 and flag["details"]["category_multi_variant_items"] == 6,
                 f"flag: {flag}")

    # 1.10: Flag severity is info
    report.check("1.10 flag severity is info",
                 flag is not None and flag["severity"] == "info")

    # 1.11: Empty list -- no crash
    try:
        _check_variant_count_consistency([])
        report.ok("1.11 empty list no crash")
    except Exception as e:
        report.fail("1.11 empty list no crash", str(e))

    # 1.12: No category on item -- excluded
    blocks = [
        _make_tb(grammar={"parsed_name": f"Item {i}"}, variants=_sml_variants())
        for i in range(4)
    ]
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    report.check("1.12 no category -- no flags",
                 all(_count_flags(tb, "cross_item_variant_count_outlier") == 0
                     for tb in blocks))

    # 1.13: Mode=5, item=2 -- flagged (gap=3)
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=[
                     _make_variant("XS", 699, "size", 0.80, "XS"),
                     _make_variant("S", 899, "size", 0.80, "S"),
                     _make_variant("M", 1099, "size", 0.80, "M"),
                     _make_variant("L", 1299, "size", 0.80, "L"),
                     _make_variant("XL", 1499, "size", 0.80, "XL"),
                 ])
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Tiny"}, category="Pizza",
        variants=[_make_variant("S", 899), _make_variant("M", 1099)],
    ))
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    report.check("1.13 mode=5 item=2 flagged (gap=3)",
                 _count_flags(blocks[3], "cross_item_variant_count_outlier") == 1)

    # 1.14: Constants have expected values
    report.check("1.14a min_items constant is 3",
                 _VARIANT_COUNT_MIN_ITEMS == 3)
    report.check("1.14b min_gap constant is 2",
                 _VARIANT_COUNT_MIN_GAP == 2)


# ---------------------------------------------------------------------------
# Group 2: Variant Label Consistency (Check 7)
# ---------------------------------------------------------------------------

def run_variant_label_tests(report: TestReport) -> None:
    print("\n--- Group 2: Variant Label Consistency ---")

    # 2.1: All items use {S, M, L} -- no flag
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(4)
    ]
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.1 all same label set -- no flag",
                 all(_count_flags(tb, "cross_item_variant_label_mismatch") == 0
                     for tb in blocks))

    # 2.2: One item uses different labels when others use {S, M, L}
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Inches"}, category="Pizza",
        variants=[
            _make_variant('10"', 899, "size", 0.80, '10"', 'size:10"'),
            _make_variant('16"', 1299, "size", 0.80, '16"', 'size:16"'),
        ],
    ))
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.2 inch labels vs S/M/L -- flagged",
                 _count_flags(blocks[3], "cross_item_variant_label_mismatch") == 1,
                 f"flags: {blocks[3].get('price_flags')}")

    # 2.3: Subset tolerance: {M, L} under dominant {S, M, L} -- no flag
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Gourmet"}, category="Pizza",
        variants=[
            _make_variant("M", 1099, "size", 0.80, "M", "size:M"),
            _make_variant("L", 1299, "size", 0.80, "L", "size:L"),
        ],
    ))
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.3 subset {M,L} of {S,M,L} -- no flag",
                 _count_flags(blocks[3], "cross_item_variant_label_mismatch") == 0,
                 f"flags: {blocks[3].get('price_flags')}")

    # 2.4: Superset tolerance: {S, M, L, XL} under dominant {S, M, L} -- no flag
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Mega"}, category="Pizza",
        variants=[
            _make_variant("S", 899, "size", 0.80, "S", "size:S"),
            _make_variant("M", 1099, "size", 0.80, "M", "size:M"),
            _make_variant("L", 1299, "size", 0.80, "L", "size:L"),
            _make_variant("XL", 1499, "size", 0.80, "XL", "size:XL"),
        ],
    ))
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.4 superset {S,M,L,XL} of {S,M,L} -- no flag",
                 _count_flags(blocks[3], "cross_item_variant_label_mismatch") == 0,
                 f"flags: {blocks[3].get('price_flags')}")

    # 2.5: Disjoint sets: {XS, S} vs dominant {M, L, XL} -- flag
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=[
                     _make_variant("M", 1099, "size", 0.80, "M", "size:M"),
                     _make_variant("L", 1299, "size", 0.80, "L", "size:L"),
                     _make_variant("XL", 1499, "size", 0.80, "XL", "size:XL"),
                 ])
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Tiny"}, category="Pizza",
        variants=[
            _make_variant("XS", 699, "size", 0.80, "XS", "size:XS"),
            _make_variant("S", 899, "size", 0.80, "S", "size:S"),
        ],
    ))
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.5 disjoint {XS,S} vs {M,L,XL} -- flagged",
                 _count_flags(blocks[3], "cross_item_variant_label_mismatch") == 1,
                 f"flags: {blocks[3].get('price_flags')}")

    # 2.6: Fewer than 3 qualifying items -- no flag
    blocks = [
        _make_tb(grammar={"parsed_name": "Pizza A"}, category="Pizza",
                 variants=_sml_variants()),
        _make_tb(grammar={"parsed_name": "Pizza B"}, category="Pizza",
                 variants=[
                     _make_variant('10"', 899, "size", 0.80, '10"'),
                     _make_variant('16"', 1299, "size", 0.80, '16"'),
                 ]),
    ]
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.6 only 2 items -- no flag",
                 all(_count_flags(tb, "cross_item_variant_label_mismatch") == 0
                     for tb in blocks))

    # 2.7: Agreement below 60% -- no flag (too fragmented)
    # 5 items: 2 use {S,M,L}, 2 use {10",16"}, 1 uses {Half,Whole}
    blocks = []
    for i in range(2):
        blocks.append(_make_tb(
            grammar={"parsed_name": f"Pizza SML {i}"}, category="Pizza",
            variants=_sml_variants()))
    for i in range(2):
        blocks.append(_make_tb(
            grammar={"parsed_name": f"Pizza Inch {i}"}, category="Pizza",
            variants=[
                _make_variant('10"', 899, "size", 0.80, '10"'),
                _make_variant('16"', 1299, "size", 0.80, '16"'),
            ]))
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Portion"}, category="Pizza",
        variants=[
            _make_variant("Half", 699, "size", 0.80, "Half"),
            _make_variant("Whole", 1299, "size", 0.80, "Whole"),
        ]))
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.7 fragmented labels (40% max) -- no flag",
                 all(_count_flags(tb, "cross_item_variant_label_mismatch") == 0
                     for tb in blocks))

    # 2.8: Non-size variants excluded (combo, flavor)
    blocks = [
        _make_tb(grammar={"parsed_name": f"Burger {chr(65+i)}"}, category="Burgers",
                 variants=[
                     _make_variant("W/Fries", 1099, "combo", 0.80),
                     _make_variant("W/Chips", 1099, "combo", 0.80),
                 ])
        for i in range(4)
    ]
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.8 combo-only variants -- no flags (non-size excluded)",
                 all(_count_flags(tb, "cross_item_variant_label_mismatch") == 0
                     for tb in blocks))

    # 2.9: Items with <2 size variants excluded
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza One Size"}, category="Pizza",
        variants=[_make_variant("M", 1099, "size", 0.80, "M")],
    ))
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.9 item with 1 size variant -- excluded, no flag",
                 _count_flags(blocks[3], "cross_item_variant_label_mismatch") == 0)

    # 2.10: Flag details correct
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Portions"}, category="Pizza",
        variants=[
            _make_variant("Half", 699, "size", 0.80, "Half"),
            _make_variant("Whole", 1299, "size", 0.80, "Whole"),
        ],
    ))
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    flag = _get_flag(blocks[3], "cross_item_variant_label_mismatch")
    report.check("2.10a flag has item_labels",
                 flag is not None and sorted(flag["details"]["item_labels"]) == ["Half", "Whole"],
                 f"flag: {flag}")
    report.check("2.10b flag has dominant_labels",
                 flag is not None and sorted(flag["details"]["dominant_labels"]) == ["L", "M", "S"],
                 f"flag: {flag}")
    report.check("2.10c flag has dominant_count",
                 flag is not None and flag["details"]["dominant_count"] == 3,
                 f"flag: {flag}")
    report.check("2.10d flag severity is info",
                 flag is not None and flag["severity"] == "info")

    # 2.11: Mixed kind item -- only size variants considered for label set
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    # Item has S/M/L + combo -- size labels match dominant, should be fine
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Combo"}, category="Pizza",
        variants=[
            _make_variant("S", 899, "size", 0.80, "S"),
            _make_variant("M", 1099, "size", 0.80, "M"),
            _make_variant("L", 1299, "size", 0.80, "L"),
            _make_variant("W/Fries", 1399, "combo", 0.80),
        ],
    ))
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.11 mixed kind: size labels match dominant -- no flag",
                 _count_flags(blocks[3], "cross_item_variant_label_mismatch") == 0)

    # 2.12: Variants without normalized_size -- excluded from label set
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Mystery"}, category="Pizza",
        variants=[
            _make_variant("Weird", 899, "size", 0.80),  # no normalized_size
            _make_variant("Odd", 1099, "size", 0.80),   # no normalized_size
        ],
    ))
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    # Item has 0 recognized size labels -> excluded (< 2 size labels)
    report.check("2.12 no normalized_size -- excluded, no flag",
                 _count_flags(blocks[3], "cross_item_variant_label_mismatch") == 0)

    # 2.13: Exactly 60% agreement -- flag fires
    # 5 items: 3 use {S,M,L} (60%), 2 use {10",16"}
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza SML {i}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    for i in range(2):
        blocks.append(_make_tb(
            grammar={"parsed_name": f"Pizza Inch {i}"}, category="Pizza",
            variants=[
                _make_variant('10"', 899, "size", 0.80, '10"'),
                _make_variant('16"', 1299, "size", 0.80, '16"'),
            ]))
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.13 60% agreement -- inch items flagged",
                 _count_flags(blocks[3], "cross_item_variant_label_mismatch") == 1
                 and _count_flags(blocks[4], "cross_item_variant_label_mismatch") == 1,
                 f"flags3: {blocks[3].get('price_flags')}, flags4: {blocks[4].get('price_flags')}")

    # 2.14: Right-aligned single label: {L} under {S, M, L} -- subset, no flag
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    # This item only has 1 size variant -> excluded by len < 2 guard
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Large Only"}, category="Pizza",
        variants=[_make_variant("L", 1299, "size", 0.80, "L")],
    ))
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.14 single variant excluded -- no flag",
                 _count_flags(blocks[3], "cross_item_variant_label_mismatch") == 0)

    # 2.15: Constants have expected values
    report.check("2.15a min_items constant is 3",
                 _VARIANT_LABEL_MIN_ITEMS == 3)
    report.check("2.15b min_agreement constant is 0.60",
                 _VARIANT_LABEL_MIN_AGREEMENT == 0.60)

    # 2.16: Two categories -- mismatch only in one
    pizza_blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    pizza_blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Inches"}, category="Pizza",
        variants=[
            _make_variant('10"', 899, "size", 0.80, '10"'),
            _make_variant('16"', 1299, "size", 0.80, '16"'),
        ],
    ))
    pasta_blocks = [
        _make_tb(grammar={"parsed_name": f"Pasta {chr(65+i)}"}, category="Pasta",
                 variants=[
                     _make_variant("Half", 699, "size", 0.80, "Half"),
                     _make_variant("Whole", 1299, "size", 0.80, "Whole"),
                 ])
        for i in range(3)
    ]
    blocks = pizza_blocks + pasta_blocks
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    report.check("2.16a pizza inch outlier flagged",
                 _count_flags(blocks[3], "cross_item_variant_label_mismatch") == 1)
    report.check("2.16b pasta items no flags",
                 all(_count_flags(blocks[i], "cross_item_variant_label_mismatch") == 0
                     for i in range(4, 7)))


# ---------------------------------------------------------------------------
# Group 3: Price Step Consistency (Check 8)
# ---------------------------------------------------------------------------

def run_price_step_tests(report: TestReport) -> None:
    print("\n--- Group 3: Price Step Consistency ---")

    # 3.1: All items with similar steps -- no flag
    # Each pizza has ~$2 step (S->M: +200, M->L: +200)
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants(899 + i*50, 1099 + i*50, 1299 + i*50))
        for i in range(4)
    ]
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    report.check("3.1 all similar steps -- no flag",
                 all(_count_flags(tb, "cross_item_price_step_outlier") == 0
                     for tb in blocks))

    # 3.2: One item with dramatically larger step -- flag
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())  # $2 step
        for i in range(4)
    ]
    # Add one item with $10 step
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Extreme"}, category="Pizza",
        variants=[
            _make_variant("S", 899, "size", 0.80, "S", "size:S"),
            _make_variant("M", 1899, "size", 0.80, "M", "size:M"),
            _make_variant("L", 2899, "size", 0.80, "L", "size:L"),
        ],
    ))
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    report.check("3.2 item with $10 step vs $2 norm -- flagged",
                 _count_flags(blocks[4], "cross_item_price_step_outlier") == 1,
                 f"flags: {blocks[4].get('price_flags')}")

    # 3.3: One item with dramatically smaller step -- flag
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=[
                     _make_variant("S", 899, "size", 0.80, "S", "size:S"),
                     _make_variant("M", 1399, "size", 0.80, "M", "size:M"),
                     _make_variant("L", 1899, "size", 0.80, "L", "size:L"),
                 ])  # $5 step
        for i in range(4)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Tiny Step"}, category="Pizza",
        variants=[
            _make_variant("S", 899, "size", 0.80, "S", "size:S"),
            _make_variant("M", 909, "size", 0.80, "M", "size:M"),
            _make_variant("L", 919, "size", 0.80, "L", "size:L"),
        ],  # $0.10 step
    ))
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    report.check("3.3 item with $0.10 step vs $5 norm -- flagged",
                 _count_flags(blocks[4], "cross_item_price_step_outlier") == 1,
                 f"flags: {blocks[4].get('price_flags')}")

    # 3.4: Fewer than 3 qualifying items -- no flag
    blocks = [
        _make_tb(grammar={"parsed_name": "Pizza A"}, category="Pizza",
                 variants=_sml_variants()),
        _make_tb(grammar={"parsed_name": "Pizza B"}, category="Pizza",
                 variants=[
                     _make_variant("S", 899, "size", 0.80, "S"),
                     _make_variant("M", 1899, "size", 0.80, "M"),
                     _make_variant("L", 2899, "size", 0.80, "L"),
                 ]),
    ]
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    report.check("3.4 only 2 items -- no flag",
                 all(_count_flags(tb, "cross_item_price_step_outlier") == 0
                     for tb in blocks))

    # 3.5: Items with only 1 size variant -- excluded
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Single"}, category="Pizza",
        variants=[_make_variant("M", 1099, "size", 0.80, "M")],
    ))
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    report.check("3.5 single size variant -- excluded, no flag",
                 _count_flags(blocks[3], "cross_item_price_step_outlier") == 0)

    # 3.6: Items with inversions (negative step) -- skipped
    # All steps negative -> no positive steps -> item excluded
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Inverted"}, category="Pizza",
        variants=[
            _make_variant("S", 1299, "size", 0.80, "S", "size:S"),
            _make_variant("M", 1099, "size", 0.80, "M", "size:M"),
            _make_variant("L", 899, "size", 0.80, "L", "size:L"),
        ],
    ))
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    report.check("3.6 inverted item excluded (no positive steps)",
                 _count_flags(blocks[3], "cross_item_price_step_outlier") == 0)

    # 3.7: Flag details correct
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(4)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Extreme"}, category="Pizza",
        variants=[
            _make_variant("S", 899, "size", 0.80, "S", "size:S"),
            _make_variant("M", 1899, "size", 0.80, "M", "size:M"),
            _make_variant("L", 2899, "size", 0.80, "L", "size:L"),
        ],
    ))
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    flag = _get_flag(blocks[4], "cross_item_price_step_outlier")
    report.check("3.7a flag has category",
                 flag is not None and flag["details"]["category"] == "Pizza",
                 f"flag: {flag}")
    report.check("3.7b flag has item_avg_step_cents",
                 flag is not None and flag["details"]["item_avg_step_cents"] == 1000,
                 f"flag: {flag}")
    report.check("3.7c flag has direction",
                 flag is not None and flag["details"]["direction"] == "above",
                 f"flag: {flag}")
    report.check("3.7d flag severity is info",
                 flag is not None and flag["severity"] == "info")

    # 3.8: Zero-price variants excluded from step calculation
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Zero"}, category="Pizza",
        variants=[
            _make_variant("S", 0, "size", 0.80, "S"),
            _make_variant("M", 1099, "size", 0.80, "M"),
            _make_variant("L", 1299, "size", 0.80, "L"),
        ],
    ))
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    # Only M->L step (200) is valid. That's normal range.
    report.check("3.8 zero-price variant excluded from steps",
                 _count_flags(blocks[3], "cross_item_price_step_outlier") == 0,
                 f"flags: {blocks[3].get('price_flags')}")

    # 3.9: Non-size variants ignored
    blocks = [
        _make_tb(grammar={"parsed_name": f"Burger {chr(65+i)}"}, category="Burgers",
                 variants=[
                     _make_variant("W/Fries", 1099, "combo", 0.80),
                     _make_variant("W/Chips", 1199, "combo", 0.80),
                 ])
        for i in range(4)
    ]
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    report.check("3.9 combo variants -- no step check",
                 all(_count_flags(tb, "cross_item_price_step_outlier") == 0
                     for tb in blocks))

    # 3.10: Two categories, outlier only in one
    pizza_blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    pizza_blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Big Step"}, category="Pizza",
        variants=[
            _make_variant("S", 899, "size", 0.80, "S", "size:S"),
            _make_variant("M", 1899, "size", 0.80, "M", "size:M"),
            _make_variant("L", 2899, "size", 0.80, "L", "size:L"),
        ],
    ))
    pasta_blocks = [
        _make_tb(grammar={"parsed_name": f"Pasta {chr(65+i)}"}, category="Pasta",
                 variants=[
                     _make_variant("Half", 699, "size", 0.80, "Half", "size:Half"),
                     _make_variant("Whole", 1299, "size", 0.80, "Whole", "size:Whole"),
                 ])
        for i in range(3)
    ]
    blocks = pizza_blocks + pasta_blocks
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    report.check("3.10a pizza outlier flagged",
                 _count_flags(blocks[3], "cross_item_price_step_outlier") == 1)
    report.check("3.10b pasta items no step flags",
                 all(_count_flags(blocks[i], "cross_item_price_step_outlier") == 0
                     for i in range(4, 7)))

    # 3.11: Empty list -- no crash
    try:
        _check_variant_price_steps([])
        report.ok("3.11 empty list no crash")
    except Exception as e:
        report.fail("3.11 empty list no crash", str(e))

    # 3.12: All identical steps (MAD=0, uses floor) -- no outliers
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(4)
    ]
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    report.check("3.12 identical steps (MAD=0 uses floor) -- no flag",
                 all(_count_flags(tb, "cross_item_price_step_outlier") == 0
                     for tb in blocks))

    # 3.13: Step with only 2 variants (S=$8, L=$14, step=$6) -- valid contribution
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=[
                     _make_variant("S", 800, "size", 0.80, "S", "size:S"),
                     _make_variant("L", 1400, "size", 0.80, "L", "size:L"),
                 ])
        for i in range(3)
    ]
    # All have step of 600 -- no outlier
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    report.check("3.13 two-variant items contribute -- no flag when consistent",
                 all(_count_flags(tb, "cross_item_price_step_outlier") == 0
                     for tb in blocks))

    # 3.14: Constants have expected values
    report.check("3.14 min_items constant is 3",
                 _PRICE_STEP_MIN_ITEMS == 3)

    # 3.15: Direction below in flag details
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=[
                     _make_variant("S", 899, "size", 0.80, "S", "size:S"),
                     _make_variant("M", 1399, "size", 0.80, "M", "size:M"),
                     _make_variant("L", 1899, "size", 0.80, "L", "size:L"),
                 ])
        for i in range(4)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Tiny Step"}, category="Pizza",
        variants=[
            _make_variant("S", 899, "size", 0.80, "S", "size:S"),
            _make_variant("M", 909, "size", 0.80, "M", "size:M"),
            _make_variant("L", 919, "size", 0.80, "L", "size:L"),
        ],
    ))
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    flag = _get_flag(blocks[4], "cross_item_price_step_outlier")
    report.check("3.15 direction is below for small step outlier",
                 flag is not None and flag["details"]["direction"] == "below",
                 f"flag: {flag}")

    # 3.16: No category on items -- excluded
    blocks = [
        _make_tb(grammar={"parsed_name": f"Item {i}"},
                 variants=_sml_variants())
        for i in range(4)
    ]
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    report.check("3.16 no category -- no flags",
                 all(_count_flags(tb, "cross_item_price_step_outlier") == 0
                     for tb in blocks))


# ---------------------------------------------------------------------------
# Group 4: Integration Tests
# ---------------------------------------------------------------------------

def run_integration_tests(report: TestReport) -> None:
    print("\n--- Group 4: Integration Tests ---")

    # 4.1: Full entry point wires all three new checks
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=[
                     _make_variant("S", 899, "size", 0.80, "S", "size:S"),
                     _make_variant("M", 1099, "size", 0.80, "M", "size:M"),
                     _make_variant("L", 1299, "size", 0.80, "L", "size:L"),
                     _make_variant("XL", 1499, "size", 0.80, "XL", "size:XL"),
                 ])
        for i in range(4)
    ]
    # Outlier: 2 variants (count outlier gap=2), wrong labels (label mismatch),
    # big step (step outlier: XS->XXL = $10 vs ~$2 norm)
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Weird"}, category="Pizza",
        variants=[
            _make_variant("XS", 499, "size", 0.80, "XS", "size:XS"),
            _make_variant("XXL", 1499, "size", 0.80, "XXL", "size:XXL"),
        ],
    ))
    check_cross_item_consistency(blocks)

    report.check("4.1a count outlier via entry point",
                 _count_flags(blocks[4], "cross_item_variant_count_outlier") == 1,
                 f"flags: {blocks[4].get('price_flags')}")
    report.check("4.1b label mismatch via entry point",
                 _count_flags(blocks[4], "cross_item_variant_label_mismatch") == 1,
                 f"flags: {blocks[4].get('price_flags')}")
    # Step: XS->XXL = 1000. Other items have ~200 step.
    report.check("4.1c step outlier via entry point",
                 _count_flags(blocks[4], "cross_item_price_step_outlier") == 1,
                 f"flags: {blocks[4].get('price_flags')}")

    # 4.2: New flags coexist with existing flags
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(4)
    ]
    # Add outlier with pre-existing flag
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Special"}, category="Pizza",
        variants=[
            _make_variant('10"', 899, "size", 0.80, '10"'),
            _make_variant('16"', 2899, "size", 0.80, '16"'),
        ],
        price_flags=[{"severity": "warn", "reason": "variant_price_inversion", "details": {}}],
    ))
    check_cross_item_consistency(blocks)

    # Pre-existing flag should still be there
    inversion_count = _count_flags(blocks[4], "variant_price_inversion")
    new_count = _count_flags(blocks[4], "cross_item_variant_label_mismatch")
    report.check("4.2 pre-existing flags preserved alongside new flags",
                 inversion_count == 1 and new_count == 1,
                 f"inversion={inversion_count}, label_mismatch={new_count}")

    # 4.3: All clean items -- zero new flags
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(4)
    ]
    check_cross_item_consistency(blocks)
    new_reasons = ["cross_item_variant_count_outlier",
                   "cross_item_variant_label_mismatch",
                   "cross_item_price_step_outlier"]
    total_new = sum(_count_flags(tb, r) for tb in blocks for r in new_reasons)
    report.check("4.3 clean items -- zero Day 65 flags",
                 total_new == 0,
                 f"total new flags: {total_new}")

    # 4.4: ai_ocr_helper path compatibility (name + price_cents, no grammar)
    blocks = [
        _make_tb(name=f"Pizza {chr(65+i)}", category="Pizza",
                 variants=[
                     _make_variant("S", 899, "size", 0.80, "S"),
                     _make_variant("M", 1099, "size", 0.80, "M"),
                     _make_variant("L", 1299, "size", 0.80, "L"),
                 ])
        for i in range(3)
    ]
    blocks.append(_make_tb(
        name="Pizza Outlier", category="Pizza",
        variants=[
            _make_variant('10"', 899, "size", 0.80, '10"'),
            _make_variant('16"', 2899, "size", 0.80, '16"'),
        ],
    ))
    check_cross_item_consistency(blocks)
    report.check("4.4 ai_ocr_helper path -- label mismatch detected",
                 _count_flags(blocks[3], "cross_item_variant_label_mismatch") == 1,
                 f"flags: {blocks[3].get('price_flags')}")

    # 4.5: Empty blocks -- no crash
    try:
        check_cross_item_consistency([])
        report.ok("4.5 empty blocks no crash")
    except Exception as e:
        report.fail("4.5 empty blocks no crash", str(e))

    # 4.6: Item can receive multiple Day 65 flags simultaneously
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=[
                     _make_variant("S", 899, "size", 0.80, "S", "size:S"),
                     _make_variant("M", 1099, "size", 0.80, "M", "size:M"),
                     _make_variant("L", 1299, "size", 0.80, "L", "size:L"),
                     _make_variant("XL", 1499, "size", 0.80, "XL", "size:XL"),
                 ])
        for i in range(4)
    ]
    # This item: wrong count (2 vs 4, gap=2), wrong labels ({XS,XXL} vs {S,M,L,XL}),
    # wrong step (1000 vs ~200)
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Triple Outlier"}, category="Pizza",
        variants=[
            _make_variant("XS", 499, "size", 0.80, "XS", "size:XS"),
            _make_variant("XXL", 1499, "size", 0.80, "XXL", "size:XXL"),
        ],
    ))
    check_cross_item_consistency(blocks)
    count_f = _count_flags(blocks[4], "cross_item_variant_count_outlier")
    label_f = _count_flags(blocks[4], "cross_item_variant_label_mismatch")
    step_f = _count_flags(blocks[4], "cross_item_price_step_outlier")
    report.check("4.6 item has all 3 Day 65 flags",
                 count_f == 1 and label_f == 1 and step_f == 1,
                 f"count={count_f}, label={label_f}, step={step_f}")


# ---------------------------------------------------------------------------
# Group 5: Edge Cases
# ---------------------------------------------------------------------------

def run_edge_case_tests(report: TestReport) -> None:
    print("\n--- Group 5: Edge Cases ---")

    # 5.1: Category with exactly 3 qualifying items (minimum threshold)
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=[
                     _make_variant("S", 899, "size", 0.80, "S", "size:S"),
                     _make_variant("M", 1099, "size", 0.80, "M", "size:M"),
                     _make_variant("L", 1299, "size", 0.80, "L", "size:L"),
                     _make_variant("XL", 1499, "size", 0.80, "XL", "size:XL"),
                 ])
        for i in range(3)
    ]
    # Outlier with 2 variants
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Min"}, category="Pizza",
        variants=[_make_variant("S", 899, "size"), _make_variant("M", 1099, "size")],
    ))
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    report.check("5.1 exactly 3 qualifying items + 1 outlier -- flag fires",
                 _count_flags(blocks[3], "cross_item_variant_count_outlier") == 1)

    # 5.2: Large category (20+ items) -- no performance issue
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {i:03d}"}, category="Pizza",
                 variants=_sml_variants(899 + i, 1099 + i, 1299 + i))
        for i in range(25)
    ]
    _init_flags(blocks)
    try:
        _check_variant_count_consistency(blocks)
        _check_variant_label_consistency(blocks)
        _check_variant_price_steps(blocks)
        report.ok("5.2 25 items no crash or hang")
    except Exception as e:
        report.fail("5.2 25 items no crash or hang", str(e))

    # 5.3: All items no variants -- all checks skip
    blocks = [
        _make_tb(grammar={"parsed_name": f"Item {i}"}, category="Pizza")
        for i in range(5)
    ]
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    _check_variant_label_consistency(blocks)
    _check_variant_price_steps(blocks)
    new_reasons = ["cross_item_variant_count_outlier",
                   "cross_item_variant_label_mismatch",
                   "cross_item_price_step_outlier"]
    total = sum(_count_flags(tb, r) for tb in blocks for r in new_reasons)
    report.check("5.3 no variants -- zero Day 65 flags", total == 0)

    # 5.4: Item with variants but no category -- excluded
    blocks = [
        _make_tb(grammar={"parsed_name": f"Mystery {i}"},
                 variants=_sml_variants())
        for i in range(5)
    ]
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    _check_variant_label_consistency(blocks)
    _check_variant_price_steps(blocks)
    total = sum(_count_flags(tb, r) for tb in blocks for r in new_reasons)
    report.check("5.4 no category -- zero flags", total == 0)

    # 5.5: Variant with kind=None -- treated as non-size
    blocks = [
        _make_tb(grammar={"parsed_name": f"Item {i}"}, category="Pizza",
                 variants=[
                     _make_variant("A", 899, None, 0.80),
                     _make_variant("B", 1099, None, 0.80),
                 ])
        for i in range(4)
    ]
    _init_flags(blocks)
    _check_variant_label_consistency(blocks)
    _check_variant_price_steps(blocks)
    total = sum(_count_flags(tb, "cross_item_variant_label_mismatch") +
                _count_flags(tb, "cross_item_price_step_outlier")
                for tb in blocks)
    report.check("5.5 kind=None -- excluded from label/step checks", total == 0)

    # 5.6: Category with only combo variants -- count check works, size checks skip
    blocks = [
        _make_tb(grammar={"parsed_name": f"Burger {chr(65+i)}"}, category="Burgers",
                 variants=[
                     _make_variant("W/Fries", 1099, "combo", 0.80),
                     _make_variant("W/Chips", 1199, "combo", 0.80),
                     _make_variant("W/Salad", 1299, "combo", 0.80),
                 ])
        for i in range(3)
    ]
    blocks.append(_make_tb(
        grammar={"parsed_name": "Burger Outlier"}, category="Burgers",
        variants=[
            _make_variant("W/Fries", 1099, "combo", 0.80),
        ],
    ))
    _init_flags(blocks)
    _check_variant_count_consistency(blocks)
    _check_variant_label_consistency(blocks)
    _check_variant_price_steps(blocks)
    # Count check: mode=3, outlier has 1 variant (excluded, < 2 variants)
    # So no count outlier either
    report.check("5.6 combo-only: single-variant item excluded from count check",
                 _count_flags(blocks[3], "cross_item_variant_count_outlier") == 0)
    report.check("5.6b combo-only: no label/step flags",
                 all(_count_flags(tb, "cross_item_variant_label_mismatch") == 0
                     and _count_flags(tb, "cross_item_price_step_outlier") == 0
                     for tb in blocks))

    # 5.7: Real-world scenario -- pizza menu with one odd item
    blocks = []
    pizza_names = ["Cheese", "Pepperoni", "Supreme", "Veggie",
                   "Margherita", "Hawaiian", "BBQ Chicken"]
    for name in pizza_names:
        blocks.append(_make_tb(
            grammar={"parsed_name": f"{name} Pizza"}, category="Pizza",
            variants=[
                _make_variant("S", 899, "size", 0.80, "S", "size:S"),
                _make_variant("M", 1099, "size", 0.80, "M", "size:M"),
                _make_variant("L", 1299, "size", 0.80, "L", "size:L"),
            ],
        ))
    # One pizza with inch sizes -- 2 variants vs mode 3 (gap=1, below threshold)
    # but label mismatch should fire (inches vs S/M/L)
    blocks.append(_make_tb(
        grammar={"parsed_name": "Specialty Pizza"}, category="Pizza",
        variants=[
            _make_variant('12"', 1099, "size", 0.80, '12"', 'size:12"'),
            _make_variant('18"', 2099, "size", 0.80, '18"', 'size:18"'),
        ],
    ))
    check_cross_item_consistency(blocks)
    # Count outlier: gap=1 (3 vs 2), below threshold of 2. No count flag.
    report.check("5.7a real scenario: no count outlier (gap=1 < threshold 2)",
                 _count_flags(blocks[7], "cross_item_variant_count_outlier") == 0,
                 f"flags: {blocks[7].get('price_flags')}")
    report.check("5.7b real scenario: label mismatch on specialty pizza",
                 _count_flags(blocks[7], "cross_item_variant_label_mismatch") == 1,
                 f"flags: {blocks[7].get('price_flags')}")

    # 5.8: Mixed tracks within single item -- each track contributes independently
    blocks = [
        _make_tb(grammar={"parsed_name": f"Pizza {chr(65+i)}"}, category="Pizza",
                 variants=_sml_variants())
        for i in range(3)
    ]
    # Item with both word and inch tracks
    blocks.append(_make_tb(
        grammar={"parsed_name": "Pizza Mixed"}, category="Pizza",
        variants=[
            _make_variant("S", 899, "size", 0.80, "S", "size:S"),
            _make_variant("L", 1299, "size", 0.80, "L", "size:L"),
            _make_variant('10"', 999, "size", 0.80, '10"', 'size:10"'),
            _make_variant('16"', 1499, "size", 0.80, '16"', 'size:16"'),
        ],
    ))
    _init_flags(blocks)
    _check_variant_price_steps(blocks)
    # Word track step for mixed item: S->L = 400 (no M, so single step)
    # Other items: S->M=200, M->L=200, avg=200
    # 400 vs median 200 -- may or may not exceed threshold depending on MAD
    # The key test is it doesn't crash
    report.check("5.8 mixed tracks in one item -- no crash",
                 True)  # just testing no exception


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main() -> None:
    report = TestReport()

    run_variant_count_tests(report)
    run_variant_label_tests(report)
    run_price_step_tests(report)
    run_integration_tests(report)
    run_edge_case_tests(report)

    print(f"\n{'='*60}")
    print(f"Day 65 Results: {report.passed}/{report.total} passed")

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
