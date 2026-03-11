# storage/gate_calibration.py
"""
Gate Calibration Utility — Day 109, Sprint 11.3.

Analyzes threshold sensitivity, signal contribution, and pass/fail statistics
for the confidence gate.  Helps validate that GATE_THRESHOLD=0.90 is
appropriately calibrated for the production pipeline.

Typical usage
-------------
    from storage.gate_calibration import (
        make_result,
        sweep_thresholds,
        analyze_signal_contribution,
        run_calibration_report,
    )

    # Build a list of synthetic or real-pipeline results
    results = [
        make_result(items, call2_confidence=0.93, call3_confidence=0.95,
                    label="excellent"),
        make_result(items_marginal, call2_confidence=0.72, label="marginal"),
        ...
    ]

    # Sweep thresholds to see how many menus pass at each level
    sweep = sweep_thresholds(results)          # returns list[ThresholdPoint]

    # Detailed signal contribution stats
    contrib = analyze_signal_contribution(results)

    # Full report with recommendations
    report = run_calibration_report(results)
    print(report["recommendation"])
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from storage.confidence_gate import (
    GATE_THRESHOLD,
    GateResult,
    evaluate_confidence_gate,
)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """A single pipeline run, ready for calibration analysis."""

    gate_result: GateResult
    """Full gate evaluation for this run."""

    label: str = ""
    """Optional human label, e.g. 'excellent', 'marginal', 'bad_photo'."""

    ocr_char_count: int = 0
    """Raw OCR character count (informational)."""


def make_result(
    items: List[Dict[str, Any]],
    *,
    call2_confidence: Optional[float] = None,
    call3_confidence: Optional[float] = None,
    ocr_char_count: int = 0,
    label: str = "",
    threshold: float = GATE_THRESHOLD,
) -> PipelineResult:
    """Build a PipelineResult by running the gate on the given inputs."""
    gate = evaluate_confidence_gate(
        items,
        call2_confidence=call2_confidence,
        call3_confidence=call3_confidence,
        ocr_char_count=ocr_char_count,
        threshold=threshold,
    )
    return PipelineResult(gate_result=gate, label=label, ocr_char_count=ocr_char_count)


# ---------------------------------------------------------------------------
# Threshold sweep
# ---------------------------------------------------------------------------

@dataclass
class ThresholdPoint:
    """Pass/fail statistics at a single threshold level."""

    threshold: float
    n_total: int
    n_pass: int
    n_fail: int
    pass_rate: float   # 0.0–1.0
    fail_rate: float   # 0.0–1.0


def sweep_thresholds(
    results: List[PipelineResult],
    *,
    lo: float = 0.50,
    hi: float = 0.99,
    step: float = 0.05,
) -> List[ThresholdPoint]:
    """Evaluate every result at each threshold from *lo* to *hi*.

    Returns a list of ThresholdPoint sorted ascending by threshold.
    Pass rates are monotonically non-increasing as threshold rises.
    """
    if not results:
        return []

    # Collect the gate score for each result (scores don't change with threshold)
    scores = [r.gate_result.score for r in results]
    n = len(scores)

    points: List[ThresholdPoint] = []
    t = lo
    while t <= hi + 1e-9:   # 1e-9 avoids float rounding truncation
        t_rounded = round(t, 4)
        n_pass = sum(1 for s in scores if s >= t_rounded)
        n_fail = n - n_pass
        points.append(
            ThresholdPoint(
                threshold=t_rounded,
                n_total=n,
                n_pass=n_pass,
                n_fail=n_fail,
                pass_rate=round(n_pass / n, 4),
                fail_rate=round(n_fail / n, 4),
            )
        )
        t = round(t + step, 10)  # round to avoid float drift

    return points


# ---------------------------------------------------------------------------
# Signal contribution analysis
# ---------------------------------------------------------------------------

@dataclass
class SignalStats:
    """Aggregate statistics for one signal across all results."""

    signal_name: str
    count: int           # how many results have this signal available (not skipped)
    mean: float
    minimum: float
    maximum: float
    mean_weight: float   # effective weight (redistributed) across all results


def analyze_signal_contribution(
    results: List[PipelineResult],
) -> Dict[str, SignalStats]:
    """Compute per-signal statistics across all pipeline results.

    Returns a dict keyed by signal name: 'semantic', 'call2_vision',
    'call3_reconcile', 'item_count'.
    """
    if not results:
        return {}

    accum: Dict[str, Dict[str, Any]] = {
        "semantic":        {"raws": [], "weights": []},
        "call2_vision":    {"raws": [], "weights": []},
        "call3_reconcile": {"raws": [], "weights": []},
        "item_count":      {"raws": [], "weights": []},
    }

    for r in results:
        sigs = r.gate_result.signals

        # semantic
        sem = sigs.get("semantic", {})
        if sem and not sem.get("skipped"):
            accum["semantic"]["raws"].append(sem["raw"])
            accum["semantic"]["weights"].append(sem["weight"])

        # call2_vision
        c2 = sigs.get("call2_vision", {})
        if c2 and not c2.get("skipped"):
            accum["call2_vision"]["raws"].append(c2["raw"])
            accum["call2_vision"]["weights"].append(c2["weight"])

        # call3_reconcile
        c3 = sigs.get("call3_reconcile", {})
        if c3 and not c3.get("skipped"):
            accum["call3_reconcile"]["raws"].append(c3["raw"])
            accum["call3_reconcile"]["weights"].append(c3["weight"])

        # item_count
        ic = sigs.get("item_count", {})
        if ic and not ic.get("skipped"):
            accum["item_count"]["raws"].append(ic["raw"])
            accum["item_count"]["weights"].append(ic["weight"])

    stats: Dict[str, SignalStats] = {}
    for name, data in accum.items():
        raws = data["raws"]
        weights = data["weights"]
        if not raws:
            stats[name] = SignalStats(
                signal_name=name,
                count=0,
                mean=0.0,
                minimum=0.0,
                maximum=0.0,
                mean_weight=0.0,
            )
        else:
            stats[name] = SignalStats(
                signal_name=name,
                count=len(raws),
                mean=round(sum(raws) / len(raws), 4),
                minimum=round(min(raws), 4),
                maximum=round(max(raws), 4),
                mean_weight=round(sum(weights) / len(weights), 4),
            )

    return stats


# ---------------------------------------------------------------------------
# Calibration report
# ---------------------------------------------------------------------------

@dataclass
class CalibrationReport:
    """Full calibration report for a batch of pipeline results."""

    n_total: int
    n_pass: int
    n_fail: int
    pass_rate: float

    current_threshold: float
    mean_score: float
    min_score: float
    max_score: float

    signal_stats: Dict[str, SignalStats]
    sweep: List[ThresholdPoint]

    # Scores split by pass/fail for margin analysis
    passing_scores: List[float]
    failing_scores: List[float]

    # How many results are "near threshold" (within ±0.05)
    n_marginal: int

    recommendation: str = ""


def run_calibration_report(
    results: List[PipelineResult],
    *,
    threshold: float = GATE_THRESHOLD,
    sweep_lo: float = 0.50,
    sweep_hi: float = 0.99,
    sweep_step: float = 0.05,
) -> CalibrationReport:
    """Generate a full calibration report for a batch of pipeline results.

    The *recommendation* field contains a plain-English verdict on whether
    the current threshold appears well-calibrated.
    """
    if not results:
        return CalibrationReport(
            n_total=0, n_pass=0, n_fail=0, pass_rate=0.0,
            current_threshold=threshold,
            mean_score=0.0, min_score=0.0, max_score=0.0,
            signal_stats={}, sweep=[],
            passing_scores=[], failing_scores=[],
            n_marginal=0,
            recommendation="No results provided — cannot calibrate.",
        )

    scores = [r.gate_result.score for r in results]
    passing = [s for s in scores if s >= threshold]
    failing = [s for s in scores if s < threshold]

    n_total = len(scores)
    n_pass = len(passing)
    n_fail = len(failing)
    pass_rate = round(n_pass / n_total, 4)

    mean_score = round(sum(scores) / n_total, 4)
    min_score  = round(min(scores), 4)
    max_score  = round(max(scores), 4)

    marginal_lo = threshold - 0.05
    marginal_hi = threshold + 0.05
    n_marginal = sum(1 for s in scores if marginal_lo <= s <= marginal_hi)

    sweep = sweep_thresholds(
        results, lo=sweep_lo, hi=sweep_hi, step=sweep_step
    )
    signal_stats = analyze_signal_contribution(results)

    # Build recommendation
    rec = _build_recommendation(
        threshold=threshold,
        pass_rate=pass_rate,
        n_marginal=n_marginal,
        n_total=n_total,
        mean_score=mean_score,
        failing_scores=failing,
    )

    return CalibrationReport(
        n_total=n_total,
        n_pass=n_pass,
        n_fail=n_fail,
        pass_rate=pass_rate,
        current_threshold=threshold,
        mean_score=mean_score,
        min_score=min_score,
        max_score=max_score,
        signal_stats=signal_stats,
        sweep=sweep,
        passing_scores=sorted(passing),
        failing_scores=sorted(failing),
        n_marginal=n_marginal,
        recommendation=rec,
    )


def _build_recommendation(
    *,
    threshold: float,
    pass_rate: float,
    n_marginal: int,
    n_total: int,
    mean_score: float,
    failing_scores: List[float],
) -> str:
    """Return a plain-English recommendation string."""
    parts: List[str] = []

    if pass_rate >= 0.95:
        parts.append(
            f"Pass rate {pass_rate:.0%} is very high. Consider raising the "
            f"threshold if you want stricter quality control."
        )
    elif pass_rate >= 0.75:
        parts.append(
            f"Pass rate {pass_rate:.0%} is healthy. Threshold {threshold:.2f} "
            f"appears well-calibrated for this batch."
        )
    elif pass_rate >= 0.50:
        parts.append(
            f"Pass rate {pass_rate:.0%} is moderate. Review failing inputs or "
            f"consider lowering the threshold slightly."
        )
    else:
        parts.append(
            f"Pass rate {pass_rate:.0%} is low. The threshold {threshold:.2f} "
            f"may be too strict, or input quality is genuinely poor."
        )

    marginal_frac = n_marginal / n_total if n_total else 0.0
    if marginal_frac > 0.20:
        parts.append(
            f"{n_marginal}/{n_total} results ({marginal_frac:.0%}) fall within "
            f"±0.05 of the threshold — high sensitivity to small score changes."
        )

    if failing_scores:
        worst = max(failing_scores)
        if worst > threshold - 0.03:
            parts.append(
                f"Closest failing score is {worst:.4f} (just {threshold - worst:.4f} "
                f"below threshold) — near-miss rejections present."
            )

    return "  ".join(parts) if parts else f"Threshold {threshold:.2f} OK."
