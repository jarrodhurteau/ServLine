# storage/ai_menu_extract.py
"""
Claude API Menu Extraction — multimodal menu item extraction.

Primary mode (Day 102.5): sends menu IMAGE as primary input + Tesseract OCR text
as a secondary hint.  Claude reads the image directly, using the OCR text only to
disambiguate ambiguous regions.  This eliminates the root cause of garbled OCR
being faithfully extracted as garbage.

Fallback mode: text-only extraction when no image is available (original behavior).

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
# Prompt
# ---------------------------------------------------------------------------
# -- Text-only prompt (fallback when no image available) -----------------------
_SYSTEM_PROMPT_TEXT_ONLY = """\
You are a restaurant menu data extraction expert. You receive raw OCR text from \
a scanned restaurant menu. The text may contain OCR artifacts, garbled characters, \
merged words, and formatting noise.

Your job is to extract every real menu item and return structured JSON. \
The output will be imported into a Point-of-Sale (POS) system, so each item must \
be a distinct orderable product.

Rules:
1. Extract ONLY actual menu items that a customer can order. Skip:
   - Section headings (e.g., "GOURMET PIZZA", "APPETIZERS")
   - Sauce choices (e.g., "Choice of Sauce: Red, White, Pesto")
   - Size headers (e.g., "10 inch  12 inch  16 inch")
   - Informational text (e.g., "All pizzas come with mozzarella")
   - Phone numbers, addresses, hours

2. POS-ready item splitting:
   - If a menu lists "X or Y" as one item (e.g., "Beef or Chicken Empanadas"), \
split it into SEPARATE items ("Beef Empanadas", "Chicken Empanadas") at the \
same price. POS systems need one button per orderable product.
   - Similarly, "Grilled or Fried Calamari" → two items: "Grilled Calamari" and \
"Fried Calamari".

3. Toppings/add-ons as individual items:
   - When a menu lists available toppings (e.g., "MEAT TOPPINGS: Pepperoni, Chicken, \
Bacon, Hamburger, Sausage, Meatball"), create a SEPARATE item for EACH topping. \
Example: "Pepperoni Topping", "Chicken Topping", "Bacon Topping", etc.
   - Each topping item gets the same per-size prices from the "Each Topping Add" \
line. If the menu shows "EACH TOPPING ADD  1.50  2.25  2.75  4.00" under a size \
grid, every individual topping gets those exact prices as sizes.
   - Category for all toppings: "Toppings".
   - Do the same for veggie toppings, calzone toppings, etc.

4. For each item, provide:
   - "name": Clean, properly capitalized item name. Fix OCR typos (e.g., "Homburg" → "Hamburg", "88Q" → "BBQ", "Tomatoe" → "Tomato"). Use title case.
   - "description": Null for toppings. Brief description for other items if listed. Null if none.
   - "price": The primary price as a float (e.g., 17.95). Use the FIRST or BASE price if multiple sizes exist. 0 if no price is visible.
   - "category": One of these categories: "Pizza", "Toppings", "Appetizers", "Salads", \
"Soups", "Sandwiches", "Burgers", "Wraps", "Entrees", "Seafood", "Pasta", "Steaks", \
"Wings", "Sides", "Desserts", "Beverages", "Kids Menu", "Breakfast", "Calzones", \
"Subs", "Platters", or "Other".
   - "sizes": Array of size/price pairs if the item has multiple sizes. Each entry: {"label": "10\\"", "price": 12.95}. Empty array if single-priced.

5. Price association:
   - Prices often appear AFTER item names, sometimes on the next line
   - Size grids (e.g., "10\\" 12\\" 16\\"" header) apply to items below them until a new section
   - Prices like "17.95  25.95  34.75" map left-to-right to the size columns above
   - "$4.75" could be an OCR error for "$34.75" if context suggests higher prices
   - Add-on/topping items (e.g., "Each Topping") that appear under a size grid \
ALSO have per-size prices. Capture ALL size prices for toppings, not just the first.

6. Output ONLY valid JSON: {"items": [...]}
   No markdown, no explanation, just the JSON object.\
"""

# -- Multimodal prompt (image-first, OCR text as hint) -------------------------
_SYSTEM_PROMPT_MULTIMODAL = """\
You are a restaurant menu data extraction expert. You receive:
1. An image of a restaurant menu (PRIMARY — read this directly)
2. OCR text extracted from the same image (SECONDARY — use as a hint only)

IMPORTANT: Read item names, prices, and descriptions directly from the menu image. \
The OCR text may contain garbled characters, merged words, and artifacts. Use it \
only to disambiguate hard-to-read areas — never trust it over what you can clearly \
see in the image.

Your job is to extract every real menu item and return structured JSON. \
The output will be imported into a Point-of-Sale (POS) system, so each item must \
be a distinct orderable product.

Rules:
1. Extract ONLY actual menu items that a customer can order. Skip:
   - Section headings (e.g., "GOURMET PIZZA", "APPETIZERS")
   - Sauce choices (e.g., "Choice of Sauce: Red, White, Pesto")
   - Size headers (e.g., "10 inch  12 inch  16 inch")
   - Informational text (e.g., "All pizzas come with mozzarella")
   - Phone numbers, addresses, hours

2. POS-ready item splitting:
   - If a menu lists "X or Y" as one item (e.g., "Beef or Chicken Empanadas"), \
split it into SEPARATE items ("Beef Empanadas", "Chicken Empanadas") at the \
same price. POS systems need one button per orderable product.
   - Similarly, "Grilled or Fried Calamari" → two items: "Grilled Calamari" and \
"Fried Calamari".

3. Toppings/add-ons as individual items:
   - When a menu lists available toppings (e.g., "MEAT TOPPINGS: Pepperoni, Chicken, \
Bacon, Hamburger, Sausage, Meatball"), create a SEPARATE item for EACH topping. \
Example: "Pepperoni Topping", "Chicken Topping", "Bacon Topping", etc.
   - Each topping item gets the same per-size prices from the "Each Topping Add" \
line. If the menu shows "EACH TOPPING ADD  1.50  2.25  2.75  4.00" under a size \
grid, every individual topping gets those exact prices as sizes.
   - Category for all toppings: "Toppings".
   - Do the same for veggie toppings, calzone toppings, etc.

4. For each item, provide:
   - "name": Clean, properly capitalized item name as shown on the menu. Use title case.
   - "description": Null for toppings. Brief description for other items if listed. Null if none.
   - "price": The primary price as a float (e.g., 17.95). Use the FIRST or BASE price if multiple sizes exist. 0 if no price is visible.
   - "category": One of these categories: "Pizza", "Toppings", "Appetizers", "Salads", \
"Soups", "Sandwiches", "Burgers", "Wraps", "Entrees", "Seafood", "Pasta", "Steaks", \
"Wings", "Sides", "Desserts", "Beverages", "Kids Menu", "Breakfast", "Calzones", \
"Subs", "Platters", or "Other".
   - "sizes": Array of size/price pairs if the item has multiple sizes. Each entry: {"label": "10\\"", "price": 12.95}. Empty array if single-priced.

5. Price association:
   - Prices often appear AFTER item names, sometimes on the next line
   - Size grids (e.g., "10\\" 12\\" 16\\"" header) apply to items below them until a new section
   - Prices like "17.95  25.95  34.75" map left-to-right to the size columns above
   - Add-on/topping items (e.g., "Each Topping") that appear under a size grid \
ALSO have per-size prices. Capture ALL size prices for toppings, not just the first.

6. Output ONLY valid JSON: {"items": [...]}
   No markdown, no explanation, just the JSON object.\
"""

_USER_PROMPT_TEMPLATE = """\
Extract menu items from this OCR text:

---
{ocr_text}
---

Return JSON: {{"items": [{{"name": "...", "description": "...", "price": 0.00, "category": "...", "sizes": []}}]}}"""

_USER_PROMPT_MULTIMODAL_TEMPLATE = """\
Extract every menu item from the menu image above. \
Read names, prices, and descriptions directly from the image.

The following OCR text was extracted from the same image and may help \
disambiguate hard-to-read areas, but DO NOT trust it blindly — the image \
is the source of truth:

---
{ocr_text}
---

Return JSON: {{"items": [{{"name": "...", "description": "...", "price": 0.00, "category": "...", "sizes": []}}]}}"""


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------
def extract_menu_items_via_claude(
    ocr_text: str,
    *,
    image_path: Optional[str] = None,
    model: str = "claude-sonnet-4-5-20250929",
    max_tokens: int = 16000,
) -> Optional[List[Dict[str, Any]]]:
    """Extract structured menu items via Claude API.

    Multimodal mode (preferred): when *image_path* is provided and the image
    can be encoded, the menu image is sent as the primary input and the OCR
    text is included only as a disambiguation hint.

    Text-only mode (fallback): when no image is available, the raw OCR text
    is sent as the sole input (original Day 96 behaviour).

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

    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,
            system=system_prompt,
            messages=messages,
        )

        # Extract text from response
        resp_text = ""
        for block in message.content:
            if hasattr(block, "text"):
                resp_text += block.text

        if not resp_text.strip():
            log.warning("Claude returned empty response")
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
            log.warning("Claude response missing 'items' list")
            return None

        # Normalize items
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
                "category": (it.get("category") or "Other").strip(),
                "sizes": _normalize_sizes(it.get("sizes")),
            })

        mode_label = "multimodal" if multimodal else "text-only"
        log.info("Claude extracted %d menu items (%s)", len(result), mode_label)
        return result if result else None

    except json.JSONDecodeError as e:
        log.warning("Failed to parse Claude JSON response: %s", e)
        return None
    except Exception as e:
        log.warning("Claude API call failed: %s", e)
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
