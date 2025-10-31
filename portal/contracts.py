# portal/contracts.py
from __future__ import annotations
from typing import Any, Dict, Tuple

# (Optional) reference set used by the editor/clients; not enforced strictly.
DraftItemKeys = {"id", "name", "description", "price_cents", "category", "position", "meta", "confidence"}

def _is_intlike(x: Any) -> bool:
    try:
        int(x)
        return True
    except Exception:
        return False

def validate_draft_payload(payload: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Contract validator for Draft Editor save/export payloads.

    Expected top-level:
      - draft_id: int
      - items: list[DraftItem]
    Extra top-level fields are tolerated (title, restaurant_id, status, etc.).

    DraftItem rules (per item):
      - name: str (may be empty, but must be a string)
      - price_cents: int-like (defaults to 0 if omitted by clients)
      - category: str OR None (null allowed)
      - description: optional str (if present)
      - position: optional int-like or None
      - meta: optional dict
      - confidence: optional int-like (0–100) or None
      - id: optional int-like (for updates)
    """
    # --- Top level ---
    required_top = {"draft_id", "items"}
    missing = [k for k in required_top if k not in payload]
    if missing:
        return False, f"missing top-level keys: {', '.join(missing)}"

    if not _is_intlike(payload["draft_id"]):
        return False, "draft_id must be an integer"

    items = payload.get("items")
    if not isinstance(items, list):
        return False, "items must be a list"

    # --- Per item ---
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            return False, f"items[{i}] must be an object"

        # id (optional)
        if "id" in it and it["id"] is not None and not _is_intlike(it["id"]):
            return False, f"items[{i}].id must be an integer or null"

        # name (required, string)
        if "name" not in it:
            return False, f"items[{i}].name is required"
        if not isinstance(it["name"], str):
            return False, f"items[{i}].name must be a string"

        # price_cents (required int-like; clients may omit → treated as 0)
        if "price_cents" in it:
            if not _is_intlike(it["price_cents"]):
                return False, f"items[{i}].price_cents must be an integer"
        # if missing, we'll coerce to 0 downstream; validator allows omission

        # category (string or None)
        if "category" in it and it["category"] is not None and not isinstance(it["category"], str):
            return False, f"items[{i}].category must be a string or null"

        # description (optional string)
        if "description" in it and it["description"] is not None and not isinstance(it["description"], str):
            return False, f"items[{i}].description must be a string or null"

        # position (optional int-like or None)
        if "position" in it and it["position"] is not None and not _is_intlike(it["position"]):
            return False, f"items[{i}].position must be an integer or null"

        # meta (optional dict)
        if "meta" in it and it["meta"] is not None and not isinstance(it["meta"], dict):
            return False, f"items[{i}].meta must be an object"

        # confidence (optional int-like 0–100 or None)
        if "confidence" in it and it["confidence"] is not None:
            if not _is_intlike(it["confidence"]):
                return False, f"items[{i}].confidence must be an integer or null"
            try:
                val = int(it["confidence"])
                if not (0 <= val <= 100):
                    return False, f"items[{i}].confidence must be between 0 and 100"
            except Exception:
                return False, f"items[{i}].confidence must be an integer or null"

    return True, ""
