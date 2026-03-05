# storage/ai_reconcile.py
"""
Claude Targeted Reconciliation — Call 3 in the production pipeline.

Takes a menu image + ONLY items flagged by the semantic pipeline (Step 4)
and asks Claude to surgically verify/correct them.  This is a small, cheap
call that reviews 3-10 items (not the full menu).

Catches what neither Claude alone nor pipeline alone finds:
  - Pipeline flags $9.99 steak as price outlier → Claude re-checks image
  - Pipeline flags garbled name → Claude reads actual name from image
  - Pipeline flags missing description → Claude reads it from image

Usage:
    from storage.ai_reconcile import reconcile_flagged_items, collect_flagged_items

    flagged = collect_flagged_items(semantic_items)
    result = reconcile_flagged_items(image_path, flagged)
    # result = {
    #     "items":            [...],   # reconciled item list (flagged subset)
    #     "changes":          [...],   # log of what changed
    #     "confidence":       0.95,    # Claude's self-reported confidence
    #     "model":            "...",
    #     "skipped":          False,
    #     "items_confirmed":  5,
    #     "items_corrected":  2,
    #     "items_not_found":  0,
    # }

Requires ANTHROPIC_API_KEY in environment.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Reuse shared Anthropic client + helpers
from .ai_menu_extract import _get_client, _to_float, _normalize_sizes
from .ai_vision_verify import encode_menu_images

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
_MAX_TOKENS = 8_000           # Smaller than Call 2 — only reviewing a few items
MAX_RECONCILE_ITEMS = 10      # Hard cap: never send more than 10 items
CONFIDENCE_BUMP_CONFIRMED = 5  # +5 to confidence (0-100 scale) for confirmed items
CONFIDENCE_CORRECTED_VALUE = 92  # Set confidence for corrected items

_TIER_PRIORITY = {"reject": 0, "low": 1, "medium": 2, "high": 3}

_VALID_STATUSES = frozenset({"confirmed", "corrected", "not_found"})


# ---------------------------------------------------------------------------
# Collect flagged items
# ---------------------------------------------------------------------------
def collect_flagged_items(
    semantic_items: List[Dict[str, Any]],
    *,
    max_items: int = MAX_RECONCILE_ITEMS,
) -> List[Dict[str, Any]]:
    """Filter and prioritize items that need targeted reconciliation.

    Selects items where needs_review=True OR semantic_tier in (low, reject).
    Prioritizes by severity: reject first, then low, then medium.
    Within same tier, sorts by semantic_confidence ascending (worst first).
    Caps at *max_items*.

    Returns list of item dicts (references to originals).
    """
    if not semantic_items:
        return []

    flagged = [
        item for item in semantic_items
        if item.get("needs_review")
        or item.get("semantic_tier") in ("reject", "low")
    ]

    flagged.sort(key=lambda it: (
        _TIER_PRIORITY.get(it.get("semantic_tier", "reject"), 0),
        it.get("semantic_confidence", 0.0),
    ))

    return flagged[:max_items]


# ---------------------------------------------------------------------------
# Concern summarization
# ---------------------------------------------------------------------------
def _summarize_item_concerns(item: Dict[str, Any]) -> List[str]:
    """Extract human-readable concern strings from an item's semantic annotations."""
    concerns: List[str] = []

    tier = item.get("semantic_tier", "unknown")
    sc = item.get("semantic_confidence", 0.0)
    concerns.append(f"Confidence tier: {tier} ({sc:.2f})")

    for flag in (item.get("price_flags") or []):
        msg = flag.get("message")
        if msg:
            concerns.append(msg)

    # Only include non-auto-fixable recs (auto-fixable ones were already applied)
    for rec in (item.get("repair_recommendations") or []):
        if not rec.get("auto_fixable"):
            msg = rec.get("message")
            if msg:
                concerns.append(msg)

    return concerns


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are a restaurant menu verification expert performing a TARGETED review. \
You receive:
1. An image of a restaurant menu
2. A small set of specific items (3-10) that were flagged for potential issues

Your job is to look at the menu image and verify or correct ONLY these flagged \
items. For each item, the system has identified specific concerns.

Rules:
1. Return ONLY the flagged items (do NOT add new items or review unflagged items).
2. For each item, set status to "confirmed" if it looks correct on the menu, \
or "corrected" if you fixed something.
3. If an item does not appear on the menu at all, set status to "not_found".
4. Fix OCR typos, wrong prices, wrong categories only when you can clearly see \
the correct value on the menu image.
5. When uncertain, prefer "confirmed" (preserve original) over guessing.
6. Output ONLY valid JSON — no markdown, no explanation.\
"""


def _build_reconciliation_prompt(flagged_items: List[Dict[str, Any]]) -> str:
    """Build the user prompt listing flagged items with their specific concerns."""
    entries = []
    for item in flagged_items:
        price_dollars = (item.get("price_cents") or 0) / 100.0
        # Build sizes from _variants or variants
        variants = item.get("_variants") or item.get("variants") or []
        sizes = []
        for v in variants:
            if isinstance(v, dict) and v.get("kind") == "size":
                lbl = v.get("label", "")
                pr = (v.get("price_cents") or 0) / 100.0
                sizes.append({"label": lbl, "price": pr})

        entry = {
            "name": item.get("name", ""),
            "price": price_dollars,
            "category": item.get("category", "Other"),
            "description": item.get("description"),
            "sizes": sizes,
            "concerns": _summarize_item_concerns(item),
        }
        entries.append(entry)

    items_json = json.dumps(entries, indent=2)
    return f"""\
Here are {len(entries)} items flagged by our quality pipeline. \
Please verify each one against the menu image.

Flagged items with concerns:
{items_json}

Return JSON in this exact format:
{{
  "items": [
    {{
      "name": "Item Name",
      "description": "Description or null",
      "price": 12.95,
      "category": "Category",
      "sizes": [{{"label": "10\\"", "price": 12.95}}],
      "status": "confirmed" or "corrected" or "not_found",
      "changes": ["Description of what was changed"]
    }}
  ],
  "confidence": 0.95,
  "notes": "Brief summary of reconciliation results"
}}"""


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
def _parse_reconciliation_response(
    raw_text: str,
) -> Tuple[Optional[List[Dict[str, Any]]], float, str]:
    """Parse Claude's JSON reconciliation response.

    Returns (items, confidence, notes).
    items is None on parse failure.
    """
    json_str = raw_text.strip()

    # Strip markdown code fences if present
    if json_str.startswith("```"):
        json_str = re.sub(r"^```(?:json)?\s*\n?", "", json_str)
        json_str = re.sub(r"\n?```\s*$", "", json_str)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        log.warning("Failed to parse reconciliation JSON: %s", e)
        return None, 0.0, ""

    if not isinstance(data, dict):
        return None, 0.0, ""

    items = data.get("items")
    if not isinstance(items, list):
        return None, 0.0, ""

    # Default missing status to "confirmed"
    for item in items:
        if isinstance(item, dict):
            status = item.get("status", "")
            if status not in _VALID_STATUSES:
                item["status"] = "confirmed"
            if "changes" not in item:
                item["changes"] = []

    confidence = 0.0
    try:
        confidence = min(1.0, max(0.0, float(data.get("confidence", 0.0))))
    except (ValueError, TypeError):
        pass

    notes = str(data.get("notes") or "")

    return items, confidence, notes


# ---------------------------------------------------------------------------
# Item normalization
# ---------------------------------------------------------------------------
def _normalize_reconciled_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize items from Claude's reconciliation response."""
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
            "category": (it.get("category") or "").strip() or "Other",
            "sizes": _normalize_sizes(it.get("sizes")),
            "status": it.get("status", "confirmed"),
            "changes": it.get("changes") or [],
        })
    return result


# ---------------------------------------------------------------------------
# Main reconciliation function
# ---------------------------------------------------------------------------
def reconcile_flagged_items(
    image_path: str,
    flagged_items: List[Dict[str, Any]],
    *,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = _MAX_TOKENS,
) -> Dict[str, Any]:
    """Send menu image + flagged items to Claude for targeted reconciliation.

    This is a surgical, cheap Claude call that only reviews 3-10 items
    flagged by the semantic pipeline, not the entire menu.

    Returns dict with keys:
        items             - list of reconciled items (with status + changes)
        changes           - list of {type, detail} change entries
        confidence        - Claude's self-reported confidence (0.0-1.0)
        model             - model used
        skipped           - True if reconciliation was skipped
        skip_reason       - reason for skip (if skipped)
        error             - error message (if failed)
        notes             - Claude's notes
        items_confirmed   - count of confirmed items
        items_corrected   - count of corrected items
        items_not_found   - count of not-found items
    """
    _base = {
        "model": model,
        "notes": "",
        "items_confirmed": 0,
        "items_corrected": 0,
        "items_not_found": 0,
    }

    # Guard: no flagged items
    if not flagged_items:
        return {
            **_base,
            "items": [],
            "changes": [],
            "confidence": 0.0,
            "skipped": True,
            "skip_reason": "no_flagged_items",
        }

    # Guard: no API client
    client = _get_client()
    if client is None:
        log.info("No Anthropic API key; skipping targeted reconciliation")
        return {
            **_base,
            "items": flagged_items,
            "changes": [],
            "confidence": 0.0,
            "skipped": True,
            "skip_reason": "no_api_key",
        }

    # Encode image(s)
    image_blocks = encode_menu_images(image_path)
    if not image_blocks:
        log.warning("Could not encode menu image: %s", image_path)
        return {
            **_base,
            "items": flagged_items,
            "changes": [],
            "confidence": 0.0,
            "skipped": True,
            "skip_reason": "image_encode_failed",
        }

    # Build multimodal message
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
        "text": _build_reconciliation_prompt(flagged_items),
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
            log.warning("Claude reconciliation returned empty response")
            return {
                **_base,
                "items": flagged_items,
                "changes": [],
                "confidence": 0.0,
                "skipped": False,
                "error": "empty_response",
            }

        # Parse JSON
        reconciled_items, confidence, notes = _parse_reconciliation_response(resp_text)

        if reconciled_items is None:
            return {
                **_base,
                "items": flagged_items,
                "changes": [],
                "confidence": 0.0,
                "skipped": False,
                "error": "parse_failed",
            }

        # Normalize
        normalized = _normalize_reconciled_items(reconciled_items)

        # Compute changes log
        changes = _compute_reconciliation_changes(flagged_items, normalized)

        # Count statuses
        confirmed = sum(1 for it in normalized if it.get("status") == "confirmed")
        corrected = sum(1 for it in normalized if it.get("status") == "corrected")
        not_found = sum(1 for it in normalized if it.get("status") == "not_found")

        log.info(
            "Targeted reconciliation: %d flagged → %d confirmed, %d corrected, "
            "%d not_found, confidence=%.2f",
            len(flagged_items), confirmed, corrected, not_found, confidence,
        )

        return {
            "items": normalized,
            "changes": changes,
            "confidence": confidence,
            "model": model,
            "skipped": False,
            "notes": notes,
            "items_confirmed": confirmed,
            "items_corrected": corrected,
            "items_not_found": not_found,
        }

    except Exception as e:
        log.warning("Claude reconciliation API call failed: %s", e)
        return {
            **_base,
            "items": flagged_items,
            "changes": [],
            "confidence": 0.0,
            "skipped": False,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Changes log
# ---------------------------------------------------------------------------
def _compute_reconciliation_changes(
    original: List[Dict[str, Any]],
    reconciled: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Build a changes log comparing original flagged items to reconciled results."""
    changes: List[Dict[str, str]] = []

    # Index originals by normalized name
    orig_by_name: Dict[str, Dict[str, Any]] = {}
    for it in original:
        key = (it.get("name") or "").strip().lower()
        if key:
            orig_by_name[key] = it

    for rec in reconciled:
        name = rec.get("name", "")
        status = rec.get("status", "confirmed")
        key = name.strip().lower()

        if status == "not_found":
            changes.append({
                "type": "not_found",
                "detail": f"'{name}' not found on menu image — flagged for review",
            })
            continue

        # Try to find original by normalized name
        orig = orig_by_name.get(key)
        if orig is None:
            # Try matching by checking if any original name lowered matches
            # the reconciled name lowered (Claude may have corrected the name)
            for okey, oval in orig_by_name.items():
                # Check if this original hasn't been matched yet
                # Match by position in the list if names differ
                pass
            # If still no match, check Claude's own changes field
            if rec.get("changes"):
                changes.append({
                    "type": "corrected",
                    "detail": f"'{name}': {'; '.join(rec['changes'])}",
                })
            else:
                changes.append({
                    "type": "no_match",
                    "detail": f"Reconciled item '{name}' did not match any original item",
                })
            continue

        if status == "confirmed":
            changes.append({
                "type": "confirmed",
                "detail": f"Confirmed '{name}' — no changes needed",
            })
            continue

        # status == "corrected" — log specific field changes
        orig_name = (orig.get("name") or "").strip()
        if orig_name != name:
            changes.append({
                "type": "name_corrected",
                "detail": f"Name: '{orig_name}' → '{name}'",
            })

        orig_price = (orig.get("price_cents") or 0) / 100.0
        rec_price = rec.get("price", 0.0)
        if abs(orig_price - rec_price) > 0.005:
            changes.append({
                "type": "price_corrected",
                "detail": f"Price for '{name}': ${orig_price:.2f} → ${rec_price:.2f}",
            })

        orig_cat = (orig.get("category") or "").strip()
        rec_cat = (rec.get("category") or "").strip()
        if orig_cat != rec_cat:
            changes.append({
                "type": "category_corrected",
                "detail": f"Category for '{name}': '{orig_cat}' → '{rec_cat}'",
            })

        orig_desc = (orig.get("description") or "").strip()
        rec_desc = (rec.get("description") or "").strip()
        if orig_desc != rec_desc:
            changes.append({
                "type": "description_corrected",
                "detail": f"Description updated for '{name}'",
            })

    return changes


# ---------------------------------------------------------------------------
# Merge reconciled items back into the full list
# ---------------------------------------------------------------------------
def merge_reconciled_items(
    all_items: List[Dict[str, Any]],
    reconciled_items: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Merge reconciled corrections back into the full item list.

    Matches reconciled items to originals by normalized name.
    For "confirmed" items: bumps confidence by CONFIDENCE_BUMP_CONFIRMED.
    For "corrected" items: updates fields and sets confidence to CONFIDENCE_CORRECTED_VALUE.
    For "not_found" items: no changes (left for human review).

    Args:
        all_items:        Full list of draft items (mutated in place).
        reconciled_items: Items returned from reconcile_flagged_items().

    Returns (all_items, changes_log).
    """
    changes: List[Dict[str, str]] = []

    # Index reconciled items by normalized name
    rec_by_name: Dict[str, Dict[str, Any]] = {}
    for rec in reconciled_items:
        key = (rec.get("name") or "").strip().lower()
        if key:
            rec_by_name[key] = rec

    matched_keys: set = set()

    for item in all_items:
        item_name = (item.get("name") or "").strip()
        key = item_name.lower()
        rec = rec_by_name.get(key)
        if rec is None:
            continue

        matched_keys.add(key)
        status = rec.get("status", "confirmed")

        if status == "confirmed":
            old_conf = item.get("confidence", 0)
            item["confidence"] = min(100, old_conf + CONFIDENCE_BUMP_CONFIRMED)
            changes.append({
                "type": "confirmed",
                "detail": f"Confirmed '{item_name}' — confidence {old_conf} → {item['confidence']}",
            })

        elif status == "corrected":
            rec_name = (rec.get("name") or "").strip()
            rec_price = rec.get("price", 0.0)
            rec_price_cents = int(round(rec_price * 100))
            rec_cat = (rec.get("category") or "").strip()
            rec_desc = (rec.get("description") or "").strip() or None

            if rec_name and rec_name != item_name:
                changes.append({
                    "type": "name_corrected",
                    "detail": f"Name: '{item_name}' → '{rec_name}'",
                })
                item["name"] = rec_name

            old_price = item.get("price_cents", 0)
            if rec_price_cents != old_price:
                changes.append({
                    "type": "price_corrected",
                    "detail": f"Price for '{rec_name or item_name}': "
                              f"${old_price / 100:.2f} → ${rec_price_cents / 100:.2f}",
                })
                item["price_cents"] = rec_price_cents

            old_cat = (item.get("category") or "").strip()
            if rec_cat and rec_cat != old_cat:
                changes.append({
                    "type": "category_corrected",
                    "detail": f"Category for '{rec_name or item_name}': '{old_cat}' → '{rec_cat}'",
                })
                item["category"] = rec_cat

            old_desc = (item.get("description") or "").strip() or None
            if rec_desc != old_desc:
                changes.append({
                    "type": "description_corrected",
                    "detail": f"Description updated for '{rec_name or item_name}'",
                })
                item["description"] = rec_desc

            item["confidence"] = CONFIDENCE_CORRECTED_VALUE

        elif status == "not_found":
            changes.append({
                "type": "not_found",
                "detail": f"'{item_name}' not found on menu image — left for review",
            })

    # Log reconciled items that didn't match any original
    for rec in reconciled_items:
        key = (rec.get("name") or "").strip().lower()
        if key and key not in matched_keys:
            changes.append({
                "type": "no_match",
                "detail": f"Reconciled item '{rec.get('name')}' did not match any existing item",
            })

    return all_items, changes
