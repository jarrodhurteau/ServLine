# storage/confidence_gate.py
"""
Confidence Gate — Day 105, Sprint 11.3.

Binary pass/fail at the menu level.  Aggregates signals from the entire
production pipeline (semantic scoring + Claude call confidences + item count)
into a single gate score, then compares against GATE_THRESHOLD.

A failed gate means the parse is not good enough to deliver to the customer.
The customer sees only a friendly retry message — never a numeric score.

Usage:
    from storage.confidence_gate import evaluate_confidence_gate, GateResult

    result = evaluate_confidence_gate(
        items,
        call2_confidence=0.92,
        call3_confidence=0.95,
        ocr_char_count=7800,
    )
    if not result.passed:
        return {"error": result.customer_message}

Signals (weights are redistributed when optional signals are unavailable):
    1. Semantic pipeline quality (base weight 0.50) — mean semantic_confidence
       across all items after Phase 8 scoring.
    2. Call 2 vision confidence  (base weight 0.25) — Claude's self-reported
       confidence from verify_menu_with_vision().  Optional.
    3. Call 3 reconcile confidence (base weight 0.15) — Claude's confidence
       from reconcile_flagged_items().  Optional.
    4. Item count sanity (base weight 0.10) — penalizes suspiciously short
       item lists that suggest the menu was not fully read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Gate threshold: menu must score >= this to pass (0.0-1.0 scale)
GATE_THRESHOLD: float = 0.90

# Base signal weights when all signals are available (must sum to 1.0)
_W_SEMANTIC: float = 0.50
_W_CALL2:    float = 0.25
_W_CALL3:    float = 0.15
_W_ITEMS:    float = 0.10

# Item count thresholds for the item-count sanity signal
_ITEM_COUNT_GOOD: int = 10   # ≥ 10 items → 1.0
_ITEM_COUNT_OK:   int = 5    # 5-9 items  → 0.8
_ITEM_COUNT_LOW:  int = 3    # 3-4 items  → 0.6
#                              1-2 items  → 0.3
#                              0 items    → 0.0

# Customer-facing message shown when the gate fails (never shows scores)
_CUSTOMER_FAIL_MSG: str = (
    "We had trouble reading all the items in your menu. For best results, "
    "photograph each page in good lighting with the full menu clearly visible, "
    "then try again."
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    """Outcome of a confidence gate evaluation."""

    passed: bool
    """True if the pipeline result clears the threshold and is safe to deliver."""

    score: float
    """Aggregate gate score (0.0-1.0).  Never shown to customers."""

    threshold: float
    """The threshold used for this evaluation."""

    signals: Dict[str, Any] = field(default_factory=dict)
    """Per-signal breakdown: {name: {raw, weight, weighted}}.  For logging."""

    reason: str = ""
    """Human-readable technical reason for the gate decision.  For logs only."""

    customer_message: str = ""
    """Shown to the customer only if the gate failed.  Empty string if passed."""


# ---------------------------------------------------------------------------
# Internal signal scorers
# ---------------------------------------------------------------------------

def _score_semantic_signal(items: List[Dict[str, Any]]) -> float:
    """Mean semantic_confidence across all items (0.0 if empty)."""
    if not items:
        return 0.0
    total = sum(float(it.get("semantic_confidence", 0.0)) for it in items)
    return total / len(items)


def _score_item_count(n: int) -> float:
    """Score based on item count plausibility."""
    if n >= _ITEM_COUNT_GOOD:
        return 1.0
    if n >= _ITEM_COUNT_OK:
        return 0.8
    if n >= _ITEM_COUNT_LOW:
        return 0.6
    if n >= 1:
        return 0.3
    return 0.0  # no items extracted at all


def _compute_weights(call2_available: bool, call3_available: bool) -> Dict[str, float]:
    """Redistribute unavailable call weights to the semantic signal."""
    w_semantic = _W_SEMANTIC
    w_call2 = _W_CALL2 if call2_available else 0.0
    w_call3 = _W_CALL3 if call3_available else 0.0
    # Missing call weight flows back to semantic
    w_semantic += (_W_CALL2 - w_call2) + (_W_CALL3 - w_call3)
    return {
        "semantic": w_semantic,
        "call2":    w_call2,
        "call3":    w_call3,
        "items":    _W_ITEMS,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_confidence_gate(
    items: List[Dict[str, Any]],
    *,
    call2_confidence: Optional[float] = None,
    call3_confidence: Optional[float] = None,
    ocr_char_count: int = 0,
    threshold: float = GATE_THRESHOLD,
) -> GateResult:
    """Evaluate whether a pipeline result clears the confidence gate.

    Args:
        items:             Items after semantic scoring (semantic_confidence set).
        call2_confidence:  Claude's confidence from vision verify (0.0-1.0).
                           Pass None if Call 2 was skipped.
        call3_confidence:  Claude's confidence from reconciliation (0.0-1.0).
                           Pass None if Call 3 was skipped.
        ocr_char_count:    Raw OCR character count (unused in score, kept for
                           logging / future use).
        threshold:         Override the default GATE_THRESHOLD for testing.

    Returns:
        GateResult with ``passed``, ``score``, ``signals``, ``reason``, and
        ``customer_message`` populated.
    """
    n_items = len(items)

    # Clamp provided confidences to [0, 1]
    c2 = max(0.0, min(1.0, float(call2_confidence))) if call2_confidence is not None else None
    c3 = max(0.0, min(1.0, float(call3_confidence))) if call3_confidence is not None else None

    weights = _compute_weights(c2 is not None, c3 is not None)

    # Compute raw signal values
    sem_raw    = _score_semantic_signal(items)
    items_raw  = _score_item_count(n_items)
    call2_raw  = c2 if c2 is not None else 0.0
    call3_raw  = c3 if c3 is not None else 0.0

    # Weighted contributions
    sem_weighted   = sem_raw   * weights["semantic"]
    call2_weighted = call2_raw * weights["call2"]
    call3_weighted = call3_raw * weights["call3"]
    items_weighted = items_raw * weights["items"]

    score = round(
        sem_weighted + call2_weighted + call3_weighted + items_weighted,
        4,
    )
    score = max(0.0, min(1.0, score))

    passed = score >= threshold

    # Build signals breakdown for logging
    signals: Dict[str, Any] = {
        "semantic": {
            "raw": round(sem_raw, 4),
            "weight": round(weights["semantic"], 4),
            "weighted": round(sem_weighted, 4),
            "item_count": n_items,
        },
        "item_count": {
            "raw": round(items_raw, 4),
            "weight": round(weights["items"], 4),
            "weighted": round(items_weighted, 4),
            "n_items": n_items,
        },
        "ocr_char_count": ocr_char_count,
    }
    if c2 is not None:
        signals["call2_vision"] = {
            "raw": round(call2_raw, 4),
            "weight": round(weights["call2"], 4),
            "weighted": round(call2_weighted, 4),
        }
    else:
        signals["call2_vision"] = {"skipped": True, "weight_redistributed": round(_W_CALL2, 4)}

    if c3 is not None:
        signals["call3_reconcile"] = {
            "raw": round(call3_raw, 4),
            "weight": round(weights["call3"], 4),
            "weighted": round(call3_weighted, 4),
        }
    else:
        signals["call3_reconcile"] = {"skipped": True, "weight_redistributed": round(_W_CALL3, 4)}

    # Build reason string (for logs/rejection log)
    if passed:
        reason = f"PASS score={score:.4f} threshold={threshold:.2f}"
    else:
        parts = []
        if sem_raw < 0.70:
            parts.append(f"low_semantic={sem_raw:.2f}")
        if c2 is not None and c2 < 0.70:
            parts.append(f"low_call2={c2:.2f}")
        if c3 is not None and c3 < 0.70:
            parts.append(f"low_call3={c3:.2f}")
        if items_raw < 0.6:
            parts.append(f"low_item_count={n_items}")
        reason = (
            f"FAIL score={score:.4f} threshold={threshold:.2f}"
            + (f" [{', '.join(parts)}]" if parts else "")
        )

    return GateResult(
        passed=passed,
        score=score,
        threshold=threshold,
        signals=signals,
        reason=reason,
        customer_message="" if passed else _CUSTOMER_FAIL_MSG,
    )
