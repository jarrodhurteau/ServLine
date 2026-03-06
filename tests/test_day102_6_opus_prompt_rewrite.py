# tests/test_day102_6_opus_prompt_rewrite.py
"""
Day 102.6 — Minimal Prompt + Category Normalizer + Thinking Support.

Tests that:
  1. Call 1 defaults to Sonnet (3-call pipeline) — Opus+thinking is opt-in
  2. Extraction prompt is minimal — concise rules, no verbose instructions
  3. Extended thinking params (temperature=1) sent correctly when opted in
  4. Thinking blocks in response are skipped, only text blocks extracted
  5. Code-level category normalizer maps headings to whitelist values
  6. Pipeline uses 3-call pipeline (Call 2 & 3 active) by default
  7. use_thinking=True opt-in still works for Opus
"""
from __future__ import annotations

import inspect
import json
import os
import sys
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
    {"media_type": "image/jpeg", "data": "AAAA"},
]


# ---------------------------------------------------------------------------
# 1. Model default — Call 1 uses Sonnet (3-call pipeline)
# ---------------------------------------------------------------------------
class TestModelDefaults(unittest.TestCase):
    """Call 1 extraction defaults to Sonnet for the 3-call pipeline."""

    def test_default_model_is_sonnet(self):
        from storage.ai_menu_extract import extract_menu_items_via_claude
        sig = inspect.signature(extract_menu_items_via_claude)
        default = sig.parameters["model"].default
        self.assertIn("sonnet", default)

    def test_use_thinking_defaults_false(self):
        """use_thinking parameter defaults to False (3-call pipeline)."""
        from storage.ai_menu_extract import extract_menu_items_via_claude
        sig = inspect.signature(extract_menu_items_via_claude)
        default = sig.parameters["use_thinking"].default
        self.assertFalse(default)

    def test_extended_thinking_module_flag_on_for_ab_test(self):
        """EXTENDED_THINKING module constant is True during A/B testing."""
        from storage.ai_menu_extract import EXTENDED_THINKING
        self.assertTrue(EXTENDED_THINKING)


# ---------------------------------------------------------------------------
# 2. Prompt is minimal — trusts thinking over rules
# ---------------------------------------------------------------------------
class TestMinimalPrompt(unittest.TestCase):
    """New prompt is dramatically smaller — lets Opus think instead of rule-following."""

    def test_prompt_under_2000_chars(self):
        """System prompt (multimodal) should be well under old 3500 char limit."""
        from storage.ai_menu_extract import _SYSTEM_PROMPT_MULTIMODAL
        self.assertLess(len(_SYSTEM_PROMPT_MULTIMODAL), 2000,
                        f"Prompt is {len(_SYSTEM_PROMPT_MULTIMODAL)} chars — should stay concise")

    def test_extraction_goal_under_1700_chars(self):
        """Core extraction goal is concise but includes targeted accuracy guidance."""
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertLess(len(_EXTRACTION_GOAL), 1700,
                        f"Goal is {len(_EXTRACTION_GOAL)} chars — should stay concise")

    def test_no_prescriptive_rules(self):
        """No CRITICAL RULES, no verbose instructions."""
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertNotIn("CRITICAL RULES", _EXTRACTION_GOAL)
        self.assertNotIn("NEVER", _EXTRACTION_GOAL)
        self.assertNotIn("MUST be one of", _EXTRACTION_GOAL)

    def test_no_specific_menu_examples(self):
        """No menu-specific examples (those were overfitting to one menu)."""
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertNotIn("Homemade Fried Mushrooms", _EXTRACTION_GOAL)
        self.assertNotIn("Go Zangus", _EXTRACTION_GOAL)
        self.assertNotIn("Club Sandwiches", _EXTRACTION_GOAL)
        self.assertNotIn("Melt Sandwiches", _EXTRACTION_GOAL)

    def test_category_list_present(self):
        """Category whitelist is still in the prompt."""
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        for cat in ["Pizza", "Toppings", "Sandwiches", "Burgers", "Wings",
                     "Sauces", "Calzones", "Subs"]:
            self.assertIn(cat, _EXTRACTION_GOAL, f"Missing category: {cat}")

    def test_json_output_format(self):
        """Output format instruction present."""
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertIn('{"items":', _EXTRACTION_GOAL)

    def test_splitting_guidance(self):
        """Basic splitting guidance present (compound items, toppings, sauces)."""
        from storage.ai_menu_extract import _EXTRACTION_GOAL
        self.assertIn("Split compound items", _EXTRACTION_GOAL)
        self.assertIn("toppings", _EXTRACTION_GOAL.lower())
        self.assertIn("sauces", _EXTRACTION_GOAL.lower())


# ---------------------------------------------------------------------------
# 3. Extended thinking API params (opt-in via use_thinking=True)
# ---------------------------------------------------------------------------
class TestExtendedThinkingParams(unittest.TestCase):
    """Thinking mode sends correct API parameters when opted in."""

    @patch("storage.ai_menu_extract.EXTENDED_THINKING", True)
    @patch("storage.ai_menu_extract._get_client")
    def test_thinking_sends_temperature_1(self, mock_client_fn):
        """Extended thinking requires temperature=1."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(
            _SAMPLE_ITEMS_JSON, include_thinking=True))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        extract_menu_items_via_claude("menu text", use_thinking=True)

        kwargs = client.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["temperature"], 1)

    @patch("storage.ai_menu_extract.EXTENDED_THINKING", True)
    @patch("storage.ai_menu_extract._get_client")
    def test_thinking_sends_thinking_config(self, mock_client_fn):
        """Extended thinking config uses enabled + budget_tokens."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(
            _SAMPLE_ITEMS_JSON, include_thinking=True))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        extract_menu_items_via_claude("menu text", use_thinking=True)

        kwargs = client.messages.stream.call_args.kwargs
        self.assertIn("thinking", kwargs)
        self.assertEqual(kwargs["thinking"]["type"], "enabled")
        self.assertIn("budget_tokens", kwargs["thinking"])

    @patch("storage.ai_menu_extract._get_client")
    def test_default_uses_temperature_0(self, mock_client_fn):
        """Default (no thinking) uses temperature=0."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        extract_menu_items_via_claude("menu text")

        kwargs = client.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["temperature"], 0)
        self.assertNotIn("thinking", kwargs)

    @patch("storage.ai_menu_extract.EXTENDED_THINKING", True)
    @patch("storage.ai_menu_extract._get_client")
    def test_enabled_thinking_has_budget(self, mock_client_fn):
        """Enabled thinking config includes budget_tokens to cap thinking."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(
            _SAMPLE_ITEMS_JSON, include_thinking=True))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        extract_menu_items_via_claude("menu text", use_thinking=True)

        thinking = client.messages.stream.call_args.kwargs["thinking"]
        self.assertEqual(thinking["type"], "enabled")
        self.assertGreater(thinking["budget_tokens"], 0)


# ---------------------------------------------------------------------------
# 4. Thinking block parsing
# ---------------------------------------------------------------------------
class TestThinkingBlockParsing(unittest.TestCase):
    """Response parsing correctly handles thinking + text blocks."""

    @patch("storage.ai_menu_extract._get_client")
    def test_thinking_block_skipped_text_extracted(self, mock_client_fn):
        """Thinking blocks are skipped, only text block content used."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(
            _SAMPLE_ITEMS_JSON, include_thinking=True))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("menu text")

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "Margherita Pizza")

    @patch("storage.ai_menu_extract._get_client")
    def test_response_without_thinking_blocks(self, mock_client_fn):
        """Works fine when no thinking blocks in response (e.g., thinking disabled)."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(
            _SAMPLE_ITEMS_JSON, include_thinking=False))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("menu text", use_thinking=False)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)


# ---------------------------------------------------------------------------
# 5. Category normalizer (code-level guardrail)
# ---------------------------------------------------------------------------
class TestCategoryNormalizer(unittest.TestCase):
    """_normalize_category maps headings to whitelist values deterministically."""

    def setUp(self):
        from storage.ai_menu_extract import _normalize_category, VALID_CATEGORIES
        self.normalize = _normalize_category
        self.valid = VALID_CATEGORIES

    def test_valid_category_passes_through(self):
        """Already-valid categories pass through unchanged."""
        for cat in ["Pizza", "Sandwiches", "Wings", "Soups", "Calzones"]:
            self.assertEqual(self.normalize(cat), cat)

    def test_club_sandwiches_maps(self):
        self.assertEqual(self.normalize("Club Sandwiches"), "Sandwiches")

    def test_melt_sandwiches_maps(self):
        self.assertEqual(self.normalize("Melt Sandwiches"), "Sandwiches")

    def test_gourmet_pizza_maps(self):
        self.assertEqual(self.normalize("Gourmet Pizza"), "Pizza")

    def test_specialty_pizza_maps(self):
        self.assertEqual(self.normalize("Specialty Pizza"), "Pizza")

    def test_buffalo_wings_maps(self):
        self.assertEqual(self.normalize("Buffalo Wings"), "Wings")

    def test_fresh_buffalo_wings_maps(self):
        self.assertEqual(self.normalize("Fresh Buffalo Wings"), "Wings")

    def test_hot_subs_maps(self):
        self.assertEqual(self.normalize("Hot Subs"), "Subs")

    def test_cold_subs_maps(self):
        self.assertEqual(self.normalize("Cold Subs"), "Subs")

    def test_dinner_entrees_maps(self):
        self.assertEqual(self.normalize("Dinner Entrees"), "Entrees")

    def test_kids_menu_alias(self):
        self.assertEqual(self.normalize("Kid's Menu"), "Kids Menu")

    def test_wraps_city_maps(self):
        self.assertEqual(self.normalize("Wraps City"), "Wraps")

    def test_fresh_soups_maps(self):
        self.assertEqual(self.normalize("Fresh Soups"), "Soups")

    def test_empty_returns_other(self):
        self.assertEqual(self.normalize(""), "Other")

    def test_none_returns_other(self):
        self.assertEqual(self.normalize(None), "Other")

    def test_unknown_returns_other(self):
        self.assertEqual(self.normalize("Chef's Specials"), "Other")

    def test_all_valid_categories_pass_through(self):
        """Every valid category passes through unchanged."""
        for cat in self.valid:
            self.assertEqual(self.normalize(cat), cat, f"{cat} should pass through")

    def test_substring_match_works(self):
        """Substring of a valid category still matches."""
        # "Brick Oven Pizza" contains "Pizza"
        self.assertEqual(self.normalize("Brick Oven Pizza"), "Pizza")

    def test_normalizer_applied_during_extraction(self):
        """Category normalizer is applied to Claude's raw output."""
        with patch("storage.ai_menu_extract._get_client") as mock_fn:
            client = MagicMock()
            raw = json.dumps({"items": [
                {"name": "Turkey Club", "price": 14.00,
                 "category": "Club Sandwiches", "sizes": []},
            ]})
            client.messages.stream.return_value = _make_stream_cm(_make_fake_response(raw, True))
            mock_fn.return_value = client

            from storage.ai_menu_extract import extract_menu_items_via_claude
            result = extract_menu_items_via_claude("text")
            self.assertEqual(result[0]["category"], "Sandwiches")


# ---------------------------------------------------------------------------
# 6. Consolidated prompt structure
# ---------------------------------------------------------------------------
class TestConsolidatedPrompts(unittest.TestCase):
    """Both modes share the same _EXTRACTION_GOAL core."""

    def test_both_prompts_share_extraction_goal(self):
        from storage.ai_menu_extract import (
            _SYSTEM_PROMPT_MULTIMODAL, _SYSTEM_PROMPT_TEXT_ONLY, _EXTRACTION_GOAL
        )
        self.assertIn(_EXTRACTION_GOAL, _SYSTEM_PROMPT_MULTIMODAL)
        self.assertIn(_EXTRACTION_GOAL, _SYSTEM_PROMPT_TEXT_ONLY)

    def test_multimodal_mentions_image(self):
        from storage.ai_menu_extract import _SYSTEM_PROMPT_MULTIMODAL
        self.assertIn("image", _SYSTEM_PROMPT_MULTIMODAL.lower())

    def test_text_only_mentions_ocr(self):
        from storage.ai_menu_extract import _SYSTEM_PROMPT_TEXT_ONLY
        self.assertIn("OCR", _SYSTEM_PROMPT_TEXT_ONLY)

    def test_prompts_differ(self):
        from storage.ai_menu_extract import (
            _SYSTEM_PROMPT_MULTIMODAL, _SYSTEM_PROMPT_TEXT_ONLY
        )
        self.assertNotEqual(_SYSTEM_PROMPT_MULTIMODAL, _SYSTEM_PROMPT_TEXT_ONLY)

    def test_exports_exist(self):
        from storage import ai_menu_extract
        self.assertTrue(hasattr(ai_menu_extract, "_SYSTEM_PROMPT_MULTIMODAL"))
        self.assertTrue(hasattr(ai_menu_extract, "_SYSTEM_PROMPT_TEXT_ONLY"))
        self.assertTrue(hasattr(ai_menu_extract, "VALID_CATEGORIES"))
        self.assertTrue(hasattr(ai_menu_extract, "EXTENDED_THINKING"))


# ---------------------------------------------------------------------------
# 7. End-to-end extraction
# ---------------------------------------------------------------------------
class TestEndToEndExtraction(unittest.TestCase):
    """Extraction works end-to-end in default (Sonnet) and opt-in thinking modes."""

    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_multimodal_default_sonnet(self, mock_client_fn, mock_enc_fn):
        """Full multimodal extraction succeeds with default Sonnet."""
        rich = json.dumps({"items": [
            {"name": "Buffalo Wings", "description": None, "price": 11.50,
             "category": "Wings", "sizes": [
                 {"label": "10 pc", "price": 11.50},
                 {"label": "20 pc", "price": 19.95},
             ]},
            {"name": "Cheeseburger", "description": "Angus beef", "price": 9.00,
             "category": "Burgers", "sizes": [
                 {"label": "Regular", "price": 9.00},
                 {"label": "Deluxe", "price": 13.00},
             ]},
        ]})
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(rich))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("garbled ocr", image_path="/fake/menu.jpg")

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "Buffalo Wings")
        self.assertEqual(len(result[0]["sizes"]), 2)

        # Verify Sonnet, no thinking
        kwargs = client.messages.stream.call_args.kwargs
        self.assertIn("sonnet", kwargs["model"])
        self.assertEqual(kwargs["temperature"], 0)
        self.assertNotIn("thinking", kwargs)

    @patch("storage.ai_menu_extract.EXTENDED_THINKING", True)
    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_multimodal_with_thinking_optin(self, mock_client_fn, mock_enc_fn):
        """Multimodal + thinking works when explicitly opted in."""
        rich = json.dumps({"items": [
            {"name": "Buffalo Wings", "description": None, "price": 11.50,
             "category": "Wings", "sizes": []},
        ]})
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(rich, include_thinking=True))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda path: _SAMPLE_IMAGE_BLOCKS

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("garbled ocr", image_path="/fake/menu.jpg",
                                              model="claude-opus-4-6", use_thinking=True)

        self.assertIsNotNone(result)
        kwargs = client.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["temperature"], 1)
        self.assertIn("thinking", kwargs)

    @patch("storage.ai_menu_extract._get_client")
    def test_text_only_default(self, mock_client_fn):
        """Text-only extraction works with defaults."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("Some menu text")

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)

    @patch("storage.ai_menu_extract._get_client")
    def test_thinking_blocks_still_skipped(self, mock_client_fn):
        """Even if response has thinking blocks, they are skipped gracefully."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(
            _SAMPLE_ITEMS_JSON, include_thinking=True))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("text")

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)

    @patch("storage.ai_menu_extract._get_client")
    def test_category_normalization_in_extraction(self, mock_client_fn):
        """Categories are normalized post-extraction."""
        raw = json.dumps({"items": [
            {"name": "Ham & Cheese Melt", "price": 11.95,
             "category": "Melt Sandwiches", "sizes": []},
            {"name": "10 Wings", "price": 11.50,
             "category": "Fresh Buffalo Wings", "sizes": []},
            {"name": "Chicken Noodle", "price": 6.50,
             "category": "Fresh Soups", "sizes": []},
        ]})
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(raw))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("text")

        self.assertEqual(result[0]["category"], "Sandwiches")
        self.assertEqual(result[1]["category"], "Wings")
        self.assertEqual(result[2]["category"], "Soups")

    @patch("storage.ai_menu_extract._get_client")
    def test_model_override_still_works(self, mock_client_fn):
        """Callers can override model if needed."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(_make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        extract_menu_items_via_claude("text", model="claude-sonnet-4-5-20250929")

        self.assertEqual(
            client.messages.stream.call_args.kwargs["model"],
            "claude-sonnet-4-5-20250929",
        )


# ---------------------------------------------------------------------------
# 8. Pipeline — 3-call pipeline active (Call 2 & 3 not bypassed)
# ---------------------------------------------------------------------------
class TestPipelineConfig(unittest.TestCase):
    """Default config uses 3-call pipeline (Call 2 & 3 active)."""

    def test_extended_thinking_on_for_ab_test(self):
        """EXTENDED_THINKING is True during A/B testing phase."""
        from storage.ai_menu_extract import EXTENDED_THINKING
        self.assertTrue(EXTENDED_THINKING)

    def test_call2_module_exists(self):
        """ai_vision_verify.py still exists and defaults to Sonnet."""
        from storage.ai_vision_verify import _DEFAULT_MODEL
        self.assertIn("sonnet", _DEFAULT_MODEL)

    def test_call3_module_exists(self):
        """ai_reconcile.py still exists and defaults to Sonnet."""
        from storage.ai_reconcile import _DEFAULT_MODEL
        self.assertIn("sonnet", _DEFAULT_MODEL)


# ---------------------------------------------------------------------------
# 9. Valid categories constant
# ---------------------------------------------------------------------------
class TestValidCategories(unittest.TestCase):
    """VALID_CATEGORIES frozenset is correct and complete."""

    def test_valid_categories_is_frozenset(self):
        from storage.ai_menu_extract import VALID_CATEGORIES
        self.assertIsInstance(VALID_CATEGORIES, frozenset)

    def test_has_all_expected_categories(self):
        from storage.ai_menu_extract import VALID_CATEGORIES
        expected = {
            "Pizza", "Toppings", "Appetizers", "Salads", "Soups", "Sandwiches",
            "Burgers", "Wraps", "Entrees", "Seafood", "Pasta", "Steaks", "Wings",
            "Sauces", "Sides", "Desserts", "Beverages", "Kids Menu", "Breakfast",
            "Calzones", "Subs", "Platters", "Other",
        }
        self.assertEqual(VALID_CATEGORIES, expected)

    def test_categories_match_prompt(self):
        """Every category in VALID_CATEGORIES appears in the prompt."""
        from storage.ai_menu_extract import VALID_CATEGORIES, _EXTRACTION_GOAL
        for cat in VALID_CATEGORIES:
            self.assertIn(cat, _EXTRACTION_GOAL, f"Category '{cat}' not in prompt")


if __name__ == "__main__":
    unittest.main()
