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
Each named topping (Pepperoni, Mushrooms, etc.) and each named sauce (Ranch, BBQ, etc.) \
is a separate item — do not combine them into one "Each Topping Add" item. \
If the menu lists available toppings by name, create one item per topping with the per-topping price.
Sauce/flavor options listed within a section (e.g., "Hot, Mild, BBQ, Honey BBQ" under wings) \
should also be extracted as individual items in the Sauces category, not placed in descriptions.
When items are sold by quantity (e.g., wings: 6 Pcs, 10 Pcs, 20 Pcs), each quantity is its own item — \
do not collapse them into one item with quantity-based size variants.
Pay close attention to section headers — they often contain info that applies to every item below:
- Shared pricing (e.g., "Wraps — Regular $10 / W/ Fries $14") → apply as size variants to all items in that section.
- Shared options (e.g., "White or Wheat", "Naked or Breaded") → add as size variants on each item, not in descriptions.
- Shared descriptions (e.g., "All sandwiches come with lettuce, tomato...") → include in each item's description.
- Multiple price columns (e.g., "Regular/Deluxe", "W/Fries", "W/Cheese") → capture each column as a size variant.
Use Title Case for names even if the menu is printed in ALL CAPS.

IMPORTANT: Descriptions shift easily — menus often print the description on the line below \
the item name, so it looks like it belongs to the next item down. Before assigning a description, \
verify it makes sense for that item (e.g., a Veggie Calzone should not have "Grilled Chicken" \
in its description). If uncertain, set description to null — a missing description is better \
than a wrong one.
Do not skip items — check every section of the menu for completeness.

For each item return:
- "name": exact full name as printed on the menu
- "description": menu description if shown, null otherwise
- "price": price as float, 0 if not visible
- "category": one of: Pizza, Toppings, Appetizers, Salads, Soups, Sandwiches, \
Burgers, Wraps, Entrees, Seafood, Pasta, Steaks, Wings, Sauces, Sides, \
Desserts, Beverages, Kids Menu, Breakfast, Calzones, Subs, Platters, Other
- "sizes": [{"label": "...", "price": N.NN}] for items with multiple price points
- "modifier_groups": groups of options/add-ons attached to this item. Use when the menu \
shows explicit option groups with names (e.g., "Sauce Choice", "Bread", "Add-Ons"). \
Each group: {"name": "...", "required": true/false, "min_select": 0, "max_select": 0, \
"modifiers": [{"label": "...", "price": N.NN}]}. \
Omit "modifier_groups" entirely if no named option groups are shown.

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

Return JSON: {{"items": [{{"name": "...", "description": "...", "price": 0.00, "category": "...", "sizes": [], "modifier_groups": []}}]}}"""

_USER_PROMPT_MULTIMODAL_TEMPLATE = """\
Extract every menu item from the menu image above.

The following OCR text was extracted from the same image and may help \
disambiguate hard-to-read areas, but the image is the source of truth:

---
{ocr_text}
---

Return JSON: {{"items": [{{"name": "...", "description": "...", "price": 0.00, "category": "...", "sizes": [], "modifier_groups": []}}]}}"""


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
# Post-processing: description-name mismatch detector
# ---------------------------------------------------------------------------
# Catches systematic description shifting (e.g., veggie item with steak desc)
_MEAT_TERMS = {"steak", "beef", "chicken", "bacon", "ham", "sausage",
               "pepperoni", "hamburger", "meatball", "gyro"}
_VEGGIE_NAMES = {"veggie", "vegetable", "vegan"}


def _validate_descriptions(items: List[Dict[str, Any]]) -> int:
    """Null out descriptions that obviously don't match the item name.

    Returns the number of descriptions that were nulled.
    """
    fixed = 0
    for it in items:
        desc = it.get("description")
        if not desc:
            continue
        name_lower = it["name"].lower()
        desc_lower = desc.lower()

        # Veggie items should not have meat in their description
        if any(v in name_lower for v in _VEGGIE_NAMES):
            if any(m in desc_lower for m in _MEAT_TERMS):
                it["description"] = None
                fixed += 1
                continue

    return fixed


# ---------------------------------------------------------------------------
# Pipeline mode configuration
# ---------------------------------------------------------------------------
# "thinking" = single Opus call with extended thinking (skips Calls 2 & 3)
# "3call"    = full 3-call pipeline: Call 1 → Call 2 (vision) → Call 3 (reconcile)
PIPELINE_MODE = "thinking"  # Day 140: Opus + thinking required — Sonnet drops too many items
EXTENDED_THINKING = PIPELINE_MODE == "thinking"
THINKING_MODEL = "claude-opus-4-6"  # Model used when PIPELINE_MODE == "thinking"

# Day 140: Pipeline speed optimization — skip redundant calls when Vision OCR
# provides accurate text.  Set to True to bypass each call.
SKIP_CALL2 = True   # Skip per-category visual verification (Call 2)
SKIP_CALL3 = True   # Skip targeted reconciliation (Call 3)


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
                "response_text_full": response_text,
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
        # Day 102.8 finding: "adaptive" without budget lets Opus spend ALL 32k
        # tokens on thinking (0 chars response). budget_tokens prevents that.
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

        # Post-processing: catch description-name mismatches
        n_fixed = _validate_descriptions(result)
        if n_fixed:
            print(f"[Call 1] Description validator: nulled {n_fixed} mismatched description(s)")

        mode_label = "multimodal+thinking" if (multimodal and thinking_active) else \
                     "multimodal" if multimodal else "text-only"
        print(f"[Call 1] SUCCESS: {len(result)} items extracted ({mode_label})")

        # Build items manifest + category breakdown for debug log
        _manifest = []
        for it in result:
            entry = {
                "name": it["name"],
                "category": it["category"],
                "price": it["price"],
                "n_sizes": len(it.get("sizes", [])),
            }
            desc = (it.get("description") or "")[:80]
            if desc:
                entry["desc"] = desc
            sizes = it.get("sizes", [])
            if sizes:
                entry["sizes"] = [
                    {"label": s.get("label", ""), "price": s.get("price", 0)}
                    for s in sizes
                ]
            _manifest.append(entry)
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
def _build_modifier_groups_from_claude(
    raw_groups: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert Claude modifier_groups list to _modifier_groups format.

    Each output group has: name, required, min_select, max_select, position,
    and _modifiers list with: label, price_cents, kind, position.
    """
    result = []
    for gi, g in enumerate(raw_groups):
        if not isinstance(g, dict):
            continue
        name = (g.get("name") or "").strip()
        if not name:
            continue
        required = bool(g.get("required", False))
        try:
            min_select = int(g.get("min_select") or 0)
        except (ValueError, TypeError):
            min_select = 0
        try:
            max_select = int(g.get("max_select") or 0)
        except (ValueError, TypeError):
            max_select = 0

        raw_mods = g.get("modifiers") or []
        modifiers: List[Dict[str, Any]] = []
        for mi, m in enumerate(raw_mods):
            if not isinstance(m, dict):
                continue
            lbl = (m.get("label") or m.get("name") or "").strip()
            pr = _to_float(m.get("price"))
            pr_cents = int(round(pr * 100))
            if not lbl:
                continue
            modifiers.append({
                "label": lbl,
                "price_cents": pr_cents,
                "kind": "other",
                "position": mi,
            })

        result.append({
            "name": name,
            "required": required,
            "min_select": min_select,
            "max_select": max_select,
            "position": gi,
            "_modifiers": modifiers,
        })
    return result


def claude_items_to_draft_rows(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert Claude-extracted items to the draft_items DB row format.

    Returns list of dicts ready for drafts_store.upsert_draft_items().
    Each item may include:
      - '_variants' key with flat variant data (from "sizes" in Claude output)
      - '_modifier_groups' key with named option groups (from "modifier_groups")

    If modifier_groups are present, sizes are still converted to _variants
    (ungrouped) for backward compat with items that mix both.
    """
    rows = []
    for pos, it in enumerate(items, start=1):
        name = (it.get("name") or "").strip()
        if not name:
            continue

        price = _to_float(it.get("price"))
        price_cents = int(round(price * 100))

        # Build structured variants from sizes (ungrouped, backward compat)
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

        # Build modifier groups (named option groups from Claude)
        raw_groups = it.get("modifier_groups") or []
        modifier_groups = _build_modifier_groups_from_claude(raw_groups)

        subcategory = (it.get("subcategory") or "").strip() or None

        row: Dict[str, Any] = {
            "name": name,
            "description": it.get("description"),
            "price_cents": price_cents,
            "category": it.get("category") or "Other",
            "subcategory": subcategory,
            "position": pos,
            "confidence": 90,  # Claude extractions are high-confidence
        }
        if variants:
            row["_variants"] = variants
        if modifier_groups:
            row["_modifier_groups"] = modifier_groups
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Day 139: Detect + Classify + Locate — new pipeline Call 1
# ---------------------------------------------------------------------------
# This prompt asks Claude to detect every piece of text on the menu and
# classify what it IS, plus return approximate bounding box percentages.
# It does NOT build parent/child structure — that's done in code (Day 140).

_DETECT_SYSTEM_PROMPT = """\
You are extracting menu items from a restaurant menu image for POS system import.

#1 RULE — THE OCR TEXT IS THE ABSOLUTE SOURCE OF TRUTH FOR ALL PRICES AND NAMES.
The OCR text was produced by a high-accuracy document scanner. Every price in it is \
correct. Your job is to use the image for STRUCTURE (what is a section header, which \
items belong to which category, layout) and the OCR text for DATA (names, prices, \
descriptions). When you output a price, it MUST match a price in the OCR text. \
Do NOT change, round, smooth, or "correct" any price from the OCR text. \
Do NOT substitute a neighbor's price for consistency. \
If the OCR says an item is $11.99, you output $11.99 — even if every other item \
in that section is $9.99. The OCR is right. You are not allowed to override it.

THIS IS CRITICAL — PRICE ACCURACY IS THE ENTIRE POINT OF THIS SERVICE. \
If you glaze over prices or generalize "all items in this section are $X.XX" \
instead of reading each line individually, the output is USELESS and the \
service fails. Real menus have outlier prices — one item in a group of 7 \
will cost more or less than the rest. You MUST find those differences. \
Read EVERY line in the OCR text as its own independent lookup. \
Never assume. Never generalize. Never smooth.

WHAT IS AN ITEM:
Each item = one product a customer can order. Rules:
- Split compound items: "Beef or Chicken Empanadas" → two items.
- Quantity variants (6 Pcs, 10 Pcs) are separate items.
- Section headers (bold/large/centered text like "GOURMET PIZZA") are NOT items.
- Use Title Case for names even if menu is ALL CAPS.
- Extract EVERY item. Missing even one is a failure. Go line by line.

MODIFIERS, TOPPINGS, & SAUCES — USE "subcategory" FIELD:
All of these are POS add-on options. Extract each as a separate item in the \
SAME category they appear under on the menu. Set "subcategory" to group them. \
Rules:
- Named toppings (Pepperoni, Mushrooms, Bacon, etc.) → each is a separate \
item with the add-on price. Do NOT collapse into "Each Topping Add". \
Category = their parent section (e.g., "Pizza"), subcategory = "Toppings". \
Example: "TOPPINGS Sm $1.50 Lg $2.00" with "Pepperoni, Mushrooms, Bacon" → \
3 items, category "Pizza", subcategory "Toppings", each with sizes.
- Sauce flavors (Hot, Mild, BBQ, Honey BBQ, etc.) → each a separate $0 item. \
Category = parent section (e.g., "Wings"), subcategory = "Wing Sauces".
- "Choice of Sauce: Red, White, Pesto..." → each a separate $0 item. \
Category = parent section (e.g., "Pizza"), subcategory = "Sauce Options".
- "Choice of Rye, White" → each a $0 item. Category = parent section, \
subcategory = "Bread Options".
- "Add Bacon $1 extra" → item at stated price. Category = parent section, \
subcategory = "Add-Ons".

"W/ CHEESE" AND ADD-ON VARIANTS:
- When a base item has a "W/ Cheese" version at a higher price, these are \
ONE item with a size variant, NOT two separate items. \
Example: "French Fries $6.00 / French Fries W/ Cheese $8.95" → \
one item "French Fries" with sizes [{label: "Regular", price: 6.00}, \
{label: "W/ Cheese", price: 8.95}]. Same for Curly Fries, Garlic Bread, etc.

CATEGORIES — USE THE MENU'S OWN SECTION HEADERS:
- The "category" field MUST match the menu's printed section header. \
If the menu says "APPETIZERS" and lists French Fries under it, the category \
is "Appetizers" — do NOT reclassify items into "Sides" or other categories \
that don't appear on the menu. Use the menu's own organization.

SECTION STRUCTURE (use the IMAGE to determine):
- Each section has its own header and its own price columns. \
Reset column understanding at each new section.
- Some sections show shared prices once at top (in a banner or header row) \
with items listed below having no individual prices — apply those shared \
prices as size variants to every item in that section.
- Multi-column menus: each column has its own headers. A header in the \
right column does not apply to items in the left column.
- Shared descriptions ("All sandwiches come with...", "All calzones stuffed with...") \
apply to EVERY item in that section as part of each item's description.
- "Choice of" lists are POS modifier options. Extract each choice as a \
separate $0 item in the SAME category where it appears on the menu, with \
"subcategory" set to a descriptive group (e.g., "Sauce Options", "Bread Options"). \
Examples: "Choice of Sauce: Red, White, Pesto" under Pizza → 3 items in \
category "Pizza", subcategory "Sauce Options", price $0.

PRICES — READ EACH LINE INDEPENDENTLY (THIS IS YOUR CORE JOB):
- Go through the OCR text LINE BY LINE. For EACH item, find its SPECIFIC line in \
the OCR text and read the price(s) from THAT line. Do not batch-process sections. \
Do not assume a section has uniform pricing. Treat every single line as if it \
could have a completely different price than its neighbors — because it often does.
- PRICE SMOOTHING IS THE #1 ERROR AND WILL BREAK THE OUTPUT. When you see a group \
of items (like 7 melts), do NOT think "they're all $9.95." Go line by line: \
find each item's name in the OCR text, read the digits after it. If one says \
11.95 while the rest say 9.95, output 11.95. The OCR is always right.
- DOT LEADERS: Menus use dots/periods (".......") to connect an item name on the LEFT \
to its price(s) on the RIGHT of the same line. Example: \
"DOUBLE BURGER Lettuce, tomato...........11.99.......15.99" means Double Burger \
costs $11.99 (regular) and $15.99 (deluxe). The dots are just visual filler — \
the prices at the end of a line ALWAYS belong to the item at the start of that line.
- Description text on a line BELOW an item name is NOT a separate item. \
Lines without prices that describe ingredients belong to the item above.
- If a section header shows shared price columns, apply those to every item below.
- $0 means the price is truly not printed (e.g., "Market Price").

SIZE/VARIANT LABELS (use the OCR TEXT):
- Copy column header text VERBATIM from the OCR. Do not paraphrase. \
If OCR says "Mini" do not write "Med". If it says "Sml" keep "Sml".
- Sub-labels under column headers (like slice counts) are not separate sizes.

OUTPUT — return ONLY valid JSON:
{
  "items": [
    {
      "name": "Margherita Pizza",
      "description": "Fresh mozzarella, basil, tomato sauce",
      "price": 12.99,
      "category": "Pizza",
      "sizes": [
        {"label": "Small 10\\"", "price": 12.99},
        {"label": "Large 14\\"", "price": 16.99}
      ],
      "raw_line": "MARGHERITA PIZZA Fresh mozzarella, basil ....... 12.99 ....... 16.99",
      "bbox": {"x_pct": 5, "y_pct": 16, "w_pct": 30, "h_pct": 4, "page": 1},
      "subcategory": null
    }
  ]
}

ITEM FIELDS:
- "name": exact name from OCR text, Title Case
- "description": menu description if shown, null otherwise
- "price": base price as float (first size price if multi-size), 0 if not printed
- "category": ONE of: Pizza, Toppings, Appetizers, Salads, Soups, Sandwiches, \
Burgers, Wraps, Entrees, Seafood, Pasta, Steaks, Wings, Sauces, Sides, \
Desserts, Beverages, Kids Menu, Breakfast, Calzones, Subs, Platters, Other
- "subcategory": for modifiers/add-ons/toppings/sauces, a descriptive group \
name like "Toppings", "Sauce Options", "Bread Options", "Add-Ons", "Wing Sauces". \
null for regular orderable items.
- "sizes": array of {label, price} for multi-price items. Empty array if single price.
- "raw_line": the EXACT text you read from the menu for this item, including prices. \
Copy the line verbatim — this is used to verify your extraction is correct. \
If the section uses shared header prices, quote the header line instead.
- "bbox": approximate bounding box {x_pct, y_pct, w_pct, h_pct, page} as % of image. \
Err slightly larger. page is 1-based."""

_DETECT_USER_PROMPT = """\
Extract EVERY menu item — missing even one item is a failure. Go section by section, \
line by line. The OCR text below is from a high-accuracy scanner — \
every price and name in it is CORRECT. Use it as-is for all prices and names. \
Use the image ONLY for structure: sections, layout, what is a header vs item.

DO NOT change any price from the OCR text. Copy prices exactly as they appear.

REMEMBER:
- Each named topping and sauce → separate item (not collapsed into one)
- "W/ Cheese" versions → size variant of the base item, not a separate item
- Shared section descriptions → copy into every item in that section
- Modifiers (sauces, bread choices, add-ons) → separate items in their PARENT category

OCR TEXT (absolute source of truth):
---
{ocr_text}
---

Return JSON: {{"items": [...]}}"""

_DETECT_TEXT_ONLY_PROMPT = """\
Extract every menu item from this menu text (bounding boxes will be empty/approximate):

---
{ocr_text}
---

Return JSON: {{"items": [...]}}"""


def detect_menu_elements(
    ocr_text: str,
    *,
    image_path: Optional[str] = None,
    extra_image_paths: Optional[List[str]] = None,
    model: str = "claude-sonnet-4-5",
    max_tokens: int = 32000,
    use_thinking: bool = False,
) -> Optional[List[Dict[str, Any]]]:
    """Detect and classify every text element on a menu image.

    Returns a list of element dicts, each with 'type', 'bbox', and type-specific
    fields (name, description, prices for items; text for headers/notes; etc.).

    Multi-page support: pass extra_image_paths for additional pages beyond the
    primary image_path. Each file is encoded and sent as a separate page.

    This is the new Day 139 Call 1 — detection + classification + location.
    Structure assembly (parent/child grouping) is done in code, not here.
    Day 140: Opus without thinking — better precision than thinking mode
    (thinking lets Claude rationalize wrong prices for "consistency").
    """
    client = _get_client()
    if client is None:
        log.info("No Anthropic API key configured; skipping detection")
        return None

    if not ocr_text or not ocr_text.strip():
        if image_path:
            ocr_text = "(OCR produced no text)"
        else:
            return None

    # Truncate extremely long text
    max_chars = 30_000
    text = ocr_text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... truncated ...]"

    # --- Determine mode: multimodal vs text-only ---
    # Day 139: encode primary + extra pages
    image_blocks: List[Dict[str, str]] = []
    multimodal = False
    all_image_paths = []
    if image_path:
        all_image_paths.append(image_path)
    if extra_image_paths:
        all_image_paths.extend(extra_image_paths)

    if all_image_paths:
        encoder = _get_encoder()
        if encoder:
            for ip in all_image_paths:
                try:
                    blocks = encoder(ip)
                    image_blocks.extend(blocks)
                except Exception:
                    pass
        if image_blocks:
            multimodal = True
            print(f"[Detect] MULTIMODAL: {len(image_blocks)} image(s) from "
                  f"{len(all_image_paths)} file(s) + OCR hint ({len(text)} chars)")
        else:
            print("[Detect] TEXT-ONLY fallback (image encode failed)")
    else:
        print("[Detect] TEXT-ONLY mode (no image)")

    thinking_active = use_thinking and EXTENDED_THINKING
    if thinking_active:
        model = THINKING_MODEL
        max_tokens = 48000  # Detection returns more elements, needs headroom
        print(f"[Detect] Extended thinking ENABLED (budget=12k, model={model})")

    # --- Build messages ---
    if multimodal:
        system_prompt = _DETECT_SYSTEM_PROMPT
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
            "text": _DETECT_USER_PROMPT.format(ocr_text=text),
        })
        messages = [{"role": "user", "content": content}]
    else:
        system_prompt = _DETECT_SYSTEM_PROMPT
        messages = [
            {"role": "user", "content": _DETECT_TEXT_ONLY_PROMPT.format(ocr_text=text)},
        ]

    # --- Build API kwargs ---
    api_kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": messages,
    }
    if thinking_active:
        api_kwargs["temperature"] = 1
        api_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 12000}
    else:
        api_kwargs["temperature"] = 0

    _debug_base = {
        "model": model,
        "thinking_active": thinking_active,
        "multimodal": multimodal,
        "ocr_text_length": len(text),
        "image_blocks_count": len(image_blocks),
        "api_kwargs_summary": {
            k: v for k, v in api_kwargs.items()
            if k not in ("messages",)
        },
    }

    try:
        print("[Detect] Streaming API call started...")
        with client.messages.stream(**api_kwargs) as stream:
            message = stream.get_final_message()

        stop = getattr(message, "stop_reason", "unknown")
        usage = getattr(message, "usage", None)
        in_tok = getattr(usage, "input_tokens", "?") if usage else "?"
        out_tok = getattr(usage, "output_tokens", "?") if usage else "?"
        print(f"[Detect] Response: stop={stop}, in={in_tok}, out={out_tok}")

        # Extract text from response (skip thinking blocks)
        resp_text = ""
        thinking_text = ""
        for block in message.content:
            block_type = getattr(block, "type", None)
            if block_type == "thinking":
                thinking_text += getattr(block, "thinking", "")
            elif hasattr(block, "text"):
                resp_text += block.text

        if not resp_text.strip():
            print("[Detect] ERROR: empty response text")
            _write_debug_log(**_debug_base, error="empty_response_text",
                             response_text=resp_text, thinking_text=thinking_text)
            return None

        # Parse JSON
        json_str = resp_text.strip()
        if json_str.startswith("```"):
            json_str = re.sub(r"^```(?:json)?\s*\n?", "", json_str)
            json_str = re.sub(r"\n?```\s*$", "", json_str)

        data = json.loads(json_str)
        # Day 139 v2: Claude now returns items directly (not elements)
        items_raw = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items_raw, list):
            print(f"[Detect] ERROR: missing 'items' list")
            _write_debug_log(**_debug_base, error="missing_items_list",
                             response_text=resp_text, thinking_text=thinking_text)
            return None

        # Normalize items (includes bbox + category + sizes)
        result = _normalize_detected_items(items_raw)
        print(f"[Detect] SUCCESS: {len(result)} items extracted with bounding boxes")

        # Validate descriptions (reuse mismatch detector)
        n_fixed = _validate_descriptions(result)
        if n_fixed:
            print(f"[Detect] Description validator: nulled {n_fixed} mismatched description(s)")

        # Log breakdown by category
        type_counts: Dict[str, int] = {}
        for el in result:
            c = el.get("category", "Other")
            type_counts[c] = type_counts.get(c, 0) + 1
        print(f"[Detect] Breakdown by category: {type_counts}")

        _write_debug_log(
            **_debug_base,
            stop_reason=stop,
            input_tokens=in_tok,
            output_tokens=out_tok,
            response_text=resp_text,
            thinking_text=thinking_text,
            parsed_item_count=len(result),
            category_breakdown=type_counts,
        )
        return result if result else None

    except json.JSONDecodeError as e:
        print(f"[Detect] JSON PARSE ERROR: {e}")
        _write_debug_log(**_debug_base, error=f"json_parse_error: {e}",
                         response_text=resp_text, thinking_text=thinking_text)
        return None
    except Exception as e:
        print(f"[Detect] EXCEPTION: {type(e).__name__}: {e}")
        _write_debug_log(**_debug_base, error=f"{type(e).__name__}: {e}")
        return None


def _normalize_detected_items(raw_items: List[Any]) -> List[Dict[str, Any]]:
    """Normalize items returned by the detection call.

    Each item has: name, description, price, category, sizes, bbox.
    Trusts Claude's category assignment (it can see the menu visually).
    """
    result = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        if not name:
            continue

        desc_raw = it.get("description")
        description = (desc_raw or "").strip() or None if desc_raw else None

        price = _to_float(it.get("price"))
        category = _normalize_category(it.get("category") or "Other")
        sizes = _normalize_sizes(it.get("sizes"))

        # Normalize bbox (percentages, clamped 0-100)
        raw_bbox = it.get("bbox") or {}
        bbox = {
            "x_pct": max(0.0, min(100.0, _to_float(raw_bbox.get("x_pct", 0)))),
            "y_pct": max(0.0, min(100.0, _to_float(raw_bbox.get("y_pct", 0)))),
            "w_pct": max(0.0, min(100.0, _to_float(raw_bbox.get("w_pct", 0)))),
            "h_pct": max(0.0, min(100.0, _to_float(raw_bbox.get("h_pct", 0)))),
            "page": max(1, int(raw_bbox.get("page", 1) or 1)),
        }

        subcategory = (it.get("subcategory") or "").strip() or None

        result.append({
            "name": name,
            "description": description,
            "price": price,
            "category": category,
            "subcategory": subcategory,
            "sizes": sizes,
            "bbox": bbox,
        })
    return result


def _normalize_elements(raw_elements: List[Any]) -> List[Dict[str, Any]]:
    """Normalize and validate raw elements from Claude's detection response."""
    VALID_TYPES = {"category_header", "item", "variant_header", "section_note", "badge"}
    result = []
    for el in raw_elements:
        if not isinstance(el, dict):
            continue
        el_type = (el.get("type") or "").strip().lower()
        if el_type not in VALID_TYPES:
            continue

        # Normalize bbox
        raw_bbox = el.get("bbox") or {}
        bbox = {
            "x_pct": max(0, min(100, _to_float(raw_bbox.get("x_pct", 0)))),
            "y_pct": max(0, min(100, _to_float(raw_bbox.get("y_pct", 0)))),
            "w_pct": max(0, min(100, _to_float(raw_bbox.get("w_pct", 0)))),
            "h_pct": max(0, min(100, _to_float(raw_bbox.get("h_pct", 0)))),
            "page": max(1, int(raw_bbox.get("page", 1) or 1)),
        }

        normalized: Dict[str, Any] = {"type": el_type, "bbox": bbox}

        if el_type == "category_header":
            text = (el.get("text") or "").strip()
            if not text:
                continue
            normalized["text"] = text

        elif el_type == "item":
            name = (el.get("name") or "").strip()
            if not name:
                continue
            normalized["name"] = name
            desc = (el.get("description") or "")
            normalized["description"] = desc.strip() if desc else None
            # Normalize prices array
            raw_prices = el.get("prices") or []
            if not isinstance(raw_prices, list):
                raw_prices = [raw_prices]
            normalized["prices"] = [_to_float(p) for p in raw_prices]

        elif el_type == "variant_header":
            text = (el.get("text") or "").strip()
            if not text:
                continue
            normalized["text"] = text
            # Parse variant columns
            raw_variants = el.get("variants") or []
            variants = []
            for v in raw_variants:
                if not isinstance(v, dict):
                    continue
                lbl = (v.get("label") or "").strip()
                if lbl:
                    pr = v.get("price")
                    variants.append({
                        "label": lbl,
                        "price": _to_float(pr) if pr is not None else None,
                    })
            normalized["variants"] = variants

        elif el_type == "section_note":
            text = (el.get("text") or "").strip()
            if not text:
                continue
            normalized["text"] = text

        elif el_type == "badge":
            text = (el.get("text") or "").strip()
            if not text:
                continue
            normalized["text"] = text
            normalized["near_item"] = (el.get("near_item") or "").strip() or None

        result.append(normalized)

    return result


# ---------------------------------------------------------------------------
# Day 139: Assemble menu structure from classified elements (code layer)
# ---------------------------------------------------------------------------
def assemble_menu_structure(
    elements: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert flat classified elements into structured items for draft creation.

    This is the deterministic code layer — NO AI involved.
    Groups items under their nearest preceding category_header by y-position.
    Attaches variant_headers to items in the same section.
    Inherits section_notes as shared descriptions.
    Returns list of item dicts compatible with claude_items_to_draft_rows().
    """
    if not elements:
        return []

    # Separate elements by type, sorted by page then y-position
    def _sort_key(el: Dict) -> tuple:
        bb = el.get("bbox", {})
        return (bb.get("page", 1), bb.get("y_pct", 0), bb.get("x_pct", 0))

    sorted_els = sorted(elements, key=_sort_key)

    # Collect by type
    category_headers = [e for e in sorted_els if e["type"] == "category_header"]
    items = [e for e in sorted_els if e["type"] == "item"]
    variant_headers = [e for e in sorted_els if e["type"] == "variant_header"]
    section_notes = [e for e in sorted_els if e["type"] == "section_note"]
    badges = [e for e in sorted_els if e["type"] == "badge"]

    # --- Assign categories to items ---
    # Each item belongs to the nearest category_header ABOVE it on the same page
    # (or cross-page if no header on current page)
    def _find_category(item_el: Dict) -> str:
        iy = item_el["bbox"]["y_pct"]
        ip = item_el["bbox"]["page"]
        best_cat = "Other"
        best_score = -1
        for ch in category_headers:
            cy = ch["bbox"]["y_pct"]
            cp = ch["bbox"]["page"]
            # Must be above the item: earlier page, or same page with smaller y
            if cp < ip or (cp == ip and cy < iy):
                score = cp * 1000 + cy  # higher = closer to item
                if score > best_score:
                    best_score = score
                    best_cat = ch["text"]
        return _normalize_category(best_cat)

    # --- Find active variant header for each item ---
    # A variant_header applies to items below it in the same section
    # (until the next category_header or variant_header)
    def _find_variant_header(item_el: Dict) -> Optional[Dict]:
        iy = item_el["bbox"]["y_pct"]
        ip = item_el["bbox"]["page"]
        best = None
        best_score = -1
        for vh in variant_headers:
            vy = vh["bbox"]["y_pct"]
            vp = vh["bbox"]["page"]
            if vp < ip or (vp == ip and vy < iy):
                score = vp * 1000 + vy
                if score > best_score:
                    # Check no category_header between vh and item
                    blocked = False
                    for ch in category_headers:
                        cy = ch["bbox"]["y_pct"]
                        cp = ch["bbox"]["page"]
                        ch_score = cp * 1000 + cy
                        if score < ch_score < (ip * 1000 + iy):
                            blocked = True
                            break
                    if not blocked:
                        best_score = score
                        best = vh
        return best

    # --- Collect section notes for inheritance ---
    def _find_section_notes(item_el: Dict) -> List[str]:
        iy = item_el["bbox"]["y_pct"]
        ip = item_el["bbox"]["page"]
        notes = []
        for sn in section_notes:
            sy = sn["bbox"]["y_pct"]
            sp = sn["bbox"]["page"]
            if sp == ip and sy < iy:
                # Check no category_header between note and item
                blocked = False
                for ch in category_headers:
                    cy = ch["bbox"]["y_pct"]
                    cp = ch["bbox"]["page"]
                    if cp == ip and sy < cy < iy:
                        blocked = True
                        break
                if not blocked:
                    notes.append(sn["text"])
        return notes

    # --- Build structured items ---
    result = []
    for item_el in items:
        name = item_el["name"]
        description = item_el.get("description")
        prices = item_el.get("prices", [0])
        category = _find_category(item_el)

        # Inherit section notes if item has no description
        if not description:
            notes = _find_section_notes(item_el)
            if notes:
                description = "; ".join(notes)

        # Find applicable variant header
        vh = _find_variant_header(item_el)

        # Build sizes from variant_header + item prices
        sizes = []
        if vh and vh.get("variants"):
            variant_labels = vh["variants"]
            for vi, v in enumerate(variant_labels):
                price = 0.0
                # Match price from item's prices array by position
                if vi < len(prices):
                    price = prices[vi]
                elif v.get("price") is not None:
                    price = v["price"]
                sizes.append({"label": v["label"], "price": price})
        elif len(prices) > 1:
            # Multiple prices but no variant header — use generic labels
            for pi, p in enumerate(prices):
                sizes.append({"label": f"Option {pi + 1}", "price": p})

        # Base price
        base_price = prices[0] if prices else 0.0

        structured_item: Dict[str, Any] = {
            "name": name,
            "description": description,
            "price": base_price,
            "category": category,
            "sizes": sizes,
            # Carry bbox through for coordinate storage
            "_bbox": item_el["bbox"],
        }

        # Attach badge info if any
        for b in badges:
            if b.get("near_item") and b["near_item"].lower() == name.lower():
                existing_desc = structured_item.get("description") or ""
                badge_text = b["text"]
                if badge_text not in existing_desc:
                    structured_item["description"] = (
                        f"[{badge_text}] {existing_desc}" if existing_desc
                        else f"[{badge_text}]"
                    )

        result.append(structured_item)

    # Validate descriptions (reuse existing mismatch detector)
    _validate_descriptions(result)

    return result


def _extract_prices_from_raw(raw_line: str) -> List[float]:
    """Parse all dollar amounts from a raw menu line.

    Handles formats like: "BURGER...9.00...13.00", "$9.99", "14.75 19.95"
    Returns prices in order of appearance.
    """
    if not raw_line:
        return []
    # Match patterns: optional $, digits, dot, cents
    import re
    matches = re.findall(r'\$?\b(\d{1,3}\.\d{2})\b', raw_line)
    return [float(m) for m in matches]


def _verify_prices_against_raw(item: Dict[str, Any]) -> Dict[str, Any]:
    """Cross-check Claude's prices against the raw_line it quoted.

    If raw_line contains prices that don't match Claude's output,
    override with the raw_line prices. This catches consistency-smoothing.
    """
    raw_line = (item.get("raw_line") or "").strip()
    if not raw_line:
        return item

    raw_prices = _extract_prices_from_raw(raw_line)
    if not raw_prices:
        return item

    sizes = item.get("sizes") or []
    claude_price = _to_float(item.get("price"))

    if sizes:
        # Multi-price item: compare variant prices against raw_line prices
        claude_prices = [_to_float(s.get("price")) for s in sizes]
        # If raw_line has same count of prices as sizes, use raw_line prices
        if len(raw_prices) == len(sizes):
            changed = False
            for i, (rp, cp) in enumerate(zip(raw_prices, claude_prices)):
                if abs(rp - cp) > 0.001:
                    print(f"[RawLine] Price override: {item.get('name')} "
                          f"size {i}: ${cp:.2f} -> ${rp:.2f} (raw: {raw_line[:80]})")
                    sizes[i]["price"] = rp
                    changed = True
            if changed:
                item["sizes"] = sizes
                item["price"] = raw_prices[0]
        elif len(raw_prices) >= 1 and len(sizes) == 0:
            # Single price in raw but no sizes
            if abs(raw_prices[0] - claude_price) > 0.001:
                print(f"[RawLine] Price override: {item.get('name')} "
                      f"${claude_price:.2f} -> ${raw_prices[0]:.2f}")
                item["price"] = raw_prices[0]
    else:
        # Single-price item
        if raw_prices and abs(raw_prices[0] - claude_price) > 0.001:
            print(f"[RawLine] Price override: {item.get('name')} "
                  f"${claude_price:.2f} -> ${raw_prices[0]:.2f}")
            item["price"] = raw_prices[0]

    return item


def _verify_prices_against_ocr(item: Dict[str, Any], ocr_lines: List[str]) -> Dict[str, Any]:
    """Cross-check Claude's prices against the ACTUAL OCR text (not Claude's raw_line).

    Finds the OCR line matching this item's name and compares prices.
    This catches cases where Claude hallucinated both the price AND the raw_line.
    """
    name = (item.get("name") or "").strip()
    if not name or not ocr_lines:
        return item

    # Skip $0 items (sauces, toppings with no standalone price) — they're
    # intentionally priceless and would false-match random OCR lines.
    claude_price = _to_float(item.get("price"))
    sizes = item.get("sizes") or []
    if claude_price == 0 and not sizes:
        return item

    # Find the OCR line that STARTS WITH this item's name (case-insensitive).
    # "Contains" matching is too loose — "BBQ" matches "BBQ CHICKEN PIZZA".
    name_lower = name.lower()
    name_words = name_lower.split()
    if not name_words or len(name_words[0]) < 4:
        return item  # Skip short/generic names to avoid false matches

    matching_line = None
    for line in ocr_lines:
        line_stripped = line.strip().lower()
        # Line must START with the first word of the item name
        if not line_stripped.startswith(name_words[0]):
            continue
        if not _extract_prices_from_raw(line):
            continue
        # Best case: first 2 words match. Fallback: first word starts the line
        # (handles "Reuben Melt" matching OCR line "REUBEN Corned beef...")
        if len(name_words) >= 2 and " ".join(name_words[:2]) in line_stripped:
            matching_line = line
            break
        elif not matching_line:
            # First-word-only match — keep looking for a better match
            matching_line = line

    if not matching_line:
        return item

    ocr_prices = _extract_prices_from_raw(matching_line)
    if not ocr_prices:
        return item

    if sizes and len(ocr_prices) == len(sizes):
        changed = False
        for i, (op, s) in enumerate(zip(ocr_prices, sizes)):
            cp = _to_float(s.get("price"))
            if abs(op - cp) > 0.001:
                print(f"[OCR-Verify] Price override: {name} "
                      f"size {i}: ${cp:.2f} -> ${op:.2f} (OCR: {matching_line.strip()[:80]})")
                sizes[i]["price"] = op
                changed = True
        if changed:
            item["sizes"] = sizes
            item["price"] = ocr_prices[0]
    elif not sizes and ocr_prices:
        if abs(ocr_prices[0] - claude_price) > 0.001:
            print(f"[OCR-Verify] Price override: {name} "
                  f"${claude_price:.2f} -> ${ocr_prices[0]:.2f} (OCR: {matching_line.strip()[:80]})")
            item["price"] = ocr_prices[0]

    return item


def elements_to_draft_rows(
    items: List[Dict[str, Any]],
    ocr_text: str = "",
) -> tuple:
    """Convert detected items to draft rows + coordinate data.

    Day 139 v2: Trusts Claude's category/sizes assignments. Uses bounding
    boxes only for wizard highlighting, NOT for structure inference.
    Day 140: raw_line price verification — cross-checks Claude's prices
    against the text it quoted. Catches consistency-smoothing.
    Day 140: OCR text verification — second pass against actual OCR text
    to catch cases where Claude hallucinated both price AND raw_line.

    Returns (draft_rows, coord_data) where:
    - draft_rows: list of dicts for upsert_draft_items()
    - coord_data: list of dicts keyed by 'position' for post-insert item_id linking
    """
    ocr_lines = ocr_text.strip().splitlines() if ocr_text else []
    draft_rows = []
    coord_data = []
    for pos, it in enumerate(items, start=1):
        name = (it.get("name") or "").strip()
        if not name:
            continue

        # Day 140: verify prices against raw_line before processing
        it = _verify_prices_against_raw(it)
        # Day 140: second pass — verify against actual OCR text
        if ocr_lines:
            it = _verify_prices_against_ocr(it, ocr_lines)

        price = _to_float(it.get("price"))
        price_cents = int(round(price * 100))

        # Build variants from sizes (trust Claude's output)
        sizes = it.get("sizes") or []
        variants = []
        for vi, s in enumerate(sizes):
            if not isinstance(s, dict):
                continue
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
        if variants and price_cents == 0:
            price_cents = variants[0]["price_cents"]

        row: Dict[str, Any] = {
            "name": name,
            "description": it.get("description"),
            "price_cents": price_cents,
            "category": it.get("category") or "Other",
            "subcategory": it.get("subcategory"),
            "position": pos,
            "confidence": 90,
        }
        if variants:
            row["_variants"] = variants

        draft_rows.append(row)

        # Bounding box for wizard highlighting
        bbox = it.get("bbox")
        if bbox:
            coord_data.append({
                "position": pos,
                "x_pct": bbox.get("x_pct", 0),
                "y_pct": bbox.get("y_pct", 0),
                "w_pct": bbox.get("w_pct", 0),
                "h_pct": bbox.get("h_pct", 0),
                "page": bbox.get("page", 1),
                "element_type": "item",
            })

    return draft_rows, coord_data
