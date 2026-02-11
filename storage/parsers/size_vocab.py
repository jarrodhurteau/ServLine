# storage/parsers/size_vocab.py
"""
Shared Size Vocabulary — Sprint 8.2 Day 56

Single source of truth for size/portion word detection and normalization.
Used by both menu_grammar.py (parsing) and variant_engine.py (enrichment).
"""

from __future__ import annotations

from typing import Dict, Optional, Set
import re


# Canonical mapping: lowercase token -> normalized display label
# Merges grammar parser's _SIZE_WORDS with variant_engine's _SIZE_WORD_MAP.
SIZE_WORD_MAP: Dict[str, str] = {
    # XS
    "xs": "XS",
    "x-small": "XS",
    "extra small": "XS",
    # S
    "small": "S",
    "sm": "S",
    "sml": "S",
    "s": "S",
    # M
    "medium": "M",
    "med": "M",
    "md": "M",
    "m": "M",
    # L
    "large": "L",
    "lg": "L",
    "lrg": "L",
    "l": "L",
    # XL
    "x-large": "XL",
    "xlarge": "XL",
    "xl": "XL",
    "extra large": "XL",
    # XXL
    "xxl": "XXL",
    # Portion
    "half": "Half",
    "whole": "Whole",
    "slice": "Slice",
    "personal": "Personal",
    "family": "Family",
    "party": "Party",
    "party size": "Party",
    "family size": "Family",
    "individual": "Personal",
    # Count
    "single": "Single",
    "double": "Double",
    "triple": "Triple",
    # Section-level size variants (burger/sandwich menus)
    "regular": "Regular",
    "deluxe": "Deluxe",
    "mini": "Mini",
}

# Flat set of all recognized size words (for regex building)
SIZE_WORDS: Set[str] = set(SIZE_WORD_MAP.keys())

# Pre-built regex matching any size word (longest-first for greedy correctness)
SIZE_WORD_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(w) for w in sorted(SIZE_WORDS, key=len, reverse=True)
    ) + r")\b",
    re.IGNORECASE,
)

# Numeric sizes: 10", 14 inch, 16in, 6pc, 12 pieces
NUMERIC_SIZE_RE = re.compile(
    r'\b(\d{1,2})\s*(?:["\u201d\u00b0]|in(?:ch(?:es)?)?|pc|pcs|piece|pieces|ct)\b',
    re.IGNORECASE,
)


def normalize_size_token(raw: str) -> str:
    """Normalize a raw size token to its canonical display label.

    Examples:
        "small" -> "S"
        "sml"   -> "S"
        '10"'   -> '10"'
        "family" -> "Family"
        "6pc"   -> "6pc"
    """
    low = raw.strip().lower()

    # Check word map first
    if low in SIZE_WORD_MAP:
        return SIZE_WORD_MAP[low]

    # Numeric inch patterns (10", 12°, 16\u201d)
    m = re.match(r'(\d{1,2})\s*["\u201d\u00b0]', raw.strip())
    if m:
        return f'{m.group(1)}"'

    # Piece count patterns (6pc, 12pcs, 24ct)
    m = re.match(r'(\d{1,2})\s*(?:pc|pcs|piece|pieces|ct)', raw.strip(), re.IGNORECASE)
    if m:
        return f'{m.group(1)}pc'

    # Passthrough
    return raw.strip()


# ---------------------------------------------------------------------------
# Size Ordering — Sprint 8.2 Day 57
#
# Canonical ordinal positions for all normalized_size values.
# Used by variant_engine.validate_variant_prices() to check monotonic pricing.
#
# Three independent ordinal tracks (non-overlapping ranges):
#   - Word sizes:     10-55   (XS < Mini < S < ... < XXL)
#   - Portions:       110-150 (Slice < Half < Whole < Family < Party)
#   - Multiplicities: 210-230 (Single < Double < Triple)
#   - Numeric inches use their natural value (6-30)
#   - Piece counts use 300+count (306, 312, 324, 350)
# ---------------------------------------------------------------------------

_WORD_SIZE_ORDER: Dict[str, int] = {
    "XS": 10, "Mini": 15, "S": 20, "Personal": 25, "Regular": 30,
    "M": 35, "L": 40, "Deluxe": 45, "XL": 50, "XXL": 55,
}

_PORTION_ORDER: Dict[str, int] = {
    "Slice": 110, "Half": 120, "Whole": 130, "Family": 140, "Party": 150,
}

_MULTIPLICITY_ORDER: Dict[str, int] = {
    "Single": 210, "Double": 220, "Triple": 230,
}


def size_ordinal(normalized_size: str) -> Optional[int]:
    """Return an ordinal position for a normalized_size value.

    Numeric inches (e.g. '10in') and piece counts (e.g. '6pc') use their
    natural numeric value (offset into dedicated ranges).
    Word sizes, portions, and multiplicities use lookup tables.

    Returns None if the size is not recognized.
    """
    if not normalized_size:
        return None

    # Numeric inches: "10in" -> 10
    m = re.match(r"^(\d+)in$", normalized_size)
    if m:
        return int(m.group(1))

    # Piece counts: "6pc" -> 306
    m = re.match(r"^(\d+)pc$", normalized_size)
    if m:
        return 300 + int(m.group(1))

    # Word-based lookups
    if normalized_size in _WORD_SIZE_ORDER:
        return _WORD_SIZE_ORDER[normalized_size]
    if normalized_size in _PORTION_ORDER:
        return _PORTION_ORDER[normalized_size]
    if normalized_size in _MULTIPLICITY_ORDER:
        return _MULTIPLICITY_ORDER[normalized_size]

    return None


def size_track(normalized_size: str) -> Optional[str]:
    """Determine which ordering track a normalized_size belongs to.

    Returns 'inch', 'piece', 'word', 'portion', 'multiplicity', or None.
    Only variants on the same track are compared for price ordering.
    """
    if not normalized_size:
        return None
    if re.match(r"^\d+in$", normalized_size):
        return "inch"
    if re.match(r"^\d+pc$", normalized_size):
        return "piece"
    if normalized_size in _WORD_SIZE_ORDER:
        return "word"
    if normalized_size in _PORTION_ORDER:
        return "portion"
    if normalized_size in _MULTIPLICITY_ORDER:
        return "multiplicity"
    return None
