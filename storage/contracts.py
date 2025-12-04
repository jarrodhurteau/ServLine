# storage/contracts.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

"""
Contracts & validators for **structured ingestion** (CSV / JSON).

This is the "One Brain" side:
- Portal stays thin and calls into these helpers.
- We keep field rules, price normalization, and payload shape checks here.

Intended consumers:
- storage/import_jobs.py (CSV + JSON ingest)
- storage/drafts.py (when constructing drafts from structured items)
"""

StructuredItem = Dict[str, Any]
ValidationError = Dict[str, Any]


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def _is_intlike(x: Any) -> bool:
    try:
        int(x)
        return True
    except Exception:
        return False


def _is_floatlike(x: Any) -> bool:
    try:
        float(str(x).replace(",", "").replace("$", "").strip())
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def normalize_price_to_cents(raw: Any) -> Tuple[Optional[int], Optional[str]]:
    """
    Normalize a price value into integer cents.

    Accepted forms:
      - 1599           -> 1599 (treated as cents; POS-style)
      - "1599"         -> 1599 (cents)
      - 15.99          -> 1599 (dollars)
      - "15.99"        -> 1599 (dollars)
      - "$15.99"       -> 1599
      - "15"           -> 1500 (dollars)
      - "" / None      -> (None, None)  (no price supplied)

    Returns:
      (price_cents | None, error_message | None)
    """
    if raw is None:
        return None, None

    if isinstance(raw, str):
        txt = raw.strip()
        if txt == "":
            return None, None
        # strip currency symbols and commas
        txt_clean = txt.replace("$", "").replace(",", "").strip()
    else:
        txt = str(raw).strip()
        if txt == "":
            return None, None
        txt_clean = txt

    # int-like? (no dot)
    if txt_clean.isdigit():
        val = int(txt_clean)
        if val < 0:
            return None, "price must not be negative"
        # Heuristic: large values are probably cents already (POS),
        # small values are likely dollars.
        if val >= 1000:
            cents = val
        else:
            cents = val * 100
        return cents, None

    # float-like (dollars with decimal)
    if _is_floatlike(txt_clean):
        try:
            dollars = float(txt_clean)
        except Exception:
            return None, f"unable to parse price '{raw}'"
        if dollars < 0:
            return None, "price must not be negative"
        cents = int(round(dollars * 100))
        return cents, None

    return None, f"unable to parse price '{raw}'"


def normalize_category(value: Any) -> Optional[str]:
    """
    Normalize a category / subcategory value into a clean string or None.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def normalize_text(value: Any) -> Optional[str]:
    """
    Normalize an optional text field (name / description / size / sku).
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value).strip() or None
    cleaned = value.strip()
    return cleaned or None


# ---------------------------------------------------------------------------
# Structured item validation
# ---------------------------------------------------------------------------

STRUCTURED_ITEM_KEYS = {
    "name",
    "description",
    "category",
    "subcategory",
    "price",        # raw price (string / number) – optional, normalized into price_cents
    "price_cents",  # already-normalized cents – optional
    "size",
    "sku",
    "meta",
}


def validate_structured_items(
    rows: List[StructuredItem],
) -> Tuple[List[StructuredItem], List[ValidationError], Dict[str, int]]:
    """
    Validate and normalize a list of structured menu items.

    Expected per-row keys (not all required):
      - name: required, string
      - description: optional string
      - category: optional string
      - subcategory: optional string
      - price: optional raw price (string/number) – will be normalized
      - price_cents: optional int-like – used if present & valid
      - size: optional string (e.g., "Small", "12\"", "10 wings")
      - sku: optional string / code
      - meta: optional dict (extra POS / system fields)

    Behaviour:
      - Rows missing required 'name' are considered errors.
      - Price comes from:
          1) price_cents (if present and valid)
          2) normalize_price_to_cents(price) otherwise
      - If both are missing/invalid, price_cents is left as None (not fatal).
      - category / subcategory are normalized via normalize_category().
      - meta must be dict if present.

    Returns:
      (clean_items, errors, summary_dict)
    """
    clean: List[StructuredItem] = []
    errors: List[ValidationError] = []

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(
                {
                    "index": idx,
                    "error": "row must be an object",
                    "row": row,
                }
            )
            continue

        err_msgs: List[str] = []

        # name (required)
        raw_name = row.get("name")
        name = normalize_text(raw_name)
        if not name:
            err_msgs.append("name is required and must be a non-empty string")

        # description (optional)
        description = normalize_text(row.get("description"))

        # category / subcategory
        category = normalize_category(row.get("category"))
        subcategory = normalize_category(row.get("subcategory"))

        # meta (optional dict)
        meta = row.get("meta")
        if meta is not None and not isinstance(meta, dict):
            err_msgs.append("meta must be an object if provided")

        # size / sku (optional text)
        size = normalize_text(row.get("size"))
        sku = normalize_text(row.get("sku"))

        # price resolution
        price_cents: Optional[int] = None
        price_errors: List[str] = []

        if "price_cents" in row and row["price_cents"] is not None:
            if _is_intlike(row["price_cents"]):
                price_cents = int(row["price_cents"])
                if price_cents < 0:
                    price_errors.append("price_cents must not be negative")
            else:
                price_errors.append("price_cents must be an integer if provided")

        # If we don't have a usable price_cents, look at raw "price"
        if price_cents is None:
            raw_price = row.get("price")
            pc, err = normalize_price_to_cents(raw_price)
            if err:
                # Only treat as error if something non-empty was provided.
                if raw_price not in (None, "", " "):
                    price_errors.append(err)
            price_cents = pc

        # accumulate price-related errors
        err_msgs.extend(price_errors)

        if err_msgs:
            errors.append(
                {
                    "index": idx,
                    "errors": err_msgs,
                    "row": row,
                }
            )
            continue

        # Default confidence for structured items (high trust, can be tuned)
        confidence = row.get("confidence")
        if confidence is None or not _is_intlike(confidence):
            confidence_int = 95
        else:
            confidence_int = int(confidence)
            if confidence_int < 0:
                confidence_int = 0
            if confidence_int > 100:
                confidence_int = 100

        clean.append(
            {
                "name": name,
                "description": description,
                "category": category,
                "subcategory": subcategory,
                "price_cents": price_cents,
                "size": size,
                "sku": sku,
                "meta": meta or {},
                "confidence": confidence_int,
            }
        )

    summary = {
        "total_rows": len(rows),
        "valid_rows": len(clean),
        "error_rows": len(errors),
    }

    return clean, errors, summary


# ---------------------------------------------------------------------------
# JSON payload-level validation
# ---------------------------------------------------------------------------

def validate_structured_menu_payload(
    payload: Dict[str, Any],
) -> Tuple[bool, str, List[StructuredItem], List[ValidationError], Dict[str, int]]:
    """
    Validate a top-level structured menu payload, e.g. JSON imports.

    Expected shape:
      {
        "items": [ { ... structured item ... }, ... ],
        // plus optional fields:
        "title": "...",
        "restaurant_id": 123,
        "source": "pos_vendor_name",
        ...
      }

    Returns:
      (ok, message, clean_items, errors, summary)
    """
    if not isinstance(payload, dict):
        return False, "payload must be an object", [], [], {}

    if "items" not in payload:
        return False, "missing 'items' array", [], [], {}

    items = payload.get("items")
    if not isinstance(items, list):
        return False, "'items' must be a list", [], [], {}

    clean, item_errors, summary = validate_structured_items(items)

    if item_errors:
        return False, "one or more items failed validation", clean, item_errors, summary

    return True, "", clean, [], summary
