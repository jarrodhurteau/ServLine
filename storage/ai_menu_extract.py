# storage/ai_menu_extract.py
"""
Claude API Menu Extraction — sends raw OCR text to Claude for structured extraction.

Replaces the heuristic parser for draft item creation. Claude understands menu
context (names, descriptions, prices, categories) far better than regex/heuristics
can, especially with noisy OCR input.

Usage:
    from storage.ai_menu_extract import extract_menu_items_via_claude

    items = extract_menu_items_via_claude(raw_ocr_text)
    # Returns list of dicts: [{name, description, price, category}, ...]

Requires ANTHROPIC_API_KEY in environment (loaded via .env).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

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
_SYSTEM_PROMPT = """\
You are a restaurant menu data extraction expert. You receive raw OCR text from \
a scanned restaurant menu. The text may contain OCR artifacts, garbled characters, \
merged words, and formatting noise.

Your job is to extract every real menu item and return structured JSON.

Rules:
1. Extract ONLY actual menu items that a customer can order. Skip:
   - Section headings (e.g., "GOURMET PIZZA", "APPETIZERS")
   - Topping/ingredient lists (e.g., "Pepperoni, Sausage, Bacon, Ham")
   - Sauce choices (e.g., "Choice of Sauce: Red, White, Pesto")
   - Size headers (e.g., "10 inch  12 inch  16 inch")
   - Informational text (e.g., "All pizzas come with mozzarella")
   - Phone numbers, addresses, hours

2. For each item, provide:
   - "name": Clean, properly capitalized item name. Fix OCR typos (e.g., "Homburg" → "Hamburg", "88Q" → "BBQ", "Tomatoe" → "Tomato"). Use title case.
   - "description": Brief description if ingredients/details are listed. Null if none.
   - "price": The primary price as a float (e.g., 17.95). Use the FIRST or BASE price if multiple sizes exist. 0 if no price is visible.
   - "category": One of these categories: "Pizza", "Appetizers", "Salads", "Soups", "Sandwiches", "Burgers", "Wraps", "Entrees", "Seafood", "Pasta", "Steaks", "Wings", "Sides", "Desserts", "Beverages", "Kids Menu", "Breakfast", "Calzones", "Subs", "Platters", or "Other".
   - "sizes": Array of size/price pairs if the item has multiple sizes. Each entry: {"label": "10\\"", "price": 12.95}. Empty array if single-priced.

3. Price association:
   - Prices often appear AFTER item names, sometimes on the next line
   - Size grids (e.g., "10\\" 12\\" 16\\"" header) apply to items below them until a new section
   - Prices like "17.95  25.95  34.75" map left-to-right to the size columns above
   - "$4.75" could be an OCR error for "$34.75" if context suggests higher prices

4. Output ONLY valid JSON: {"items": [...]}
   No markdown, no explanation, just the JSON object.\
"""

_USER_PROMPT_TEMPLATE = """\
Extract menu items from this OCR text:

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
    model: str = "claude-sonnet-4-5-20250929",
    max_tokens: int = 16000,
) -> Optional[List[Dict[str, Any]]]:
    """Send raw OCR text to Claude API and get structured menu items back.

    Returns a list of item dicts on success, or None if the API is unavailable
    or the call fails (so the caller can fall back to the heuristic pipeline).
    """
    client = _get_client()
    if client is None:
        log.info("No Anthropic API key configured; skipping Claude extraction")
        return None

    if not ocr_text or not ocr_text.strip():
        return None

    # Truncate extremely long text to stay within token limits
    # ~4 chars per token, leave room for system prompt + response
    max_chars = 30_000
    text = ocr_text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... truncated ...]"

    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": _USER_PROMPT_TEMPLATE.format(ocr_text=text)},
            ],
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

        log.info("Claude extracted %d menu items", len(result))
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
    """
    rows = []
    for pos, it in enumerate(items, start=1):
        name = (it.get("name") or "").strip()
        if not name:
            continue

        price = _to_float(it.get("price"))
        price_cents = int(round(price * 100))

        # Build price_text from sizes if available
        sizes = it.get("sizes") or []
        if sizes:
            parts = []
            for s in sizes:
                lbl = s.get("label", "")
                pr = _to_float(s.get("price"))
                if lbl and pr > 0:
                    parts.append(f"{lbl}: ${pr:.2f}")
                elif pr > 0:
                    parts.append(f"${pr:.2f}")
            price_text = " | ".join(parts) if parts else ""
            # Use first size price if base price is 0
            if price_cents == 0 and sizes:
                first_price = _to_float(sizes[0].get("price"))
                price_cents = int(round(first_price * 100))
        else:
            price_text = f"${price:.2f}" if price > 0 else ""

        rows.append({
            "name": name,
            "description": it.get("description"),
            "price_cents": price_cents,
            "price_text": price_text,
            "category": it.get("category") or "Other",
            "position": pos,
            "confidence": 90,  # Claude extractions are high-confidence
        })
    return rows
