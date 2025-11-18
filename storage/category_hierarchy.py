# storage/category_hierarchy.py
"""
Lightweight Category Hierarchy Inference — Phase 4 pt.4

Given a flat list of AI-cleaned items, infer a simple hierarchy with
optional subcategory labels. The goal is NOT to be perfect; it’s to give
downstream UIs a hint for grouping (e.g., Calzones vs Strombolis vs Subs)
without re-parsing text everywhere.

Public API
----------
    infer_category_hierarchy(items) -> Dict[str, Dict[str, str]]

Returns a mapping keyed by item name (best-effort; not guaranteed unique):

    {
        "Meat Lovers": {
            "category": "Pizza",
            "subcategory": "Specialty Pizzas"
        },
        ...
    }

`ai_ocr_helper.analyze_ocr_text` uses only the "subcategory" hint, and
adds it directly to each item as `item["subcategory"]` when present.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import re


def _norm(s: Optional[str]) -> str:
    return (s or "").strip()


def _lower(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _has_word(text_low: str, *words: str) -> bool:
    return any(w in text_low for w in words)


def _infer_pizza_subcat(name: str, desc: str) -> Optional[str]:
    low = _lower(name + " " + desc)

    # Explicit pizza “families”
    if _has_word(low, "calzone", "calzones"):
        return "Calzones"
    if _has_word(low, "stromboli", "strombolis"):
        return "Strombolis"
    if _has_word(low, "sicilian"):
        return "Sicilian Pizza"

    # “Specialty” / named pies
    if _has_word(
        low,
        "meat lovers",
        "hawaiian",
        "margherita",
        "margarita",
        "veggie",
        "veggie deluxe",
        "supreme",
        "buffalo chicken",
        "bbq chicken",
        "white pizza",
    ):
        return "Specialty Pizzas"

    # By-the-slice / slice combos
    if _has_word(low, "slice", "slices", "by the slice"):
        return "Pizza By The Slice"

    return None


def _infer_wings_subcat(name: str, desc: str) -> Optional[str]:
    low = _lower(name + " " + desc)
    if _has_word(low, "boneless"):
        return "Boneless Wings"
    if _has_word(low, "bone in", "bone-in"):
        return "Bone-In Wings"
    if _has_word(low, "tenders", "tender", "nuggets"):
        return "Tenders & Nuggets"
    return None


def _infer_burger_sandwich_subcat(name: str, desc: str) -> Optional[str]:
    low = _lower(name + " " + desc)
    if _has_word(low, "burger", "cheeseburger"):
        return "Burgers"
    if _has_word(low, "wrap"):
        return "Wraps"
    if _has_word(low, "sub", "grinder", "hoagie", "philly"):
        return "Subs & Grinders"
    if _has_word(low, "panini"):
        return "Panini"
    if _has_word(low, "gyro", "pita"):
        return "Gyros & Pitas"
    return None


def _infer_salads_subcat(name: str, desc: str) -> Optional[str]:
    low = _lower(name + " " + desc)
    if _has_word(low, "garden"):
        return "Garden Salads"
    if _has_word(low, "greek"):
        return "Greek Salads"
    if _has_word(low, "caesar"):
        return "Caesar Salads"
    if _has_word(low, "chef"):
        return "Chef Salads"
    if _has_word(low, "antipasto"):
        return "Antipasto Salads"
    return None


def _infer_sides_apps_subcat(name: str, desc: str) -> Optional[str]:
    low = _lower(name + " " + desc)
    if _has_word(low, "fries"):
        return "Fries"
    if _has_word(low, "onion rings", "rings"):
        return "Onion Rings"
    if _has_word(low, "mozzarella sticks", "mozzarella stick", "cheese stick"):
        return "Mozzarella Sticks"
    if _has_word(low, "garlic bread", "cheesy bread", "breadsticks"):
        return "Garlic Bread & Breadsticks"
    if _has_word(low, "jalapeno popper", "jalapeño popper", "poppers"):
        return "Poppers"
    if _has_word(low, "meatball", "meatballs"):
        return "Meatballs"
    return None


def _infer_beverages_subcat(name: str, desc: str) -> Optional[str]:
    low = _lower(name + " " + desc)
    if _has_word(low, "bottle", "bottled"):
        return "Bottled Drinks"
    if _has_word(low, "can", "cans"):
        return "Canned Drinks"
    if _has_word(low, "2 liter", "2lt", "2ltr", "2-litre", "2-litter"):
        return "2-Liter Soda"
    if _has_word(low, "fountain", "refill"):
        return "Fountain Drinks"
    if _has_word(low, "coffee", "espresso", "latte", "cappuccino"):
        return "Coffee & Espresso"
    if _has_word(low, "tea", "iced tea", "sweet tea"):
        return "Tea & Lemonade"
    return None


def _infer_subcategory_for_item(item: Dict[str, Any]) -> Optional[str]:
    """
    Best-effort subcategory classifier for a single item.
    Uses item["category"], name, description.
    """
    cat = _lower(item.get("category"))
    name = _norm(item.get("name"))
    desc = _norm(item.get("description"))

    if not name:
        return None

    if "pizza" in cat:
        return _infer_pizza_subcat(name, desc)
    if "wing" in cat:
        return _infer_wings_subcat(name, desc)
    if "burger" in cat or "sandwich" in cat:
        return _infer_burger_sandwich_subcat(name, desc)
    if "salad" in cat:
        return _infer_salads_subcat(name, desc)
    if "side" in cat or "apps" in cat or "appetizer" in cat:
        return _infer_sides_apps_subcat(name, desc)
    if "beverage" in cat or "drink" in cat:
        return _infer_beverages_subcat(name, desc)

    # Fallback: simple calzone/stromboli/etc hints even if category is fuzzy
    low = _lower(name + " " + desc)
    if _has_word(low, "calzone", "calzones"):
        return "Calzones"
    if _has_word(low, "stromboli", "strombolis"):
        return "Strombolis"

    return None


def infer_category_hierarchy(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """
    Inspect a list of AI-cleaned items and infer a lightweight hierarchy.

    Returns:
        mapping[name] -> {"category": <top_level>, "subcategory": <label>}
    Only items with a non-empty inferred subcategory are included.
    """
    hierarchy: Dict[str, Dict[str, str]] = {}

    for it in items:
        name = _norm(it.get("name"))
        if not name:
            continue

        sub = _infer_subcategory_for_item(it)
        if not sub:
            continue

        cat = _norm(it.get("category") or "Uncategorized")
        hierarchy[name] = {
            "category": cat,
            "subcategory": sub,
        }

    return hierarchy
