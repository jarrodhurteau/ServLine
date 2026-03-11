# tests/test_day105_confidence_gate.py
"""
Day 105 — Sprint 11.3: Confidence Gate Foundation.

Sprint 11.3 goal: binary pass/fail gate at the menu level, Claude's
self-reported confidence as signal #6 in semantic scoring, and a rejection
logging system so failed parses can drive future pipeline hardening.

33 tests across 7 classes:
  1.  Module imports and API surface (4)
  2.  Signal #6 in semantic_confidence — opt-in (8)
  3.  stamp_claude_confidence helper (4)
  4.  Gate signal computation — weights and redistribution (5)
  5.  Gate pass/fail decisions (6)
  6.  Gate with optional signals skipped (4)
  7.  Rejection logging — DB round-trip (6) [uses monkeypatched in-memory DB]
  8.  Integration: gate + rejection log together (2 bonus)
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(
    *,
    name: str = "Margherita Pizza",
    price_cents: int = 1200,
    confidence: float | None = None,
    grammar_conf: float = 0.90,
    claude_confidence: float | None = None,
) -> Dict[str, Any]:
    """Build a minimal scored item dict."""
    it: Dict[str, Any] = {
        "name": name,
        "price_cents": price_cents,
        "category": "Pizza",
        "grammar": {"parse_confidence": grammar_conf},
    }
    if confidence is not None:
        it["confidence"] = confidence
    if claude_confidence is not None:
        it["claude_confidence"] = claude_confidence
    return it


def _scored_item(
    *,
    semantic_confidence: float = 0.85,
    semantic_tier: str = "high",
    needs_review: bool = False,
    claude_confidence: float | None = None,
) -> Dict[str, Any]:
    """Build a pre-scored item (already through semantic pipeline)."""
    it: Dict[str, Any] = {
        "name": "Test Item",
        "price_cents": 1000,
        "category": "Other",
        "semantic_confidence": semantic_confidence,
        "semantic_tier": semantic_tier,
        "needs_review": needs_review,
    }
    if claude_confidence is not None:
        it["claude_confidence"] = claude_confidence
    return it


def _make_test_db():
    """In-memory SQLite with the full schema needed for rejection logging."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    cur = conn.cursor()
    # Minimal tables the _init_db() call will create
    cur.executescript("""
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
            source_file_path TEXT,
            menu_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
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
            created_at       TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Class 1 — Module imports and API surface
# ---------------------------------------------------------------------------

class TestModuleImports:
    def test_confidence_gate_importable(self):
        from storage.confidence_gate import evaluate_confidence_gate, GateResult
        assert callable(evaluate_confidence_gate)

    def test_gate_result_is_dataclass(self):
        from storage.confidence_gate import GateResult
        r = GateResult(passed=True, score=0.95, threshold=0.90)
        assert r.passed is True
        assert r.score == 0.95

    def test_stamp_claude_confidence_importable(self):
        from storage.semantic_confidence import stamp_claude_confidence
        assert callable(stamp_claude_confidence)

    def test_rejection_log_functions_importable(self):
        from storage.drafts import log_pipeline_rejection, get_pipeline_rejections
        assert callable(log_pipeline_rejection)
        assert callable(get_pipeline_rejections)


# ---------------------------------------------------------------------------
# Class 2 — Signal #6 in semantic_confidence — opt-in
# ---------------------------------------------------------------------------

class TestSignal6SemanticConfidence:
    """Adding claude_confidence to an item activates the 6-signal formula."""

    def test_without_claude_confidence_uses_5_signal(self):
        """Items without claude_confidence must produce unchanged scores."""
        from storage.semantic_confidence import score_semantic_confidence
        item = _item(grammar_conf=1.0, price_cents=1200)
        score_semantic_confidence([item])
        details = item["semantic_confidence_details"]
        # Old 5-signal formula: grammar weight = 0.30
        assert details["grammar_weight"] == pytest.approx(0.30, abs=1e-4)
        assert "claude_confidence_score" not in details

    def test_with_claude_confidence_uses_6_signal(self):
        """Items WITH claude_confidence use the 6-signal formula."""
        from storage.semantic_confidence import score_semantic_confidence
        item = _item(grammar_conf=1.0, price_cents=1200, claude_confidence=0.95)
        score_semantic_confidence([item])
        details = item["semantic_confidence_details"]
        # 6-signal formula: grammar weight = 0.27
        assert details["grammar_weight"] == pytest.approx(0.27, abs=1e-4)
        assert "claude_confidence_score" in details
        assert details["claude_confidence_weight"] == pytest.approx(0.10, abs=1e-4)

    def test_6signal_details_include_claude_fields(self):
        from storage.semantic_confidence import score_semantic_confidence
        item = _item(claude_confidence=0.92)
        score_semantic_confidence([item])
        d = item["semantic_confidence_details"]
        assert "claude_confidence_score" in d
        assert "claude_confidence_weight" in d
        assert "claude_confidence_weighted" in d

    def test_6signal_weights_sum_to_1(self):
        from storage import semantic_confidence as sc
        total = (sc._W6_GRAMMAR + sc._W6_NAME + sc._W6_PRICE
                 + sc._W6_VARIANT + sc._W6_FLAGS + sc._W6_CLAUDE)
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_5signal_weights_unchanged(self):
        from storage import semantic_confidence as sc
        total = (sc._W_GRAMMAR + sc._W_NAME + sc._W_PRICE
                 + sc._W_VARIANT + sc._W_FLAGS)
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_high_claude_confidence_boosts_score(self):
        """High claude_confidence raises the score vs no claude_confidence."""
        from storage.semantic_confidence import score_semantic_confidence
        item_plain = _item(grammar_conf=0.70, price_cents=1000)
        item_claude = _item(grammar_conf=0.70, price_cents=1000, claude_confidence=1.0)
        score_semantic_confidence([item_plain, item_claude])
        # 6-signal with claude=1.0 should score >= 5-signal
        assert item_claude["semantic_confidence"] >= item_plain["semantic_confidence"]

    def test_low_claude_confidence_lowers_score(self):
        """Low claude_confidence pulls the score down."""
        from storage.semantic_confidence import score_semantic_confidence
        item_plain = _item(grammar_conf=1.0, price_cents=1200)
        item_claude = _item(grammar_conf=1.0, price_cents=1200, claude_confidence=0.0)
        score_semantic_confidence([item_plain, item_claude])
        assert item_claude["semantic_confidence"] < item_plain["semantic_confidence"]

    def test_neutral_claude_confidence_minimal_change(self):
        """claude_confidence=0.5 should produce nearly the same score as no claude."""
        from storage.semantic_confidence import score_semantic_confidence
        item_plain = _item(grammar_conf=0.85, price_cents=1100)
        item_claude = _item(grammar_conf=0.85, price_cents=1100, claude_confidence=0.5)
        score_semantic_confidence([item_plain, item_claude])
        # 0.5 is the neutral default — scores should be within ~2%
        diff = abs(item_claude["semantic_confidence"] - item_plain["semantic_confidence"])
        assert diff < 0.05


# ---------------------------------------------------------------------------
# Class 3 — stamp_claude_confidence
# ---------------------------------------------------------------------------

class TestStampClaudeConfidence:
    def test_stamps_all_items(self):
        from storage.semantic_confidence import stamp_claude_confidence
        items = [_item(), _item(name="Calzone"), _item(name="Tiramisu")]
        stamp_claude_confidence(items, 0.93)
        for it in items:
            assert it["claude_confidence"] == pytest.approx(0.93, abs=1e-6)

    def test_clamps_above_1(self):
        from storage.semantic_confidence import stamp_claude_confidence
        items = [_item()]
        stamp_claude_confidence(items, 1.5)
        assert items[0]["claude_confidence"] == pytest.approx(1.0, abs=1e-6)

    def test_clamps_below_0(self):
        from storage.semantic_confidence import stamp_claude_confidence
        items = [_item()]
        stamp_claude_confidence(items, -0.2)
        assert items[0]["claude_confidence"] == pytest.approx(0.0, abs=1e-6)

    def test_empty_list_no_error(self):
        from storage.semantic_confidence import stamp_claude_confidence
        stamp_claude_confidence([], 0.9)  # must not raise


# ---------------------------------------------------------------------------
# Class 4 — Gate signal computation and weight redistribution
# ---------------------------------------------------------------------------

class TestGateSignals:
    def test_semantic_signal_is_mean_confidence(self):
        from storage.confidence_gate import _score_semantic_signal
        items = [
            {"semantic_confidence": 0.80},
            {"semantic_confidence": 0.60},
            {"semantic_confidence": 1.00},
        ]
        result = _score_semantic_signal(items)
        assert result == pytest.approx(0.80, abs=1e-4)

    def test_semantic_signal_empty_is_zero(self):
        from storage.confidence_gate import _score_semantic_signal
        assert _score_semantic_signal([]) == 0.0

    def test_item_count_scores(self):
        from storage.confidence_gate import _score_item_count
        assert _score_item_count(0)  == pytest.approx(0.0, abs=1e-4)
        assert _score_item_count(1)  == pytest.approx(0.3, abs=1e-4)
        assert _score_item_count(2)  == pytest.approx(0.3, abs=1e-4)
        assert _score_item_count(3)  == pytest.approx(0.6, abs=1e-4)
        assert _score_item_count(5)  == pytest.approx(0.8, abs=1e-4)
        assert _score_item_count(10) == pytest.approx(1.0, abs=1e-4)
        assert _score_item_count(50) == pytest.approx(1.0, abs=1e-4)

    def test_weights_sum_to_1_all_available(self):
        from storage.confidence_gate import _compute_weights
        w = _compute_weights(call2_available=True, call3_available=True)
        total = sum(w.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_weights_redistribute_when_calls_skipped(self):
        from storage.confidence_gate import _compute_weights, _W_CALL2, _W_CALL3, _W_SEMANTIC
        w_none = _compute_weights(call2_available=False, call3_available=False)
        # All call weight should flow to semantic
        expected_semantic = _W_SEMANTIC + _W_CALL2 + _W_CALL3
        assert w_none["semantic"] == pytest.approx(expected_semantic, abs=1e-6)
        assert w_none["call2"] == 0.0
        assert w_none["call3"] == 0.0
        total = sum(w_none.values())
        assert total == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Class 5 — Gate pass/fail decisions
# ---------------------------------------------------------------------------

class TestGateDecisions:
    def _high_quality_items(self, n: int = 15) -> List[Dict[str, Any]]:
        return [_scored_item(semantic_confidence=0.92) for _ in range(n)]

    def test_all_signals_high_passes(self):
        from storage.confidence_gate import evaluate_confidence_gate
        items = self._high_quality_items(20)
        result = evaluate_confidence_gate(
            items, call2_confidence=0.95, call3_confidence=0.97
        )
        assert result.passed is True
        assert result.score >= 0.90

    def test_low_semantic_fails(self):
        from storage.confidence_gate import evaluate_confidence_gate
        items = [_scored_item(semantic_confidence=0.30) for _ in range(15)]
        result = evaluate_confidence_gate(
            items, call2_confidence=0.95, call3_confidence=0.95
        )
        assert result.passed is False

    def test_low_call2_fails(self):
        from storage.confidence_gate import evaluate_confidence_gate
        items = self._high_quality_items()
        result = evaluate_confidence_gate(
            items, call2_confidence=0.20, call3_confidence=0.95
        )
        assert result.passed is False

    def test_empty_items_fails(self):
        from storage.confidence_gate import evaluate_confidence_gate
        result = evaluate_confidence_gate(
            [], call2_confidence=0.99, call3_confidence=0.99
        )
        assert result.passed is False

    def test_customer_message_on_fail(self):
        from storage.confidence_gate import evaluate_confidence_gate
        items = [_scored_item(semantic_confidence=0.10) for _ in range(5)]
        result = evaluate_confidence_gate(items)
        assert result.passed is False
        assert len(result.customer_message) > 10
        # Must not mention scores/numbers
        assert "%" not in result.customer_message
        assert "0." not in result.customer_message

    def test_customer_message_empty_on_pass(self):
        from storage.confidence_gate import evaluate_confidence_gate
        items = self._high_quality_items(20)
        result = evaluate_confidence_gate(
            items, call2_confidence=0.95, call3_confidence=0.97
        )
        assert result.passed is True
        assert result.customer_message == ""

    def test_custom_threshold(self):
        from storage.confidence_gate import evaluate_confidence_gate
        items = [_scored_item(semantic_confidence=0.70) for _ in range(12)]
        # With default threshold (0.90) this should fail, but at 0.50 it passes
        result_strict = evaluate_confidence_gate(
            items, call2_confidence=0.75, call3_confidence=0.75
        )
        result_loose = evaluate_confidence_gate(
            items, call2_confidence=0.75, call3_confidence=0.75, threshold=0.50
        )
        assert result_strict.passed is False
        assert result_loose.passed is True


# ---------------------------------------------------------------------------
# Class 6 — Gate with optional signals skipped
# ---------------------------------------------------------------------------

class TestGateOptionalSignals:
    def _ok_items(self, n: int = 12) -> List[Dict[str, Any]]:
        return [_scored_item(semantic_confidence=0.88) for _ in range(n)]

    def test_no_call2_no_call3_uses_semantic_only(self):
        """Without any call confidences, gate uses high semantic + item count."""
        from storage.confidence_gate import evaluate_confidence_gate
        items = [_scored_item(semantic_confidence=0.96) for _ in range(20)]
        result = evaluate_confidence_gate(items)  # no call confidences
        # High semantic + good item count should still pass
        assert result.passed is True

    def test_skipped_calls_reflected_in_signals(self):
        from storage.confidence_gate import evaluate_confidence_gate
        result = evaluate_confidence_gate(self._ok_items())
        assert result.signals["call2_vision"].get("skipped") is True
        assert result.signals["call3_reconcile"].get("skipped") is True

    def test_call2_only(self):
        from storage.confidence_gate import evaluate_confidence_gate
        items = [_scored_item(semantic_confidence=0.92) for _ in range(15)]
        result = evaluate_confidence_gate(items, call2_confidence=0.95)
        assert "raw" in result.signals["call2_vision"]
        assert result.signals["call3_reconcile"].get("skipped") is True

    def test_signals_dict_always_present(self):
        from storage.confidence_gate import evaluate_confidence_gate
        result = evaluate_confidence_gate([])
        assert "semantic" in result.signals
        assert "item_count" in result.signals
        assert "call2_vision" in result.signals
        assert "call3_reconcile" in result.signals


# ---------------------------------------------------------------------------
# Class 7 — Rejection logging DB round-trip
# ---------------------------------------------------------------------------

class TestRejectionLogging:
    """Uses monkeypatched db_connect so tests are self-contained."""

    def _patched_conn(self):
        return _make_test_db()

    def test_log_rejection_returns_id(self, tmp_path):
        from storage import drafts as drafts_mod
        conn = _make_test_db()
        with patch.object(drafts_mod, "db_connect", return_value=conn):
            row_id = drafts_mod.log_pipeline_rejection(
                restaurant_id=1,
                draft_id=None,
                gate_score=0.72,
                gate_reason="FAIL score=0.72 threshold=0.90 [low_semantic=0.65]",
                ocr_chars=5000,
                item_count=18,
            )
        assert isinstance(row_id, int)
        assert row_id >= 1

    def test_logged_rejection_retrievable(self, tmp_path):
        from storage import drafts as drafts_mod
        conn = _make_test_db()
        with patch.object(drafts_mod, "db_connect", return_value=conn):
            drafts_mod.log_pipeline_rejection(
                restaurant_id=5,
                draft_id=None,
                gate_score=0.55,
                gate_reason="FAIL score=0.55 [low_semantic=0.40]",
                ocr_chars=3200,
                item_count=8,
                pipeline_signals={"semantic": {"raw": 0.40}},
            )
            rows = drafts_mod.get_pipeline_rejections()

        assert len(rows) == 1
        r = rows[0]
        assert r["gate_score"] == pytest.approx(0.55, abs=1e-5)
        assert r["item_count"] == 8
        assert r["ocr_chars"] == 3200
        assert r["pipeline_signals"]["semantic"]["raw"] == pytest.approx(0.40, abs=1e-5)

    def test_restaurant_scoping(self, tmp_path):
        from storage import drafts as drafts_mod
        conn = _make_test_db()
        with patch.object(drafts_mod, "db_connect", return_value=conn):
            drafts_mod.log_pipeline_rejection(1, None, 0.60, "FAIL", ocr_chars=100, item_count=5)
            drafts_mod.log_pipeline_rejection(2, None, 0.70, "FAIL", ocr_chars=200, item_count=10)
            drafts_mod.log_pipeline_rejection(1, None, 0.50, "FAIL", ocr_chars=300, item_count=3)

            all_rows = drafts_mod.get_pipeline_rejections()
            r1_rows = drafts_mod.get_pipeline_rejections(restaurant_id=1)
            r2_rows = drafts_mod.get_pipeline_rejections(restaurant_id=2)

        assert len(all_rows) == 3
        assert len(r1_rows) == 2
        assert len(r2_rows) == 1

    def test_ordering_most_recent_first(self, tmp_path):
        from storage import drafts as drafts_mod
        conn = _make_test_db()
        with patch.object(drafts_mod, "db_connect", return_value=conn):
            id1 = drafts_mod.log_pipeline_rejection(1, None, 0.60, "A", ocr_chars=100, item_count=5)
            id2 = drafts_mod.log_pipeline_rejection(1, None, 0.70, "B", ocr_chars=100, item_count=5)
            id3 = drafts_mod.log_pipeline_rejection(1, None, 0.80, "C", ocr_chars=100, item_count=5)
            rows = drafts_mod.get_pipeline_rejections()

        assert rows[0]["id"] == id3
        assert rows[1]["id"] == id2
        assert rows[2]["id"] == id1

    def test_limit_parameter(self, tmp_path):
        from storage import drafts as drafts_mod
        conn = _make_test_db()
        with patch.object(drafts_mod, "db_connect", return_value=conn):
            for i in range(10):
                drafts_mod.log_pipeline_rejection(1, None, 0.50 + i * 0.01, f"FAIL-{i}", ocr_chars=100, item_count=5)
            rows = drafts_mod.get_pipeline_rejections(limit=3)

        assert len(rows) == 3

    def test_pipeline_signals_json_roundtrip(self, tmp_path):
        from storage import drafts as drafts_mod
        conn = _make_test_db()
        signals = {
            "semantic": {"raw": 0.55, "weighted": 0.28},
            "call2_vision": {"raw": 0.88, "weighted": 0.22},
            "item_count": {"n_items": 12},
        }
        with patch.object(drafts_mod, "db_connect", return_value=conn):
            drafts_mod.log_pipeline_rejection(
                1, None, 0.62, "FAIL",
                pipeline_signals=signals,
                ocr_chars=4500,
                item_count=12,
            )
            rows = drafts_mod.get_pipeline_rejections()

        retrieved = rows[0]["pipeline_signals"]
        assert retrieved["semantic"]["raw"] == pytest.approx(0.55, abs=1e-6)
        assert retrieved["call2_vision"]["raw"] == pytest.approx(0.88, abs=1e-6)
        assert retrieved["item_count"]["n_items"] == 12


# ---------------------------------------------------------------------------
# Bonus: Integration — gate result feeds directly into rejection log
# ---------------------------------------------------------------------------

class TestGateToRejectionIntegration:
    def test_gate_result_signals_storable(self):
        """GateResult.signals dict must be JSON-serializable."""
        from storage.confidence_gate import evaluate_confidence_gate
        items = [_scored_item(semantic_confidence=0.30) for _ in range(5)]
        result = evaluate_confidence_gate(
            items, call2_confidence=0.40, call3_confidence=0.50
        )
        # Must not raise
        encoded = json.dumps(result.signals)
        decoded = json.loads(encoded)
        assert "semantic" in decoded

    def test_full_gate_and_log_flow(self, tmp_path):
        """Gate fails → log_pipeline_rejection stores everything."""
        from storage.confidence_gate import evaluate_confidence_gate
        from storage import drafts as drafts_mod

        items = [_scored_item(semantic_confidence=0.40) for _ in range(7)]
        result = evaluate_confidence_gate(
            items,
            call2_confidence=0.55,
            call3_confidence=0.60,
            ocr_char_count=2500,
        )
        assert result.passed is False

        conn = _make_test_db()
        with patch.object(drafts_mod, "db_connect", return_value=conn):
            row_id = drafts_mod.log_pipeline_rejection(
                restaurant_id=3,
                draft_id=None,
                gate_score=result.score,
                gate_reason=result.reason,
                ocr_chars=2500,
                item_count=len(items),
                pipeline_signals=result.signals,
            )
            rows = drafts_mod.get_pipeline_rejections(restaurant_id=3)

        assert len(rows) == 1
        row = rows[0]
        assert row["gate_score"] == pytest.approx(result.score, abs=1e-5)
        assert row["gate_reason"] == result.reason
        assert row["item_count"] == 7
        assert "semantic" in row["pipeline_signals"]
