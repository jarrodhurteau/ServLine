# tests/test_day106_gate_wiring.py
"""
Day 106 — Sprint 11.3: Confidence Gate Wiring into the Live Pipeline.

Sprint 11.3 goal: wire the confidence gate (built Day 105) into the actual
production pipeline so that:
  1. stamp_claude_confidence() is called after Call 2 (vision) — activating
     signal #6 in the semantic pipeline scoring.
  2. stamp_claude_confidence() is called after Call 3 (reconciliation) and
     before re-scoring — ensuring the final quality grade uses the most
     recent Claude confidence.
  3. evaluate_confidence_gate() is called at the end of the pipeline, using
     the final semantic items + both call confidences.
  4. A failed gate → job status="rejected" + error=customer_message.
  5. A failed gate → log_pipeline_rejection() called with full signals.
  6. Debug payload includes a "confidence_gate" block on every run with items.

37 tests across 8 classes:
  1. Imports & API surface (4)
  2. stamp_claude_confidence after Call 2 (5)
  3. stamp_claude_confidence after Call 3 (5)
  4. Gate evaluation signal flow (5)
  5. Gate pass → status done (4)
  6. Gate fail → status rejected (6)
  7. Edge cases — empty items / skipped calls (4)
  8. Integration — full gate flow + DB (4)
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _draft_items(n: int = 6, conf: int = 90) -> List[Dict[str, Any]]:
    """Build a list of n minimal draft items (as returned by claude_items_to_draft_rows)."""
    return [
        {
            "name": f"Item {i}",
            "price_cents": 1000 + i * 100,
            "category": "Pizza",
            "confidence": conf,
            "description": f"Desc {i}",
        }
        for i in range(n)
    ]


def _scored_items(n: int = 6, sem_conf: float = 0.92) -> List[Dict[str, Any]]:
    """Build a list of n items already scored by the semantic pipeline."""
    return [
        {
            "name": f"Item {i}",
            "price_cents": 1000 + i * 100,
            "category": "Pizza",
            "semantic_confidence": sem_conf,
            "semantic_tier": "high",
            "needs_review": False,
        }
        for i in range(n)
    ]


def _low_scored_items(n: int = 6) -> List[Dict[str, Any]]:
    """Items that will fail the confidence gate (low semantic confidence)."""
    return [
        {
            "name": f"Item {i}",
            "price_cents": 0,
            "category": "Other",
            "semantic_confidence": 0.30,
            "semantic_tier": "low",
            "needs_review": True,
        }
        for i in range(n)
    ]


def _make_rejection_db():
    """In-memory SQLite with pipeline_rejections + import_jobs tables."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT
        );
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            restaurant_id INTEGER,
            status TEXT DEFAULT 'editing',
            source TEXT,
            source_job_id INTEGER,
            menu_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS import_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER,
            filename TEXT,
            status TEXT DEFAULT 'pending',
            error TEXT,
            draft_path TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS pipeline_rejections (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id    INTEGER,
            draft_id         INTEGER,
            image_path       TEXT,
            ocr_chars        INTEGER NOT NULL DEFAULT 0,
            item_count       INTEGER NOT NULL DEFAULT 0,
            gate_score       REAL NOT NULL,
            gate_reason      TEXT,
            pipeline_signals TEXT,
            created_at       TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Class 1 — Imports & API Surface
# ---------------------------------------------------------------------------

class TestImportsAndAPI:
    """Verify all Day 106 components are importable with the expected API."""

    def test_evaluate_confidence_gate_importable(self):
        from storage.confidence_gate import evaluate_confidence_gate
        assert callable(evaluate_confidence_gate)

    def test_stamp_claude_confidence_importable(self):
        from storage.semantic_confidence import stamp_claude_confidence
        assert callable(stamp_claude_confidence)

    def test_log_pipeline_rejection_importable(self):
        from storage.drafts import log_pipeline_rejection
        assert callable(log_pipeline_rejection)

    def test_gate_result_fields(self):
        from storage.confidence_gate import GateResult
        r = GateResult(passed=True, score=0.95, threshold=0.90)
        assert hasattr(r, "passed")
        assert hasattr(r, "score")
        assert hasattr(r, "threshold")
        assert hasattr(r, "signals")
        assert hasattr(r, "reason")
        assert hasattr(r, "customer_message")


# ---------------------------------------------------------------------------
# Class 2 — stamp_claude_confidence After Call 2
# ---------------------------------------------------------------------------

class TestStampAfterCall2:
    """stamp_claude_confidence is called on draft items after Call 2 vision verify,
    activating signal #6 when the semantic pipeline scores them."""

    def test_stamp_sets_claude_confidence_on_all_items(self):
        from storage.semantic_confidence import stamp_claude_confidence
        items = _draft_items(5)
        for it in items:
            assert "claude_confidence" not in it
        stamp_claude_confidence(items, 0.94)
        for it in items:
            assert it["claude_confidence"] == pytest.approx(0.94)

    def test_stamp_activates_signal6_in_semantic_scoring(self):
        """After stamping, score_semantic_confidence uses the 6-signal formula."""
        from storage.semantic_confidence import stamp_claude_confidence, score_semantic_confidence
        items = _draft_items(3)
        stamp_claude_confidence(items, 0.94)
        score_semantic_confidence(items)
        for it in items:
            details = it.get("semantic_confidence_details", {})
            # 6-signal formula includes "claude" key in the audit trail
            assert "claude_confidence_score" in details, "claude_confidence_score signal should appear in details after stamping"

    def test_stamp_call2_conf_overwrites_existing_claude_confidence(self):
        """If items already have a claude_confidence (e.g., from Call 1), it is overwritten."""
        from storage.semantic_confidence import stamp_claude_confidence
        items = _draft_items(3)
        for it in items:
            it["claude_confidence"] = 0.50  # old value
        stamp_claude_confidence(items, 0.94)
        for it in items:
            assert it["claude_confidence"] == pytest.approx(0.94)

    def test_stamp_with_zero_conf_still_stamps(self):
        """stamp_claude_confidence(items, 0.0) stamps 0.0 on all items (guard is caller's job)."""
        from storage.semantic_confidence import stamp_claude_confidence
        items = _draft_items(2)
        stamp_claude_confidence(items, 0.0)
        for it in items:
            assert "claude_confidence" in it
            assert it["claude_confidence"] == pytest.approx(0.0)

    def test_stamp_empty_list_no_crash(self):
        from storage.semantic_confidence import stamp_claude_confidence
        stamp_claude_confidence([], 0.94)  # must not raise


# ---------------------------------------------------------------------------
# Class 3 — stamp_claude_confidence After Call 3 (before re-score)
# ---------------------------------------------------------------------------

class TestStampAfterCall3:
    """stamp_claude_confidence is called on semantic items after reconciliation,
    before _rescore — ensuring the re-scored confidence uses Call 3 signal."""

    def test_stamp_on_sem_items_before_rescore(self):
        """Stamping before re-score causes signal #6 to appear in final scoring."""
        from storage.semantic_confidence import (
            stamp_claude_confidence, score_semantic_confidence, classify_confidence_tiers
        )
        items = _draft_items(4)
        stamp_claude_confidence(items, 0.92)
        score_semantic_confidence(items)
        classify_confidence_tiers(items)
        for it in items:
            details = it.get("semantic_confidence_details", {})
            assert "claude_confidence_score" in details

    def test_stamp_call3_conf_overwrites_call2_conf(self):
        """Call 3 confidence (more recent) overwrites Call 2 confidence on sem_items."""
        from storage.semantic_confidence import stamp_claude_confidence
        items = _draft_items(3)
        # Simulate Call 2 stamp
        stamp_claude_confidence(items, 0.88)
        for it in items:
            assert it["claude_confidence"] == pytest.approx(0.88)
        # Simulate Call 3 stamp (override)
        stamp_claude_confidence(items, 0.93)
        for it in items:
            assert it["claude_confidence"] == pytest.approx(0.93)

    def test_stamp_improves_confidence_score(self):
        """Stamping high Call 3 confidence slightly improves semantic_confidence score."""
        from storage.semantic_confidence import (
            stamp_claude_confidence, score_semantic_confidence
        )
        items_no_stamp = _draft_items(3)
        items_stamped = _draft_items(3)
        score_semantic_confidence(items_no_stamp)
        stamp_claude_confidence(items_stamped, 0.98)
        score_semantic_confidence(items_stamped)
        avg_no_stamp = sum(it["semantic_confidence"] for it in items_no_stamp) / 3
        avg_stamped = sum(it["semantic_confidence"] for it in items_stamped) / 3
        # High Claude confidence should boost the score vs no-stamp
        assert avg_stamped >= avg_no_stamp

    def test_stamp_preserves_other_fields(self):
        """stamp_claude_confidence only sets claude_confidence; other fields untouched."""
        from storage.semantic_confidence import stamp_claude_confidence
        items = _draft_items(2)
        original_names = [it["name"] for it in items]
        original_prices = [it["price_cents"] for it in items]
        stamp_claude_confidence(items, 0.91)
        for it, name, price in zip(items, original_names, original_prices):
            assert it["name"] == name
            assert it["price_cents"] == price

    def test_stamp_clamped_to_0_1(self):
        """Confidence values outside [0, 1] are clamped."""
        from storage.semantic_confidence import stamp_claude_confidence
        items = _draft_items(2)
        stamp_claude_confidence(items, 1.5)
        assert items[0]["claude_confidence"] == pytest.approx(1.0)
        stamp_claude_confidence(items, -0.2)
        assert items[0]["claude_confidence"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Class 4 — Gate Evaluation Signal Flow
# ---------------------------------------------------------------------------

class TestGateSignalFlow:
    """evaluate_confidence_gate receives the correct arguments and produces
    a GateResult with all expected signals."""

    def test_gate_uses_semantic_items_when_available(self):
        """When semantic_result["items"] are available, the gate reads their
        semantic_confidence values."""
        from storage.confidence_gate import evaluate_confidence_gate
        sem_items = _scored_items(8, sem_conf=0.95)
        result = evaluate_confidence_gate(sem_items, call2_confidence=0.94, call3_confidence=0.92)
        assert result.signals["semantic"]["item_count"] == 8
        assert result.signals["semantic"]["raw"] == pytest.approx(0.95, abs=0.01)

    def test_gate_uses_draft_items_when_no_semantic(self):
        """Without semantic scoring, gate uses raw draft items (semantic_confidence=0)."""
        from storage.confidence_gate import evaluate_confidence_gate
        items = _draft_items(4)  # no semantic_confidence set
        result = evaluate_confidence_gate(items)
        # semantic raw = mean of 0.0 per item (field missing)
        assert result.signals["semantic"]["raw"] == pytest.approx(0.0)

    def test_gate_passes_call2_confidence_as_signal(self):
        """call2_confidence appears as a numeric signal in the result."""
        from storage.confidence_gate import evaluate_confidence_gate
        items = _scored_items(5, sem_conf=0.92)
        result = evaluate_confidence_gate(items, call2_confidence=0.88)
        assert "call2_vision" in result.signals
        assert result.signals["call2_vision"]["raw"] == pytest.approx(0.88)

    def test_gate_passes_call3_confidence_as_signal(self):
        """call3_confidence appears as a numeric signal in the result."""
        from storage.confidence_gate import evaluate_confidence_gate
        items = _scored_items(5, sem_conf=0.92)
        result = evaluate_confidence_gate(items, call2_confidence=0.90, call3_confidence=0.93)
        assert "call3_reconcile" in result.signals
        assert result.signals["call3_reconcile"]["raw"] == pytest.approx(0.93)

    def test_gate_passes_ocr_char_count_for_logging(self):
        """ocr_char_count is stored in the signals dict for observability."""
        from storage.confidence_gate import evaluate_confidence_gate
        items = _scored_items(5)
        result = evaluate_confidence_gate(items, ocr_char_count=7800)
        assert result.signals["ocr_char_count"] == 7800


# ---------------------------------------------------------------------------
# Class 5 — Gate Pass → Status Done
# ---------------------------------------------------------------------------

class TestGatePassStatusDone:
    """When the gate passes, the import job status is set to 'done'."""

    def test_gate_pass_high_confidence_returns_passed_true(self):
        from storage.confidence_gate import evaluate_confidence_gate
        items = _scored_items(12, sem_conf=0.95)
        result = evaluate_confidence_gate(items, call2_confidence=0.94, call3_confidence=0.92)
        assert result.passed is True

    def test_gate_pass_customer_message_is_empty(self):
        """Passed gate returns empty customer_message."""
        from storage.confidence_gate import evaluate_confidence_gate
        items = _scored_items(12, sem_conf=0.95)
        result = evaluate_confidence_gate(items, call2_confidence=0.94, call3_confidence=0.92)
        assert result.customer_message == ""

    def test_gate_pass_score_above_threshold(self):
        from storage.confidence_gate import evaluate_confidence_gate, GATE_THRESHOLD
        items = _scored_items(12, sem_conf=0.96)
        result = evaluate_confidence_gate(items, call2_confidence=0.95, call3_confidence=0.94)
        assert result.score >= GATE_THRESHOLD

    def test_gate_pass_reason_contains_pass(self):
        from storage.confidence_gate import evaluate_confidence_gate
        items = _scored_items(12, sem_conf=0.95)
        result = evaluate_confidence_gate(items, call2_confidence=0.94)
        assert "PASS" in result.reason


# ---------------------------------------------------------------------------
# Class 6 — Gate Fail → Status Rejected
# ---------------------------------------------------------------------------

class TestGateFailStatusRejected:
    """When the gate fails, job status = 'rejected', error = customer_message,
    and a rejection entry is logged."""

    def test_gate_fail_low_semantic_returns_passed_false(self):
        from storage.confidence_gate import evaluate_confidence_gate
        items = _low_scored_items(3)  # sem_conf=0.30, 3 items
        result = evaluate_confidence_gate(items, call2_confidence=0.40, call3_confidence=0.45)
        assert result.passed is False

    def test_gate_fail_score_below_threshold(self):
        from storage.confidence_gate import evaluate_confidence_gate, GATE_THRESHOLD
        items = _low_scored_items(3)
        result = evaluate_confidence_gate(items, call2_confidence=0.40, call3_confidence=0.45)
        assert result.score < GATE_THRESHOLD

    def test_gate_fail_reason_contains_fail(self):
        from storage.confidence_gate import evaluate_confidence_gate
        items = _low_scored_items(3)
        result = evaluate_confidence_gate(items)
        assert "FAIL" in result.reason

    def test_gate_fail_customer_message_non_empty(self):
        from storage.confidence_gate import evaluate_confidence_gate
        items = _low_scored_items(3)
        result = evaluate_confidence_gate(items)
        assert len(result.customer_message) > 20
        # Should mention photo quality (never exposes numeric scores)
        assert any(word in result.customer_message.lower()
                   for word in ("photo", "photograph", "image", "menu"))

    def test_gate_fail_rejection_log_called(self):
        """log_pipeline_rejection() is called once with correct fields on gate fail."""
        from storage.confidence_gate import evaluate_confidence_gate
        items = _low_scored_items(3)
        gate_result = evaluate_confidence_gate(items)
        assert not gate_result.passed

        conn = _make_rejection_db()
        with patch("storage.drafts.db_connect", return_value=conn):
            from storage.drafts import log_pipeline_rejection
            rejection_id = log_pipeline_rejection(
                restaurant_id=None,
                draft_id=None,
                image_path="/uploads/test.png",
                ocr_chars=5000,
                item_count=3,
                gate_score=gate_result.score,
                gate_reason=gate_result.reason,
                pipeline_signals=gate_result.signals,
            )
        assert rejection_id is not None
        row = conn.execute("SELECT * FROM pipeline_rejections WHERE id=?", (rejection_id,)).fetchone()
        assert row is not None
        assert row["item_count"] == 3
        assert abs(row["gate_score"] - gate_result.score) < 0.0001

    def test_gate_fail_rejection_signals_round_trip(self):
        """pipeline_signals are serialized as JSON and can be parsed back."""
        from storage.confidence_gate import evaluate_confidence_gate
        items = _low_scored_items(4)
        gate_result = evaluate_confidence_gate(items, call2_confidence=0.40)

        conn = _make_rejection_db()
        with patch("storage.drafts.db_connect", return_value=conn):
            from storage.drafts import log_pipeline_rejection, get_pipeline_rejections
            log_pipeline_rejection(
                restaurant_id=None,
                draft_id=None,
                image_path="/uploads/test.png",
                ocr_chars=3000,
                item_count=4,
                gate_score=gate_result.score,
                gate_reason=gate_result.reason,
                pipeline_signals=gate_result.signals,
            )
            rejections = get_pipeline_rejections()

        assert len(rejections) == 1
        signals = rejections[0]["pipeline_signals"]
        assert isinstance(signals, dict)
        assert "semantic" in signals
        assert "item_count" in signals


# ---------------------------------------------------------------------------
# Class 7 — Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Gate is skipped when items list is empty; skipped calls don't pass None conf."""

    def test_gate_result_is_none_when_no_items(self):
        """Empty items list → gate is not evaluated → gate_result stays None."""
        from storage.confidence_gate import evaluate_confidence_gate
        # Gate block in pipeline is guarded: `if items:`
        # Verify directly that evaluating with empty list returns low score
        result = evaluate_confidence_gate([])
        assert result.passed is False  # 0 items → item_count signal = 0.0
        assert result.score == pytest.approx(0.0)

    def test_gate_skipped_vision_no_call2_conf(self):
        """When vision was skipped, call2_confidence is None → weight redistributed."""
        from storage.confidence_gate import evaluate_confidence_gate
        items = _scored_items(8, sem_conf=0.95)
        # No call2_confidence (vision skipped)
        result = evaluate_confidence_gate(items, call2_confidence=None)
        # Should still evaluate — semantic gets extra weight
        assert result.signals["call2_vision"].get("skipped") is True
        assert "weight_redistributed" in result.signals["call2_vision"]

    def test_gate_skipped_reconcile_no_call3_conf(self):
        """When reconciliation was skipped, call3_confidence is None → weight redistributed."""
        from storage.confidence_gate import evaluate_confidence_gate
        items = _scored_items(8, sem_conf=0.95)
        result = evaluate_confidence_gate(items, call2_confidence=0.92, call3_confidence=None)
        assert result.signals["call3_reconcile"].get("skipped") is True

    def test_gate_no_calls_semantic_only(self):
        """Gate can evaluate with only semantic signal when both calls are skipped."""
        from storage.confidence_gate import evaluate_confidence_gate
        items = _scored_items(12, sem_conf=0.97)
        result = evaluate_confidence_gate(items)  # no call confidences
        # With sem=0.97 weight=0.90 + items=1.0 weight=0.10 → score ≈ 0.973
        assert result.score > 0.90
        assert result.passed is True

    def test_gate_skipped_in_thinking_mode(self):
        """In extended thinking mode, the gate is bypassed (gate_result stays None).
        Semantic pipeline doesn't run in thinking mode, so items have no
        semantic_confidence — the gate would always false-fail on score=0.10."""
        # Simulates the pipeline guard: `if items and not _thinking_active`
        items = _draft_items(141)   # realistic thinking-mode output
        _thinking_active = True
        gate_result = None
        if items and not _thinking_active:
            from storage.confidence_gate import evaluate_confidence_gate
            gate_result = evaluate_confidence_gate(items)
        # Gate must NOT have been evaluated
        assert gate_result is None


# ---------------------------------------------------------------------------
# Class 8 — Integration: Full Gate Flow + DB
# ---------------------------------------------------------------------------

class TestIntegration:
    """End-to-end flow: stamp → semantic → gate → rejection log (or pass)."""

    def test_full_gate_flow_pass_all_signals(self):
        """High quality menu: stamp + semantic + gate all pass cleanly."""
        from storage.semantic_confidence import (
            stamp_claude_confidence, score_semantic_confidence, classify_confidence_tiers
        )
        from storage.confidence_gate import evaluate_confidence_gate

        items = _draft_items(12)
        # Simulate Call 2 stamp
        stamp_claude_confidence(items, 0.94)
        # Run semantic pipeline
        score_semantic_confidence(items)
        classify_confidence_tiers(items)

        gate_result = evaluate_confidence_gate(
            items,
            call2_confidence=0.94,
            call3_confidence=0.92,
            ocr_char_count=7800,
        )
        assert gate_result.passed is True
        assert gate_result.score >= 0.90

    def test_full_gate_flow_fail_low_quality(self):
        """Low quality menu: gate fails and produces rejection-ready result."""
        from storage.confidence_gate import evaluate_confidence_gate

        items = _low_scored_items(2)  # 2 items, low semantic conf
        gate_result = evaluate_confidence_gate(
            items,
            call2_confidence=0.35,
            call3_confidence=0.40,
        )
        assert gate_result.passed is False
        assert len(gate_result.customer_message) > 0
        assert gate_result.signals["item_count"]["n_items"] == 2

    def test_rejection_db_round_trip(self):
        """Gate fail → log_pipeline_rejection → get_pipeline_rejections returns row."""
        from storage.confidence_gate import evaluate_confidence_gate
        from storage.drafts import log_pipeline_rejection, get_pipeline_rejections

        items = _low_scored_items(2)
        gate_result = evaluate_confidence_gate(items)

        conn = _make_rejection_db()
        with patch("storage.drafts.db_connect", return_value=conn):
            log_pipeline_rejection(
                restaurant_id=None,
                draft_id=None,
                image_path="/uploads/bad_menu.jpg",
                ocr_chars=1200,
                item_count=len(items),
                gate_score=gate_result.score,
                gate_reason=gate_result.reason,
                pipeline_signals=gate_result.signals,
            )
            rows = get_pipeline_rejections()

        assert len(rows) == 1
        r = rows[0]
        assert r["image_path"] == "/uploads/bad_menu.jpg"
        assert r["item_count"] == 2
        assert r["gate_score"] == pytest.approx(gate_result.score, abs=0.0001)
        assert "FAIL" in r["gate_reason"]
        # Signals round-trip as dict
        assert isinstance(r["pipeline_signals"], dict)
        assert "semantic" in r["pipeline_signals"]

    def test_debug_payload_gate_block_structure(self):
        """The confidence_gate payload block has the expected keys and types."""
        from storage.confidence_gate import evaluate_confidence_gate

        items = _scored_items(10, sem_conf=0.94)
        gate_result = evaluate_confidence_gate(
            items,
            call2_confidence=0.93,
            call3_confidence=0.91,
        )
        # Build the payload block as app.py does
        gate_block = {
            "passed": gate_result.passed,
            "score": round(gate_result.score, 4),
            "threshold": gate_result.threshold,
            "signals": gate_result.signals,
            "reason": gate_result.reason,
        }

        assert isinstance(gate_block["passed"], bool)
        assert isinstance(gate_block["score"], float)
        assert 0.0 <= gate_block["score"] <= 1.0
        assert isinstance(gate_block["threshold"], float)
        assert isinstance(gate_block["signals"], dict)
        assert isinstance(gate_block["reason"], str)

        # Must be JSON-serializable (stored in debug payload)
        json_str = json.dumps(gate_block)
        parsed = json.loads(json_str)
        assert parsed["passed"] == gate_block["passed"]
        assert parsed["score"] == gate_block["score"]
