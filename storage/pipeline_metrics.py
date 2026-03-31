# storage/pipeline_metrics.py
"""
Pipeline Metrics & Observability — Day 99, Sprint 11.1.

Lightweight tracker that records per-step timing, item counts, and metadata
as a menu flows through the production AI pipeline (OCR → Call 1 → Call 2 →
Semantic).  The summary dict is saved into the OCR debug payload so every
import has a full performance profile.

Usage:
    from storage.pipeline_metrics import PipelineTracker, STEP_OCR_TEXT

    tracker = PipelineTracker()
    tracker.start_step(STEP_OCR_TEXT)
    # ... do OCR ...
    tracker.end_step(STEP_OCR_TEXT, chars=7200)
    tracker.strategy = "claude_api+vision"
    summary = tracker.summary()
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Step name constants
# ---------------------------------------------------------------------------
STEP_OCR_TEXT = "ocr_text_extraction"
STEP_CALL1_EXTRACT = "call_1_claude_extraction"
STEP_CALL2_VISION = "call_2_vision_verification"
STEP_SEMANTIC = "semantic_pipeline"
STEP_CALL3_RECONCILE = "call_3_targeted_reconciliation"
STEP_CALL4_PRICE = "call_4_price_intelligence"

# Canonical ordering for item_flow output
_STEP_ORDER = [STEP_OCR_TEXT, STEP_CALL1_EXTRACT, STEP_CALL2_VISION, STEP_SEMANTIC, STEP_CALL3_RECONCILE, STEP_CALL4_PRICE]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def format_duration(ms: float) -> str:
    """Human-readable duration string.

    >>> format_duration(450)
    '450ms'
    >>> format_duration(1200)
    '1.2s'
    >>> format_duration(65000)
    '1m 5.0s'
    """
    if ms < 1000:
        return f"{int(round(ms))}ms"
    secs = ms / 1000.0
    if secs < 60:
        return f"{secs:.1f}s"
    mins = int(secs // 60)
    remainder = secs - mins * 60
    return f"{mins}m {remainder:.1f}s"


# ---------------------------------------------------------------------------
# PipelineTracker
# ---------------------------------------------------------------------------
class PipelineTracker:
    """Accumulates per-step timing and metadata for the production pipeline."""

    def __init__(self) -> None:
        self._steps: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._start_time: float = time.monotonic()
        self._pending: Dict[str, float] = {}  # step_name → start monotonic
        self.strategy: str = "none"

    # -- Step lifecycle -----------------------------------------------------

    def start_step(self, name: str) -> None:
        """Mark the beginning of a pipeline step."""
        self._pending[name] = time.monotonic()

    def end_step(self, name: str, *, items: int = 0, **extra: Any) -> None:
        """Mark a step as successfully completed.

        Args:
            name:  Step name (use the STEP_* constants).
            items: Number of items produced by this step (0 for text-only steps).
            **extra: Arbitrary metadata (e.g. confidence=0.95, changes=3).
        """
        start = self._pending.pop(name, None)
        elapsed_ms = round((time.monotonic() - start) * 1000) if start is not None else 0
        self._steps[name] = {
            "status": "success",
            "duration_ms": elapsed_ms,
            "items": items,
            **extra,
        }

    def skip_step(self, name: str, reason: str) -> None:
        """Record that a step was skipped (not an error)."""
        # Clear any pending start for this step
        start = self._pending.pop(name, None)
        elapsed_ms = round((time.monotonic() - start) * 1000) if start is not None else 0
        self._steps[name] = {
            "status": "skipped",
            "duration_ms": elapsed_ms,
            "items": 0,
            "skip_reason": reason,
        }

    def fail_step(self, name: str, error: str) -> None:
        """Record that a step failed."""
        start = self._pending.pop(name, None)
        elapsed_ms = round((time.monotonic() - start) * 1000) if start is not None else 0
        self._steps[name] = {
            "status": "failed",
            "duration_ms": elapsed_ms,
            "items": 0,
            "error": error,
        }

    # -- Summary ------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Build the full metrics summary dict for the debug payload."""
        total_ms = round((time.monotonic() - self._start_time) * 1000)

        # Build item_flow in canonical order
        item_flow: List[Dict[str, Any]] = []
        for step_name in _STEP_ORDER:
            info = self._steps.get(step_name)
            if info is None:
                continue
            entry: Dict[str, Any] = {
                "step": step_name,
                "items": info.get("items", 0),
            }
            if info["status"] == "skipped":
                entry["note"] = f"skipped: {info.get('skip_reason', '')}"
            elif info["status"] == "failed":
                entry["note"] = f"failed: {info.get('error', '')}"
            elif step_name == STEP_OCR_TEXT:
                chars = info.get("chars", 0)
                if chars:
                    entry["note"] = f"{chars} chars"
            item_flow.append(entry)

        # Also include any non-canonical steps (future-proofing)
        for step_name, info in self._steps.items():
            if step_name not in _STEP_ORDER:
                item_flow.append({
                    "step": step_name,
                    "items": info.get("items", 0),
                })

        # Bottleneck = slowest successful step
        bottleneck = None
        max_dur = -1
        for step_name, info in self._steps.items():
            if info["status"] == "success" and info["duration_ms"] > max_dur:
                max_dur = info["duration_ms"]
                bottleneck = step_name

        # Steps dict (all recorded steps with full metadata)
        steps_dict: Dict[str, Dict[str, Any]] = {}
        for step_name, info in self._steps.items():
            steps_dict[step_name] = dict(info)

        return {
            "total_duration_ms": total_ms,
            "total_duration_human": format_duration(total_ms),
            "steps": steps_dict,
            "item_flow": item_flow,
            "bottleneck": bottleneck,
            "extraction_strategy": self.strategy,
        }
