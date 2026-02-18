"""
Cross-Item Consistency Checks -- Sprint 8.3 Days 61-65

Compares items ACROSS the menu to detect anomalies that per-item
checks cannot catch:

  1. Duplicate / near-duplicate name detection (exact + fuzzy)
  2. Category price outlier detection (MAD-based)
  3. Category isolation detection (lone miscategorized items)
  4. Category reassignment suggestions (neighbor-based smoothing)
  5. Cross-category price coherence (sides < entrees)
  6. Variant count consistency (category-level mode comparison)
  7. Variant label set consistency (dominant size labels)
  8. Price step consistency (MAD-based step outlier detection)

Day 62 additions: Fuzzy name matching via SequenceMatcher to catch
OCR typos like "BUFALO" vs "BUFFALO", "MARGARITA" vs "MARGHERITA".

Day 63 additions: Multi-signal category suggestion using neighbor
agreement, keyword fit, price band, and original confidence.

Day 64 additions: Cross-category price coherence — detect when a
cheap-category item (side, beverage) costs more than the typical
expensive-category item (pizza, pasta), or vice versa.

Day 65 additions: Cross-item variant pattern enforcement — detect
variant count outliers, mismatched size label sets, and price step
deviations within the same category.

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

from .category_infer import CATEGORY_KEYWORDS, CATEGORY_PRICE_BANDS
from .parsers.size_vocab import size_ordinal, size_track

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

# Day 63: Category suggestion constants
_SUGGESTION_WINDOW = 3            # +-3 neighbor window (wider than isolation's +-2)
_SUGGESTION_MIN_NEIGHBORS = 3     # minimum categorized neighbors to make a suggestion
_SUGGESTION_MIN_AGREEMENT = 0.60  # 60% of neighbors must agree on dominant category
_SUGGESTION_MIN_CONFIDENCE = 0.30 # minimum suggestion confidence to emit flag
_SUGGESTION_KEYWORD_GUARD = 2     # suppress if current category has >= this many keyword matches

# Day 65: Cross-item variant pattern enforcement constants
_VARIANT_COUNT_MIN_ITEMS = 3      # need 3+ multi-variant items to establish mode
_VARIANT_COUNT_MIN_GAP = 2        # flag when mode - actual >= 2
_VARIANT_LABEL_MIN_ITEMS = 3      # need 3+ size-variant items for label check
_VARIANT_LABEL_MIN_AGREEMENT = 0.60  # 60% must agree on dominant label set
_PRICE_STEP_MIN_ITEMS = 3         # need 3+ multi-size items for step check


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
# Day 63: Keyword and price-band helpers for category suggestions
# ---------------------------------------------------------------------------

def _keyword_match_count(norm_name: str, category: str) -> int:
    """Count how many CATEGORY_KEYWORDS for *category* appear in *norm_name*.

    Expects *norm_name* to be lowercased already.
    """
    if not norm_name:
        return 0
    count = 0
    for kw in CATEGORY_KEYWORDS.get(category, ()):
        if kw in norm_name:
            count += 1
    return count


def _in_price_band(price_cents: int, category: str) -> bool:
    """Return True if *price_cents* falls within the expected band for *category*."""
    if price_cents <= 0:
        return False
    band = CATEGORY_PRICE_BANDS.get(category)
    if not band:
        return False
    lo, hi = band
    return lo <= price_cents <= hi


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
# Check 4: Category reassignment suggestions (Day 63)
# ---------------------------------------------------------------------------

def _check_category_suggestions(text_blocks: List[Dict[str, Any]]) -> None:
    """Suggest category reassignment based on multi-signal scoring.

    Signals:
      1. Neighbor agreement (primary) — +-3 window, need 60%+ consensus
      2. Keyword fit — do keywords in the item name favor current or suggested?
      3. Price band fit — does the price fit current or suggested category band?
      4. Original category confidence — low confidence boosts suggestion

    Emits ``cross_item_category_suggestion`` flags (severity: info).
    Keyword guard: if the current category has >= 2 keyword matches in the
    item name, suppress the suggestion (item is likely correctly categorized).
    """
    n = len(text_blocks)

    for idx, tb in enumerate(text_blocks):
        current_cat = tb.get("category")
        if not current_cat:
            continue

        # --- Signal 1: Neighbor agreement ---
        neighbor_cats: List[str] = []
        for offset in range(-_SUGGESTION_WINDOW, _SUGGESTION_WINDOW + 1):
            if offset == 0:
                continue
            ni = idx + offset
            if 0 <= ni < n:
                nc = text_blocks[ni].get("category")
                if nc:
                    neighbor_cats.append(nc)

        if len(neighbor_cats) < _SUGGESTION_MIN_NEIGHBORS:
            continue

        cat_counts = Counter(neighbor_cats)
        dominant_cat, dominant_count = cat_counts.most_common(1)[0]

        if dominant_cat == current_cat:
            continue

        neighbor_agreement = dominant_count / len(neighbor_cats)
        if neighbor_agreement < _SUGGESTION_MIN_AGREEMENT:
            continue

        # --- Keyword guard ---
        raw_name = _extract_item_name(tb)
        norm_name = _normalize_name(raw_name).lower() if raw_name else ""

        current_kw_count = _keyword_match_count(norm_name, current_cat)
        if current_kw_count >= _SUGGESTION_KEYWORD_GUARD:
            continue

        # --- Signal 2: Keyword fit ---
        suggested_kw_count = _keyword_match_count(norm_name, dominant_cat)

        if suggested_kw_count > current_kw_count:
            keyword_delta = 0.20
        elif current_kw_count > suggested_kw_count:
            keyword_delta = -0.20
        else:
            keyword_delta = 0.0

        # --- Signal 3: Price band fit ---
        price_cents = _extract_primary_price_cents(tb)
        price_band_delta = 0.0
        if price_cents > 0:
            fits_current = _in_price_band(price_cents, current_cat)
            fits_suggested = _in_price_band(price_cents, dominant_cat)
            if fits_suggested and not fits_current:
                price_band_delta = 0.15
            elif fits_current and not fits_suggested:
                price_band_delta = -0.15

        # --- Signal 4: Original category confidence ---
        orig_conf = tb.get("category_confidence")
        if orig_conf is None:
            orig_conf = 50
        orig_conf = int(orig_conf)

        confidence_delta = 0.0
        if orig_conf < 50:
            confidence_delta = 0.10
        elif orig_conf >= 80:
            confidence_delta = -0.15

        # --- Combine signals ---
        suggestion_confidence = (
            neighbor_agreement * 0.40
            + keyword_delta
            + price_band_delta
            + confidence_delta
        )
        suggestion_confidence = max(0.0, min(1.0, suggestion_confidence))

        if suggestion_confidence < _SUGGESTION_MIN_CONFIDENCE:
            continue

        # --- Build human-readable signal descriptions ---
        signals: List[str] = []
        signals.append(
            f"{dominant_count}/{len(neighbor_cats)} neighbors are {dominant_cat}"
        )
        if keyword_delta > 0:
            signals.append(
                f"keywords favor {dominant_cat} ({suggested_kw_count} vs {current_kw_count})"
            )
        elif keyword_delta < 0:
            signals.append(
                f"keywords favor {current_cat} ({current_kw_count} vs {suggested_kw_count})"
            )
        if price_band_delta > 0:
            signals.append(f"price fits {dominant_cat} band, not {current_cat}")
        elif price_band_delta < 0:
            signals.append(f"price fits {current_cat} band, not {dominant_cat}")
        if confidence_delta > 0:
            signals.append(f"low original confidence ({orig_conf})")
        elif confidence_delta < 0:
            signals.append(f"high original confidence ({orig_conf})")

        tb["price_flags"].append({
            "severity": "info",
            "reason": "cross_item_category_suggestion",
            "details": {
                "current_category": current_cat,
                "suggested_category": dominant_cat,
                "suggestion_confidence": round(suggestion_confidence, 3),
                "neighbor_agreement": round(neighbor_agreement, 3),
                "neighbor_count": len(neighbor_cats),
                "signals": signals,
            },
        })


# ---------------------------------------------------------------------------
# Check 5: Cross-category price coherence (Day 64)
# ---------------------------------------------------------------------------

# Expected ordering: (cheaper_category, more_expensive_category).
# Only encodes strong, near-universal relationships.
_CROSS_CAT_PRICE_RULES: List[Tuple[str, str]] = [
    # Beverages are almost always cheapest
    ("Beverages", "Sides / Appetizers"),
    ("Beverages", "Salads"),
    ("Beverages", "Wings"),
    ("Beverages", "Subs / Sandwiches"),
    ("Beverages", "Burgers"),
    ("Beverages", "Pizza"),
    ("Beverages", "Pasta"),
    ("Beverages", "Calzones / Stromboli"),
    # Sides/appetizers cheaper than main entrees
    ("Sides / Appetizers", "Subs / Sandwiches"),
    ("Sides / Appetizers", "Burgers"),
    ("Sides / Appetizers", "Pizza"),
    ("Sides / Appetizers", "Pasta"),
    ("Sides / Appetizers", "Calzones / Stromboli"),
    # Desserts cheaper than main entrees
    ("Desserts", "Pizza"),
    ("Desserts", "Pasta"),
    ("Desserts", "Calzones / Stromboli"),
]

_CROSS_CAT_MIN_ITEMS = 2       # Need >= 2 items per category to compute median
_CROSS_CAT_MIN_GAP_RATIO = 1.3  # Medians must differ by 30%+ to activate rule


def _check_cross_category_coherence(text_blocks: List[Dict[str, Any]]) -> None:
    """Flag items whose price violates cross-category ordering expectations.

    For each (cheap_cat, expensive_cat) rule:
      - If a cheap_cat item costs MORE than the expensive_cat median → flag
      - If an expensive_cat item costs LESS than the cheap_cat median → flag

    Guards:
      - Both categories need >= 2 priced items.
      - Medians must have a 30%+ gap (otherwise categories overlap in this menu).
      - Each item gets at most one "above" flag and one "below" flag (most
        dramatic violation kept).
    """
    # --- Step 1: Group items by category with prices ---
    cat_items: Dict[str, List[Tuple[int, int]]] = {}  # cat -> [(idx, price_cents)]
    for idx, tb in enumerate(text_blocks):
        cat = tb.get("category")
        if not cat:
            continue
        price = _extract_primary_price_cents(tb)
        if price <= 0:
            continue
        cat_items.setdefault(cat, []).append((idx, price))

    # --- Step 2: Compute per-category medians ---
    cat_medians: Dict[str, float] = {}
    for cat, members in cat_items.items():
        if len(members) >= _CROSS_CAT_MIN_ITEMS:
            prices = [p for (_, p) in members]
            cat_medians[cat] = statistics.median(prices)

    # --- Step 3: Collect potential flags per item ---
    # For each item, keep only the most dramatic violation (largest gap).
    # Key: item index  Value: (details_dict, gap_cents)
    best_above: Dict[int, Tuple[Dict[str, Any], int]] = {}
    best_below: Dict[int, Tuple[Dict[str, Any], int]] = {}

    for cheap_cat, exp_cat in _CROSS_CAT_PRICE_RULES:
        if cheap_cat not in cat_medians or exp_cat not in cat_medians:
            continue

        cheap_med = cat_medians[cheap_cat]
        exp_med = cat_medians[exp_cat]

        # Skip if medians don't have a meaningful gap
        if exp_med < cheap_med * _CROSS_CAT_MIN_GAP_RATIO:
            continue

        # Cheap-cat items priced above expensive-cat median
        for (idx, price) in cat_items.get(cheap_cat, []):
            if price > exp_med:
                gap = price - int(exp_med)
                prev = best_above.get(idx)
                if prev is None or gap > prev[1]:
                    best_above[idx] = ({
                        "item_category": cheap_cat,
                        "item_price_cents": price,
                        "compared_category": exp_cat,
                        "compared_median_cents": int(exp_med),
                        "own_median_cents": int(cheap_med),
                    }, gap)

        # Expensive-cat items priced below cheap-cat median
        for (idx, price) in cat_items.get(exp_cat, []):
            if price < cheap_med:
                gap = int(cheap_med) - price
                prev = best_below.get(idx)
                if prev is None or gap > prev[1]:
                    best_below[idx] = ({
                        "item_category": exp_cat,
                        "item_price_cents": price,
                        "compared_category": cheap_cat,
                        "compared_median_cents": int(cheap_med),
                        "own_median_cents": int(exp_med),
                    }, gap)

    # --- Step 4: Emit flags (one per item per direction) ---
    for idx, (details, _gap) in best_above.items():
        text_blocks[idx]["price_flags"].append({
            "severity": "warn",
            "reason": "cross_category_price_above",
            "details": details,
        })

    for idx, (details, _gap) in best_below.items():
        text_blocks[idx]["price_flags"].append({
            "severity": "warn",
            "reason": "cross_category_price_below",
            "details": details,
        })


# ---------------------------------------------------------------------------
# Check 6: Cross-item variant count consistency (Day 65)
# ---------------------------------------------------------------------------

def _check_variant_count_consistency(text_blocks: List[Dict[str, Any]]) -> None:
    """Flag items whose variant count deviates from the category mode.

    Within each category, compute the MODE variant count among items with
    2+ variants.  Flag items where ``mode - actual >= 2``.

    Items with 0-1 variants (single-price) are excluded entirely.
    """
    cat_groups: Dict[str, List[Tuple[int, int]]] = {}  # cat -> [(idx, var_count)]

    for idx, tb in enumerate(text_blocks):
        cat = tb.get("category")
        if not cat:
            continue
        variants = tb.get("variants") or []
        if len(variants) < 2:
            continue
        cat_groups.setdefault(cat, []).append((idx, len(variants)))

    for cat, members in cat_groups.items():
        if len(members) < _VARIANT_COUNT_MIN_ITEMS:
            continue

        counts = [c for (_, c) in members]
        mode_count = Counter(counts).most_common(1)[0][0]

        for idx, var_count in members:
            gap = mode_count - var_count
            if gap >= _VARIANT_COUNT_MIN_GAP:
                text_blocks[idx]["price_flags"].append({
                    "severity": "info",
                    "reason": "cross_item_variant_count_outlier",
                    "details": {
                        "category": cat,
                        "item_variant_count": var_count,
                        "category_mode_count": mode_count,
                        "category_multi_variant_items": len(members),
                    },
                })


# ---------------------------------------------------------------------------
# Check 7: Cross-item variant label set consistency (Day 65)
# ---------------------------------------------------------------------------

def _check_variant_label_consistency(text_blocks: List[Dict[str, Any]]) -> None:
    """Flag items whose size-variant label set differs from the category norm.

    Within each category, collect the sorted tuple of ``normalized_size``
    values for ``kind == "size"`` variants.  Find the dominant set (>= 60%
    agreement).  Flag items using a non-dominant set that is neither a subset
    (gourmet right-alignment tolerance) nor a superset (extra sizes OK).
    """
    cat_groups: Dict[str, List[Tuple[int, frozenset]]] = {}

    for idx, tb in enumerate(text_blocks):
        cat = tb.get("category")
        if not cat:
            continue
        variants = tb.get("variants") or []
        size_labels: Set[str] = set()
        for v in variants:
            if v.get("kind") == "size" and v.get("normalized_size"):
                size_labels.add(v["normalized_size"])
        if len(size_labels) < 2:
            continue
        cat_groups.setdefault(cat, []).append((idx, frozenset(size_labels)))

    for cat, members in cat_groups.items():
        if len(members) < _VARIANT_LABEL_MIN_ITEMS:
            continue

        set_counter: Counter = Counter(label_set for (_, label_set) in members)
        dominant_set, dominant_count = set_counter.most_common(1)[0]

        if dominant_count / len(members) < _VARIANT_LABEL_MIN_AGREEMENT:
            continue

        for idx, item_set in members:
            if item_set == dominant_set:
                continue
            # Subset tolerance (right-alignment: {M, L} under {S, M, L})
            if item_set.issubset(dominant_set):
                continue
            # Superset tolerance (extra sizes: {S, M, L, XL} under {S, M, L})
            if dominant_set.issubset(item_set):
                continue

            text_blocks[idx]["price_flags"].append({
                "severity": "info",
                "reason": "cross_item_variant_label_mismatch",
                "details": {
                    "category": cat,
                    "item_labels": sorted(item_set),
                    "dominant_labels": sorted(dominant_set),
                    "dominant_count": dominant_count,
                    "category_size_items": len(members),
                },
            })


# ---------------------------------------------------------------------------
# Check 8: Cross-item price step consistency (Day 65)
# ---------------------------------------------------------------------------

def _check_variant_price_steps(text_blocks: List[Dict[str, Any]]) -> None:
    """Flag items whose size-variant price step deviates from the category norm.

    Within each category, compute per-item average price step between
    consecutive sizes (ordered by ``size_ordinal``).  Use MAD-based outlier
    detection to flag items whose average step is significantly different.
    Only positive steps are considered (inversions already flagged Day 57).
    """
    # cat -> [(idx, avg_step_cents)]
    cat_steps: Dict[str, List[Tuple[int, float]]] = {}

    for idx, tb in enumerate(text_blocks):
        cat = tb.get("category")
        if not cat:
            continue
        variants = tb.get("variants") or []

        # Collect size variants with valid ordinal and positive price
        sized: List[Tuple[int, int, str]] = []  # (ordinal, price_cents, track)
        for v in variants:
            if v.get("kind") != "size":
                continue
            ns = v.get("normalized_size")
            if not ns:
                continue
            pc = v.get("price_cents", 0)
            if not isinstance(pc, (int, float)) or pc <= 0:
                continue
            ordinal = size_ordinal(ns)
            if ordinal is None:
                continue
            trk = size_track(ns)
            if not trk:
                continue
            sized.append((ordinal, int(pc), trk))

        if len(sized) < 2:
            continue

        # Group by track and compute steps per track
        track_groups: Dict[str, List[Tuple[int, int]]] = {}
        for ordinal, pc, trk in sized:
            track_groups.setdefault(trk, []).append((ordinal, pc))

        item_steps: List[int] = []
        for trk, entries in track_groups.items():
            if len(entries) < 2:
                continue
            entries.sort(key=lambda x: x[0])
            for i in range(len(entries) - 1):
                step = entries[i + 1][1] - entries[i][1]
                if step > 0:
                    item_steps.append(step)

        if not item_steps:
            continue

        avg_step = sum(item_steps) / len(item_steps)
        cat_steps.setdefault(cat, []).append((idx, avg_step))

    # Outlier detection per category
    for cat, members in cat_steps.items():
        if len(members) < _PRICE_STEP_MIN_ITEMS:
            continue

        all_steps = [s for (_, s) in members]
        median_step = statistics.median(all_steps)
        if median_step <= 0:
            continue

        deviations = [abs(s - median_step) for s in all_steps]
        mad = statistics.median(deviations)
        mad_effective = max(mad, median_step * 0.15)
        threshold = 3.0 * mad_effective

        for idx, avg_step in members:
            deviation = abs(avg_step - median_step)
            if deviation > threshold:
                direction = "above" if avg_step > median_step else "below"
                text_blocks[idx]["price_flags"].append({
                    "severity": "info",
                    "reason": "cross_item_price_step_outlier",
                    "details": {
                        "category": cat,
                        "item_avg_step_cents": int(round(avg_step)),
                        "category_median_step_cents": int(round(median_step)),
                        "category_mad_step_cents": int(round(mad)),
                        "deviation_cents": int(round(deviation)),
                        "threshold_cents": int(round(threshold)),
                        "direction": direction,
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
    _check_category_suggestions(text_blocks)
    _check_cross_category_coherence(text_blocks)
    _check_variant_count_consistency(text_blocks)
    _check_variant_label_consistency(text_blocks)
    _check_variant_price_steps(text_blocks)
