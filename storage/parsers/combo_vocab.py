# storage/parsers/combo_vocab.py
"""
Combo Food Vocabulary — Sprint 8.2 Day 58

Single source of truth for combo side-item detection.
Used by menu_grammar.py (parsing) and variant_engine.py (kind classification).

Combo foods are side items that appear after "w/" or "with" to indicate a
combo upgrade (e.g., "9.95 W/FRIES 13.50" means base $9.95, with-fries $13.50).
"""

from __future__ import annotations

import re
from typing import List, Set

# ── Combo food vocabulary ────────────────────────────

COMBO_FOODS: Set[str] = {
    # Fried sides
    "fries", "frie", "french fries", "curly fries", "waffle fries",
    "sweet potato fries", "steak fries", "seasoned fries",
    "onion rings", "onion ring", "tater tots", "tots",
    "fried pickles", "fried mushrooms",
    # Chips
    "chips", "chip", "potato chips",
    # Salads & slaws
    "coleslaw", "cole slaw", "slaw",
    "side salad", "garden salad", "caesar salad", "house salad", "salad",
    # Vegetables
    "vegetables", "veggies", "mixed vegetables",
    # Carbs / starches
    "rice", "fried rice", "white rice", "brown rice",
    "mashed potatoes", "mashed potato", "baked potato",
    "potato salad", "mac and cheese", "macaroni and cheese",
    # Cheese add-ons
    "cheese", "extra cheese",
    # Drinks
    "drink", "soda", "beverage", "fountain drink",
    # Soup
    "soup", "side soup", "cup of soup",
    # Bread
    "garlic bread", "breadsticks", "bread",
}

# ── Pattern detection ────────────────────────────────

# Build regex: "w/" or "with" followed by a combo food.
# Sort longest-first so "french fries" matches before "fries".
_COMBO_ALTS = "|".join(
    re.escape(f) for f in sorted(COMBO_FOODS, key=len, reverse=True)
)

COMBO_PATTERN_RE = re.compile(
    r"\b(?:w/|with)\s+(" + _COMBO_ALTS + r")\b",
    re.IGNORECASE,
)


def is_combo_food(token: str) -> bool:
    """Check if *token* matches a known combo food item.

    >>> is_combo_food("fries")
    True
    >>> is_combo_food("pepperoni")
    False
    """
    return token.strip().lower() in COMBO_FOODS


def extract_combo_hints(text: str) -> List[str]:
    """Return combo food names found after 'w/' or 'with' in *text*.

    >>> extract_combo_hints("9.95 with FRIES 13.50")
    ['fries']
    >>> extract_combo_hints("plain pizza 12.99")
    []
    """
    return [m.strip().lower() for m in COMBO_PATTERN_RE.findall(text)]
