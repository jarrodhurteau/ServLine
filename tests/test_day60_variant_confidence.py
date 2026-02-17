# tests/test_day60_variant_confidence.py
"""
Day 60: Sprint 8.2 -- Variant Confidence Scoring

Tests:
  1. Label clarity scoring (size/combo/flavor/style/other/empty)
  2. Grammar context modifiers (high/medium/low/missing)
  3. Grid context boost (grid-applied vs not)
  4. Price flag penalties (inversions, duplicates, zero, mixed, gaps)
  5. Combined scoring and clamping
  6. Pipeline integration (enrichment + validation + scoring)
  7. ai_ocr_helper path compatibility
  8. Edge cases and regressions

Run: python tests/test_day60_variant_confidence.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.variant_engine import (
    score_variant_confidence,
    _score_single_variant,
    _variant_in_inversion,
    _variant_is_duplicate,
    enrich_variants_on_text_blocks,
    validate_variant_prices,
    check_variant_consistency,
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
    meta: Optional[Dict] = None,
    price_flags: Optional[List[Dict]] = None,
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
    if meta is not None:
        tb["meta"] = meta
    if price_flags is not None:
        tb["price_flags"] = price_flags
    return tb


def _make_variant(
    label: str = "Small",
    price_cents: int = 1099,
    confidence: float = 0.85,
    kind: Optional[str] = None,
    normalized_size: Optional[str] = None,
    group_key: Optional[str] = None,
    kind_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a minimal variant dict for testing."""
    v: Dict[str, Any] = {
        "label": label,
        "price_cents": price_cents,
        "confidence": confidence,
    }
    if kind is not None:
        v["kind"] = kind
    if normalized_size is not None:
        v["normalized_size"] = normalized_size
    if group_key is not None:
        v["group_key"] = group_key
    if kind_hint is not None:
        v["kind_hint"] = kind_hint
    return v


# ==================================================================
# Group 1: Label Clarity Scoring
# ==================================================================

def run_label_clarity_tests(report: TestReport) -> None:
    """Test label clarity bonus/penalty based on variant kind."""

    # 1.1: Size with normalized_size -> +0.05
    v = _make_variant(kind="size", normalized_size="S")
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("1.1 size+normalized -> +0.05",
                 d["label_mod"] == 0.05,
                 f"got label_mod={d['label_mod']}")

    # 1.2: Combo -> +0.03
    v = _make_variant(label="W/Fries", kind="combo")
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("1.2 combo -> +0.03",
                 d["label_mod"] == 0.03,
                 f"got label_mod={d['label_mod']}")

    # 1.3: Flavor -> +0.02
    v = _make_variant(label="Hot", kind="flavor")
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("1.3 flavor -> +0.02",
                 d["label_mod"] == 0.02,
                 f"got label_mod={d['label_mod']}")

    # 1.4: Style -> +0.02
    v = _make_variant(label="Thin Crust", kind="style")
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("1.4 style -> +0.02",
                 d["label_mod"] == 0.02,
                 f"got label_mod={d['label_mod']}")

    # 1.5: "other" kind -> -0.10
    v = _make_variant(label="Mystery", kind="other")
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("1.5 other -> -0.10",
                 d["label_mod"] == -0.10,
                 f"got label_mod={d['label_mod']}")

    # 1.6: Empty label -> -0.20
    v = _make_variant(label="", kind="size", normalized_size="S")
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("1.6 empty label -> -0.20",
                 d["label_mod"] == -0.20,
                 f"got label_mod={d['label_mod']}")

    # 1.7: Whitespace-only label -> -0.20
    v = _make_variant(label="   ", kind="size")
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("1.7 whitespace label -> -0.20",
                 d["label_mod"] == -0.20,
                 f"got label_mod={d['label_mod']}")

    # 1.8: Size kind but no normalized_size -> 0.0
    v = _make_variant(label="Big", kind="size")
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("1.8 size no normalized -> 0.0",
                 d["label_mod"] == 0.0,
                 f"got label_mod={d['label_mod']}")

    # 1.9: No kind field -> defaults to "other" -> -0.10
    v = _make_variant(label="Something")
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("1.9 no kind -> other -> -0.10",
                 d["label_mod"] == -0.10,
                 f"got label_mod={d['label_mod']}")

    # 1.10: S/M/L sizes all get same bonus
    for size_label, ns in [("S", "S"), ("M", "M"), ("L", "L")]:
        v = _make_variant(label=size_label, kind="size", normalized_size=ns)
        tb = _make_tb(variants=[v])
        d = _score_single_variant(v, tb)
        report.check(f"1.10 {size_label} size -> +0.05",
                     d["label_mod"] == 0.05,
                     f"{size_label}: got label_mod={d['label_mod']}")

    # 1.11: Inch sizes get bonus
    v = _make_variant(label='10"', kind="size", normalized_size='10"')
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("1.11 inch size -> +0.05",
                 d["label_mod"] == 0.05,
                 f"got label_mod={d['label_mod']}")

    # 1.12: Piece count sizes get bonus
    v = _make_variant(label="6 PC", kind="size", normalized_size="6pc")
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("1.12 piece size -> +0.05",
                 d["label_mod"] == 0.05,
                 f"got label_mod={d['label_mod']}")

    # 1.13: Multiple variants scored independently
    v1 = _make_variant(label="Small", kind="size", normalized_size="S")
    v2 = _make_variant(label="Unknown", kind="other")
    tb = _make_tb(variants=[v1, v2])
    d1 = _score_single_variant(v1, tb)
    d2 = _score_single_variant(v2, tb)
    report.check("1.13 independent scoring",
                 d1["label_mod"] == 0.05 and d2["label_mod"] == -0.10,
                 f"got v1={d1['label_mod']}, v2={d2['label_mod']}")


# ==================================================================
# Group 2: Grammar Context Modifiers
# ==================================================================

def run_grammar_context_tests(report: TestReport) -> None:
    """Test grammar parse_confidence influence on variant scoring."""

    base_v = _make_variant(kind="size", normalized_size="S")

    # 2.1: High grammar (0.90) -> +0.03
    tb = _make_tb(grammar={"parse_confidence": 0.90}, variants=[base_v])
    d = _score_single_variant(base_v, tb)
    report.check("2.1 grammar 0.90 -> +0.03",
                 d["grammar_mod"] == 0.03,
                 f"got grammar_mod={d['grammar_mod']}")

    # 2.2: Medium grammar (0.65) -> 0.0
    tb = _make_tb(grammar={"parse_confidence": 0.65}, variants=[base_v])
    d = _score_single_variant(base_v, tb)
    report.check("2.2 grammar 0.65 -> 0.0",
                 d["grammar_mod"] == 0.0,
                 f"got grammar_mod={d['grammar_mod']}")

    # 2.3: Low grammar (0.40) -> penalty
    tb = _make_tb(grammar={"parse_confidence": 0.40}, variants=[base_v])
    d = _score_single_variant(base_v, tb)
    expected = round(-0.10 * (1.0 - 0.40 / 0.50), 4)  # -0.02
    report.check("2.3 grammar 0.40 -> penalty",
                 d["grammar_mod"] == expected,
                 f"got grammar_mod={d['grammar_mod']}, expected={expected}")

    # 2.4: Very low grammar (0.20) -> stronger penalty
    tb = _make_tb(grammar={"parse_confidence": 0.20}, variants=[base_v])
    d = _score_single_variant(base_v, tb)
    expected = round(-0.10 * (1.0 - 0.20 / 0.50), 4)  # -0.06
    report.check("2.4 grammar 0.20 -> -0.06",
                 d["grammar_mod"] == expected,
                 f"got grammar_mod={d['grammar_mod']}, expected={expected}")

    # 2.5: Missing grammar -> 0.0
    tb = _make_tb(variants=[base_v])
    d = _score_single_variant(base_v, tb)
    report.check("2.5 no grammar -> 0.0",
                 d["grammar_mod"] == 0.0,
                 f"got grammar_mod={d['grammar_mod']}")

    # 2.6: Grammar exactly 0.50 boundary -> 0.0 (not < 0.50)
    tb = _make_tb(grammar={"parse_confidence": 0.50}, variants=[base_v])
    d = _score_single_variant(base_v, tb)
    report.check("2.6 grammar 0.50 boundary -> 0.0",
                 d["grammar_mod"] == 0.0,
                 f"got grammar_mod={d['grammar_mod']}")

    # 2.7: Grammar exactly 0.80 boundary -> +0.03
    tb = _make_tb(grammar={"parse_confidence": 0.80}, variants=[base_v])
    d = _score_single_variant(base_v, tb)
    report.check("2.7 grammar 0.80 boundary -> +0.03",
                 d["grammar_mod"] == 0.03,
                 f"got grammar_mod={d['grammar_mod']}")

    # 2.8: Grammar 1.0 -> +0.03
    tb = _make_tb(grammar={"parse_confidence": 1.0}, variants=[base_v])
    d = _score_single_variant(base_v, tb)
    report.check("2.8 grammar 1.0 -> +0.03",
                 d["grammar_mod"] == 0.03,
                 f"got grammar_mod={d['grammar_mod']}")

    # 2.9: Grammar dict present but no parse_confidence -> 0.0
    tb = _make_tb(grammar={"line_type": "menu_item"}, variants=[base_v])
    d = _score_single_variant(base_v, tb)
    report.check("2.9 grammar no parse_confidence -> 0.0",
                 d["grammar_mod"] == 0.0,
                 f"got grammar_mod={d['grammar_mod']}")

    # 2.10: Grammar confidence 0.0 -> max penalty -0.10
    tb = _make_tb(grammar={"parse_confidence": 0.0}, variants=[base_v])
    d = _score_single_variant(base_v, tb)
    report.check("2.10 grammar 0.0 -> -0.10",
                 d["grammar_mod"] == -0.10,
                 f"got grammar_mod={d['grammar_mod']}")

    # 2.11: Grammar modifier stacks with label modifier
    v = _make_variant(kind="combo", label="W/Fries")
    tb = _make_tb(grammar={"parse_confidence": 0.90}, variants=[v])
    d = _score_single_variant(v, tb)
    report.check("2.11 grammar + label stack",
                 d["grammar_mod"] == 0.03 and d["label_mod"] == 0.03,
                 f"grammar_mod={d['grammar_mod']}, label_mod={d['label_mod']}")

    # 2.12: Grammar 0.49 -> small penalty (just below 0.50)
    tb = _make_tb(grammar={"parse_confidence": 0.49}, variants=[base_v])
    d = _score_single_variant(base_v, tb)
    report.check("2.12 grammar 0.49 -> small penalty",
                 d["grammar_mod"] < 0,
                 f"got grammar_mod={d['grammar_mod']}")


# ==================================================================
# Group 3: Grid Context Boost
# ==================================================================

def run_grid_context_tests(report: TestReport) -> None:
    """Test size_grid_applied boost."""

    base_v = _make_variant(kind="size", normalized_size="S")

    # 3.1: Grid applied -> +0.05
    tb = _make_tb(variants=[base_v], meta={"size_grid_applied": True})
    d = _score_single_variant(base_v, tb)
    report.check("3.1 grid applied -> +0.05",
                 d["grid_mod"] == 0.05,
                 f"got grid_mod={d['grid_mod']}")

    # 3.2: No grid -> 0.0
    tb = _make_tb(variants=[base_v])
    d = _score_single_variant(base_v, tb)
    report.check("3.2 no grid -> 0.0",
                 d["grid_mod"] == 0.0,
                 f"got grid_mod={d['grid_mod']}")

    # 3.3: Missing meta key -> 0.0
    tb = _make_tb(variants=[base_v])
    d = _score_single_variant(base_v, tb)
    report.check("3.3 no meta -> 0.0",
                 d["grid_mod"] == 0.0,
                 f"got grid_mod={d['grid_mod']}")

    # 3.4: meta present but size_grid_applied=False -> 0.0
    tb = _make_tb(variants=[base_v], meta={"size_grid_applied": False})
    d = _score_single_variant(base_v, tb)
    report.check("3.4 grid False -> 0.0",
                 d["grid_mod"] == 0.0,
                 f"got grid_mod={d['grid_mod']}")

    # 3.5: Grid boost stacks with label bonus
    tb = _make_tb(variants=[base_v], meta={"size_grid_applied": True})
    d = _score_single_variant(base_v, tb)
    report.check("3.5 grid + label stack",
                 d["grid_mod"] == 0.05 and d["label_mod"] == 0.05,
                 f"grid_mod={d['grid_mod']}, label_mod={d['label_mod']}")

    # 3.6: Grid boost stacks with grammar
    tb = _make_tb(variants=[base_v],
                  grammar={"parse_confidence": 0.85},
                  meta={"size_grid_applied": True})
    d = _score_single_variant(base_v, tb)
    report.check("3.6 grid + grammar stack",
                 d["grid_mod"] == 0.05 and d["grammar_mod"] == 0.03,
                 f"grid_mod={d['grid_mod']}, grammar_mod={d['grammar_mod']}")

    # 3.7: confidence_details contains grid_mod
    report.check("3.7 details has grid_mod",
                 "grid_mod" in d,
                 f"keys={list(d.keys())}")

    # 3.8: Empty meta dict -> 0.0
    tb = _make_tb(variants=[base_v], meta={})
    d = _score_single_variant(base_v, tb)
    report.check("3.8 empty meta -> 0.0",
                 d["grid_mod"] == 0.0,
                 f"got grid_mod={d['grid_mod']}")


# ==================================================================
# Group 4: Price Flag Penalties
# ==================================================================

def run_price_flag_tests(report: TestReport) -> None:
    """Test targeted price flag penalty application."""

    # 4.1: Variant involved in inversion -> -0.12
    v = _make_variant(label="Small", kind="size", normalized_size="S")
    flag = {
        "severity": "warn",
        "reason": "variant_price_inversion",
        "details": {
            "inversions": [
                {"smaller_size": "S", "smaller_price_cents": 1200,
                 "larger_size": "M", "larger_price_cents": 1000}
            ]
        },
    }
    tb = _make_tb(variants=[v], price_flags=[flag])
    d = _score_single_variant(v, tb)
    report.check("4.1 inversion penalty -> -0.12",
                 d["flag_penalty"] == -0.12,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.2: Variant NOT in inversion -> 0.0
    v2 = _make_variant(label="Large", kind="size", normalized_size="L")
    tb = _make_tb(variants=[v2], price_flags=[flag])
    d = _score_single_variant(v2, tb)
    report.check("4.2 not in inversion -> 0.0",
                 d["flag_penalty"] == 0.0,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.3: Duplicate variant -> -0.15
    v = _make_variant(label="Small", kind="size", group_key="size:S")
    dup_flag = {
        "severity": "warn",
        "reason": "duplicate_variant",
        "details": {"duplicated_keys": ["size:S"], "variant_count": 3},
    }
    tb = _make_tb(variants=[v], price_flags=[dup_flag])
    d = _score_single_variant(v, tb)
    report.check("4.3 duplicate -> -0.15",
                 d["flag_penalty"] == -0.15,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.4: Non-duplicate variant on same block -> 0.0
    v2 = _make_variant(label="Large", kind="size", group_key="size:L")
    tb = _make_tb(variants=[v2], price_flags=[dup_flag])
    d = _score_single_variant(v2, tb)
    report.check("4.4 non-duplicate -> 0.0",
                 d["flag_penalty"] == 0.0,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.5: Zero-price variant -> -0.20
    v = _make_variant(label="Small", price_cents=0, kind="size")
    zero_flag = {
        "severity": "warn",
        "reason": "zero_price_variant",
        "details": {"zero_labels": ["Small"], "nonzero_count": 2},
    }
    tb = _make_tb(variants=[v], price_flags=[zero_flag])
    d = _score_single_variant(v, tb)
    report.check("4.5 zero price -> -0.20",
                 d["flag_penalty"] == -0.20,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.6: Nonzero variant on block with zero_price flag -> 0.0
    v2 = _make_variant(label="Large", price_cents=1499, kind="size")
    tb = _make_tb(variants=[v2], price_flags=[zero_flag])
    d = _score_single_variant(v2, tb)
    report.check("4.6 nonzero on zero-flag block -> 0.0",
                 d["flag_penalty"] == 0.0,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.7: Mixed kinds severity=warn -> -0.05
    mixed_flag = {
        "severity": "warn",
        "reason": "mixed_variant_kinds",
        "details": {"kinds_found": ["size", "combo", "flavor"], "variant_count": 4},
    }
    v = _make_variant(kind="size", normalized_size="S")
    tb = _make_tb(variants=[v], price_flags=[mixed_flag])
    d = _score_single_variant(v, tb)
    report.check("4.7 mixed kinds warn -> -0.05",
                 d["flag_penalty"] == -0.05,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.8: Mixed kinds severity=info -> 0.0
    mixed_info = {
        "severity": "info",
        "reason": "mixed_variant_kinds",
        "details": {"kinds_found": ["size", "combo"], "variant_count": 3},
    }
    tb = _make_tb(variants=[v], price_flags=[mixed_info])
    d = _score_single_variant(v, tb)
    report.check("4.8 mixed kinds info -> 0.0",
                 d["flag_penalty"] == 0.0,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.9: Size gap -> -0.03
    gap_flag = {
        "severity": "info",
        "reason": "size_gap",
        "details": {"track": "word", "missing_sizes": ["M"]},
    }
    tb = _make_tb(variants=[v], price_flags=[gap_flag])
    d = _score_single_variant(v, tb)
    report.check("4.9 size gap -> -0.03",
                 d["flag_penalty"] == -0.03,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.10: Grid incomplete -> -0.03
    inc_flag = {
        "severity": "info",
        "reason": "grid_incomplete",
        "details": {"grid_column_count": 4, "variant_count": 2, "missing_columns": 2},
    }
    tb = _make_tb(variants=[v], price_flags=[inc_flag])
    d = _score_single_variant(v, tb)
    report.check("4.10 grid incomplete -> -0.03",
                 d["flag_penalty"] == -0.03,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.11: Grid count outlier -> -0.03
    out_flag = {
        "severity": "info",
        "reason": "grid_count_outlier",
        "details": {"grid_source_line": 5, "variant_count": 1, "mode_count": 4},
    }
    tb = _make_tb(variants=[v], price_flags=[out_flag])
    d = _score_single_variant(v, tb)
    report.check("4.11 grid outlier -> -0.03",
                 d["flag_penalty"] == -0.03,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.12: Multiple flags stack: inversion + gap
    inv_flag = {
        "severity": "warn",
        "reason": "variant_price_inversion",
        "details": {"inversions": [
            {"smaller_size": "S", "smaller_price_cents": 1200,
             "larger_size": "M", "larger_price_cents": 1000}
        ]},
    }
    v = _make_variant(label="Small", kind="size", normalized_size="S")
    tb = _make_tb(variants=[v], price_flags=[inv_flag, gap_flag])
    d = _score_single_variant(v, tb)
    report.check("4.12 inversion + gap stack",
                 d["flag_penalty"] == -0.15,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.13: No price_flags -> 0.0
    v = _make_variant(kind="size", normalized_size="S")
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("4.13 no flags -> 0.0",
                 d["flag_penalty"] == 0.0,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.14: Empty price_flags list -> 0.0
    tb = _make_tb(variants=[v], price_flags=[])
    d = _score_single_variant(v, tb)
    report.check("4.14 empty flags -> 0.0",
                 d["flag_penalty"] == 0.0,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.15: Unknown flag reason -> ignored
    unk_flag = {"severity": "warn", "reason": "some_future_check", "details": {}}
    tb = _make_tb(variants=[v], price_flags=[unk_flag])
    d = _score_single_variant(v, tb)
    report.check("4.15 unknown reason -> 0.0",
                 d["flag_penalty"] == 0.0,
                 f"got flag_penalty={d['flag_penalty']}")

    # 4.16: _variant_in_inversion helper True
    v_test = _make_variant(normalized_size="M")
    flag_test = {"details": {"inversions": [
        {"smaller_size": "S", "larger_size": "M"}
    ]}}
    report.check("4.16 helper inversion True",
                 _variant_in_inversion(v_test, flag_test),
                 "expected True")

    # 4.17: _variant_in_inversion helper False
    v_test2 = _make_variant(normalized_size="L")
    report.check("4.17 helper inversion False",
                 not _variant_in_inversion(v_test2, flag_test),
                 "expected False")

    # 4.18: _variant_in_inversion no normalized_size -> False
    v_test3 = _make_variant()
    report.check("4.18 helper no normalized_size -> False",
                 not _variant_in_inversion(v_test3, flag_test),
                 "expected False")

    # 4.19: _variant_is_duplicate helper True
    v_dup = _make_variant(group_key="size:S")
    dup_f = {"details": {"duplicated_keys": ["size:S", "size:M"]}}
    report.check("4.19 helper duplicate True",
                 _variant_is_duplicate(v_dup, dup_f),
                 "expected True")

    # 4.20: _variant_is_duplicate helper False
    v_nodup = _make_variant(group_key="size:L")
    report.check("4.20 helper duplicate False",
                 not _variant_is_duplicate(v_nodup, dup_f),
                 "expected False")

    # 4.21: _variant_is_duplicate no group_key -> False
    v_nogk = _make_variant()
    report.check("4.21 helper no group_key -> False",
                 not _variant_is_duplicate(v_nogk, dup_f),
                 "expected False")

    # 4.22: Same reason flag doesn't stack (only one penalty per reason type)
    inv_flag2 = {
        "severity": "warn",
        "reason": "variant_price_inversion",
        "details": {"inversions": [
            {"smaller_size": "S", "smaller_price_cents": 1200,
             "larger_size": "M", "larger_price_cents": 1000},
            {"smaller_size": "S", "smaller_price_cents": 1200,
             "larger_size": "L", "larger_price_cents": 1100},
        ]},
    }
    v = _make_variant(label="Small", kind="size", normalized_size="S")
    tb = _make_tb(variants=[v], price_flags=[inv_flag2])
    d = _score_single_variant(v, tb)
    report.check("4.22 same reason once -> -0.12",
                 d["flag_penalty"] == -0.12,
                 f"got flag_penalty={d['flag_penalty']}")


# ==================================================================
# Group 5: Combined Scoring & Clamping
# ==================================================================

def run_combined_scoring_tests(report: TestReport) -> None:
    """Test full score computation and clamping."""

    # 5.1: All positive signals -> high confidence
    v = _make_variant(kind="size", normalized_size="S", confidence=0.85)
    tb = _make_tb(
        variants=[v],
        grammar={"parse_confidence": 0.90},
        meta={"size_grid_applied": True},
    )
    d = _score_single_variant(v, tb)
    # 0.85 + 0.05 + 0.03 + 0.05 = 0.98
    report.check("5.1 all positive -> 0.98",
                 d["final"] == 0.98,
                 f"got final={d['final']}")

    # 5.2: Cap at 1.0
    v = _make_variant(kind="size", normalized_size="S", confidence=0.95)
    tb = _make_tb(
        variants=[v],
        grammar={"parse_confidence": 0.90},
        meta={"size_grid_applied": True},
    )
    d = _score_single_variant(v, tb)
    # 0.95 + 0.05 + 0.03 + 0.05 = 1.08 -> capped to 1.0
    report.check("5.2 capped at 1.0",
                 d["final"] == 1.0,
                 f"got final={d['final']}")

    # 5.3: Floor at 0.05
    v = _make_variant(label="", kind="other", confidence=0.30, price_cents=0)
    zero_flag = {"severity": "warn", "reason": "zero_price_variant",
                 "details": {"zero_labels": [""], "nonzero_count": 1}}
    tb = _make_tb(
        variants=[v],
        grammar={"parse_confidence": 0.0},
        price_flags=[zero_flag],
    )
    d = _score_single_variant(v, tb)
    # 0.30 + (-0.20) + (-0.10) + 0 + (-0.20) = -0.20 -> floored to 0.05
    report.check("5.3 floored at 0.05",
                 d["final"] == 0.05,
                 f"got final={d['final']}")

    # 5.4: Neutral case (no modifiers beyond kind)
    v = _make_variant(kind="size", normalized_size="M", confidence=0.85)
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    # 0.85 + 0.05 + 0 + 0 + 0 = 0.90
    report.check("5.4 base + label only -> 0.90",
                 d["final"] == 0.90,
                 f"got final={d['final']}")

    # 5.5: base=0.85 + label=-0.10 + grammar=-0.02 = 0.73
    v = _make_variant(kind="other", confidence=0.85)
    tb = _make_tb(variants=[v], grammar={"parse_confidence": 0.40})
    d = _score_single_variant(v, tb)
    expected_grammar = round(-0.10 * (1.0 - 0.40 / 0.50), 4)  # -0.02
    expected = round(0.85 + (-0.10) + expected_grammar, 4)
    report.check("5.5 other + low grammar",
                 d["final"] == expected,
                 f"got final={d['final']}, expected={expected}")

    # 5.6: Grid imperfect base (0.75) + grid boost + size
    v = _make_variant(kind="size", normalized_size="S", confidence=0.75)
    tb = _make_tb(variants=[v], meta={"size_grid_applied": True})
    d = _score_single_variant(v, tb)
    # 0.75 + 0.05 + 0 + 0.05 = 0.85
    report.check("5.6 imperfect grid + boosts -> 0.85",
                 d["final"] == 0.85,
                 f"got final={d['final']}")

    # 5.7: base preserved in details
    v = _make_variant(confidence=0.85, kind="size", normalized_size="S")
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("5.7 base preserved in details",
                 d["base"] == 0.85,
                 f"got base={d['base']}")

    # 5.8: score_variant_confidence writes back to variant
    v = _make_variant(kind="size", normalized_size="S", confidence=0.85)
    tb = _make_tb(variants=[v])
    score_variant_confidence([tb])
    report.check("5.8 confidence written back",
                 v["confidence"] == 0.90,
                 f"got confidence={v['confidence']}")

    # 5.9: confidence_details attached to variant
    report.check("5.9 confidence_details attached",
                 "confidence_details" in v,
                 f"keys={list(v.keys())}")
    report.check("5.9b details has all keys",
                 all(k in v["confidence_details"]
                     for k in ("base", "label_mod", "grammar_mod", "grid_mod",
                               "flag_penalty", "final")),
                 f"keys={list(v['confidence_details'].keys())}")

    # 5.10: Combo with high grammar
    v = _make_variant(label="W/Fries", kind="combo", confidence=0.85)
    tb = _make_tb(variants=[v], grammar={"parse_confidence": 0.85})
    d = _score_single_variant(v, tb)
    # 0.85 + 0.03 + 0.03 = 0.91
    report.check("5.10 combo + grammar -> 0.91",
                 d["final"] == 0.91,
                 f"got final={d['final']}")

    # 5.11: base 0.90 + size -> 0.95
    v = _make_variant(kind="size", normalized_size="L", confidence=0.90)
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("5.11 base 0.90 + size -> 0.95",
                 d["final"] == 0.95,
                 f"got final={d['final']}")

    # 5.12: base 0.80 + other -> 0.70
    v = _make_variant(kind="other", confidence=0.80)
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("5.12 base 0.80 + other -> 0.70",
                 d["final"] == 0.70,
                 f"got final={d['final']}")

    # 5.13: Grid + inversion (net effect)
    v = _make_variant(kind="size", normalized_size="S", confidence=0.85)
    inv_flag = {"severity": "warn", "reason": "variant_price_inversion",
                "details": {"inversions": [
                    {"smaller_size": "S", "smaller_price_cents": 1200,
                     "larger_size": "M", "larger_price_cents": 1000}
                ]}}
    tb = _make_tb(variants=[v],
                  meta={"size_grid_applied": True},
                  price_flags=[inv_flag])
    d = _score_single_variant(v, tb)
    # 0.85 + 0.05 + 0 + 0.05 + (-0.12) = 0.83
    report.check("5.13 grid + inversion -> 0.83",
                 d["final"] == 0.83,
                 f"got final={d['final']}")


# ==================================================================
# Group 6: Pipeline Integration
# ==================================================================

def run_pipeline_integration_tests(report: TestReport) -> None:
    """Test scoring in context of full enrichment + validation pipeline."""

    # 6.1: Empty list -> no crash
    score_variant_confidence([])
    report.check("6.1 empty list ok", True)

    # 6.2: Block with no variants -> skipped
    tb = _make_tb(text="Just a heading")
    score_variant_confidence([tb])
    report.check("6.2 no variants skipped",
                 "variants" not in tb or not tb.get("variants"),
                 "should have no variants")

    # 6.3: Block with empty variants list -> skipped
    tb = _make_tb(variants=[])
    score_variant_confidence([tb])
    report.check("6.3 empty variants skipped", True)

    # 6.4: S/M/L monotonic -> all high confidence
    vs = [
        _make_variant("Small", 999, 0.85, kind="size", normalized_size="S", group_key="size:S"),
        _make_variant("Medium", 1299, 0.85, kind="size", normalized_size="M", group_key="size:M"),
        _make_variant("Large", 1599, 0.85, kind="size", normalized_size="L", group_key="size:L"),
    ]
    tb = _make_tb(variants=vs, grammar={"parse_confidence": 0.85})
    validate_variant_prices([tb])
    check_variant_consistency([tb])
    score_variant_confidence([tb])
    report.check("6.4 S/M/L monotonic -> high",
                 all(v["confidence"] >= 0.90 for v in vs),
                 f"confidences={[v['confidence'] for v in vs]}")

    # 6.5: S/M/L with inversion -> S and M penalized
    vs = [
        _make_variant("Small", 1299, 0.85, kind="size", normalized_size="S", group_key="size:S"),
        _make_variant("Medium", 999, 0.85, kind="size", normalized_size="M", group_key="size:M"),
        _make_variant("Large", 1599, 0.85, kind="size", normalized_size="L", group_key="size:L"),
    ]
    tb = _make_tb(variants=vs, grammar={"parse_confidence": 0.85})
    validate_variant_prices([tb])
    check_variant_consistency([tb])
    score_variant_confidence([tb])
    # S and M are in the inversion, L is not
    report.check("6.5 inversion: S penalized",
                 vs[0]["confidence"] < 0.90,
                 f"S conf={vs[0]['confidence']}")
    report.check("6.5b inversion: M penalized",
                 vs[1]["confidence"] < 0.90,
                 f"M conf={vs[1]['confidence']}")
    report.check("6.5c inversion: L not penalized",
                 vs[2]["confidence"] >= 0.90,
                 f"L conf={vs[2]['confidence']}")

    # 6.6: Combo variants -> get combo boost
    vs = [
        _make_variant("W/Fries", 1099, 0.85, kind="combo", group_key="combo:w/fries"),
        _make_variant("W/Chips", 1099, 0.85, kind="combo", group_key="combo:w/chips"),
    ]
    tb = _make_tb(variants=vs, grammar={"parse_confidence": 0.80})
    check_variant_consistency([tb])
    score_variant_confidence([tb])
    report.check("6.6 combo variants -> boosted",
                 all(v["confidence"] >= 0.88 for v in vs),
                 f"confidences={[v['confidence'] for v in vs]}")

    # 6.7: Grid-applied S/M/L -> high confidence
    vs = [
        _make_variant("Small", 999, 0.85, kind="size", normalized_size="S", group_key="size:S"),
        _make_variant("Large", 1599, 0.85, kind="size", normalized_size="L", group_key="size:L"),
    ]
    tb = _make_tb(variants=vs,
                  grammar={"parse_confidence": 0.85},
                  meta={"size_grid_applied": True, "size_grid_source": 0,
                        "size_grid_column_count": 2})
    validate_variant_prices([tb])
    check_variant_consistency([tb])
    score_variant_confidence([tb])
    report.check("6.7 grid S/L -> high",
                 all(v["confidence"] >= 0.95 for v in vs),
                 f"confidences={[v['confidence'] for v in vs]}")

    # 6.8: Multiple blocks processed independently
    tb1 = _make_tb(variants=[
        _make_variant("S", 999, 0.85, kind="size", normalized_size="S"),
    ])
    tb2 = _make_tb(variants=[
        _make_variant("Mystery", 1099, 0.85, kind="other"),
    ])
    score_variant_confidence([tb1, tb2])
    report.check("6.8 independent blocks",
                 tb1["variants"][0]["confidence"] > tb2["variants"][0]["confidence"],
                 f"tb1={tb1['variants'][0]['confidence']}, tb2={tb2['variants'][0]['confidence']}")


# ==================================================================
# Group 7: ai_ocr_helper Path Compatibility
# ==================================================================

def run_ai_ocr_helper_path_tests(report: TestReport) -> None:
    """Test scoring works on items from ai_ocr_helper (no grammar, no meta)."""

    # 7.1: Item without grammar -> grammar_mod = 0.0
    v = _make_variant(kind="size", normalized_size="S")
    item = {"name": "Pizza", "variants": [v]}
    score_variant_confidence([item])
    report.check("7.1 no grammar -> 0.0 mod",
                 v["confidence_details"]["grammar_mod"] == 0.0,
                 f"grammar_mod={v['confidence_details']['grammar_mod']}")

    # 7.2: Item without meta -> grid_mod = 0.0
    report.check("7.2 no meta -> 0.0 grid",
                 v["confidence_details"]["grid_mod"] == 0.0,
                 f"grid_mod={v['confidence_details']['grid_mod']}")

    # 7.3: Items with price_flags from validation
    v = _make_variant("Small", 1299, 0.85, kind="size", normalized_size="S",
                      group_key="size:S")
    v2 = _make_variant("Medium", 999, 0.85, kind="size", normalized_size="M",
                       group_key="size:M")
    item = {"name": "Pizza", "variants": [v, v2]}
    validate_variant_prices([item])
    score_variant_confidence([item])
    report.check("7.3 price_flags consumed",
                 v["confidence"] < 0.85,
                 f"conf={v['confidence']}")

    # 7.4: Scoring result consistent for typical ai_ocr_helper variant
    v = _make_variant("Large", 1599, 0.90, kind="size", normalized_size="L")
    item = {"name": "Burger", "variants": [v]}
    score_variant_confidence([item])
    # 0.90 + 0.05 = 0.95
    report.check("7.4 typical ai variant -> 0.95",
                 v["confidence"] == 0.95,
                 f"got confidence={v['confidence']}")

    # 7.5: confidence_details present on ai path
    report.check("7.5 details present on ai path",
                 "confidence_details" in v,
                 f"keys={list(v.keys())}")

    # 7.6: Scoring does not crash on minimal item dict
    item = {"name": "Item", "variants": [
        {"label": "X", "price_cents": 500, "confidence": 0.80}
    ]}
    score_variant_confidence([item])
    report.check("7.6 minimal dict no crash",
                 "confidence_details" in item["variants"][0],
                 "should have details")

    # 7.7: Combo variant on ai path
    v = _make_variant("W/Fries", 1099, 0.90, kind="combo")
    item = {"name": "Burger", "variants": [v]}
    score_variant_confidence([item])
    # 0.90 + 0.03 = 0.93
    report.check("7.7 combo on ai path -> 0.93",
                 v["confidence"] == 0.93,
                 f"got confidence={v['confidence']}")

    # 7.8: Items with consistency flags consumed
    v1 = _make_variant("S", 999, 0.85, kind="size", normalized_size="S",
                       group_key="size:S")
    v2 = _make_variant("S", 1099, 0.85, kind="size", normalized_size="S",
                       group_key="size:S")
    item = {"name": "Pizza", "variants": [v1, v2]}
    check_variant_consistency([item])
    score_variant_confidence([item])
    report.check("7.8 consistency flags consumed",
                 v1["confidence"] < 0.85 and v2["confidence"] < 0.85,
                 f"v1={v1['confidence']}, v2={v2['confidence']}")


# ==================================================================
# Group 8: Edge Cases & Regressions
# ==================================================================

def run_edge_case_tests(report: TestReport) -> None:
    """Test edge cases and regressions."""

    # 8.1: Variant with kind=None -> treated as "other"
    v = _make_variant(label="Hmm")
    v["kind"] = None
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("8.1 kind=None -> other",
                 d["label_mod"] == -0.10,
                 f"got label_mod={d['label_mod']}")

    # 8.2: price_flags with missing details key
    v = _make_variant(kind="size", normalized_size="S")
    flag = {"severity": "warn", "reason": "variant_price_inversion"}
    tb = _make_tb(variants=[v], price_flags=[flag])
    d = _score_single_variant(v, tb)
    report.check("8.2 missing details -> no crash",
                 d["flag_penalty"] == 0.0,
                 f"got flag_penalty={d['flag_penalty']}")

    # 8.3: Very high base + all bonuses -> capped at 1.0
    v = _make_variant(kind="size", normalized_size="S", confidence=0.99)
    tb = _make_tb(variants=[v],
                  grammar={"parse_confidence": 0.95},
                  meta={"size_grid_applied": True})
    d = _score_single_variant(v, tb)
    report.check("8.3 extreme positive -> 1.0",
                 d["final"] == 1.0,
                 f"got final={d['final']}")

    # 8.4: Very low base + all penalties -> 0.05
    v = _make_variant(label="", kind="other", confidence=0.10, price_cents=0)
    zero_flag = {"severity": "warn", "reason": "zero_price_variant",
                 "details": {"zero_labels": [""], "nonzero_count": 1}}
    mixed_flag = {"severity": "warn", "reason": "mixed_variant_kinds",
                  "details": {"kinds_found": ["a", "b", "c"], "variant_count": 3}}
    tb = _make_tb(variants=[v],
                  grammar={"parse_confidence": 0.0},
                  price_flags=[zero_flag, mixed_flag])
    d = _score_single_variant(v, tb)
    report.check("8.4 extreme negative -> 0.05",
                 d["final"] == 0.05,
                 f"got final={d['final']}")

    # 8.5: Variant with missing confidence field -> defaults to 0.80
    v = {"label": "Test", "price_cents": 500}
    tb = _make_tb(variants=[v])
    d = _score_single_variant(v, tb)
    report.check("8.5 missing confidence -> base 0.80",
                 d["base"] == 0.80,
                 f"got base={d['base']}")

    # 8.6: price_flags with empty inversions list
    v = _make_variant(kind="size", normalized_size="S")
    flag = {"severity": "warn", "reason": "variant_price_inversion",
            "details": {"inversions": []}}
    tb = _make_tb(variants=[v], price_flags=[flag])
    d = _score_single_variant(v, tb)
    report.check("8.6 empty inversions -> 0.0 penalty",
                 d["flag_penalty"] == 0.0,
                 f"got flag_penalty={d['flag_penalty']}")

    # 8.7: price_flags with empty duplicated_keys
    flag = {"severity": "warn", "reason": "duplicate_variant",
            "details": {"duplicated_keys": []}}
    tb = _make_tb(variants=[v], price_flags=[flag])
    d = _score_single_variant(v, tb)
    report.check("8.7 empty duplicated_keys -> 0.0",
                 d["flag_penalty"] == 0.0,
                 f"got flag_penalty={d['flag_penalty']}")

    # 8.8: All details keys are floats (round correctness)
    v = _make_variant(kind="size", normalized_size="S", confidence=0.85)
    tb = _make_tb(variants=[v], grammar={"parse_confidence": 0.85},
                  meta={"size_grid_applied": True})
    d = _score_single_variant(v, tb)
    for key in ("base", "label_mod", "grammar_mod", "grid_mod", "flag_penalty", "final"):
        report.check(f"8.8 {key} is float",
                     isinstance(d[key], float),
                     f"{key}={d[key]} type={type(d[key])}")

    # 8.9: Score applied via score_variant_confidence mutates in place
    v1 = _make_variant("Small", 999, 0.85, kind="size", normalized_size="S")
    v2 = _make_variant("Large", 1599, 0.85, kind="size", normalized_size="L")
    tb = _make_tb(variants=[v1, v2])
    score_variant_confidence([tb])
    # Both should be modified
    report.check("8.9 both variants mutated",
                 "confidence_details" in v1 and "confidence_details" in v2,
                 f"v1 keys={list(v1.keys())}, v2 keys={list(v2.keys())}")

    # 8.10: Scoring preserves other variant fields
    v = _make_variant("Medium", 1299, 0.85, kind="size",
                      normalized_size="M", group_key="size:M")
    tb = _make_tb(variants=[v])
    score_variant_confidence([tb])
    report.check("8.10 preserves fields",
                 v["label"] == "Medium" and v["price_cents"] == 1299 and
                 v["kind"] == "size" and v["normalized_size"] == "M" and
                 v["group_key"] == "size:M",
                 f"v={v}")

    # 8.11: grammar confidence exactly at boundaries
    v = _make_variant(kind="size", normalized_size="S")
    # Exactly 0.79 -> between 0.50 and 0.80 -> 0.0
    tb = _make_tb(variants=[v], grammar={"parse_confidence": 0.79})
    d = _score_single_variant(v, tb)
    report.check("8.11 grammar 0.79 -> 0.0",
                 d["grammar_mod"] == 0.0,
                 f"got grammar_mod={d['grammar_mod']}")

    # 8.12: Multiple blocks, only blocks with variants are scored
    tb_no_var = _make_tb(text="Heading")
    tb_var = _make_tb(variants=[
        _make_variant("S", 999, 0.85, kind="size", normalized_size="S")
    ])
    score_variant_confidence([tb_no_var, tb_var])
    report.check("8.12 only variant blocks scored",
                 "confidence_details" in tb_var["variants"][0] and
                 "variants" not in tb_no_var,
                 "heading block shouldn't have variants")


# ==================================================================
# Main Runner
# ==================================================================

def main() -> None:
    report = TestReport()

    print("=" * 70)
    print("Day 60: Variant Confidence Scoring Tests")
    print("=" * 70)

    groups = [
        ("Group 1: Label Clarity", run_label_clarity_tests),
        ("Group 2: Grammar Context", run_grammar_context_tests),
        ("Group 3: Grid Context", run_grid_context_tests),
        ("Group 4: Price Flag Penalties", run_price_flag_tests),
        ("Group 5: Combined Scoring", run_combined_scoring_tests),
        ("Group 6: Pipeline Integration", run_pipeline_integration_tests),
        ("Group 7: ai_ocr_helper Path", run_ai_ocr_helper_path_tests),
        ("Group 8: Edge Cases", run_edge_case_tests),
    ]

    for name, fn in groups:
        before = report.total
        fn(report)
        count = report.total - before
        fails = len([f for f in report.failures if f not in report.failures[:len(report.failures) - (report.total - report.passed - (before - (before - len(report.failures))))]])
        print(f"  {name}: {count} tests")

    print("-" * 70)
    print(f"Total: {report.total} | Passed: {report.passed} | "
          f"Failed: {report.total - report.passed}")

    if report.failures:
        print("\nFailures:")
        for f in report.failures:
            print(f)
        sys.exit(1)
    else:
        print("\nAll tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
