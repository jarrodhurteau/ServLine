# storage/parsers/menu_grammar.py
"""
Menu Item Grammar Parser — Phase 8 Sprint 8.1 (Days 51-55)

Parses OCR text lines into structured menu item components:
  - item_name: the core menu item name
  - description: toppings, ingredients, or detail text
  - modifiers: qualifier phrases ("extra cheese", "no onions", "add bacon")
  - size_mentions: detected size/portion words in the line
  - price_mentions: detected price values in the line
  - line_type: "menu_item" | "heading" | "size_header" | "topping_list" |
               "info_line" | "price_only" | "modifier_line" |
               "description_only" | "multi_column" | "unknown"
  - confidence: 0.0–1.0 parse confidence
  - components: structured decomposition of description (toppings, sauce, etc.)
  - column_segments: split text segments when multi-column merge detected

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

Day 53 additions:
  - Broader ingredient vocabulary for description detection
  - Lowercase-start description continuation heuristic
  - Expanded info line patterns (flavor lists, option lines, cross-references)
  - Post-garble short noise cleanup
  - W/ and Wi OCR normalization
  - Contextual multi-pass classification (classify_menu_lines)

Day 54 additions:
  - Item component detection (toppings, sauce, preparation, flavors)
  - Multi-column merge detection in classify_menu_lines

Day 55 additions:
  - Pipeline integration: enrich_grammar_on_text_blocks() for OCR pipeline
  - OCR typo normalization (88Q→BBQ, piZzA→PIZZA, etc.)
  - Confidence tiers: high (0.80+), medium (0.60-0.79), low (0.40-0.59)
  - Fallback OCR hardening for degraded Tesseract output
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import re


# ── Result types ─────────────────────────────────────

@dataclass
class ItemComponents:
    """Structured decomposition of a menu item's description."""
    toppings: List[str] = field(default_factory=list)
    sauce: Optional[str] = None
    preparation: Optional[str] = None
    flavor_options: List[str] = field(default_factory=list)


@dataclass
class ParsedMenuItem:
    """Structured parse result for a single menu text line or block."""
    item_name: str = ""
    description: str = ""
    modifiers: List[str] = field(default_factory=list)
    size_mentions: List[str] = field(default_factory=list)
    price_mentions: List[float] = field(default_factory=list)
    line_type: str = "unknown"  # menu_item | heading | modifier_line | description_only | multi_column | unknown
    confidence: float = 0.0
    raw_text: str = ""
    components: Optional[ItemComponents] = None
    column_segments: Optional[List[str]] = None


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


def _strip_short_noise(text: str) -> str:
    """Remove OCR noise fragments that survive garble stripping.

    Catches:
    - Isolated 1-3 char non-word tokens (digits/symbols/single letters)
    - Mid-length (4-11 char) tokens with very high garble char ratios
    - Triple-repeat 3-char fragments like 'eee', 'sss'
    Preserves real tokens: prices, '&', 'w/', numbers, known abbreviations.
    """
    _KEEP_SHORT = {'&', 'w/', 'or', 'of', 'on', 'in', 'to', 'a', 'no', 'pc'}
    parts = text.split()
    cleaned = []
    for tok in parts:
        low = tok.lower().rstrip('.,;:!?)')
        stripped = tok.strip('.,;:!?)')

        # Keep price-like tokens anywhere
        if _PRICE_RE.match(stripped) or tok.startswith('$'):
            cleaned.append(tok)
            continue

        alpha = [c for c in tok if c.isalpha()]
        alpha_count = len(alpha)

        # Short tokens (< 4 chars)
        if len(stripped) < 4:
            # Keep known short words
            if low in _KEEP_SHORT:
                cleaned.append(tok)
                continue
            # Keep single real digits/numbers (but not "00")
            digits_only = stripped.strip('.,').isdigit()
            if digits_only and stripped.strip('.,') not in ('00', '000'):
                cleaned.append(tok)
                continue
            # Drop pure symbol/digit noise
            if alpha_count == 0:
                continue
            # Drop 1-char alpha fragments
            if alpha_count <= 1 and len(stripped) <= 2:
                continue
            # Drop triple-repeat fragments like 'eee'
            if alpha_count == len(stripped) and len(set(c.lower() for c in alpha)) == 1:
                continue
            cleaned.append(tok)
            continue

        # Mid-length tokens (4-11 chars): check for garble residue
        if 4 <= len(stripped) <= 11:
            # Mixed digit/letter noise like "F590", "s0s00", "25150)"
            if alpha_count > 0 and alpha_count < len(stripped) * 0.4:
                continue  # Drop mostly-numeric noise with some letters
            # High garble ratio tokens (nearly all garble chars)
            if alpha_count >= 3:
                garble_ratio = sum(1 for c in alpha if c.lower() in _GARBLE_CHARS) / alpha_count
                if garble_ratio >= 0.85:
                    unique_ratio = len(set(c.lower() for c in alpha)) / alpha_count
                    if unique_ratio < 0.65:
                        continue  # Drop garble residue

        cleaned.append(tok)

    result = ' '.join(cleaned)
    return result


# ── W/ and Wi OCR normalization ─────────────────────

def _normalize_w_slash(text: str) -> str:
    """Normalize OCR variants of 'w/' (with) — 'W/', 'w/', 'Wi ' → 'with'.

    'W/ FRIES' → 'with FRIES', 'Wi CHEESE' → 'with CHEESE'
    """
    # W/ or w/ followed by a word
    text = re.sub(r'\bW/\s*', 'with ', text, flags=re.IGNORECASE)
    # 'Wi ' before a consonant word — common OCR misread of 'W/'
    text = re.sub(r'\bWi\s+(?=[BCDFGHJKLMNPQRSTVWXYZbcdfghjklmnpqrstvwxyz])', 'with ', text)
    return text


# ── OCR typo normalization (Day 55) ─────────────────
# Common Tesseract misreads in restaurant menus

_OCR_TYPO_MAP = {
    "88q": "BBQ",
    "88Q": "BBQ",
    "8BQ": "BBQ",
    "880": "BBQ",
    "B8Q": "BBQ",
    "Basi!": "Basil",
    "basi!": "basil",
}

# Regex-based corrections for patterns that can't be a simple dict lookup
_OCR_TYPO_PATTERNS = [
    # "piZzA" → "PIZZA" (mixed-case single-word garble for known words)
    (re.compile(r'\bpiZzA\b'), 'PIZZA'),
    # "Smt" → "Sml" in size context
    (re.compile(r'\bSmt\b'), 'Sml'),
    # Leading bracket noise: "[a1" or "[a1]" prefix
    (re.compile(r'^\[a?\d*\]?\s*'), ''),
    # "WI/" and "WI/FRIES" → "W/" (Tesseract reads W/ as WI/)
    (re.compile(r'\bWI/'), 'W/'),
]


def _normalize_ocr_typos(text: str) -> str:
    """Fix common OCR misreads found in fallback/degraded Tesseract output."""
    # Dict-based replacements (word boundaries)
    for typo, fix in _OCR_TYPO_MAP.items():
        if typo in text:
            text = text.replace(typo, fix)

    # Regex-based patterns
    for pattern, replacement in _OCR_TYPO_PATTERNS:
        text = pattern.sub(replacement, text)

    return text


# ── Size / portion patterns ──────────────────────────

_SIZE_WORDS = {
    "small", "sm", "sml",
    "medium", "med", "md",
    "large", "lg", "lrg",
    "x-large", "xlarge", "xl", "extra large",
    "personal", "family", "party",
    "half", "whole", "slice",
    "single", "double", "triple",
    "regular", "deluxe",
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

# Known section heading phrases (module-level for reuse in contextual pass)
_KNOWN_SECTION_HEADINGS = {
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
    "wraps city", "build your own burger!",
    "build your own calzone!", "build your own!",
}


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
    # Strip trailing punctuation for matching
    lower_clean = re.sub(r'[_!.]+$', '', lower).strip()
    if lower in _KNOWN_SECTION_HEADINGS or lower_clean in _KNOWN_SECTION_HEADINGS:
        return True

    return False


# ── Size grid header detection ──────────────────────

_SIZE_HEADER_TOKEN_RE = re.compile(
    r"""
    \d{1,2}\s*["\u201d°]\s*\w*   |   # 10"Mini, 12"Sml, 16"lrg
    \b(?:mini|small|sml|sm|medium|med|large|lrg|lg|family|party|personal|regular|deluxe)\b  |
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
    r'^\s*(?:'
    r'Choice of\b'
    r'|All\s+(?:\w+\s+){1,4}(?:come|stuffed|served|include)\b'
    r'|Served with\b'
    r'|Add\s+\$'
    r'|Add\s+\w+\s+\$'           # "Add Bacon $1 extra"
    r'|\w+\s+toppings?\s+same\b'  # "Calzone toppings same as pizza"
    r')',
    re.IGNORECASE,
)

# ALL-CAPS comma-delimited flavor/sauce list (e.g., "HOT, MILD, BBQ, HONEY BBQ")
_FLAVOR_LIST_RE = re.compile(
    r'^[A-Z][A-Z,;\s&]+$'
)

# Short option lines: "X or Y" with no price, 2-5 words
_OPTION_LINE_RE = re.compile(
    r'^\s*\w+(?:\s+\w+)?\s+or\s+\w+(?:\s+\w+)?\s*$',
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
    if 'toppings' in lower and len(stripped.split()) <= 8 and not _PRICE_RE.search(stripped):
        return True, "topping_list"
    # ALL-CAPS comma-separated flavor lists (3+ commas, no price)
    words = stripped.split()
    if (len(words) >= 3 and _FLAVOR_LIST_RE.match(stripped)
            and stripped.count(',') >= 2 and not _PRICE_RE.search(stripped)):
        return True, "info_line"
    # Short option lines: "Naked or Breaded", "White or Wheat"
    if (_OPTION_LINE_RE.match(stripped) and len(words) <= 5
            and not _PRICE_RE.search(stripped)):
        return True, "info_line"
    # Dimension lines: "17x26\"", "17x24°" — sheet/tray size, not menu items
    if re.match(r'^\d{1,3}\s*x\s*\d{1,3}\s*["\u201d°]?\s*$', stripped):
        return True, "info_line"
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

# Common restaurant ingredients for recognizing description/continuation content
_COMMON_TOPPINGS = {
    # Pizza toppings
    "pepperoni", "sausage", "mushroom", "mushrooms", "onion", "onions",
    "pepper", "peppers", "green pepper", "green peppers",
    "olive", "olives", "black olive", "black olives",
    "bacon", "ham", "salami", "meatball", "meatballs",
    "pineapple", "jalapeno", "jalapenos", "banana pepper", "banana peppers",
    "tomato", "tomatoes", "spinach", "broccoli", "artichoke",
    "garlic", "basil", "oregano",
    "anchovies", "shrimp", "clam", "clams",
    "roasted red pepper", "sun dried tomato",
    "eggplant",
    # Cheese
    "mozzarella", "ricotta", "provolone", "parmesan", "cheddar", "feta",
    "american cheese", "swiss", "blue cheese", "fresh mozzarella",
    # Proteins
    "chicken", "steak", "philly steak", "grilled chicken",
    "buffalo chicken", "bbq chicken", "hamburger", "ground beef",
    "turkey", "roast beef", "tuna", "corned beef", "gyro",
    # Condiments / sauces
    "ranch", "mayo", "mayonnaise", "mustard", "ketchup", "hot sauce",
    "bbq sauce", "marinara", "alfredo sauce", "pesto sauce",
    "ranch dressing", "sour cream", "salsa", "tzatziki",
    "russian dressing", "caesar dressing", "thousand island",
    "blue cheese base",
    # Sides / accompaniments
    "lettuce", "pickles", "coleslaw", "french fries", "chips",
    "avocado", "beans", "sauerkraut",
}


def _has_topping_content(text: str) -> bool:
    """Check if text contains recognizable topping/ingredient words."""
    lower = text.lower()
    matches = sum(1 for t in _COMMON_TOPPINGS if t in lower)
    return matches >= 2


# ── Component detection vocabularies ─────────────────

_SAUCE_TOKENS = {
    "marinara", "marinara sauce", "alfredo", "alfredo sauce",
    "pesto", "pesto sauce", "bbq sauce", "hot sauce",
    "ranch", "ranch dressing", "ranch sauce",
    "blue cheese", "blue cheese base", "bleu cheese",
    "garlic sauce", "red sauce", "white sauce", "buffalo sauce",
    "1000 island", "thousand island", "russian dressing",
    "caesar dressing", "tzatziki", "mayo", "mayonnaise",
    "tomato sauce", "olive oil", "1000 island base",
    "salsa", "sour cream",
}

_PREPARATION_TOKENS = {
    "fried", "grilled", "baked", "roasted", "steamed",
    "sauteed", "braised", "breaded", "crispy", "smoked",
    "shaved", "diced", "chopped", "sliced", "stuffed",
    "marinated", "homemade",
}

_COMPONENT_FLAVOR_TOKENS = {
    "hot", "mild", "medium", "honey bbq", "bbq",
    "garlic parm", "garlic parmesan", "garlic romano",
    "teriyaki", "buffalo", "spicy", "sweet",
    "cajun", "lemon pepper", "mango habanero",
    "sweet chili", "sriracha", "jack daniels bbq",
    "plain", "naked", "original", "honey mustard",
}

# Build reverse lookup: token → category
_INGREDIENT_CATEGORY: Dict[str, str] = {}
for _t in _SAUCE_TOKENS:
    _INGREDIENT_CATEGORY[_t] = "sauce"
for _t in _PREPARATION_TOKENS:
    _INGREDIENT_CATEGORY[_t] = "preparation"
for _t in _COMPONENT_FLAVOR_TOKENS:
    _INGREDIENT_CATEGORY[_t] = "flavor"
for _t in _COMMON_TOPPINGS:
    if _t not in _INGREDIENT_CATEGORY:
        _INGREDIENT_CATEGORY[_t] = "topping"


# ── Description tokenizer & component classifier ────

_DESC_SPLIT_RE = re.compile(r',\s*|\s+&\s+|\s+and\s+|;\s*|\s+or\s+', re.IGNORECASE)
_W_PREFIX_RE = re.compile(r'^(?:w/\s*|with\s+)', re.IGNORECASE)
_W_INFIX_RE = re.compile(r'\s+w/\s*', re.IGNORECASE)


def _tokenize_description(description: str) -> List[str]:
    """Split a description string into individual component tokens.

    Splits on: comma, ' & ', ' and ', ' or ', semicolon, ' w/ '.
    Strips whitespace, dots, leading 'w/'/'with' from each token.
    """
    # First split on w/ infix (e.g., "Chicken w/ Alfredo Sauce")
    desc = _W_INFIX_RE.sub(', ', description)
    tokens = _DESC_SPLIT_RE.split(desc)
    result: List[str] = []
    for tok in tokens:
        tok = tok.strip().strip('.')
        tok = _W_PREFIX_RE.sub('', tok).strip()
        if tok:
            result.append(tok)
    return result


def _classify_components(tokens: List[str], item_name: str = "") -> ItemComponents:
    """Classify each token into topping, sauce, preparation, or flavor."""
    comp = ItemComponents()
    sauce_found = False

    # Check if this looks like a flavor-option list:
    # ALL tokens match flavor vocabulary and there are 2+ of them
    flavor_matches = 0
    for tok in tokens:
        low = tok.lower().strip()
        if low in _COMPONENT_FLAVOR_TOKENS:
            flavor_matches += 1
    all_flavors = len(tokens) >= 2 and flavor_matches == len(tokens)

    for tok in tokens:
        low = tok.lower().strip()

        # Try longest-match against known vocabularies
        matched_cat = None
        matched_key = ""
        for known, cat in _INGREDIENT_CATEGORY.items():
            if known in low and len(known) > len(matched_key):
                matched_cat = cat
                matched_key = known

        # Also check if the first word is a preparation method
        first_word = low.split()[0] if low.split() else ""
        has_prep_prefix = first_word in _PREPARATION_TOKENS

        if all_flavors and low in _COMPONENT_FLAVOR_TOKENS:
            comp.flavor_options.append(low)
        elif matched_cat == "sauce" and not sauce_found:
            # Extract the sauce name (use the matched key)
            comp.sauce = matched_key
            if matched_key.endswith(" base"):
                comp.sauce = matched_key[:-5].strip()
            elif matched_key.endswith(" sauce"):
                comp.sauce = matched_key[:-6].strip()
            elif matched_key.endswith(" dressing"):
                comp.sauce = matched_key[:-9].strip()
            sauce_found = True
        elif has_prep_prefix and comp.preparation is None:
            # Token starts with prep word (e.g., "grilled chicken", "fried chicken")
            comp.preparation = first_word
            remainder = low[len(first_word):].strip()
            if remainder:
                comp.toppings.append(remainder)
        elif matched_cat == "preparation" and comp.preparation is None:
            comp.preparation = matched_key
            remainder = low.replace(matched_key, "", 1).strip()
            if remainder:
                comp.toppings.append(remainder)
        elif matched_cat == "flavor" and not all_flavors:
            # Mixed list — individual flavor tokens become flavor_options
            comp.flavor_options.append(low)
        elif matched_cat == "sauce" and sauce_found:
            # Second sauce → treat as topping
            comp.toppings.append(tok.strip())
        else:
            # Default: topping/ingredient
            comp.toppings.append(tok.strip())

    return comp


def _extract_components(description: str, item_name: str = "") -> Optional[ItemComponents]:
    """Tokenize a description and classify into structured components."""
    tokens = _tokenize_description(description)
    if not tokens:
        return None
    return _classify_components(tokens, item_name)


# ── Multi-column merge detection ─────────────────────

_COLUMN_GAP_RE = re.compile(r'\s{5,}')


def detect_column_merge(text: str) -> Optional[List[str]]:
    """Detect if a line contains multi-column merged content.

    Returns a list of text segments if multi-column merge detected, else None.
    Primary signal: 5+ consecutive whitespace characters between text content.
    """
    stripped = text.strip()
    if not stripped:
        return None

    # Find gaps of 5+ spaces
    gaps = list(_COLUMN_GAP_RE.finditer(stripped))
    if not gaps:
        return None

    # Split at gaps
    segments: List[str] = []
    last_end = 0
    for gap in gaps:
        seg = stripped[last_end:gap.start()].strip().strip('.')
        if seg:
            segments.append(seg)
        last_end = gap.end()
    # Final segment after last gap
    tail = stripped[last_end:].strip().strip('.')
    if tail:
        segments.append(tail)

    # Filter out pure noise segments (but don't run garble detection
    # on individual words — real words like CHEESEBURGER can false-positive)
    clean_segments: List[str] = []
    for seg in segments:
        # Only strip dot runs and very short noise, not full garble detection
        cleaned = re.sub(r'\.{2,}', ' ', seg).strip()
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        # Drop segments that are only dots, whitespace, or 1-char noise
        alpha_count = sum(1 for c in cleaned if c.isalpha())
        if cleaned and (alpha_count >= 2 or _PRICE_RE.search(cleaned)):
            clean_segments.append(cleaned)

    if len(clean_segments) >= 2:
        return clean_segments
    return None


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

    # ── Step 0.2: Normalize common OCR typos ──
    working = _normalize_ocr_typos(working)

    # ── Step 0.3: Normalize W/ and Wi OCR artifacts ──
    working = _normalize_w_slash(working)

    # ── Step 0.5: Strip OCR dot-leader garble ──
    cleaned = _strip_ocr_garble(working)
    if cleaned != working:
        working = cleaned

    # ── Step 0.5b: Early info-line detection before noise stripping ──
    # Dimension lines like "17x26" get destroyed by noise stripping, so detect first
    is_early_info, early_info_type = _is_topping_or_info_line(working)
    if is_early_info:
        result.item_name = working
        result.line_type = early_info_type
        result.confidence = 0.75
        return result

    # ── Step 0.6: Strip short noise residue ──
    working = _strip_short_noise(working)

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
            result.components = _extract_components(desc_part, name_part)
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
        result.components = _extract_components(desc_part, name_part)
        return result

    # No explicit separator — check if the line is a description-only fragment
    tnp_words = text_no_price.split()
    if not prices and _has_topping_content(text_no_price) and len(tnp_words) <= 14:
        result.description = text_no_price
        result.line_type = "description_only"
        result.confidence = 0.60
        result.components = _extract_components(text_no_price)
        return result

    # Lowercase-start continuation: lines starting lowercase with commas or "and"
    # and no price are almost always description continuations
    if (not prices and tnp_words
            and tnp_words[0][0:1].islower()
            and (',' in text_no_price or ' and ' in text_no_price.lower())
            and len(tnp_words) <= 14):
        result.description = text_no_price
        result.line_type = "description_only"
        result.confidence = 0.58
        result.components = _extract_components(text_no_price)
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

    # Component detection on merged description
    if result.description:
        result.components = _extract_components(result.description, result.item_name)

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


# ── Contextual multi-pass classification ─────────────

def _is_known_section_heading(name: str) -> bool:
    """Check if a heading name matches a known section heading."""
    lower = name.strip().lower()
    clean = re.sub(r'[_!.]+$', '', lower).strip()
    return lower in _KNOWN_SECTION_HEADINGS or clean in _KNOWN_SECTION_HEADINGS


def classify_menu_lines(lines: List[str]) -> List[ParsedMenuItem]:
    """
    Classify a sequence of menu lines with contextual awareness.

    Multi-pass approach:
      0. Pass 0: detect multi-column merges and flag them
      1. First pass: classify each line independently via parse_menu_line
      2. Second pass: resolve heading-vs-item using neighbor context
      3. Third pass: resolve heading clusters (runs of non-section headings)

    Returns list of ParsedMenuItem (same length as input, including blanks).
    """
    # First pass: independent classification
    results = [parse_menu_line(line) for line in lines]
    n = len(results)

    # Pass 0: detect multi-column merges
    for i in range(n):
        raw = results[i].raw_text
        if not raw or not raw.strip():
            continue
        segments = detect_column_merge(raw)
        if segments and len(segments) >= 2:
            results[i].column_segments = segments
            results[i].line_type = "multi_column"
            results[i].confidence = 0.70

    def _get_neighbor_type(start: int, direction: int) -> Optional[str]:
        """Find the line_type of the nearest non-empty, non-unknown neighbor."""
        for j in range(start, max(-1, start + direction * 3) if direction < 0
                       else min(n, start + direction * 3), direction):
            if 0 <= j < n and results[j].raw_text.strip() and results[j].line_type != "unknown":
                return results[j].line_type
        return None

    # Second pass: neighbor-based heading resolution
    for i in range(n):
        r = results[i]
        if r.line_type != "heading":
            continue
        if _is_known_section_heading(r.item_name):
            continue

        next_type = _get_neighbor_type(i + 1, 1)
        prev_type = _get_neighbor_type(i - 1, -1)

        # If followed by description_only or price_only → item with split content
        if next_type in ("description_only", "price_only"):
            r.line_type = "menu_item"
            r.confidence = 0.60

        # If sandwiched between items/descriptions → likely an item
        if (r.line_type == "heading"
                and prev_type in ("menu_item", "description_only")
                and next_type in ("menu_item", "description_only", "price_only")):
            r.line_type = "menu_item"
            r.confidence = 0.55

    # Third pass: heading cluster resolution
    # If 2+ consecutive non-empty lines are all "heading" and none are known
    # section headings, they're almost certainly menu items (like a list of
    # appetizers or melts without prices).
    # Clusters are broken at blank lines and at known section headings.
    i = 0
    while i < n:
        if results[i].line_type != "heading" or not results[i].raw_text.strip():
            i += 1
            continue
        # Don't start a cluster from a known section heading
        if _is_known_section_heading(results[i].item_name):
            i += 1
            continue

        # Collect the run of consecutive headings
        cluster = [i]
        j = i + 1
        while j < n:
            # Blank lines break the cluster
            if not results[j].raw_text.strip():
                break
            # Known section heading terminates the cluster
            if (results[j].line_type == "heading"
                    and _is_known_section_heading(results[j].item_name)):
                break
            if results[j].line_type == "heading":
                cluster.append(j)
                j += 1
            else:
                break

        # If 2+ headings in a cluster → reclassify as menu items
        if len(cluster) >= 2:
            for k in cluster:
                results[k].line_type = "menu_item"
                results[k].confidence = 0.52

        i = j if j > i + 1 else i + 1

    return results


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
            "components": {
                "toppings": parsed.components.toppings,
                "sauce": parsed.components.sauce,
                "preparation": parsed.components.preparation,
                "flavor_options": parsed.components.flavor_options,
            } if parsed.components else None,
        }
        results.append(new_item)

    return results


# ── Confidence tiers (Day 55) ────────────────────────

def confidence_tier(score: float) -> str:
    """Map a numeric confidence score to a human-readable tier.

    Returns:
      'high'    — 0.80+  (strong structural evidence)
      'medium'  — 0.60–0.79 (reasonable parse, some ambiguity)
      'low'     — 0.40–0.59 (weak evidence, needs review)
      'unknown' — below 0.40
    """
    if score >= 0.80:
        return "high"
    elif score >= 0.60:
        return "medium"
    elif score >= 0.40:
        return "low"
    return "unknown"


# ── Pipeline integration (Day 55) ────────────────────

def _parsed_to_dict(p: ParsedMenuItem) -> Dict[str, Any]:
    """Convert a ParsedMenuItem to a plain dict for pipeline enrichment."""
    return {
        "parsed_name": p.item_name,
        "parsed_description": p.description,
        "modifiers": p.modifiers,
        "size_mentions": p.size_mentions,
        "price_mentions": p.price_mentions,
        "line_type": p.line_type,
        "parse_confidence": p.confidence,
        "confidence_tier": confidence_tier(p.confidence),
        "components": {
            "toppings": p.components.toppings,
            "sauce": p.components.sauce,
            "preparation": p.components.preparation,
            "flavor_options": p.components.flavor_options,
        } if p.components else None,
        "column_segments": p.column_segments,
    }


def enrich_grammar_on_text_blocks(
    text_blocks: List[Dict[str, Any]],
) -> None:
    """
    Enrich a list of OCR pipeline text_blocks with grammar parse metadata.

    Adds a "grammar" key to each text_block containing the full ParsedMenuItem
    result as a plain dict. Uses classify_menu_lines for contextual awareness.

    This function mutates text_blocks in place (same pattern as
    infer_categories_on_text_blocks and annotate_prices_and_variants_on_text_blocks).
    """
    if not text_blocks:
        return

    # Extract merged text from each block
    raw_lines: List[str] = []
    for tb in text_blocks:
        text = (tb.get("merged_text") or tb.get("text") or "").strip()
        raw_lines.append(text)

    # Run contextual multi-pass classification
    parsed = classify_menu_lines(raw_lines)

    # Attach grammar metadata to each text_block
    for tb, p in zip(text_blocks, parsed):
        tb["grammar"] = _parsed_to_dict(p)
