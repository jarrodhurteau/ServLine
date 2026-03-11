# portal/contracts.py
from __future__ import annotations
from typing import Any, Dict, Tuple

# (Optional) reference set used by the editor/clients; not enforced strictly.
DraftItemKeys = {"id", "name", "description", "price_cents", "category", "position", "meta", "confidence"}

VALID_VARIANT_KINDS = {"size", "combo", "flavor", "style", "other"}

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

        # kitchen_name (optional string or None)
        if "kitchen_name" in it and it["kitchen_name"] is not None:
            if not isinstance(it["kitchen_name"], str):
                return False, f"items[{i}].kitchen_name must be a string or null"

        # _modifier_groups (optional list of modifier group dicts)
        if "_modifier_groups" in it:
            groups = it["_modifier_groups"]
            if not isinstance(groups, list):
                return False, f"items[{i}]._modifier_groups must be a list"
            for gi, g in enumerate(groups):
                if not isinstance(g, dict):
                    return False, f"items[{i}]._modifier_groups[{gi}] must be an object"
                # name (required string)
                if "name" not in g or not isinstance(g.get("name"), str):
                    return False, f"items[{i}]._modifier_groups[{gi}].name must be a string"
                if not g["name"].strip():
                    return False, f"items[{i}]._modifier_groups[{gi}].name must not be empty"
                # required (optional int-like or None)
                if "required" in g and g["required"] is not None:
                    if not _is_intlike(g["required"]):
                        return False, f"items[{i}]._modifier_groups[{gi}].required must be an integer"
                # min_select (optional int-like or None)
                if "min_select" in g and g["min_select"] is not None:
                    if not _is_intlike(g["min_select"]):
                        return False, f"items[{i}]._modifier_groups[{gi}].min_select must be an integer"
                # max_select (optional int-like or None)
                if "max_select" in g and g["max_select"] is not None:
                    if not _is_intlike(g["max_select"]):
                        return False, f"items[{i}]._modifier_groups[{gi}].max_select must be an integer"
                # position (optional int-like or None)
                if "position" in g and g["position"] is not None:
                    if not _is_intlike(g["position"]):
                        return False, f"items[{i}]._modifier_groups[{gi}].position must be an integer"
                # _modifiers (optional list)
                if "_modifiers" in g:
                    modifiers = g["_modifiers"]
                    if not isinstance(modifiers, list):
                        return False, f"items[{i}]._modifier_groups[{gi}]._modifiers must be a list"
                    for mi, m in enumerate(modifiers):
                        if not isinstance(m, dict):
                            return False, f"items[{i}]._modifier_groups[{gi}]._modifiers[{mi}] must be an object"
                        # label (required string)
                        if "label" not in m or not isinstance(m.get("label"), str):
                            return False, f"items[{i}]._modifier_groups[{gi}]._modifiers[{mi}].label must be a string"
                        if not m["label"].strip():
                            return False, f"items[{i}]._modifier_groups[{gi}]._modifiers[{mi}].label must not be empty"
                        # price_cents (optional int-like or None)
                        if "price_cents" in m and m["price_cents"] is not None:
                            if not _is_intlike(m["price_cents"]):
                                return False, f"items[{i}]._modifier_groups[{gi}]._modifiers[{mi}].price_cents must be an integer"

        # _variants (optional list of variant dicts)
        if "_variants" in it:
            variants = it["_variants"]
            if not isinstance(variants, list):
                return False, f"items[{i}]._variants must be a list"
            for vi, v in enumerate(variants):
                if not isinstance(v, dict):
                    return False, f"items[{i}]._variants[{vi}] must be an object"
                # id (optional int-like or null)
                if "id" in v and v["id"] is not None and not _is_intlike(v["id"]):
                    return False, f"items[{i}]._variants[{vi}].id must be an integer or null"
                # label (required string)
                if "label" not in v or not isinstance(v.get("label"), str):
                    return False, f"items[{i}]._variants[{vi}].label must be a string"
                if not v["label"].strip():
                    return False, f"items[{i}]._variants[{vi}].label must not be empty"
                # price_cents (required int-like)
                if "price_cents" in v:
                    if not _is_intlike(v["price_cents"]):
                        return False, f"items[{i}]._variants[{vi}].price_cents must be an integer"
                # kind (optional, must be valid)
                if "kind" in v and v["kind"] is not None:
                    if not isinstance(v["kind"], str) or v["kind"] not in VALID_VARIANT_KINDS:
                        return False, f"items[{i}]._variants[{vi}].kind must be one of {sorted(VALID_VARIANT_KINDS)}"
                # position (optional int-like)
                if "position" in v and v["position"] is not None:
                    if not _is_intlike(v["position"]):
                        return False, f"items[{i}]._variants[{vi}].position must be an integer"

    # --- Top-level deleted_variant_ids (optional) ---
    if "deleted_variant_ids" in payload:
        dvids = payload["deleted_variant_ids"]
        if not isinstance(dvids, list):
            return False, "deleted_variant_ids must be a list"
        for dvi, dvid in enumerate(dvids):
            if not _is_intlike(dvid):
                return False, f"deleted_variant_ids[{dvi}] must be an integer"

    # --- Top-level deleted_modifier_group_ids (optional) ---
    if "deleted_modifier_group_ids" in payload:
        dmgids = payload["deleted_modifier_group_ids"]
        if not isinstance(dmgids, list):
            return False, "deleted_modifier_group_ids must be a list"
        for dmi, dmid in enumerate(dmgids):
            if not _is_intlike(dmid):
                return False, f"deleted_modifier_group_ids[{dmi}] must be an integer"

    return True, ""
