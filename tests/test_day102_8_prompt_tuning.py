# tests/test_day102_8_prompt_tuning.py
"""
Day 102.8 — Opus Live Testing & Prompt Tuning.

Tests that:
  1. _validate_descriptions() catches veggie items with meat descriptions
  2. _validate_descriptions() leaves caesar+pesto alone (not universally wrong)
  3. _validate_descriptions() leaves valid descriptions unchanged
  4. _validate_descriptions() handles None/empty descriptions safely
  5. Prompt includes topping enumeration guidance
  6. Prompt includes description alignment warning
  7. Prompt includes multi-column pricing guidance
  8. Prompt includes completeness check guidance
  8b. Prompt includes section header pricing/options/descriptions guidance
  8c. Prompt includes sauce extraction guidance
  8d. Prompt includes quantity-based item split guidance
  9. Thinking mode uses adaptive (not enabled)
  10. Debug log includes response_text_full field
  11. Items manifest includes description and sizes fields
  12. _validate_descriptions called during extraction flow
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_fake_response(items_json: str, include_thinking: bool = False) -> MagicMock:
    """Build a mock API response, optionally with a thinking block."""
    blocks = []
    if include_thinking:
        thinking_block = MagicMock()
        thinking_block.type = "thinking"
        thinking_block.thinking = "Let me analyze this menu section by section..."
        blocks.append(thinking_block)
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = items_json
    blocks.append(text_block)
    resp = MagicMock()
    resp.content = blocks
    resp.stop_reason = "end_turn"
    usage = MagicMock()
    usage.input_tokens = 500
    usage.output_tokens = 200
    resp.usage = usage
    return resp


def _make_stream_cm(response: MagicMock) -> MagicMock:
    """Wrap a fake response in a context manager mock for messages.stream()."""
    stream = MagicMock()
    stream.get_final_message.return_value = response
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    return stream


# ---------------------------------------------------------------------------
# 1. _validate_descriptions — veggie items with meat
# ---------------------------------------------------------------------------
class TestValidateDescriptions(unittest.TestCase):
    """Description validator catches obvious name-description mismatches."""

    def test_veggie_item_with_steak_desc_is_nulled(self):
        from storage.ai_menu_extract import _validate_descriptions
        items = [{"name": "Veggie Wrap", "description": "Steak, Lettuce, Tomato"}]
        fixed = _validate_descriptions(items)
        self.assertEqual(fixed, 1)
        self.assertIsNone(items[0]["description"])

    def test_veggie_calzone_with_chicken_desc_is_nulled(self):
        from storage.ai_menu_extract import _validate_descriptions
        items = [{"name": "Veggie Calzone", "description": "Grilled Chicken, Onion, Pepper"}]
        fixed = _validate_descriptions(items)
        self.assertEqual(fixed, 1)
        self.assertIsNone(items[0]["description"])

    def test_vegan_item_with_bacon_desc_is_nulled(self):
        from storage.ai_menu_extract import _validate_descriptions
        items = [{"name": "Vegan Bowl", "description": "Bacon, Lettuce, Tomato"}]
        fixed = _validate_descriptions(items)
        self.assertEqual(fixed, 1)
        self.assertIsNone(items[0]["description"])

    def test_caesar_with_pesto_desc_left_alone(self):
        """Caesar + pesto is not universally wrong — some restaurants do this."""
        from storage.ai_menu_extract import _validate_descriptions
        items = [{"name": "Chicken Caesar Wrap", "description": "Pesto Sauce, Lettuce, Tomato"}]
        fixed = _validate_descriptions(items)
        self.assertEqual(fixed, 0)
        self.assertEqual(items[0]["description"], "Pesto Sauce, Lettuce, Tomato")

    def test_valid_veggie_desc_left_alone(self):
        from storage.ai_menu_extract import _validate_descriptions
        items = [{"name": "Veggie Wrap", "description": "Tomato, Onions, Peppers, Broccoli, Spinach"}]
        fixed = _validate_descriptions(items)
        self.assertEqual(fixed, 0)
        self.assertEqual(items[0]["description"], "Tomato, Onions, Peppers, Broccoli, Spinach")

    def test_valid_caesar_desc_left_alone(self):
        from storage.ai_menu_extract import _validate_descriptions
        items = [{"name": "Chicken Caesar Wrap", "description": "Romaine, Parmesan, Caesar Dressing"}]
        fixed = _validate_descriptions(items)
        self.assertEqual(fixed, 0)
        self.assertEqual(items[0]["description"], "Romaine, Parmesan, Caesar Dressing")

    def test_non_veggie_item_with_meat_desc_left_alone(self):
        """Non-veggie items should keep their meat descriptions."""
        from storage.ai_menu_extract import _validate_descriptions
        items = [{"name": "Steak Wrap", "description": "Steak, Lettuce, Tomato"}]
        fixed = _validate_descriptions(items)
        self.assertEqual(fixed, 0)

    def test_none_description_not_counted(self):
        from storage.ai_menu_extract import _validate_descriptions
        items = [{"name": "Veggie Wrap", "description": None}]
        fixed = _validate_descriptions(items)
        self.assertEqual(fixed, 0)

    def test_empty_string_description_not_counted(self):
        from storage.ai_menu_extract import _validate_descriptions
        items = [{"name": "Veggie Wrap", "description": ""}]
        fixed = _validate_descriptions(items)
        self.assertEqual(fixed, 0)

    def test_multiple_items_mixed(self):
        """Handles batch of items with some mismatches."""
        from storage.ai_menu_extract import _validate_descriptions
        items = [
            {"name": "Veggie Wrap", "description": "Steak and cheese"},
            {"name": "Cheese Pizza", "description": "Mozzarella"},
            {"name": "Chicken Caesar Wrap", "description": "Pesto, Lettuce"},
            {"name": "Veggie Calzone", "description": "Broccoli, Tomato, Olives"},
        ]
        fixed = _validate_descriptions(items)
        self.assertEqual(fixed, 1)  # Only veggie+steak is universally wrong
        self.assertIsNone(items[0]["description"])
        self.assertEqual(items[1]["description"], "Mozzarella")
        self.assertEqual(items[2]["description"], "Pesto, Lettuce")  # Caesar+pesto is valid
        self.assertEqual(items[3]["description"], "Broccoli, Tomato, Olives")

    def test_empty_list(self):
        from storage.ai_menu_extract import _validate_descriptions
        self.assertEqual(_validate_descriptions([]), 0)

    def test_veggie_with_all_meat_terms(self):
        """All meat terms trigger the mismatch."""
        from storage.ai_menu_extract import _validate_descriptions, _MEAT_TERMS
        for meat in _MEAT_TERMS:
            items = [{"name": "Veggie Wrap", "description": f"With {meat} and cheese"}]
            fixed = _validate_descriptions(items)
            self.assertEqual(fixed, 1, f"Should catch '{meat}' in veggie description")


# ---------------------------------------------------------------------------
# 2. Prompt content checks
# ---------------------------------------------------------------------------
class TestPromptContent(unittest.TestCase):
    """Verify prompt includes guidance for identified issues."""

    def test_prompt_has_topping_enumeration(self):
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertIn("separate item", _EXTRACTION_GOAL.lower())
        self.assertIn("topping", _EXTRACTION_GOAL.lower())

    def test_prompt_has_description_alignment_warning(self):
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertIn("descriptions shift easily", _EXTRACTION_GOAL.lower())

    def test_prompt_has_multi_column_pricing(self):
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertIn("price column", _EXTRACTION_GOAL.lower())

    def test_prompt_has_completeness_check(self):
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertIn("do not skip", _EXTRACTION_GOAL.lower())

    def test_prompt_has_section_header_pricing(self):
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertIn("shared pricing", _EXTRACTION_GOAL.lower())

    def test_prompt_has_section_header_options(self):
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertIn("shared options", _EXTRACTION_GOAL.lower())

    def test_prompt_has_section_header_descriptions(self):
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertIn("shared descriptions", _EXTRACTION_GOAL.lower())

    def test_prompt_has_sauce_extraction(self):
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertIn("sauces category", _EXTRACTION_GOAL.lower())

    def test_prompt_has_quantity_split(self):
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertIn("each quantity is its own item", _EXTRACTION_GOAL.lower())

    def test_prompt_has_valid_categories(self):
        from storage.ai_menu_extract import _EXTRACTION_GOAL, VALID_CATEGORIES
        for cat in ["Pizza", "Toppings", "Wings", "Calzones", "Wraps", "Sauces"]:
            self.assertIn(cat, _EXTRACTION_GOAL)


# ---------------------------------------------------------------------------
# 3. Thinking mode uses enabled + budget_tokens
# ---------------------------------------------------------------------------
class TestThinkingConfig(unittest.TestCase):
    """Thinking uses enabled+budget (adaptive without budget exhausts all tokens)."""

    @patch("storage.ai_menu_extract.EXTENDED_THINKING", True)
    @patch("storage.ai_menu_extract._get_client")
    def test_thinking_sends_enabled_with_budget(self, mock_client_fn):
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response('{"items": []}', include_thinking=True))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        with patch("storage.ai_menu_extract._write_debug_log"):
            extract_menu_items_via_claude("menu text", use_thinking=True)

        kwargs = client.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["thinking"]["type"], "enabled")
        # budget_tokens prevents Opus from spending all tokens on thinking
        self.assertIn("budget_tokens", kwargs["thinking"])
        self.assertGreater(kwargs["thinking"]["budget_tokens"], 0)
        self.assertEqual(kwargs["temperature"], 1)

    @patch("storage.ai_menu_extract._get_client")
    def test_default_no_thinking(self, mock_client_fn):
        """Without thinking, no thinking config is sent."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response('{"items": []}'))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        with patch("storage.ai_menu_extract._write_debug_log"):
            extract_menu_items_via_claude("menu text")

        kwargs = client.messages.stream.call_args.kwargs
        self.assertNotIn("thinking", kwargs)
        self.assertEqual(kwargs["temperature"], 0)


# ---------------------------------------------------------------------------
# 4. Debug log includes response_text_full
# ---------------------------------------------------------------------------
class TestDebugLogFullResponse(unittest.TestCase):
    """Debug log captures full response text (not just preview)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_response_text_in_log(self):
        from storage.ai_menu_extract import _write_debug_log
        long_text = "x" * 5000
        with patch("storage.ai_menu_extract._LOGS_DIR", self.tmpdir):
            path = _write_debug_log(
                model="test", thinking_active=False, multimodal=False,
                ocr_text_length=100, image_blocks_count=0,
                api_kwargs_summary={}, response_text=long_text,
            )

        with open(path, "r") as f:
            data = json.load(f)
        self.assertEqual(data["response"]["response_text_full"], long_text)
        self.assertEqual(len(data["response"]["response_text_full"]), 5000)
        # Preview is still truncated
        self.assertEqual(len(data["response"]["response_text_preview"]), 500)


# ---------------------------------------------------------------------------
# 5. Items manifest includes description and sizes
# ---------------------------------------------------------------------------
class TestManifestEnrichment(unittest.TestCase):
    """Items manifest includes description + size details for analysis."""

    @patch("storage.ai_menu_extract._write_debug_log")
    @patch("storage.ai_menu_extract._get_client")
    def test_manifest_includes_desc_and_sizes(self, mock_client_fn, mock_log):
        items_json = json.dumps({"items": [
            {"name": "Cheese Pizza", "description": "Classic cheese",
             "price": 8.00, "category": "Pizza",
             "sizes": [{"label": "Small", "price": 8.0}, {"label": "Large", "price": 12.0}]},
            {"name": "French Fries", "description": None,
             "price": 7.00, "category": "Appetizers", "sizes": []},
        ]})
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response(items_json))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        extract_menu_items_via_claude("menu text")

        kwargs = mock_log.call_args.kwargs
        manifest = kwargs["items_manifest"]
        self.assertEqual(len(manifest), 2)

        # Pizza has description and sizes
        pizza = manifest[0]
        self.assertEqual(pizza["name"], "Cheese Pizza")
        self.assertEqual(pizza["desc"], "Classic cheese")
        self.assertEqual(pizza["n_sizes"], 2)
        self.assertEqual(len(pizza["sizes"]), 2)
        self.assertEqual(pizza["sizes"][0]["label"], "Small")
        self.assertEqual(pizza["sizes"][0]["price"], 8.0)

        # Fries has no description or sizes
        fries = manifest[1]
        self.assertEqual(fries["name"], "French Fries")
        self.assertNotIn("desc", fries)
        self.assertNotIn("sizes", fries)


# ---------------------------------------------------------------------------
# 6. Description validator called in extraction flow
# ---------------------------------------------------------------------------
class TestValidatorIntegration(unittest.TestCase):
    """_validate_descriptions is called during extract_menu_items_via_claude."""

    @patch("storage.ai_menu_extract._validate_descriptions", return_value=2)
    @patch("storage.ai_menu_extract._write_debug_log")
    @patch("storage.ai_menu_extract._get_client")
    def test_validator_called_during_extraction(self, mock_client_fn, mock_log, mock_validate):
        items_json = json.dumps({"items": [
            {"name": "Veggie Wrap", "description": "Steak, Lettuce",
             "price": 10.00, "category": "Wraps", "sizes": []},
        ]})
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response(items_json))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("menu text")

        mock_validate.assert_called_once()
        self.assertIsNotNone(result)

    @patch("storage.ai_menu_extract._write_debug_log")
    @patch("storage.ai_menu_extract._get_client")
    def test_validator_actually_nulls_in_extraction(self, mock_client_fn, mock_log):
        """Full integration: veggie + steak desc gets nulled in actual extraction."""
        items_json = json.dumps({"items": [
            {"name": "Veggie Wrap", "description": "Steak, Lettuce, Tomato",
             "price": 10.00, "category": "Wraps", "sizes": []},
            {"name": "Steak Wrap", "description": "Steak, Onion, Mushroom",
             "price": 10.00, "category": "Wraps", "sizes": []},
        ]})
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response(items_json))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("menu text")

        self.assertEqual(len(result), 2)
        # Veggie Wrap desc should be nulled
        self.assertIsNone(result[0]["description"])
        # Steak Wrap desc should be kept
        self.assertEqual(result[1]["description"], "Steak, Onion, Mushroom")


if __name__ == "__main__":
    unittest.main()
