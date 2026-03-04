# storage/ai_vision_verify.py
"""
Claude Vision Verification — Call 2 in the production pipeline.

Takes a menu image + structured items from Call 1 (text extraction) and asks
Claude to independently verify them against the actual menu image.  This catches
OCR misreads, wrong prices, missing items, and category errors that text-only
extraction misses.

Usage:
    from storage.ai_vision_verify import verify_menu_with_vision

    result = verify_menu_with_vision(image_path, extracted_items)
    # result = {
    #     "items":       [...],   # corrected item list
    #     "changes":     [...],   # log of what changed
    #     "confidence":  0.92,    # Claude's self-reported confidence
    #     "model":       "...",   # model used
    #     "skipped":     False,
    # }

Requires ANTHROPIC_API_KEY in environment.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reuse the shared Anthropic client from ai_menu_extract
# ---------------------------------------------------------------------------
from .ai_menu_extract import _get_client, _to_float, _normalize_sizes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
_MAX_TOKENS = 16_000

# Supported image MIME types for Claude vision
_MIME_MAP = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
}


# ---------------------------------------------------------------------------
# Image encoding helpers
# ---------------------------------------------------------------------------
def _encode_image_file(path: str) -> Optional[Dict[str, str]]:
    """Read an image file and return {media_type, data} for Claude vision API."""
    p = Path(path)
    ext = p.suffix.lower()
    mime = _MIME_MAP.get(ext)
    if not mime:
        return None
    try:
        raw = p.read_bytes()
        return {"media_type": mime, "data": base64.standard_b64encode(raw).decode("ascii")}
    except Exception as e:
        log.warning("Failed to read image %s: %s", path, e)
        return None


def _pdf_to_images(path: str, dpi: int = 200) -> List[Dict[str, str]]:
    """Convert PDF pages to base64 PNG images for Claude vision API."""
    try:
        from pdf2image import convert_from_path
        poppler_path = os.getenv("POPPLER_PATH") or None
        pages = convert_from_path(path, dpi=dpi, poppler_path=poppler_path)
    except Exception as e:
        log.warning("pdf2image conversion failed: %s", e)
        return []

    results = []
    for page in pages:
        import io
        buf = io.BytesIO()
        page.save(buf, format="PNG")
        b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
        results.append({"media_type": "image/png", "data": b64})
    return results


def encode_menu_images(path: str) -> List[Dict[str, str]]:
    """Encode a menu file (image or PDF) into base64 image blocks for Claude.

    Returns a list of {media_type, data} dicts — one per page/image.
    """
    p = Path(path)
    if not p.exists():
        log.warning("Menu file not found: %s", path)
        return []

    ext = p.suffix.lower()
    if ext == ".pdf":
        return _pdf_to_images(path)
    elif ext in _MIME_MAP:
        img = _encode_image_file(path)
        return [img] if img else []
    else:
        log.warning("Unsupported file type: %s", ext)
        return []


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are a restaurant menu verification expert. You receive:
1. An image of a restaurant menu
2. A list of items that were extracted from this menu via OCR + AI

Your job is to INDEPENDENTLY read the menu image and verify that the extracted \
items are correct. Compare what you see in the image against the extracted list.

For each item, check:
- Name spelling (fix OCR typos — e.g., "Homburg" → "Hamburg", "88Q" → "BBQ")
- Price accuracy (match exactly what the menu shows)
- Description accuracy
- Category correctness
- Whether the item actually exists on the menu (remove phantom items)
- Items on the menu that were MISSED by extraction (add them)

Rules:
1. Return ALL items — both verified-correct and corrected ones.
2. For corrected items, preserve the original structure but fix the values.
3. For newly discovered items, add them with the same structure.
4. Do NOT include section headers, topping lists, or non-orderable text as items.
5. Report your overall confidence (0.0-1.0) in the accuracy of the final item list.
6. Output ONLY valid JSON — no markdown, no explanation.\
"""


def _build_user_prompt(items: List[Dict[str, Any]]) -> str:
    """Build the user prompt with the extracted items for verification."""
    items_json = json.dumps(items, indent=2)
    return f"""\
Here are the items extracted from this menu via OCR + AI text extraction.
Please verify them against the menu image.

Extracted items:
{items_json}

Return JSON in this exact format:
{{
  "items": [
    {{
      "name": "Item Name",
      "description": "Description or null",
      "price": 12.95,
      "category": "Category",
      "sizes": [{{"label": "10\\"", "price": 12.95}}]
    }}
  ],
  "confidence": 0.92,
  "notes": "Brief summary of changes made, or 'No changes needed' if all correct"
}}"""


# ---------------------------------------------------------------------------
# Changes log — diff original vs corrected
# ---------------------------------------------------------------------------
def compute_changes_log(
    original: List[Dict[str, Any]],
    corrected: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Compare original items to corrected items and return a changes log.

    Each entry: {"type": "...", "detail": "..."}.
    Types: name_fixed, price_fixed, description_fixed, category_fixed,
           item_added, item_removed, sizes_changed.
    """
    changes: List[Dict[str, str]] = []

    # Index originals by lowercase name for matching
    orig_by_name: Dict[str, Dict[str, Any]] = {}
    for it in original:
        key = (it.get("name") or "").strip().lower()
        if key:
            orig_by_name[key] = it

    corr_by_name: Dict[str, Dict[str, Any]] = {}
    for it in corrected:
        key = (it.get("name") or "").strip().lower()
        if key:
            corr_by_name[key] = it

    # Check for removed items
    for key, orig in orig_by_name.items():
        if key not in corr_by_name:
            changes.append({
                "type": "item_removed",
                "detail": f"Removed '{orig.get('name')}' (not found on menu image)",
            })

    # Check for added or modified items
    for key, corr in corr_by_name.items():
        if key not in orig_by_name:
            changes.append({
                "type": "item_added",
                "detail": f"Added '{corr.get('name')}' (found on menu image but missing from extraction)",
            })
            continue

        orig = orig_by_name[key]

        # Name change (case/spelling fix — matched by lowercase key)
        orig_name = (orig.get("name") or "").strip()
        corr_name = (corr.get("name") or "").strip()
        if orig_name != corr_name:
            changes.append({
                "type": "name_fixed",
                "detail": f"Name: '{orig_name}' → '{corr_name}'",
            })

        # Price change
        orig_price = _to_float(orig.get("price"))
        corr_price = _to_float(corr.get("price"))
        if orig_price != corr_price:
            changes.append({
                "type": "price_fixed",
                "detail": f"Price for '{corr_name}': ${orig_price:.2f} → ${corr_price:.2f}",
            })

        # Description change
        orig_desc = (orig.get("description") or "").strip()
        corr_desc = (corr.get("description") or "").strip()
        if orig_desc != corr_desc:
            changes.append({
                "type": "description_fixed",
                "detail": f"Description updated for '{corr_name}'",
            })

        # Category change
        orig_cat = (orig.get("category") or "").strip()
        corr_cat = (corr.get("category") or "").strip()
        if orig_cat != corr_cat:
            changes.append({
                "type": "category_fixed",
                "detail": f"Category for '{corr_name}': '{orig_cat}' → '{corr_cat}'",
            })

        # Sizes change
        orig_sizes = orig.get("sizes") or []
        corr_sizes = corr.get("sizes") or []
        if _sizes_differ(orig_sizes, corr_sizes):
            changes.append({
                "type": "sizes_changed",
                "detail": f"Sizes/prices updated for '{corr_name}'",
            })

    return changes


def _sizes_differ(a: list, b: list) -> bool:
    """Check if two size lists are meaningfully different."""
    if len(a) != len(b):
        return True
    for sa, sb in zip(a, b):
        if (sa.get("label") or "") != (sb.get("label") or ""):
            return True
        if _to_float(sa.get("price")) != _to_float(sb.get("price")):
            return True
    return False


# ---------------------------------------------------------------------------
# Main verification function
# ---------------------------------------------------------------------------
def verify_menu_with_vision(
    image_path: str,
    extracted_items: List[Dict[str, Any]],
    *,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = _MAX_TOKENS,
) -> Dict[str, Any]:
    """Send menu image + extracted items to Claude for vision-based verification.

    Returns dict with keys:
        items       - corrected item list (same structure as input)
        changes     - list of changes made
        confidence  - Claude's self-reported confidence (0.0–1.0)
        model       - model used
        skipped     - True if verification was skipped (no API key, etc.)
        error       - error message if failed (items will be original)
    """
    # Guard: no items to verify
    if not extracted_items:
        return {
            "items": [],
            "changes": [],
            "confidence": 0.0,
            "model": model,
            "skipped": True,
            "skip_reason": "no_items",
        }

    # Guard: no API client
    client = _get_client()
    if client is None:
        log.info("No Anthropic API key; skipping vision verification")
        return {
            "items": extracted_items,
            "changes": [],
            "confidence": 0.0,
            "model": model,
            "skipped": True,
            "skip_reason": "no_api_key",
        }

    # Encode image(s)
    image_blocks = encode_menu_images(image_path)
    if not image_blocks:
        log.warning("Could not encode menu image: %s", image_path)
        return {
            "items": extracted_items,
            "changes": [],
            "confidence": 0.0,
            "model": model,
            "skipped": True,
            "skip_reason": "image_encode_failed",
        }

    # Build multimodal message content: images first, then text prompt
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
        "text": _build_user_prompt(extracted_items),
    })

    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )

        # Extract text response
        resp_text = ""
        for block in message.content:
            if hasattr(block, "text"):
                resp_text += block.text

        if not resp_text.strip():
            log.warning("Claude vision returned empty response")
            return {
                "items": extracted_items,
                "changes": [],
                "confidence": 0.0,
                "model": model,
                "skipped": False,
                "error": "empty_response",
            }

        # Parse JSON
        corrected_items, confidence, notes = _parse_verification_response(resp_text)

        if corrected_items is None:
            return {
                "items": extracted_items,
                "changes": [],
                "confidence": 0.0,
                "model": model,
                "skipped": False,
                "error": "parse_failed",
            }

        # Normalize corrected items
        normalized = _normalize_items(corrected_items)

        # Compute changes log
        changes = compute_changes_log(extracted_items, normalized)

        log.info(
            "Vision verification: %d items → %d items, %d changes, confidence=%.2f",
            len(extracted_items), len(normalized), len(changes), confidence,
        )

        return {
            "items": normalized,
            "changes": changes,
            "confidence": confidence,
            "model": model,
            "skipped": False,
            "notes": notes,
        }

    except Exception as e:
        log.warning("Claude vision API call failed: %s", e)
        return {
            "items": extracted_items,
            "changes": [],
            "confidence": 0.0,
            "model": model,
            "skipped": False,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
def _parse_verification_response(
    resp_text: str,
) -> Tuple[Optional[List[Dict[str, Any]]], float, str]:
    """Parse Claude's JSON response. Returns (items, confidence, notes)."""
    json_str = resp_text.strip()

    # Strip markdown code fences if present
    if json_str.startswith("```"):
        json_str = re.sub(r"^```(?:json)?\s*\n?", "", json_str)
        json_str = re.sub(r"\n?```\s*$", "", json_str)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        log.warning("Failed to parse vision verification JSON: %s", e)
        return None, 0.0, ""

    if not isinstance(data, dict):
        return None, 0.0, ""

    items = data.get("items")
    if not isinstance(items, list):
        return None, 0.0, ""

    confidence = 0.0
    try:
        confidence = min(1.0, max(0.0, float(data.get("confidence", 0.0))))
    except (ValueError, TypeError):
        pass

    notes = str(data.get("notes") or "")

    return items, confidence, notes


def _normalize_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize items from Claude's verification response."""
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
    return result


# ---------------------------------------------------------------------------
# Convert verified items to draft rows (reuse logic from ai_menu_extract)
# ---------------------------------------------------------------------------
def verified_items_to_draft_rows(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert vision-verified items to draft_items DB row format.

    Same structure as claude_items_to_draft_rows but with higher confidence
    since these items have been vision-verified.
    """
    from .ai_menu_extract import claude_items_to_draft_rows
    rows = claude_items_to_draft_rows(items)
    # Boost confidence — these items were vision-verified
    for row in rows:
        row["confidence"] = 95
    return rows