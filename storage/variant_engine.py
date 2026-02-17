# storage/variant_engine.py
"""
Variant & Size Intelligence — Phase 4 pt.3–4 (Day 27)

Phase 4 pt.3:
    Takes the coarse OCRVariant list per text block (built in ocr_pipeline.py)
    and enriches each variant with:

    - kind: "size" | "flavor" | "style" | "combo" | "other"
    - normalized_size: canonical string for sizes/counts ("10in", "14in",
      "6pc", "24pc", "S", "M", "L", ... )
    - group_key: stable key so downstream export can easily cluster variants
      into families per item without re-parsing labels.

    This module is deliberately stateless and only mutates the TextBlock dicts
    in place. It works entirely on the already-merged text + variants, so it is
    safe to run after Phase 4 pt.1–2 and Phase 3 pt.6.

Phase 4 pt.4:
    Provides light helpers for Phase-A AI cleanup outputs so we can normalize
    simple (label, price) variant dictionaries into enriched OCRVariant
    structures that share the same enrichment logic.

    These helpers are used by storage/ai_ocr_helper.py for its own
    variant + hierarchy pass.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union
import re

from .ocr_types import OCRVariant
from .parsers.size_vocab import (
    SIZE_WORD_MAP as _SIZE_WORD_MAP,
    normalize_size_token,
    size_ordinal,
    size_track,
    WORD_CHAIN,
    PORTION_CHAIN,
    MULTIPLICITY_CHAIN,
)
from .parsers.combo_vocab import is_combo_food


PriceLike = Union[int, float]


# -----------------------------
# Heuristic tables
# -----------------------------

# _SIZE_WORD_MAP imported from .parsers.size_vocab (Sprint 8.2 Day 56)

# Wing counts / piece counts, etc.
_PIECE_SUFFIXES = ("pc", "pcs", "piece", "pieces", "ct")

# Flavor words – these usually indicate sauce / taste rather than geometry.
_FLAVOR_TOKENS = {
    "hot", "mild", "medium", "honey", "bbq", "barbecue", "honey bbq",
    "garlic", "parm", "parmesan", "garlic parm", "teriyaki",
    "buffalo", "spicy", "sweet", "sour", "honey mustard",
    "lemon", "pepper", "lemon pepper",
    # Phase 8: expanded wing/sauce flavors
    "mango habanero", "carolina gold", "thai chili", "sweet chili",
    "old bay", "cajun", "ranch", "blue cheese",
    "asian zing", "korean bbq", "sriracha",
    "plain", "naked", "original",
}

# Style / preparation – crust types, bone-in vs boneless, etc.
_STYLE_TOKENS = {
    "bone-in", "bone in", "boneless",
    "thin", "thin crust", "thick", "thick crust", "deep dish", "stuffed crust",
    "white", "red", "red sauce", "alfredo", "pesto",
    # Phase 8: expanded pizza crust vocabulary
    "pan", "pan crust",
    "hand tossed", "hand-tossed",
    "brooklyn", "brooklyn style",
    "sicilian", "sicilian style",
    "neapolitan", "neapolitan style",
    "detroit", "detroit style",
    "new york", "ny style",
    "flatbread",
    "gluten free", "gluten-free", "cauliflower crust",
    "crispy", "extra crispy",
    # Phase 8: wing preparation styles
    "fried", "grilled", "baked",
    "breaded", "naked",
    "dry rub", "tossed",
}


_INCH_RE = re.compile(r'(\d{1,2})\s*(?:["]|in(?:ch(?:es)?)?)\b', re.IGNORECASE)
_PIECE_RE = re.compile(r'(\d{1,2})\s*(?:pc|pcs|piece|pieces|ct)\b', re.IGNORECASE)


# -----------------------------
# Core inference helpers
# -----------------------------

def _normalize_size_from_label(label: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Try to infer a normalized size string (and kind="size") from a variant label.

    Returns (kind, normalized_size) where kind is "size" or None.
    """
    if not label:
        return None, None

    raw = label.strip()
    low = raw.lower()

    # 1) Numeric inch patterns: 10", 10 in, 10 inch, etc.
    m = _INCH_RE.search(low)
    if m:
        inches = m.group(1)
        return "size", f"{int(inches)}in"

    # 2) Piece / count patterns: 6pc, 12 pcs, 24ct
    m = _PIECE_RE.search(low)
    if m:
        count = m.group(1)
        return "size", f"{int(count)}pc"

    # 3) S/M/L style words
    tokens = re.split(r"\s+", low)
    for t in tokens:
        t_clean = t.strip(".,;:-")
        if not t_clean:
            continue
        mapped = _SIZE_WORD_MAP.get(t_clean)
        if mapped:
            return "size", mapped

    # 4) Bare numbers, e.g. "10", "14", "18" in a pizza context.
    # We treat 6–30 as inches; anything smaller/larger is probably something else.
    nums = [int(n) for n in re.findall(r"\b(\d{1,2})\b", low)]
    for n in nums:
        if 6 <= n <= 30:
            return "size", f"{n}in"

    return None, None


def _infer_flavor_or_style(label: str) -> Optional[str]:
    """
    Decide whether this label looks more like a flavor or a style indicator.
    Returns "flavor", "style", or None.
    """
    low = label.lower()
    # Style wins if we see crust/bone hints
    for token in _STYLE_TOKENS:
        if token in low:
            return "style"
    for token in _FLAVOR_TOKENS:
        if token in low:
            return "flavor"
    return None


def _infer_variant_kind_and_normalized_size(label: str) -> Tuple[str, Optional[str]]:
    """
    High-level classifier for a variant label.

    Returns (kind, normalized_size):
      - kind: "size" | "flavor" | "style" | "combo" | "other"
      - normalized_size: canonical size/count string, only for kind == "size"
    """
    kind, norm_size = _normalize_size_from_label(label)
    if kind == "size":
        return "size", norm_size

    # Day 58: Check for combo pattern — "W/Fries", "with Cheese", etc.
    stripped = label.strip()
    combo_match = re.match(r'^(?:w/\s*|with\s+)(.+)$', stripped, re.IGNORECASE)
    if combo_match:
        food = combo_match.group(1).strip()
        if is_combo_food(food):
            return "combo", None

    # Standalone combo food used as a variant label (e.g., "Fries")
    if is_combo_food(stripped):
        return "combo", None

    # Not clearly a size or combo; maybe flavor or style
    fs_kind = _infer_flavor_or_style(label)
    if fs_kind is not None:
        return fs_kind, None

    return "other", None


def _build_group_key(kind: str, label: str, normalized_size: Optional[str]) -> Optional[str]:
    """
    Build a stable group_key so downstream export can cluster variants.

    Examples:
      size + "10in"  -> "size:10in"
      size + "6pc"   -> "size:6pc"
      flavor + "Hot" -> "flavor:hot"
      style + "Thin Crust" -> "style:thin crust"
    """
    if kind == "size" and normalized_size:
        return f"size:{normalized_size}"
    if kind in ("flavor", "style", "combo"):
        return f"{kind}:{label.strip().lower()}"
    return None


def _enrich_variant(v: OCRVariant) -> None:
    """
    Mutate a single OCRVariant in-place with kind/normalized_size/group_key.

    Day 58: respects ``kind_hint`` set during variant building (e.g., "combo").
    """
    label = (v.get("label") or "").strip()
    if not label:
        v["kind"] = v.get("kind_hint", "other")
        return

    kind, norm_size = _infer_variant_kind_and_normalized_size(label)

    # Day 58: honour kind_hint from variant building when inference is ambiguous
    hint = v.get("kind_hint")
    if hint == "combo" and kind == "other":
        kind = "combo"

    v["kind"] = kind
    if norm_size is not None:
        v["normalized_size"] = norm_size
    group_key = _build_group_key(kind, label, norm_size)
    if group_key is not None:
        v["group_key"] = group_key


# -----------------------------
# Phase 4 pt.4 helpers for Phase-A outputs
# -----------------------------

def _to_cents(value: PriceLike | None) -> Optional[int]:
    """
    Best-effort conversion of a dollar-ish value into integer cents.

    Phase-A AI cleanup usually yields floats like 9.99 or 19.95; we round
    to the nearest cent. If the input already looks like cents (int),
    we still treat it as dollars by design, because all menu prices
    have been floats historically.
    """
    if value is None:
        return None
    try:
        cents = int(round(float(value) * 100))
        if cents < 0:
            return None
        return cents
    except Exception:
        return None


def classify_raw_variant(
    label: str,
    price: PriceLike | None = None,
    confidence: float = 0.9,
) -> OCRVariant:
    """
    Helper used by ai_ocr_helper:

    Given a loose (label, price) pair where price is in dollars (float/int),
    build a canonical OCRVariant dict:

        {
          "label": "L",
          "price_cents": 1299,
          "confidence": 0.9,
          "kind": "size",
          "normalized_size": "L",
          "group_key": "size:L",
        }
    """
    v: OCRVariant = {
        "label": (label or "").strip(),
        "confidence": float(confidence),
    }
    cents = _to_cents(price)
    if cents is not None:
        v["price_cents"] = cents

    _enrich_variant(v)
    return v


def normalize_variant_group(raw_variants: List[Dict[str, Any]]) -> List[OCRVariant]:
    """
    Normalize a list of loose variant dicts into enriched OCRVariant objects.

    Accepts rows like:
        {"label": "S", "price": 9.99}
        {"label": "L", "price": 12.99}
        {"label": "10\"", "price_cents": 1299}

    Returns a list of OCRVariant with:
        - price_cents
        - confidence (default 0.9 unless provided)
        - kind / normalized_size / group_key from _enrich_variant
    """
    out: List[OCRVariant] = []
    if not raw_variants:
        return out

    for rv in raw_variants:
        label = (rv.get("label") or "").strip()
        if not label:
            continue

        # Prefer explicit "price" (dollars); else try "price_cents".
        price_val: PriceLike | None = None
        if "price" in rv and rv["price"] is not None:
            price_val = rv["price"]  # assumed dollars
        elif "price_cents" in rv and rv["price_cents"] is not None:
            try:
                cents_int = int(rv["price_cents"])
                price_val = cents_int / 100.0
            except Exception:
                price_val = None

        conf = float(rv.get("confidence", 0.9) or 0.9)
        v = classify_raw_variant(label, price_val, confidence=conf)
        out.append(v)

    return out


# -----------------------------
# Size Grid Context — Sprint 8.2 Day 56
# -----------------------------

@dataclass
class SizeGridColumn:
    """One column in a size grid header."""
    raw_label: str       # e.g. '10"Mini', 'Family Size', 'Regular'
    normalized: str      # e.g. '10" Mini', 'Family', 'Regular'
    position: int        # 0-based column index


@dataclass
class SizeGridContext:
    """Tracks the active size grid from a size_header line."""
    columns: List[SizeGridColumn]
    source_line_index: int

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def label_for_position(self, pos: int) -> Optional[str]:
        """Get the normalized label for a 0-based column position."""
        if 0 <= pos < len(self.columns):
            return self.columns[pos].normalized
        return None


# Regex for scanning size header tokens left-to-right (mirrors menu_grammar's
# _SIZE_HEADER_TOKEN_RE but we define our own to avoid import cycle concerns).
_GRID_TOKEN_RE = re.compile(
    r"""
    (\d{1,2})\s*(["\u201d\u00b0])([a-zA-Z]*)   |   # numeric inch + optional word: 10"Mini
    \b(mini|small|sml|sm|medium|med|large|lrg|lg|family|party|personal|regular|deluxe)\b  |
    \b(\d+)\s*(slices?|pieces?|pcs?|cuts?)\b
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _parse_size_header_columns(text: str) -> List[SizeGridColumn]:
    """Parse a size header string into ordered columns left-to-right.

    Two-pass approach:
      1. Collect all regex matches with positions and type tags.
      2. Merge adjacent numeric + word matches into single columns.
         (Handles both '10"Mini' where the word is glued, and '12" Sml'
         where there's a space between.)

    Examples:
        '10"Mini 12" Sml 16"lrg Family Size'
            -> [10" Mini, 12" S, 16" L, Family]
        'Regular Deluxe'
            -> [Regular, Deluxe]
        '12" Sml   16"lrg  Family Size'
            -> [12" S, 16" L, Family]
    """
    # Pass 1: collect raw matches in order
    # Each entry: (kind, raw_text, normalized, match_start, match_end)
    raw_matches: List[Tuple[str, str, str, int, int]] = []

    for m in _GRID_TOKEN_RE.finditer(text):
        if m.group(1):
            num = m.group(1)
            trailing_word = (m.group(3) or "").strip()
            if trailing_word:
                norm_word = normalize_size_token(trailing_word)
                raw_matches.append(
                    ("numeric_word", m.group(0).strip(), f'{num}" {norm_word}',
                     m.start(), m.end()))
            else:
                raw_matches.append(
                    ("numeric", f'{num}{m.group(2)}', f'{num}"',
                     m.start(), m.end()))
        elif m.group(4):
            word = m.group(4)
            norm = normalize_size_token(word)
            raw_matches.append(("word", word, norm, m.start(), m.end()))
        elif m.group(5):
            num = m.group(5)
            suffix = m.group(6).lower()
            if "slice" in suffix or "cut" in suffix:
                continue  # slice counts are info, not columns
            raw_matches.append(
                ("piece", m.group(0).strip(), f"{num}pc", m.start(), m.end()))

    # Words that pair with inch sizes as qualifiers (mini, sml, lrg, etc.)
    # Standalone size names (family, regular, deluxe) stay as their own columns.
    _INCH_QUALIFIERS = {"mini", "sm", "sml", "small", "med", "medium",
                        "lg", "lrg", "large"}

    # Pass 2: merge adjacent numeric + qualifier-word into single columns
    columns: List[SizeGridColumn] = []
    used: set = set()

    for i, (kind, raw, norm, start, end) in enumerate(raw_matches):
        if i in used:
            continue

        if kind == "numeric" and i + 1 < len(raw_matches):
            next_kind, next_raw, next_norm, next_start, next_end = raw_matches[i + 1]
            # Only merge if next is a qualifier word and there's only whitespace between
            gap = text[end:next_start]
            is_qualifier = next_raw.lower() in _INCH_QUALIFIERS
            if (next_kind == "word" and (i + 1) not in used
                    and gap.strip() == "" and is_qualifier):
                merged_norm = f'{norm} {next_norm}'
                merged_raw = f'{raw} {next_raw}'
                columns.append(SizeGridColumn(
                    raw_label=merged_raw,
                    normalized=merged_norm,
                    position=len(columns),
                ))
                used.add(i)
                used.add(i + 1)
                continue

        if kind == "numeric_word":
            # Already coalesced by regex (e.g. 10"Mini)
            columns.append(SizeGridColumn(
                raw_label=raw, normalized=norm, position=len(columns)))
            used.add(i)
            continue

        # Standalone: numeric-only, word-only, or piece
        columns.append(SizeGridColumn(
            raw_label=raw, normalized=norm, position=len(columns)))
        used.add(i)

    return columns


def _extract_size_grid(grammar: Dict[str, Any], raw_text: str,
                       block_index: int) -> Optional[SizeGridContext]:
    """Build a SizeGridContext from a size_header text_block.

    Uses the raw text (not grammar["size_mentions"]) to preserve positional
    order of columns left-to-right.
    """
    if grammar.get("line_type") != "size_header":
        return None

    columns = _parse_size_header_columns(raw_text)
    if len(columns) < 2:
        return None

    return SizeGridContext(columns=columns, source_line_index=block_index)


# Known section headings that expire the active grid (imported from grammar
# vocabulary to stay in sync).
from .parsers.menu_grammar import _KNOWN_SECTION_HEADINGS  # noqa: E402


def _is_section_heading_name(name: str) -> bool:
    """Check if a heading name is a known section heading (grid-expiring)."""
    lower = name.strip().lower()
    clean = re.sub(r'[_!.]+$', '', lower).strip()
    return lower in _KNOWN_SECTION_HEADINGS or clean in _KNOWN_SECTION_HEADINGS


def _build_variants_from_grid(
    grid: SizeGridContext,
    grammar_prices: List[float],
    price_candidates: List[Dict[str, Any]],
    existing_variants: List[OCRVariant],
) -> List[OCRVariant]:
    """Map prices to size grid columns, producing properly labeled variants.

    Priority:
    1. Use grammar_prices (float dollars) if available and count matches
    2. Fall back to price_candidates
    3. Fall back to existing_variants (from backward-token-walk)
    """
    prices_cents: List[int] = []
    confidences: List[float] = []

    if grammar_prices:
        for p in grammar_prices:
            cents = int(round(p * 100))
            if cents > 0:
                prices_cents.append(cents)
                confidences.append(0.85)
    elif price_candidates:
        for pc in price_candidates:
            cents = pc.get("price_cents")
            if cents and cents > 0:
                prices_cents.append(cents)
                confidences.append(float(pc.get("confidence", 0.8)))
    elif existing_variants:
        for v in existing_variants:
            cents = v.get("price_cents", 0)
            if cents and cents > 0:
                prices_cents.append(cents)
                confidences.append(float(v.get("confidence", 0.8)))

    if len(prices_cents) < 2:
        return []

    variants: List[OCRVariant] = []

    if len(prices_cents) == grid.column_count:
        # Perfect 1:1 mapping
        for col_idx, (cents, conf) in enumerate(zip(prices_cents, confidences)):
            label = grid.label_for_position(col_idx) or f"Size {col_idx + 1}"
            variants.append({
                "label": label,
                "price_cents": cents,
                "confidence": min(conf, 0.85),
            })
    elif len(prices_cents) < grid.column_count:
        # Fewer prices than columns: right-align
        # (gourmet items often skip the smallest size)
        offset = grid.column_count - len(prices_cents)
        for price_idx, (cents, conf) in enumerate(zip(prices_cents, confidences)):
            col_idx = price_idx + offset
            label = grid.label_for_position(col_idx) or f"Size {col_idx + 1}"
            variants.append({
                "label": label,
                "price_cents": cents,
                "confidence": min(conf, 0.75),  # lower for imperfect mapping
            })
    else:
        # More prices than columns — don't apply grid
        return []

    return variants


def apply_size_grid_context(text_blocks: List[Dict[str, Any]]) -> None:
    """Bridge grammar parse results to variant creation using size grid context.

    Scans text_blocks for size_header grammar types, tracks the active grid,
    and for subsequent multi-price items, creates or improves variants by
    mapping prices to the size grid columns positionally.

    Pipeline placement: after annotate_prices_and_variants (Step 7),
    before enrich_variants_on_text_blocks (Step 8).

    Mutates text_blocks in place.
    """
    active_grid: Optional[SizeGridContext] = None

    for i, tb in enumerate(text_blocks):
        grammar = tb.get("grammar")
        if not grammar:
            continue

        line_type = grammar.get("line_type", "")
        raw_text = (tb.get("merged_text") or tb.get("text") or "").strip()

        # --- Grid lifecycle ---

        # New size_header replaces current grid
        if line_type == "size_header":
            new_grid = _extract_size_grid(grammar, raw_text, i)
            if new_grid:
                active_grid = new_grid
            continue

        # Known section heading expires the grid
        if line_type == "heading":
            parsed_name = grammar.get("parsed_name", "")
            if parsed_name and _is_section_heading_name(parsed_name):
                active_grid = None
            continue

        # Non-item types: skip but don't expire grid
        if line_type in ("info_line", "topping_list", "description_only",
                         "price_only", "noise", "garble"):
            continue

        # --- Apply grid to menu items ---

        if active_grid is None:
            continue

        if line_type != "menu_item":
            continue

        # Collect price sources
        grammar_prices = grammar.get("price_mentions", [])
        existing_variants = tb.get("variants", [])
        price_candidates = tb.get("price_candidates", [])

        # Only apply grid when there are multiple prices
        price_count = (len(grammar_prices) if grammar_prices
                       else len(price_candidates) if price_candidates
                       else len(existing_variants))
        if price_count < 2:
            continue

        grid_variants = _build_variants_from_grid(
            active_grid, grammar_prices, price_candidates, existing_variants
        )

        if grid_variants:
            tb["variants"] = grid_variants
            tb.setdefault("meta", {})["size_grid_applied"] = True
            tb["meta"]["size_grid_source"] = active_grid.source_line_index
            tb["meta"]["size_grid_column_count"] = active_grid.column_count


# -----------------------------
# Text-block level enrichment (Phase 4 pt.3)
# -----------------------------

def enrich_variants_on_text_blocks(text_blocks: List[Dict[str, Any]]) -> None:
    """
    Entry point: walk all text_blocks, look for an existing "variants" list,
    and enrich each variant dict in-place.

    We do **not** change the shape of "variants" so downstream code that
    already consumes [OCRVariant] stays happy. We simply add more fields.

    Side effect:
      - Blocks that have at least one size-like variant will also receive
        a convenience flag "has_size_variants" in tb["meta"].
    """
    for tb in text_blocks:
        variants: List[OCRVariant] = tb.get("variants") or []  # type: ignore[assignment]
        if not variants:
            continue

        has_size = False
        for v in variants:
            _enrich_variant(v)
            if v.get("kind") == "size":
                has_size = True

        if has_size:
            meta = tb.setdefault("meta", {})
            meta["has_size_variants"] = True


# ---------------------------------------------------------------------------
# Variant price validation — Sprint 8.2 Day 57
# ---------------------------------------------------------------------------

def validate_variant_prices(text_blocks: List[Dict[str, Any]]) -> None:
    """Validate that size variant prices are monotonically non-decreasing.

    For each text_block with 2+ size-typed variants on the same size track,
    sorts by canonical size ordinal and checks that prices do not decrease
    as size increases.  Equal prices are allowed (some menus charge the
    same for adjacent sizes).

    Attaches ``price_flags`` to text_blocks where inversions are found.

    Pipeline placement: after enrich_variants_on_text_blocks (Step 8),
    as Step 8.5.  Mutates text_blocks in place.
    """
    for tb in text_blocks:
        variants: List[OCRVariant] = tb.get("variants") or []  # type: ignore[assignment]
        if len(variants) < 2:
            continue

        # Collect size variants with valid ordinals
        sized: List[Tuple[str, int, int, str]] = []  # (norm, ordinal, cents, track)
        for v in variants:
            if v.get("kind") != "size":
                continue
            ns = v.get("normalized_size")
            pc = v.get("price_cents", 0)
            if not ns or not pc or pc <= 0:
                continue
            ordi = size_ordinal(ns)
            trk = size_track(ns)
            if ordi is None or trk is None:
                continue
            sized.append((ns, ordi, pc, trk))

        if len(sized) < 2:
            continue

        # Group by track — only validate within same track
        tracks: Dict[str, List[Tuple[str, int, int, str]]] = {}
        for entry in sized:
            tracks.setdefault(entry[3], []).append(entry)

        for track_name, track_entries in tracks.items():
            if len(track_entries) < 2:
                continue

            # Sort by ordinal (canonical size order)
            sorted_entries = sorted(track_entries, key=lambda e: e[1])

            # Check monotonic non-decreasing prices
            inversions = []
            for i in range(len(sorted_entries) - 1):
                ns_small, _, price_small, _ = sorted_entries[i]
                ns_large, _, price_large, _ = sorted_entries[i + 1]
                if price_large < price_small:
                    inversions.append({
                        "smaller_size": ns_small,
                        "smaller_price_cents": price_small,
                        "larger_size": ns_large,
                        "larger_price_cents": price_large,
                    })

            if inversions:
                flags = tb.setdefault("price_flags", [])
                flags.append({
                    "severity": "warn",
                    "reason": "variant_price_inversion",
                    "details": {
                        "track": track_name,
                        "inversions": inversions,
                        "expected_order": [e[0] for e in sorted_entries],
                        "actual_prices_cents": [e[2] for e in sorted_entries],
                    },
                })


# -----------------------------
# Cross-Variant Consistency — Sprint 8.2 Day 59
# -----------------------------

# Gap-detection chains.
# Inch and piece tracks are naturally sparse, so no gap detection for them.
# Word track is split into two sub-chains: abbreviated (S/M/L) and named
# (Mini/Personal/Regular/Deluxe) — a menu using S/M/L should not be flagged
# for missing Personal or Regular.
_WORD_ABBREVIATED_CHAIN: List[str] = ["XS", "S", "M", "L", "XL", "XXL"]
_WORD_NAMED_CHAIN: List[str] = ["Mini", "Personal", "Regular", "Deluxe"]
_GAP_CHAINS: Dict[str, List[List[str]]] = {
    "word": [_WORD_ABBREVIATED_CHAIN, _WORD_NAMED_CHAIN],
    "portion": [PORTION_CHAIN],
    "multiplicity": [MULTIPLICITY_CHAIN],
}


def _check_duplicate_variants(
    tb: Dict[str, Any], variants: List[OCRVariant],
) -> None:
    """Flag items with duplicate group_key values."""
    keys: List[Optional[str]] = [v.get("group_key") for v in variants]
    non_none = [k for k in keys if k is not None]
    if len(non_none) < 2:
        return
    counts = Counter(non_none)
    duped = [k for k, c in counts.items() if c > 1]
    if duped:
        tb.setdefault("price_flags", []).append({
            "severity": "warn",
            "reason": "duplicate_variant",
            "details": {
                "duplicated_keys": sorted(duped),
                "variant_count": len(variants),
            },
        })


def _check_zero_price_variants(
    tb: Dict[str, Any], variants: List[OCRVariant],
) -> None:
    """Flag variants with price_cents == 0 when other variants are nonzero."""
    zero_labels: List[str] = []
    nonzero_count = 0
    for v in variants:
        pc = v.get("price_cents", -1)
        if pc == 0:
            zero_labels.append(v.get("label", ""))
        elif pc > 0:
            nonzero_count += 1
    if zero_labels and nonzero_count > 0:
        tb.setdefault("price_flags", []).append({
            "severity": "warn",
            "reason": "zero_price_variant",
            "details": {
                "zero_labels": zero_labels,
                "nonzero_count": nonzero_count,
            },
        })


def _check_mixed_kinds(
    tb: Dict[str, Any], variants: List[OCRVariant],
) -> None:
    """Flag items with unusual mixes of variant kinds."""
    kinds = {v.get("kind") for v in variants if v.get("kind") not in (None, "other")}
    if len(kinds) < 2:
        return
    severity = "warn" if len(kinds) >= 3 else "info"
    tb.setdefault("price_flags", []).append({
        "severity": severity,
        "reason": "mixed_variant_kinds",
        "details": {
            "kinds_found": sorted(kinds),
            "variant_count": len(variants),
        },
    })


def _check_size_gaps(
    tb: Dict[str, Any], variants: List[OCRVariant],
) -> None:
    """Flag missing intermediate sizes in word/portion/multiplicity tracks."""
    # Collect size variants grouped by track
    by_track: Dict[str, List[str]] = {}
    for v in variants:
        if v.get("kind") != "size":
            continue
        ns = v.get("normalized_size")
        trk = size_track(ns) if ns else None
        if trk and trk in _GAP_CHAINS:
            by_track.setdefault(trk, []).append(ns)  # type: ignore[arg-type]

    for track_name, present in by_track.items():
        if len(present) < 2:
            continue
        sub_chains = _GAP_CHAINS[track_name]
        # Pick the sub-chain with the most matches to present sizes
        best_chain: Optional[List[str]] = None
        best_hits = 0
        for sc in sub_chains:
            hits = sum(1 for s in present if s in sc)
            if hits > best_hits:
                best_hits = hits
                best_chain = sc
        if best_chain is None or best_hits < 2:
            continue
        chain = best_chain
        # Find positions in the chain
        positions = []
        for s in present:
            if s in chain:
                positions.append(chain.index(s))
        if len(positions) < 2:
            continue
        lo, hi = min(positions), max(positions)
        # Everything between lo and hi that's absent
        missing = [chain[i] for i in range(lo + 1, hi) if chain[i] not in present]
        if missing:
            tb.setdefault("price_flags", []).append({
                "severity": "info",
                "reason": "size_gap",
                "details": {
                    "track": track_name,
                    "present_sizes": sorted(
                        [s for s in present if s in chain],
                        key=lambda s: chain.index(s),
                    ),
                    "missing_sizes": missing,
                },
            })


def _check_grid_completeness(
    tb: Dict[str, Any], variants: List[OCRVariant],
) -> None:
    """Flag items under a grid that have significantly fewer variants than grid columns."""
    meta = tb.get("meta") or {}
    if not meta.get("size_grid_applied"):
        return
    col_count = meta.get("size_grid_column_count")
    if not col_count or col_count < 2:
        return
    var_count = len(variants)
    missing = col_count - var_count
    # 1 missing is normal (gourmet right-alignment), 2+ is suspicious
    if missing >= 2:
        tb.setdefault("price_flags", []).append({
            "severity": "info",
            "reason": "grid_incomplete",
            "details": {
                "grid_column_count": col_count,
                "variant_count": var_count,
                "missing_count": missing,
                "grid_source_line": meta.get("size_grid_source"),
            },
        })


def _check_grid_count_consistency(text_blocks: List[Dict[str, Any]]) -> None:
    """Flag items whose variant count is an outlier within their grid group."""
    # Group items by grid source
    grid_groups: Dict[int, List[Dict[str, Any]]] = {}
    for tb in text_blocks:
        meta = tb.get("meta") or {}
        if not meta.get("size_grid_applied"):
            continue
        src = meta.get("size_grid_source")
        if src is not None:
            grid_groups.setdefault(src, []).append(tb)

    for src, group in grid_groups.items():
        if len(group) < 2:
            continue
        counts = [len(t.get("variants") or []) for t in group]
        mode_count = Counter(counts).most_common(1)[0][0]
        for tb in group:
            var_count = len(tb.get("variants") or [])
            if mode_count - var_count >= 2:
                tb.setdefault("price_flags", []).append({
                    "severity": "info",
                    "reason": "grid_count_outlier",
                    "details": {
                        "grid_source_line": src,
                        "item_variant_count": var_count,
                        "group_mode_count": mode_count,
                        "group_size": len(group),
                    },
                })


def check_variant_consistency(text_blocks: List[Dict[str, Any]]) -> None:
    """Check cross-variant consistency within and across items.

    Six categories of checks:
      1. Duplicate variant detection (same group_key)
      2. Zero-price variant detection ($0.00 when siblings are nonzero)
      3. Mixed kind detection (unusual kind combinations)
      4. Size gap detection (missing intermediate sizes)
      5. Grid completeness (fewer variants than grid columns)
      6. Grid consistency across items (outlier variant counts)

    Pipeline placement: after validate_variant_prices (Step 8.5),
    as Step 8.6.  Mutates text_blocks in place.
    """
    for tb in text_blocks:
        variants: List[OCRVariant] = tb.get("variants") or []  # type: ignore[assignment]
        if not variants:
            continue
        _check_duplicate_variants(tb, variants)
        _check_zero_price_variants(tb, variants)
        _check_mixed_kinds(tb, variants)
        _check_size_gaps(tb, variants)
        _check_grid_completeness(tb, variants)

    # Cross-item check requires full list
    _check_grid_count_consistency(text_blocks)
