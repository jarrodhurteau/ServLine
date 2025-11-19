# servline/storage/price_integrity.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import statistics

Number = int  # we treat prices as cents


def analyze_prices(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Analyze prices for a list of preview items and attach:
      - corrected_price_cents (int) when we can safely fix a mis-scaled price
      - price_flags: list of {severity, reason, details} dictionaries

    This function is designed to be called from ocr_pipeline AFTER:
      - base category inference
      - variant_engine classification (so `variants` & `group_key` exist)

    It is intentionally conservative: it only auto-fixes prices when there is
    very strong evidence (e.g., obvious decimal shift inside a tight group).
    Otherwise, it will attach a warning flag instead of changing the value.
    """
    if not items:
        return items

    # Group items by (category, variant_family_key) to get coherent price bands.
    groups: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]] = {}

    for item in items:
        cat = item.get("category")
        family_key = _extract_price_family_key(item)
        group_key = (cat, family_key)

        groups.setdefault(group_key, []).append(item)

        # Ensure we have a list to append flags to later.
        if "price_flags" not in item:
            item["price_flags"] = []

    # Process each group independently.
    for group_key, group_items in groups.items():
        _analyze_group(group_items)

    return items


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------

def _extract_price_family_key(item: Dict[str, Any]) -> Optional[str]:
    """
    Decide which variant 'family' this item belongs to for price analysis.

    Strategy:
      - Prefer size-based families (e.g., size:10in, size:12in)
      - Fallback to category-only when no obvious size family exists
    """
    variants = item.get("variants") or []
    size_keys: List[str] = []
    other_keys: List[str] = []

    for v in variants:
        kind = (v.get("kind") or "").lower()
        gk = v.get("group_key")
        if not gk:
            continue
        if kind == "size":
            size_keys.append(gk)
        else:
            other_keys.append(gk)

    if size_keys:
        # Use first size family as the main key for this item.
        return size_keys[0]
    if other_keys:
        # Fallback: some other family (flavor/style).
        return other_keys[0]
    return None


# ---------------------------------------------------------------------------
# Core group analysis
# ---------------------------------------------------------------------------

def _analyze_group(items: List[Dict[str, Any]]) -> None:
    """
    Analyze a single (category, family) group of items whose prices should be
    broadly comparable. Mutates items in-place.
    """
    # Collect non-zero prices to estimate a typical range.
    prices: List[Number] = [
        it.get("price_cents", 0)
        for it in items
        if isinstance(it.get("price_cents"), int) and it.get("price_cents", 0) > 0
    ]

    if len(prices) < 3:
        # Not enough data to form a strong opinion; still flag zeros.
        for it in items:
            _flag_zero_price_if_needed(it, None)
        return

    # Basic stats: median is robust for skewed menus.
    median_price = statistics.median(prices)
    if median_price <= 0:
        for it in items:
            _flag_zero_price_if_needed(it, None)
        return

    # Also compute a rough "typical band" (25th–75th percentile).
    prices_sorted = sorted(prices)
    q1 = statistics.median(prices_sorted[: len(prices_sorted) // 2])
    q3 = statistics.median(prices_sorted[len(prices_sorted) // 2 :])
    iqr = max(q3 - q1, 1)  # avoid division by zero

    for it in items:
        _flag_zero_price_if_needed(it, median_price)
        _check_and_fix_price(it, median_price, iqr)


# ---------------------------------------------------------------------------
# Per-item checks
# ---------------------------------------------------------------------------

def _flag_zero_price_if_needed(item: Dict[str, Any], median_price: Optional[Number]) -> None:
    price = item.get("price_cents")
    if not isinstance(price, int) or price > 0:
        return

    flags: List[Dict[str, Any]] = item.setdefault("price_flags", [])
    reason = "zero_price_in_group"
    details: Dict[str, Any] = {}
    if median_price:
        details["group_median_cents"] = int(median_price)

    flags.append(
        {
            "severity": "warn",
            "reason": reason,
            "details": details,
        }
    )


def _check_and_fix_price(item: Dict[str, Any], median_price: Number, iqr: Number) -> None:
    """
    Decide whether this item's price is suspicious, and if so:
      - try decimal-shift corrections
      - either auto-fix (corrected_price_cents) or attach a warning
    """
    price = item.get("price_cents")
    if not isinstance(price, int) or price <= 0:
        return

    flags: List[Dict[str, Any]] = item.setdefault("price_flags", [])

    # Heuristic: treat anything that is wildly far from median as suspicious.
    # We'll express deviation in multiples of IQR to keep it scale-aware.
    deviation = abs(price - median_price)
    z_iqr = deviation / max(iqr, 1)

    # If it's inside ~4 IQRs, we assume it's fine.
    if z_iqr <= 4:
        return

    # Price is an outlier; try safe decimal shift corrections.
    corrected, correction_flag = _suggest_decimal_correction(price, median_price)
    if corrected is not None and correction_flag is not None:
        # Only accept corrections that produce a plausible menu price.
        item["corrected_price_cents"] = corrected
        flags.append(correction_flag)
        return

    # If we can't confidently auto-fix, just warn.
    flags.append(
        {
            "severity": "warn",
            "reason": "price_outlier",
            "details": {
                "observed_cents": price,
                "median_cents": int(median_price),
                "deviation_iqr": z_iqr,
            },
        }
    )


# ---------------------------------------------------------------------------
# Decimal-shift heuristics
# ---------------------------------------------------------------------------

def _suggest_decimal_correction(price: Number, median_price: Number) -> Tuple[Optional[Number], Optional[Dict[str, Any]]]:
    """
    Given an obviously outlier price and the median for its group, attempt to
    fix decimal shifts by dividing by 10, 100, or 1000.

    Example:
      - If most pizzas are ~1600–2400 cents ($16–$24) and we see 16000,
        we will try 16000/10=1600, 16000/100=160, 16000/1000=16.
    """
    if median_price <= 0:
        return None, None

    candidate_divisors = [10, 100, 1000]
    best_candidate: Optional[Number] = None
    best_ratio: Optional[float] = None
    best_divisor: Optional[int] = None

    for d in candidate_divisors:
        if price % d != 0:
            continue
        cand = price // d
        # Reject obviously unrealistic menu prices.
        if cand <= 25 or cand >= 50000:
            # < $0.25 or > $500.00 is probably still wrong.
            continue

        ratio = abs(cand - median_price) / median_price
        if best_ratio is None or ratio < best_ratio:
            best_ratio = ratio
            best_candidate = cand
            best_divisor = d

    # Only accept a candidate if it is *substantially* closer to the median.
    if best_candidate is None:
        return None, None

    # Sanity check: require that original price is at least 5x further away
    # than the corrected candidate.
    original_ratio = abs(price - median_price) / median_price
    if best_ratio is None or original_ratio <= 5 * best_ratio:
        return None, None

    flag = {
        "severity": "auto_fix",
        "reason": "decimal_shift_corrected",
        "details": {
            "original_cents": price,
            "corrected_cents": best_candidate,
            "median_cents": int(median_price),
            "divisor": best_divisor,
            "original_ratio_to_median": original_ratio,
            "corrected_ratio_to_median": best_ratio,
        },
    }
    return best_candidate, flag
