# tests/test_day102_7_thinking_debug_log.py
"""
Day 102.7 — Sonnet Thinking + File Debug Logging.

Tests that:
  1. _write_debug_log() creates file with expected fields
  2. THINKING_MODEL constant defaults to sonnet-4-6
  3. Thinking opt-in overrides model to THINKING_MODEL
  4. Thinking opt-in sends correct API params (temperature=1, adaptive)
  5. Debug log written on success (with parsed_item_count)
  6. Debug log written on failure (with error field)
  7. 3-call pipeline still works as default (EXTENDED_THINKING=False)
  8. Portal passes use_thinking when EXTENDED_THINKING is on
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
        thinking_block.thinking = "Let me analyze this menu carefully..."
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


_SAMPLE_ITEMS_JSON = json.dumps({
    "items": [
        {"name": "Margherita Pizza", "description": "Fresh tomato, mozzarella",
         "price": 14.95, "category": "Pizza", "sizes": []},
        {"name": "Caesar Salad", "description": None,
         "price": 9.50, "category": "Salads", "sizes": []},
    ]
})


# ---------------------------------------------------------------------------
# 1. THINKING_MODEL constant
# ---------------------------------------------------------------------------
class TestThinkingModelConstant(unittest.TestCase):
    """THINKING_MODEL defaults to Sonnet 4.6."""

    def test_thinking_model_is_sonnet_4_6(self):
        from storage.ai_menu_extract import THINKING_MODEL
        self.assertEqual(THINKING_MODEL, "claude-sonnet-4-6")

    def test_thinking_model_differs_from_default(self):
        """THINKING_MODEL is different from the default Call 1 model."""
        import inspect
        from storage.ai_menu_extract import THINKING_MODEL, extract_menu_items_via_claude
        sig = inspect.signature(extract_menu_items_via_claude)
        default_model = sig.parameters["model"].default
        self.assertNotEqual(THINKING_MODEL, default_model)

    def test_extended_thinking_still_off_by_default(self):
        from storage.ai_menu_extract import EXTENDED_THINKING
        self.assertFalse(EXTENDED_THINKING)


# ---------------------------------------------------------------------------
# 2. Thinking opt-in overrides model to THINKING_MODEL
# ---------------------------------------------------------------------------
class TestThinkingModelOverride(unittest.TestCase):
    """When thinking is active, model is overridden to THINKING_MODEL."""

    @patch("storage.ai_menu_extract.EXTENDED_THINKING", True)
    @patch("storage.ai_menu_extract._get_client")
    def test_thinking_uses_thinking_model(self, mock_client_fn):
        """Model sent to API should be THINKING_MODEL when thinking is active."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response(_SAMPLE_ITEMS_JSON, include_thinking=True))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude, THINKING_MODEL
        with patch("storage.ai_menu_extract._write_debug_log"):
            extract_menu_items_via_claude("menu text", use_thinking=True)

        kwargs = client.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["model"], THINKING_MODEL)

    @patch("storage.ai_menu_extract.EXTENDED_THINKING", True)
    @patch("storage.ai_menu_extract._get_client")
    def test_thinking_sends_adaptive_config(self, mock_client_fn):
        """Thinking config is adaptive (no budget_tokens)."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response(_SAMPLE_ITEMS_JSON, include_thinking=True))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        with patch("storage.ai_menu_extract._write_debug_log"):
            extract_menu_items_via_claude("menu text", use_thinking=True)

        kwargs = client.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["thinking"], {"type": "adaptive"})
        self.assertEqual(kwargs["temperature"], 1)

    @patch("storage.ai_menu_extract._get_client")
    def test_default_uses_sonnet_not_thinking_model(self, mock_client_fn):
        """Default (no thinking) still uses the Sonnet default, not THINKING_MODEL."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude, THINKING_MODEL
        with patch("storage.ai_menu_extract._write_debug_log"):
            extract_menu_items_via_claude("menu text")

        kwargs = client.messages.stream.call_args.kwargs
        self.assertNotEqual(kwargs["model"], THINKING_MODEL)
        self.assertIn("sonnet", kwargs["model"])
        self.assertEqual(kwargs["temperature"], 0)
        self.assertNotIn("thinking", kwargs)

    @patch("storage.ai_menu_extract.EXTENDED_THINKING", True)
    @patch("storage.ai_menu_extract._get_client")
    def test_explicit_model_ignored_when_thinking(self, mock_client_fn):
        """Even if caller passes model=X, thinking overrides to THINKING_MODEL."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response(_SAMPLE_ITEMS_JSON, include_thinking=True))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude, THINKING_MODEL
        with patch("storage.ai_menu_extract._write_debug_log"):
            extract_menu_items_via_claude("menu text", model="claude-opus-4-6",
                                          use_thinking=True)

        kwargs = client.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["model"], THINKING_MODEL)


# ---------------------------------------------------------------------------
# 3. _write_debug_log creates file with expected fields
# ---------------------------------------------------------------------------
class TestWriteDebugLog(unittest.TestCase):
    """_write_debug_log() writes JSON to storage/logs/."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_file_with_fields(self):
        from storage.ai_menu_extract import _write_debug_log
        with patch("storage.ai_menu_extract._LOGS_DIR", self.tmpdir):
            path = _write_debug_log(
                model="claude-sonnet-4-6",
                thinking_active=True,
                multimodal=True,
                ocr_text_length=5000,
                image_blocks_count=1,
                api_kwargs_summary={"model": "claude-sonnet-4-6", "temperature": 1},
                stop_reason="end_turn",
                input_tokens=500,
                output_tokens=200,
                block_types=["thinking", "text"],
                thinking_chars=1500,
                response_text='{"items": []}',
                parsed_item_count=0,
            )

        self.assertIsNotNone(path)
        self.assertTrue(os.path.exists(path))

        with open(path, "r") as f:
            data = json.load(f)

        # Top-level fields
        self.assertEqual(data["model"], "claude-sonnet-4-6")
        self.assertTrue(data["thinking_active"])
        self.assertTrue(data["multimodal"])
        self.assertEqual(data["ocr_text_length"], 5000)
        self.assertEqual(data["image_blocks_count"], 1)

        # Response fields
        self.assertEqual(data["response"]["stop_reason"], "end_turn")
        self.assertEqual(data["response"]["input_tokens"], 500)
        self.assertEqual(data["response"]["output_tokens"], 200)
        self.assertEqual(data["response"]["block_types"], ["thinking", "text"])
        self.assertEqual(data["response"]["thinking_chars"], 1500)

        # Result
        self.assertEqual(data["result"]["parsed_item_count"], 0)
        self.assertIsNone(data["result"]["error"])

    def test_creates_directory_if_missing(self):
        from storage.ai_menu_extract import _write_debug_log
        nested = os.path.join(self.tmpdir, "subdir", "logs")
        with patch("storage.ai_menu_extract._LOGS_DIR", nested):
            path = _write_debug_log(
                model="test", thinking_active=False, multimodal=False,
                ocr_text_length=0, image_blocks_count=0,
                api_kwargs_summary={},
            )
        self.assertIsNotNone(path)
        self.assertTrue(os.path.isdir(nested))

    def test_error_field_captured(self):
        from storage.ai_menu_extract import _write_debug_log
        with patch("storage.ai_menu_extract._LOGS_DIR", self.tmpdir):
            path = _write_debug_log(
                model="test", thinking_active=False, multimodal=False,
                ocr_text_length=100, image_blocks_count=0,
                api_kwargs_summary={},
                error="json_parse_error: Expecting value",
            )

        with open(path, "r") as f:
            data = json.load(f)
        self.assertEqual(data["result"]["error"], "json_parse_error: Expecting value")
        self.assertIsNone(data["result"]["parsed_item_count"])

    def test_response_text_truncated_at_2000(self):
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
        self.assertEqual(len(data["response"]["response_text_preview"]), 2000)

    def test_filename_pattern(self):
        from storage.ai_menu_extract import _write_debug_log
        with patch("storage.ai_menu_extract._LOGS_DIR", self.tmpdir):
            path = _write_debug_log(
                model="test", thinking_active=False, multimodal=False,
                ocr_text_length=0, image_blocks_count=0,
                api_kwargs_summary={},
            )
        basename = os.path.basename(path)
        self.assertTrue(basename.startswith("call1_debug_"))
        self.assertTrue(basename.endswith(".json"))

    def test_returns_none_on_write_failure(self):
        from storage.ai_menu_extract import _write_debug_log
        # Point to an invalid path (file as dir)
        fake_file = os.path.join(self.tmpdir, "not_a_dir")
        with open(fake_file, "w") as f:
            f.write("block")
        invalid_dir = os.path.join(fake_file, "logs")
        with patch("storage.ai_menu_extract._LOGS_DIR", invalid_dir):
            result = _write_debug_log(
                model="test", thinking_active=False, multimodal=False,
                ocr_text_length=0, image_blocks_count=0,
                api_kwargs_summary={},
            )
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 4. Debug log integration — called during extraction
# ---------------------------------------------------------------------------
class TestDebugLogIntegration(unittest.TestCase):
    """Debug log is called after every API response (success or failure)."""

    @patch("storage.ai_menu_extract._write_debug_log")
    @patch("storage.ai_menu_extract._get_client")
    def test_debug_log_called_on_success(self, mock_client_fn, mock_log):
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("menu text")

        self.assertIsNotNone(result)
        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["parsed_item_count"], 2)
        self.assertIsNone(kwargs.get("error"))

    @patch("storage.ai_menu_extract._write_debug_log")
    @patch("storage.ai_menu_extract._get_client")
    def test_debug_log_called_on_json_error(self, mock_client_fn, mock_log):
        client = MagicMock()
        bad_response = _make_fake_response("NOT JSON AT ALL")
        client.messages.stream.return_value = _make_stream_cm(bad_response)
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("menu text")

        self.assertIsNone(result)
        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        self.assertIn("json_parse_error", kwargs["error"])

    @patch("storage.ai_menu_extract._write_debug_log")
    @patch("storage.ai_menu_extract._get_client")
    def test_debug_log_called_on_empty_response(self, mock_client_fn, mock_log):
        client = MagicMock()
        empty_resp = _make_fake_response("   ")
        client.messages.stream.return_value = _make_stream_cm(empty_resp)
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("menu text")

        self.assertIsNone(result)
        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["error"], "empty_response_text")

    @patch("storage.ai_menu_extract._write_debug_log")
    @patch("storage.ai_menu_extract._get_client")
    def test_debug_log_called_on_exception(self, mock_client_fn, mock_log):
        client = MagicMock()
        client.messages.stream.side_effect = RuntimeError("network failure")
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        result = extract_menu_items_via_claude("menu text")

        self.assertIsNone(result)
        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        self.assertIn("RuntimeError", kwargs["error"])

    @patch("storage.ai_menu_extract.EXTENDED_THINKING", True)
    @patch("storage.ai_menu_extract._write_debug_log")
    @patch("storage.ai_menu_extract._get_client")
    def test_debug_log_captures_thinking_model(self, mock_client_fn, mock_log):
        """Debug log records THINKING_MODEL when thinking is active."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response(_SAMPLE_ITEMS_JSON, include_thinking=True))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude, THINKING_MODEL
        extract_menu_items_via_claude("menu text", use_thinking=True)

        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["model"], THINKING_MODEL)
        self.assertTrue(kwargs["thinking_active"])

    @patch("storage.ai_menu_extract._write_debug_log")
    @patch("storage.ai_menu_extract._get_client")
    def test_debug_log_captures_block_types(self, mock_client_fn, mock_log):
        """Debug log records the type of each response block."""
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response(_SAMPLE_ITEMS_JSON, include_thinking=True))
        mock_client_fn.return_value = client

        from storage.ai_menu_extract import extract_menu_items_via_claude
        extract_menu_items_via_claude("menu text")

        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["block_types"], ["thinking", "text"])


# ---------------------------------------------------------------------------
# 5. _LOGS_DIR points to storage/logs/
# ---------------------------------------------------------------------------
class TestLogsDir(unittest.TestCase):
    def test_logs_dir_under_storage(self):
        from storage.ai_menu_extract import _LOGS_DIR
        self.assertTrue(_LOGS_DIR.replace("\\", "/").endswith("storage/logs"))


# ---------------------------------------------------------------------------
# 6. 3-call pipeline default unchanged
# ---------------------------------------------------------------------------
class TestPipelineDefaults(unittest.TestCase):
    """Default configuration still uses the 3-call Sonnet pipeline."""

    def test_extended_thinking_is_false(self):
        from storage.ai_menu_extract import EXTENDED_THINKING
        self.assertFalse(EXTENDED_THINKING)

    def test_default_model_is_sonnet_4_5(self):
        import inspect
        from storage.ai_menu_extract import extract_menu_items_via_claude
        sig = inspect.signature(extract_menu_items_via_claude)
        self.assertEqual(sig.parameters["model"].default, "claude-sonnet-4-5")

    def test_call2_exists(self):
        from storage.ai_vision_verify import _DEFAULT_MODEL
        self.assertIn("sonnet", _DEFAULT_MODEL)

    def test_call3_exists(self):
        from storage.ai_reconcile import _DEFAULT_MODEL
        self.assertIn("sonnet", _DEFAULT_MODEL)


# ---------------------------------------------------------------------------
# 7. api_kwargs_summary excludes messages (no image data in logs)
# ---------------------------------------------------------------------------
class TestDebugLogSanitization(unittest.TestCase):
    """Debug log doesn't include raw image data."""

    @patch("storage.ai_menu_extract._write_debug_log")
    @patch("storage.ai_menu_extract._get_encoder")
    @patch("storage.ai_menu_extract._get_client")
    def test_messages_excluded_from_kwargs_summary(self, mock_client_fn, mock_enc_fn, mock_log):
        client = MagicMock()
        client.messages.stream.return_value = _make_stream_cm(
            _make_fake_response(_SAMPLE_ITEMS_JSON))
        mock_client_fn.return_value = client
        mock_enc_fn.return_value = lambda p: [{"media_type": "image/jpeg", "data": "BIGDATA"}]

        from storage.ai_menu_extract import extract_menu_items_via_claude
        extract_menu_items_via_claude("text", image_path="/fake/img.jpg")

        kwargs = mock_log.call_args.kwargs
        summary = kwargs["api_kwargs_summary"]
        self.assertNotIn("messages", summary)
        self.assertIn("model", summary)
        self.assertIn("max_tokens", summary)


# ---------------------------------------------------------------------------
# 8. Portal wiring — use_thinking passed when EXTENDED_THINKING is on
# ---------------------------------------------------------------------------
class TestPortalWiring(unittest.TestCase):
    """portal/app.py passes use_thinking=_thinking_active to Call 1."""

    def test_portal_passes_use_thinking(self):
        """Verify the portal call includes use_thinking parameter."""
        import re
        portal_path = os.path.join(os.path.dirname(__file__), "..", "portal", "app.py")
        with open(portal_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Find the extract_menu_items_via_claude call
        match = re.search(
            r"extract_menu_items_via_claude\(.*?use_thinking\s*=\s*_thinking_active",
            content, re.DOTALL)
        self.assertIsNotNone(match, "portal/app.py should pass use_thinking=_thinking_active")


if __name__ == "__main__":
    unittest.main()
