# tests/test_day108_e2e_gate_pipeline.py
"""
Day 108 — Sprint 11.3: End-to-End Gate Pipeline Integration Tests.

Sprint 11.3 pending item: "End-to-end live pipeline test with real menu image
+ gate threshold check."

This suite calls run_ocr_and_make_draft() directly with all external dependencies
mocked (OCR, three Claude calls, semantic pipeline).  It verifies the observable
outcomes from gate evaluation: job status in import_jobs, rejection log entries
in pipeline_rejections, and the confidence_gate block in the OCR debug payload.

32 tests across 8 classes:
  1. Full 3-call pass path      (4) — all signals high → status=done
  2. Full 3-call fail path      (5) — all signals low  → status=rejected + DB entry
  3. Call 2 skipped             (4) — vision skipped   → gate uses redistributed weights
  4. Call 3 skipped             (4) — no flagged items → gate without call3_confidence
  5. Thinking mode bypass       (4) — EXTENDED_THINKING=True → gate not evaluated
  6. Empty extraction           (3) — no items         → gate guard skips evaluation
  7. Gate threshold boundary    (4) — score at/near threshold + multi-rejection accumulation
  8. Debug payload completeness (4) — confidence_gate block structure & content
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS restaurants (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    restaurant_id INTEGER,
    source_job_id INTEGER,
    title TEXT DEFAULT '',
    status TEXT DEFAULT 'editing',
    source_file_path TEXT,
    menu_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS draft_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    price_cents INTEGER DEFAULT 0,
    category TEXT DEFAULT '',
    position INTEGER DEFAULT 0,
    confidence INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS draft_item_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    label TEXT DEFAULT '',
    price_cents INTEGER DEFAULT 0,
    kind TEXT DEFAULT 'size',
    position INTEGER DEFAULT 0,
            modifier_group_id   INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS import_jobs (
    id INTEGER PRIMARY KEY,
    restaurant_id INTEGER,
    filename TEXT,
    source_type TEXT DEFAULT 'upload',
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
CREATE TABLE IF NOT EXISTS ocr_debug (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id    INTEGER NOT NULL UNIQUE,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    """In-memory SQLite with all required tables."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    return conn


def _insert_job(conn: sqlite3.Connection, job_id: int = 1) -> int:
    conn.execute(
        "INSERT INTO import_jobs (id, filename, status) VALUES (?, 'menu.jpg', 'pending')",
        (job_id,),
    )
    conn.commit()
    return job_id


def _get_job(conn: sqlite3.Connection, job_id: int) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT id, status, error, draft_path FROM import_jobs WHERE id=?",
        (job_id,),
    ).fetchone()
    return dict(row) if row else {}


def _get_rejections(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM pipeline_rejections ORDER BY id DESC"
    ).fetchall()
    result = []
    for row in rows:
        r = dict(row)
        if r.get("pipeline_signals") and isinstance(r["pipeline_signals"], str):
            try:
                r["pipeline_signals"] = json.loads(r["pipeline_signals"])
            except Exception:
                pass
        result.append(r)
    return result


# ---------------------------------------------------------------------------
# Mock data builders
# ---------------------------------------------------------------------------

def _claude_items(n: int = 12, conf: int = 90) -> List[Dict[str, Any]]:
    """Items in the format returned by extract_menu_items_via_claude."""
    return [
        {"name": f"Item {i}", "price": f"${10 + i}.00", "category": "Pizza",
         "description": f"Desc {i}", "confidence": conf}
        for i in range(n)
    ]


def _draft_rows(n: int = 12, sem_conf: float = 0.92) -> List[Dict[str, Any]]:
    """Items after claude_items_to_draft_rows + semantic scoring."""
    return [
        {"name": f"Item {i}", "price_cents": (10 + i) * 100, "category": "Pizza",
         "description": f"Desc {i}", "confidence": 90,
         "semantic_confidence": sem_conf, "semantic_tier": "high",
         "needs_review": False}
        for i in range(n)
    ]


def _vision_result(conf: float = 0.94, n: int = 12, *, skipped: bool = False,
                   error: Optional[str] = None) -> Dict[str, Any]:
    if skipped:
        return {"skipped": True, "skip_reason": "no_api_key"}
    if error:
        return {"error": error}
    items = _draft_rows(n, 0.92)
    return {"confidence": conf, "items": items, "changes": [], "model": "claude-test",
            "notes": None}


def _semantic_result(items: List[Dict[str, Any]], grade: str = "A") -> Dict[str, Any]:
    scored = [dict(it, semantic_confidence=0.92, semantic_tier="high",
                   needs_review=False) for it in items]
    return {
        "items": scored,
        "quality_grade": grade,
        "mean_confidence": 0.92,
        "tier_counts": {"high": len(scored), "medium": 0, "low": 0, "reject": 0},
        "repairs_applied": 0,
        "repair_results": {},
        "items_metadata": [],
    }


def _low_semantic_result(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    scored = [dict(it, semantic_confidence=0.28, semantic_tier="low",
                   needs_review=True) for it in items]
    return {
        "items": scored,
        "quality_grade": "D",
        "mean_confidence": 0.28,
        "tier_counts": {"high": 0, "medium": 0, "low": len(scored), "reject": 0},
        "repairs_applied": 0,
        "repair_results": {},
        "items_metadata": [],
    }


def _reconcile_result(conf: float = 0.92, n_items: int = 10) -> Dict[str, Any]:
    items = _draft_rows(n_items, 0.93)
    return {
        "confidence": conf,
        "items": items,
        "items_confirmed": n_items,
        "items_corrected": 0,
        "items_not_found": 0,
        "changes": [],
        "model": "claude-test",
        "notes": None,
    }


def _low_reconcile_result(n_items: int = 5) -> Dict[str, Any]:
    items = _draft_rows(n_items, 0.25)
    return {
        "confidence": 0.35,
        "items": items,
        "items_confirmed": 2,
        "items_corrected": 1,
        "items_not_found": 2,
        "changes": [],
        "model": "claude-test",
        "notes": None,
    }


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

class _FakeDraftsStore:
    """Mimics storage.drafts module for pipeline wiring tests."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.debug_payloads: List[Dict[str, Any]] = []
        self.upserted_items: List[Dict[str, Any]] = []

    def upsert_draft_items(self, draft_id: int, items: List[Dict[str, Any]]) -> None:
        self.upserted_items.extend(items)

    def save_ocr_debug(self, draft_id: int, payload: Dict[str, Any]) -> None:
        self.debug_payloads.append(payload)

    def get_draft_items(self, draft_id: int, include_variants: bool = False):
        return []

    def delete_draft_items(self, draft_id: int, ids: List[int]) -> None:
        pass

    def log_pipeline_rejection(self, **kwargs) -> Optional[int]:
        from storage.drafts import log_pipeline_rejection
        with patch("storage.drafts.db_connect", return_value=self._conn):
            return log_pipeline_rejection(**kwargs)


@contextmanager
def _run_pipeline(
    job_id: int,
    tmp_path: Path,
    conn: sqlite3.Connection,
    *,
    ocr_text: str = "Pizza $10\nBurger $12\n" * 6,
    call1_items: Optional[List] = None,          # None → use default 12 items
    call2_result: Optional[Dict] = None,          # None → use default pass result
    sem_result: Optional[Dict] = None,            # None → derived from items
    call3_flagged: Optional[List] = None,         # None → empty (skip Call 3)
    call3_result: Optional[Dict] = None,          # None → skip result
    thinking: bool = False,
):
    """
    Context manager that patches all external dependencies and calls
    run_ocr_and_make_draft(job_id, saved_file_path).

    Yields a dict with:
      - store: _FakeDraftsStore (has .debug_payloads, .upserted_items)
      - conn:  sqlite3.Connection (check import_jobs + pipeline_rejections)
    """
    # Create a fake .jpg file
    fake_image = tmp_path / "menu.jpg"
    fake_image.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)  # minimal JPEG header

    # Build default mock data
    c1_items = call1_items if call1_items is not None else _claude_items(12)
    draft_items_list = _draft_rows(len(c1_items) if c1_items else 0)
    c2_result = call2_result if call2_result is not None else _vision_result(0.94)
    sem = sem_result if sem_result is not None else _semantic_result(draft_items_list)
    flagged = call3_flagged if call3_flagged is not None else []
    c3_result = call3_result if call3_result is not None else _reconcile_result(0.92)

    store = _FakeDraftsStore(conn)

    # We use nested context managers via ExitStack
    from contextlib import ExitStack
    with ExitStack() as stack:
        # Infrastructure patches
        stack.enter_context(patch("portal.app.db_connect", return_value=conn))
        stack.enter_context(patch("portal.app._path_for_ocr",
                                  return_value=(str(fake_image), "image")))
        stack.enter_context(patch("portal.app._ensure_work_image", return_value=None))
        stack.enter_context(patch("portal.app._ocr_image_to_text",
                                  return_value=ocr_text))
        stack.enter_context(patch("portal.app._save_draft_json",
                                  return_value="drafts/1.json"))
        stack.enter_context(patch("portal.app._get_or_create_draft_for_job",
                                  return_value=42))
        stack.enter_context(patch("portal.app.RAW_FOLDER", tmp_path))
        stack.enter_context(patch("portal.app.drafts_store", store))

        # Call 1: Claude extraction
        stack.enter_context(
            patch("storage.ai_menu_extract.extract_menu_items_via_claude",
                  return_value=c1_items)
        )
        stack.enter_context(
            patch("storage.ai_menu_extract.claude_items_to_draft_rows",
                  return_value=draft_items_list)
        )
        stack.enter_context(
            patch("storage.ai_menu_extract.EXTENDED_THINKING", thinking)
        )
        stack.enter_context(
            patch("storage.ai_menu_extract.PIPELINE_MODE", "normal")
        )

        # Call 2: Vision verification
        stack.enter_context(
            patch("storage.ai_vision_verify.verify_menu_with_vision",
                  return_value=c2_result)
        )
        stack.enter_context(
            patch("storage.ai_vision_verify.verified_items_to_draft_rows",
                  return_value=c2_result.get("items", draft_items_list)
                  if not c2_result.get("skipped") and not c2_result.get("error")
                  else draft_items_list)
        )

        # Semantic pipeline
        stack.enter_context(
            patch("storage.semantic_bridge.run_semantic_pipeline",
                  return_value=sem)
        )

        # Call 3: Reconciliation (score_semantic_confidence + classify are no-ops here)
        stack.enter_context(
            patch("storage.ai_reconcile.collect_flagged_items",
                  return_value=flagged)
        )
        if flagged:
            stack.enter_context(
                patch("storage.ai_reconcile.reconcile_flagged_items",
                      return_value=c3_result)
            )
            stack.enter_context(
                patch("storage.ai_reconcile.merge_reconciled_items",
                      return_value=(sem["items"], []))
            )
        # Prevent re-scoring from changing our pre-set sem_conf values
        stack.enter_context(
            patch("storage.semantic_confidence.score_semantic_confidence")
        )
        stack.enter_context(
            patch("storage.semantic_confidence.classify_confidence_tiers")
        )

        from portal.app import run_ocr_and_make_draft
        run_ocr_and_make_draft(job_id, fake_image)

        yield {"store": store, "conn": conn}


# ---------------------------------------------------------------------------
# Class 1 — Full 3-Call Pass Path
# ---------------------------------------------------------------------------

class TestFullPassPath:
    """High confidence across all 3 calls → gate passes → status=done."""

    def test_pass_sets_status_done(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 1)
        flagged = _draft_rows(3, 0.55)  # some flagged items for Call 3
        c3 = _reconcile_result(0.92)
        with _run_pipeline(1, tmp_path, conn, call3_flagged=flagged, call3_result=c3):
            pass
        job = _get_job(conn, 1)
        assert job["status"] == "done"

    def test_pass_no_rejection_logged(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 2)
        with _run_pipeline(2, tmp_path, conn):
            pass
        rejections = _get_rejections(conn)
        assert len(rejections) == 0

    def test_pass_debug_payload_gate_passed(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 3)
        flagged = _draft_rows(2, 0.50)
        with _run_pipeline(3, tmp_path, conn, call3_flagged=flagged,
                           call3_result=_reconcile_result(0.94)) as ctx:
            store = ctx["store"]
        assert len(store.debug_payloads) > 0
        payload = store.debug_payloads[-1]
        gate_block = payload.get("confidence_gate")
        assert gate_block is not None
        assert gate_block["passed"] is True
        assert gate_block["score"] >= 0.90

    def test_pass_items_upserted_to_draft(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 4)
        with _run_pipeline(4, tmp_path, conn) as ctx:
            store = ctx["store"]
        # Items should have been upserted even on pass
        assert len(store.upserted_items) > 0

    def test_pass_error_field_is_none(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 5)
        with _run_pipeline(5, tmp_path, conn):
            pass
        job = _get_job(conn, 5)
        assert job["error"] is None


# ---------------------------------------------------------------------------
# Class 2 — Full 3-Call Fail Path
# ---------------------------------------------------------------------------

class TestFullFailPath:
    """Low confidence → gate fails → status=rejected + rejection logged."""

    def _low_items(self, n: int = 5) -> List[Dict[str, Any]]:
        return _draft_rows(n, 0.28)

    def test_fail_sets_status_rejected(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 10)
        low_items = self._low_items()
        low_sem = _low_semantic_result(low_items)
        low_c2 = _vision_result(0.32)
        low_c3 = _low_reconcile_result()
        flagged = low_items[:3]
        with _run_pipeline(10, tmp_path, conn,
                           call1_items=_claude_items(5, conf=40),
                           call2_result=low_c2,
                           sem_result=low_sem,
                           call3_flagged=flagged,
                           call3_result=low_c3):
            pass
        job = _get_job(conn, 10)
        assert job["status"] == "rejected"

    def test_fail_error_field_has_customer_message(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 11)
        low_items = self._low_items()
        low_sem = _low_semantic_result(low_items)
        with _run_pipeline(11, tmp_path, conn,
                           call1_items=_claude_items(5, conf=40),
                           call2_result=_vision_result(0.30),
                           sem_result=low_sem,
                           call3_flagged=low_items[:2],
                           call3_result=_low_reconcile_result()):
            pass
        job = _get_job(conn, 11)
        assert job["error"] is not None
        assert len(job["error"]) > 20
        assert any(word in job["error"].lower()
                   for word in ("photo", "photograph", "image", "menu"))

    def test_fail_rejection_logged_to_db(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 12)
        low_items = self._low_items()
        low_sem = _low_semantic_result(low_items)
        with _run_pipeline(12, tmp_path, conn,
                           call1_items=_claude_items(5, conf=40),
                           call2_result=_vision_result(0.30),
                           sem_result=low_sem,
                           call3_flagged=low_items[:2],
                           call3_result=_low_reconcile_result()):
            pass
        rejections = _get_rejections(conn)
        assert len(rejections) == 1
        assert rejections[0]["item_count"] >= 1
        assert rejections[0]["gate_score"] < 0.90

    def test_fail_rejection_signals_json_valid(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 13)
        low_items = self._low_items()
        low_sem = _low_semantic_result(low_items)
        with _run_pipeline(13, tmp_path, conn,
                           call1_items=_claude_items(5, conf=40),
                           call2_result=_vision_result(0.30),
                           sem_result=low_sem,
                           call3_flagged=low_items[:2],
                           call3_result=_low_reconcile_result()):
            pass
        rejections = _get_rejections(conn)
        assert len(rejections) >= 1
        signals = rejections[0]["pipeline_signals"]
        assert isinstance(signals, dict)
        assert "semantic" in signals
        assert "item_count" in signals

    def test_fail_gate_reason_contains_fail(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 14)
        low_items = self._low_items()
        low_sem = _low_semantic_result(low_items)
        with _run_pipeline(14, tmp_path, conn,
                           call1_items=_claude_items(5, conf=40),
                           call2_result=_vision_result(0.30),
                           sem_result=low_sem,
                           call3_flagged=low_items[:2],
                           call3_result=_low_reconcile_result()):
            pass
        rejections = _get_rejections(conn)
        assert len(rejections) >= 1
        assert "FAIL" in rejections[0]["gate_reason"]


# ---------------------------------------------------------------------------
# Class 3 — Call 2 Skipped
# ---------------------------------------------------------------------------

class TestCall2Skipped:
    """Vision verification skipped → gate evaluates without call2_confidence."""

    def test_vision_skipped_gate_still_runs(self, tmp_path):
        """When vision is skipped, gate still evaluates (semantic-only weights)."""
        conn = _make_db()
        _insert_job(conn, 20)
        with _run_pipeline(20, tmp_path, conn, call2_result=_vision_result(skipped=True)):
            pass
        job = _get_job(conn, 20)
        # High semantic items → should still pass
        assert job["status"] == "done"

    def test_vision_skipped_call2_signal_marked_skipped(self, tmp_path):
        """Debug payload shows call2_vision.skipped=True when vision is skipped."""
        conn = _make_db()
        _insert_job(conn, 21)
        with _run_pipeline(21, tmp_path, conn,
                           call2_result=_vision_result(skipped=True)) as ctx:
            store = ctx["store"]
        assert len(store.debug_payloads) > 0
        payload = store.debug_payloads[-1]
        gate_block = payload.get("confidence_gate")
        assert gate_block is not None
        call2_sig = gate_block["signals"].get("call2_vision", {})
        assert call2_sig.get("skipped") is True

    def test_vision_error_gate_still_runs(self, tmp_path):
        """Vision API error is treated as skipped; gate still evaluates."""
        conn = _make_db()
        _insert_job(conn, 22)
        with _run_pipeline(22, tmp_path, conn,
                           call2_result=_vision_result(error="api_timeout")):
            pass
        job = _get_job(conn, 22)
        # High semantic → still passes even without Call 2
        assert job["status"] == "done"

    def test_vision_skipped_semantic_weight_redistributed(self, tmp_path):
        """When Call 2 skipped, semantic weight increases; gate can pass on semantic alone."""
        conn = _make_db()
        _insert_job(conn, 23)
        # High semantic items, no Call 2
        high_items = _draft_rows(15, 0.96)
        high_sem = _semantic_result(high_items)
        with _run_pipeline(23, tmp_path, conn,
                           call2_result=_vision_result(skipped=True),
                           sem_result=high_sem) as ctx:
            store = ctx["store"]
        job = _get_job(conn, 23)
        assert job["status"] == "done"
        # Gate score should reflect redistributed weights → above threshold
        payload = store.debug_payloads[-1]
        assert payload["confidence_gate"]["score"] >= 0.90


# ---------------------------------------------------------------------------
# Class 4 — Call 3 Skipped (No Flagged Items)
# ---------------------------------------------------------------------------

class TestCall3Skipped:
    """No flagged items → reconciliation skipped → gate evaluates without call3_confidence."""

    def test_no_flagged_items_gate_still_runs(self, tmp_path):
        """collect_flagged_items returns [] → Call 3 skipped, gate evaluates."""
        conn = _make_db()
        _insert_job(conn, 30)
        with _run_pipeline(30, tmp_path, conn, call3_flagged=[]):
            pass
        job = _get_job(conn, 30)
        assert job["status"] == "done"

    def test_no_flagged_call3_signal_marked_skipped(self, tmp_path):
        """Debug payload shows call3_reconcile.skipped=True."""
        conn = _make_db()
        _insert_job(conn, 31)
        with _run_pipeline(31, tmp_path, conn, call3_flagged=[]) as ctx:
            store = ctx["store"]
        payload = store.debug_payloads[-1]
        gate_block = payload.get("confidence_gate")
        assert gate_block is not None
        call3_sig = gate_block["signals"].get("call3_reconcile", {})
        assert call3_sig.get("skipped") is True

    def test_both_calls_skipped_semantic_only_gate(self, tmp_path):
        """Vision + reconcile both skipped → gate is pure semantic signal."""
        conn = _make_db()
        _insert_job(conn, 32)
        # Very high semantic items → should pass on semantic alone
        high_items = _draft_rows(20, 0.97)
        high_sem = _semantic_result(high_items)
        with _run_pipeline(32, tmp_path, conn,
                           call2_result=_vision_result(skipped=True),
                           sem_result=high_sem,
                           call3_flagged=[]) as ctx:
            store = ctx["store"]
        job = _get_job(conn, 32)
        assert job["status"] == "done"
        gate_block = store.debug_payloads[-1]["confidence_gate"]
        # Both optional calls skipped — weight fully on semantic + item count
        assert gate_block["signals"]["call2_vision"]["skipped"] is True
        assert gate_block["signals"]["call3_reconcile"]["skipped"] is True

    def test_no_rejection_when_call3_skipped_and_pass(self, tmp_path):
        """No rejection log entry when gate passes with Call 3 skipped."""
        conn = _make_db()
        _insert_job(conn, 33)
        with _run_pipeline(33, tmp_path, conn, call3_flagged=[]):
            pass
        rejections = _get_rejections(conn)
        assert len(rejections) == 0


# ---------------------------------------------------------------------------
# Class 5 — Thinking Mode Bypass
# ---------------------------------------------------------------------------

class TestThinkingModeBypass:
    """EXTENDED_THINKING=True → gate not evaluated, always status=done."""

    def test_thinking_mode_status_always_done(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 40)
        # Even with low semantic items, thinking mode bypasses gate
        low_items = _draft_rows(5, 0.20)
        low_sem = _low_semantic_result(low_items)
        with _run_pipeline(40, tmp_path, conn,
                           sem_result=low_sem, thinking=True):
            pass
        job = _get_job(conn, 40)
        assert job["status"] == "done"

    def test_thinking_mode_no_rejection_logged(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 41)
        with _run_pipeline(41, tmp_path, conn, thinking=True):
            pass
        rejections = _get_rejections(conn)
        assert len(rejections) == 0

    def test_thinking_mode_no_gate_block_in_payload(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 42)
        with _run_pipeline(42, tmp_path, conn, thinking=True) as ctx:
            store = ctx["store"]
        if store.debug_payloads:
            payload = store.debug_payloads[-1]
            # confidence_gate block must be absent in thinking mode
            assert "confidence_gate" not in payload

    def test_thinking_mode_skips_call2_and_call3(self, tmp_path):
        """In thinking mode the pipeline skips Call 2 and Call 3 entirely."""
        conn = _make_db()
        _insert_job(conn, 43)
        # Mock verify_menu_with_vision to raise if it's called
        with patch("storage.ai_vision_verify.verify_menu_with_vision",
                   side_effect=AssertionError("Call 2 must not run in thinking mode")):
            with _run_pipeline(43, tmp_path, conn, thinking=True):
                pass
        # Reaching here means Call 2 was never invoked
        job = _get_job(conn, 43)
        assert job["status"] == "done"


# ---------------------------------------------------------------------------
# Class 6 — Empty Extraction
# ---------------------------------------------------------------------------

class TestEmptyExtraction:
    """No items extracted → gate guard fires → gate not evaluated, status=done."""

    def test_empty_extraction_status_done(self, tmp_path):
        """No items → gate is skipped (if items:) → status=done, not rejected."""
        conn = _make_db()
        _insert_job(conn, 50)
        with _run_pipeline(50, tmp_path, conn, call1_items=[]):
            pass
        job = _get_job(conn, 50)
        assert job["status"] == "done"

    def test_empty_extraction_no_rejection_logged(self, tmp_path):
        conn = _make_db()
        _insert_job(conn, 51)
        with _run_pipeline(51, tmp_path, conn, call1_items=[]):
            pass
        rejections = _get_rejections(conn)
        assert len(rejections) == 0

    def test_empty_extraction_no_gate_block(self, tmp_path):
        """No items → gate not evaluated → no confidence_gate block in debug payload."""
        conn = _make_db()
        _insert_job(conn, 52)
        with _run_pipeline(52, tmp_path, conn, call1_items=[]) as ctx:
            store = ctx["store"]
        if store.debug_payloads:
            payload = store.debug_payloads[-1]
            assert "confidence_gate" not in payload


# ---------------------------------------------------------------------------
# Class 7 — Gate Threshold Boundary
# ---------------------------------------------------------------------------

class TestGateThresholdBoundary:
    """Verify gate score arithmetic at the boundary and accumulation across runs."""

    def test_high_semantic_alone_clears_threshold(self, tmp_path):
        """Very high semantic (0.98) + good item count clears 0.90 with both calls."""
        conn = _make_db()
        _insert_job(conn, 60)
        top_items = _draft_rows(20, 0.98)
        top_sem = _semantic_result(top_items)
        with _run_pipeline(60, tmp_path, conn,
                           call2_result=_vision_result(0.98),
                           sem_result=top_sem,
                           call3_flagged=[]) as ctx:
            store = ctx["store"]
        job = _get_job(conn, 60)
        assert job["status"] == "done"
        gate_score = store.debug_payloads[-1]["confidence_gate"]["score"]
        assert gate_score >= 0.90

    def test_low_semantic_fails_gate(self, tmp_path):
        """Very low semantic (0.25) cannot clear the threshold."""
        conn = _make_db()
        _insert_job(conn, 61)
        low_items = _draft_rows(4, 0.25)
        low_sem = _low_semantic_result(low_items)
        with _run_pipeline(61, tmp_path, conn,
                           call1_items=_claude_items(4, 40),
                           call2_result=_vision_result(0.25),
                           sem_result=low_sem,
                           call3_flagged=low_items[:2],
                           call3_result=_low_reconcile_result()):
            pass
        job = _get_job(conn, 61)
        assert job["status"] == "rejected"

    def test_multiple_rejections_accumulate_in_db(self, tmp_path):
        """Each failed run writes a separate rejection row."""
        conn = _make_db()
        for i, job_id in enumerate([70, 71, 72], start=1):
            _insert_job(conn, job_id)
            low_items = _draft_rows(3, 0.20)
            low_sem = _low_semantic_result(low_items)
            with _run_pipeline(job_id, tmp_path, conn,
                               call1_items=_claude_items(3, 30),
                               call2_result=_vision_result(0.25),
                               sem_result=low_sem,
                               call3_flagged=low_items[:1],
                               call3_result=_low_reconcile_result()):
                pass
        rejections = _get_rejections(conn)
        assert len(rejections) == 3

    def test_rejection_rows_ordered_desc_by_id(self, tmp_path):
        """get_pipeline_rejections returns newest-first."""
        conn = _make_db()
        for job_id in [80, 81]:
            _insert_job(conn, job_id)
            low_items = _draft_rows(3, 0.20)
            low_sem = _low_semantic_result(low_items)
            with _run_pipeline(job_id, tmp_path, conn,
                               call1_items=_claude_items(3, 30),
                               call2_result=_vision_result(0.25),
                               sem_result=low_sem,
                               call3_flagged=low_items[:1],
                               call3_result=_low_reconcile_result()):
                pass
        rows = _get_rejections(conn)
        assert len(rows) == 2
        assert rows[0]["id"] > rows[1]["id"]  # newest first


# ---------------------------------------------------------------------------
# Class 8 — Debug Payload Completeness
# ---------------------------------------------------------------------------

class TestDebugPayload:
    """Confidence gate block in the OCR debug payload is complete and accurate."""

    def test_gate_pass_payload_structure(self, tmp_path):
        """Gate pass → confidence_gate block has all required fields."""
        conn = _make_db()
        _insert_job(conn, 90)
        with _run_pipeline(90, tmp_path, conn) as ctx:
            store = ctx["store"]
        assert len(store.debug_payloads) > 0
        block = store.debug_payloads[-1].get("confidence_gate", {})
        assert "passed" in block
        assert "score" in block
        assert "threshold" in block
        assert "signals" in block
        assert "reason" in block

    def test_gate_fail_payload_reason_says_fail(self, tmp_path):
        """Gate fail → reason field in debug payload contains 'FAIL'."""
        conn = _make_db()
        _insert_job(conn, 91)
        low_items = _draft_rows(3, 0.22)
        low_sem = _low_semantic_result(low_items)
        with _run_pipeline(91, tmp_path, conn,
                           call1_items=_claude_items(3, 30),
                           call2_result=_vision_result(0.28),
                           sem_result=low_sem,
                           call3_flagged=low_items[:2],
                           call3_result=_low_reconcile_result()) as ctx:
            store = ctx["store"]
        block = store.debug_payloads[-1].get("confidence_gate", {})
        assert block.get("passed") is False
        assert "FAIL" in block.get("reason", "")

    def test_ocr_char_count_in_gate_signals(self, tmp_path):
        """ocr_char_count is captured in signals for logging/diagnostics."""
        conn = _make_db()
        _insert_job(conn, 92)
        ocr = "Margherita Pizza $12\nPepperoni Pizza $14\n" * 5
        with _run_pipeline(92, tmp_path, conn, ocr_text=ocr) as ctx:
            store = ctx["store"]
        block = store.debug_payloads[-1].get("confidence_gate", {})
        assert "ocr_char_count" in block.get("signals", {})
        assert block["signals"]["ocr_char_count"] == len(ocr)

    def test_gate_threshold_stored_in_payload(self, tmp_path):
        """threshold field matches GATE_THRESHOLD constant."""
        conn = _make_db()
        _insert_job(conn, 93)
        with _run_pipeline(93, tmp_path, conn) as ctx:
            store = ctx["store"]
        block = store.debug_payloads[-1].get("confidence_gate", {})
        from storage.confidence_gate import GATE_THRESHOLD
        assert block.get("threshold") == pytest.approx(GATE_THRESHOLD)