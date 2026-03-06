# storage/ai_menu_extract.py
"""
Claude API Menu Extraction — multimodal menu item extraction.

Architecture: 3-call pipeline (extract → vision verify → reconcile).
Call 1 (this module): sends menu IMAGE + OCR hint for structured extraction.
Fallback mode: text-only extraction when no image is available.

Usage:
    from storage.ai_menu_extract import extract_menu_items_via_claude

    # Multimodal (preferred):
    items = extract_menu_items_via_claude(raw_ocr_text, image_path="/path/to/menu.jpg")

    # Text-only fallback:
    items = extract_menu_items_via_claude(raw_ocr_text)

Requires ANTHROPIC_API_KEY in environment (loaded via .env).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Lazy import — encode_menu_images is only needed when an image is provided
_encode_menu_images = None


def _get_encoder():
    """Lazy-load encode_menu_images from ai_vision_verify to avoid circular imports."""
    global _encode_menu_images
    if _encode_menu_images is not None:
        return _encode_menu_images
    try:
        from .ai_vision_verify import encode_menu_images
        _encode_menu_images = encode_menu_images
        return _encode_menu_images
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Claude API client (lazy init)
# ---------------------------------------------------------------------------
_client = None


def _get_client():
    """Lazy-init Anthropic client. Returns None if API key not set."""
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic
        _client = anthropic.Anthropic(api_key=api_key)
        return _client
    except Exception as e:
        log.warning("Failed to init Anthropic client: %s", e)
        return None


# ---------------------------------------------------------------------------
# Prompt — structured extraction for POS import
# ---------------------------------------------------------------------------
_EXTRACTION_GOAL = """\
Extract every orderable item from this restaurant menu for POS system import.

Each item = one product a customer can order in a POS system.
Split compound items: "Beef or Chicken Empanadas" → two separate items.
Individual toppings, sauces, and dressings each get their own item.
Section-wide choices (e.g., "Naked or Breaded", "White or Wheat") → size variants on each item, not descriptions.
Section-wide notes (e.g., "All sandwiches come with lettuce, tomato...") → include in each item's description.
Use Title Case for names even if the menu is printed in ALL CAPS.

For each item return:
- "name": exact full name as printed on the menu
- "description": menu description if shown, null otherwise
- "price": price as float, 0 if not visible
- "category": one of: Pizza, Toppings, Appetizers, Salads, Soups, Sandwiches, \
Burgers, Wraps, Entrees, Seafood, Pasta, Steaks, Wings, Sauces, Sides, \
Desserts, Beverages, Kids Menu, Breakfast, Calzones, Subs, Platters, Other
- "sizes": [{"label": "...", "price": N.NN}] for items with multiple price points

Output ONLY valid JSON: {"items": [...]}"""

# -- Multimodal preamble (used when image is available) -------------------------
_MULTIMODAL_PREAMBLE = """\
You are extracting menu items from a restaurant menu image for POS import.
Read the image carefully — it is the primary source of truth.
OCR text is provided as a hint but often contains errors — always trust the image.

"""

# -- Text-only preamble (fallback when no image available) ----------------------
_TEXT_ONLY_PREAMBLE = """\
You are extracting menu items from OCR text of a restaurant menu for POS import.
The text may contain OCR artifacts and formatting noise — use your judgment.

"""

# Consolidated system prompts
_SYSTEM_PROMPT_MULTIMODAL = _MULTIMODAL_PREAMBLE + _EXTRACTION_GOAL
_SYSTEM_PROMPT_TEXT_ONLY = _TEXT_ONLY_PREAMBLE + _EXTRACTION_GOAL

_USER_PROMPT_TEMPLATE = """\
Extract menu items from this OCR text:

---
{ocr_text}
---

Return JSON: {{"items": [{{"name": "...", "description": "...", "price": 0.00, "category": "...", "sizes": []}}]}}"""

_USER_PROMPT_MULTIMODAL_TEMPLATE = """\
Extract every menu item from the menu image above.

The following OCR text was extracted from the same image and may help \
disambiguate hard-to-read areas, but the image is the source of truth:

---
{ocr_text}
---

Return JSON: {{"items": [{{"name": "...", "description": "...", "price": 0.00, "category": "...", "sizes": []}}]}}"""


# ---------------------------------------------------------------------------
# Category normalizer — code-level guardrail (deterministic, no AI needed)
# ---------------------------------------------------------------------------
VALID_CATEGORIES = frozenset([
    "Pizza", "Toppings", "Appetizers", "Salads", "Soups", "Sandwiches",
    "Burgers", "Wraps", "Entrees", "Seafood", "Pasta", "Steaks", "Wings",
    "Sauces", "Sides", "Desserts", "Beverages", "Kids Menu", "Breakfast",
    "Calzones", "Subs", "Platters", "Other",
])

_CATEGORY_ALIASES: Dict[str, str] = {
    # Common menu section headings → canonical POS category
    "club sandwiches": "Sandwiches",
    "melt sandwiches": "Sandwiches",
    "melts": "Sandwiches",
    "clubs": "Sandwiches",
    "hot sandwiches": "Sandwiches",
    "cold sandwiches": "Sandwiches",
    "gourmet pizza": "Pizza",
    "specialty pizza": "Pizza",
    "brick oven pizza": "Pizza",
    "wraps city": "Wraps",
    "fresh buffalo wings": "Wings",
    "buffalo wings": "Wings",
    "chicken wings": "Wings",
    "fresh soups": "Soups",
    "homemade soups": "Soups",
    "hot subs": "Subs",
    "cold subs": "Subs",
    "hot heroes": "Subs",
    "cold heroes": "Subs",
    "dinner entrees": "Entrees",
    "dinner platters": "Platters",
    "kid's menu": "Kids Menu",
    "kids": "Kids Menu",
    "children's menu": "Kids Menu",
    "dessert": "Desserts",
    "drink": "Beverages",
    "drinks": "Beverages",
    "beverage": "Beverages",
    "salad": "Salads",
    "soup": "Soups",
    "burger": "Burgers",
    "wrap": "Wraps",
    "calzone": "Calzones",
    "sub": "Subs",
    "topping": "Toppings",
    "sauce": "Sauces",
    "side": "Sides",
    "steak": "Steaks",
    "wing": "Wings",
    "entree": "Entrees",
    "platter": "Platters",
    "appetizer": "Appetizers",
}


def _normalize_category(cat: str) -> str:
    """Map a category string to the canonical whitelist value.

    Handles raw section headings, common aliases, and fuzzy substring matches.
    Always returns a valid category from VALID_CATEGORIES.
    """
    if not cat:
        return "Other"
    cat = cat.strip()
    if cat in VALID_CATEGORIES:
        return cat
    # Exact alias lookup (case-insensitive)
    lower = cat.lower()
    if lower in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[lower]
    # Substring match: check if any valid category appears in the string
    for valid in VALID_CATEGORIES:
        if valid.lower() in lower:
            return valid
    return "Other"


# ---------------------------------------------------------------------------
# Extended thinking configuration
# ---------------------------------------------------------------------------
EXTENDED_THINKING = True  # A/B test: single-call Sonnet 4.6 + adaptive thinking
THINKING_MODEL = "claude-opus-4-6"  # Model used when EXTENDED_THINKING=True


# ---------------------------------------------------------------------------
# File-based debug logging — captures full API response for post-mortem analysis
# ---------------------------------------------------------------------------
_LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")


def _write_debug_log(
    *,
    model: str,
    thinking_active: bool,
    multimodal: bool,
    ocr_text_length: int,
    image_blocks_count: int,
    api_kwargs_summary: Dict[str, Any],
    stop_reason: str = "unknown",
    input_tokens: Any = "?",
    output_tokens: Any = "?",
    block_types: List[str] | None = None,
    thinking_chars: int = 0,
    thinking_text: str = "",
    response_text: str = "",
    parsed_item_count: int | None = None,
    items_manifest: List[Dict[str, Any]] | None = None,
    category_breakdown: Dict[str, int] | None = None,
    error: str | None = None,
) -> str | None:
    """Write a JSON debug log for a Call 1 API interaction.

    Captures the full Opus reasoning (thinking_text) and a compact manifest
    of every extracted item so post-mortem analysis can see exactly what
    happened without re-running the API call.

    Returns the path to the written file, or None on failure.
    """
    try:
        os.makedirs(_LOGS_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"call1_debug_{ts}.json"
        path = os.path.join(_LOGS_DIR, filename)
        entry = {
            "timestamp": ts,
            "model": model,
            "thinking_active": thinking_active,
            "multimodal": multimodal,
            "ocr_text_length": ocr_text_length,
            "image_blocks_count": image_blocks_count,
            "api_kwargs": api_kwargs_summary,
            "response": {
                "stop_reason": stop_reason,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "block_types": block_types or [],
                "thinking_chars": thinking_chars,
                "thinking_text": thinking_text,
                "response_text_preview": response_text[:500],
                "response_text_length": len(response_text),
            },
            "result": {
                "parsed_item_count": parsed_item_count,
                "category_breakdown": category_breakdown,
                "items_manifest": items_manifest,
                "error": error,
            },
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2, default=str)
        print(f"[Call 1] Debug log written: {path}")
        return path
    except Exception as exc:
        print(f"[Call 1] Failed to write debug log: {exc}")
        return None


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------
def extract_menu_items_via_claude(
    ocr_text: str,
    *,
    image_path: Optional[str] = None,
    model: str = "claude-sonnet-4-5",
    max_tokens: int = 16000,
    use_thinking: bool = False,
) -> Optional[List[Dict[str, Any]]]:
    """Extract structured menu items via Claude API.

    Multimodal mode (preferred): when *image_path* is provided and the image
    can be encoded, the menu image is sent as the primary input and the OCR
    text is included only as a disambiguation hint.

    Text-only mode (fallback): when no image is available, the raw OCR text
    is sent as the sole input.

    Returns a list of item dicts on success, or None if the API is unavailable
    or the call fails (so the caller can fall back gracefully).
    """
    client = _get_client()
    if client is None:
        log.info("No Anthropic API key configured; skipping Claude extraction")
        return None

    if not ocr_text or not ocr_text.strip():
        # In multimodal mode we still need *some* text to include as hint;
        # if the OCR produced nothing, send a minimal placeholder.
        if image_path:
            ocr_text = "(OCR produced no text)"
        else:
            return None

    # Truncate extremely long text to stay within token limits
    # ~4 chars per token, leave room for system prompt + response
    max_chars = 30_000
    text = ocr_text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... truncated ...]"

    # --- Determine mode: multimodal vs text-only ---
    image_blocks: List[Dict[str, str]] = []
    multimodal = False
    if image_path:
        encoder = _get_encoder()
        print(f"[Call 1] image_path={image_path}, encoder={'loaded' if encoder else 'FAILED'}")
        if encoder:
            try:
                image_blocks = encoder(image_path)
                print(f"[Call 1] encode_menu_images returned {len(image_blocks)} image block(s)")
            except Exception as _enc_err:
                print(f"[Call 1] encode_menu_images EXCEPTION: {_enc_err}")
                image_blocks = []
        if image_blocks:
            multimodal = True
            print(f"[Call 1] MULTIMODAL mode: {len(image_blocks)} image(s) + OCR hint ({len(text)} chars)")
        else:
            print(f"[Call 1] TEXT-ONLY fallback (image encode failed or returned empty)")
    else:
        print(f"[Call 1] TEXT-ONLY mode (no image_path provided)")

    thinking_active = use_thinking and EXTENDED_THINKING
    if thinking_active:
        # Override model to THINKING_MODEL when thinking is active
        model = THINKING_MODEL
        # max_tokens = total budget for thinking + response (combined).
        # budget_tokens caps thinking so the response has room.
        # Baseline needed ~12.5k tokens for 169-item JSON response.
        # 32k total - 10k thinking = 22k for response (ample headroom).
        max_tokens = 32000
        print(f"[Call 1] Extended thinking ENABLED (budget=10k, model={model})")

    # --- Build messages ---
    if multimodal:
        system_prompt = _SYSTEM_PROMPT_MULTIMODAL
        # Images first, then text prompt
        content: List[Dict[str, Any]] = []
        for img in image_blocks:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["media_type"],
                    "data": img["data"],
                },
            })
        content.append({
            "type": "text",
            "text": _USER_PROMPT_MULTIMODAL_TEMPLATE.format(ocr_text=text),
        })
        messages = [{"role": "user", "content": content}]
    else:
        system_prompt = _SYSTEM_PROMPT_TEXT_ONLY
        messages = [
            {"role": "user", "content": _USER_PROMPT_TEMPLATE.format(ocr_text=text)},
        ]

    # --- Build API kwargs ---
    api_kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": messages,
    }
    if thinking_active:
        api_kwargs["temperature"] = 1  # required for extended thinking
        # "enabled" + budget_tokens: guarantees thinking IS used, but CAPS it.
        # "adaptive" mode let the model spend ALL max_tokens on thinking
        # (68k thinking chars, 0 text response) — budget_tokens prevents that.
        api_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 10000}
    else:
        api_kwargs["temperature"] = 0

    # --- Shared debug log kwargs (populated after API call) ---
    _debug_base = {
        "model": model,
        "thinking_active": thinking_active,
        "multimodal": multimodal,
        "ocr_text_length": len(text),
        "image_blocks_count": len(image_blocks),
        "api_kwargs_summary": {
            k: v for k, v in api_kwargs.items()
            if k not in ("messages",)  # exclude bulky image data
        },
    }

    try:
        # Use streaming — required for Opus + thinking which can exceed 10 min
        print("[Call 1] Streaming API call started...")
        with client.messages.stream(**api_kwargs) as stream:
            message = stream.get_final_message()

        # Debug: show response metadata
        stop = getattr(message, "stop_reason", "unknown")
        usage = getattr(message, "usage", None)
        in_tok = getattr(usage, "input_tokens", "?") if usage else "?"
        out_tok = getattr(usage, "output_tokens", "?") if usage else "?"
        n_blocks = len(message.content) if message.content else 0
        print(f"[Call 1] Response received: stop_reason={stop}, "
              f"blocks={n_blocks}, input_tokens={in_tok}, output_tokens={out_tok}")

        # Extract text from response (skip thinking blocks)
        resp_text = ""
        thinking_chars = 0
        thinking_text = ""
        block_types: List[str] = []
        for block in message.content:
            block_type = getattr(block, "type", None)
            block_types.append(block_type or "unknown")
            if block_type == "thinking":
                t = getattr(block, "thinking", "")
                thinking_chars += len(t)
                thinking_text += t
            elif hasattr(block, "text"):
                resp_text += block.text

        if thinking_chars > 0:
            print(f"[Call 1] Thinking: {thinking_chars} chars")

        print(f"[Call 1] Response text: {len(resp_text)} chars")
        if resp_text:
            print(f"[Call 1] First 200 chars: {resp_text[:200]!r}")

        if not resp_text.strip():
            print("[Call 1] ERROR: Claude returned empty response text")
            _write_debug_log(
                **_debug_base, stop_reason=stop, input_tokens=in_tok,
                output_tokens=out_tok, block_types=block_types,
                thinking_chars=thinking_chars, thinking_text=thinking_text,
                response_text=resp_text,
                error="empty_response_text",
            )
            return None

        # Parse JSON from response (handle markdown code blocks if present)
        json_str = resp_text.strip()
        # Strip markdown code fences if Claude wrapped it
        if json_str.startswith("```"):
            json_str = re.sub(r"^```(?:json)?\s*\n?", "", json_str)
            json_str = re.sub(r"\n?```\s*$", "", json_str)

        data = json.loads(json_str)

        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            print(f"[Call 1] ERROR: missing 'items' list. Keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
            _write_debug_log(
                **_debug_base, stop_reason=stop, input_tokens=in_tok,
                output_tokens=out_tok, block_types=block_types,
                thinking_chars=thinking_chars, thinking_text=thinking_text,
                response_text=resp_text,
                error="missing_items_list",
            )
            return None

        # Normalize items + code-level category guardrail
        result = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = (it.get("name") or "").strip()
            if not name:
                continue
            result.append({
                "name": name,
                "description": (it.get("description") or "").strip() or None,
                "price": _to_float(it.get("price")),
                "category": _normalize_category(it.get("category") or "Other"),
                "sizes": _normalize_sizes(it.get("sizes")),
            })

        mode_label = "multimodal+thinking" if (multimodal and thinking_active) else \
                     "multimodal" if multimodal else "text-only"
        print(f"[Call 1] SUCCESS: {len(result)} items extracted ({mode_label})")

        # Build compact items manifest + category breakdown for debug log
        _manifest = [
            {"name": it["name"], "category": it["category"],
             "price": it["price"], "n_sizes": len(it.get("sizes", []))}
            for it in result
        ]
        _cat_counts: Dict[str, int] = {}
        for it in result:
            c = it["category"]
            _cat_counts[c] = _cat_counts.get(c, 0) + 1

        _write_debug_log(
            **_debug_base, stop_reason=stop, input_tokens=in_tok,
            output_tokens=out_tok, block_types=block_types,
            thinking_chars=thinking_chars, thinking_text=thinking_text,
            response_text=resp_text,
            parsed_item_count=len(result),
            items_manifest=_manifest,
            category_breakdown=_cat_counts,
        )
        return result if result else None

    except json.JSONDecodeError as e:
        print(f"[Call 1] JSON PARSE ERROR: {e}")
        if resp_text:
            print(f"[Call 1] Last 200 chars: {resp_text[-200:]!r}")
        _write_debug_log(
            **_debug_base, stop_reason=stop, input_tokens=in_tok,
            output_tokens=out_tok, block_types=block_types,
            thinking_chars=thinking_chars, thinking_text=thinking_text,
            response_text=resp_text,
            error=f"json_parse_error: {e}",
        )
        return None
    except Exception as e:
        print(f"[Call 1] EXCEPTION: {type(e).__name__}: {e}")
        _write_debug_log(**_debug_base, error=f"{type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_float(val) -> float:
    """Safely convert a value to float, defaulting to 0.0."""
    if val is None:
        return 0.0
    try:
        return round(float(val), 2)
    except (ValueError, TypeError):
        return 0.0


def _normalize_sizes(sizes) -> List[Dict[str, Any]]:
    """Normalize size/price entries from Claude's response."""
    if not isinstance(sizes, list):
        return []
    result = []
    for s in sizes:
        if not isinstance(s, dict):
            continue
        label = (s.get("label") or s.get("name") or "").strip()
        price = _to_float(s.get("price"))
        if label or price > 0:
            result.append({"label": label, "price": price})
    return result


# ---------------------------------------------------------------------------
# Convert Claude items to draft DB rows
# ---------------------------------------------------------------------------
def claude_items_to_draft_rows(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert Claude-extracted items to the draft_items DB row format.

    Returns list of dicts ready for drafts_store.upsert_draft_items().
    Each item may include a '_variants' key with structured variant data
    that upsert_draft_items() will insert into draft_item_variants.
    """
    rows = []
    for pos, it in enumerate(items, start=1):
        name = (it.get("name") or "").strip()
        if not name:
            continue

        price = _to_float(it.get("price"))
        price_cents = int(round(price * 100))

        # Build structured variants from sizes
        sizes = it.get("sizes") or []
        variants: List[Dict[str, Any]] = []
        if sizes:
            for vi, s in enumerate(sizes):
                lbl = (s.get("label") or s.get("name") or "").strip()
                pr = _to_float(s.get("price"))
                pr_cents = int(round(pr * 100))
                if lbl or pr_cents > 0:
                    variants.append({
                        "label": lbl or f"Size {vi + 1}",
                        "price_cents": pr_cents,
                        "kind": "size",
                        "position": vi,
                    })
            # Use first size price as base if base price is 0
            if price_cents == 0 and variants:
                price_cents = variants[0]["price_cents"]

        row: Dict[str, Any] = {
            "name": name,
            "description": it.get("description"),
            "price_cents": price_cents,
            "category": it.get("category") or "Other",
            "position": pos,
            "confidence": 90,  # Claude extractions are high-confidence
        }
        if variants:
            row["_variants"] = variants
        rows.append(row)
    return rows
