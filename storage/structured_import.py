# storage/structured_import.py
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class StructuredItem:
    """
    Canonical, POS-friendly structured menu item.

    This is deliberately minimal. It maps 1:1 to what we will insert into
    draft_items, without any AI cleanup or inference.
    """
    name: str
    description: str = ""
    category: Optional[str] = None
    subcategory: Optional[str] = None
    price_cents: Optional[int] = None
    sku: Optional[str] = None
    pos_code: Optional[str] = None
    size_name: Optional[str] = None
    tags: Optional[List[str]] = None
    source_row: Optional[int] = None
    source_type: str = "structured_csv"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description or "",
            "category": self.category,
            "subcategory": self.subcategory,
            "price_cents": self.price_cents,
            "sku": self.sku,
            "pos_code": self.pos_code,
            "size_name": self.size_name,
            "tags": self.tags or [],
            "source_row": self.source_row,
            "source_type": self.source_type,
        }


class StructuredImportError(Exception):
    """Fatal structured import error (bad file, missing required headers, etc.)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CANONICAL_HEADERS = {
    "name": {"name"},
    "description": {"description", "desc"},
    "category": {"category", "cat"},
    "subcategory": {"subcategory", "sub_category", "subcat"},
    "price_cents": {"price_cents", "price_in_cents"},
    "price": {"price", "base_price", "unit_price", "amount"},
    "sku": {"sku", "item_sku", "item_code"},
    "pos_code": {"pos_code", "button_code", "plu"},
    "size_name": {"size", "portion", "size_label"},
    "tags": {"tags", "labels"},
}


def _normalize_header(header: str) -> str:
    h = (header or "").strip().lower()
    for canonical, aliases in _CANONICAL_HEADERS.items():
        if h in aliases:
            return canonical
    return h  # unknown headers are passed through unchanged


def _parse_price_to_cents(raw: Any) -> Optional[int]:
    """
    Convert a raw price value (string/float/int) to integer cents.
    Returns None if the value is empty or explicitly "mp" / "market".
    Raises StructuredImportError if the value is non-empty but invalid.
    """
    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        # Assume dollars if float/int
        cents = int(round(float(raw) * 100))
        return cents if cents >= 0 else None

    s = str(raw).strip()
    if not s:
        return None

    lowered = s.lower()
    if lowered in {"mp", "market", "market price"}:
        return None

    # Strip leading currency symbol
    if s[0] in "$€£":
        s = s[1:].strip()

    try:
        value = Decimal(s)
    except InvalidOperation:
        raise StructuredImportError(f"Invalid price value: {raw!r}")

    cents = int((value * 100).to_integral_value())
    return cents if cents >= 0 else None


def _split_tags(raw: Any) -> List[str]:
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    # "veg, gluten free, spicy" -> ["veg", "gluten free", "spicy"]
    return [part.strip() for part in s.split(",") if part.strip()]


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def parse_csv_menu(text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Parse a CSV menu file into a list of canonical StructuredItems (as dicts)
    and a list of non-fatal warnings.

    Raises StructuredImportError for fatal problems (e.g., missing 'name'
    column, invalid price formats).
    """
    if not text.strip():
        raise StructuredImportError("CSV file appears to be empty.")

    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        raise StructuredImportError("CSV file has no header row.")

    # Map headers to canonical names
    raw_headers = reader.fieldnames
    header_map: Dict[str, str] = {}
    for h in raw_headers:
        canonical = _normalize_header(h)
        header_map[h] = canonical

    canonical_values = set(header_map.values())
    if "name" not in canonical_values:
        raise StructuredImportError(
            "CSV must include a 'name' column (or alias like 'Name')."
        )

    warnings: List[str] = []
    if "price" not in canonical_values and "price_cents" not in canonical_values:
        warnings.append(
            "No price or price_cents column found; items will be imported without base prices."
        )

    items: List[Dict[str, Any]] = []
    row_index = 1  # 1-based including header? We'll use data row index starting at 2.
    for row in reader:
        row_index += 1
        # Remap row keys to canonical
        raw_item: Dict[str, Any] = {}
        for original_key, value in row.items():
            canonical_key = header_map.get(original_key, original_key)
            raw_item[canonical_key] = value

        name = (raw_item.get("name") or "").strip()
        if not name:
            # Fatal per-row error
            raise StructuredImportError(f"Row {row_index}: missing required 'name'.")

        description = (raw_item.get("description") or "").strip()
        category = (raw_item.get("category") or "").strip() or None
        subcategory = (raw_item.get("subcategory") or "").strip() or None

        price_cents: Optional[int] = None
        if "price_cents" in raw_item and raw_item.get("price_cents") not in (None, ""):
            try:
                price_cents = _parse_price_to_cents(raw_item["price_cents"])
            except StructuredImportError as e:
                raise StructuredImportError(f"Row {row_index}: {e}") from e
        elif "price" in raw_item and raw_item.get("price") not in (None, ""):
            try:
                price_cents = _parse_price_to_cents(raw_item["price"])
            except StructuredImportError as e:
                raise StructuredImportError(f"Row {row_index}: {e}") from e

        sku = (raw_item.get("sku") or "").strip() or None
        pos_code = (raw_item.get("pos_code") or "").strip() or None
        size_name = (raw_item.get("size_name") or "").strip() or None
        tags = _split_tags(raw_item.get("tags"))

        item = StructuredItem(
            name=name,
            description=description,
            category=category,
            subcategory=subcategory,
            price_cents=price_cents,
            sku=sku,
            pos_code=pos_code,
            size_name=size_name,
            tags=tags,
            source_row=row_index,
            source_type="structured_csv",
        )
        items.append(item.to_dict())

    return items, warnings


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def parse_json_menu(data: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Parse a JSON menu payload into a list of canonical StructuredItems (as dicts)
    and a list of non-fatal warnings.

    Accepts either:
      - {"items": [...]} or
      - [...]
    """
    if data is None:
        raise StructuredImportError("JSON body is empty.")

    if isinstance(data, dict) and "items" in data:
        items_in = data["items"]
    else:
        items_in = data

    if not isinstance(items_in, list):
        raise StructuredImportError("JSON payload must be a list of items or an object with an 'items' array.")

    if not items_in:
        raise StructuredImportError("JSON 'items' array is empty.")

    warnings: List[str] = []
    parsed: List[Dict[str, Any]] = []

    for idx, raw in enumerate(items_in, start=1):
        if not isinstance(raw, dict):
            raise StructuredImportError(f"Item {idx} is not an object.")

        name = (str(raw.get("name", "")).strip())
        if not name:
            raise StructuredImportError(f"Item {idx}: missing required 'name'.")

        description = str(raw.get("description", "") or "").strip()
        category = (str(raw.get("category", "") or "").strip()) or None
        subcategory = (str(raw.get("subcategory", "") or "").strip()) or None

        price_cents: Optional[int] = None
        if "price_cents" in raw:
            try:
                price_cents = _parse_price_to_cents(raw["price_cents"])
            except StructuredImportError as e:
                raise StructuredImportError(f"Item {idx}: {e}") from e
        elif "price" in raw:
            try:
                price_cents = _parse_price_to_cents(raw["price"])
            except StructuredImportError as e:
                raise StructuredImportError(f"Item {idx}: {e}") from e

        sku_raw = raw.get("sku")
        pos_raw = raw.get("pos_code")
        size_raw = raw.get("size_name")
        tags_raw = raw.get("tags")

        sku = str(sku_raw).strip() or None if sku_raw is not None else None
        pos_code = str(pos_raw).strip() or None if pos_raw is not None else None
        size_name = str(size_raw).strip() or None if size_raw is not None else None

        if isinstance(tags_raw, list):
            tags = [str(t).strip() for t in tags_raw if str(t).strip()]
        else:
            tags = _split_tags(tags_raw)

        item = StructuredItem(
            name=name,
            description=description,
            category=category,
            subcategory=subcategory,
            price_cents=price_cents,
            sku=sku,
            pos_code=pos_code,
            size_name=size_name,
            tags=tags,
            source_row=idx,
            source_type="structured_json",
        )
        parsed.append(item.to_dict())

    if all(it.get("price_cents") is None for it in parsed):
        warnings.append(
            "No prices found in JSON items; drafts will be created without base prices."
        )

    return parsed, warnings


# ---------------------------------------------------------------------------
# Unified entrypoint for Flask routes
# ---------------------------------------------------------------------------

def parse_upload(file_bytes: bytes, filename: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Route-friendly helper: decide parser based on filename extension.

    Returns (items, warnings). Raises StructuredImportError for fatal issues.
    """
    if not filename:
        raise StructuredImportError("File has no name; expected .csv or .json.")

    lower = filename.lower()
    if lower.endswith(".csv"):
        try:
            text = file_bytes.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            # Fallback with replacement chars but still import as best we can
            text = file_bytes.decode("utf-8-sig", errors="replace")
        return parse_csv_menu(text)

    if lower.endswith(".json"):
        try:
            text = file_bytes.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            text = file_bytes.decode("utf-8-sig", errors="replace")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise StructuredImportError(f"Invalid JSON: {e}") from e
        return parse_json_menu(data)

    raise StructuredImportError("Unsupported file type; only .csv and .json are accepted for structured import.")
