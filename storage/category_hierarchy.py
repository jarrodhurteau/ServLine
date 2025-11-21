# storage/category_hierarchy.py
"""
Category Hierarchy v2 — Phase 4 pt.7–10

Given a flat list of AI-cleaned items, infer a more stable category
hierarchy with optional subcategory labels and lightweight grouping.

Goals:
- Normalize noisy OCR category labels into a smaller set of canonical
  categories (e.g., "Pizzas", "Our Pizza", "NY Style Pizza" → "Pizza").
- Infer useful subcategories ("Specialty Pizza", "Calzones", "Grinders").
- Attach stable category/subcategory *paths* per item for downstream use:
    - item["category_path"]      → ["Pizza"]
    - item["subcategory_path"]   → ["Specialty Pizzas"]
    - item["category_slug"]      → "pizza"
    - item["subcategory_slug"]   → "specialty-pizzas"
- Provide a grouped structure that downstream code can use for:
    category → subcategory → [items]
- Keep backward-compatible helper for ai_ocr_helper, which currently only
  cares about subcategory hints per item.

Public API
----------
    build_grouped_hierarchy(items, blocks=None) -> Dict[str, Any]

Returns a dict shaped like:

    {
        "groups": {
            "Pizza": {
                "Specialty Pizzas": [item, ...],
                None: [item, ...],
            },
            "Wings": {
                None: [item, ...],
            },
            ...
        },
        "category_order": ["Pizza", "Wings", ...],
        "subcategory_order": {
            "Pizza": ["Specialty Pizzas", None],
            "Wings": [None],
        },
    }

This is the v2 structure that ocr_pipeline / Finalize JSON will use
before the structured output layer.

Legacy helper (kept for compatibility)
--------------------------------------
    infer_category_hierarchy(items) -> Dict[str, Dict[str, str]]

Returns a mapping keyed by item name (best-effort; not guaranteed unique):

    {
        "Meat Lovers": {
            "category": "Pizza",
            "subcategory": "Specialty Pizzas"
        },
        ...
    }

`ai_ocr_helper.analyze_ocr_text` can continue to use this to look up
per-item subcategory hints and apply them directly to items.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import re


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _norm(s: Optional[str]) -> str:
    return (s or "").strip()


def _lower(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _has_word(text_low: str, *words: str) -> bool:
    return any(w in text_low for w in words)


def _slugify(text: str) -> str:
    """
    Simple slug for potential future anchors (POS exports, URLs).
    """
    text = _lower(text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


# Canonical category aliases. Keys are canonical labels that we want to
# stabilize on; values are lists of substrings that indicate that label.
_CANONICAL_CATEGORY_ALIASES: Dict[str, List[str]] = {
    "Pizza": [
        "pizza", "pizzas", "ny style pizza", "new york style pizza",
        "gourmet pizza", "specialty pizza", "sicilian pizza",
    ],
    "Calzones & Strombolis": [
        "calzone", "calzones", "stromboli", "strombolis",
    ],
    "Wings": [
        "wings", "chicken wings", "boneless wings", "bone-in wings",
    ],
    "Burgers & Sandwiches": [
        "burger", "burgers", "sandwich", "sandwiches", "club sandwich",
        "panini", "wraps", "wrap", "subs", "sub", "grinder", "grinders",
        "hoagie", "hoagies", "philly",
    ],
    "Salads": [
        "salad", "salads",
    ],
    "Pastas": [
        "pasta", "pastas", "pasta dinners", "italian dinners", "dinners",
    ],
    "Appetizers & Sides": [
        "appetizer", "appetizers", "starters", "sides", "side orders",
        "finger foods",
    ],
    "Beverages": [
        "beverage", "beverages", "drinks", "drink", "soda", "soft drinks",
    ],
    "Desserts": [
        "dessert", "desserts", "sweet treats",
    ],
}


def _collapse_category_name(raw_cat: Optional[str]) -> str:
    """
    Collapse various noisy/duplicated headings into a canonical
    top-level category. If nothing matches, return a cleaned version
    or 'Uncategorized'.
    """
    raw = _norm(raw_cat)
    low = _lower(raw)

    if not low:
        return "Uncategorized"

    for canonical, hints in _CANONICAL_CATEGORY_ALIASES.items():
        for h in hints:
            if h in low:
                return canonical

    # Fallback: title-case the raw string but avoid shouty ALL CAPS
    if len(raw) > 2:
        return raw.title()

    return "Uncategorized"


# ---------------------------------------------------------------------------
# Subcategory inference (semantic) — largely reused from v1
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Geometry / heading scaffold (for future extension)
# ---------------------------------------------------------------------------


def _apply_geometric_headings(
    items: List[Dict[str, Any]],
    blocks: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Placeholder for Phase 4 geometric rules.

    In a later pass, this will:
    - Use OCR block coordinates and heading roles (e.g. from debug/blocks)
      to assign stronger category/subcategory hints.
    - Track section breaks via vertical gaps.
    - Promote obvious big-font headings to category or subcategory labels.

    For now, this is a no-op and serves as a clear hook for future logic.
    """
    if not blocks:
        return
    return


# ---------------------------------------------------------------------------
# v2: grouped hierarchy builder + per-item normalization
# ---------------------------------------------------------------------------


def _classify_item_category_and_subcat(
    item: Dict[str, Any]
) -> Tuple[str, Optional[str], List[str]]:
    """
    Decide on a canonical category + best subcategory for an item.

    Returns:
        (canonical_category, subcategory, flags)

    Flags can include:
      - "missing_category"
      - "category_collapsed"
      - "subcategory_inferred"
    """
    flags: List[str] = []

    raw_cat = item.get("category") or ""
    canonical = _collapse_category_name(raw_cat)
    if not raw_cat:
        flags.append("missing_category")
    elif canonical != _norm(raw_cat):
        flags.append("category_collapsed")

    # Respect existing subcategory if present; otherwise infer
    sub = _norm(item.get("subcategory"))
    if not sub:
        inferred = _infer_subcategory_for_item(item)
        if inferred:
            sub = inferred
            flags.append("subcategory_inferred")

    return canonical, (sub or None), flags


def build_grouped_hierarchy(
    items: List[Dict[str, Any]],
    blocks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Primary v2 entrypoint.

    Given a list of AI-cleaned items (optionally plus OCR blocks), produce
    a grouped structure:

        category → subcategory → [items]

    Side effects (Phase 4 pt.10):
    - Normalizes each item's category/subcategory in-place:
        * item["category"]           → canonical category
        * item["subcategory"]        → best-effort inferred subcategory (optional)
        * item["category_path"]      → [] or [canonical]
        * item["subcategory_path"]   → [] or [subcategory]
        * item["category_slug"]      → slug of canonical
        * item["subcategory_slug"]   → slug of subcategory (if any)
    - Attaches `hierarchy_flags` to items that needed normalization.
    - Leaves item order intact within each group (input order is preserved).
    """
    # Hook for future geometric refinement (no-op for now)
    _apply_geometric_headings(items, blocks=blocks)

    groups: Dict[str, Dict[Optional[str], List[Dict[str, Any]]]] = {}
    category_order: List[str] = []
    subcategory_order: Dict[str, List[Optional[str]]] = {}

    for it in items:
        name = _norm(it.get("name"))
        if not name:
            continue

        canon_cat, subcat, flags = _classify_item_category_and_subcat(it)

        # ---- Per-item normalization (paths + slugs) ----
        # Overwrite raw category with canonical label so downstream sees a stable family.
        it["category"] = canon_cat
        if subcat:
            it["subcategory"] = subcat

        # Category path: empty for truly Uncategorized, otherwise single-level.
        if canon_cat and canon_cat != "Uncategorized":
            it["category_path"] = [canon_cat]
            it["category_slug"] = _slugify(canon_cat)
        else:
            it["category_path"] = []
            # leave slug absent for Uncategorized

        # Subcategory path: optional, sits under the canonical category.
        if subcat:
            it["subcategory_path"] = [subcat]
            it["subcategory_slug"] = _slugify(subcat)
        else:
            it["subcategory_path"] = []

        # Attach any hierarchy flags (dedup with existing list if present).
        if flags:
            existing = it.get("hierarchy_flags") or []
            if not isinstance(existing, list):
                existing = [str(existing)]
            it["hierarchy_flags"] = list({*existing, *flags})

        # ---- Grouping structure ----
        if canon_cat not in groups:
            groups[canon_cat] = {}
            category_order.append(canon_cat)
            subcategory_order[canon_cat] = []

        if subcat not in groups[canon_cat]:
            groups[canon_cat][subcat] = []
            subcategory_order[canon_cat].append(subcat)

        groups[canon_cat][subcat].append(it)

    return {
        "groups": groups,
        "category_order": category_order,
        "subcategory_order": subcategory_order,
    }


# ---------------------------------------------------------------------------
# Legacy helper for ai_ocr_helper (kept for compatibility)
# ---------------------------------------------------------------------------


def infer_category_hierarchy(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """
    Inspect a list of AI-cleaned items and infer a lightweight hierarchy.

    Returns:
        mapping[name] -> {"category": <top_level>, "subcategory": <label>}

    Only items with a non-empty inferred subcategory are included.

    This now delegates to the v2 grouping logic to ensure that category
    collapsing rules stay in sync, and also benefits from per-item
    normalization (category/subcategory paths & slugs).
    """
    hierarchy: Dict[str, Dict[str, str]] = {}

    grouped = build_grouped_hierarchy(items)
    groups: Dict[str, Dict[Optional[str], List[Dict[str, Any]]]] = grouped["groups"]

    for cat, submap in groups.items():
        for subcat, items_list in submap.items():
            if not subcat:
                continue  # legacy helper only reports explicit subcategories
            for it in items_list:
                name = _norm(it.get("name"))
                if not name:
                    continue
                hierarchy[name] = {
                    "category": cat,
                    "subcategory": subcat,
                }

    return hierarchy
