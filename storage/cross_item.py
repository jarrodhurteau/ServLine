"""
Cross-Item Consistency Checks -- Sprint 8.3 Days 61-62

Compares items ACROSS the menu to detect anomalies that per-item
checks cannot catch:

  1. Duplicate / near-duplicate name detection (exact + fuzzy)
  2. Category price outlier detection (MAD-based)
  3. Category isolation detection (lone miscategorized items)

Day 62 additions: Fuzzy name matching via SequenceMatcher to catch
OCR typos like "BUFALO" vs "BUFFALO", "MARGARITA" vs "MARGHERITA".

Entry function: check_cross_item_consistency(text_blocks)
Mutates in place by appending to each item's price_flags list.

Pipeline placement: Step 9.1, after score_variant_confidence (Step 8.7).
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Name extraction / normalisation helpers
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")
_COMMON_PREFIXES_RE = re.compile(
    r"^(?:our\s+|the\s+|homemade\s+|fresh\s+|classic\s+)",
    re.IGNORECASE,
)
_PRICE_TOKEN_RE = re.compile(r"\$?\d+\.\d{2}")

# Day 62: Fuzzy matching constants
_FUZZY_THRESHOLD = 0.82   # similarity ratio to consider a fuzzy match
_FUZZY_MIN_LEN = 4        # minimum normalized name length for fuzzy comparison


def _name_similarity(a: str, b: str) -> float:
    """Return similarity ratio (0.0-1.0) between two normalised names."""
    return SequenceMatcher(None, a, b).ratio()


def _extract_item_name(tb: Dict[str, Any]) -> str:
    """Extract a comparable item name from a text_block or item dict.

    Priority:
      1. grammar.parsed_name  (pipeline path, most cleaned-up)
      2. name                 (ai_ocr_helper path)
      3. merged_text with price tokens stripped (fallback)
    """
    grammar = tb.get("grammar") or {}
    parsed = grammar.get("parsed_name")
    if parsed and parsed.strip():
        return parsed.strip()

    name = tb.get("name")
    if name and name.strip():
        return name.strip()

    merged = tb.get("merged_text") or ""
    merged = _PRICE_TOKEN_RE.sub("", merged)
    return merged.strip()


def _normalize_name(name: str) -> str:
    """Normalise a name for comparison: lowercase, strip common prefixes,
    collapse whitespace, strip trailing punctuation."""
    n = name.lower().strip()
    # Strip common prefixes repeatedly (handles "Our Classic ...")
    prev = None
    while prev != n:
        prev = n
        n = _COMMON_PREFIXES_RE.sub("", n)
    n = _WHITESPACE_RE.sub(" ", n).strip()
    n = n.rstrip(".:- ")
    return n


# ---------------------------------------------------------------------------
# Price extraction helper
# ---------------------------------------------------------------------------

def _extract_primary_price_cents(tb: Dict[str, Any]) -> int:
    """Return the primary price in cents (lowest positive variant, else
    first positive price candidate, else direct price_cents field)."""
    # Variants: take lowest positive price (base / smallest size)
    variants = tb.get("variants") or []
    if variants:
        var_prices = [
            v.get("price_cents", 0)
            for v in variants
            if isinstance(v.get("price_cents"), (int, float))
            and v.get("price_cents", 0) > 0
        ]
        if var_prices:
            return int(min(var_prices))

    # Price candidates (pipeline uses price_cents, ai_ocr_helper uses value)
    candidates = tb.get("price_candidates") or []
    for pc in candidates:
        cents = pc.get("price_cents", 0)
        if isinstance(cents, (int, float)) and cents > 0:
            return int(cents)
        val = pc.get("value", 0)
        if isinstance(val, (int, float)) and val > 0:
            return int(round(val * 100))

    # Direct field
    direct = tb.get("price_cents", 0)
    if isinstance(direct, (int, float)) and direct > 0:
        return int(direct)

    return 0


# ---------------------------------------------------------------------------
# Check 1: Duplicate / near-duplicate name detection
# ---------------------------------------------------------------------------

def _check_duplicate_names(text_blocks: List[Dict[str, Any]]) -> None:
    """Flag items with duplicate normalised names (exact + fuzzy)."""

    # --- Phase 1: collect normalised names ---
    items: List[Tuple[int, str, int]] = []  # (idx, norm_name, price)
    for idx, tb in enumerate(text_blocks):
        raw_name = _extract_item_name(tb)
        if not raw_name or len(raw_name) < 3:
            continue
        norm = _normalize_name(raw_name)
        if not norm:
            continue
        price = _extract_primary_price_cents(tb)
        items.append((idx, norm, price))

    # --- Phase 2: exact matching (unchanged from Day 61) ---
    name_groups: Dict[str, List[Tuple[int, int]]] = {}
    for idx, norm, price in items:
        name_groups.setdefault(norm, []).append((idx, price))

    exact_grouped: Set[int] = set()  # indices that belong to an exact group

    for norm_name, members in name_groups.items():
        if len(members) < 2:
            continue

        exact_grouped.update(i for (i, _) in members)

        all_prices = {p for (_, p) in members}
        all_same_price = len(all_prices) == 1

        if all_same_price:
            reason = "cross_item_exact_duplicate"
            severity = "info"
        else:
            reason = "cross_item_duplicate_name"
            severity = "warn"

        for (idx, price) in members:
            other_indices = [i for (i, _) in members if i != idx]
            other_prices = [p for (i, p) in members if i != idx]
            text_blocks[idx]["price_flags"].append({
                "severity": severity,
                "reason": reason,
                "details": {
                    "normalized_name": norm_name,
                    "this_price_cents": price,
                    "other_prices_cents": other_prices,
                    "other_indices": other_indices,
                    "group_size": len(members),
                },
            })

    # --- Phase 3: fuzzy matching (Day 62) ---
    # Build per-group representative name for cross-group comparison,
    # plus ungrouped singletons. Compare all pairs across different groups.
    # Skip pairs within the same exact group (already flagged).
    fuzzy_candidates: List[Tuple[int, str, int, str]] = []
    #  (idx, norm_name, price, exact_group_key)
    for idx, norm, price in items:
        if len(norm) < _FUZZY_MIN_LEN:
            continue
        fuzzy_candidates.append((idx, norm, price, norm))

    # Track which (i, j) pairs already fuzzy-flagged to avoid double-flagging
    fuzzy_flagged: Set[Tuple[int, int]] = set()

    for a_pos in range(len(fuzzy_candidates)):
        a_idx, a_norm, a_price, a_group = fuzzy_candidates[a_pos]
        for b_pos in range(a_pos + 1, len(fuzzy_candidates)):
            b_idx, b_norm, b_price, b_group = fuzzy_candidates[b_pos]

            # Skip if same exact group (already flagged)
            if a_group == b_group:
                continue

            # Skip if names are identical (should be same group, but defensive)
            if a_norm == b_norm:
                continue

            sim = _name_similarity(a_norm, b_norm)
            if sim < _FUZZY_THRESHOLD:
                continue

            # Avoid double-flagging the same pair
            pair_key = (min(a_idx, b_idx), max(a_idx, b_idx))
            if pair_key in fuzzy_flagged:
                continue
            fuzzy_flagged.add(pair_key)

            same_price = a_price == b_price
            if same_price:
                reason = "cross_item_fuzzy_exact_duplicate"
                severity = "info"
            else:
                reason = "cross_item_fuzzy_duplicate"
                severity = "warn"

            text_blocks[a_idx]["price_flags"].append({
                "severity": severity,
                "reason": reason,
                "details": {
                    "this_name": a_norm,
                    "matched_name": b_norm,
                    "similarity": round(sim, 3),
                    "this_price_cents": a_price,
                    "matched_price_cents": b_price,
                    "matched_index": b_idx,
                },
            })
            text_blocks[b_idx]["price_flags"].append({
                "severity": severity,
                "reason": reason,
                "details": {
                    "this_name": b_norm,
                    "matched_name": a_norm,
                    "similarity": round(sim, 3),
                    "this_price_cents": b_price,
                    "matched_price_cents": a_price,
                    "matched_index": a_idx,
                },
            })


# ---------------------------------------------------------------------------
# Check 2: Category price outlier detection (IQR-based)
# ---------------------------------------------------------------------------

def _check_category_price_outliers(text_blocks: List[Dict[str, Any]]) -> None:
    """Flag items whose price is a statistical outlier within their category.

    Uses MAD (median absolute deviation) which is robust to outliers —
    unlike IQR, a single extreme value does not corrupt the threshold.
    Threshold: 3 × MAD_effective, where MAD_effective = max(MAD, 10% of median).
    """
    cat_groups: Dict[str, List[Tuple[int, int]]] = {}

    for idx, tb in enumerate(text_blocks):
        cat = tb.get("category")
        if not cat:
            continue
        price = _extract_primary_price_cents(tb)
        if price <= 0:
            continue
        cat_groups.setdefault(cat, []).append((idx, price))

    for cat, members in cat_groups.items():
        if len(members) < 3:
            continue

        prices = [p for (_, p) in members]
        median_price = statistics.median(prices)
        if median_price <= 0:
            continue

        # MAD with a floor of 10% of median (avoids zero-MAD on identical prices)
        deviations = [abs(p - median_price) for p in prices]
        mad = statistics.median(deviations)
        mad_effective = max(mad, median_price * 0.10)
        threshold = 3.0 * mad_effective

        for (idx, price) in members:
            deviation = abs(price - median_price)
            if deviation > threshold:
                direction = "above" if price > median_price else "below"
                text_blocks[idx]["price_flags"].append({
                    "severity": "warn",
                    "reason": "cross_item_category_price_outlier",
                    "details": {
                        "category": cat,
                        "item_price_cents": price,
                        "category_median_cents": int(median_price),
                        "category_mad_cents": int(mad),
                        "deviation_cents": int(deviation),
                        "threshold_cents": int(threshold),
                        "direction": direction,
                        "category_item_count": len(members),
                    },
                })


# ---------------------------------------------------------------------------
# Check 3: Category isolation detection
# ---------------------------------------------------------------------------

def _check_category_isolation(text_blocks: List[Dict[str, Any]]) -> None:
    """Flag items whose category differs from all nearby neighbours."""
    n = len(text_blocks)

    for idx, tb in enumerate(text_blocks):
        cat = tb.get("category")
        if not cat:
            continue

        neighbor_cats: List[str] = []
        for offset in (-2, -1, 1, 2):
            ni = idx + offset
            if 0 <= ni < n:
                nc = text_blocks[ni].get("category")
                if nc:
                    neighbor_cats.append(nc)

        if len(neighbor_cats) < 2:
            continue

        if all(nc != cat for nc in neighbor_cats):
            dominant = Counter(neighbor_cats).most_common(1)[0][0]
            tb["price_flags"].append({
                "severity": "info",
                "reason": "cross_item_category_isolated",
                "details": {
                    "item_category": cat,
                    "neighbor_categories": neighbor_cats,
                    "dominant_neighbor_category": dominant,
                    "position_index": idx,
                },
            })


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def check_cross_item_consistency(text_blocks: List[Dict[str, Any]]) -> None:
    """Run all cross-item consistency checks.

    Pipeline placement: Step 9.1, after score_variant_confidence (8.7).
    Mutates text_blocks in place (appends to price_flags).
    """
    if len(text_blocks) < 2:
        return

    for tb in text_blocks:
        tb.setdefault("price_flags", [])

    _check_duplicate_names(text_blocks)
    _check_category_price_outliers(text_blocks)
    _check_category_isolation(text_blocks)
