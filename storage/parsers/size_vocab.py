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
    # M
    "medium": "M",
    "med": "M",
    "md": "M",
    # L
    "large": "L",
    "lg": "L",
    "lrg": "L",
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
