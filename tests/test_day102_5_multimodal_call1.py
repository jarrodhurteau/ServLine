# tests/test_day102_5_multimodal_call1.py
"""
Day 102.5 — Multimodal Call 1: Image-First Menu Extraction.

Tests that extract_menu_items_via_claude() sends the menu image as primary
input with OCR text as a secondary hint when image_path is provided, and
falls back to text-only mode when no image is available.

Test classes:
  1. TestMultimodalPromptSelection — correct system prompt chosen
  2. TestMultimodalMessageBuilding — image blocks + text in content
  3. TestTextOnlyFallback — original text-only behaviour preserved
  4. TestImageEncodeFallback — graceful fallback when image can't be encoded
  5. TestEmptyOcrWithImage — OCR placeholder when text is empty but image exists
  6. TestEndToEndMultimodal — full happy-path with mocked API
  7. TestDraftRowsUnchanged — claude_items_to_draft_rows still works identically
  8. TestPortalWiring — run_ocr_and_make_draft passes image_path
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, call

# Ensure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Fake Anthropic client + response objects used across tests
# ---------------------------------------------------------------------------
def _make_fake_response(items_json: str) -> MagicMock:
    """Build a fake Anthropic message response with the given JSON text."""
    block = MagicMock()
    block.type = "text"
    block.text = items_json
    resp = MagicMock()
    resp.content = [block]
    return resp


def _make_stream_cm(response: MagicMock) -> MagicMock:
    """Wrap a fake response in a context manager mock for messages.stream()."""
    stream = MagicMock()
    stream.get_final_message.return_value = response
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    return stream


_SAMPLE_ITEMS_JSON = json.dumps({
    "items": [
        {"name": "Margherita Pizza", "description": "Fresh tomato, mozzarella",
         "price": 14.95, "category": "Pizza", "sizes": []},
        {"name": "Caesar Salad", "description": None,
         "price": 9.50, "category": "Salads", "sizes": []},
    ]
})

_SAMPLE_IMAGE_BLOCKS = [
    {"media_type": "image/jpeg", "data": "AAAA"},  # fake base64
]


# ---------------------------------------------------------------------------
# 1. Prompt selection
# ---------------------------------------------------------------------------
class TestMultimodalPromptSelection(unittest.TestCase):
    """Verify the correct system prompt is chosen based on multimodal vs text-only."""

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_multimodal_uses_multimodal_prompt(self, mock_client_fn, mock_enc_fn):
        """When image encodes successfully, the multimodal system prompt is used."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude, _SYSTEM_PROMPT_MULTIMODAL
        extract_menu_items_via_claude("some ocr text", image_path="/fake/menu.jpg")

        create_call = client.messages.stream.call_args
        self.assertEqual(create_call.kwargs["system"], _SYSTEM_PROMPT_MULTIMODAL)

    @patch("storage.ai_menu_extract._get_client")
    def test_text_only_uses_text_prompt(self, mock_client_fn):
        """When no image_path is provided, the text-only system prompt is used."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude, _SYSTEM_PROMPT_TEXT_ONLY
        extract_menu_items_via_claude("some ocr text")

        create_call = client.messages.stream.call_args
        self.assertEqual(create_call.kwargs["system"], _SYSTEM_PROMPT_TEXT_ONLY)


# ---------------------------------------------------------------------------
# 2. Message building (multimodal)
# ---------------------------------------------------------------------------
class TestMultimodalMessageBuilding(unittest.TestCase):
    """Verify multimodal messages contain image blocks + text content."""

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_image_blocks_come_first(self, mock_client_fn, mock_enc_fn):
        """Image content blocks appear before the text prompt."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude
        extract_menu_items_via_claude("menu text here", image_path="/fake/menu.jpg")

        create_call = client.messages.stream.call_args
        messages = create_call.kwargs["messages"]
        content = messages[0]["content"]

        # First block should be image
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["source"]["type"], "base64")
        self.assertEqual(content[0]["source"]["media_type"], "image/jpeg")
        self.assertEqual(content[0]["source"]["data"], "AAAA")

        # Last block should be text
        self.assertEqual(content[-1]["type"], "text")
        self.assertIn("menu text here", content[-1]["text"])

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_multi_page_pdf_sends_multiple_images(self, mock_client_fn, mock_enc_fn):
        """Multi-page PDFs produce multiple image blocks."""
        multi_images = [
            {"media_type": "image/png", "data": "PAGE1"},
            {"media_type": "image/png", "data": "PAGE2"},
            {"media_type": "image/png", "data": "PAGE3"},
        ]
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: multi_images

        from storage.ai_menu_extract import extract_menu_items_via_claude
        extract_menu_items_via_claude("ocr text", image_path="/fake/menu.pdf")

        content = client.messages.stream.call_args.kwargs["messages"][0]["content"]
        image_blocks = [b for b in content if b["type"] == "image"]
        text_blocks = [b for b in content if b["type"] == "text"]

        self.assertEqual(len(image_blocks), 3)
        self.assertEqual(len(text_blocks), 1)
        self.assertEqual(image_blocks[0]["source"]["data"], "PAGE1")
        self.assertEqual(image_blocks[2]["source"]["data"], "PAGE3")

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_multimodal_prompt_contains_ocr_hint(self, mock_client_fn, mock_enc_fn):
        """The multimodal user prompt includes the OCR text as a hint."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude
        extract_menu_items_via_claude("Garbled OCR text here", image_path="/fake/img.jpg")

        content = client.messages.stream.call_args.kwargs["messages"][0]["content"]
        text_block = [b for b in content if b["type"] == "text"][0]
        self.assertIn("Garbled OCR text here", text_block["text"])
        self.assertIn("source of truth", text_block["text"])


# ---------------------------------------------------------------------------
# 3. Text-only fallback
# ---------------------------------------------------------------------------
class TestTextOnlyFallback(unittest.TestCase):
    """Original text-only behaviour preserved when no image_path given."""

    @patch("storage.ai_menu_extract._get_client")
    def test_text_only_sends_string_content(self, mock_client_fn):
        """Text-only mode sends a plain string as content, not a list."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        extract_menu_items_via_claude("Some menu text")

        content = client.messages.stream.call_args.kwargs["messages"][0]["content"]
        # Text-only sends a plain string, not a list of content blocks
        self.assertIsInstance(content, str)
        self.assertIn("Some menu text", content)

    @patch("storage.ai_menu_extract._get_client")
    def test_text_only_returns_none_on_empty_text(self, mock_client_fn):
        """Text-only with empty text returns None immediately."""
        mock_client_fn.return_value = MagicMock()

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("")
        self.assertIsNone(result)

    @patch("storage.ai_menu_extract._get_client")
    def test_text_only_no_api_key_returns_none(self, mock_client_fn):
        """No API key → None (regardless of mode)."""
        mock_client_fn.return_value = None

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("text", image_path="/fake.jpg")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 4. Image encode failure → text-only fallback
# ---------------------------------------------------------------------------
class TestImageEncodeFallback(unittest.TestCase):
    """When image encoding fails, falls back to text-only gracefully."""

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_encoder_returns_empty_falls_back(self, mock_client_fn, mock_enc_fn):
        """encode_menu_images returns [] → text-only mode."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: []  # encode failed

        from storage.ai_menu_extract import extract_menu_items_via_claude, _SYSTEM_PROMPT_TEXT_ONLY
        result = extract_menu_items_via_claude("ocr text", image_path="/fake.jpg")

        # Should still succeed with text-only
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)

        # Should have used text-only prompt
        create_call = client.messages.stream.call_args
        self.assertEqual(create_call.kwargs["system"], _SYSTEM_PROMPT_TEXT_ONLY)

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_encoder_not_available_falls_back(self, mock_client_fn, mock_enc_fn):
        """_get_encoder returns None → text-only mode."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = None  # encoder unavailable

        from storage.ai_menu_extract import extract_menu_items_via_claude, _SYSTEM_PROMPT_TEXT_ONLY
        result = extract_menu_items_via_claude("ocr text", image_path="/fake.jpg")

        self.assertIsNotNone(result)
        create_call = client.messages.stream.call_args
        self.assertEqual(create_call.kwargs["system"], _SYSTEM_PROMPT_TEXT_ONLY)


# ---------------------------------------------------------------------------
# 5. Empty OCR text with image
# ---------------------------------------------------------------------------
class TestEmptyOcrWithImage(unittest.TestCase):
    """When OCR text is empty but image_path is provided, use placeholder."""

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_empty_ocr_with_image_sends_placeholder(self, mock_client_fn, mock_enc_fn):
        """Empty OCR + image → placeholder hint text, still multimodal."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude, _SYSTEM_PROMPT_MULTIMODAL
        result = extract_menu_items_via_claude("", image_path="/fake/menu.jpg")

        self.assertIsNotNone(result)
        create_call = client.messages.stream.call_args
        self.assertEqual(create_call.kwargs["system"], _SYSTEM_PROMPT_MULTIMODAL)

        content = create_call.kwargs["messages"][0]["content"]
        text_block = [b for b in content if b["type"] == "text"][0]
        self.assertIn("OCR produced no text", text_block["text"])

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_whitespace_only_ocr_with_image(self, mock_client_fn, mock_enc_fn):
        """Whitespace-only OCR + image → placeholder, still works."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("   \n\n  ", image_path="/fake/menu.jpg")

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)


# ---------------------------------------------------------------------------
# 6. End-to-end multimodal
# ---------------------------------------------------------------------------
class TestEndToEndMultimodal(unittest.TestCase):
    """Full happy-path: image + OCR text → structured items."""

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_multimodal_extracts_items(self, mock_client_fn, mock_enc_fn):
        """Multimodal extraction returns correct items."""
        rich_response = json.dumps({
            "items": [
                {"name": "6 oz Angus Burger", "description": "Certified Angus beef",
                 "price": 12.95, "category": "Burgers", "sizes": []},
                {"name": "Fresh Soups", "description": "Made daily",
                 "price": 5.95, "category": "Soups", "sizes": [
                     {"label": "Cup", "price": 5.95},
                     {"label": "Bowl", "price": 8.95},
                 ]},
                {"name": "Buffalo Wings", "description": "Tossed in sauce",
                 "price": 11.50, "category": "Wings", "sizes": []},
            ]
        })
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(rich_response))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude(
            "Go Zangus Burger\nFresh Soups & Buffalo Wings",  # garbled OCR
            image_path="/fake/menu.jpg",
        )

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 3)
        # Claude reads image directly — names should be correct, not garbled
        self.assertEqual(result[0]["name"], "6 oz Angus Burger")
        self.assertEqual(result[0]["price"], 12.95)
        self.assertEqual(result[0]["category"], "Burgers")
        # Soups has sizes
        self.assertEqual(len(result[1]["sizes"]), 2)
        self.assertEqual(result[1]["sizes"][0]["label"], "Cup")

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_multimodal_handles_markdown_fenced_json(self, mock_client_fn, mock_enc_fn):
        """Response wrapped in ```json fences is parsed correctly."""
        fenced = '```json\n' + _SAMPLE_ITEMS_JSON + '\n```'
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(fenced))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("ocr", image_path="/fake/img.jpg")

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_multimodal_api_error_returns_none(self, mock_client_fn, mock_enc_fn):
        """API exception in multimodal mode → returns None gracefully."""
        client = MagicMock()
        client.messages.stream.side_effect = Exception("API timeout")
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("ocr text", image_path="/fake/menu.jpg")
        self.assertIsNone(result)

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_multimodal_empty_response_returns_none(self, mock_client_fn, mock_enc_fn):
        """Empty API response in multimodal mode → returns None."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response("   "))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("ocr text", image_path="/fake/menu.jpg")
        self.assertIsNone(result)

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_multimodal_invalid_json_returns_none(self, mock_client_fn, mock_enc_fn):
        """Malformed JSON response → returns None."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response("not valid json {{{"))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("ocr text", image_path="/fake/menu.jpg")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 7. Draft rows unchanged
# ---------------------------------------------------------------------------
class TestDraftRowsUnchanged(unittest.TestCase):
    """claude_items_to_draft_rows() is not affected by multimodal changes."""

    def test_basic_conversion(self):
        from storage.ai_menu_extract import claude_items_to_draft_rows
        items = [
            {"name": "Test Item", "description": "Desc", "price": 10.50,
             "category": "Entrees", "sizes": []},
        ]
        rows = claude_items_to_draft_rows(items)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Test Item")
        self.assertEqual(rows[0]["price_cents"], 1050)
        self.assertEqual(rows[0]["confidence"], 90)

    def test_with_sizes_creates_variants(self):
        from storage.ai_menu_extract import claude_items_to_draft_rows
        items = [
            {"name": "Soup", "description": None, "price": 0,
             "category": "Soups", "sizes": [
                 {"label": "Cup", "price": 4.95},
                 {"label": "Bowl", "price": 7.95},
             ]},
        ]
        rows = claude_items_to_draft_rows(items)
        self.assertEqual(len(rows), 1)
        self.assertIn("_variants", rows[0])
        self.assertEqual(len(rows[0]["_variants"]), 2)
        self.assertEqual(rows[0]["_variants"][0]["label"], "Cup")
        self.assertEqual(rows[0]["price_cents"], 495)  # first size price


# ---------------------------------------------------------------------------
# 8. Portal wiring — image_path passed to extract
# ---------------------------------------------------------------------------
class TestPortalWiring(unittest.TestCase):
    """Verify portal/app.py passes image_path= to extract_menu_items_via_claude."""

    def test_call1_passes_image_path(self):
        """The run_ocr_and_make_draft code passes image_path=str(saved_file_path)."""
        import inspect
        # Read the relevant source to verify the wiring
        app_path = os.path.join(os.path.dirname(__file__), "..", "portal", "app.py")
        with open(app_path, "r", encoding="utf-8") as f:
            src = f.read()

        # Verify the extract call includes image_path
        self.assertIn("image_path=str(saved_file_path)", src,
                       "run_ocr_and_make_draft should pass image_path to extract_menu_items_via_claude")

    def test_extract_function_accepts_image_path(self):
        """extract_menu_items_via_claude has image_path parameter."""
        import inspect
        from storage.ai_menu_extract import extract_menu_items_via_claude
        sig = inspect.signature(extract_menu_items_via_claude)
        self.assertIn("image_path", sig.parameters)
        # Should be keyword-only with default None
        param = sig.parameters["image_path"]
        self.assertEqual(param.default, None)
        self.assertEqual(param.kind, inspect.Parameter.KEYWORD_ONLY)


# ---------------------------------------------------------------------------
# 9. Text truncation still works in multimodal mode
# ---------------------------------------------------------------------------
class TestTextTruncation(unittest.TestCase):
    """OCR text truncation at 30k chars works in both modes."""

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_long_ocr_text_truncated_multimodal(self, mock_client_fn, mock_enc_fn):
        """OCR text > 30k chars is truncated even in multimodal mode."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude
        long_text = "x" * 35_000
        extract_menu_items_via_claude(long_text, image_path="/fake/menu.jpg")

        content = client.messages.stream.call_args.kwargs["messages"][0]["content"]
        text_block = [b for b in content if b["type"] == "text"][0]
        self.assertIn("[... truncated ...]", text_block["text"])
        # Text should be much shorter than 35k
        self.assertLess(len(text_block["text"]), 32_000)


# ---------------------------------------------------------------------------
# 10. Encoder caching
# ---------------------------------------------------------------------------
class TestEncoderCaching(unittest.TestCase):
    """_get_encoder lazily caches the encoder function."""

    def test_get_encoder_returns_callable(self):
        """_get_encoder() returns encode_menu_images or None."""
        from storage.ai_menu_extract import _get_encoder
        result = _get_encoder()
        # It should return the actual encode_menu_images function
        if result is not None:
            self.assertTrue(callable(result))


if __name__ == "__main__":
    unittest.main()
