# tests/test_day97_vision_pipeline.py
"""
Day 97 — Vision Verification Pipeline Integration (Sprint 11.1 continued)
Tests for wiring ai_vision_verify into the extraction pipeline + page batching.

Covers:
  1. Pipeline integration: verify_menu_with_vision called after Claude extraction
  2. Graceful fallback: Call 1 items used when vision fails/skips
  3. Extraction strategy tracking: "claude_api+vision" vs "claude_api"
  4. Vision metadata saved in debug payload
  5. Page batching: large PDFs capped at _MAX_PAGES_PER_CALL
  6. Page warning for menus with many pages
  7. encode_menu_images with max_pages parameter
  8. pages_sent field in verification result
  9. End-to-end: items flow through Call 1 → Call 2 → draft rows
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_claude_items():
    """Items as returned by extract_menu_items_via_claude (Call 1)."""
    return [
        {"name": "Pepperoni Pizza", "description": "Classic pepperoni", "price": 14.95, "category": "Pizza", "sizes": []},
        {"name": "Garden Salad", "description": "Mixed greens", "price": 8.95, "category": "Salads", "sizes": []},
        {"name": "Chicken Wings", "description": "Buffalo style", "price": 11.50, "category": "Appetizers", "sizes": []},
    ]


@pytest.fixture
def vision_corrected_items():
    """Items after vision verification — some corrections applied."""
    return [
        {"name": "Pepperoni Pizza", "description": "Classic pepperoni", "price": 14.95, "category": "Pizza", "sizes": []},
        {"name": "Garden Salad", "description": "Mixed greens, tomatoes, cucumbers", "price": 9.95, "category": "Salads", "sizes": []},
        {"name": "Chicken Wings", "description": "Buffalo style", "price": 11.50, "category": "Appetizers", "sizes": []},
        {"name": "Mozzarella Sticks", "description": "With marinara", "price": 7.95, "category": "Appetizers", "sizes": []},
    ]


@pytest.fixture
def tmp_menu_image(tmp_path):
    """Create a tiny PNG file to simulate a menu image."""
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    img = tmp_path / "test_menu.png"
    img.write_bytes(png_bytes)
    return img


# ===========================================================================
# 1. Page batching tests
# ===========================================================================
class TestPageBatching:
    def test_max_pages_constant_exists(self):
        from storage.ai_vision_verify import _MAX_PAGES_PER_CALL
        assert _MAX_PAGES_PER_CALL > 0
        assert _MAX_PAGES_PER_CALL == 20

    def test_warn_pages_constant_exists(self):
        from storage.ai_vision_verify import _WARN_PAGES
        assert _WARN_PAGES > 0
        assert _WARN_PAGES == 8

    def test_encode_menu_images_caps_pages(self, tmp_path):
        """PDFs with more pages than max_pages are capped."""
        from storage.ai_vision_verify import encode_menu_images

        pdf = tmp_path / "big_menu.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        # Simulate a 25-page PDF
        fake_pages = [
            {"media_type": "image/png", "data": base64.b64encode(b"page").decode()}
            for _ in range(25)
        ]

        import storage.ai_vision_verify as mod
        original_fn = mod._pdf_to_images

        def mock_pdf_to_images(path, dpi=200):
            return list(fake_pages)

        mod._pdf_to_images = mock_pdf_to_images
        try:
            result = encode_menu_images(str(pdf), max_pages=20)
            assert len(result) == 20  # Capped at max_pages
        finally:
            mod._pdf_to_images = original_fn

    def test_encode_menu_images_custom_max_pages(self, tmp_path):
        """Custom max_pages parameter is respected."""
        from storage.ai_vision_verify import encode_menu_images

        pdf = tmp_path / "menu.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        fake_pages = [
            {"media_type": "image/png", "data": base64.b64encode(b"page").decode()}
            for _ in range(10)
        ]

        import storage.ai_vision_verify as mod
        original_fn = mod._pdf_to_images
        mod._pdf_to_images = lambda path, dpi=200: list(fake_pages)
        try:
            result = encode_menu_images(str(pdf), max_pages=5)
            assert len(result) == 5
        finally:
            mod._pdf_to_images = original_fn

    def test_encode_menu_images_under_limit_no_cap(self, tmp_path):
        """PDFs within page limit are not capped."""
        from storage.ai_vision_verify import encode_menu_images

        pdf = tmp_path / "menu.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        fake_pages = [
            {"media_type": "image/png", "data": base64.b64encode(b"page").decode()}
            for _ in range(3)
        ]

        import storage.ai_vision_verify as mod
        original_fn = mod._pdf_to_images
        mod._pdf_to_images = lambda path, dpi=200: list(fake_pages)
        try:
            result = encode_menu_images(str(pdf))
            assert len(result) == 3  # All pages included
        finally:
            mod._pdf_to_images = original_fn

    def test_single_image_unaffected_by_page_cap(self, tmp_menu_image):
        """Single images are not affected by page batching."""
        from storage.ai_vision_verify import encode_menu_images
        result = encode_menu_images(str(tmp_menu_image), max_pages=1)
        assert len(result) == 1
        assert result[0]["media_type"] == "image/png"


# ===========================================================================
# 2. pages_sent in verification result
# ===========================================================================
class TestPagesSentField:
    def test_pages_sent_single_image(self, tmp_menu_image, sample_claude_items):
        """Successful verification includes pages_sent count."""
        from storage.ai_vision_verify import verify_menu_with_vision

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text=json.dumps({
            "items": sample_claude_items,
            "confidence": 0.95,
            "notes": "No changes needed",
        }))
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_image), sample_claude_items)

        assert result["pages_sent"] == 1

    def test_pages_sent_multi_page(self, tmp_menu_image, sample_claude_items):
        """Multi-page verification reports correct page count."""
        from storage.ai_vision_verify import verify_menu_with_vision

        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        four_pages = [
            {"media_type": "image/png", "data": base64.b64encode(png_bytes).decode()}
            for _ in range(4)
        ]

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text=json.dumps({
            "items": sample_claude_items,
            "confidence": 0.93,
            "notes": "",
        }))
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client), \
             patch("storage.ai_vision_verify.encode_menu_images", return_value=four_pages):
            result = verify_menu_with_vision(str(tmp_menu_image), sample_claude_items)

        assert result["pages_sent"] == 4


# ===========================================================================
# 3. Pipeline integration — Call 1 → Call 2 flow
# ===========================================================================
class TestPipelineIntegration:
    """Test that vision verification is wired into the extraction pipeline correctly."""

    def test_verified_items_to_draft_rows_confidence_boost(self, vision_corrected_items):
        """Vision-verified items get confidence=95 (higher than Call 1's 90)."""
        from storage.ai_vision_verify import verified_items_to_draft_rows
        rows = verified_items_to_draft_rows(vision_corrected_items)
        assert len(rows) == 4
        for row in rows:
            assert row["confidence"] == 95  # Vision-verified boost

    def test_call1_items_have_confidence_90(self, sample_claude_items):
        """Call 1 items (without vision) get confidence=90."""
        from storage.ai_menu_extract import claude_items_to_draft_rows
        rows = claude_items_to_draft_rows(sample_claude_items)
        for row in rows:
            assert row["confidence"] == 90

    def test_vision_adds_missing_items(self, sample_claude_items, vision_corrected_items):
        """Vision verification can discover items missed by OCR extraction."""
        from storage.ai_vision_verify import compute_changes_log
        changes = compute_changes_log(sample_claude_items, vision_corrected_items)
        added = [c for c in changes if c["type"] == "item_added"]
        assert len(added) == 1
        assert "Mozzarella Sticks" in added[0]["detail"]

    def test_vision_fixes_prices(self, sample_claude_items, vision_corrected_items):
        """Vision verification can correct incorrect prices."""
        from storage.ai_vision_verify import compute_changes_log
        changes = compute_changes_log(sample_claude_items, vision_corrected_items)
        price_fixes = [c for c in changes if c["type"] == "price_fixed"]
        assert len(price_fixes) == 1
        assert "$8.95" in price_fixes[0]["detail"]
        assert "$9.95" in price_fixes[0]["detail"]

    def test_vision_fixes_descriptions(self, sample_claude_items, vision_corrected_items):
        """Vision verification can correct descriptions."""
        from storage.ai_vision_verify import compute_changes_log
        changes = compute_changes_log(sample_claude_items, vision_corrected_items)
        desc_fixes = [c for c in changes if c["type"] == "description_fixed"]
        assert len(desc_fixes) == 1

    def test_end_to_end_call1_to_call2_to_draft_rows(
        self, tmp_menu_image, sample_claude_items, vision_corrected_items
    ):
        """Full flow: Claude extraction → vision verification → draft rows."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows

        # Mock successful vision verification
        mock_response = MagicMock()
        mock_block = SimpleNamespace(text=json.dumps({
            "items": vision_corrected_items,
            "confidence": 0.94,
            "notes": "Fixed salad price, added Mozzarella Sticks",
        }))
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_image), sample_claude_items)

        assert not result.get("skipped")
        assert not result.get("error")
        assert result["confidence"] == 0.94
        assert len(result["items"]) == 4  # 3 original + 1 added

        # Convert to draft rows
        rows = verified_items_to_draft_rows(result["items"])
        assert len(rows) == 4
        names = [r["name"] for r in rows]
        assert "Mozzarella Sticks" in names  # New item added
        assert all(r["confidence"] == 95 for r in rows)

        # Check price was corrected
        salad = next(r for r in rows if r["name"] == "Garden Salad")
        assert salad["price_cents"] == 995  # Fixed from 895


# ===========================================================================
# 4. Graceful fallback tests
# ===========================================================================
class TestGracefulFallback:
    def test_vision_skip_no_api_key_uses_call1(self, tmp_menu_image, sample_claude_items):
        """When vision is skipped (no API key), Call 1 items are used."""
        from storage.ai_vision_verify import verify_menu_with_vision
        from storage.ai_menu_extract import claude_items_to_draft_rows

        with patch("storage.ai_vision_verify._get_client", return_value=None):
            result = verify_menu_with_vision(str(tmp_menu_image), sample_claude_items)

        assert result["skipped"] is True
        assert result["skip_reason"] == "no_api_key"

        # Caller should fall back to Call 1 items
        rows = claude_items_to_draft_rows(sample_claude_items)
        assert len(rows) == 3
        assert all(r["confidence"] == 90 for r in rows)

    def test_vision_error_uses_call1(self, tmp_menu_image, sample_claude_items):
        """When vision API errors, Call 1 items are returned."""
        from storage.ai_vision_verify import verify_menu_with_vision

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API timeout")

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_image), sample_claude_items)

        assert result["error"] == "API timeout"
        assert result["items"] == sample_claude_items  # Original items returned

    def test_vision_bad_image_uses_call1(self, sample_claude_items):
        """When image encoding fails, Call 1 items are returned."""
        from storage.ai_vision_verify import verify_menu_with_vision

        mock_client = MagicMock()
        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision("/nonexistent/menu.png", sample_claude_items)

        assert result["skipped"] is True
        assert result["skip_reason"] == "image_encode_failed"
        assert result["items"] == sample_claude_items

    def test_vision_parse_failure_uses_call1(self, tmp_menu_image, sample_claude_items):
        """When Claude returns unparseable response, Call 1 items are used."""
        from storage.ai_vision_verify import verify_menu_with_vision

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text="I see a menu with many items...")
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_image), sample_claude_items)

        assert result["error"] == "parse_failed"
        assert result["items"] == sample_claude_items


# ===========================================================================
# 5. Vision metadata in debug payload
# ===========================================================================
class TestVisionMetadata:
    def test_metadata_structure_on_success(self, vision_corrected_items):
        """Vision result contains all fields needed for debug payload."""
        # Simulate what portal/app.py stores
        vision_result = {
            "skipped": False,
            "skip_reason": None,
            "error": None,
            "confidence": 0.94,
            "model": "claude-sonnet-4-5-20250929",
            "changes": [
                {"type": "price_fixed", "detail": "Price for 'Garden Salad': $8.95 → $9.95"},
                {"type": "item_added", "detail": "Added 'Mozzarella Sticks'"},
            ],
            "notes": "Fixed salad price, added Mozzarella Sticks",
            "items": vision_corrected_items,
            "pages_sent": 1,
        }

        # Build the payload as portal/app.py does
        payload = {}
        payload["vision_verification"] = {
            "skipped": vision_result.get("skipped", False),
            "skip_reason": vision_result.get("skip_reason"),
            "error": vision_result.get("error"),
            "confidence": vision_result.get("confidence", 0.0),
            "model": vision_result.get("model"),
            "changes_count": len(vision_result.get("changes", [])),
            "changes": vision_result.get("changes", []),
            "notes": vision_result.get("notes"),
            "item_count_before": len(vision_result.get("items", [])),
        }

        vv = payload["vision_verification"]
        assert vv["skipped"] is False
        assert vv["confidence"] == 0.94
        assert vv["changes_count"] == 2
        assert vv["model"] == "claude-sonnet-4-5-20250929"
        assert len(vv["changes"]) == 2
        assert vv["item_count_before"] == 4

    def test_metadata_on_skip(self):
        """Skipped vision result stored correctly."""
        vision_result = {
            "skipped": True,
            "skip_reason": "no_api_key",
            "confidence": 0.0,
            "model": "claude-sonnet-4-5-20250929",
            "changes": [],
            "items": [],
        }

        payload = {}
        payload["vision_verification"] = {
            "skipped": vision_result.get("skipped", False),
            "skip_reason": vision_result.get("skip_reason"),
            "error": vision_result.get("error"),
            "confidence": vision_result.get("confidence", 0.0),
            "model": vision_result.get("model"),
            "changes_count": len(vision_result.get("changes", [])),
            "changes": vision_result.get("changes", []),
            "notes": vision_result.get("notes"),
            "item_count_before": len(vision_result.get("items", [])),
        }

        vv = payload["vision_verification"]
        assert vv["skipped"] is True
        assert vv["skip_reason"] == "no_api_key"
        assert vv["changes_count"] == 0

    def test_metadata_on_error(self):
        """Error vision result stored correctly."""
        vision_result = {
            "skipped": False,
            "error": "API timeout",
            "confidence": 0.0,
            "model": "claude-sonnet-4-5-20250929",
            "changes": [],
            "items": [{"name": "Test", "price": 5.0}],
        }

        payload = {}
        payload["vision_verification"] = {
            "skipped": vision_result.get("skipped", False),
            "skip_reason": vision_result.get("skip_reason"),
            "error": vision_result.get("error"),
            "confidence": vision_result.get("confidence", 0.0),
            "model": vision_result.get("model"),
            "changes_count": len(vision_result.get("changes", [])),
            "changes": vision_result.get("changes", []),
            "notes": vision_result.get("notes"),
            "item_count_before": len(vision_result.get("items", [])),
        }

        vv = payload["vision_verification"]
        assert vv["skipped"] is False
        assert vv["error"] == "API timeout"


# ===========================================================================
# 6. Extraction strategy tracking
# ===========================================================================
class TestExtractionStrategy:
    def test_vision_success_strategy_is_claude_api_plus_vision(
        self, tmp_menu_image, sample_claude_items, vision_corrected_items
    ):
        """When vision succeeds, strategy should be 'claude_api+vision'."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text=json.dumps({
            "items": vision_corrected_items,
            "confidence": 0.92,
            "notes": "Corrections applied",
        }))
        mock_response.content = [mock_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_image), sample_claude_items)

        # Simulate pipeline logic from portal/app.py
        extraction_strategy = "claude_api"  # Set by Call 1
        vision_result = result

        if not vision_result.get("skipped") and not vision_result.get("error"):
            items = verified_items_to_draft_rows(vision_result["items"])
            extraction_strategy = "claude_api+vision"

        assert extraction_strategy == "claude_api+vision"
        assert len(items) == 4

    def test_vision_skip_strategy_stays_claude_api(self, sample_claude_items):
        """When vision is skipped, strategy stays 'claude_api'."""
        from storage.ai_vision_verify import verify_menu_with_vision
        from storage.ai_menu_extract import claude_items_to_draft_rows

        with patch("storage.ai_vision_verify._get_client", return_value=None):
            result = verify_menu_with_vision("/fake.png", sample_claude_items)

        extraction_strategy = "claude_api"
        if not result.get("skipped") and not result.get("error"):
            extraction_strategy = "claude_api+vision"

        assert extraction_strategy == "claude_api"  # Stays as Call 1 only


# ===========================================================================
# 7. Size variants flow through vision
# ===========================================================================
class TestSizeVariantsThroughVision:
    def test_sizes_preserved_through_verification(self, tmp_menu_image):
        """Items with size variants survive the vision verification round-trip."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows

        items_with_sizes = [
            {
                "name": "Supreme Pizza",
                "description": "The works",
                "price": 0,
                "category": "Pizza",
                "sizes": [
                    {"label": '10"', "price": 14.95},
                    {"label": '14"', "price": 19.95},
                    {"label": '18"', "price": 24.95},
                ],
            }
        ]

        # Mock Claude returning the same items (no changes needed)
        mock_response = MagicMock()
        mock_block = SimpleNamespace(text=json.dumps({
            "items": items_with_sizes,
            "confidence": 0.97,
            "notes": "No changes needed",
        }))
        mock_response.content = [mock_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_image), items_with_sizes)

        assert result["items"][0]["sizes"][0]["label"] == '10"'
        assert result["items"][0]["sizes"][2]["price"] == 24.95

        # Convert to draft rows — variants should be present
        rows = verified_items_to_draft_rows(result["items"])
        assert len(rows) == 1
        assert "_variants" in rows[0]
        assert len(rows[0]["_variants"]) == 3
        assert rows[0]["_variants"][0]["kind"] == "size"
        assert rows[0]["_variants"][0]["price_cents"] == 1495
        assert rows[0]["_variants"][2]["price_cents"] == 2495


# ===========================================================================
# 8. Multiple change types in one verification
# ===========================================================================
class TestMultipleChangeTypes:
    def test_all_seven_change_types(self):
        """All 7 change types can appear in a single verification.

        Note: compute_changes_log matches by lowercase name, so name_fixed only
        triggers when the lowercase key matches but the exact casing differs.
        """
        from storage.ai_vision_verify import compute_changes_log

        original = [
            # name_fixed: "hamburger" matches "Hamburger" by key but case differs
            # + price_fixed + description_fixed + category_fixed
            {"name": "hamburger", "description": "Beef patty", "price": 10.95,
             "category": "Burgers", "sizes": []},
            # item_removed: present in original, absent in corrected
            {"name": "Phantom Item", "description": None, "price": 5.0,
             "category": "Other", "sizes": []},
            # sizes_changed
            {"name": "Fries", "description": "Golden fries", "price": 4.95,
             "category": "Sides", "sizes": []},
        ]

        corrected = [
            # name_fixed (case) + price_fixed + description_fixed + category_fixed
            {"name": "Hamburger", "description": "Angus beef patty", "price": 11.95,
             "category": "Sandwiches", "sizes": []},
            # Phantom Item removed (not here)
            # sizes_changed
            {"name": "Fries", "description": "Golden fries", "price": 4.95,
             "category": "Sides", "sizes": [{"label": "Regular", "price": 4.95}, {"label": "Large", "price": 6.95}]},
            # item_added
            {"name": "Onion Rings", "description": "Beer battered", "price": 6.95,
             "category": "Sides", "sizes": []},
        ]

        changes = compute_changes_log(original, corrected)
        types = {c["type"] for c in changes}

        assert "name_fixed" in types
        assert "price_fixed" in types
        assert "description_fixed" in types
        assert "category_fixed" in types
        assert "item_removed" in types
        assert "item_added" in types
        assert "sizes_changed" in types
        assert len(types) == 7


# ===========================================================================
# 9. Edge cases
# ===========================================================================
class TestEdgeCases:
    def test_empty_claude_items_skips_vision(self, tmp_menu_image):
        """Empty Call 1 result means no vision verification needed."""
        from storage.ai_vision_verify import verify_menu_with_vision
        result = verify_menu_with_vision(str(tmp_menu_image), [])
        assert result["skipped"] is True
        assert result["skip_reason"] == "no_items"

    def test_vision_with_single_item(self, tmp_menu_image):
        """Vision works with a single item."""
        from storage.ai_vision_verify import verify_menu_with_vision

        single = [{"name": "Water", "description": None, "price": 2.0, "category": "Beverages", "sizes": []}]

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text=json.dumps({
            "items": single,
            "confidence": 0.99,
            "notes": "Single item verified",
        }))
        mock_response.content = [mock_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_image), single)

        assert len(result["items"]) == 1
        assert result["confidence"] == 0.99

    def test_vision_large_menu_many_items(self, tmp_menu_image):
        """Vision handles menus with many items (50+)."""
        from storage.ai_vision_verify import verify_menu_with_vision, verified_items_to_draft_rows

        large_menu = [
            {"name": f"Item {i}", "description": f"Description {i}",
             "price": float(i) + 0.95, "category": "Main", "sizes": []}
            for i in range(50)
        ]

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text=json.dumps({
            "items": large_menu,
            "confidence": 0.88,
            "notes": "Large menu verified",
        }))
        mock_response.content = [mock_block]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(str(tmp_menu_image), large_menu)

        assert len(result["items"]) == 50
        rows = verified_items_to_draft_rows(result["items"])
        assert len(rows) == 50
        assert all(r["confidence"] == 95 for r in rows)

    def test_vision_result_none_vision_result_in_pipeline(self):
        """When extraction_strategy is not claude_api, vision_result stays None."""
        # Simulates the portal/app.py flow where Claude extraction fails
        # and we fall back to heuristic AI
        vision_result = None
        extraction_strategy = "heuristic_ai"

        # Debug payload should NOT include vision_verification
        payload = {}
        payload["extraction_strategy"] = extraction_strategy
        if vision_result is not None:
            payload["vision_verification"] = {"skipped": True}

        assert "vision_verification" not in payload
