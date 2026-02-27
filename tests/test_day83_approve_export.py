"""
Day 83 -- "Approve & Export to POS" Button.

Sprint 9.4, Day 83: One-click approve & export workflow with validation modal,
new 'approved' draft status, and export history tracking.

Covers:
  Export History CRUD:
  - record_export() creates rows
  - get_export_history() returns newest-first
  - Multiple exports tracked
  - FK cascade: delete draft -> history deleted
  - Empty history returns []

  Approve Draft Status:
  - approve_draft() sets status to 'approved'
  - Approved draft blocks saves (403)
  - Re-approval is idempotent

  Approve & Export Endpoint:
  - Happy path: editing draft -> approve -> POS JSON + status change
  - Returns correct item/variant counts
  - Returns warnings list
  - Records export history automatically
  - Nonexistent draft -> 404
  - Already-approved draft -> re-export allowed
  - Empty draft -> valid response (0 items)
  - Draft with validation warnings -> still approves

  Export History Endpoint:
  - Returns history records newest-first
  - Empty history returns []
  - Multiple exports in order

  UI Integration via Flask Routes:
  - Approve endpoint returns downloadable JSON structure
  - Status changes persist across requests
  - Approved draft: save returns 403
  - Export history persists

  Edge Cases:
  - Draft with no items -> approval succeeds
  - Draft with all 5 variant kinds -> correct export
  - Very large draft (50+ items) -> approval works
  - Unicode item names in approved export
  - Export history with mixed formats
  - Re-export after approval records second history entry
"""

from __future__ import annotations

import json
import sqlite3
import pytest
from typing import Optional


# ---------------------------------------------------------------------------
# In-memory DB helpers (same pattern as Day 71-82 tests)
# ---------------------------------------------------------------------------
_TEST_CONN: Optional[sqlite3.Connection] = None


def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the required schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            restaurant_id INTEGER,
            status TEXT NOT NULL DEFAULT 'editing',
            source TEXT,
            source_job_id INTEGER,
            source_file_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id) ON DELETE SET NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            category TEXT,
            position INTEGER,
            confidence INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_item_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            price_cents INTEGER NOT NULL DEFAULT 0,
            kind TEXT DEFAULT 'size',
            position INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (item_id) REFERENCES draft_items(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draft_export_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            format TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            variant_count INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0,
            exported_at TEXT NOT NULL,
            FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            restaurant_id INTEGER,
            name TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL DEFAULT 0,
            category TEXT,
            position INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_draft ON draft_items(draft_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_variants_item ON draft_item_variants(item_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_export_history_draft ON draft_export_history(draft_id)")
    conn.commit()
    return conn


def _patch_db(monkeypatch):
    global _TEST_CONN
    _TEST_CONN = _make_test_db()
    import storage.drafts as drafts_mod
    def mock_connect():
        return _TEST_CONN
    monkeypatch.setattr(drafts_mod, "db_connect", mock_connect)
    return _TEST_CONN


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    conn = _patch_db(monkeypatch)
    yield conn
    global _TEST_CONN
    _TEST_CONN = None


def _create_draft(conn, title="Test Draft", status="editing") -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO drafts (title, status, created_at, updated_at) VALUES (?, ?, datetime('now'), datetime('now'))",
        (title, status),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_item(conn, draft_id, name, price_cents=0, category=None,
                 description=None, position=None) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_items (draft_id, name, description, price_cents, category, "
        "position, confidence, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 80, datetime('now'), datetime('now'))",
        (draft_id, name, description, price_cents, category, position),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_variant(conn, item_id, label, price_cents, kind="size",
                    position=0) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO draft_item_variants (item_id, label, price_cents, kind, "
        "position, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (item_id, label, price_cents, kind, position),
    )
    conn.commit()
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Import tested functions
# ---------------------------------------------------------------------------
from portal.app import (
    _validate_draft_for_export,
    _build_generic_pos_json,
)
import storage.drafts as drafts_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _item(id=1, name="Burger", price_cents=999, category="Entrees",
          description="", variants=None):
    return {
        "id": id, "name": name, "price_cents": price_cents,
        "category": category, "description": description,
        "position": None,
        "variants": variants or [],
    }


def _var(label="Small", price_cents=799, kind="size", position=0):
    return {"label": label, "price_cents": price_cents, "kind": kind,
            "position": position}


@pytest.fixture()
def client(fresh_db):
    """Flask test client with mocked DB and fake session login."""
    from portal.app import app
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = {"id": 1, "email": "test@test.com"}
        yield c


def _build_realistic_draft(conn):
    """Create a draft with 3 categories, mix of variant/no-variant items."""
    did = _create_draft(conn, title="Full Menu")

    b1 = _insert_item(conn, did, "Classic Burger", 999, "Burgers",
                      "Beef patty with lettuce and tomato")
    _insert_variant(conn, b1, "Single", 999, "size", 0)
    _insert_variant(conn, b1, "Double", 1399, "size", 1)

    b2 = _insert_item(conn, did, "Cheese Burger", 1099, "Burgers",
                      "With American cheese")
    _insert_variant(conn, b2, "Single", 1099, "size", 0)
    _insert_variant(conn, b2, "Double", 1499, "size", 1)
    _insert_variant(conn, b2, "w/ Fries", 200, "combo", 2)

    d1 = _insert_item(conn, did, "Soda", 250, "Drinks", "Coke, Sprite, Fanta")
    _insert_variant(conn, d1, "Small", 250, "size", 0)
    _insert_variant(conn, d1, "Medium", 350, "size", 1)
    _insert_variant(conn, d1, "Large", 450, "size", 2)

    _insert_item(conn, did, "Water", 150, "Drinks")
    _insert_item(conn, did, "French Fries", 499, "Sides", "Crispy golden fries")

    return did


# ===========================================================================
# SECTION 1: Export History CRUD
# ===========================================================================

class TestExportHistoryCRUD:
    """Test record_export(), get_export_history(), FK cascade."""

    def test_record_export_creates_row(self, fresh_db):
        did = _create_draft(fresh_db)
        row_id = drafts_store.record_export(did, "generic_pos", 10, 5, 2)
        assert row_id > 0
        history = drafts_store.get_export_history(did)
        assert len(history) == 1
        assert history[0]["format"] == "generic_pos"
        assert history[0]["item_count"] == 10
        assert history[0]["variant_count"] == 5
        assert history[0]["warning_count"] == 2

    def test_get_export_history_newest_first(self, fresh_db):
        did = _create_draft(fresh_db)
        drafts_store.record_export(did, "generic_pos", 5, 2, 0)
        drafts_store.record_export(did, "square", 5, 2, 1)
        drafts_store.record_export(did, "toast", 5, 2, 0)
        history = drafts_store.get_export_history(did)
        assert len(history) == 3
        # Newest first (toast was last inserted)
        assert history[0]["format"] == "toast"
        assert history[2]["format"] == "generic_pos"

    def test_multiple_exports_all_recorded(self, fresh_db):
        did = _create_draft(fresh_db)
        for i in range(5):
            drafts_store.record_export(did, f"format_{i}", i, 0, 0)
        history = drafts_store.get_export_history(did)
        assert len(history) == 5

    def test_fk_cascade_delete_draft_deletes_history(self, fresh_db):
        did = _create_draft(fresh_db)
        drafts_store.record_export(did, "generic_pos", 3, 1, 0)
        drafts_store.record_export(did, "square", 3, 1, 0)
        assert len(drafts_store.get_export_history(did)) == 2
        # Delete the draft
        fresh_db.execute("DELETE FROM drafts WHERE id=?", (did,))
        fresh_db.commit()
        # History should be gone (FK CASCADE)
        rows = fresh_db.execute(
            "SELECT * FROM draft_export_history WHERE draft_id=?", (did,)
        ).fetchall()
        assert len(rows) == 0

    def test_empty_history_returns_empty_list(self, fresh_db):
        did = _create_draft(fresh_db)
        history = drafts_store.get_export_history(did)
        assert history == []

    def test_record_export_returns_id(self, fresh_db):
        did = _create_draft(fresh_db)
        id1 = drafts_store.record_export(did, "generic_pos", 1, 0, 0)
        id2 = drafts_store.record_export(did, "square", 2, 0, 0)
        assert id2 > id1

    def test_history_has_exported_at_timestamp(self, fresh_db):
        did = _create_draft(fresh_db)
        drafts_store.record_export(did, "generic_pos", 5, 2, 0)
        history = drafts_store.get_export_history(did)
        assert history[0]["exported_at"] is not None
        assert len(history[0]["exported_at"]) > 0

    def test_history_isolates_by_draft(self, fresh_db):
        d1 = _create_draft(fresh_db, "Draft A")
        d2 = _create_draft(fresh_db, "Draft B")
        drafts_store.record_export(d1, "generic_pos", 3, 1, 0)
        drafts_store.record_export(d2, "square", 5, 2, 1)
        assert len(drafts_store.get_export_history(d1)) == 1
        assert len(drafts_store.get_export_history(d2)) == 1
        assert drafts_store.get_export_history(d1)[0]["format"] == "generic_pos"
        assert drafts_store.get_export_history(d2)[0]["format"] == "square"

    def test_record_export_zero_counts(self, fresh_db):
        did = _create_draft(fresh_db)
        drafts_store.record_export(did, "generic_pos", 0, 0, 0)
        history = drafts_store.get_export_history(did)
        assert history[0]["item_count"] == 0
        assert history[0]["variant_count"] == 0
        assert history[0]["warning_count"] == 0


# ===========================================================================
# SECTION 2: Approve Draft Status
# ===========================================================================

class TestApproveDraftStatus:
    """Test approve_draft() and status behavior."""

    def test_approve_draft_sets_status(self, fresh_db):
        did = _create_draft(fresh_db)
        drafts_store.approve_draft(did)
        draft = drafts_store.get_draft(did)
        assert draft["status"] == "approved"

    def test_approve_draft_idempotent(self, fresh_db):
        did = _create_draft(fresh_db)
        drafts_store.approve_draft(did)
        drafts_store.approve_draft(did)  # second call
        draft = drafts_store.get_draft(did)
        assert draft["status"] == "approved"

    def test_approved_draft_save_blocked(self, client, fresh_db):
        did = _create_draft(fresh_db, status="approved")
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.post(f"/drafts/{did}/save",
                           json={"title": "Updated", "items": []})
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["ok"] is False
        assert "approved" in data["error"].lower()

    def test_published_draft_save_blocked(self, client, fresh_db):
        did = _create_draft(fresh_db, status="published")
        resp = client.post(f"/drafts/{did}/save",
                           json={"title": "Updated", "items": []})
        assert resp.status_code == 403

    def test_editing_draft_save_allowed(self, client, fresh_db):
        did = _create_draft(fresh_db, status="editing")
        _insert_item(fresh_db, did, "Burger", 999, "Entrees")
        resp = client.post(f"/drafts/{did}/save",
                           json={"title": "Updated", "items": []})
        assert resp.status_code == 200

    def test_approve_from_finalized_status(self, fresh_db):
        did = _create_draft(fresh_db, status="finalized")
        drafts_store.approve_draft(did)
        draft = drafts_store.get_draft(did)
        assert draft["status"] == "approved"

    def test_approve_updates_timestamp(self, fresh_db):
        did = _create_draft(fresh_db)
        draft_before = drafts_store.get_draft(did)
        drafts_store.approve_draft(did)
        draft_after = drafts_store.get_draft(did)
        assert draft_after["updated_at"] >= draft_before["updated_at"]

    def test_autosave_ping_still_works_on_approved(self, client, fresh_db):
        """Autosave ping should still succeed (it's a no-op check)."""
        did = _create_draft(fresh_db, status="approved")
        resp = client.post(f"/drafts/{did}/save",
                           json={"autosave_ping": True})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data.get("ping") is True


# ===========================================================================
# SECTION 3: Approve & Export Endpoint
# ===========================================================================

class TestApproveExportEndpoint:
    """Test POST /drafts/<id>/approve_export."""

    def test_happy_path_editing_draft(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.post(f"/drafts/{did}/approve_export")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["item_count"] == 5
        assert data["variant_count"] == 8
        assert "pos_json" in data
        assert "menu" in data["pos_json"]
        assert "approved_at" in data

    def test_sets_status_to_approved(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        draft = drafts_store.get_draft(did)
        assert draft["status"] == "approved"

    def test_records_export_history(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        history = drafts_store.get_export_history(did)
        assert len(history) == 1
        assert history[0]["format"] == "generic_pos"
        assert history[0]["item_count"] == 5
        assert history[0]["variant_count"] == 8

    def test_returns_warnings(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "No Price Item", 0, None)  # missing category + price
        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["warning_count"] > 0
        assert len(data["warnings"]) > 0

    def test_nonexistent_draft_404(self, client, fresh_db):
        resp = client.post("/drafts/9999/approve_export")
        assert resp.status_code == 404

    def test_already_approved_re_export_allowed(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        # First approval
        resp1 = client.post(f"/drafts/{did}/approve_export")
        assert resp1.status_code == 200
        # Second approval (re-export)
        resp2 = client.post(f"/drafts/{did}/approve_export")
        assert resp2.status_code == 200
        data2 = resp2.get_json()
        assert data2["ok"] is True
        # Should have 2 history entries
        history = drafts_store.get_export_history(did)
        assert len(history) == 2

    def test_empty_draft_valid_response(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.post(f"/drafts/{did}/approve_export")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["item_count"] == 0
        assert data["variant_count"] == 0

    def test_pos_json_structure(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        pos = data["pos_json"]
        assert "menu" in pos
        assert "metadata" in pos
        assert "categories" in pos["menu"]
        cats = pos["menu"]["categories"]
        cat_names = [c["name"] for c in cats]
        assert "Burgers" in cat_names
        assert "Drinks" in cat_names
        assert "Sides" in cat_names

    def test_pos_json_has_modifiers(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        cats = data["pos_json"]["menu"]["categories"]
        burgers = next(c for c in cats if c["name"] == "Burgers")
        # Classic Burger has 2 size variants
        classic = next(i for i in burgers["items"] if i["name"] == "Classic Burger")
        assert len(classic["modifiers"]) == 2

    def test_warnings_with_variants_missing_price(self, client, fresh_db):
        did = _create_draft(fresh_db)
        item_id = _insert_item(fresh_db, did, "Pizza", 999, "Entrees")
        _insert_variant(fresh_db, item_id, "Small", 0, "size", 0)  # 0 price
        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        assert data["ok"] is True
        # Should have variant_missing_price warning
        warning_types = [w["type"] for w in data["warnings"]]
        assert "variant_missing_price" in warning_types

    def test_approve_export_variant_count_correct(self, client, fresh_db):
        did = _create_draft(fresh_db)
        i1 = _insert_item(fresh_db, did, "A", 100, "Cat")
        _insert_variant(fresh_db, i1, "S", 100, "size", 0)
        _insert_variant(fresh_db, i1, "M", 200, "size", 1)
        _insert_variant(fresh_db, i1, "L", 300, "size", 2)
        i2 = _insert_item(fresh_db, did, "B", 200, "Cat")
        # No variants
        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        assert data["item_count"] == 2
        assert data["variant_count"] == 3

    def test_approve_export_metadata(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        meta = data["pos_json"]["metadata"]
        assert meta["format"] == "generic_pos"
        assert meta["item_count"] == 5
        assert "exported_at" in meta


# ===========================================================================
# SECTION 4: Export History Endpoint
# ===========================================================================

class TestExportHistoryEndpoint:
    """Test GET /drafts/<id>/export_history."""

    def test_empty_history(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.get(f"/drafts/{did}/export_history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["history"] == []

    def test_history_after_approve(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        resp = client.get(f"/drafts/{did}/export_history")
        data = resp.get_json()
        assert len(data["history"]) == 1
        assert data["history"][0]["format"] == "generic_pos"

    def test_multiple_history_entries_newest_first(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        client.post(f"/drafts/{did}/approve_export")
        resp = client.get(f"/drafts/{did}/export_history")
        data = resp.get_json()
        assert len(data["history"]) == 2
        # Newest first
        assert data["history"][0]["exported_at"] >= data["history"][1]["exported_at"]

    def test_nonexistent_draft_404(self, client, fresh_db):
        resp = client.get("/drafts/9999/export_history")
        assert resp.status_code == 404

    def test_history_has_correct_counts(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        resp = client.get(f"/drafts/{did}/export_history")
        data = resp.get_json()
        entry = data["history"][0]
        assert entry["item_count"] == 5
        assert entry["variant_count"] == 8


# ===========================================================================
# SECTION 5: UI Integration via Flask Routes
# ===========================================================================

class TestUIIntegration:
    """Test approve/export flow through Flask routes end-to-end."""

    def test_approve_then_save_blocked(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        # Approve
        resp = client.post(f"/drafts/{did}/approve_export")
        assert resp.status_code == 200
        # Try to save — should be blocked
        resp2 = client.post(f"/drafts/{did}/save",
                            json={"title": "Changed", "items": []})
        assert resp2.status_code == 403

    def test_status_persists_after_approve(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        # Check status endpoint
        resp = client.get(f"/drafts/{did}/status")
        data = resp.get_json()
        assert data["status"] == "approved"

    def test_export_history_persists_after_approve(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        # History via endpoint
        resp = client.get(f"/drafts/{did}/export_history")
        data = resp.get_json()
        assert len(data["history"]) == 1

    def test_approve_response_has_pos_json_for_download(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        # pos_json should be a valid POS JSON structure for client-side download
        pos = data["pos_json"]
        raw = json.dumps(pos, indent=2)
        parsed = json.loads(raw)
        assert "menu" in parsed
        assert "metadata" in parsed

    def test_existing_exports_still_work_after_approve(self, client, fresh_db):
        """Read-only export endpoints should still work on approved drafts."""
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        # JSON export should still work
        resp = client.get(f"/drafts/{did}/export.json")
        assert resp.status_code == 200
        # POS JSON export should still work
        resp2 = client.get(f"/drafts/{did}/export_pos.json")
        assert resp2.status_code == 200

    def test_validate_endpoint_works_on_approved(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        resp = client.get(f"/drafts/{did}/export/validate")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["item_count"] == 5

    def test_metrics_endpoint_works_on_approved(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        resp = client.get(f"/drafts/{did}/export/metrics")
        assert resp.status_code == 200

    def test_backfill_blocked_on_approved(self, client, fresh_db):
        """Backfill requires editing status."""
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        resp = client.post(f"/drafts/{did}/backfill_variants")
        assert resp.status_code == 400


# ===========================================================================
# SECTION 6: Edge Cases
# ===========================================================================

class TestEdgeCases:
    """Edge case testing for approve & export."""

    def test_empty_draft_approves(self, client, fresh_db):
        did = _create_draft(fresh_db)
        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["item_count"] == 0
        pos = data["pos_json"]
        assert pos["metadata"]["item_count"] == 0

    def test_all_five_variant_kinds(self, client, fresh_db):
        did = _create_draft(fresh_db)
        item_id = _insert_item(fresh_db, did, "Supreme Pizza", 1499, "Pizza",
                               "All toppings")
        _insert_variant(fresh_db, item_id, "Small", 1499, "size", 0)
        _insert_variant(fresh_db, item_id, "Large", 1999, "size", 1)
        _insert_variant(fresh_db, item_id, "w/ Salad", 300, "combo", 2)
        _insert_variant(fresh_db, item_id, "Spicy", 0, "flavor", 3)
        _insert_variant(fresh_db, item_id, "Deep Dish", 200, "style", 4)
        _insert_variant(fresh_db, item_id, "Gluten Free", 300, "other", 5)

        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["variant_count"] == 6
        # Check modifiers have all kinds
        pos = data["pos_json"]
        pizza_cat = next(c for c in pos["menu"]["categories"] if c["name"] == "Pizza")
        modifiers = pizza_cat["items"][0]["modifiers"]
        groups = {m["group"] for m in modifiers}
        assert "Size" in groups
        assert "Combo Add-on" in groups
        assert "Flavor" in groups
        assert "Style" in groups
        assert "Option" in groups

    def test_large_draft_50_items(self, client, fresh_db):
        did = _create_draft(fresh_db)
        for i in range(55):
            item_id = _insert_item(fresh_db, did, f"Item {i}", 100 * (i + 1),
                                   f"Cat{i % 5}")
            if i % 3 == 0:
                _insert_variant(fresh_db, item_id, "Small", 100 * (i + 1), "size", 0)
                _insert_variant(fresh_db, item_id, "Large", 200 * (i + 1), "size", 1)

        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["item_count"] == 55

    def test_unicode_item_names(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Creme Brulee", 899, "Desserts")
        _insert_item(fresh_db, did, "Pad Thai", 1299, "Thai")
        _insert_item(fresh_db, did, "Tonkotsu Ramen", 1499, "Japanese")

        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        assert data["ok"] is True
        raw = json.dumps(data["pos_json"])
        assert "Creme Brulee" in raw
        assert "Pad Thai" in raw

    def test_export_history_mixed_formats(self, fresh_db):
        did = _create_draft(fresh_db)
        drafts_store.record_export(did, "generic_pos", 5, 2, 0)
        drafts_store.record_export(did, "square", 5, 2, 1)
        drafts_store.record_export(did, "toast", 5, 2, 0)
        history = drafts_store.get_export_history(did)
        formats = [h["format"] for h in history]
        assert "generic_pos" in formats
        assert "square" in formats
        assert "toast" in formats

    def test_re_export_after_approval_records_second_entry(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        client.post(f"/drafts/{did}/approve_export")
        history = drafts_store.get_export_history(did)
        assert len(history) == 2
        assert all(h["format"] == "generic_pos" for h in history)

    def test_draft_with_only_variants_no_base_price(self, client, fresh_db):
        """Item with 0 base price but variant prices."""
        did = _create_draft(fresh_db)
        item_id = _insert_item(fresh_db, did, "Pizza", 0, "Pizza")
        _insert_variant(fresh_db, item_id, "Small", 999, "size", 0)
        _insert_variant(fresh_db, item_id, "Large", 1499, "size", 1)
        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["variant_count"] == 2

    def test_description_with_special_chars(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "Burger", 999, "Entrees",
                     'With "special" sauce & extra <toppings>')
        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        assert data["ok"] is True
        raw = json.dumps(data["pos_json"])
        assert "special" in raw

    def test_approve_export_warning_count_matches_warnings_length(self, client, fresh_db):
        did = _create_draft(fresh_db)
        _insert_item(fresh_db, did, "No Price", 0, None)
        _insert_item(fresh_db, did, "", 999, "Cat")  # missing name
        resp = client.post(f"/drafts/{did}/approve_export")
        data = resp.get_json()
        assert data["warning_count"] == len(data["warnings"])

    def test_history_draft_id_matches(self, client, fresh_db):
        did = _build_realistic_draft(fresh_db)
        client.post(f"/drafts/{did}/approve_export")
        resp = client.get(f"/drafts/{did}/export_history")
        data = resp.get_json()
        assert data["draft_id"] == did
        assert data["history"][0]["draft_id"] == did
