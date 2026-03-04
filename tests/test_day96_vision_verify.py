# tests/test_day96_vision_verify.py
"""
Day 96 — Vision Verification Module (Sprint 11.1 Start)
Tests for storage/ai_vision_verify.py

Covers:
  1. Image encoding helpers (_encode_image_file, _pdf_to_images, encode_menu_images)
  2. Prompt building (_build_user_prompt)
  3. Response parsing (_parse_verification_response)
  4. Changes log computation (compute_changes_log)
  5. Item normalization (_normalize_items)
  6. Main verify_menu_with_vision function (mocked API)
  7. verified_items_to_draft_rows conversion
  8. Edge cases: empty items, no API key, bad image, multi-page PDF
"""

import base64
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_items():
    """Sample extracted items from Call 1."""
    return [
        {"name": "Margherita Pizza", "description": "Fresh mozzarella, basil", "price": 14.95, "category": "Pizza", "sizes": []},
        {"name": "Caesar Salad", "description": "Romaine, croutons, parmesan", "price": 9.95, "category": "Salads", "sizes": []},
        {"name": "BBQ Burger", "description": "Bacon, cheddar, BBQ sauce", "price": 12.50, "category": "Burgers", "sizes": []},
    ]


@pytest.fixture
def corrected_items():
    """Items after vision verification with some corrections."""
    return [
        {"name": "Margherita Pizza", "description": "Fresh mozzarella, basil", "price": 14.95, "category": "Pizza", "sizes": []},
        {"name": "Caesar Salad", "description": "Romaine, croutons, parmesan", "price": 10.95, "category": "Salads", "sizes": []},
        {"name": "BBQ Burger", "description": "Bacon, cheddar, BBQ sauce", "price": 12.50, "category": "Burgers", "sizes": []},
        {"name": "Garlic Bread", "description": "With marinara", "price": 5.95, "category": "Appetizers", "sizes": []},
    ]


@pytest.fixture
def tmp_image(tmp_path):
    """Create a tiny valid PNG file for testing."""
    # Minimal 1x1 PNG
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    img_path = tmp_path / "menu.png"
    img_path.write_bytes(png_bytes)
    return str(img_path)


@pytest.fixture
def tmp_jpeg(tmp_path):
    """Create a tiny JPEG file for testing."""
    # Minimal valid JPEG (smallest valid JFIF)
    jpeg_bytes = bytes([
        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00,
        0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xD9,
    ])
    img_path = tmp_path / "menu.jpg"
    img_path.write_bytes(jpeg_bytes)
    return str(img_path)


# ===========================================================================
# 1. Image encoding tests
# ===========================================================================
class TestEncodeImageFile:
    def test_encode_png(self, tmp_image):
        from storage.ai_vision_verify import _encode_image_file
        result = _encode_image_file(tmp_image)
        assert result is not None
        assert result["media_type"] == "image/png"
        assert len(result["data"]) > 0
        # Should be valid base64
        decoded = base64.b64decode(result["data"])
        assert decoded[:4] == b"\x89PNG"

    def test_encode_jpeg(self, tmp_jpeg):
        from storage.ai_vision_verify import _encode_image_file
        result = _encode_image_file(tmp_jpeg)
        assert result is not None
        assert result["media_type"] == "image/jpeg"

    def test_encode_unsupported_extension(self, tmp_path):
        from storage.ai_vision_verify import _encode_image_file
        txt = tmp_path / "menu.txt"
        txt.write_text("not an image")
        assert _encode_image_file(str(txt)) is None

    def test_encode_nonexistent_file(self):
        from storage.ai_vision_verify import _encode_image_file
        assert _encode_image_file("/nonexistent/file.png") is None


class TestEncodeMenuImages:
    def test_single_image(self, tmp_image):
        from storage.ai_vision_verify import encode_menu_images
        result = encode_menu_images(tmp_image)
        assert len(result) == 1
        assert result[0]["media_type"] == "image/png"

    def test_nonexistent_file(self):
        from storage.ai_vision_verify import encode_menu_images
        assert encode_menu_images("/nonexistent/menu.png") == []

    def test_unsupported_format(self, tmp_path):
        from storage.ai_vision_verify import encode_menu_images
        bmp = tmp_path / "menu.bmp"
        bmp.write_bytes(b"\x00" * 10)
        assert encode_menu_images(str(bmp)) == []

    def test_pdf_delegates_to_pdf2image(self, tmp_path):
        """PDF encoding delegates to pdf2image (mocked)."""
        from storage.ai_vision_verify import encode_menu_images
        pdf = tmp_path / "menu.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        # Mock pdf2image to return a fake PIL image
        mock_img = MagicMock()
        mock_img.save = MagicMock(side_effect=lambda buf, format: buf.write(b"\x89PNG fake"))

        with patch("storage.ai_vision_verify.convert_from_path", return_value=[mock_img], create=True):
            # Need to patch the import inside the function
            import storage.ai_vision_verify as mod
            original_fn = mod._pdf_to_images

            def mock_pdf_to_images(path, dpi=200):
                return [{"media_type": "image/png", "data": base64.b64encode(b"fake").decode()}]

            mod._pdf_to_images = mock_pdf_to_images
            try:
                result = encode_menu_images(str(pdf))
                assert len(result) == 1
                assert result[0]["media_type"] == "image/png"
            finally:
                mod._pdf_to_images = original_fn


# ===========================================================================
# 2. Prompt building tests
# ===========================================================================
class TestBuildUserPrompt:
    def test_prompt_contains_items_json(self, sample_items):
        from storage.ai_vision_verify import _build_user_prompt
        prompt = _build_user_prompt(sample_items)
        assert "Margherita Pizza" in prompt
        assert "Caesar Salad" in prompt
        assert "14.95" in prompt
        assert '"items"' in prompt
        assert '"confidence"' in prompt

    def test_prompt_with_empty_items(self):
        from storage.ai_vision_verify import _build_user_prompt
        prompt = _build_user_prompt([])
        assert "[]" in prompt


# ===========================================================================
# 3. Response parsing tests
# ===========================================================================
class TestParseVerificationResponse:
    def test_valid_json_response(self, corrected_items):
        from storage.ai_vision_verify import _parse_verification_response
        resp = json.dumps({
            "items": corrected_items,
            "confidence": 0.92,
            "notes": "Fixed Caesar Salad price, added Garlic Bread",
        })
        items, confidence, notes = _parse_verification_response(resp)
        assert items is not None
        assert len(items) == 4
        assert confidence == 0.92
        assert "Caesar Salad" in notes

    def test_markdown_code_fences(self, corrected_items):
        from storage.ai_vision_verify import _parse_verification_response
        resp = "```json\n" + json.dumps({
            "items": corrected_items,
            "confidence": 0.88,
            "notes": "",
        }) + "\n```"
        items, confidence, notes = _parse_verification_response(resp)
        assert items is not None
        assert len(items) == 4
        assert confidence == 0.88

    def test_invalid_json(self):
        from storage.ai_vision_verify import _parse_verification_response
        items, confidence, notes = _parse_verification_response("not json at all")
        assert items is None
        assert confidence == 0.0

    def test_missing_items_key(self):
        from storage.ai_vision_verify import _parse_verification_response
        items, confidence, _ = _parse_verification_response('{"confidence": 0.9}')
        assert items is None

    def test_confidence_clamped(self):
        from storage.ai_vision_verify import _parse_verification_response
        resp = json.dumps({"items": [{"name": "Test", "price": 5.0}], "confidence": 1.5})
        _, confidence, _ = _parse_verification_response(resp)
        assert confidence == 1.0

    def test_negative_confidence_clamped(self):
        from storage.ai_vision_verify import _parse_verification_response
        resp = json.dumps({"items": [{"name": "Test", "price": 5.0}], "confidence": -0.5})
        _, confidence, _ = _parse_verification_response(resp)
        assert confidence == 0.0

    def test_non_dict_response(self):
        from storage.ai_vision_verify import _parse_verification_response
        items, _, _ = _parse_verification_response("[1, 2, 3]")
        assert items is None


# ===========================================================================
# 4. Changes log tests
# ===========================================================================
class TestComputeChangesLog:
    def test_no_changes(self, sample_items):
        from storage.ai_vision_verify import compute_changes_log
        changes = compute_changes_log(sample_items, sample_items)
        assert changes == []

    def test_price_fix(self, sample_items):
        from storage.ai_vision_verify import compute_changes_log
        corrected = [dict(it) for it in sample_items]
        corrected[1]["price"] = 10.95  # Caesar Salad price fixed
        changes = compute_changes_log(sample_items, corrected)
        assert len(changes) == 1
        assert changes[0]["type"] == "price_fixed"
        assert "$9.95" in changes[0]["detail"]
        assert "$10.95" in changes[0]["detail"]

    def test_name_fix(self, sample_items):
        from storage.ai_vision_verify import compute_changes_log
        corrected = [dict(it) for it in sample_items]
        corrected[2]["name"] = "BBQ Bacon Burger"  # name changed
        # Different name won't match by key, so it shows as add + remove
        changes = compute_changes_log(sample_items, corrected)
        types = {c["type"] for c in changes}
        assert "item_added" in types or "name_fixed" in types

    def test_item_added(self, sample_items, corrected_items):
        from storage.ai_vision_verify import compute_changes_log
        changes = compute_changes_log(sample_items, corrected_items)
        types = [c["type"] for c in changes]
        assert "item_added" in types
        added = [c for c in changes if c["type"] == "item_added"]
        assert any("Garlic Bread" in c["detail"] for c in added)

    def test_item_removed(self, sample_items):
        from storage.ai_vision_verify import compute_changes_log
        # Remove BBQ Burger from corrected
        corrected = [it for it in sample_items if it["name"] != "BBQ Burger"]
        changes = compute_changes_log(sample_items, corrected)
        removed = [c for c in changes if c["type"] == "item_removed"]
        assert len(removed) == 1
        assert "BBQ Burger" in removed[0]["detail"]

    def test_category_fix(self, sample_items):
        from storage.ai_vision_verify import compute_changes_log
        corrected = [dict(it) for it in sample_items]
        corrected[0]["category"] = "Specialty Pizzas"
        changes = compute_changes_log(sample_items, corrected)
        cat_changes = [c for c in changes if c["type"] == "category_fixed"]
        assert len(cat_changes) == 1
        assert "'Pizza'" in cat_changes[0]["detail"]
        assert "'Specialty Pizzas'" in cat_changes[0]["detail"]

    def test_description_fix(self, sample_items):
        from storage.ai_vision_verify import compute_changes_log
        corrected = [dict(it) for it in sample_items]
        corrected[0]["description"] = "Fresh mozzarella, tomato, basil"
        changes = compute_changes_log(sample_items, corrected)
        desc_changes = [c for c in changes if c["type"] == "description_fixed"]
        assert len(desc_changes) == 1

    def test_sizes_changed(self, sample_items):
        from storage.ai_vision_verify import compute_changes_log
        corrected = [dict(it) for it in sample_items]
        corrected[0]["sizes"] = [{"label": '10"', "price": 12.95}, {"label": '14"', "price": 16.95}]
        changes = compute_changes_log(sample_items, corrected)
        size_changes = [c for c in changes if c["type"] == "sizes_changed"]
        assert len(size_changes) == 1

    def test_multiple_changes(self, sample_items, corrected_items):
        from storage.ai_vision_verify import compute_changes_log
        changes = compute_changes_log(sample_items, corrected_items)
        # Caesar Salad price + Garlic Bread added = at least 2 changes
        assert len(changes) >= 2

    def test_empty_lists(self):
        from storage.ai_vision_verify import compute_changes_log
        assert compute_changes_log([], []) == []

    def test_all_removed(self, sample_items):
        from storage.ai_vision_verify import compute_changes_log
        changes = compute_changes_log(sample_items, [])
        assert len(changes) == 3
        assert all(c["type"] == "item_removed" for c in changes)


# ===========================================================================
# 5. Item normalization tests
# ===========================================================================
class TestNormalizeItems:
    def test_basic_normalization(self):
        from storage.ai_vision_verify import _normalize_items
        raw = [
            {"name": "  Test Item  ", "description": "  Desc  ", "price": "12.95", "category": "  Pizza  "},
            {"name": "", "price": 5.0},  # should be skipped (no name)
            "not a dict",  # should be skipped
        ]
        result = _normalize_items(raw)
        assert len(result) == 1
        assert result[0]["name"] == "Test Item"
        assert result[0]["description"] == "Desc"
        assert result[0]["price"] == 12.95
        assert result[0]["category"] == "Pizza"

    def test_null_description_normalized(self):
        from storage.ai_vision_verify import _normalize_items
        raw = [{"name": "Item", "description": "", "price": 5.0}]
        result = _normalize_items(raw)
        assert result[0]["description"] is None

    def test_default_category(self):
        from storage.ai_vision_verify import _normalize_items
        raw = [{"name": "Item", "price": 5.0}]
        result = _normalize_items(raw)
        assert result[0]["category"] == "Other"

    def test_sizes_normalized(self):
        from storage.ai_vision_verify import _normalize_items
        raw = [{"name": "Pizza", "price": 12.0, "sizes": [{"label": "Sm", "price": 10.0}, {"label": "Lg", "price": 16.0}]}]
        result = _normalize_items(raw)
        assert len(result[0]["sizes"]) == 2
        assert result[0]["sizes"][0]["label"] == "Sm"


# ===========================================================================
# 6. Main function tests (mocked API)
# ===========================================================================
class TestVerifyMenuWithVision:
    def test_no_items_returns_skipped(self, tmp_image):
        from storage.ai_vision_verify import verify_menu_with_vision
        result = verify_menu_with_vision(tmp_image, [])
        assert result["skipped"] is True
        assert result["skip_reason"] == "no_items"
        assert result["items"] == []

    def test_no_api_key_returns_skipped(self, tmp_image, sample_items):
        from storage.ai_vision_verify import verify_menu_with_vision
        with patch("storage.ai_vision_verify._get_client", return_value=None):
            result = verify_menu_with_vision(tmp_image, sample_items)
        assert result["skipped"] is True
        assert result["skip_reason"] == "no_api_key"
        assert result["items"] == sample_items  # returns original

    def test_bad_image_path_returns_skipped(self, sample_items):
        from storage.ai_vision_verify import verify_menu_with_vision
        mock_client = MagicMock()
        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision("/nonexistent/menu.png", sample_items)
        assert result["skipped"] is True
        assert result["skip_reason"] == "image_encode_failed"

    def test_successful_verification(self, tmp_image, sample_items, corrected_items):
        from storage.ai_vision_verify import verify_menu_with_vision

        # Mock Claude API response
        mock_response = MagicMock()
        mock_block = SimpleNamespace(text=json.dumps({
            "items": corrected_items,
            "confidence": 0.92,
            "notes": "Fixed Caesar Salad price, added Garlic Bread",
        }))
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(tmp_image, sample_items)

        assert result["skipped"] is False
        assert "error" not in result
        assert len(result["items"]) == 4
        assert result["confidence"] == 0.92
        assert len(result["changes"]) >= 2  # price fix + item added

        # Verify the API was called with image content
        call_args = mock_client.messages.create.call_args
        msg_content = call_args.kwargs["messages"][0]["content"]
        # First block should be image
        assert msg_content[0]["type"] == "image"
        assert msg_content[0]["source"]["media_type"] == "image/png"
        # Last block should be text
        assert msg_content[-1]["type"] == "text"

    def test_api_error_returns_original(self, tmp_image, sample_items):
        from storage.ai_vision_verify import verify_menu_with_vision

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API rate limited")

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(tmp_image, sample_items)

        assert result["skipped"] is False
        assert result["error"] == "API rate limited"
        assert result["items"] == sample_items  # returns original on failure

    def test_empty_response_returns_original(self, tmp_image, sample_items):
        from storage.ai_vision_verify import verify_menu_with_vision

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text="")
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(tmp_image, sample_items)

        assert result["error"] == "empty_response"
        assert result["items"] == sample_items

    def test_bad_json_response_returns_original(self, tmp_image, sample_items):
        from storage.ai_vision_verify import verify_menu_with_vision

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text="I can see a menu with items...")
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(tmp_image, sample_items)

        assert result["error"] == "parse_failed"
        assert result["items"] == sample_items

    def test_no_changes_needed(self, tmp_image, sample_items):
        """When Claude confirms items are correct, no changes logged."""
        from storage.ai_vision_verify import verify_menu_with_vision

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text=json.dumps({
            "items": sample_items,
            "confidence": 0.98,
            "notes": "No changes needed",
        }))
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(tmp_image, sample_items)

        assert result["changes"] == []
        assert result["confidence"] == 0.98
        assert len(result["items"]) == 3

    def test_custom_model(self, tmp_image, sample_items):
        from storage.ai_vision_verify import verify_menu_with_vision

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text=json.dumps({
            "items": sample_items,
            "confidence": 0.95,
            "notes": "",
        }))
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client):
            result = verify_menu_with_vision(
                tmp_image, sample_items, model="claude-sonnet-4-5-20250929"
            )

        assert result["model"] == "claude-sonnet-4-5-20250929"
        call_args = mock_client.messages.create.call_args
        assert call_args.kwargs["model"] == "claude-sonnet-4-5-20250929"


# ===========================================================================
# 7. Draft row conversion tests
# ===========================================================================
class TestVerifiedItemsToDraftRows:
    def test_basic_conversion(self, corrected_items):
        from storage.ai_vision_verify import verified_items_to_draft_rows
        rows = verified_items_to_draft_rows(corrected_items)
        assert len(rows) == 4
        # Vision-verified items get higher confidence
        for row in rows:
            assert row["confidence"] == 95
            assert "name" in row
            assert "price_cents" in row
            assert "category" in row

    def test_price_conversion(self):
        from storage.ai_vision_verify import verified_items_to_draft_rows
        items = [{"name": "Test", "price": 12.95, "category": "Other", "sizes": []}]
        rows = verified_items_to_draft_rows(items)
        assert rows[0]["price_cents"] == 1295

    def test_sizes_become_variants(self):
        from storage.ai_vision_verify import verified_items_to_draft_rows
        items = [{
            "name": "Pizza",
            "price": 0,
            "category": "Pizza",
            "sizes": [
                {"label": '10"', "price": 12.95},
                {"label": '14"', "price": 16.95},
            ],
        }]
        rows = verified_items_to_draft_rows(items)
        assert "_variants" in rows[0]
        assert len(rows[0]["_variants"]) == 2
        assert rows[0]["_variants"][0]["kind"] == "size"

    def test_empty_items(self):
        from storage.ai_vision_verify import verified_items_to_draft_rows
        assert verified_items_to_draft_rows([]) == []


# ===========================================================================
# 8. Sizes differ helper
# ===========================================================================
class TestSizesDiffer:
    def test_same_sizes(self):
        from storage.ai_vision_verify import _sizes_differ
        a = [{"label": "Sm", "price": 10.0}, {"label": "Lg", "price": 16.0}]
        b = [{"label": "Sm", "price": 10.0}, {"label": "Lg", "price": 16.0}]
        assert _sizes_differ(a, b) is False

    def test_different_length(self):
        from storage.ai_vision_verify import _sizes_differ
        assert _sizes_differ([{"label": "Sm", "price": 10.0}], []) is True

    def test_different_label(self):
        from storage.ai_vision_verify import _sizes_differ
        a = [{"label": "Sm", "price": 10.0}]
        b = [{"label": "Small", "price": 10.0}]
        assert _sizes_differ(a, b) is True

    def test_different_price(self):
        from storage.ai_vision_verify import _sizes_differ
        a = [{"label": "Sm", "price": 10.0}]
        b = [{"label": "Sm", "price": 11.0}]
        assert _sizes_differ(a, b) is True

    def test_both_empty(self):
        from storage.ai_vision_verify import _sizes_differ
        assert _sizes_differ([], []) is False


# ===========================================================================
# 9. MIME map coverage
# ===========================================================================
class TestMimeMap:
    def test_supported_formats(self):
        from storage.ai_vision_verify import _MIME_MAP
        assert _MIME_MAP[".png"] == "image/png"
        assert _MIME_MAP[".jpg"] == "image/jpeg"
        assert _MIME_MAP[".jpeg"] == "image/jpeg"
        assert _MIME_MAP[".gif"] == "image/gif"
        assert _MIME_MAP[".webp"] == "image/webp"

    def test_unsupported_not_in_map(self):
        from storage.ai_vision_verify import _MIME_MAP
        assert ".bmp" not in _MIME_MAP
        assert ".tiff" not in _MIME_MAP
        assert ".pdf" not in _MIME_MAP


# ===========================================================================
# 10. Multi-page image support
# ===========================================================================
class TestMultiPageSupport:
    def test_multiple_images_in_content(self, tmp_path, sample_items):
        """Verify that multi-page PDFs send multiple image blocks."""
        from storage.ai_vision_verify import verify_menu_with_vision

        # Create two image files to simulate multi-page
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        img_path = tmp_path / "menu.png"
        img_path.write_bytes(png_bytes)

        mock_response = MagicMock()
        mock_block = SimpleNamespace(text=json.dumps({
            "items": sample_items,
            "confidence": 0.95,
            "notes": "",
        }))
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        # Mock encode_menu_images to return 3 pages
        three_pages = [
            {"media_type": "image/png", "data": base64.b64encode(png_bytes).decode()},
            {"media_type": "image/png", "data": base64.b64encode(png_bytes).decode()},
            {"media_type": "image/png", "data": base64.b64encode(png_bytes).decode()},
        ]

        with patch("storage.ai_vision_verify._get_client", return_value=mock_client), \
             patch("storage.ai_vision_verify.encode_menu_images", return_value=three_pages):
            result = verify_menu_with_vision(str(img_path), sample_items)

        # Should have 3 image blocks + 1 text block
        call_args = mock_client.messages.create.call_args
        msg_content = call_args.kwargs["messages"][0]["content"]
        image_blocks = [b for b in msg_content if b["type"] == "image"]
        text_blocks = [b for b in msg_content if b["type"] == "text"]
        assert len(image_blocks) == 3
        assert len(text_blocks) == 1