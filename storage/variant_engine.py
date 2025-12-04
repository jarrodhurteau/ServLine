# storage/variant_engine.py
"""
Variant & Size Intelligence — Phase 4 pt.3–4 (Day 27)

Phase 4 pt.3:
    Takes the coarse OCRVariant list per text block (built in ocr_pipeline.py)
    and enriches each variant with:

    - kind: "size" | "flavor" | "style" | "other"
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

from typing import Any, Dict, List, Optional, Tuple, Union
import re

from .ocr_types import OCRVariant


PriceLike = Union[int, float]


# -----------------------------
# Heuristic tables
# -----------------------------

_SIZE_WORD_MAP = {
    "xs": "XS",
    "x-small": "XS",
    "extra small": "XS",
    "small": "S",
    "sm": "S",
    "sml": "S",
    "medium": "M",
    "med": "M",
    "md": "M",
    "large": "L",
    "lg": "L",
    "xlarge": "XL",
    "x-large": "XL",
    "extra large": "XL",
    "xl": "XL",
    "xxl": "XXL",
}

# Wing counts / piece counts, etc.
_PIECE_SUFFIXES = ("pc", "pcs", "piece", "pieces", "ct")

# Flavor words – these usually indicate sauce / taste rather than geometry.
_FLAVOR_TOKENS = {
    "hot", "mild", "medium", "honey", "bbq", "barbecue", "honey bbq",
    "garlic", "parm", "parmesan", "garlic parm", "teriyaki",
    "buffalo", "spicy", "sweet", "sour", "honey mustard",
    "lemon", "pepper", "lemon pepper",
}

# Style / preparation – crust types, bone-in vs boneless, etc.
_STYLE_TOKENS = {
    "bone-in", "bone in", "boneless",
    "thin", "thin crust", "thick", "deep dish", "stuffed crust",
    "white", "red", "red sauce", "alfredo", "pesto",
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
      - kind: "size" | "flavor" | "style" | "other"
      - normalized_size: canonical size/count string, only for kind == "size"
    """
    kind, norm_size = _normalize_size_from_label(label)
    if kind == "size":
        return "size", norm_size

    # Not clearly a size; maybe flavor or style
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
    if kind in ("flavor", "style"):
        return f"{kind}:{label.strip().lower()}"
    return None


def _enrich_variant(v: OCRVariant) -> None:
    """
    Mutate a single OCRVariant in-place with kind/normalized_size/group_key.
    """
    label = (v.get("label") or "").strip()
    if not label:
        v["kind"] = "other"
        return

    kind, norm_size = _infer_variant_kind_and_normalized_size(label)
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
