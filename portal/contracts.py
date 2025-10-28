# portal/contracts.py
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

DraftItemKeys = {"id", "name", "description", "price_cents", "category", "position", "meta"}

def _is_intlike(x: Any) -> bool:
    try:
        int(x); return True
    except Exception:
        return False

def validate_draft_payload(payload: Dict[str, Any]) -> Tuple[bool, str]:
    # Top-level keys we expect when exporting/saving
    required_top = {"draft_id", "items"}
    missing = [k for k in required_top if k not in payload]
    if missing:
        return False, f"missing top-level keys: {', '.join(missing)}"

    # draft_id
    if not _is_intlike(payload["draft_id"]):
        return False, "draft_id must be an integer"

    # items
    items = payload.get("items")
    if not isinstance(items, list):
        return False, "items must be a list"

    for i, it in enumerate(items):
        if not isinstance(it, dict):
            return False, f"items[{i}] must be an object"
        # required fields for each item
        name = it.get("name", "")
        if not isinstance(name, str):
            return False, f"items[{i}].name must be a string"
        price_cents = it.get("price_cents", 0)
        if not _is_intlike(price_cents):
            return False, f"items[{i}].price_cents must be an integer"
        category = it.get("category", "")
        if not isinstance(category, str):
            return False, f"items[{i}].category must be a string"
        # allow optional keys but ensure types are sane
        if "description" in it and not isinstance(it["description"], str):
            return False, f"items[{i}].description must be a string"
        if "position" in it and it["position"] is not None and not _is_intlike(it["position"]):
            return False, f"items[{i}].position must be an integer or null"
        if "meta" in it and not isinstance(it["meta"], dict):
            return False, f"items[{i}].meta must be an object"

    return True, ""
