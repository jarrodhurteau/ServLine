"""
storage/category_infer.py

Lightweight category inference helpers for OCR → menu items.

Goals:
- No heavyweight ML deps.
- Work on plain dicts coming from OCR / ai_cleanup.
- Return a simple category + confidence score + human-readable reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import re


# ------------------------
# Data structures
# ------------------------

@dataclass
class CategoryGuess:
    category: str
    confidence: int  # 0–100
    reason: str = ""


# ------------------------
# Keyword and price heuristics
# ------------------------

# Simple keyword map. These are intentionally "fuzzy" / inclusive.
CATEGORY_KEYWORDS: Dict[str, Sequence[str]] = {
    "Pizza": [
        "pizza", "pie", "sicilian", "neapolitan", "margherita", "slice",
        "toppings", "pizzeria",
    ],
    "Calzones / Stromboli": [
        "calzone", "stromboli", "roll", "stuffed", "folded",
    ],
    "Subs / Sandwiches": [
        "sub", "hoagie", "grinder", "sandwich", "wrap", "panini", "gyro",
    ],
    "Burgers": [
        "burger", "cheeseburger", "patty", "bacon burger",
    ],
    "Wings": [
        "wing", "wings", "buffalo", "boneless", "drumette",
    ],
    "Salads": [
        "salad", "garden", "caesar", "chef salad", "antipasto",
    ],
    "Pasta": [
        "pasta", "spaghetti", "ziti", "penne", "lasagna", "ravioli",
        "alfredo", "carbonara", "bolognese",
    ],
    "Sides / Appetizers": [
        "fries", "fry", "onion rings", "mozzarella stick", "stick",
        "appetizer", "app", "garlic bread", "breadstick", "bread stick",
        "jalapeno popper", "cheese stick",
    ],
    "Desserts": [
        "dessert", "brownie", "cookie", "cheesecake", "tiramisu",
        "cannoli", "ice cream", "lava cake", "cinnamon",
    ],
    "Beverages": [
        "soda", "pop", "drink", "beverage", "juice", "tea", "coffee",
        "coke", "pepsi", "sprite", "mountain dew", "root beer", "bottle",
        "can", "2 liter", "2-liter", "liter",
    ],
}

# Very rough price bands per category (USD cents).
CATEGORY_PRICE_BANDS: Dict[str, Tuple[int, int]] = {
    # (min_cents, max_cents)
    "Pizza": (799, 3999),
    "Calzones / Stromboli": (899, 2499),
    "Subs / Sandwiches": (699, 1999),
    "Burgers": (699, 1999),
    "Wings": (699, 2499),
    "Salads": (499, 1599),
    "Pasta": (899, 2499),
    "Sides / Appetizers": (299, 1499),
    "Desserts": (299, 1499),
    "Beverages": (99, 799),
}


# ------------------------
# Text helpers
# ------------------------

_whitespace_re = re.compile(r"\s+")


def _norm(text: Optional[str]) -> str:
    if not text:
        return ""
    text = text.strip().lower()
    return _whitespace_re.sub(" ", text)


def _keyword_score(text: str, category: str) -> int:
    """Simple keyword scoring: count matches; weight name/desc later."""
    if not text:
        return 0
    score = 0
    for kw in CATEGORY_KEYWORDS.get(category, ()):
        if kw in text:
            score += 1
    return score


def _price_band_score(price_cents: int, category: str) -> int:
    """Return a small bonus if price falls in the expected band."""
    if price_cents <= 0:
        return 0
    band = CATEGORY_PRICE_BANDS.get(category)
    if not band:
        return 0
    lo, hi = band
    if lo <= price_cents <= hi:
        return 2  # modest but meaningful nudge
    # Slight penalty if wildly outside band
    if price_cents < lo // 2 or price_cents > hi * 2:
        return -1
    return 0


def _neighbor_score(
    category: str,
    neighbor_categories: Optional[Sequence[str]],
) -> int:
    """
    Neighbor heuristic:
    - If at least 2 neighbors are the same category → bonus.
    - If neighbors mostly a different category → tiny penalty.
    """
    if not neighbor_categories:
        return 0
    normalized = [c for c in neighbor_categories if c]
    if not normalized:
        return 0
    same = sum(1 for c in normalized if c == category)
    total = len(normalized)

    if same >= 2:
        return 2  # we "flow" with nearby items
    if same == 0 and total >= 2:
        return -1  # we stand out vs context (tiny penalty)
    return 0


# ------------------------
# Core inference
# ------------------------

def infer_category_for_text(
    name: Optional[str],
    description: Optional[str] = None,
    price_cents: int = 0,
    neighbor_categories: Optional[Sequence[str]] = None,
    fallback: str = "Uncategorized",
) -> CategoryGuess:
    """
    Infer a category given raw text and price.

    Returns a CategoryGuess with:
    - category: chosen label (or fallback)
    - confidence: 0–100
    - reason: short human-readable explanation
    """
    name_norm = _norm(name)
    desc_norm = _norm(description)

    # Short-circuit: nothing to go on at all.
    if not name_norm and not desc_norm and price_cents <= 0:
        return CategoryGuess(
            category=fallback,
            confidence=5,
            reason="no name/description/price; using fallback",
        )

    best_category: Optional[str] = None
    best_raw_score = -999

    # Evaluate all known categories
    for category in CATEGORY_KEYWORDS.keys():
        score = 0

        # Name is strongest signal.
        score += _keyword_score(name_norm, category) * 4

        # Description is weaker.
        score += _keyword_score(desc_norm, category) * 2

        # Price band (small influence).
        score += _price_band_score(price_cents, category)

        # Neighbor categories.
        score += _neighbor_score(category, neighbor_categories)

        if score > best_raw_score:
            best_raw_score = score
            best_category = category

    # If the best score is non-positive, we might not trust it.
    if best_raw_score <= 0:
        # If we have a price that strongly looks like a drink,
        # we can still take Beverages as a mild guess.
        beverage_hint = (
            "Beverages" if 0 < price_cents <= 799 else None
        )
        if beverage_hint:
            return CategoryGuess(
                category=beverage_hint,
                confidence=35,
                reason="weak text match but price looks like a drink",
            )

        return CategoryGuess(
            category=fallback,
            confidence=15,
            reason="no strong keyword or price signal; using fallback",
        )

    # Map raw score into a 40–95 band.
    # raw_score of ~1 → ~45, larger → up to ~95
    raw = float(best_raw_score)
    # Slightly compress extremes so we don't hit 100 all the time.
    confidence = 40 + int(min(raw * 6.0, 55))  # max 95

    reason_bits: List[str] = []
    if name_norm:
        reason_bits.append("matched name keywords")
    if desc_norm:
        reason_bits.append("matched description keywords")
    if price_cents > 0:
        reason_bits.append("price fell in expected band")
    if neighbor_categories:
        reason_bits.append("neighbors support this category")

    reason = ", ".join(reason_bits) or "heuristic match"

    return CategoryGuess(
        category=best_category or fallback,
        confidence=confidence,
        reason=reason,
    )


def infer_category_for_item(
    item: Dict[str, Any],
    neighbor_categories: Optional[Sequence[str]] = None,
    fallback: str = "Uncategorized",
    price_field: str = "price_cents",
) -> CategoryGuess:
    """
    Convenience wrapper for dict-shaped items coming from OCR / ai_cleanup.

    Expects keys like:
      - name
      - description (optional)
      - price_cents (or configurable via price_field)

    Does NOT mutate the item. Use apply_inference_to_items() to mutate.
    """
    name = item.get("name") or ""
    description = item.get("description") or ""

    price_raw = item.get(price_field, 0) or 0
    try:
        # tolerate strings like "12.99" or "12"
        if isinstance(price_raw, str):
            if "." in price_raw:
                price_cents = int(round(float(price_raw) * 100))
            else:
                price_cents = int(price_raw) * 100
        else:
            price_cents = int(price_raw)
    except Exception:
        price_cents = 0

    return infer_category_for_text(
        name=name,
        description=description,
        price_cents=price_cents,
        neighbor_categories=neighbor_categories,
        fallback=fallback,
    )


def apply_inference_to_items(
    items: Iterable[Dict[str, Any]],
    fallback: str = "Uncategorized",
    price_field: str = "price_cents",
) -> List[Dict[str, Any]]:
    """
    Apply category inference to a sequence of items.

    Returns a NEW list of item dicts with:
      - item["category"] set (if not already or if fallback)
      - item["category_confidence"] set to 0–100
      - item["category_source"] = "inferred" | "existing"

    We also pass neighbor categories (previous/next) as a soft signal.
    """
    items_list = list(items)
    out: List[Dict[str, Any]] = []

    # Precompute existing categories for neighbor context
    existing_cats: List[Optional[str]] = [
        (itm.get("category") if itm.get("category") not in (None, "", fallback) else None)
        for itm in items_list
    ]

    for idx, itm in enumerate(items_list):
        left = existing_cats[idx - 1] if idx - 1 >= 0 else None
        right = existing_cats[idx + 1] if idx + 1 < len(existing_cats) else None
        neighbors = [c for c in (left, right) if c]

        guess = infer_category_for_item(
            itm,
            neighbor_categories=neighbors,
            fallback=fallback,
            price_field=price_field,
        )

        new_itm = dict(itm)  # shallow copy to avoid mutating caller input

        # Only override when:
        # - no category is set, or
        # - category is fallback / blank.
        current_cat = (itm.get("category") or "").strip()
        if not current_cat or current_cat == fallback:
            new_itm["category"] = guess.category
            new_itm["category_source"] = "inferred"
        else:
            new_itm["category"] = current_cat
            new_itm["category_source"] = "existing"

        new_itm["category_confidence"] = guess.confidence
        out.append(new_itm)

    return out
