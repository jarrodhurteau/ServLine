# storage/menu_classifier.py
"""
Post-scrape classification of competitor menu items.

Raw JSON-LD / Apify scrapes return everything as "menu items" — primary
dishes, standalone sides, sauce ramekins, size variants, drinks. For
price-comparison purposes, we only care about primary dishes, sides,
and desserts. Sauces, toppings, and size variants bloat the dataset
and drag down category averages.

This module runs a single Haiku call per competitor menu to attach two
fields to each item:
  - role: "dish" | "side" | "drink" | "dessert" | "modifier" | "variant"
  - canonical_name: the logical item name (so WINGS (10), WINGS (20),
    WINGS (50) all collapse to "Wings")

Cost: ~$0.01-0.02 per competitor menu. Cached as part of competitor_menus
so each ZIP pays once per 30-day cache cycle.

Used by storage.price_intel after scrape_competitor_menu() produces items.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

# Keep prompts short but precise — clearer input, less noise.
_SYSTEM = (
    "You classify menu items for price comparison. For each item, pick a "
    "role from {dish, side, drink, dessert, modifier, variant} and output "
    "a canonical_name (the logical item without size suffixes).\n\n"
    "Definitions:\n"
    "- dish: primary orderable menu item (Pepperoni Pizza, Ribeye Steak)\n"
    "- side: standalone orderable side (French Fries, Garlic Bread)\n"
    "- drink: beverages (Soda, Coffee, Beer)\n"
    "- dessert: desserts (Tiramisu, Ice Cream)\n"
    "- modifier: add-on/extra/sauce ramekin/topping upgrade, usually <$3\n"
    "  (Side BBQ Sauce, Extra Cheese, Add Bacon)\n"
    "- variant: a size/portion variant of another item, usually a sibling\n"
    "  with a (10), (20), (SM), (LG) suffix. Pick the base item's logical\n"
    "  name as canonical_name (WINGS (20) -> canonical_name 'Wings',\n"
    "  role 'variant').\n\n"
    "canonical_name rules: strip size/portion suffixes, normalize casing "
    "to Title Case. Keep flavor/style distinctions (Margherita Pizza and "
    "Pepperoni Pizza are different dishes, not variants)."
)


def _get_client():
    """Lazy-init Anthropic client; returns None if API key missing."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except Exception as e:
        log.warning("menu_classifier: anthropic client init failed: %s", e)
        return None


def _chunk(items: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def classify_menu_items(
    place_name: str,
    items: List[Dict[str, Any]],
    batch_size: int = 120,
) -> List[Dict[str, Any]]:
    """
    Return the input `items` with `role` and `canonical_name` attached.

    Falls back to returning items unchanged (role=None) if the API is
    unavailable or the classification call fails. Never raises.
    """
    if not items:
        return items
    client = _get_client()
    if not client:
        log.info("menu_classifier: no API key, returning items unclassified")
        return items

    # Build compact input: just what Haiku needs to classify.
    # Include index so we can map the response back unambiguously.
    compact = [
        {
            "i": idx,
            "name": it.get("name", ""),
            "category": it.get("category", ""),
            "price_cents": it.get("price_cents", 0),
        }
        for idx, it in enumerate(items)
    ]

    classifications: Dict[int, Dict[str, str]] = {}

    for batch_idx, batch in enumerate(_chunk(compact, batch_size)):
        prompt = (
            f"Restaurant: {place_name}\n\n"
            f"Classify these {len(batch)} items. Return a JSON array where "
            f"each element matches by index `i`:\n"
            f"  [{{\"i\": <index>, \"role\": \"...\", \"canonical_name\": \"...\"}}]\n\n"
            f"Return ONLY the JSON array, no prose.\n\n"
            f"Items:\n{json.dumps(batch, ensure_ascii=False)}"
        )
        try:
            resp = client.messages.create(
                model=_MODEL,
                max_tokens=8000,
                system=_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = ""
            for block in resp.content:
                if getattr(block, "text", None):
                    text += block.text
            parsed = _parse_json_array(text)
            for row in parsed:
                if not isinstance(row, dict):
                    continue
                try:
                    idx = int(row.get("i"))
                except (TypeError, ValueError):
                    continue
                role = (row.get("role") or "").strip().lower()
                canonical = (row.get("canonical_name") or "").strip()
                if role in _VALID_ROLES and canonical:
                    classifications[idx] = {"role": role, "canonical_name": canonical}
            log.info(
                "menu_classifier: batch %d/%d, %d items, %d classified",
                batch_idx + 1, (len(compact) + batch_size - 1) // batch_size,
                len(batch), sum(1 for i in range(len(batch)) if (batch_idx * batch_size + i) in classifications),
            )
        except Exception as e:
            log.warning("menu_classifier: batch %d failed: %s", batch_idx + 1, e)

    out: List[Dict[str, Any]] = []
    for idx, it in enumerate(items):
        new = dict(it)
        tag = classifications.get(idx)
        if tag:
            new["role"] = tag["role"]
            new["canonical_name"] = tag["canonical_name"]
        else:
            # Unclassified: keep item, let consumers decide what to do.
            new.setdefault("role", None)
            new.setdefault("canonical_name", it.get("name"))
        out.append(new)
    return out


_VALID_ROLES = {"dish", "side", "drink", "dessert", "modifier", "variant"}


def _parse_json_array(raw: str) -> List[Any]:
    """Best-effort parse: strip markdown fencing, find the JSON array."""
    if not raw:
        return []
    s = raw.strip()
    if s.startswith("```"):
        # Strip opening fence (```json or ```)
        first_nl = s.find("\n")
        if first_nl > 0:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
    # Isolate the array
    lo = s.find("[")
    hi = s.rfind("]")
    if lo < 0 or hi <= lo:
        return []
    try:
        return json.loads(s[lo:hi + 1])
    except json.JSONDecodeError:
        return []


def filter_comparison_items(
    items: List[Dict[str, Any]],
    include_roles: Optional[set] = None,
    collapse_variants: bool = True,
) -> List[Dict[str, Any]]:
    """
    Trim a classified item list to what's useful for price comparison.

    - include_roles defaults to {dish, side, dessert}. Skips drinks and
      modifiers so sauce ramekins don't pollute category averages.
    - collapse_variants merges items sharing a canonical_name into one
      synthetic item with min/max price, so WINGS (10/20/50) becomes one
      logical "Wings" entry with a price range.
    """
    if include_roles is None:
        include_roles = {"dish", "side", "dessert"}

    filtered = [
        it for it in items
        if (it.get("role") in include_roles) or it.get("role") is None
    ]

    if not collapse_variants:
        return filtered

    # Collapse `variant` rows with their sibling `dish`/`side`. We still
    # want to include items flagged as variants (for price-range context),
    # but merged into a single canonical entry.
    by_canonical: Dict[str, Dict[str, Any]] = {}
    extras: List[Dict[str, Any]] = []
    for it in items:
        canonical = (it.get("canonical_name") or it.get("name") or "").strip()
        role = it.get("role")
        price = it.get("price_cents") or 0
        if role == "variant" and canonical:
            bucket = by_canonical.setdefault(canonical, {
                "name": canonical,
                "canonical_name": canonical,
                "category": it.get("category"),
                "role": "dish",
                "price_cents_min": price,
                "price_cents_max": price,
                "price_cents": price,
                "variant_count": 0,
            })
            bucket["variant_count"] += 1
            if price:
                if not bucket["price_cents_min"] or price < bucket["price_cents_min"]:
                    bucket["price_cents_min"] = price
                if price > bucket["price_cents_max"]:
                    bucket["price_cents_max"] = price
                # Keep representative single price as the lowest (smallest size)
                bucket["price_cents"] = bucket["price_cents_min"]
        elif role in include_roles or role is None:
            extras.append(it)

    return extras + list(by_canonical.values())
