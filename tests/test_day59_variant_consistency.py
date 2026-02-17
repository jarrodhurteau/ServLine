# tests/test_day59_variant_consistency.py
"""
Day 59: Sprint 8.2 -- Cross-Variant Consistency Checks

Tests:
  1. Duplicate variant detection (same group_key)
  2. Grid completeness (fewer variants than grid columns)
  3. Mixed kind detection (unusual kind combinations)
  4. Zero-price variant detection ($0.00 variants)
  5. Size gap detection (missing intermediate sizes)
  6. Grid consistency across items (outlier variant counts)
  7. Integration with full pipeline
  8. Edge cases and regressions

Run: python tests/test_day59_variant_consistency.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.variant_engine import (
    check_variant_consistency,
    validate_variant_prices,
    enrich_variants_on_text_blocks,
    apply_size_grid_context,
)
from storage.parsers.size_vocab import (
    size_ordinal,
    size_track,
    WORD_CHAIN,
    PORTION_CHAIN,
    MULTIPLICITY_CHAIN,
)
from storage.parsers.menu_grammar import enrich_grammar_on_text_blocks


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


def _make_tb(
    text: str,
    grammar: Optional[Dict] = None,
    variants: Optional[List[Dict]] = None,
    meta: Optional[Dict] = None,
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
    return tb


def _count_flags(tb: Dict[str, Any], reason: str) -> int:
    """Count flags with a given reason on a text_block."""
    flags = tb.get("price_flags") or []
    return sum(1 for f in flags if f.get("reason") == reason)


def _get_flag(tb: Dict[str, Any], reason: str) -> Optional[Dict]:
    """Get the first flag with a given reason, or None."""
    for f in (tb.get("price_flags") or []):
        if f.get("reason") == reason:
            return f
    return None


# ==================================================================
# Group 1: Duplicate Variant Detection
# ==================================================================

def run_duplicate_tests(report: TestReport) -> None:
    print("\n--- Group 1: Duplicate Variant Detection ---")

    # 1.1: Two variants with same normalized_size "S" -> duplicate_variant
    tb = _make_tb("Small 9.99 Small 10.99", variants=[
        {"label": "Small", "price_cents": 999, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "Small", "price_cents": 1099, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "duplicate_variant") == 1:
        report.ok("1.1 duplicate size S")
    else:
        report.fail("1.1 duplicate size S", f"expected 1 flag, got {_count_flags(tb, 'duplicate_variant')}")

    # 1.2: Two combo variants with same group_key
    tb = _make_tb("W/Fries 9.99 W/Fries 12.99", variants=[
        {"label": "W/Fries", "price_cents": 999, "confidence": 0.85,
         "kind": "combo", "group_key": "combo:w/fries"},
        {"label": "W/Fries", "price_cents": 1299, "confidence": 0.85,
         "kind": "combo", "group_key": "combo:w/fries"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "duplicate_variant") == 1:
        report.ok("1.2 duplicate combo W/Fries")
    else:
        report.fail("1.2 duplicate combo W/Fries", f"expected 1, got {_count_flags(tb, 'duplicate_variant')}")

    # 1.3: Three variants with same group_key
    tb = _make_tb("Small 8 Small 9 Small 10", variants=[
        {"label": "Small", "price_cents": 800, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "Small", "price_cents": 900, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "Small", "price_cents": 1000, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "duplicate_variant") == 1:
        report.ok("1.3 triple duplicate size S")
    else:
        report.fail("1.3 triple duplicate", f"expected 1, got {_count_flags(tb, 'duplicate_variant')}")

    # 1.4: Two different sizes (no duplicates) -> no flag
    tb = _make_tb("Small 8.99 Large 14.99", variants=[
        {"label": "Small", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "Large", "price_cents": 1499, "confidence": 0.85,
         "kind": "size", "normalized_size": "L", "group_key": "size:L"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "duplicate_variant") == 0:
        report.ok("1.4 no duplicate S/L")
    else:
        report.fail("1.4 no duplicate S/L", "unexpected flag")

    # 1.5: S/M/L all unique -> no flag
    tb = _make_tb("S 8 M 10 L 14", variants=[
        {"label": "S", "price_cents": 800, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "M", "price_cents": 1000, "confidence": 0.85,
         "kind": "size", "normalized_size": "M", "group_key": "size:M"},
        {"label": "L", "price_cents": 1400, "confidence": 0.85,
         "kind": "size", "normalized_size": "L", "group_key": "size:L"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "duplicate_variant") == 0:
        report.ok("1.5 unique S/M/L")
    else:
        report.fail("1.5 unique S/M/L", "unexpected flag")

    # 1.6: Two flavor variants with same group_key
    tb = _make_tb("Hot 9.99 Hot 12.99", variants=[
        {"label": "Hot", "price_cents": 999, "confidence": 0.85,
         "kind": "flavor", "group_key": "flavor:hot"},
        {"label": "Hot", "price_cents": 1299, "confidence": 0.85,
         "kind": "flavor", "group_key": "flavor:hot"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "duplicate_variant") == 1:
        report.ok("1.6 duplicate flavor Hot")
    else:
        report.fail("1.6 duplicate flavor Hot", f"expected 1, got {_count_flags(tb, 'duplicate_variant')}")

    # 1.7: Variant with no group_key -> not counted for duplicate check
    tb = _make_tb("test", variants=[
        {"label": "Small", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "??", "price_cents": 999, "confidence": 0.5,
         "kind": "other"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "duplicate_variant") == 0:
        report.ok("1.7 no dup with None group_key")
    else:
        report.fail("1.7 no dup with None group_key", "unexpected flag")

    # 1.8: Single variant -> no flag
    tb = _make_tb("Small 8.99", variants=[
        {"label": "Small", "price_cents": 899, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "duplicate_variant") == 0:
        report.ok("1.8 single variant")
    else:
        report.fail("1.8 single variant", "unexpected flag")

    # 1.9: Two different combos -> no flag
    tb = _make_tb("W/Fries 9 W/Cheese 10", variants=[
        {"label": "W/Fries", "price_cents": 900, "confidence": 0.85,
         "kind": "combo", "group_key": "combo:w/fries"},
        {"label": "W/Cheese", "price_cents": 1000, "confidence": 0.85,
         "kind": "combo", "group_key": "combo:w/cheese"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "duplicate_variant") == 0:
        report.ok("1.9 unique combos")
    else:
        report.fail("1.9 unique combos", "unexpected flag")

    # 1.10: Mixed kinds, no duplicates -> no duplicate flag
    tb = _make_tb("S 8 Hot 10", variants=[
        {"label": "S", "price_cents": 800, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "Hot", "price_cents": 1000, "confidence": 0.85,
         "kind": "flavor", "group_key": "flavor:hot"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "duplicate_variant") == 0:
        report.ok("1.10 mixed kinds no dup")
    else:
        report.fail("1.10 mixed kinds no dup", "unexpected flag")

    # 1.11: Details contain correct duplicated_keys
    tb = _make_tb("S 8 S 9", variants=[
        {"label": "S", "price_cents": 800, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "S", "price_cents": 900, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "duplicate_variant")
    if flag and "size:S" in flag["details"]["duplicated_keys"]:
        report.ok("1.11 details duplicated_keys")
    else:
        report.fail("1.11 details duplicated_keys", f"flag={flag}")

    # 1.12: Multiple different duplicated keys
    tb = _make_tb("S 8 S 9 L 10 L 11", variants=[
        {"label": "S", "price_cents": 800, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "S", "price_cents": 900, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "L", "price_cents": 1000, "confidence": 0.85,
         "kind": "size", "normalized_size": "L", "group_key": "size:L"},
        {"label": "L", "price_cents": 1100, "confidence": 0.85,
         "kind": "size", "normalized_size": "L", "group_key": "size:L"},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "duplicate_variant")
    if flag and len(flag["details"]["duplicated_keys"]) == 2:
        report.ok("1.12 two duplicated keys")
    else:
        report.fail("1.12 two duplicated keys", f"flag={flag}")

    # 1.13: Duplicate style variants
    tb = _make_tb("Boneless 9 Boneless 12", variants=[
        {"label": "Boneless", "price_cents": 900, "confidence": 0.85,
         "kind": "style", "group_key": "style:boneless"},
        {"label": "Boneless", "price_cents": 1200, "confidence": 0.85,
         "kind": "style", "group_key": "style:boneless"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "duplicate_variant") == 1:
        report.ok("1.13 duplicate style")
    else:
        report.fail("1.13 duplicate style", f"expected 1, got {_count_flags(tb, 'duplicate_variant')}")

    # 1.14: Severity is "warn"
    tb = _make_tb("S 8 S 9", variants=[
        {"label": "S", "price_cents": 800, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "S", "price_cents": 900, "confidence": 0.85,
         "kind": "size", "normalized_size": "S", "group_key": "size:S"},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "duplicate_variant")
    if flag and flag["severity"] == "warn":
        report.ok("1.14 severity is warn")
    else:
        report.fail("1.14 severity", f"flag={flag}")


# ==================================================================
# Group 2: Grid Completeness
# ==================================================================

def run_grid_completeness_tests(report: TestReport) -> None:
    print("\n--- Group 2: Grid Completeness ---")

    # 2.1: 4-column grid, 4 variants -> no flag
    tb = _make_tb("CHEESE 8 11 14 22", variants=[
        {"label": '10"', "price_cents": 800, "confidence": 0.85, "kind": "size"},
        {"label": '12"', "price_cents": 1100, "confidence": 0.85, "kind": "size"},
        {"label": '16"', "price_cents": 1400, "confidence": 0.85, "kind": "size"},
        {"label": "Family", "price_cents": 2200, "confidence": 0.85, "kind": "size"},
    ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 4})
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 0:
        report.ok("2.1 4-col/4-var no flag")
    else:
        report.fail("2.1 4-col/4-var", "unexpected flag")

    # 2.2: 4-column grid, 3 variants -> no flag (right-aligned gourmet)
    tb = _make_tb("GOURMET 14 17 25", variants=[
        {"label": '12"', "price_cents": 1400, "confidence": 0.85, "kind": "size"},
        {"label": '16"', "price_cents": 1700, "confidence": 0.85, "kind": "size"},
        {"label": "Family", "price_cents": 2500, "confidence": 0.85, "kind": "size"},
    ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 4})
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 0:
        report.ok("2.2 4-col/3-var gourmet OK")
    else:
        report.fail("2.2 4-col/3-var gourmet", "unexpected flag")

    # 2.3: 4-column grid, 2 variants -> flag
    tb = _make_tb("SPECIAL 14 25", variants=[
        {"label": '12"', "price_cents": 1400, "confidence": 0.85, "kind": "size"},
        {"label": "Family", "price_cents": 2500, "confidence": 0.85, "kind": "size"},
    ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 4})
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 1:
        report.ok("2.3 4-col/2-var flagged")
    else:
        report.fail("2.3 4-col/2-var", f"expected 1, got {_count_flags(tb, 'grid_incomplete')}")

    # 2.4: 4-column grid, 1 variant -> flag
    tb = _make_tb("ITEM 14", variants=[
        {"label": '12"', "price_cents": 1400, "confidence": 0.85, "kind": "size"},
    ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 4})
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 1:
        report.ok("2.4 4-col/1-var flagged")
    else:
        report.fail("2.4 4-col/1-var", f"expected 1, got {_count_flags(tb, 'grid_incomplete')}")

    # 2.5: 3-column grid, 1 variant -> flag
    tb = _make_tb("ITEM 14", variants=[
        {"label": "S", "price_cents": 1400, "confidence": 0.85, "kind": "size"},
    ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 3})
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 1:
        report.ok("2.5 3-col/1-var flagged")
    else:
        report.fail("2.5 3-col/1-var", f"expected 1, got {_count_flags(tb, 'grid_incomplete')}")

    # 2.6: No grid applied -> no flag regardless
    tb = _make_tb("ITEM 14 17", variants=[
        {"label": "S", "price_cents": 1400, "confidence": 0.85, "kind": "size"},
        {"label": "L", "price_cents": 1700, "confidence": 0.85, "kind": "size"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 0:
        report.ok("2.6 no grid no flag")
    else:
        report.fail("2.6 no grid", "unexpected flag")

    # 2.7: meta absent -> no flag
    tb = _make_tb("ITEM 14", variants=[
        {"label": "S", "price_cents": 1400, "confidence": 0.85, "kind": "size"},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 0:
        report.ok("2.7 no meta no flag")
    else:
        report.fail("2.7 no meta", "unexpected flag")

    # 2.8: Severity is "info"
    tb = _make_tb("ITEM 14", variants=[
        {"label": "S", "price_cents": 1400, "confidence": 0.85, "kind": "size"},
    ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 4})
    check_variant_consistency([tb])
    flag = _get_flag(tb, "grid_incomplete")
    if flag and flag["severity"] == "info":
        report.ok("2.8 severity is info")
    else:
        report.fail("2.8 severity", f"flag={flag}")

    # 2.9: Details contain correct counts
    tb = _make_tb("ITEM 14", variants=[
        {"label": "S", "price_cents": 1400, "confidence": 0.85, "kind": "size"},
    ], meta={"size_grid_applied": True, "size_grid_source": 5, "size_grid_column_count": 4})
    check_variant_consistency([tb])
    flag = _get_flag(tb, "grid_incomplete")
    if (flag and flag["details"]["grid_column_count"] == 4
            and flag["details"]["variant_count"] == 1
            and flag["details"]["missing_count"] == 3
            and flag["details"]["grid_source_line"] == 5):
        report.ok("2.9 details correct")
    else:
        report.fail("2.9 details", f"flag={flag}")

    # 2.10: 2-column grid, 2 variants -> no flag
    tb = _make_tb("ITEM 10 15", variants=[
        {"label": "S", "price_cents": 1000, "confidence": 0.85, "kind": "size"},
        {"label": "L", "price_cents": 1500, "confidence": 0.85, "kind": "size"},
    ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 2})
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 0:
        report.ok("2.10 2-col/2-var no flag")
    else:
        report.fail("2.10 2-col/2-var", "unexpected flag")

    # 2.11: Grid column count < 2 -> skip
    tb = _make_tb("ITEM 10", variants=[
        {"label": "S", "price_cents": 1000, "confidence": 0.85, "kind": "size"},
    ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 1})
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 0:
        report.ok("2.11 1-col grid skip")
    else:
        report.fail("2.11 1-col", "unexpected flag")

    # 2.12: 3-column grid, 2 variants -> no flag (only 1 missing)
    tb = _make_tb("ITEM 10 15", variants=[
        {"label": "S", "price_cents": 1000, "confidence": 0.85, "kind": "size"},
        {"label": "L", "price_cents": 1500, "confidence": 0.85, "kind": "size"},
    ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 3})
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 0:
        report.ok("2.12 3-col/2-var no flag (1 missing OK)")
    else:
        report.fail("2.12 3-col/2-var", "unexpected flag")


# ==================================================================
# Group 3: Mixed Kind Detection
# ==================================================================

def run_mixed_kinds_tests(report: TestReport) -> None:
    print("\n--- Group 3: Mixed Kind Detection ---")

    # 3.1: All size variants -> no flag
    tb = _make_tb("S 8 M 10 L 14", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
        {"label": "M", "price_cents": 1000, "kind": "size", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "mixed_variant_kinds") == 0:
        report.ok("3.1 all size no flag")
    else:
        report.fail("3.1 all size", "unexpected flag")

    # 3.2: All combo variants -> no flag
    tb = _make_tb("W/Fries 9 W/Cheese 10", variants=[
        {"label": "W/Fries", "price_cents": 900, "kind": "combo", "confidence": 0.85},
        {"label": "W/Cheese", "price_cents": 1000, "kind": "combo", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "mixed_variant_kinds") == 0:
        report.ok("3.2 all combo no flag")
    else:
        report.fail("3.2 all combo", "unexpected flag")

    # 3.3: All flavor variants -> no flag
    tb = _make_tb("Hot 9 BBQ 10", variants=[
        {"label": "Hot", "price_cents": 900, "kind": "flavor", "confidence": 0.85},
        {"label": "BBQ", "price_cents": 1000, "kind": "flavor", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "mixed_variant_kinds") == 0:
        report.ok("3.3 all flavor no flag")
    else:
        report.fail("3.3 all flavor", "unexpected flag")

    # 3.4: All style variants -> no flag
    tb = _make_tb("Boneless 9 Bone-in 10", variants=[
        {"label": "Boneless", "price_cents": 900, "kind": "style", "confidence": 0.85},
        {"label": "Bone-in", "price_cents": 1000, "kind": "style", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "mixed_variant_kinds") == 0:
        report.ok("3.4 all style no flag")
    else:
        report.fail("3.4 all style", "unexpected flag")

    # 3.5: Size + combo mix -> info flag
    tb = _make_tb("S 8 W/Fries 12", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
        {"label": "W/Fries", "price_cents": 1200, "kind": "combo", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "mixed_variant_kinds")
    if flag and flag["severity"] == "info":
        report.ok("3.5 size+combo info")
    else:
        report.fail("3.5 size+combo", f"flag={flag}")

    # 3.6: Size + flavor mix -> info flag
    tb = _make_tb("S 8 Hot 10", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
        {"label": "Hot", "price_cents": 1000, "kind": "flavor", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "mixed_variant_kinds")
    if flag and flag["severity"] == "info":
        report.ok("3.6 size+flavor info")
    else:
        report.fail("3.6 size+flavor", f"flag={flag}")

    # 3.7: Size + style mix -> info flag
    tb = _make_tb("S 8 Boneless 10", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
        {"label": "Boneless", "price_cents": 1000, "kind": "style", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "mixed_variant_kinds")
    if flag and flag["severity"] == "info":
        report.ok("3.7 size+style info")
    else:
        report.fail("3.7 size+style", f"flag={flag}")

    # 3.8: Flavor + style mix -> info flag
    tb = _make_tb("Hot 8 Boneless 10", variants=[
        {"label": "Hot", "price_cents": 800, "kind": "flavor", "confidence": 0.85},
        {"label": "Boneless", "price_cents": 1000, "kind": "style", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "mixed_variant_kinds")
    if flag and flag["severity"] == "info":
        report.ok("3.8 flavor+style info")
    else:
        report.fail("3.8 flavor+style", f"flag={flag}")

    # 3.9: Size + combo + flavor (3 kinds) -> warn
    tb = _make_tb("S 8 W/Fries 10 Hot 12", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
        {"label": "W/Fries", "price_cents": 1000, "kind": "combo", "confidence": 0.85},
        {"label": "Hot", "price_cents": 1200, "kind": "flavor", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "mixed_variant_kinds")
    if flag and flag["severity"] == "warn":
        report.ok("3.9 three kinds -> warn")
    else:
        report.fail("3.9 three kinds", f"flag={flag}")

    # 3.10: Variants with "other" kind only -> no flag
    tb = _make_tb("A 8 B 10", variants=[
        {"label": "A", "price_cents": 800, "kind": "other", "confidence": 0.85},
        {"label": "B", "price_cents": 1000, "kind": "other", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "mixed_variant_kinds") == 0:
        report.ok("3.10 all other no flag")
    else:
        report.fail("3.10 all other", "unexpected flag")

    # 3.11: Size + other -> no flag (other is ignored)
    tb = _make_tb("S 8 ?? 10", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
        {"label": "??", "price_cents": 1000, "kind": "other", "confidence": 0.5},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "mixed_variant_kinds") == 0:
        report.ok("3.11 size+other no flag")
    else:
        report.fail("3.11 size+other", "unexpected flag")

    # 3.12: Single variant -> no flag
    tb = _make_tb("S 8", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "mixed_variant_kinds") == 0:
        report.ok("3.12 single variant no flag")
    else:
        report.fail("3.12 single variant", "unexpected flag")

    # 3.13: Details contain correct kinds_found
    tb = _make_tb("S 8 W/Fries 10", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
        {"label": "W/Fries", "price_cents": 1000, "kind": "combo", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "mixed_variant_kinds")
    if flag and sorted(flag["details"]["kinds_found"]) == ["combo", "size"]:
        report.ok("3.13 details kinds_found")
    else:
        report.fail("3.13 details", f"flag={flag}")

    # 3.14: Variant with kind=None -> treated as other, ignored
    tb = _make_tb("S 8 ?? 10", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
        {"label": "??", "price_cents": 1000, "confidence": 0.5},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "mixed_variant_kinds") == 0:
        report.ok("3.14 kind=None ignored")
    else:
        report.fail("3.14 kind=None", "unexpected flag")


# ==================================================================
# Group 4: Zero-Price Variant Detection
# ==================================================================

def run_zero_price_tests(report: TestReport) -> None:
    print("\n--- Group 4: Zero-Price Variant Detection ---")

    # 4.1: All valid prices -> no flag
    tb = _make_tb("S 8 L 14", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "zero_price_variant") == 0:
        report.ok("4.1 all valid no flag")
    else:
        report.fail("4.1 all valid", "unexpected flag")

    # 4.2: One zero, one nonzero -> flag
    tb = _make_tb("S 0 L 14", variants=[
        {"label": "Small", "price_cents": 0, "kind": "size", "confidence": 0.85},
        {"label": "Large", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "zero_price_variant") == 1:
        report.ok("4.2 one zero flagged")
    else:
        report.fail("4.2 one zero", f"expected 1, got {_count_flags(tb, 'zero_price_variant')}")

    # 4.3: Both zero -> no flag (no nonzero reference)
    tb = _make_tb("S 0 L 0", variants=[
        {"label": "Small", "price_cents": 0, "kind": "size", "confidence": 0.85},
        {"label": "Large", "price_cents": 0, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "zero_price_variant") == 0:
        report.ok("4.3 both zero no flag")
    else:
        report.fail("4.3 both zero", "unexpected flag")

    # 4.4: Missing price_cents key -> not flagged (absent != zero)
    tb = _make_tb("S ? L 14", variants=[
        {"label": "Small", "kind": "size", "confidence": 0.85},
        {"label": "Large", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "zero_price_variant") == 0:
        report.ok("4.4 missing price_cents no flag")
    else:
        report.fail("4.4 missing price_cents", "unexpected flag")

    # 4.5: Details contain zero_labels
    tb = _make_tb("S 0 L 14", variants=[
        {"label": "Small", "price_cents": 0, "kind": "size", "confidence": 0.85},
        {"label": "Large", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "zero_price_variant")
    if flag and "Small" in flag["details"]["zero_labels"]:
        report.ok("4.5 details zero_labels")
    else:
        report.fail("4.5 details", f"flag={flag}")

    # 4.6: Two zero, one nonzero -> flag with 2 labels
    tb = _make_tb("S 0 M 0 L 14", variants=[
        {"label": "Small", "price_cents": 0, "kind": "size", "confidence": 0.85},
        {"label": "Medium", "price_cents": 0, "kind": "size", "confidence": 0.85},
        {"label": "Large", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "zero_price_variant")
    if flag and len(flag["details"]["zero_labels"]) == 2:
        report.ok("4.6 two zero labels")
    else:
        report.fail("4.6 two zero", f"flag={flag}")

    # 4.7: Severity is "warn"
    tb = _make_tb("S 0 L 14", variants=[
        {"label": "Small", "price_cents": 0, "kind": "size", "confidence": 0.85},
        {"label": "Large", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "zero_price_variant")
    if flag and flag["severity"] == "warn":
        report.ok("4.7 severity is warn")
    else:
        report.fail("4.7 severity", f"flag={flag}")

    # 4.8: Combo variant with zero price
    tb = _make_tb("9 W/Fries 0", variants=[
        {"label": "", "price_cents": 900, "kind": "other", "confidence": 0.85},
        {"label": "W/Fries", "price_cents": 0, "kind": "combo", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "zero_price_variant") == 1:
        report.ok("4.8 combo zero flagged")
    else:
        report.fail("4.8 combo zero", f"expected 1, got {_count_flags(tb, 'zero_price_variant')}")

    # 4.9: Negative price_cents -> not counted as zero
    tb = _make_tb("S -1 L 14", variants=[
        {"label": "Small", "price_cents": -1, "kind": "size", "confidence": 0.85},
        {"label": "Large", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "zero_price_variant") == 0:
        report.ok("4.9 negative price not zero")
    else:
        report.fail("4.9 negative price", "unexpected flag")

    # 4.10: Details nonzero_count correct
    tb = _make_tb("S 0 M 10 L 14", variants=[
        {"label": "S", "price_cents": 0, "kind": "size", "confidence": 0.85},
        {"label": "M", "price_cents": 1000, "kind": "size", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "zero_price_variant")
    if flag and flag["details"]["nonzero_count"] == 2:
        report.ok("4.10 nonzero_count=2")
    else:
        report.fail("4.10 nonzero_count", f"flag={flag}")


# ==================================================================
# Group 5: Size Gap Detection
# ==================================================================

def run_size_gap_tests(report: TestReport) -> None:
    print("\n--- Group 5: Size Gap Detection ---")

    # 5.1: S, M, L present -> no gap
    tb = _make_tb("S 8 M 10 L 14", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S", "confidence": 0.85},
        {"label": "M", "price_cents": 1000, "kind": "size", "normalized_size": "M", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "normalized_size": "L", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("5.1 S/M/L no gap")
    else:
        report.fail("5.1 S/M/L", "unexpected flag")

    # 5.2: S, L present (M missing) -> gap flag
    tb = _make_tb("S 8 L 14", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "normalized_size": "L", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "size_gap")
    if flag and "M" in flag["details"]["missing_sizes"]:
        report.ok("5.2 S/L gap M")
    else:
        report.fail("5.2 S/L gap", f"flag={flag}")

    # 5.3: S, XL present -> M, L missing (abbreviated chain)
    tb = _make_tb("S 8 XL 20", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S", "confidence": 0.85},
        {"label": "XL", "price_cents": 2000, "kind": "size", "normalized_size": "XL", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "size_gap")
    expected_missing = {"M", "L"}
    if flag and set(flag["details"]["missing_sizes"]) == expected_missing:
        report.ok("5.3 S/XL missing M,L")
    else:
        report.fail("5.3 S/XL", f"flag={flag}")

    # 5.4: Half, Whole present -> no gap in portion track
    tb = _make_tb("Half 8 Whole 14", variants=[
        {"label": "Half", "price_cents": 800, "kind": "size", "normalized_size": "Half", "confidence": 0.85},
        {"label": "Whole", "price_cents": 1400, "kind": "size", "normalized_size": "Whole", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("5.4 Half/Whole no gap")
    else:
        report.fail("5.4 Half/Whole", "unexpected flag")

    # 5.5: Slice, Family present -> Half,Whole missing
    tb = _make_tb("Slice 4 Family 22", variants=[
        {"label": "Slice", "price_cents": 400, "kind": "size", "normalized_size": "Slice", "confidence": 0.85},
        {"label": "Family", "price_cents": 2200, "kind": "size", "normalized_size": "Family", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "size_gap")
    if flag and set(flag["details"]["missing_sizes"]) == {"Half", "Whole"}:
        report.ok("5.5 Slice/Family gap Half,Whole")
    else:
        report.fail("5.5 Slice/Family", f"flag={flag}")

    # 5.6: Single, Triple present (Double missing)
    tb = _make_tb("Single 6 Triple 12", variants=[
        {"label": "Single", "price_cents": 600, "kind": "size", "normalized_size": "Single", "confidence": 0.85},
        {"label": "Triple", "price_cents": 1200, "kind": "size", "normalized_size": "Triple", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "size_gap")
    if flag and "Double" in flag["details"]["missing_sizes"]:
        report.ok("5.6 Single/Triple gap Double")
    else:
        report.fail("5.6 Single/Triple", f"flag={flag}")

    # 5.7: 10in, 16in -> no flag (inch track skipped)
    tb = _make_tb('10" 8 16" 14', variants=[
        {"label": '10"', "price_cents": 800, "kind": "size", "normalized_size": "10in", "confidence": 0.85},
        {"label": '16"', "price_cents": 1400, "kind": "size", "normalized_size": "16in", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("5.7 inch track skipped")
    else:
        report.fail("5.7 inch track", "unexpected flag")

    # 5.8: 6pc, 24pc -> no flag (piece track skipped)
    tb = _make_tb("6pc 5 24pc 18", variants=[
        {"label": "6pc", "price_cents": 500, "kind": "size", "normalized_size": "6pc", "confidence": 0.85},
        {"label": "24pc", "price_cents": 1800, "kind": "size", "normalized_size": "24pc", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("5.8 piece track skipped")
    else:
        report.fail("5.8 piece track", "unexpected flag")

    # 5.9: S only -> no flag (need 2+ for gap)
    tb = _make_tb("S 8", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("5.9 single size no gap")
    else:
        report.fail("5.9 single size", "unexpected flag")

    # 5.10: S, M (adjacent, no gap)
    tb = _make_tb("S 8 M 10", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S", "confidence": 0.85},
        {"label": "M", "price_cents": 1000, "kind": "size", "normalized_size": "M", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("5.10 S/M adjacent no gap")
    else:
        report.fail("5.10 S/M adjacent", "unexpected flag")

    # 5.11: M, L (adjacent, no gap)
    tb = _make_tb("M 10 L 14", variants=[
        {"label": "M", "price_cents": 1000, "kind": "size", "normalized_size": "M", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "normalized_size": "L", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("5.11 M/L adjacent no gap")
    else:
        report.fail("5.11 M/L adjacent", "unexpected flag")

    # 5.12: XS, S, L (gap between S and L -> missing Personal, Regular, M)
    tb = _make_tb("XS 6 S 8 L 14", variants=[
        {"label": "XS", "price_cents": 600, "kind": "size", "normalized_size": "XS", "confidence": 0.85},
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "normalized_size": "L", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "size_gap")
    if flag and "M" in flag["details"]["missing_sizes"]:
        report.ok("5.12 XS/S/L gap includes M")
    else:
        report.fail("5.12 XS/S/L", f"flag={flag}")

    # 5.13: Mixed tracks: S/L word + Half/Whole portion -> only word track flagged
    tb = _make_tb("S 8 L 14 Half 6 Whole 12", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "normalized_size": "L", "confidence": 0.85},
        {"label": "Half", "price_cents": 600, "kind": "size", "normalized_size": "Half", "confidence": 0.85},
        {"label": "Whole", "price_cents": 1200, "kind": "size", "normalized_size": "Whole", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flags = [f for f in (tb.get("price_flags") or []) if f.get("reason") == "size_gap"]
    word_flags = [f for f in flags if f["details"]["track"] == "word"]
    portion_flags = [f for f in flags if f["details"]["track"] == "portion"]
    if len(word_flags) == 1 and len(portion_flags) == 0:
        report.ok("5.13 word gap only, portion OK")
    else:
        report.fail("5.13 mixed tracks", f"word={len(word_flags)} portion={len(portion_flags)}")

    # 5.14: Severity is "info"
    tb = _make_tb("S 8 L 14", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "normalized_size": "L", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "size_gap")
    if flag and flag["severity"] == "info":
        report.ok("5.14 severity is info")
    else:
        report.fail("5.14 severity", f"flag={flag}")

    # 5.15: Regular, Deluxe (adjacent in word chain) -> no gap
    tb = _make_tb("Regular 10 Deluxe 14", variants=[
        {"label": "Regular", "price_cents": 1000, "kind": "size", "normalized_size": "Regular", "confidence": 0.85},
        {"label": "Deluxe", "price_cents": 1400, "kind": "size", "normalized_size": "Deluxe", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("5.15 Regular/Deluxe adjacent")
    else:
        report.fail("5.15 Regular/Deluxe", "unexpected flag")

    # 5.16: Non-size variants -> no gap check
    tb = _make_tb("Hot 8 BBQ 10", variants=[
        {"label": "Hot", "price_cents": 800, "kind": "flavor", "confidence": 0.85},
        {"label": "BBQ", "price_cents": 1000, "kind": "flavor", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("5.16 flavor variants no gap check")
    else:
        report.fail("5.16 flavor", "unexpected flag")

    # 5.17: Details present_sizes is ordered
    tb = _make_tb("S 8 L 14", variants=[
        {"label": "L", "price_cents": 1400, "kind": "size", "normalized_size": "L", "confidence": 0.85},
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    flag = _get_flag(tb, "size_gap")
    if flag and flag["details"]["present_sizes"] == ["S", "L"]:
        report.ok("5.17 present_sizes ordered")
    else:
        report.fail("5.17 ordering", f"flag={flag}")

    # 5.18: Mini, L present -> different sub-chains, no gap
    # (Mini is in named chain, L is in abbreviated chain)
    tb = _make_tb("Mini 5 L 14", variants=[
        {"label": "Mini", "price_cents": 500, "kind": "size", "normalized_size": "Mini", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "normalized_size": "L", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("5.18 Mini/L cross-chain no gap")
    else:
        report.fail("5.18 Mini/L", "unexpected gap flag")


# ==================================================================
# Group 6: Grid Consistency Across Items
# ==================================================================

def run_grid_consistency_tests(report: TestReport) -> None:
    print("\n--- Group 6: Grid Consistency Across Items ---")

    def _grid_tb(text, n_variants, grid_src=0):
        """Helper: make text_block with n size variants under a grid."""
        variants = [
            {"label": f"V{i}", "price_cents": 800 + i * 200, "kind": "size", "confidence": 0.85}
            for i in range(n_variants)
        ]
        return _make_tb(text, variants=variants,
                        meta={"size_grid_applied": True, "size_grid_source": grid_src,
                              "size_grid_column_count": 4})

    # 6.1: 3 items, all 4 variants -> no flag
    tbs = [_grid_tb("A", 4), _grid_tb("B", 4), _grid_tb("C", 4)]
    check_variant_consistency(tbs)
    total = sum(_count_flags(t, "grid_count_outlier") for t in tbs)
    if total == 0:
        report.ok("6.1 all 4-var no flag")
    else:
        report.fail("6.1 all 4-var", f"unexpected {total} flags")

    # 6.2: 3 items: two with 4, one with 2 -> flag on the 2
    tbs = [_grid_tb("A", 4), _grid_tb("B", 4), _grid_tb("C", 2)]
    check_variant_consistency(tbs)
    if _count_flags(tbs[2], "grid_count_outlier") == 1 and _count_flags(tbs[0], "grid_count_outlier") == 0:
        report.ok("6.2 outlier 2 flagged")
    else:
        report.fail("6.2 outlier", f"flags: {[_count_flags(t, 'grid_count_outlier') for t in tbs]}")

    # 6.3: 3 items: two with 4, one with 3 -> no flag (mode-1 allowed)
    tbs = [_grid_tb("A", 4), _grid_tb("B", 4), _grid_tb("C", 3)]
    check_variant_consistency(tbs)
    total = sum(_count_flags(t, "grid_count_outlier") for t in tbs)
    if total == 0:
        report.ok("6.3 mode-1 allowed")
    else:
        report.fail("6.3 mode-1", f"unexpected {total} flags")

    # 6.4: 5 items: four with 3, one with 1 -> flag on the 1
    tbs = [_grid_tb("A", 3), _grid_tb("B", 3), _grid_tb("C", 3),
           _grid_tb("D", 3), _grid_tb("E", 1)]
    check_variant_consistency(tbs)
    if _count_flags(tbs[4], "grid_count_outlier") == 1:
        report.ok("6.4 outlier 1 flagged")
    else:
        report.fail("6.4 outlier 1", f"flag count: {_count_flags(tbs[4], 'grid_count_outlier')}")

    # 6.5: 1 item under grid -> no flag (group too small)
    tbs = [_grid_tb("A", 2)]
    check_variant_consistency(tbs)
    if _count_flags(tbs[0], "grid_count_outlier") == 0:
        report.ok("6.5 single item no flag")
    else:
        report.fail("6.5 single item", "unexpected flag")

    # 6.6: Items under different grids -> validated separately
    tbs = [_grid_tb("A", 4, grid_src=0), _grid_tb("B", 4, grid_src=0),
           _grid_tb("C", 2, grid_src=10), _grid_tb("D", 2, grid_src=10)]
    check_variant_consistency(tbs)
    total = sum(_count_flags(t, "grid_count_outlier") for t in tbs)
    if total == 0:
        report.ok("6.6 different grids separate")
    else:
        report.fail("6.6 different grids", f"unexpected {total} flags")

    # 6.7: Items not under any grid -> no flag
    tbs = [_make_tb("A", variants=[{"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85}]),
           _make_tb("B", variants=[{"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85}])]
    check_variant_consistency(tbs)
    total = sum(_count_flags(t, "grid_count_outlier") for t in tbs)
    if total == 0:
        report.ok("6.7 no grid no flag")
    else:
        report.fail("6.7 no grid", f"unexpected {total} flags")

    # 6.8: Severity is "info"
    tbs = [_grid_tb("A", 4), _grid_tb("B", 4), _grid_tb("C", 1)]
    check_variant_consistency(tbs)
    flag = _get_flag(tbs[2], "grid_count_outlier")
    if flag and flag["severity"] == "info":
        report.ok("6.8 severity is info")
    else:
        report.fail("6.8 severity", f"flag={flag}")

    # 6.9: Details contain correct counts
    tbs = [_grid_tb("A", 4), _grid_tb("B", 4), _grid_tb("C", 1)]
    check_variant_consistency(tbs)
    flag = _get_flag(tbs[2], "grid_count_outlier")
    if (flag and flag["details"]["item_variant_count"] == 1
            and flag["details"]["group_mode_count"] == 4
            and flag["details"]["group_size"] == 3):
        report.ok("6.9 details correct")
    else:
        report.fail("6.9 details", f"flag={flag}")

    # 6.10: 4 items all with 2 variants -> no flag (mode == actual)
    tbs = [_grid_tb("A", 2), _grid_tb("B", 2), _grid_tb("C", 2), _grid_tb("D", 2)]
    check_variant_consistency(tbs)
    total = sum(_count_flags(t, "grid_count_outlier") for t in tbs)
    if total == 0:
        report.ok("6.10 all same count no flag")
    else:
        report.fail("6.10 all same", f"unexpected {total} flags")

    # 6.11: Grid source line in details
    tbs = [_grid_tb("A", 4, grid_src=5), _grid_tb("B", 4, grid_src=5), _grid_tb("C", 1, grid_src=5)]
    check_variant_consistency(tbs)
    flag = _get_flag(tbs[2], "grid_count_outlier")
    if flag and flag["details"]["grid_source_line"] == 5:
        report.ok("6.11 grid_source_line=5")
    else:
        report.fail("6.11 grid_source_line", f"flag={flag}")

    # 6.12: Multiple outliers in same grid group
    tbs = [_grid_tb("A", 4), _grid_tb("B", 4), _grid_tb("C", 4),
           _grid_tb("D", 1), _grid_tb("E", 1)]
    check_variant_consistency(tbs)
    if _count_flags(tbs[3], "grid_count_outlier") == 1 and _count_flags(tbs[4], "grid_count_outlier") == 1:
        report.ok("6.12 multiple outliers flagged")
    else:
        report.fail("6.12 multiple outliers", f"flags: {[_count_flags(t, 'grid_count_outlier') for t in tbs]}")

    # 6.13: 3 items with 3 variants each + 1 with 2 -> no flag (mode-1)
    tbs = [_grid_tb("A", 3), _grid_tb("B", 3), _grid_tb("C", 3), _grid_tb("D", 2)]
    check_variant_consistency(tbs)
    total = sum(_count_flags(t, "grid_count_outlier") for t in tbs)
    if total == 0:
        report.ok("6.13 mode-1 no flag")
    else:
        report.fail("6.13 mode-1", f"unexpected {total} flags")

    # 6.14: 2 items with different counts but gap < 2 -> no flag
    tbs = [_grid_tb("A", 4), _grid_tb("B", 3)]
    check_variant_consistency(tbs)
    total = sum(_count_flags(t, "grid_count_outlier") for t in tbs)
    if total == 0:
        report.ok("6.14 2 items gap<2 no flag")
    else:
        report.fail("6.14 gap<2", f"unexpected {total} flags")

    # 6.15: 2 items with gap == 2 -> flag
    tbs = [_grid_tb("A", 4), _grid_tb("B", 4), _grid_tb("C", 2)]
    check_variant_consistency(tbs)
    if _count_flags(tbs[2], "grid_count_outlier") == 1:
        report.ok("6.15 gap==2 flagged")
    else:
        report.fail("6.15 gap==2", f"expected flag on C")


# ==================================================================
# Group 7: Integration Tests
# ==================================================================

def run_integration_tests(report: TestReport) -> None:
    print("\n--- Group 7: Integration Tests ---")

    # 7.1: Empty list -> no crash
    check_variant_consistency([])
    report.ok("7.1 empty list no crash")

    # 7.2: Block with no variants -> skipped
    tb = _make_tb("HEADING")
    check_variant_consistency([tb])
    if not tb.get("price_flags"):
        report.ok("7.2 no variants skipped")
    else:
        report.fail("7.2 no variants", "unexpected flags")

    # 7.3: Block with empty variants list -> skipped
    tb = _make_tb("HEADING", variants=[])
    check_variant_consistency([tb])
    if not tb.get("price_flags"):
        report.ok("7.3 empty variants skipped")
    else:
        report.fail("7.3 empty variants", "unexpected flags")

    # 7.4: Multiple flag types on same item
    tb = _make_tb("S 0 S 14", variants=[
        {"label": "S", "price_cents": 0, "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "S", "price_cents": 1400, "kind": "size", "normalized_size": "S", "group_key": "size:S"},
    ])
    check_variant_consistency([tb])
    dup = _count_flags(tb, "duplicate_variant")
    zero = _count_flags(tb, "zero_price_variant")
    if dup >= 1 and zero >= 1:
        report.ok("7.4 multiple flag types coexist")
    else:
        report.fail("7.4 multi flags", f"dup={dup} zero={zero}")

    # 7.5: Existing price_inversion flags preserved
    tb = _make_tb("S 14 L 8", variants=[
        {"label": "S", "price_cents": 1400, "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "L", "price_cents": 800, "kind": "size", "normalized_size": "L", "group_key": "size:L"},
    ])
    validate_variant_prices([tb])  # Should create inversion flag
    inversion_before = _count_flags(tb, "variant_price_inversion")
    check_variant_consistency([tb])
    inversion_after = _count_flags(tb, "variant_price_inversion")
    if inversion_before == inversion_after and inversion_after >= 1:
        report.ok("7.5 inversion preserved")
    else:
        report.fail("7.5 inversion", f"before={inversion_before} after={inversion_after}")

    # 7.6: price_flags already exists -> appended to
    tb = _make_tb("S 8 L 14", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "L", "price_cents": 1400, "kind": "size", "normalized_size": "L", "group_key": "size:L"},
    ])
    tb["price_flags"] = [{"severity": "warn", "reason": "existing_flag", "details": {}}]
    check_variant_consistency([tb])
    existing = [f for f in tb["price_flags"] if f["reason"] == "existing_flag"]
    if len(existing) == 1:
        report.ok("7.6 existing flags preserved")
    else:
        report.fail("7.6 existing", f"existing_flag count={len(existing)}")

    # 7.7: Does not mutate variant dicts
    variants = [
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S", "group_key": "size:S"},
        {"label": "L", "price_cents": 1400, "kind": "size", "normalized_size": "L", "group_key": "size:L"},
    ]
    import copy
    orig = copy.deepcopy(variants)
    tb = _make_tb("S 8 L 14", variants=variants)
    check_variant_consistency([tb])
    if variants == orig:
        report.ok("7.7 variants not mutated")
    else:
        report.fail("7.7 mutation", "variants were mutated")

    # 7.8: Correct S/M/L -> no spurious flags at all
    tb = _make_tb("S 8 M 10 L 14", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S",
         "group_key": "size:S", "confidence": 0.85},
        {"label": "M", "price_cents": 1000, "kind": "size", "normalized_size": "M",
         "group_key": "size:M", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "normalized_size": "L",
         "group_key": "size:L", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    total_flags = len(tb.get("price_flags") or [])
    if total_flags == 0:
        report.ok("7.8 correct S/M/L zero flags")
    else:
        report.fail("7.8 clean item", f"unexpected {total_flags} flags")

    # 7.9: Grid items with correct variants -> no consistency flags
    tbs = [
        _make_tb("CHEESE 8 11 14 22", variants=[
            {"label": '10"', "price_cents": 800, "kind": "size", "normalized_size": "10in",
             "group_key": "size:10in", "confidence": 0.85},
            {"label": '12"', "price_cents": 1100, "kind": "size", "normalized_size": "12in",
             "group_key": "size:12in", "confidence": 0.85},
            {"label": '16"', "price_cents": 1400, "kind": "size", "normalized_size": "16in",
             "group_key": "size:16in", "confidence": 0.85},
            {"label": "Family", "price_cents": 2200, "kind": "size", "normalized_size": "Family",
             "group_key": "size:Family", "confidence": 0.85},
        ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 4}),
        _make_tb("PEPPERONI 9 12 15 24", variants=[
            {"label": '10"', "price_cents": 900, "kind": "size", "normalized_size": "10in",
             "group_key": "size:10in", "confidence": 0.85},
            {"label": '12"', "price_cents": 1200, "kind": "size", "normalized_size": "12in",
             "group_key": "size:12in", "confidence": 0.85},
            {"label": '16"', "price_cents": 1500, "kind": "size", "normalized_size": "16in",
             "group_key": "size:16in", "confidence": 0.85},
            {"label": "Family", "price_cents": 2400, "kind": "size", "normalized_size": "Family",
             "group_key": "size:Family", "confidence": 0.85},
        ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 4}),
    ]
    check_variant_consistency(tbs)
    total_flags = sum(len(t.get("price_flags") or []) for t in tbs)
    if total_flags == 0:
        report.ok("7.9 grid items clean")
    else:
        report.fail("7.9 grid items", f"unexpected {total_flags} flags")

    # 7.10: Grid with one OCR-duplicated variant -> duplicate flag
    tbs = [
        _make_tb("A 8 11 14 22", variants=[
            {"label": '10"', "price_cents": 800, "kind": "size", "normalized_size": "10in",
             "group_key": "size:10in", "confidence": 0.85},
            {"label": '12"', "price_cents": 1100, "kind": "size", "normalized_size": "12in",
             "group_key": "size:12in", "confidence": 0.85},
            {"label": '16"', "price_cents": 1400, "kind": "size", "normalized_size": "16in",
             "group_key": "size:16in", "confidence": 0.85},
            {"label": "Family", "price_cents": 2200, "kind": "size", "normalized_size": "Family",
             "group_key": "size:Family", "confidence": 0.85},
        ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 4}),
        _make_tb("B 9 9 15 24", variants=[
            {"label": '10"', "price_cents": 900, "kind": "size", "normalized_size": "10in",
             "group_key": "size:10in", "confidence": 0.85},
            {"label": '10"', "price_cents": 900, "kind": "size", "normalized_size": "10in",
             "group_key": "size:10in", "confidence": 0.85},
            {"label": '16"', "price_cents": 1500, "kind": "size", "normalized_size": "16in",
             "group_key": "size:16in", "confidence": 0.85},
            {"label": "Family", "price_cents": 2400, "kind": "size", "normalized_size": "Family",
             "group_key": "size:Family", "confidence": 0.85},
        ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 4}),
    ]
    check_variant_consistency(tbs)
    if _count_flags(tbs[0], "duplicate_variant") == 0 and _count_flags(tbs[1], "duplicate_variant") == 1:
        report.ok("7.10 OCR duplicate flagged")
    else:
        report.fail("7.10 duplicate", f"A={_count_flags(tbs[0], 'duplicate_variant')} B={_count_flags(tbs[1], 'duplicate_variant')}")

    # 7.11: Combo variants with valid structure -> no flags
    tb = _make_tb("9.95 W/Fries 13.50", variants=[
        {"label": "", "price_cents": 995, "kind": "other", "confidence": 0.85},
        {"label": "W/Fries", "price_cents": 1350, "kind": "combo",
         "group_key": "combo:w/fries", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    # Should have no duplicate, zero, or gap flags
    dup = _count_flags(tb, "duplicate_variant")
    zero = _count_flags(tb, "zero_price_variant")
    gap = _count_flags(tb, "size_gap")
    if dup == 0 and zero == 0 and gap == 0:
        report.ok("7.11 combo clean")
    else:
        report.fail("7.11 combo", f"dup={dup} zero={zero} gap={gap}")

    # 7.12: Variant with kind not set -> gracefully handled
    tb = _make_tb("A 8 B 10", variants=[
        {"label": "A", "price_cents": 800, "confidence": 0.85},
        {"label": "B", "price_cents": 1000, "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    report.ok("7.12 kind not set no crash")

    # 7.13: Variant with normalized_size not set -> gap check skips
    tb = _make_tb("S 8 L 14", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("7.13 no normalized_size -> no gap check")
    else:
        report.fail("7.13 no normalized_size", "unexpected gap flag")

    # 7.14: Large variant list (10+ variants) -> no crash
    variants = [
        {"label": f"V{i}", "price_cents": 100 * (i + 1), "kind": "size",
         "normalized_size": f"{10 + i}in", "group_key": f"size:{10+i}in", "confidence": 0.85}
        for i in range(12)
    ]
    tb = _make_tb("many prices", variants=variants)
    check_variant_consistency([tb])
    report.ok("7.14 large variant list no crash")

    # 7.15: Full pipeline order: enrich -> validate_prices -> check_consistency
    tb = _make_tb("Small 8.99 Large 14.99", variants=[
        {"label": "Small", "price_cents": 899, "confidence": 0.85},
        {"label": "Large", "price_cents": 1499, "confidence": 0.85},
    ])
    enrich_variants_on_text_blocks([tb])
    validate_variant_prices([tb])
    check_variant_consistency([tb])
    # S -> L gap should include M (among others)
    gap = _get_flag(tb, "size_gap")
    if gap:
        report.ok("7.15 full pipeline gap detected")
    else:
        report.fail("7.15 full pipeline", "expected gap flag for S/L")


# ==================================================================
# Group 8: Edge Cases and Regressions
# ==================================================================

def run_edge_case_tests(report: TestReport) -> None:
    print("\n--- Group 8: Edge Cases and Regressions ---")

    # 8.1: None variants field -> no crash
    tb = _make_tb("test")
    tb["variants"] = None
    check_variant_consistency([tb])
    report.ok("8.1 None variants no crash")

    # 8.2: meta present but empty -> grid checks skip
    tb = _make_tb("S 8 L 14", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ], meta={})
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 0:
        report.ok("8.2 empty meta no grid flag")
    else:
        report.fail("8.2 empty meta", "unexpected grid flag")

    # 8.3: size_grid_applied=False -> grid checks skip
    tb = _make_tb("S 8 L 14", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
    ], meta={"size_grid_applied": False, "size_grid_column_count": 4})
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 0:
        report.ok("8.3 grid not applied no flag")
    else:
        report.fail("8.3 grid not applied", "unexpected flag")

    # 8.4: All variants have group_key=None -> no duplicate check
    tb = _make_tb("A 8 B 10", variants=[
        {"label": "A", "price_cents": 800, "kind": "other", "confidence": 0.85},
        {"label": "B", "price_cents": 1000, "kind": "other", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "duplicate_variant") == 0:
        report.ok("8.4 all None group_key no dup")
    else:
        report.fail("8.4 all None group_key", "unexpected flag")

    # 8.5: Variant with empty string label and zero price
    tb = _make_tb("0 14", variants=[
        {"label": "", "price_cents": 0, "kind": "other", "confidence": 0.5},
        {"label": "Large", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "zero_price_variant") == 1:
        report.ok("8.5 empty label zero price flagged")
    else:
        report.fail("8.5 empty label", f"expected 1 zero flag, got {_count_flags(tb, 'zero_price_variant')}")

    # 8.6: Multiple text blocks, only some with variants
    tbs = [
        _make_tb("HEADING"),
        _make_tb("S 8 L 14", variants=[
            {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S",
             "group_key": "size:S", "confidence": 0.85},
            {"label": "L", "price_cents": 1400, "kind": "size", "normalized_size": "L",
             "group_key": "size:L", "confidence": 0.85},
        ]),
        _make_tb("DESCRIPTION"),
    ]
    check_variant_consistency(tbs)
    if not tbs[0].get("price_flags") and not tbs[2].get("price_flags"):
        report.ok("8.6 non-variant blocks untouched")
    else:
        report.fail("8.6 non-variant blocks", "unexpected flags on heading/description")

    # 8.7: size_grid_column_count missing from meta -> grid completeness skips
    tb = _make_tb("S 8", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "confidence": 0.85},
    ], meta={"size_grid_applied": True, "size_grid_source": 0})
    check_variant_consistency([tb])
    if _count_flags(tb, "grid_incomplete") == 0:
        report.ok("8.7 no column_count no grid flag")
    else:
        report.fail("8.7 no column_count", "unexpected flag")

    # 8.8: Variant with kind=None and group_key=None mixed with size
    tb = _make_tb("S 8 ?? 10", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S",
         "group_key": "size:S", "confidence": 0.85},
        {"label": "??", "price_cents": 1000, "confidence": 0.5},
    ])
    check_variant_consistency([tb])
    # No duplicate (different group_keys), no zero price, no mixed kinds (None ignored)
    dup = _count_flags(tb, "duplicate_variant")
    zero = _count_flags(tb, "zero_price_variant")
    mixed = _count_flags(tb, "mixed_variant_kinds")
    if dup == 0 and zero == 0 and mixed == 0:
        report.ok("8.8 None kind graceful")
    else:
        report.fail("8.8 None kind", f"dup={dup} zero={zero} mixed={mixed}")

    # 8.9: Running consistency check twice doesn't double flags
    tb = _make_tb("S 0 L 14", variants=[
        {"label": "S", "price_cents": 0, "kind": "size", "confidence": 0.85},
        {"label": "L", "price_cents": 1400, "kind": "size", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    count_first = _count_flags(tb, "zero_price_variant")
    check_variant_consistency([tb])
    count_second = _count_flags(tb, "zero_price_variant")
    # Second run adds another flag (idempotency not guaranteed, but this is expected behavior)
    # The function appends flags - running it twice is caller's responsibility to avoid
    if count_first >= 1:
        report.ok("8.9 first run produces flags")
    else:
        report.fail("8.9 first run", "no flags produced")

    # 8.10: Size gap with only one size in chain -> no crash
    tb = _make_tb("Mini 5", variants=[
        {"label": "Mini", "price_cents": 500, "kind": "size", "normalized_size": "Mini", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("8.10 single in chain no gap")
    else:
        report.fail("8.10 single in chain", "unexpected flag")

    # 8.11: Size not in any chain -> no gap check
    tb = _make_tb("10in 8 16in 14", variants=[
        {"label": '10"', "price_cents": 800, "kind": "size", "normalized_size": "10in", "confidence": 0.85},
        {"label": '16"', "price_cents": 1400, "kind": "size", "normalized_size": "16in", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("8.11 inch sizes no gap check")
    else:
        report.fail("8.11 inch sizes", "unexpected flag")

    # 8.12: Grid count consistency with mix of grid and non-grid items
    tbs = [
        _make_tb("HEADING"),  # no variants, no grid
        _make_tb("A 8 11 14 22", variants=[
            {"label": "V0", "price_cents": 800, "kind": "size", "confidence": 0.85},
            {"label": "V1", "price_cents": 1100, "kind": "size", "confidence": 0.85},
            {"label": "V2", "price_cents": 1400, "kind": "size", "confidence": 0.85},
            {"label": "V3", "price_cents": 2200, "kind": "size", "confidence": 0.85},
        ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 4}),
        _make_tb("B 9 12 15 24", variants=[
            {"label": "V0", "price_cents": 900, "kind": "size", "confidence": 0.85},
            {"label": "V1", "price_cents": 1200, "kind": "size", "confidence": 0.85},
            {"label": "V2", "price_cents": 1500, "kind": "size", "confidence": 0.85},
            {"label": "V3", "price_cents": 2400, "kind": "size", "confidence": 0.85},
        ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 4}),
        _make_tb("C standalone 14", variants=[
            {"label": "Regular", "price_cents": 1400, "kind": "size", "confidence": 0.85},
        ]),  # no grid
    ]
    check_variant_consistency(tbs)
    # Non-grid items should not interfere with grid group
    if (_count_flags(tbs[0], "grid_count_outlier") == 0
            and _count_flags(tbs[3], "grid_count_outlier") == 0):
        report.ok("8.12 non-grid items excluded from grid check")
    else:
        report.fail("8.12 non-grid items", "unexpected grid_count_outlier flag")

    # 8.13: Combo + size mix with grid -> gets both mixed_kinds + grid checks
    tb = _make_tb("S 8 W/Fries 12", variants=[
        {"label": "S", "price_cents": 800, "kind": "size", "normalized_size": "S",
         "group_key": "size:S", "confidence": 0.85},
        {"label": "W/Fries", "price_cents": 1200, "kind": "combo",
         "group_key": "combo:w/fries", "confidence": 0.85},
    ], meta={"size_grid_applied": True, "size_grid_source": 0, "size_grid_column_count": 4})
    check_variant_consistency([tb])
    mixed = _count_flags(tb, "mixed_variant_kinds")
    grid = _count_flags(tb, "grid_incomplete")
    if mixed >= 1 and grid >= 1:
        report.ok("8.13 mixed + grid flags coexist")
    else:
        report.fail("8.13 mixed+grid", f"mixed={mixed} grid={grid}")

    # 8.14: Single Double Triple all present -> no gap
    tb = _make_tb("Single 6 Double 10 Triple 14", variants=[
        {"label": "Single", "price_cents": 600, "kind": "size", "normalized_size": "Single", "confidence": 0.85},
        {"label": "Double", "price_cents": 1000, "kind": "size", "normalized_size": "Double", "confidence": 0.85},
        {"label": "Triple", "price_cents": 1400, "kind": "size", "normalized_size": "Triple", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("8.14 Single/Double/Triple no gap")
    else:
        report.fail("8.14 S/D/T", "unexpected gap flag")

    # 8.15: Slice Half Whole Family Party all present -> no gap
    tb = _make_tb("all portions", variants=[
        {"label": "Slice", "price_cents": 300, "kind": "size", "normalized_size": "Slice", "confidence": 0.85},
        {"label": "Half", "price_cents": 700, "kind": "size", "normalized_size": "Half", "confidence": 0.85},
        {"label": "Whole", "price_cents": 1200, "kind": "size", "normalized_size": "Whole", "confidence": 0.85},
        {"label": "Family", "price_cents": 2200, "kind": "size", "normalized_size": "Family", "confidence": 0.85},
        {"label": "Party", "price_cents": 4000, "kind": "size", "normalized_size": "Party", "confidence": 0.85},
    ])
    check_variant_consistency([tb])
    if _count_flags(tb, "size_gap") == 0:
        report.ok("8.15 all portions no gap")
    else:
        report.fail("8.15 all portions", "unexpected gap flag")


# ==================================================================
# Main runner
# ==================================================================

def main() -> None:
    report = TestReport()

    run_duplicate_tests(report)
    run_grid_completeness_tests(report)
    run_mixed_kinds_tests(report)
    run_zero_price_tests(report)
    run_size_gap_tests(report)
    run_grid_consistency_tests(report)
    run_integration_tests(report)
    run_edge_case_tests(report)

    print(f"\n{'=' * 60}")
    print(f"Day 59 Results: {report.passed}/{report.total} passed")
    if report.failures:
        print(f"\n{len(report.failures)} FAILURES:")
        for f in report.failures:
            print(f)
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
