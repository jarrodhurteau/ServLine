# servline/storage/price_integrity.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import statistics
import re

Number = int  # we treat prices as cents


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def analyze_prices(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Analyze prices for a list of preview/draft items and attach:

      - corrected_price_cents (int) when we can safely fix a mis-scaled price
      - price_flags: list of {severity, reason, details} dictionaries
      - price_role: optional lightweight role tag:
          * "primary" (default)
          * "side"    (add-ons, extras, toppings)
          * "coupon"  (deals / combos / BOGO / specials)
      - price_meta.group_median_cents / group_iqr_cents

    This function is designed to be called from ocr_pipeline/downstream AFTER:
      - base category inference
      - variant_engine classification (so `variants` & `group_key` exist)

    V2 adds:
      - side-price detection (add-ons, toppings, extras)
      - coupon/odd-line detection (deals, combos, BOGO, "2 for" lines)
      - more conservative group stats based on primary items only
      - outlier detection + decimal-shift correction in a category/variant band
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

        # Ensure we have structural fields to append into later.
        item.setdefault("price_flags", [])
        item.setdefault("price_meta", {})

        # Light role classification (side / coupon / primary)
        # We keep this cheap and textual.
        role = item.get("price_role")
        if not role:
            if _is_coupon_or_deal_item(item):
                item["price_role"] = "coupon"
            elif _is_side_price_item(item):
                item["price_role"] = "side"
            else:
                item["price_role"] = "primary"

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
# Side-price & coupon detection (textual, conservative)
# ---------------------------------------------------------------------------

_SIDE_HINTS = [
    "add ", "extra ", "side of", "side:", "topping", "toppings",
    "each topping", "per topping", "extra cheese", "add cheese",
    "add bacon", "add pepperoni", "extra sauce", "cup of sauce",
    "ranch", "blue cheese", "bleu cheese", "dressing", "jalapeños",
    "peppers", "mushrooms", "onions", "olive", "olives",
    "garlic knots", "breadsticks", "fries", "chips",
]

_COUPON_HINTS = [
    "coupon", "special", "specials", "deal", "family deal", "family special",
    "combo", "combos", "meal deal", "value meal",
    "2 for", "two for", "3 for", "three for",
    "buy 1", "buy one", "get 1", "get one", "bogo",
    "any 2", "any two", "pick any", "choose any",
    "only", "for only", "just",
]


def _text_from_item(item: Dict[str, Any]) -> str:
    name = (item.get("name") or "").strip()
    desc = (item.get("description") or "").strip()
    if desc:
        return f"{name} {desc}".strip()
    return name


def _is_side_price_item(item: Dict[str, Any]) -> bool:
    """
    Identify obvious add-ons / extras / per-topping lines.

    We keep this conservative to avoid mis-tagging real entrees.
    """
    txt = _text_from_item(item).lower()
    if not txt:
        return False

    # Short lines with a strong side/add-on hint.
    if len(txt) <= 64:
        for hint in _SIDE_HINTS:
            if hint in txt:
                return True

    # Categories that scream "side/extra".
    cat = (item.get("category") or "").lower()
    if cat in {"toppings", "extras", "sides", "dressings"}:
        return True

    return False


def _is_coupon_or_deal_item(item: Dict[str, Any]) -> bool:
    """
    Detect menu lines that look like coupons / bundled deals / BOGO / combos.
    These should not anchor the main price bands.
    """
    txt = _text_from_item(item).lower()
    if not txt:
        return False

    # If it mentions "any" + quantity + deal language, treat as coupon-ish.
    for hint in _COUPON_HINTS:
        if hint in txt:
            return True

    # Very long, descriptive lines with multiple "and/+" often indicate combos.
    if len(txt) > 80 and (" and " in txt or " + " in txt):
        return True

    return False


# ---------------------------------------------------------------------------
# Core group analysis
# ---------------------------------------------------------------------------

def _analyze_group(items: List[Dict[str, Any]]) -> None:
    """
    Analyze a single (category, family) group of items whose prices should be
    broadly comparable. Mutates items in-place.

    V2 behavior:
      - Compute stats primarily from "primary" items (not sides/coupons).
      - Attach group median/IQR into price_meta for all items.
      - Retain existing decimal-shift correction + outlier detection.
    """
    if not items:
        return

    primary_items = [
        it for it in items
        if (it.get("price_role") or "primary") == "primary"
    ]
    # Fallback: if somehow no primaries, use the whole group.
    basis_items = primary_items or items

    prices: List[Number] = [
        it.get("price_cents", 0)
        for it in basis_items
        if isinstance(it.get("price_cents"), int) and it.get("price_cents", 0) > 0
    ]

    # If we don't have enough signal, we still flag zeros but skip heavy stats.
    if len(prices) < 3:
        for it in items:
            _attach_group_meta(it, None, None)
            _flag_zero_price_if_needed(it, None)
            _flag_side_or_coupon(it)
        return

    # Basic stats: median is robust for skewed menus.
    median_price = statistics.median(prices)
    if median_price <= 0:
        for it in items:
            _attach_group_meta(it, None, None)
            _flag_zero_price_if_needed(it, None)
            _flag_side_or_coupon(it)
        return

    # Also compute a rough "typical band" (25th–75th percentile).
    prices_sorted = sorted(prices)
    q1 = statistics.median(prices_sorted[: len(prices_sorted) // 2])
    q3 = statistics.median(prices_sorted[len(prices_sorted) // 2 :])
    iqr = max(q3 - q1, 1)  # avoid division by zero

    # Attach common group meta and run per-item checks.
    for it in items:
        _attach_group_meta(it, median_price, iqr)
        _flag_zero_price_if_needed(it, median_price)
        _flag_side_or_coupon(it)

        # We still want to catch insane numbers on sides/coupons,
        # but primary lines are the main concern.
        _check_and_fix_price(
            it,
            median_price=median_price,
            iqr=iqr,
        )


def _attach_group_meta(item: Dict[str, Any], median_price: Optional[Number], iqr: Optional[Number]) -> None:
    meta: Dict[str, Any] = item.setdefault("price_meta", {})
    if median_price is not None:
        meta["group_median_cents"] = int(median_price)
    if iqr is not None:
        meta["group_iqr_cents"] = int(iqr)


def _flag_side_or_coupon(item: Dict[str, Any]) -> None:
    role = item.get("price_role") or "primary"
    flags: List[Dict[str, Any]] = item.setdefault("price_flags", [])
    if role == "side":
        # Mostly informational; downstream UI can choose how prominently to show.
        flags.append(
            {
                "severity": "info",
                "reason": "side_price_candidate",
                "details": {
                    "hint": "Likely add-on / extra / topping line",
                },
            }
        )
    elif role == "coupon":
        flags.append(
            {
                "severity": "info",
                "reason": "coupon_or_deal_line",
                "details": {
                    "hint": "Likely coupon / combo / deal line; do not treat as base item price",
                },
            }
        )


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

    We apply this to all roles, but the stats come from primary items, so
    sides/coupons are judged relative to the main band.
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
