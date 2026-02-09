# storage/parsers/menu_grammar.py
"""
Menu Item Grammar Parser — Phase 8 Sprint 8.1 (Days 51-52)

Parses OCR text lines into structured menu item components:
  - item_name: the core menu item name
  - description: toppings, ingredients, or detail text
  - modifiers: qualifier phrases ("extra cheese", "no onions", "add bacon")
  - size_mentions: detected size/portion words in the line
  - price_mentions: detected price values in the line
  - line_type: "menu_item" | "heading" | "size_header" | "topping_list" |
               "info_line" | "price_only" | "modifier_line" |
               "description_only" | "unknown"
  - confidence: 0.0–1.0 parse confidence

Design principles:
  - Pizza-first grammar, expandable to other categories
  - Pure regex + heuristic, no ML dependencies
  - Non-destructive: returns parsed structure, does not mutate input
  - Composable with existing ai_cleanup / variant_engine / category_infer

Day 52 additions:
  - OCR dot-leader garble stripping (Step 0.5)
  - Comma-decimal price support (34,75 → 34.75)
  - Size grid header detection (Step 1.5)
  - Topping list / info line detection (Step 1.6)
  - Orphaned price-only line detection (Step 1.7)
  - ALL CAPS name + mixed-case description split (Step 5a)
  - Multi-price text stripping enhancement (Step 2)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import re


# ── Result type ──────────────────────────────────────

@dataclass
class ParsedMenuItem:
    """Structured parse result for a single menu text line or block."""
    item_name: str = ""
    description: str = ""
    modifiers: List[str] = field(default_factory=list)
    size_mentions: List[str] = field(default_factory=list)
    price_mentions: List[float] = field(default_factory=list)
    line_type: str = "unknown"  # menu_item | heading | modifier_line | description_only | unknown
    confidence: float = 0.0
    raw_text: str = ""


# ── Price regex ──────────────────────────────────────

_PRICE_RE = re.compile(
    r"""
    \$?\s*                    # optional dollar sign
    (\d{1,3}[.,]\d{2})        # digits.cents  (e.g. 12.99 or 12,99)
    """,
    re.VERBOSE,
)

# Trailing price: price at end of line, possibly with whitespace/dots
_TRAILING_PRICE_RE = re.compile(
    r"""
    [\s.·…]*                  # dot leaders / whitespace before price
    \$?\s*(\d{1,3}[.,]\d{2})  # the price
    \s*$                      # end of line
    """,
    re.VERBOSE,
)


def _parse_price(s: str) -> float:
    """Parse a price string, normalizing comma decimals to dot."""
    return float(s.replace(",", "."))


# ── OCR dot-leader garble detection ─────────────────
# Tesseract reads dot leaders as garbled lowercase runs like
# "coseeee", "ssssvvssseecsscssssssssescstvsesneneeosees".

_GARBLE_SPAN_RE = re.compile(r'[a-zA-Z]{5,}')
_GARBLE_CHARS = set('secrnotvw')
_TRIPLE_REPEAT_RE = re.compile(r'(.)\1{2,}', re.IGNORECASE)


def _is_garble_run(span: str) -> bool:
    """Return True if the span is OCR dot-leader noise, not a real word."""
    alpha = [c for c in span if c.isalpha()]
    if len(alpha) < 5:
        return False

    has_triple = bool(_TRIPLE_REPEAT_RE.search(span))
    garble_ratio = sum(1 for c in alpha if c.lower() in _GARBLE_CHARS) / len(alpha)
    unique_ratio = len(set(c.lower() for c in alpha)) / len(alpha)
    is_long_run = len(span) >= 12

    # Need at least 2 signals to classify as garble
    signals = sum([
        has_triple,
        garble_ratio >= 0.55,
        unique_ratio <= 0.45,
        is_long_run,
    ])
    return signals >= 2


def _strip_ocr_garble(text: str) -> str:
    """Remove OCR dot-leader garble from text, preserving real words and prices."""
    # Strip long dot runs (but preserve single dots in prices)
    cleaned = re.sub(r'\.{2,}', ' ', text)
    # Remove garble spans
    parts: list[str] = []
    last_end = 0
    for m in _GARBLE_SPAN_RE.finditer(cleaned):
        if _is_garble_run(m.group(0)):
            parts.append(cleaned[last_end:m.start()])
            parts.append(' ')
            last_end = m.end()
    parts.append(cleaned[last_end:])
    cleaned = ''.join(parts)
    # Collapse whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


# ── Size / portion patterns ──────────────────────────

_SIZE_WORDS = {
    "small", "sm", "sml",
    "medium", "med", "md",
    "large", "lg", "lrg",
    "x-large", "xlarge", "xl", "extra large",
    "personal", "family", "party",
    "half", "whole", "slice",
    "single", "double", "triple",
}

_SIZE_WORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(_SIZE_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Numeric sizes: 10", 14 inch, 16in, 6pc, 12 pieces
_NUMERIC_SIZE_RE = re.compile(
    r'\b(\d{1,2})\s*(?:["\u201d]|in(?:ch(?:es)?)?|pc|pcs|piece|pieces|ct)\b',
    re.IGNORECASE,
)


# ── Modifier patterns ────────────────────────────────
# Phrases like "extra cheese", "no onions", "add bacon", "with peppers"

_MODIFIER_RE = re.compile(
    r"\b(extra|add|no|without|hold the|sub|substitute|make it|gluten[- ]?free|vegetarian|vegan)\b"
    r"\s+"
    r"([\w\s]{2,30}?)(?=,|\band\b|\bor\b|$)",
    re.IGNORECASE,
)

# Standalone modifier flags (not followed by a noun)
_MODIFIER_FLAG_RE = re.compile(
    r"\b(gluten[- ]?free|vegetarian|vegan|dairy[- ]?free|keto|spicy|mild|hot)\b",
    re.IGNORECASE,
)


# ── Separator patterns ───────────────────────────────
# Used to detect the boundary between item name and description/toppings

_SEPARATOR_RE = re.compile(
    r"""
    \s+[-–—]\s+       |   # dash separator: "Meat Lovers - pepperoni, sausage"
    \s*:\s+            |   # colon: "Hawaiian: ham, pineapple"
    \s*[•·]\s*             # bullet: "Supreme • pepperoni, sausage, peppers"
    """,
    re.VERBOSE,
)


# ── Heading detection ────────────────────────────────

def _is_heading(text: str) -> bool:
    """
    Detect if a line is a menu section heading rather than an item.

    Headings are typically:
    - Short (1-4 words)
    - No price
    - Often ALL CAPS or title case
    - Common heading words
    """
    stripped = text.strip()
    if not stripped:
        return False

    words = stripped.split()
    word_count = len(words)

    # Too long for a heading
    if word_count > 5:
        return False

    # Has a price → not a heading
    if _PRICE_RE.search(stripped):
        return False

    # ALL CAPS with 1-4 words is a strong heading signal
    alpha_chars = [c for c in stripped if c.isalpha()]
    if alpha_chars and all(c.isupper() for c in alpha_chars) and word_count <= 4:
        return True

    # Known heading phrases
    lower = stripped.lower()
    _HEADING_PHRASES = {
        "pizza", "pizzas", "specialty pizzas", "gourmet pizzas", "gourmet pizza",
        "appetizers", "starters", "sides",
        "salads", "soups", "soup & salad",
        "sandwiches", "subs", "hoagies", "wraps",
        "burgers", "hamburgers",
        "wings", "chicken wings", "buffalo wings", "fresh buffalo wings",
        "pasta", "pastas", "italian classics",
        "entrees", "dinner", "lunch",
        "desserts", "sweets",
        "beverages", "drinks", "cold drinks", "hot drinks",
        "calzones", "stromboli", "calzones & stromboli",
        "seafood", "fish",
        "kids menu", "children's menu",
        "specials", "daily specials",
        "toppings", "extras", "add ons", "add-ons",
        "club sandwiches", "melt sandwiches",
        "build your own burger!",
    }
    if lower in _HEADING_PHRASES:
        return True

    return False


# ── Size grid header detection ──────────────────────

_SIZE_HEADER_TOKEN_RE = re.compile(
    r"""
    \d{1,2}\s*["\u201d°]\s*\w*   |   # 10"Mini, 12"Sml, 16"lrg
    \b(?:mini|small|sml|sm|medium|med|large|lrg|lg|family|party|personal)\b  |
    \b\d+\s*(?:slices?|pieces?|pcs?|cuts?)\b
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _is_size_header(text: str) -> bool:
    """Detect size grid header lines (e.g., '10" Mini  12" Sml  16" Lrg  Family Size')."""
    stripped = text.strip()
    if not stripped:
        return False
    matches = _SIZE_HEADER_TOKEN_RE.findall(stripped)
    if len(matches) < 2:
        return False
    if _PRICE_RE.search(stripped):
        return False
    if len(stripped.split()) > 12:
        return False
    return True


# ── Topping list / info line detection ──────────────

_TOPPING_LIST_RE = re.compile(
    r'^\s*(?:MEAT|VEGGIE|VEGETABLE|PIZZA|CALZONE)\s+TOPPINGS?\s*:',
    re.IGNORECASE,
)
_INFO_LINE_RE = re.compile(
    r'^\s*(?:Choice of|All\s+(?:\w+\s+){1,4}(?:come|stuffed|served|include)\b|Served with|Add\s+\$)',
    re.IGNORECASE,
)


def _is_topping_or_info_line(text: str) -> tuple[bool, str]:
    """Detect topping list and informational context lines.
    Returns (is_match, subtype) where subtype is 'topping_list' or 'info_line'."""
    stripped = text.strip()
    if _TOPPING_LIST_RE.match(stripped):
        return True, "topping_list"
    if _INFO_LINE_RE.match(stripped):
        return True, "info_line"
    lower = stripped.lower()
    if 'toppings' in lower and len(stripped.split()) <= 5 and not _PRICE_RE.search(stripped):
        return True, "topping_list"
    return False, ""


# ── Price-only / orphaned price line detection ──────

_PRICE_ONLY_RE = re.compile(
    r'^[\s.\-\u2013\u2014\xbb\xb7,;:$»]*\$?\s*(\d{1,3}[.,]\d{2})\s*$'
)


def _is_price_only_line(text: str) -> float | None:
    """Detect orphaned price lines. Returns the price value if matched, else None."""
    stripped = text.strip()
    if not stripped:
        return None
    m = _PRICE_ONLY_RE.match(stripped)
    if m:
        try:
            return _parse_price(m.group(1))
        except ValueError:
            return None
    return None


# ── ALL CAPS name + mixed-case description split ────

def _split_caps_name_from_desc(text: str) -> tuple[str, str] | None:
    """
    Detect ALL-CAPS-name + mixed-case-description pattern.

    'MEAT LOVERS Pepperoni, Sausage, Bacon' → ('MEAT LOVERS', 'Pepperoni, Sausage, Bacon')
    Returns (name, description) or None.
    """
    words = text.split()
    if len(words) < 2:
        return None

    # Find boundary: last consecutive ALL CAPS word
    caps_end = 0
    for i, word in enumerate(words):
        clean = re.sub(r'[^A-Za-z]', '', word)
        if not clean:
            # Non-alpha token (& or number) — extend the CAPS run if we're in one
            if caps_end > 0:
                caps_end = i + 1
            continue
        if clean.isupper() and len(clean) >= 2:
            caps_end = i + 1
        else:
            break

    if caps_end < 1 or caps_end >= len(words):
        return None

    name_part = ' '.join(words[:caps_end])
    desc_part = ' '.join(words[caps_end:])

    # Description must have meaningful alpha content
    desc_alpha = sum(1 for c in desc_part if c.isalpha())
    if desc_alpha < 3:
        return None

    # When only 1 CAPS word, be conservative: abbreviations like BBQ, BLT
    # are often part of the item name ("BBQ Chicken Pizza"), not standalone.
    # Only split if desc starts lowercase or has early commas (topping list).
    if caps_end == 1:
        first_alpha = next((c for c in desc_part if c.isalpha()), '')
        has_early_comma = ',' in desc_part[:40]
        if first_alpha.isupper() and not has_early_comma:
            return None

    return name_part, desc_part


# ── Topping / ingredient detection ───────────────────

# Common pizza/Italian toppings for recognizing description content
_COMMON_TOPPINGS = {
    "pepperoni", "sausage", "mushroom", "mushrooms", "onion", "onions",
    "pepper", "peppers", "green pepper", "green peppers",
    "olive", "olives", "black olive", "black olives",
    "bacon", "ham", "salami", "meatball", "meatballs",
    "pineapple", "jalapeno", "jalapenos", "banana pepper", "banana peppers",
    "tomato", "tomatoes", "spinach", "broccoli", "artichoke",
    "garlic", "basil", "oregano",
    "mozzarella", "ricotta", "provolone", "parmesan", "cheddar", "feta",
    "chicken", "steak", "philly steak", "grilled chicken",
    "anchovies", "shrimp", "clam", "clams",
    "roasted red pepper", "sun dried tomato", "fresh mozzarella",
    "buffalo chicken", "bbq chicken",
}


def _has_topping_content(text: str) -> bool:
    """Check if text contains recognizable topping/ingredient words."""
    lower = text.lower()
    matches = sum(1 for t in _COMMON_TOPPINGS if t in lower)
    return matches >= 2


# ── Core parser ──────────────────────────────────────

def parse_menu_line(text: str) -> ParsedMenuItem:
    """
    Parse a single menu text line into structured components.

    This is the primary entrypoint for the grammar parser.
    Works on raw OCR text (pre- or post-cleanup).
    """
    result = ParsedMenuItem(raw_text=text)

    if not text or not text.strip():
        result.line_type = "unknown"
        return result

    working = text.strip()

    # ── Step 0.5: Strip OCR dot-leader garble ──
    cleaned = _strip_ocr_garble(working)
    if cleaned != working:
        working = cleaned

    # ── Step 0.7: Topping list / info line detection ──
    # (before heading detection so "PIZZA & CALZONE TOPPINGS" → topping_list)
    is_info, info_type = _is_topping_or_info_line(working)
    if is_info:
        result.item_name = working
        result.line_type = info_type
        result.confidence = 0.75
        return result

    # ── Step 1: Heading detection ──
    if _is_heading(working):
        result.item_name = working
        result.line_type = "heading"
        result.confidence = 0.85
        return result

    # ── Step 1.5: Size header detection ──
    if _is_size_header(working):
        result.item_name = working
        result.line_type = "size_header"
        result.confidence = 0.80
        for m in _SIZE_WORD_RE.finditer(working):
            result.size_mentions.append(m.group(1))
        for m in _NUMERIC_SIZE_RE.finditer(working):
            result.size_mentions.append(m.group(0))
        return result

    # ── Step 1.7: Orphaned price-only line detection ──
    orphan_price = _is_price_only_line(working)
    if orphan_price is not None:
        result.price_mentions = [orphan_price]
        result.line_type = "price_only"
        result.confidence = 0.70
        return result

    # ── Step 2: Extract prices ──
    prices = []
    for m in _PRICE_RE.finditer(working):
        try:
            prices.append(_parse_price(m.group(1)))
        except ValueError:
            pass
    result.price_mentions = prices

    # Strip prices from text to get name/description content
    if len(prices) > 1:
        # Multiple prices: strip ALL price tokens
        text_no_price = _PRICE_RE.sub('', working)
        text_no_price = re.sub(r'[\s$,.\-]+$', '', text_no_price)
        text_no_price = re.sub(r'\s{2,}', ' ', text_no_price).strip()
    else:
        # Single price: just strip trailing price
        text_no_price = _TRAILING_PRICE_RE.sub("", working).strip()

    if not text_no_price:
        text_no_price = working

    # ── Step 3: Extract size mentions ──
    sizes = []
    for m in _SIZE_WORD_RE.finditer(text_no_price):
        sizes.append(m.group(1))
    for m in _NUMERIC_SIZE_RE.finditer(text_no_price):
        num = m.group(1)
        suffix = m.group(0)[len(num):].strip().lower()
        if "pc" in suffix or "piece" in suffix or "ct" in suffix:
            sizes.append(f"{num}pc")
        else:
            sizes.append(f'{num}"')
    result.size_mentions = sizes

    # ── Step 4: Extract modifiers ──
    modifiers = []
    for m in _MODIFIER_RE.finditer(text_no_price):
        mod_phrase = f"{m.group(1)} {m.group(2)}".strip()
        mod_phrase = re.sub(r"\s+", " ", mod_phrase)
        modifiers.append(mod_phrase)
    for m in _MODIFIER_FLAG_RE.finditer(text_no_price):
        flag = m.group(1)
        if not any(flag.lower() in mod.lower() for mod in modifiers):
            modifiers.append(flag)
    result.modifiers = modifiers

    # ── Step 5: Split name from description ──
    # Try explicit separators first
    sep_match = _SEPARATOR_RE.search(text_no_price)
    if sep_match:
        name_part = text_no_price[:sep_match.start()].strip()
        desc_part = text_no_price[sep_match.end():].strip()

        if len(name_part.split()) >= 1 and desc_part:
            result.item_name = name_part
            result.description = desc_part
            result.line_type = "menu_item"
            result.confidence = 0.80
            return result

    # ── Step 5a: ALL CAPS name + mixed-case description split ──
    # If _split_caps_name_from_desc matches, there IS mixed-case content
    # following the CAPS name, so this is a name+description, not a heading.
    caps_split = _split_caps_name_from_desc(text_no_price)
    if caps_split:
        name_part, desc_part = caps_split
        result.item_name = name_part
        result.description = desc_part
        result.line_type = "menu_item"
        result.confidence = 0.78
        return result

    # No explicit separator — check if the line is a description-only fragment
    if not prices and _has_topping_content(text_no_price) and len(text_no_price.split()) <= 8:
        result.description = text_no_price
        result.line_type = "description_only"
        result.confidence = 0.60
        return result

    # Check for modifier-only lines ("Add toppings $1.50 each")
    if modifiers and not any(
        t.lower() not in _SIZE_WORDS
        for t in text_no_price.split()
        if len(t) > 2 and t.lower() not in {"add", "extra", "no", "with", "sub"}
        and not _PRICE_RE.match(t)
        and not _MODIFIER_FLAG_RE.match(t)
    ):
        result.line_type = "modifier_line"
        result.item_name = text_no_price
        result.confidence = 0.55
        return result

    # Default: treat as menu item with the whole text as the name
    name_text = text_no_price

    # Remove leading size word if present (e.g., "Large Cheese Pizza")
    lead_size = _SIZE_WORD_RE.match(name_text)
    if lead_size:
        name_text = name_text[lead_size.end():].strip()

    result.item_name = name_text if name_text else text_no_price
    result.line_type = "menu_item"
    # Multi-price is strong evidence of a real menu item
    if len(prices) >= 3:
        result.confidence = 0.80
    elif prices:
        result.confidence = 0.65
    else:
        result.confidence = 0.45
    return result


def parse_menu_block(text: str) -> ParsedMenuItem:
    """
    Parse a multi-line text block (merged OCR text) into a single ParsedMenuItem.

    Handles patterns like:
        "Meat Lovers Pizza
         pepperoni, sausage, ham, bacon
         Small 10.99  Large 14.99"

    Strategy: parse each line, then merge results intelligently.
    """
    if not text or not text.strip():
        return ParsedMenuItem(raw_text=text or "")

    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]

    if len(lines) == 1:
        return parse_menu_line(lines[0])

    # Parse each line independently
    parsed_lines = [parse_menu_line(line) for line in lines]

    # Find the "name" line (first non-description, non-modifier line)
    result = ParsedMenuItem(raw_text=text)
    name_found = False
    desc_parts: List[str] = []
    all_prices: List[float] = []
    all_sizes: List[str] = []
    all_modifiers: List[str] = []

    for pl in parsed_lines:
        all_prices.extend(pl.price_mentions)
        all_sizes.extend(pl.size_mentions)
        all_modifiers.extend(pl.modifiers)

        # Skip metadata lines in block context
        if pl.line_type in ("size_header", "topping_list", "info_line"):
            continue
        # Merge orphaned prices (already added to all_prices above)
        if pl.line_type == "price_only":
            continue

        if pl.line_type == "heading" and not name_found:
            result.item_name = pl.item_name
            result.line_type = "heading"
            name_found = True
        elif pl.line_type == "menu_item" and not name_found:
            result.item_name = pl.item_name
            if pl.description:
                desc_parts.append(pl.description)
            name_found = True
        elif pl.line_type == "description_only":
            desc_parts.append(pl.description)
        elif pl.line_type == "menu_item" and name_found:
            # Second item-like line in block → treat as description
            full = pl.item_name
            if pl.description:
                full = f"{full}, {pl.description}"
            desc_parts.append(full)
        elif pl.line_type == "modifier_line":
            desc_parts.append(pl.item_name)

    result.description = ", ".join(desc_parts) if desc_parts else ""
    result.price_mentions = all_prices
    result.size_mentions = list(dict.fromkeys(all_sizes))  # dedupe, preserve order
    result.modifiers = list(dict.fromkeys(all_modifiers))

    if result.line_type != "heading":
        result.line_type = "menu_item" if result.item_name else "unknown"

    # Confidence based on how much structure we found
    signals = sum([
        bool(result.item_name),
        bool(result.price_mentions),
        bool(result.description),
        bool(result.size_mentions),
    ])
    result.confidence = min(0.95, 0.40 + signals * 0.15)

    return result


# ── Batch helper ─────────────────────────────────────

def parse_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Parse a list of draft-style item dicts and attach grammar metadata.

    Each item gets a new "grammar" key with the ParsedMenuItem fields.
    Does NOT mutate the originals — returns new dicts.

    Expects items with at least a "name" key.
    """
    results: List[Dict[str, Any]] = []

    for item in items:
        name = item.get("name") or ""
        desc = item.get("description") or ""

        # Combine name + description for full parse
        combined = name
        if desc:
            combined = f"{name}\n{desc}"

        parsed = parse_menu_block(combined)

        new_item = dict(item)
        new_item["grammar"] = {
            "parsed_name": parsed.item_name,
            "parsed_description": parsed.description,
            "modifiers": parsed.modifiers,
            "size_mentions": parsed.size_mentions,
            "price_mentions": parsed.price_mentions,
            "line_type": parsed.line_type,
            "parse_confidence": parsed.confidence,
        }
        results.append(new_item)

    return results
