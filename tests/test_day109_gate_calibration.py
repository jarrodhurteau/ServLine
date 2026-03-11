# tests/test_day109_gate_calibration.py
"""
Day 109 — Gate Calibration Utility Tests.

Validates threshold sweep, signal contribution analysis, and calibration
report generation in storage/gate_calibration.py.
"""

import pytest

from storage.gate_calibration import (
    CalibrationReport,
    PipelineResult,
    SignalStats,
    ThresholdPoint,
    analyze_signal_contribution,
    make_result,
    run_calibration_report,
    sweep_thresholds,
)
from storage.confidence_gate import GATE_THRESHOLD, GateResult


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _item(semantic: float) -> dict:
    return {"name": "Test Item", "price_cents": 999, "semantic_confidence": semantic}


def _good_items(n: int = 12, conf: float = 0.92) -> list:
    return [_item(conf) for _ in range(n)]


def _poor_items(n: int = 12, conf: float = 0.45) -> list:
    return [_item(conf) for _ in range(n)]


def _marginal_items(n: int = 12, conf: float = 0.72) -> list:
    return [_item(conf) for _ in range(n)]


# ---------------------------------------------------------------------------
# make_result
# ---------------------------------------------------------------------------

class TestMakeResult:
    def test_returns_pipeline_result(self):
        r = make_result(_good_items(), call2_confidence=0.94)
        assert isinstance(r, PipelineResult)

    def test_gate_result_populated(self):
        r = make_result(_good_items(), call2_confidence=0.94)
        assert isinstance(r.gate_result, GateResult)
        assert r.gate_result.score > 0

    def test_label_stored(self):
        r = make_result(_good_items(), label="excellent")
        assert r.label == "excellent"

    def test_ocr_char_count_stored(self):
        r = make_result(_good_items(), ocr_char_count=7800)
        assert r.ocr_char_count == 7800

    def test_no_optional_calls(self):
        r = make_result(_good_items())
        # Should still produce a valid score without call2/call3
        assert 0.0 <= r.gate_result.score <= 1.0

    def test_passing_result_on_excellent_input(self):
        r = make_result(_good_items(12, 0.95), call2_confidence=0.97, call3_confidence=0.96)
        assert r.gate_result.passed is True

    def test_failing_result_on_poor_input(self):
        r = make_result(_poor_items(), call2_confidence=0.40, call3_confidence=0.35)
        assert r.gate_result.passed is False


# ---------------------------------------------------------------------------
# sweep_thresholds
# ---------------------------------------------------------------------------

class TestSweepThresholds:
    def _batch(self):
        return [
            make_result(_good_items(12, 0.95), call2_confidence=0.97, call3_confidence=0.96),
            make_result(_good_items(12, 0.92), call2_confidence=0.93),
            make_result(_marginal_items(), call2_confidence=0.72),
            make_result(_poor_items(), call2_confidence=0.40),
        ]

    def test_returns_list_of_threshold_points(self):
        pts = sweep_thresholds(self._batch())
        assert isinstance(pts, list)
        assert all(isinstance(p, ThresholdPoint) for p in pts)

    def test_sorted_ascending(self):
        pts = sweep_thresholds(self._batch())
        thresholds = [p.threshold for p in pts]
        assert thresholds == sorted(thresholds)

    def test_pass_rate_decreases_with_threshold(self):
        pts = sweep_thresholds(self._batch())
        rates = [p.pass_rate for p in pts]
        # monotonically non-increasing
        for i in range(len(rates) - 1):
            assert rates[i] >= rates[i + 1]

    def test_n_total_constant(self):
        batch = self._batch()
        pts = sweep_thresholds(batch)
        for p in pts:
            assert p.n_total == len(batch)

    def test_n_pass_plus_fail_equals_total(self):
        for p in sweep_thresholds(self._batch()):
            assert p.n_pass + p.n_fail == p.n_total

    def test_pass_rate_plus_fail_rate_equals_one(self):
        for p in sweep_thresholds(self._batch()):
            assert abs(p.pass_rate + p.fail_rate - 1.0) < 1e-6

    def test_empty_results_returns_empty_list(self):
        assert sweep_thresholds([]) == []

    def test_low_threshold_all_pass(self):
        # At threshold 0.50 all but truly broken menus pass
        batch = [make_result(_good_items(12, 0.90), call2_confidence=0.90)]
        pts = sweep_thresholds(batch, lo=0.50, hi=0.50, step=0.50)
        assert pts[0].pass_rate == 1.0

    def test_high_threshold_all_fail(self):
        batch = [make_result(_poor_items(), call2_confidence=0.40)]
        pts = sweep_thresholds(batch, lo=0.99, hi=0.99, step=0.99)
        assert pts[0].n_fail == 1

    def test_custom_step(self):
        pts = sweep_thresholds(self._batch(), lo=0.80, hi=0.95, step=0.05)
        expected = [0.80, 0.85, 0.90, 0.95]
        assert [p.threshold for p in pts] == expected


# ---------------------------------------------------------------------------
# analyze_signal_contribution
# ---------------------------------------------------------------------------

class TestAnalyzeSignalContribution:
    def _batch_all_signals(self):
        return [
            make_result(_good_items(12, 0.92), call2_confidence=0.94, call3_confidence=0.95),
            make_result(_good_items(12, 0.88), call2_confidence=0.90, call3_confidence=0.91),
            make_result(_marginal_items(), call2_confidence=0.70, call3_confidence=0.72),
        ]

    def test_returns_dict_with_expected_keys(self):
        stats = analyze_signal_contribution(self._batch_all_signals())
        assert "semantic" in stats
        assert "call2_vision" in stats
        assert "call3_reconcile" in stats
        assert "item_count" in stats

    def test_signal_stats_are_correct_type(self):
        stats = analyze_signal_contribution(self._batch_all_signals())
        for v in stats.values():
            assert isinstance(v, SignalStats)

    def test_mean_within_min_max(self):
        stats = analyze_signal_contribution(self._batch_all_signals())
        for sig in stats.values():
            if sig.count > 0:
                assert sig.minimum <= sig.mean <= sig.maximum

    def test_skipped_calls_have_zero_count(self):
        # Batch with no call2 or call3
        batch = [make_result(_good_items()) for _ in range(3)]
        stats = analyze_signal_contribution(batch)
        assert stats["call2_vision"].count == 0
        assert stats["call3_reconcile"].count == 0

    def test_semantic_always_present(self):
        batch = [make_result(_good_items()) for _ in range(3)]
        stats = analyze_signal_contribution(batch)
        assert stats["semantic"].count == 3

    def test_empty_results_returns_empty_dict(self):
        assert analyze_signal_contribution([]) == {}

    def test_count_matches_batch_size_when_all_provided(self):
        batch = self._batch_all_signals()
        stats = analyze_signal_contribution(batch)
        assert stats["call2_vision"].count == len(batch)
        assert stats["call3_reconcile"].count == len(batch)


# ---------------------------------------------------------------------------
# run_calibration_report
# ---------------------------------------------------------------------------

class TestRunCalibrationReport:
    def _mixed_batch(self):
        return [
            make_result(_good_items(12, 0.95), call2_confidence=0.97, call3_confidence=0.96,
                        label="excellent"),
            make_result(_good_items(12, 0.91), call2_confidence=0.92, label="good"),
            make_result(_marginal_items(), call2_confidence=0.72, label="marginal"),
            make_result(_poor_items(), call2_confidence=0.40, label="poor"),
        ]

    def test_returns_calibration_report(self):
        r = run_calibration_report(self._mixed_batch())
        assert isinstance(r, CalibrationReport)

    def test_n_total_correct(self):
        batch = self._mixed_batch()
        r = run_calibration_report(batch)
        assert r.n_total == len(batch)

    def test_pass_plus_fail_equals_total(self):
        r = run_calibration_report(self._mixed_batch())
        assert r.n_pass + r.n_fail == r.n_total

    def test_pass_rate_in_range(self):
        r = run_calibration_report(self._mixed_batch())
        assert 0.0 <= r.pass_rate <= 1.0

    def test_score_stats_valid(self):
        r = run_calibration_report(self._mixed_batch())
        assert r.min_score <= r.mean_score <= r.max_score

    def test_sweep_included(self):
        r = run_calibration_report(self._mixed_batch())
        assert isinstance(r.sweep, list)
        assert len(r.sweep) > 0

    def test_signal_stats_included(self):
        r = run_calibration_report(self._mixed_batch())
        assert "semantic" in r.signal_stats

    def test_passing_failing_scores_partition_all(self):
        r = run_calibration_report(self._mixed_batch())
        total = len(r.passing_scores) + len(r.failing_scores)
        assert total == r.n_total

    def test_recommendation_non_empty(self):
        r = run_calibration_report(self._mixed_batch())
        assert isinstance(r.recommendation, str)
        assert len(r.recommendation) > 0

    def test_empty_batch_returns_safe_report(self):
        r = run_calibration_report([])
        assert r.n_total == 0
        assert "No results" in r.recommendation

    def test_all_excellent_batch_high_pass_rate(self):
        batch = [
            make_result(_good_items(12, 0.95), call2_confidence=0.97, call3_confidence=0.96)
            for _ in range(5)
        ]
        r = run_calibration_report(batch)
        assert r.pass_rate == 1.0

    def test_all_poor_batch_low_pass_rate(self):
        batch = [
            make_result(_poor_items(), call2_confidence=0.40)
            for _ in range(5)
        ]
        r = run_calibration_report(batch)
        assert r.pass_rate == 0.0

    def test_custom_threshold_changes_pass_count(self):
        batch = self._mixed_batch()
        r_strict = run_calibration_report(batch, threshold=0.99)
        r_lenient = run_calibration_report(batch, threshold=0.50)
        assert r_lenient.n_pass >= r_strict.n_pass

    def test_n_marginal_within_total(self):
        r = run_calibration_report(self._mixed_batch())
        assert 0 <= r.n_marginal <= r.n_total

    def test_current_threshold_stored(self):
        r = run_calibration_report(self._mixed_batch(), threshold=0.85)
        assert r.current_threshold == 0.85
