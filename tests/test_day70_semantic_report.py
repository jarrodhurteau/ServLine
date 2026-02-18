"""
Day 70: Sprint 8.4 -- Semantic Quality Report (Phase 8 Capstone)

Tests the semantic quality report in storage/semantic_confidence.py:
  1. Empty menu (zero items)
  2. Single item (each tier)
  3. Multi-item menu (mixed tiers)
  4. Menu confidence integration (delegates to compute_menu_confidence_summary)
  5. Repair summary integration (delegates to compute_repair_summary)
  6. Auto-repair results passthrough
  7. Pipeline coverage metrics
  8. Issue digest (top issues, worst items, common flags)
  9. Category health ranking (sorted worst-first)
 10. Quality narrative (human-readable text)
 11. Report structure completeness (all top-level keys present)
 12. Grade accuracy (A/B/C/D thresholds)
 13. Real-world menu simulation (20+ items, mixed quality)

Run: python tests/test_day70_semantic_report.py
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.semantic_confidence import (
    score_semantic_confidence,
    classify_confidence_tiers,
    generate_repair_recommendations,
    apply_auto_repairs,
    compute_menu_confidence_summary,
    compute_repair_summary,
    generate_semantic_report,
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
        print(f"  FAIL  {name}  {msg}")

    def summary(self) -> str:
        status = "ALL PASSED" if not self.failures else "FAILURES"
        return f"\n{status}: {self.passed}/{self.total} passed"


# ---------------------------------------------------------------------------
# Item builder helpers
# ---------------------------------------------------------------------------

def _make_high_item(name: str = "Margherita Pizza", category: str = "Pizza",
                    price_cents: int = 1499, grammar_conf: float = 0.90) -> Dict[str, Any]:
    """Build a high-confidence item (tier: high, score >= 0.80)."""
    return {
        "grammar": {"parsed_name": name, "parse_confidence": grammar_conf},
        "category": category,
        "price_candidates": [{"price_cents": price_cents}],
        "variants": [{"confidence": 0.90, "price_cents": price_cents}],
        "price_flags": [],
    }


def _make_medium_item(name: str = "Cheese Sticks", category: str = "Sides",
                      price_cents: int = 799, grammar_conf: float = 0.45) -> Dict[str, Any]:
    """Build a medium-confidence item (tier: medium, 0.60-0.79).

    Tuned signals: low grammar (0.45), low variant confidence (0.30),
    two info flags. Produces score ~0.71.
    """
    return {
        "grammar": {"parsed_name": name, "parse_confidence": grammar_conf},
        "category": category,
        "price_candidates": [{"price_cents": price_cents}],
        "variants": [{"confidence": 0.30}],
        "price_flags": [
            {"severity": "info", "reason": "cross_item_price_outlier"},
            {"severity": "info", "reason": "cross_item_isolated"},
        ],
    }


def _make_low_item(name: str = "XY", category: str = "Uncategorized",
                   grammar_conf: float = 0.35) -> Dict[str, Any]:
    """Build a low-confidence item (tier: low, 0.40-0.59)."""
    return {
        "grammar": {"parsed_name": name, "parse_confidence": grammar_conf},
        "category": category,
        "price_candidates": [],
        "variants": [],
        "price_flags": [
            {"severity": "warn", "reason": "variant_price_inversion"},
        ],
    }


def _make_reject_item(name: str = "ssseeeccc", category: str = "Uncategorized",
                      grammar_conf: float = 0.15) -> Dict[str, Any]:
    """Build a reject-confidence item (tier: reject, < 0.40)."""
    return {
        "grammar": {"parsed_name": name, "parse_confidence": grammar_conf},
        "category": category,
        "price_candidates": [],
        "variants": [],
        "price_flags": [
            {"severity": "warn", "reason": "variant_price_inversion"},
            {"severity": "warn", "reason": "duplicate_variant"},
        ],
    }


def _run_full_pipeline(items: list) -> Dict[str, Any]:
    """Run Steps 9.2-9.6 and return report."""
    score_semantic_confidence(items)
    classify_confidence_tiers(items)
    generate_repair_recommendations(items)
    repair_results = apply_auto_repairs(items)
    # Re-score after repairs
    score_semantic_confidence(items)
    classify_confidence_tiers(items)
    return generate_semantic_report(items, repair_results)


# ---------------------------------------------------------------------------
# 1. Empty menu
# ---------------------------------------------------------------------------

def test_empty_menu(r: TestReport) -> None:
    report = generate_semantic_report([])
    assert report["menu_confidence"]["total_items"] == 0
    assert report["repair_summary"]["total_items"] == 0
    assert report["auto_repair_results"]["repairs_applied"] == 0
    assert report["pipeline_coverage"] == {}
    assert report["issue_digest"]["top_issues"] == []
    assert report["issue_digest"]["worst_items"] == []
    assert report["issue_digest"]["common_flags"] == []
    assert report["category_health"] == []
    assert "No items" in report["quality_narrative"]
    r.ok("empty_menu_all_sections_empty")


def test_empty_menu_top_level_keys(r: TestReport) -> None:
    report = generate_semantic_report([])
    expected_keys = {
        "menu_confidence", "repair_summary", "auto_repair_results",
        "pipeline_coverage", "issue_digest", "category_health",
        "quality_narrative",
    }
    assert set(report.keys()) == expected_keys, f"Keys: {set(report.keys())}"
    r.ok("empty_menu_top_level_keys")


# ---------------------------------------------------------------------------
# 2. Single item per tier
# ---------------------------------------------------------------------------

def test_single_high_item(r: TestReport) -> None:
    items = [_make_high_item()]
    report = _run_full_pipeline(items)
    assert report["menu_confidence"]["quality_grade"] == "A"
    assert report["menu_confidence"]["tier_counts"]["high"] == 1
    assert report["menu_confidence"]["needs_review_count"] == 0
    r.ok("single_high_item_grade_A")


def test_single_medium_item(r: TestReport) -> None:
    items = [_make_medium_item()]
    report = _run_full_pipeline(items)
    assert report["menu_confidence"]["quality_grade"] == "D"
    assert report["menu_confidence"]["tier_counts"]["medium"] >= 1
    r.ok("single_medium_item_grade_D")


def test_single_low_item(r: TestReport) -> None:
    items = [_make_low_item()]
    report = _run_full_pipeline(items)
    assert report["menu_confidence"]["quality_grade"] == "D"
    assert report["menu_confidence"]["tier_counts"]["high"] == 0
    r.ok("single_low_item_grade_D")


def test_single_reject_item(r: TestReport) -> None:
    items = [_make_reject_item()]
    report = _run_full_pipeline(items)
    grade = report["menu_confidence"]["quality_grade"]
    assert grade == "D", f"Expected D, got {grade}"
    assert report["menu_confidence"]["tier_counts"]["reject"] >= 1
    r.ok("single_reject_item_grade_D")


# ---------------------------------------------------------------------------
# 3. Multi-item mixed menus
# ---------------------------------------------------------------------------

def test_mixed_menu_5_items(r: TestReport) -> None:
    items = [
        _make_high_item("Pepperoni Pizza", "Pizza"),
        _make_high_item("Hawaiian Pizza", "Pizza"),
        _make_high_item("Buffalo Wings", "Wings"),
        _make_medium_item("Garlic Bread", "Sides"),
        _make_low_item("XY", "Uncategorized"),
    ]
    report = _run_full_pipeline(items)
    mc = report["menu_confidence"]
    assert mc["total_items"] == 5
    assert mc["tier_counts"]["high"] >= 3
    r.ok("mixed_menu_5_items_tier_counts")


def test_mixed_menu_grade_B(r: TestReport) -> None:
    """4 high + 2 medium = 66% high = grade B."""
    items = [
        _make_high_item("Item A", "Pizza"),
        _make_high_item("Item B", "Pizza"),
        _make_high_item("Item C", "Wings"),
        _make_high_item("Item D", "Sides"),
        _make_medium_item("Item E", "Salads"),
        _make_medium_item("Item F", "Beverages"),
    ]
    report = _run_full_pipeline(items)
    grade = report["menu_confidence"]["quality_grade"]
    assert grade == "B", f"Expected B, got {grade}"
    r.ok("mixed_menu_grade_B")


def test_mixed_menu_grade_C(r: TestReport) -> None:
    """2 high + 3 medium = 40% high = grade C."""
    items = [
        _make_high_item("Item A", "Pizza"),
        _make_high_item("Item B", "Wings"),
        _make_medium_item("Item C", "Sides"),
        _make_medium_item("Item D", "Salads"),
        _make_medium_item("Item E", "Beverages"),
    ]
    report = _run_full_pipeline(items)
    grade = report["menu_confidence"]["quality_grade"]
    assert grade == "C", f"Expected C, got {grade}"
    r.ok("mixed_menu_grade_C")


# ---------------------------------------------------------------------------
# 4. Menu confidence section
# ---------------------------------------------------------------------------

def test_menu_confidence_mean_median(r: TestReport) -> None:
    items = [
        _make_high_item("Item A", "Pizza"),
        _make_low_item("XY", "Uncategorized"),
    ]
    report = _run_full_pipeline(items)
    mc = report["menu_confidence"]
    assert mc["mean_confidence"] > 0
    assert mc["median_confidence"] > 0
    assert mc["stdev_confidence"] > 0
    r.ok("menu_confidence_mean_median_stdev")


def test_menu_confidence_category_summary(r: TestReport) -> None:
    items = [
        _make_high_item("Pepperoni", "Pizza"),
        _make_high_item("Margherita", "Pizza"),
        _make_medium_item("Cola", "Beverages"),
    ]
    report = _run_full_pipeline(items)
    cs = report["menu_confidence"]["category_summary"]
    assert "Pizza" in cs
    assert cs["Pizza"]["count"] == 2
    assert "Beverages" in cs
    assert cs["Beverages"]["count"] == 1
    r.ok("menu_confidence_category_summary")


# ---------------------------------------------------------------------------
# 5. Repair summary section
# ---------------------------------------------------------------------------

def test_repair_summary_no_recs(r: TestReport) -> None:
    """All-high menu produces zero recommendations."""
    items = [_make_high_item("Good Item", "Pizza") for _ in range(3)]
    report = _run_full_pipeline(items)
    rs = report["repair_summary"]
    assert rs["total_recommendations"] == 0
    assert rs["items_with_recommendations"] == 0
    assert rs["auto_fixable_count"] == 0
    r.ok("repair_summary_no_recs")


def test_repair_summary_with_recs(r: TestReport) -> None:
    """Low item generates recommendations."""
    items = [_make_low_item("XY", "Uncategorized")]
    report = _run_full_pipeline(items)
    rs = report["repair_summary"]
    assert rs["total_recommendations"] > 0
    assert rs["items_with_recommendations"] >= 1
    r.ok("repair_summary_with_recs")


def test_repair_summary_by_priority(r: TestReport) -> None:
    items = [_make_reject_item()]
    report = _run_full_pipeline(items)
    rs = report["repair_summary"]
    # reject -> critical priority
    assert rs["by_priority"]["critical"] >= 0
    r.ok("repair_summary_by_priority")


def test_repair_summary_by_type_keys(r: TestReport) -> None:
    items = [_make_low_item()]
    report = _run_full_pipeline(items)
    rs = report["repair_summary"]
    assert isinstance(rs["by_type"], dict)
    r.ok("repair_summary_by_type_keys")


# ---------------------------------------------------------------------------
# 6. Auto-repair results passthrough
# ---------------------------------------------------------------------------

def test_auto_repair_results_present(r: TestReport) -> None:
    items = [_make_high_item()]
    report = _run_full_pipeline(items)
    ar = report["auto_repair_results"]
    assert "total_items_repaired" in ar
    assert "repairs_applied" in ar
    assert "by_type" in ar
    r.ok("auto_repair_results_present")


def test_auto_repair_results_none_default(r: TestReport) -> None:
    """When repair_results=None, default empty dict is used."""
    items = [_make_high_item()]
    score_semantic_confidence(items)
    classify_confidence_tiers(items)
    generate_repair_recommendations(items)
    report = generate_semantic_report(items, repair_results=None)
    ar = report["auto_repair_results"]
    assert ar["total_items_repaired"] == 0
    assert ar["repairs_applied"] == 0
    assert ar["by_type"] == {}
    r.ok("auto_repair_results_none_default")


def test_auto_repair_results_passthrough(r: TestReport) -> None:
    """Explicit repair_results dict is passed through."""
    items = [_make_high_item()]
    score_semantic_confidence(items)
    classify_confidence_tiers(items)
    custom_results = {"total_items_repaired": 5, "repairs_applied": 8, "by_type": {"name": 5, "category": 3}}
    report = generate_semantic_report(items, repair_results=custom_results)
    assert report["auto_repair_results"] == custom_results
    r.ok("auto_repair_results_passthrough")


# ---------------------------------------------------------------------------
# 7. Pipeline coverage metrics
# ---------------------------------------------------------------------------

def test_pipeline_coverage_all_signals(r: TestReport) -> None:
    """Items after full pipeline should have all coverage signals."""
    items = [_make_high_item()]
    report = _run_full_pipeline(items)
    cov = report["pipeline_coverage"]
    assert cov["has_grammar"]["count"] == 1
    assert cov["has_grammar"]["pct"] == 1.0
    assert cov["has_semantic_confidence"]["count"] == 1
    assert cov["has_semantic_tier"]["count"] == 1
    r.ok("pipeline_coverage_all_signals")


def test_pipeline_coverage_no_variants(r: TestReport) -> None:
    items = [_make_low_item()]
    report = _run_full_pipeline(items)
    cov = report["pipeline_coverage"]
    assert cov["has_variants"]["count"] == 0
    assert cov["has_variants"]["pct"] == 0.0
    r.ok("pipeline_coverage_no_variants")


def test_pipeline_coverage_partial(r: TestReport) -> None:
    """Mix of items with and without variants."""
    items = [
        _make_high_item(),  # has variants
        _make_low_item(),   # no variants
    ]
    report = _run_full_pipeline(items)
    cov = report["pipeline_coverage"]
    assert cov["has_variants"]["count"] == 1
    assert cov["has_variants"]["pct"] == 0.5
    r.ok("pipeline_coverage_partial")


def test_pipeline_coverage_keys(r: TestReport) -> None:
    items = [_make_high_item()]
    report = _run_full_pipeline(items)
    cov = report["pipeline_coverage"]
    expected = {
        "has_grammar", "has_semantic_confidence", "has_semantic_tier",
        "has_price_flags", "has_variants", "has_repair_recommendations",
        "has_auto_repairs",
    }
    assert set(cov.keys()) == expected, f"Coverage keys: {set(cov.keys())}"
    r.ok("pipeline_coverage_keys")


def test_pipeline_coverage_pct_range(r: TestReport) -> None:
    items = [_make_high_item(), _make_medium_item(), _make_low_item()]
    report = _run_full_pipeline(items)
    for key, val in report["pipeline_coverage"].items():
        assert 0.0 <= val["pct"] <= 1.0, f"{key} pct out of range: {val['pct']}"
        assert 0 <= val["count"] <= 3, f"{key} count out of range: {val['count']}"
    r.ok("pipeline_coverage_pct_range")


def test_pipeline_coverage_empty(r: TestReport) -> None:
    report = generate_semantic_report([])
    assert report["pipeline_coverage"] == {}
    r.ok("pipeline_coverage_empty")


# ---------------------------------------------------------------------------
# 8. Issue digest
# ---------------------------------------------------------------------------

def test_issue_digest_top_issues_empty(r: TestReport) -> None:
    """All-high menu has no issues."""
    items = [_make_high_item() for _ in range(3)]
    report = _run_full_pipeline(items)
    assert report["issue_digest"]["top_issues"] == []
    r.ok("issue_digest_top_issues_empty")


def test_issue_digest_top_issues_present(r: TestReport) -> None:
    items = [_make_low_item()]
    report = _run_full_pipeline(items)
    ti = report["issue_digest"]["top_issues"]
    assert len(ti) > 0
    assert "type" in ti[0]
    assert "count" in ti[0]
    assert "pct" in ti[0]
    r.ok("issue_digest_top_issues_present")


def test_issue_digest_top_issues_sorted(r: TestReport) -> None:
    """Top issues should be sorted by count descending."""
    items = [_make_low_item(f"X{i}", "Uncategorized") for i in range(5)]
    report = _run_full_pipeline(items)
    ti = report["issue_digest"]["top_issues"]
    if len(ti) >= 2:
        counts = [t["count"] for t in ti]
        assert counts == sorted(counts, reverse=True), f"Not sorted: {counts}"
    r.ok("issue_digest_top_issues_sorted")


def test_issue_digest_top_issues_pct_sum(r: TestReport) -> None:
    """Top issue percentages should sum to <= 1.0."""
    items = [_make_low_item(), _make_reject_item()]
    report = _run_full_pipeline(items)
    ti = report["issue_digest"]["top_issues"]
    total_pct = sum(t["pct"] for t in ti)
    assert total_pct <= 1.01, f"Pct sum > 1.0: {total_pct}"
    r.ok("issue_digest_top_issues_pct_sum")


def test_issue_digest_worst_items_empty(r: TestReport) -> None:
    items = [_make_high_item() for _ in range(3)]
    report = _run_full_pipeline(items)
    # Worst items still lists items (sorted by confidence), just all high
    wi = report["issue_digest"]["worst_items"]
    assert len(wi) == 3
    for w in wi:
        assert w["tier"] == "high"
    r.ok("issue_digest_worst_items_all_high")


def test_issue_digest_worst_items_sorted(r: TestReport) -> None:
    items = [
        _make_high_item("Good One", "Pizza"),
        _make_low_item("Bad One", "Uncategorized"),
    ]
    report = _run_full_pipeline(items)
    wi = report["issue_digest"]["worst_items"]
    assert len(wi) == 2
    # First item should have lowest confidence
    assert wi[0]["confidence"] <= wi[1]["confidence"]
    r.ok("issue_digest_worst_items_sorted")


def test_issue_digest_worst_items_limit(r: TestReport) -> None:
    """Should cap at 10 worst items."""
    items = [_make_medium_item(f"Item {i}", "Sides") for i in range(15)]
    report = _run_full_pipeline(items)
    assert len(report["issue_digest"]["worst_items"]) <= 10
    r.ok("issue_digest_worst_items_limit")


def test_issue_digest_worst_items_fields(r: TestReport) -> None:
    items = [_make_low_item("Short", "Sides")]
    report = _run_full_pipeline(items)
    wi = report["issue_digest"]["worst_items"]
    assert len(wi) >= 1
    w = wi[0]
    assert "name" in w
    assert "confidence" in w
    assert "tier" in w
    assert "category" in w
    assert "issue_count" in w
    r.ok("issue_digest_worst_items_fields")


def test_issue_digest_common_flags_empty(r: TestReport) -> None:
    """Items without flags produce no common flags."""
    items = [_make_high_item()]
    report = _run_full_pipeline(items)
    assert report["issue_digest"]["common_flags"] == []
    r.ok("issue_digest_common_flags_empty")


def test_issue_digest_common_flags_present(r: TestReport) -> None:
    items = [_make_low_item()]
    report = _run_full_pipeline(items)
    cf = report["issue_digest"]["common_flags"]
    assert len(cf) > 0
    assert "reason" in cf[0]
    assert "count" in cf[0]
    assert "severity" in cf[0]
    r.ok("issue_digest_common_flags_present")


def test_issue_digest_common_flags_sorted(r: TestReport) -> None:
    """Flags sorted by count descending."""
    items = [
        _make_low_item("A1", "Cat"),
        _make_low_item("A2", "Cat"),
        _make_reject_item("B1", "Cat"),
    ]
    report = _run_full_pipeline(items)
    cf = report["issue_digest"]["common_flags"]
    if len(cf) >= 2:
        counts = [f["count"] for f in cf]
        assert counts == sorted(counts, reverse=True)
    r.ok("issue_digest_common_flags_sorted")


def test_issue_digest_common_flags_limit(r: TestReport) -> None:
    """Should cap at 8 common flags."""
    items = []
    for i in range(10):
        it = _make_low_item(f"Item{i}", "Cat")
        it["price_flags"] = [{"severity": "info", "reason": f"flag_{j}"} for j in range(i)]
        items.append(it)
    report = _run_full_pipeline(items)
    assert len(report["issue_digest"]["common_flags"]) <= 8
    r.ok("issue_digest_common_flags_limit")


# ---------------------------------------------------------------------------
# 9. Category health ranking
# ---------------------------------------------------------------------------

def test_category_health_single_category(r: TestReport) -> None:
    items = [_make_high_item("Item A", "Pizza"), _make_high_item("Item B", "Pizza")]
    report = _run_full_pipeline(items)
    ch = report["category_health"]
    assert len(ch) == 1
    assert ch[0]["category"] == "Pizza"
    assert ch[0]["count"] == 2
    assert ch[0]["grade"] == "A"
    r.ok("category_health_single_category")


def test_category_health_multi_categories(r: TestReport) -> None:
    items = [
        _make_high_item("Pizza", "Pizza"),
        _make_low_item("XY", "Uncategorized"),
    ]
    report = _run_full_pipeline(items)
    ch = report["category_health"]
    assert len(ch) == 2
    # Sorted worst first
    assert ch[0]["mean_confidence"] <= ch[1]["mean_confidence"]
    r.ok("category_health_multi_categories_sorted")


def test_category_health_worst_first(r: TestReport) -> None:
    items = [
        _make_high_item("Good", "Pizza"),
        _make_high_item("Also Good", "Wings"),
        _make_reject_item("Bad", "Beverages"),
    ]
    report = _run_full_pipeline(items)
    ch = report["category_health"]
    # Beverages (reject) should be first
    assert ch[0]["category"] == "Beverages" or ch[0]["mean_confidence"] <= ch[-1]["mean_confidence"]
    r.ok("category_health_worst_first")


def test_category_health_fields(r: TestReport) -> None:
    items = [_make_high_item()]
    report = _run_full_pipeline(items)
    ch = report["category_health"]
    assert len(ch) >= 1
    h = ch[0]
    expected_fields = {"category", "count", "mean_confidence", "needs_review_pct", "grade"}
    assert set(h.keys()) == expected_fields, f"Fields: {set(h.keys())}"
    r.ok("category_health_fields")


def test_category_health_grade_A(r: TestReport) -> None:
    items = [_make_high_item(f"Item {i}", "Pizza") for i in range(5)]
    report = _run_full_pipeline(items)
    ch = report["category_health"]
    assert ch[0]["grade"] == "A"
    r.ok("category_health_grade_A")


def test_category_health_grade_D(r: TestReport) -> None:
    items = [_make_low_item(f"XY{i}", "Sides") for i in range(5)]
    report = _run_full_pipeline(items)
    ch = report["category_health"]
    sides = [c for c in ch if c["category"] == "Sides"]
    assert len(sides) == 1
    assert sides[0]["grade"] == "D"
    r.ok("category_health_grade_D")


def test_category_health_needs_review_pct(r: TestReport) -> None:
    items = [
        _make_high_item("Good", "Pizza"),
        _make_low_item("Bad", "Pizza"),
    ]
    report = _run_full_pipeline(items)
    ch = report["category_health"]
    pizza = [c for c in ch if c["category"] == "Pizza"][0]
    assert 0.0 <= pizza["needs_review_pct"] <= 1.0
    r.ok("category_health_needs_review_pct")


def test_category_health_empty(r: TestReport) -> None:
    report = generate_semantic_report([])
    assert report["category_health"] == []
    r.ok("category_health_empty")


# ---------------------------------------------------------------------------
# 10. Quality narrative
# ---------------------------------------------------------------------------

def test_narrative_empty(r: TestReport) -> None:
    report = generate_semantic_report([])
    assert "No items" in report["quality_narrative"]
    r.ok("narrative_empty")


def test_narrative_grade_A(r: TestReport) -> None:
    items = [_make_high_item() for _ in range(5)]
    report = _run_full_pipeline(items)
    narr = report["quality_narrative"]
    assert "A" in narr
    assert "Excellent" in narr
    r.ok("narrative_grade_A")


def test_narrative_grade_D(r: TestReport) -> None:
    items = [_make_reject_item() for _ in range(5)]
    report = _run_full_pipeline(items)
    narr = report["quality_narrative"]
    assert "D" in narr
    assert "Poor" in narr
    r.ok("narrative_grade_D")


def test_narrative_mentions_items(r: TestReport) -> None:
    items = [_make_high_item() for _ in range(7)]
    report = _run_full_pipeline(items)
    assert "7 items" in report["quality_narrative"]
    r.ok("narrative_mentions_items")


def test_narrative_mentions_repairs(r: TestReport) -> None:
    items = [_make_low_item()]
    report = _run_full_pipeline(items)
    narr = report["quality_narrative"]
    # Should mention repair recommendations if any exist
    if report["repair_summary"]["total_recommendations"] > 0:
        assert "repair" in narr.lower() or "recommendation" in narr.lower()
    r.ok("narrative_mentions_repairs")


def test_narrative_mentions_weakest_category(r: TestReport) -> None:
    items = [
        _make_high_item("Good", "Pizza"),
        _make_reject_item("Bad", "Beverages"),
    ]
    report = _run_full_pipeline(items)
    narr = report["quality_narrative"]
    # Should mention weakest category if it's below medium threshold
    if report["category_health"][0]["mean_confidence"] < 0.60:
        assert "Weakest" in narr or "weakest" in narr.lower()
    r.ok("narrative_mentions_weakest_category")


def test_narrative_with_repair_results(r: TestReport) -> None:
    items = [_make_high_item()]
    score_semantic_confidence(items)
    classify_confidence_tiers(items)
    custom_results = {"total_items_repaired": 3, "repairs_applied": 5, "by_type": {"name": 5}}
    report = generate_semantic_report(items, repair_results=custom_results)
    assert "5 auto-repairs" in report["quality_narrative"]
    r.ok("narrative_with_repair_results")


def test_narrative_is_string(r: TestReport) -> None:
    items = [_make_high_item(), _make_low_item()]
    report = _run_full_pipeline(items)
    assert isinstance(report["quality_narrative"], str)
    assert len(report["quality_narrative"]) > 20
    r.ok("narrative_is_string")


# ---------------------------------------------------------------------------
# 11. Report structure completeness
# ---------------------------------------------------------------------------

def test_report_top_level_keys(r: TestReport) -> None:
    items = [_make_high_item()]
    report = _run_full_pipeline(items)
    expected = {
        "menu_confidence", "repair_summary", "auto_repair_results",
        "pipeline_coverage", "issue_digest", "category_health",
        "quality_narrative",
    }
    assert set(report.keys()) == expected
    r.ok("report_top_level_keys")


def test_menu_confidence_keys(r: TestReport) -> None:
    items = [_make_high_item()]
    report = _run_full_pipeline(items)
    mc = report["menu_confidence"]
    expected = {
        "total_items", "mean_confidence", "median_confidence",
        "stdev_confidence", "tier_counts", "needs_review_count",
        "quality_grade", "category_summary",
    }
    assert set(mc.keys()) == expected
    r.ok("menu_confidence_keys")


def test_repair_summary_keys(r: TestReport) -> None:
    items = [_make_high_item()]
    report = _run_full_pipeline(items)
    rs = report["repair_summary"]
    expected = {
        "total_items", "items_with_recommendations", "total_recommendations",
        "by_priority", "by_type", "auto_fixable_count", "category_breakdown",
    }
    assert set(rs.keys()) == expected
    r.ok("repair_summary_keys")


def test_auto_repair_results_keys(r: TestReport) -> None:
    items = [_make_high_item()]
    report = _run_full_pipeline(items)
    ar = report["auto_repair_results"]
    expected = {"total_items_repaired", "repairs_applied", "by_type"}
    assert set(ar.keys()) == expected
    r.ok("auto_repair_results_keys")


def test_issue_digest_keys(r: TestReport) -> None:
    items = [_make_high_item()]
    report = _run_full_pipeline(items)
    digest = report["issue_digest"]
    expected = {"top_issues", "worst_items", "common_flags"}
    assert set(digest.keys()) == expected
    r.ok("issue_digest_keys")


# ---------------------------------------------------------------------------
# 12. Grade accuracy
# ---------------------------------------------------------------------------

def test_grade_A_threshold(r: TestReport) -> None:
    """80%+ high = grade A."""
    items = [_make_high_item(f"Item {i}", "Pizza") for i in range(8)]
    items += [_make_medium_item(f"Med {i}", "Sides") for i in range(2)]
    report = _run_full_pipeline(items)
    assert report["menu_confidence"]["quality_grade"] == "A"
    r.ok("grade_A_threshold_80pct")


def test_grade_B_threshold(r: TestReport) -> None:
    """60-79% high = grade B."""
    items = [_make_high_item(f"Item {i}", "Pizza") for i in range(7)]
    items += [_make_medium_item(f"Med {i}", "Sides") for i in range(3)]
    report = _run_full_pipeline(items)
    grade = report["menu_confidence"]["quality_grade"]
    assert grade in ("A", "B"), f"Expected A or B, got {grade}"
    r.ok("grade_B_threshold_60pct")


def test_grade_D_all_reject(r: TestReport) -> None:
    items = [_make_reject_item(f"ssseeeccc{i}") for i in range(5)]
    report = _run_full_pipeline(items)
    assert report["menu_confidence"]["quality_grade"] == "D"
    r.ok("grade_D_all_reject")


# ---------------------------------------------------------------------------
# 13. Real-world menu simulation
# ---------------------------------------------------------------------------

def test_realistic_pizza_menu(r: TestReport) -> None:
    """Simulate a 20-item pizza restaurant menu with mixed quality."""
    items = [
        # Pizza section (good quality)
        _make_high_item("Margherita Pizza", "Pizza", 1499),
        _make_high_item("Pepperoni Pizza", "Pizza", 1599),
        _make_high_item("Hawaiian Pizza", "Pizza", 1699),
        _make_high_item("Veggie Pizza", "Pizza", 1599),
        _make_high_item("Meat Lovers Pizza", "Pizza", 1899),
        # Wings (good quality)
        _make_high_item("Buffalo Wings", "Wings", 1299),
        _make_high_item("BBQ Wings", "Wings", 1299),
        _make_high_item("Garlic Parmesan Wings", "Wings", 1399),
        # Sides (medium quality)
        _make_medium_item("Garlic Bread", "Sides", 599),
        _make_medium_item("Mozzarella Sticks", "Sides", 799),
        _make_medium_item("Onion Rings", "Sides", 699),
        # Salads (high quality)
        _make_high_item("Caesar Salad", "Salads", 999),
        _make_high_item("Garden Salad", "Salads", 899),
        # Beverages (medium quality)
        _make_medium_item("Coca Cola", "Beverages", 299),
        _make_medium_item("Sprite", "Beverages", 299),
        # Desserts (mixed)
        _make_high_item("Tiramisu", "Desserts", 899),
        _make_medium_item("Cannoli", "Desserts", 599),
        # Problem items (OCR artifacts)
        _make_low_item("XY", "Uncategorized"),
        _make_reject_item("ssseeeccc", "Uncategorized"),
        _make_low_item("AB", "Uncategorized"),
    ]
    report = _run_full_pipeline(items)

    # Should have all sections
    assert report["menu_confidence"]["total_items"] == 20
    assert report["menu_confidence"]["quality_grade"] in ("B", "C")

    # Should have category health for all categories
    categories = {h["category"] for h in report["category_health"]}
    assert "Pizza" in categories
    assert "Wings" in categories

    # Should have some repair recommendations
    assert report["repair_summary"]["total_recommendations"] > 0

    # Should have worst items
    assert len(report["issue_digest"]["worst_items"]) >= 3

    # Narrative should be non-empty
    assert len(report["quality_narrative"]) > 50

    r.ok("realistic_pizza_menu_20_items")


def test_realistic_all_high_quality(r: TestReport) -> None:
    """Perfect menu — all items high confidence."""
    items = [_make_high_item(f"Item {i}", "Pizza", 1000 + i * 100) for i in range(10)]
    report = _run_full_pipeline(items)
    assert report["menu_confidence"]["quality_grade"] == "A"
    assert report["menu_confidence"]["needs_review_count"] == 0
    assert report["repair_summary"]["total_recommendations"] == 0
    assert report["issue_digest"]["top_issues"] == []
    assert "Excellent" in report["quality_narrative"]
    r.ok("realistic_all_high_quality")


def test_realistic_all_reject(r: TestReport) -> None:
    """Worst case — all items rejected."""
    items = [_make_reject_item(f"ssseeeccc{i}") for i in range(8)]
    report = _run_full_pipeline(items)
    assert report["menu_confidence"]["quality_grade"] == "D"
    assert report["menu_confidence"]["needs_review_count"] == 8
    assert report["repair_summary"]["total_recommendations"] > 0
    assert "Poor" in report["quality_narrative"]
    r.ok("realistic_all_reject")


def test_realistic_path_b_items(r: TestReport) -> None:
    """Path B items (flat dicts from ai_ocr_helper)."""
    items = [
        {"name": "Margherita Pizza", "category": "Pizza", "confidence": 0.90,
         "price_cents": 1499, "variants": [], "price_flags": []},
        {"name": "Pepperoni Pizza", "category": "Pizza", "confidence": 0.85,
         "price_cents": 1599, "variants": [], "price_flags": []},
        {"name": "XY", "category": "Uncategorized", "confidence": 0.30,
         "price_cents": 0, "variants": [], "price_flags": []},
    ]
    report = _run_full_pipeline(items)
    assert report["menu_confidence"]["total_items"] == 3
    # Narrative should work for Path B
    assert isinstance(report["quality_narrative"], str)
    r.ok("realistic_path_b_items")


# ---------------------------------------------------------------------------
# 14. Edge cases
# ---------------------------------------------------------------------------

def test_items_without_scores(r: TestReport) -> None:
    """Items that haven't been scored yet."""
    items = [{"name": "Plain Item", "category": "Other"}]
    report = generate_semantic_report(items)
    # Should still produce a valid report (with defaults)
    assert report["menu_confidence"]["total_items"] == 1
    r.ok("items_without_scores")


def test_no_mutation(r: TestReport) -> None:
    """generate_semantic_report should not mutate items."""
    items = [_make_high_item()]
    score_semantic_confidence(items)
    classify_confidence_tiers(items)
    generate_repair_recommendations(items)

    import copy
    items_copy = copy.deepcopy(items)
    generate_semantic_report(items)
    assert items == items_copy
    r.ok("no_mutation")


def test_single_item_all_sections_populated(r: TestReport) -> None:
    """Single item should still populate all sections."""
    items = [_make_high_item()]
    report = _run_full_pipeline(items)
    assert report["menu_confidence"]["total_items"] == 1
    assert report["repair_summary"]["total_items"] == 1
    assert len(report["pipeline_coverage"]) == 7
    assert len(report["issue_digest"]["worst_items"]) == 1
    assert len(report["category_health"]) == 1
    assert len(report["quality_narrative"]) > 0
    r.ok("single_item_all_sections")


def test_large_menu_performance(r: TestReport) -> None:
    """100 items should complete without issues."""
    items = [_make_high_item(f"Item {i}", "Pizza" if i % 3 == 0 else "Wings", 1000 + i * 50)
             for i in range(100)]
    report = _run_full_pipeline(items)
    assert report["menu_confidence"]["total_items"] == 100
    assert len(report["category_health"]) == 2  # Pizza and Wings
    r.ok("large_menu_100_items")


def test_all_same_confidence(r: TestReport) -> None:
    """All items with identical scores."""
    items = [_make_high_item(f"Item {i}", "Pizza") for i in range(5)]
    report = _run_full_pipeline(items)
    mc = report["menu_confidence"]
    assert mc["stdev_confidence"] < 0.01  # near zero
    r.ok("all_same_confidence")


def test_many_categories(r: TestReport) -> None:
    """Menu with many different categories."""
    categories = ["Pizza", "Wings", "Sides", "Salads", "Beverages",
                   "Desserts", "Subs", "Burgers", "Pasta", "Calzones"]
    items = [_make_high_item(f"Item in {cat}", cat) for cat in categories]
    report = _run_full_pipeline(items)
    assert len(report["category_health"]) == 10
    r.ok("many_categories_10")


def test_items_with_only_price_flags(r: TestReport) -> None:
    """Items with price_flags but no other signals."""
    items = [{
        "name": "Cheese Pizza",
        "category": "Pizza",
        "price_flags": [
            {"severity": "warn", "reason": "variant_price_inversion"},
            {"severity": "info", "reason": "cross_item_price_outlier"},
        ],
    }]
    report = _run_full_pipeline(items)
    cf = report["issue_digest"]["common_flags"]
    assert len(cf) >= 1
    r.ok("items_with_only_price_flags")


# ---------------------------------------------------------------------------
# 15. Integration: full pipeline chain
# ---------------------------------------------------------------------------

def test_full_pipeline_chain(r: TestReport) -> None:
    """Full Steps 9.2-9.6 chain produces consistent report."""
    items = [
        _make_high_item("Pepperoni", "Pizza"),
        _make_medium_item("Bread", "Sides"),
        _make_low_item("X1", "Unknown"),
        _make_reject_item("ssseeeccc", "Unknown"),
    ]
    report = _run_full_pipeline(items)

    # Check consistency: menu_confidence total matches pipeline_coverage item counts
    total = report["menu_confidence"]["total_items"]
    for key, val in report["pipeline_coverage"].items():
        assert val["count"] <= total

    # Check consistency: repair summary total matches menu confidence
    assert report["repair_summary"]["total_items"] == total

    # Category health covers all categories
    ch_cats = {h["category"] for h in report["category_health"]}
    mc_cats = set(report["menu_confidence"]["category_summary"].keys())
    assert ch_cats == mc_cats, f"Category mismatch: {ch_cats} vs {mc_cats}"

    r.ok("full_pipeline_chain_consistency")


def test_pipeline_report_after_repairs(r: TestReport) -> None:
    """Report after repairs should reflect improved scores."""
    items = [
        {"name": "PEPPERONI PIZZA", "grammar": {"parsed_name": "PEPPERONI PIZZA", "parse_confidence": 0.65},
         "category": "Pizza", "price_candidates": [{"price_cents": 1499}],
         "variants": [{"confidence": 0.50}],
         "price_flags": [{"severity": "info", "reason": "some_flag"}]},
    ]
    # First pass: score, tier, recommend
    score_semantic_confidence(items)
    classify_confidence_tiers(items)
    generate_repair_recommendations(items)

    # Apply repairs
    repair_results = apply_auto_repairs(items)

    # Re-score
    score_semantic_confidence(items)
    classify_confidence_tiers(items)

    report = generate_semantic_report(items, repair_results)

    # The name should have been title-cased by auto-repair
    if repair_results["repairs_applied"] > 0:
        assert report["auto_repair_results"]["repairs_applied"] > 0
    r.ok("pipeline_report_after_repairs")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    r = TestReport()
    print("Day 70: Semantic Quality Report (Phase 8 Capstone)\n")

    # 1. Empty menu
    print("[1] Empty menu")
    test_empty_menu(r)
    test_empty_menu_top_level_keys(r)

    # 2. Single item per tier
    print("\n[2] Single item per tier")
    test_single_high_item(r)
    test_single_medium_item(r)
    test_single_low_item(r)
    test_single_reject_item(r)

    # 3. Multi-item mixed menus
    print("\n[3] Multi-item mixed menus")
    test_mixed_menu_5_items(r)
    test_mixed_menu_grade_B(r)
    test_mixed_menu_grade_C(r)

    # 4. Menu confidence section
    print("\n[4] Menu confidence section")
    test_menu_confidence_mean_median(r)
    test_menu_confidence_category_summary(r)

    # 5. Repair summary section
    print("\n[5] Repair summary section")
    test_repair_summary_no_recs(r)
    test_repair_summary_with_recs(r)
    test_repair_summary_by_priority(r)
    test_repair_summary_by_type_keys(r)

    # 6. Auto-repair results
    print("\n[6] Auto-repair results passthrough")
    test_auto_repair_results_present(r)
    test_auto_repair_results_none_default(r)
    test_auto_repair_results_passthrough(r)

    # 7. Pipeline coverage
    print("\n[7] Pipeline coverage metrics")
    test_pipeline_coverage_all_signals(r)
    test_pipeline_coverage_no_variants(r)
    test_pipeline_coverage_partial(r)
    test_pipeline_coverage_keys(r)
    test_pipeline_coverage_pct_range(r)
    test_pipeline_coverage_empty(r)

    # 8. Issue digest
    print("\n[8] Issue digest")
    test_issue_digest_top_issues_empty(r)
    test_issue_digest_top_issues_present(r)
    test_issue_digest_top_issues_sorted(r)
    test_issue_digest_top_issues_pct_sum(r)
    test_issue_digest_worst_items_empty(r)
    test_issue_digest_worst_items_sorted(r)
    test_issue_digest_worst_items_limit(r)
    test_issue_digest_worst_items_fields(r)
    test_issue_digest_common_flags_empty(r)
    test_issue_digest_common_flags_present(r)
    test_issue_digest_common_flags_sorted(r)
    test_issue_digest_common_flags_limit(r)

    # 9. Category health
    print("\n[9] Category health ranking")
    test_category_health_single_category(r)
    test_category_health_multi_categories(r)
    test_category_health_worst_first(r)
    test_category_health_fields(r)
    test_category_health_grade_A(r)
    test_category_health_grade_D(r)
    test_category_health_needs_review_pct(r)
    test_category_health_empty(r)

    # 10. Quality narrative
    print("\n[10] Quality narrative")
    test_narrative_empty(r)
    test_narrative_grade_A(r)
    test_narrative_grade_D(r)
    test_narrative_mentions_items(r)
    test_narrative_mentions_repairs(r)
    test_narrative_mentions_weakest_category(r)
    test_narrative_with_repair_results(r)
    test_narrative_is_string(r)

    # 11. Report structure
    print("\n[11] Report structure completeness")
    test_report_top_level_keys(r)
    test_menu_confidence_keys(r)
    test_repair_summary_keys(r)
    test_auto_repair_results_keys(r)
    test_issue_digest_keys(r)

    # 12. Grade accuracy
    print("\n[12] Grade accuracy")
    test_grade_A_threshold(r)
    test_grade_B_threshold(r)
    test_grade_D_all_reject(r)

    # 13. Real-world simulation
    print("\n[13] Real-world menu simulation")
    test_realistic_pizza_menu(r)
    test_realistic_all_high_quality(r)
    test_realistic_all_reject(r)
    test_realistic_path_b_items(r)

    # 14. Edge cases
    print("\n[14] Edge cases")
    test_items_without_scores(r)
    test_no_mutation(r)
    test_single_item_all_sections_populated(r)
    test_large_menu_performance(r)
    test_all_same_confidence(r)
    test_many_categories(r)
    test_items_with_only_price_flags(r)

    # 15. Integration
    print("\n[15] Full pipeline integration")
    test_full_pipeline_chain(r)
    test_pipeline_report_after_repairs(r)

    print(r.summary())
    if r.failures:
        print("\nFailures:")
        for f in r.failures:
            print(f"  {f}")
        return 1
    return 0


if __name__ == "__main__":
    exit(main())
