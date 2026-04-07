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
_DEFAULT_MODEL = "claude-opus-4-6"
_MAX_TOKENS = 16_000

# Page batching — Claude vision has a per-request token limit.
# Each base64-encoded page image ≈ 1-2MB ->~1600 tokens per page.
# Safe limit: 20 pages per call. For menus >20 pages, split into batches.
_MAX_PAGES_PER_CALL = 20
# Warn threshold — menus with many pages may produce degraded results
_WARN_PAGES = 8

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


def encode_menu_images(path: str, *, max_pages: int = _MAX_PAGES_PER_CALL) -> List[Dict[str, str]]:
    """Encode a menu file (image or PDF) into base64 image blocks for Claude.

    Returns a list of {media_type, data} dicts — one per page/image.
    For PDFs with more pages than *max_pages*, only the first *max_pages*
    are included (covers virtually all real restaurant menus).
    """
    p = Path(path)
    if not p.exists():
        log.warning("Menu file not found: %s", path)
        return []

    ext = p.suffix.lower()
    if ext == ".pdf":
        all_pages = _pdf_to_images(path)
        if len(all_pages) > max_pages:
            log.warning(
                "PDF has %d pages; capping at %d for vision verification",
                len(all_pages), max_pages,
            )
            return all_pages[:max_pages]
        if len(all_pages) > _WARN_PAGES:
            log.info(
                "Large PDF: %d pages sent for vision verification", len(all_pages)
            )
        return all_pages
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
- Name spelling (fix OCR typos — e.g., "Homburg" -> "Hamburg", "88Q" -> "BBQ")
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
                "detail": f"Name: '{orig_name}' ->'{corr_name}'",
            })

        # Price change
        orig_price = _to_float(orig.get("price"))
        corr_price = _to_float(corr.get("price"))
        if orig_price != corr_price:
            changes.append({
                "type": "price_fixed",
                "detail": f"Price for '{corr_name}': ${orig_price:.2f} ->${corr_price:.2f}",
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
                "detail": f"Category for '{corr_name}': '{orig_cat}' ->'{corr_cat}'",
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
            temperature=0,
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
            "Vision verification: %d items ->%d items, %d changes, confidence=%.2f",
            len(extracted_items), len(normalized), len(changes), confidence,
        )

        return {
            "items": normalized,
            "changes": changes,
            "confidence": confidence,
            "model": model,
            "skipped": False,
            "notes": notes,
            "pages_sent": len(image_blocks),
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


# ---------------------------------------------------------------------------
# Day 139.5: Fresh Call 2 — Verify + Gap Scan for detect+assemble pipeline
# ---------------------------------------------------------------------------
# Works NATIVELY with draft-row schema (price_cents, _variants).
# Three jobs:
#   1. Verify: does each item match the text on the menu?
#   2. Infer: shared descriptions, variant headers applying across items
#   3. Gap scan: find menu regions with text that weren't extracted
# ---------------------------------------------------------------------------

_VERIFY_DRAFT_SYSTEM_PROMPT = """\
You are a restaurant menu verification expert. You receive:
1. An image of a restaurant menu
2. A list of items already extracted from this menu
3. Optionally, raw OCR text from the same menu

Your job: audit the extracted items against the menu image, ONE BY ONE. \
Do NOT re-output all items. ONLY output corrections for items that have errors, \
and any items that were missed entirely.

Think through each item carefully. For each one, look at the menu image and verify:
- Is the name spelled correctly?
- Is the price correct? Read the EXACT price from the menu.
- Are the variant labels and prices correct?
- Is the description accurate for THIS item (not a neighboring item)?

WHAT TO OUTPUT:
1. "corrections" — a list of fixes for items that have errors. Each correction \
names the item (by position number) and specifies ONLY the fields that need fixing.
2. "missing_items" — items on the menu that were NOT in the extracted list.
3. "shared_descriptions" — section-level descriptions that should be added to items \
(e.g. "All club sandwiches come with lettuce, tomato, bacon, French fries and pickles").
4. "gap_warnings" — regions of the menu with text you can see but aren't confident \
enough to extract as items.

RULES:
- Be precise with prices. Read them carefully from the menu image. If you can't \
read a price clearly, set "uncertain": true on that correction.
- NEVER remove items. If an item is in the list, it stays.
- NEVER change categories. They are POS-normalized and intentional.
- Prices are in cents (e.g. 1299 = $12.99).
- Output ONLY valid JSON — no markdown, no explanation.\
"""


def _build_verify_draft_prompt(
    draft_rows: List[Dict[str, Any]],
    ocr_text: Optional[str] = None,
) -> str:
    """Build user prompt for corrections-only verification."""
    # Build a compact item list — position, name, price, variants
    lines = []
    for row in draft_rows:
        pos = row.get("position", 0)
        name = row.get("name", "")
        price = row.get("price_cents", 0)
        cat = row.get("category", "Other")
        desc = row.get("description") or ""
        variants = row.get("_variants", [])

        line = f"  #{pos} [{cat}] {name} — ${price / 100:.2f}"
        if desc:
            line += f' — "{desc[:60]}"'
        if variants:
            v_parts = [f'{v.get("label","?")}=${v.get("price_cents",0)/100:.2f}'
                       for v in variants]
            line += f" | variants: {', '.join(v_parts)}"
        lines.append(line)

    items_text = "\n".join(lines)

    ocr_section = ""
    if ocr_text:
        ocr_section = f"""

The following OCR text was extracted from the same menu image via Tesseract. \
Use it to cross-check prices and spelling — it may have the exact characters \
that are hard to read in the image:

---
{ocr_text[:8000]}
---"""

    return f"""\
Here are {len(draft_rows)} items extracted from this menu. \
Audit them against the menu image and report ONLY corrections.
{ocr_section}

Extracted items:
{items_text}

Return JSON in this exact format:
{{
  "corrections": [
    {{
      "position": 5,
      "name": "French Fries",
      "fixes": {{
        "price_cents": 600,
        "description": "Served with ketchup"
      }},
      "reason": "Menu shows $6.00 not $7.00"
    }},
    {{
      "position": 12,
      "name": "Cheese Pizza",
      "variant_fixes": [
        {{"label": "10\\" Mini", "price_cents": 800}},
        {{"label": "12\\" Sm", "price_cents": 1150}}
      ],
      "reason": "Fixed variant prices to match menu columns"
    }}
  ],
  "missing_items": [
    {{
      "name": "Garlic Bread",
      "price_cents": 400,
      "category": "Appetizers",
      "description": null,
      "_variants": []
    }}
  ],
  "shared_descriptions": [
    {{
      "category": "Sandwiches",
      "description": "All club sandwiches come with lettuce, tomato, bacon, French fries and pickles",
      "applies_to": "all"
    }}
  ],
  "gap_warnings": [
    {{
      "description": "Possible items in bottom-right corner, hard to read"
    }}
  ],
  "confidence": 0.92,
  "notes": "Summary of what was checked and fixed"
}}

IMPORTANT:
- "position" in corrections refers to the # number of the item above
- Only include corrections for items that ACTUALLY have errors
- If an item is correct, do NOT include it in corrections
- price_cents is integer cents (e.g. 600 = $6.00)
- variant_fixes replaces ALL variants for that item (include all of them, not just changed ones)
- If you can't read a price clearly, add "uncertain": true to that correction"""


def verify_draft_with_vision(
    image_path: str,
    draft_rows: List[Dict[str, Any]],
    *,
    coord_data: Optional[List[Dict[str, Any]]] = None,
    ocr_text: Optional[str] = None,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = _MAX_TOKENS,
) -> Dict[str, Any]:
    """Verify detect+assemble draft rows against the menu image.

    Day 139.5: Corrections-only Call 2. Does NOT re-output all items.
    Returns only corrections, missing items, and shared descriptions.
    Corrections are applied deterministically in code.

    Args:
        image_path: path to menu image/PDF
        draft_rows: items in draft-row schema from elements_to_draft_rows()
        coord_data: optional bounding box data from Call 1
        ocr_text: optional clean OCR text for price cross-checking
        model: Claude model to use
        max_tokens: max response tokens

    Returns dict with keys:
        items       - corrected draft rows (original + corrections applied)
        coord_data  - coordinate data (unchanged since items aren't restructured)
        changes     - list of {type, detail} changes applied
        gap_warnings - list of flagged regions
        confidence  - Claude's self-reported confidence
        model       - model used
        skipped     - True if verification was skipped
        error       - error message if failed
    """
    _skip = {
        "items": draft_rows,
        "coord_data": coord_data or [],
        "changes": [],
        "gap_warnings": [],
        "confidence": 0.0,
        "model": model,
        "skipped": True,
    }

    if not draft_rows:
        return {**_skip, "skip_reason": "no_items"}

    client = _get_client()
    if client is None:
        log.info("No Anthropic API key; skipping draft verification")
        return {**_skip, "skip_reason": "no_api_key"}

    image_blocks = encode_menu_images(image_path)
    if not image_blocks:
        log.warning("Could not encode menu image: %s", image_path)
        return {**_skip, "skip_reason": "image_encode_failed"}

    # Build multimodal message: images + item list + optional OCR text
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
        "text": _build_verify_draft_prompt(draft_rows, ocr_text=ocr_text),
    })

    try:
        print(f"[Call2] Auditing {len(draft_rows)} items (corrections-only)...")
        with client.messages.stream(
            model=model,
            max_tokens=48000,
            temperature=1,
            thinking={"type": "enabled", "budget_tokens": 10000},
            system=_VERIFY_DRAFT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        ) as stream:
            message = stream.get_final_message()

        # Extract text (skip thinking blocks)
        resp_text = ""
        for block in message.content:
            block_type = getattr(block, "type", None)
            if block_type == "thinking":
                continue
            if hasattr(block, "text"):
                resp_text += block.text

        usage = getattr(message, "usage", None)
        in_tok = getattr(usage, "input_tokens", "?") if usage else "?"
        out_tok = getattr(usage, "output_tokens", "?") if usage else "?"
        print(f"[Call2] Response: in={in_tok}, out={out_tok}")

        if not resp_text.strip():
            print("[Call2] ERROR: empty response")
            return {**_skip, "skipped": False, "error": "empty_response"}

        # Parse JSON
        json_str = resp_text.strip()
        if json_str.startswith("```"):
            json_str = re.sub(r"^```(?:json)?\s*\n?", "", json_str)
            json_str = re.sub(r"\n?```\s*$", "", json_str)

        data = json.loads(json_str)
        if not isinstance(data, dict):
            print("[Call2] ERROR: response is not a dict")
            return {**_skip, "skipped": False, "error": "parse_failed"}

        # --- Apply corrections to a COPY of draft_rows ---
        corrections = data.get("corrections") or []
        missing_items = data.get("missing_items") or []
        shared_descs = data.get("shared_descriptions") or []
        gap_warnings = [
            {"description": str(gw.get("description", ""))}
            for gw in (data.get("gap_warnings") or [])
            if isinstance(gw, dict) and gw.get("description")
        ]

        import copy
        corrected_rows = copy.deepcopy(draft_rows)
        changes: List[Dict[str, str]] = []

        # Build position -> index lookup
        pos_to_idx: Dict[int, int] = {}
        for idx, row in enumerate(corrected_rows):
            pos_to_idx[row.get("position", 0)] = idx

        # Apply field corrections
        for corr in corrections:
            if not isinstance(corr, dict):
                continue
            pos = corr.get("position")
            idx = pos_to_idx.get(pos)
            if idx is None:
                continue

            row = corrected_rows[idx]
            item_name = row.get("name", "?")
            reason = corr.get("reason", "")
            uncertain = corr.get("uncertain", False)

            # Apply field fixes
            fixes = corr.get("fixes") or {}
            for field, new_val in fixes.items():
                if field == "price_cents" and isinstance(new_val, (int, float)):
                    old_val = row.get("price_cents", 0)
                    new_cents = int(round(float(new_val)))
                    if old_val != new_cents:
                        row["price_cents"] = new_cents
                        tag = " (uncertain)" if uncertain else ""
                        changes.append({
                            "type": "price_fixed",
                            "detail": f"Price for '{item_name}': "
                                      f"${old_val / 100:.2f} -> ${new_cents / 100:.2f}{tag}",
                            "reason": reason,
                        })
                elif field == "name" and isinstance(new_val, str):
                    old_name = row.get("name", "")
                    if old_name != new_val.strip():
                        row["name"] = new_val.strip()
                        changes.append({
                            "type": "name_fixed",
                            "detail": f"Name: '{old_name}' -> '{new_val.strip()}'",
                            "reason": reason,
                        })
                elif field == "description":
                    old_desc = row.get("description") or ""
                    new_desc = (new_val or "").strip() if new_val else None
                    if old_desc != (new_desc or ""):
                        row["description"] = new_desc
                        changes.append({
                            "type": "description_fixed",
                            "detail": f"Description updated for '{item_name}'",
                            "reason": reason,
                        })

            # Apply variant fixes (replaces all variants for this item)
            variant_fixes = corr.get("variant_fixes")
            if variant_fixes and isinstance(variant_fixes, list):
                from .ai_menu_extract import _to_float
                old_variants = row.get("_variants") or []
                new_variants = []
                for vi, vf in enumerate(variant_fixes):
                    if not isinstance(vf, dict):
                        continue
                    lbl = (vf.get("label") or "").strip()
                    v_price = 0
                    if "price_cents" in vf:
                        try:
                            v_price = int(round(float(vf["price_cents"])))
                        except (ValueError, TypeError):
                            pass
                    elif "price" in vf:
                        v_price = int(round(_to_float(vf["price"]) * 100))
                    if lbl or v_price > 0:
                        new_variants.append({
                            "label": lbl or f"Size {vi + 1}",
                            "price_cents": v_price,
                            "kind": vf.get("kind", "size"),
                            "position": vi,
                        })
                if new_variants:
                    row["_variants"] = new_variants
                    if row.get("price_cents", 0) == 0:
                        row["price_cents"] = new_variants[0]["price_cents"]
                    changes.append({
                        "type": "variants_changed",
                        "detail": f"Variants updated for '{item_name}' "
                                  f"({len(old_variants)} -> {len(new_variants)})",
                        "reason": reason,
                    })

            # Boost confidence on corrected items
            row["confidence"] = 93

        # Apply shared descriptions
        for sd in shared_descs:
            if not isinstance(sd, dict):
                continue
            cat = (sd.get("category") or "").strip()
            desc = (sd.get("description") or "").strip()
            if not cat or not desc:
                continue
            applied = 0
            for row in corrected_rows:
                if (row.get("category") or "").lower() == cat.lower():
                    existing = (row.get("description") or "").strip()
                    if not existing:
                        row["description"] = desc
                        applied += 1
            if applied:
                changes.append({
                    "type": "description_added",
                    "detail": f"Added shared description to {applied} "
                              f"'{cat}' items: \"{desc[:80]}\"",
                })

        # Add missing items (gap scan discoveries)
        new_coord_data = list(coord_data or [])
        next_pos = max((r.get("position", 0) for r in corrected_rows), default=0) + 1
        for mi in missing_items:
            if not isinstance(mi, dict):
                continue
            name = (mi.get("name") or "").strip()
            if not name:
                continue
            price_cents = 0
            if "price_cents" in mi:
                try:
                    price_cents = int(round(float(mi["price_cents"])))
                except (ValueError, TypeError):
                    pass
            new_row: Dict[str, Any] = {
                "name": name,
                "description": (mi.get("description") or "").strip() or None,
                "price_cents": price_cents,
                "category": (mi.get("category") or "Other").strip(),
                "position": next_pos,
                "confidence": 85,
            }
            # Variants on missing items
            raw_v = mi.get("_variants") or []
            if raw_v:
                variants = []
                for vi, v in enumerate(raw_v):
                    if isinstance(v, dict):
                        lbl = (v.get("label") or "").strip()
                        vp = 0
                        try:
                            vp = int(round(float(v.get("price_cents", 0))))
                        except (ValueError, TypeError):
                            pass
                        if lbl or vp:
                            variants.append({
                                "label": lbl or f"Size {vi + 1}",
                                "price_cents": vp,
                                "kind": v.get("kind", "size"),
                                "position": vi,
                            })
                if variants:
                    new_row["_variants"] = variants
                    if new_row["price_cents"] == 0:
                        new_row["price_cents"] = variants[0]["price_cents"]

            corrected_rows.append(new_row)
            changes.append({
                "type": "item_added",
                "detail": f"Added '{name}' (${price_cents / 100:.2f}) "
                          f"in '{new_row['category']}' -- found on menu but missing",
            })
            next_pos += 1

        # Boost confidence on uncorrected items
        corrected_positions = {c.get("position") for c in corrections if isinstance(c, dict)}
        for row in corrected_rows:
            if row.get("position") not in corrected_positions and row.get("confidence", 0) < 95:
                row["confidence"] = 95

        confidence = 0.0
        try:
            confidence = min(1.0, max(0.0, float(data.get("confidence", 0.0))))
        except (ValueError, TypeError):
            pass

        notes = str(data.get("notes") or "")

        n_corrections = len(corrections)
        n_added = len(missing_items)
        n_shared = len([c for c in changes if c["type"] == "description_added"])
        print(f"[Call2] Applied {n_corrections} corrections, "
              f"+{n_added} missing items, {n_shared} shared descriptions, "
              f"{len(gap_warnings)} gap warnings, confidence={confidence:.2f}")

        return {
            "items": corrected_rows,
            "coord_data": new_coord_data,
            "changes": changes,
            "gap_warnings": gap_warnings,
            "confidence": confidence,
            "model": model,
            "skipped": False,
            "notes": notes,
            "pages_sent": len(image_blocks),
        }

    except json.JSONDecodeError as e:
        print(f"[Call2] JSON parse error: {e}")
        return {
            "items": draft_rows,
            "coord_data": coord_data or [],
            "changes": [],
            "gap_warnings": [],
            "confidence": 0.0,
            "model": model,
            "skipped": False,
            "error": f"json_parse: {e}",
        }
    except Exception as e:
        print(f"[Call2] Exception: {type(e).__name__}: {e}")
        return {
            "items": draft_rows,
            "coord_data": coord_data or [],
            "changes": [],
            "gap_warnings": [],
            "confidence": 0.0,
            "model": model,
            "skipped": False,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Day 139.5v2: Visual Diff Verification — per-category cropped comparison
# ---------------------------------------------------------------------------
# Instead of verifying all items at once, this approach:
#   1. Crops the menu image to just one category's region (zoomed in)
#   2. Formats the extracted items for that category as clean text
#   3. Sends both to Claude: "Compare what we extracted vs what you see"
#   4. Returns corrections-only (same format as above)
# ---------------------------------------------------------------------------

_VISUAL_DIFF_SYSTEM_PROMPT = """\
You are auditing a restaurant menu extraction. You receive:
1. A CROPPED section of the original menu image (zoomed in to one category)
2. A list of items we extracted from that section

Your job: carefully read every item and price in the menu image, then compare \
against what was extracted. Report ONLY the differences.

Go line by line through the menu image. For each item you see:
- Is it in our extracted list? If not, it's missing.
- Is the name spelled correctly?
- Is the price EXACTLY right? Read the price from the image very carefully.
- Are variant/size prices correct? Check each column.
- Is the description accurate?

RULES:
- Be PRECISE with prices. Read each digit carefully from the image.
- If you can't read a price clearly, say so with "uncertain": true.
- NEVER change categories — they are POS-normalized and intentional.
- Only report items that have actual errors or are missing.
- If everything looks correct, return empty corrections.
- Output ONLY valid JSON — no markdown, no explanation.\
"""


def _build_visual_diff_prompt(
    category: str,
    items: List[Dict[str, Any]],
) -> str:
    """Build prompt for per-category visual diff verification."""
    lines = []
    for item in items:
        name = item.get("name", "")
        price = item.get("price_cents", 0)
        desc = item.get("description") or ""
        item_id = item.get("id", 0)
        variants = item.get("variants") or item.get("_variants") or []

        line = f"  [{item_id}] {name} -- ${price / 100:.2f}"
        if desc:
            line += f'  "{desc[:80]}"'
        if variants:
            v_parts = []
            for v in variants:
                lbl = v.get("label", "?")
                vp = v.get("price_cents", 0)
                v_parts.append(f"{lbl}=${vp / 100:.2f}")
            line += f"\n        variants: {', '.join(v_parts)}"
        lines.append(line)

    items_text = "\n".join(lines)

    return f"""\
Category: {category} ({len(items)} items extracted)

Find the "{category}" section on the menu image above, then compare it \
against what we extracted:

{items_text}

Compare the image against our extraction and return ONLY corrections:
{{
  "corrections": [
    {{
      "item_id": 123,
      "name": "French Fries",
      "fixes": {{
        "price_cents": 600,
        "name": "Fixed Name"
      }},
      "variant_fixes": [
        {{"label": "Regular", "price_cents": 600}},
        {{"label": "W/ Cheese", "price_cents": 895}}
      ],
      "reason": "Menu shows $6.00 not $7.00"
    }}
  ],
  "missing_items": [
    {{
      "name": "Garlic Bread",
      "price_cents": 400,
      "description": null,
      "_variants": []
    }}
  ],
  "notes": "Summary of what was checked"
}}

RULES:
- "item_id" matches the [ID] number in the list above
- Only include items that have ACTUAL errors
- price_cents = integer cents (600 = $6.00)
- variant_fixes replaces ALL variants (include all, not just changed ones)
- If everything looks correct, return {{"corrections": [], "missing_items": [], "notes": "All correct"}}"""


def crop_category_region(
    image_path: str,
    items: List[Dict[str, Any]],
    coordinates: Dict[int, Dict[str, Any]],
    *,
    padding_pct: float = 3.0,
) -> Optional[Dict[str, str]]:
    """Crop the menu image to the bounding region of a category's items.

    Args:
        image_path: path to the full menu image
        items: items in this category (need 'id' field)
        coordinates: dict of item_id -> {x_pct, y_pct, w_pct, h_pct, page}
        padding_pct: extra padding around the crop (percentage of image)

    Returns {media_type, data} base64 image block, or None if no coordinates.
    """
    try:
        from PIL import Image
        import io
    except ImportError:
        log.warning("PIL not available for image cropping")
        return None

    # Collect bboxes for items in this category
    bboxes = []
    for item in items:
        item_id = item.get("id")
        if item_id and item_id in coordinates:
            bboxes.append(coordinates[item_id])

    if not bboxes:
        # No coordinates — fall back to full image
        return _encode_image_file(image_path)

    # Compute bounding region (union of all item bboxes)
    min_x = min(b["x_pct"] for b in bboxes)
    min_y = min(b["y_pct"] for b in bboxes)
    max_x = max(b["x_pct"] + b["w_pct"] for b in bboxes)
    max_y = max(b["y_pct"] + b["h_pct"] for b in bboxes)

    # Add padding
    min_x = max(0, min_x - padding_pct)
    min_y = max(0, min_y - padding_pct)
    max_x = min(100, max_x + padding_pct)
    max_y = min(100, max_y + padding_pct)

    # Load image (handle PDFs by converting first page)
    p = Path(image_path)
    if p.suffix.lower() == ".pdf":
        try:
            from pdf2image import convert_from_path
            poppler_path = os.getenv("POPPLER_PATH") or None
            pages = convert_from_path(image_path, dpi=200, poppler_path=poppler_path)
            if not pages:
                return _encode_image_file(image_path) if p.suffix.lower() != ".pdf" else None
            img = pages[0]  # Use first page for now
        except Exception as e:
            log.warning("PDF conversion failed for cropping: %s", e)
            return None
    else:
        img = Image.open(image_path)
    w, h = img.size
    crop_box = (
        int(min_x / 100 * w),
        int(min_y / 100 * h),
        int(max_x / 100 * w),
        int(max_y / 100 * h),
    )
    cropped = img.crop(crop_box)

    # Encode to base64 PNG
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")

    return {"media_type": "image/png", "data": b64}


def verify_category_visual(
    image_path: str,
    category: str,
    items: List[Dict[str, Any]],
    coordinates: Dict[int, Dict[str, Any]],
    *,
    model: str = _DEFAULT_MODEL,
) -> Dict[str, Any]:
    """Verify one category by visual diff: cropped menu image vs extracted items.

    This is the Day 139.5v2 approach — triggered from the wizard per-category,
    not in the background pipeline. Small focused chunks, zoomed in.

    Returns:
        corrections: list of {item_id, fixes, variant_fixes, reason}
        missing_items: list of new items found
        notes: summary
        error: error message if failed
    """
    empty = {"corrections": [], "missing_items": [], "notes": "", "error": None}

    if not items:
        return {**empty, "notes": "No items to verify"}

    client = _get_client()
    if client is None:
        return {**empty, "error": "no_api_key"}

    # Send full menu image — bbox-based cropping is unreliable (Day 139 known issue).
    # Claude finds the right section by category name.
    img_blocks = encode_menu_images(image_path)
    if not img_blocks:
        return {**empty, "error": "image_encode_failed"}
    crop = img_blocks[0]

    # Build message: cropped image + items text
    content: List[Dict[str, Any]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": crop["media_type"],
                "data": crop["data"],
            },
        },
        {
            "type": "text",
            "text": _build_visual_diff_prompt(category, items),
        },
    ]

    resp_text = ""
    try:
        print(f"[VisualDiff] Verifying '{category}' ({len(items)} items)...")
        with client.messages.stream(
            model=model,
            max_tokens=8000,
            temperature=0,
            system=_VISUAL_DIFF_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        ) as stream:
            message = stream.get_final_message()

        resp_text = ""
        for block in message.content:
            if hasattr(block, "text"):
                resp_text += block.text

        usage = getattr(message, "usage", None)
        in_tok = getattr(usage, "input_tokens", "?") if usage else "?"
        out_tok = getattr(usage, "output_tokens", "?") if usage else "?"
        print(f"[VisualDiff] '{category}': in={in_tok}, out={out_tok}")

        if not resp_text.strip():
            return {**empty, "error": "empty_response"}

        # Parse JSON — try to find JSON object in response
        json_str = resp_text.strip()
        if json_str.startswith("```"):
            json_str = re.sub(r"^```(?:json)?\s*\n?", "", json_str)
            json_str = re.sub(r"\n?```\s*$", "", json_str)

        # If response has text before/after JSON, extract the JSON object
        if not json_str.startswith("{"):
            start = json_str.find("{")
            if start >= 0:
                json_str = json_str[start:]
                # Find matching closing brace
                depth = 0
                for i, ch in enumerate(json_str):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            json_str = json_str[:i + 1]
                            break

        if not json_str.strip():
            print(f"[VisualDiff] No JSON found in response: {repr(resp_text[:200])}")
            return {**empty, "error": "no_json_in_response"}

        data = json.loads(json_str)
        if not isinstance(data, dict):
            return {**empty, "error": "invalid_response"}

        corrections = data.get("corrections") or []
        missing_items = data.get("missing_items") or []
        notes = str(data.get("notes") or "")

        n_fixes = len(corrections)
        n_missing = len(missing_items)
        print(f"[VisualDiff] '{category}': {n_fixes} corrections, "
              f"{n_missing} missing items")

        return {
            "corrections": corrections,
            "missing_items": missing_items,
            "notes": notes,
            "error": None,
        }

    except json.JSONDecodeError as e:
        print(f"[VisualDiff] JSON parse error for '{category}': {e}")
        print(f"[VisualDiff] Raw response preview: {repr(resp_text[:500])}")
        return {**empty, "error": f"json_parse: {e}"}
    except Exception as e:
        print(f"[VisualDiff] Error for '{category}': {e}")
        return {**empty, "error": str(e)}


# ---------------------------------------------------------------------------
# Day 139.5: Single-call verification for ALL categories at once
# ---------------------------------------------------------------------------

def verify_all_categories_visual(
    image_path: str,
    items: List[Dict[str, Any]],
    *,
    model: str = _DEFAULT_MODEL,
) -> Dict[str, Any]:
    """Verify all items across all categories in ONE API call.

    Sends the full menu image + all items organized by category.
    Returns corrections-only (same format as verify_category_visual).
    """
    empty = {"corrections": [], "missing_items": [], "notes": "", "error": None}

    if not items:
        return {**empty, "notes": "No items to verify"}

    client = _get_client()
    if client is None:
        return {**empty, "error": "no_api_key"}

    img_blocks = encode_menu_images(image_path)
    if not img_blocks:
        return {**empty, "error": "image_encode_failed"}

    # Group items by category for the prompt
    cat_groups: Dict[str, list] = {}
    for it in items:
        cat = (it.get("category") or "Other").strip()
        cat_groups.setdefault(cat, []).append(it)

    # Build compact item list organized by category
    lines = []
    for cat_name, cat_items in cat_groups.items():
        lines.append(f"\n=== {cat_name} ({len(cat_items)} items) ===")
        for item in cat_items:
            item_id = item.get("id", 0)
            name = item.get("name", "")
            price = item.get("price_cents", 0)
            desc = item.get("description") or ""
            variants = item.get("variants") or item.get("_variants") or []

            line = f"  [{item_id}] {name} -- ${price / 100:.2f}"
            if desc:
                line += f'  "{desc[:60]}"'
            if variants:
                v_parts = [f"{v.get('label','?')}=${v.get('price_cents',0)/100:.2f}"
                           for v in variants]
                line += f"\n        variants: {', '.join(v_parts)}"
            lines.append(line)

    items_text = "\n".join(lines)

    prompt = f"""\
Audit ALL items below against the menu image. Go section by section through \
the menu, find each category, and verify every item.

{items_text}

Return ONLY corrections for items that have errors, plus any missing items:
{{
  "corrections": [
    {{
      "item_id": 123,
      "name": "French Fries",
      "fixes": {{"price_cents": 600}},
      "reason": "Menu shows $6.00 not $7.00"
    }}
  ],
  "missing_items": [
    {{
      "name": "Garlic Bread",
      "price_cents": 400,
      "category": "Appetizers",
      "description": null
    }}
  ],
  "notes": "Summary of verification"
}}

RULES:
- [item_id] matches the number in brackets above
- Only include items with ACTUAL errors — if correct, skip it
- price_cents = integer cents (600 = $6.00)
- variant_fixes replaces ALL variants for that item
- NEVER change categories
- NEVER remove items
- Read prices CAREFULLY from the image — check each digit
- If a price is unclear, add "uncertain": true
- Bread choices (Rye, White, Wheat) are NOT price columns
- Section header notes (e.g. "All sandwiches come with...") are descriptions, not items"""

    content: List[Dict[str, Any]] = []
    for img in img_blocks:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img["media_type"],
                "data": img["data"],
            },
        })
    content.append({"type": "text", "text": prompt})

    resp_text = ""
    try:
        print(f"[Call2] Verifying {len(items)} items across "
              f"{len(cat_groups)} categories (single call)...")
        with client.messages.stream(
            model=model,
            max_tokens=16000,
            temperature=0,
            system=_VISUAL_DIFF_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        ) as stream:
            message = stream.get_final_message()

        for block in message.content:
            if hasattr(block, "text"):
                resp_text += block.text

        usage = getattr(message, "usage", None)
        in_tok = getattr(usage, "input_tokens", "?") if usage else "?"
        out_tok = getattr(usage, "output_tokens", "?") if usage else "?"
        print(f"[Call2] Response: in={in_tok}, out={out_tok}")

        if not resp_text.strip():
            print("[Call2] Empty response")
            return {**empty, "error": "empty_response"}

        # Parse JSON — extract from response
        json_str = resp_text.strip()
        if json_str.startswith("```"):
            json_str = re.sub(r"^```(?:json)?\s*\n?", "", json_str)
            json_str = re.sub(r"\n?```\s*$", "", json_str)
        if not json_str.startswith("{"):
            start = json_str.find("{")
            if start >= 0:
                json_str = json_str[start:]
                depth = 0
                for i, ch in enumerate(json_str):
                    if ch == "{": depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            json_str = json_str[:i + 1]
                            break

        data = json.loads(json_str)
        corrections = data.get("corrections") or []
        missing_items = data.get("missing_items") or []
        notes = str(data.get("notes") or "")

        print(f"[Call2] {len(corrections)} corrections, "
              f"{len(missing_items)} missing items")

        return {
            "corrections": corrections,
            "missing_items": missing_items,
            "notes": notes,
            "error": None,
        }

    except json.JSONDecodeError as e:
        print(f"[Call2] JSON parse error: {e}")
        print(f"[Call2] Raw preview: {repr(resp_text[:500])}")
        return {**empty, "error": f"json_parse: {e}"}
    except Exception as e:
        print(f"[Call2] Error: {e}")
        return {**empty, "error": str(e)}