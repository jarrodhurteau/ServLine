# servline/storage/ai_cleanup.py
"""
AI Cleanup for Draft Items — Day 32 (Text-only Safe Mode)

Responsibilities (current scope):
- Normalize item names & descriptions (soft cleanup; preserve as much as possible).
- Leave prices, categories, and OCR metadata exactly as produced by the OCR pipeline.

Notes:
- Earlier versions tried to:
    * recover prices from text
    * infer/override categories via category_infer
    * blend OCR + AI confidence
  That logic is now considered legacy and has been removed for safety.

- From Day 32 onward, this module is a *text surgeon only*:
    * It may change `name` and `description`.
    * It must NOT change `price_cents`, `category`, or any other structured fields.

The core cleanup logic lives in `normalize_draft_items(items)` so the same
normalization is used for:
    * DB-side cleanup (apply_ai_cleanup)
    * In-memory structured export (Finalized menu JSON, Superimport prep).
"""

from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
import re
import unicodedata

from .drafts import get_draft_items, upsert_draft_items

TAG = "[AI Cleaned]"

# ---------- Text cleaning regex ----------
_WS_RX = re.compile(r"\s+")
_DOT_LEADERS_RX = re.compile(r"\.{2,}\s*")
_TRAIL_PUNCT_RX = re.compile(r"[^\w)\]]+$")
_MULTI_PUNCT_RX = re.compile(r"[^\w\s$.,&()/+'-]{2,}")
_HARD_JUNK_RX = re.compile(r"[|]{2,}")
_NONALNUM_BURST_RX = re.compile(r"(?<=\w)[^\w\s]{1,}(?=\w)")


# Helpers
def _normalize_spaces(s: str) -> str:
    return _WS_RX.sub(" ", s or "").strip()


def _unicode_norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def _collapse_runs(s: str) -> str:
    return re.sub(r"(.)\1{2,}", r"\1\1", s)


def _cleanup_punct(s: str) -> str:
    t = s
    t = _DOT_LEADERS_RX.sub(" ", t)
    t = _MULTI_PUNCT_RX.sub(" ", t)
    t = _HARD_JUNK_RX.sub(" ", t)
    t = _NONALNUM_BURST_RX.sub("", t)
    return _normalize_spaces(t)


def smart_title(s: str) -> str:
    if not s:
        return s
    out = []
    for tok in s.split(" "):
        if len(tok) <= 2 or tok.isupper():
            out.append(tok)
        else:
            out.append(tok[:1].upper() + tok[1:].lower())
    return " ".join(out)


# ---------- Ingredient smoothing ----------
def _smooth_ingredients(desc: str) -> str:
    t = desc or ""
    if not t:
        return t

    # Normalize comma spacing
    t = re.sub(r"\s*,\s*", ", ", t)
    t = re.sub(r",\s*,+", ", ", t)

    t = _normalize_spaces(t)

    # Trim dangling connectors
    lower = t.lower()
    for conn in (" with", " and", " or", " on", " in"):
        if lower.endswith(conn):
            t = t[: -len(conn)].rstrip()
            lower = t.lower()
            break

    # Trim trailing single-letter junk (except sizes)
    parts = t.split()
    while parts:
        last = parts[-1]
        if last.lower() in {"oz", "xl", "lg", "sm"}:
            break
        if len(last) == 1 and last.isalpha():
            parts.pop()
        else:
            break
    t = " ".join(parts)

    return t


# ---------- Name cleanup (ultra-safe) ----------
def clean_item_name(s: str) -> str:
    if not s:
        return ""
    t = _unicode_norm(s)
    t = _collapse_runs(t)
    t = _cleanup_punct(t)
    t = _TRAIL_PUNCT_RX.sub("", t).strip()
    t = smart_title(t)
    return t


# ---------- Description cleanup (SOFT MODE) ----------
def clean_description_soft(s: str) -> Tuple[str, float]:
    """
    Return (cleaned_description, salvage_ratio)

    salvage_ratio = portion of tokens preserved.
    Used to decide whether to prefix [AI Cleaned] or not.
    """
    if not s:
        return "", 0.0

    raw_tokens = s.split()
    raw_len = max(len(raw_tokens), 1)

    t = _unicode_norm(s)
    t = _collapse_runs(t)
    t = _cleanup_punct(t)
    t = _normalize_spaces(t)
    t = _smooth_ingredients(t)

    cleaned_tokens = t.split()
    kept = sum(1 for tok in cleaned_tokens if tok.lower() in s.lower())
    salvage_ratio = kept / raw_len

    return t, salvage_ratio


# ---------- Description tag decision logic ----------
def _decide_description(desc_raw: str, desc_clean: str, salvage_ratio: float) -> str:
    """
    Decide how to tag:
    - If salvage_ratio < 0.2 and desc_clean is very short => replace fully with TAG.
    - If salvage_ratio between 0.2 and 0.5 => prefix TAG to cleaned text.
    - If salvage_ratio >= 0.5 => keep cleaned text WITHOUT TAG.
    """
    if not desc_raw.strip() and not desc_clean.strip():
        return ""

    # Hard trash: very few useful tokens survived
    if salvage_ratio < 0.2 and len(desc_clean.split()) <= 3:
        return TAG

    # Medium salvage: note AI involvement
    if salvage_ratio < 0.5:
        if not desc_clean.startswith(TAG):
            return f"{TAG} {desc_clean}".strip()
        return desc_clean

    # Good salvage: keep cleaned text as-is, no tagging
    return desc_clean


# ---------- Core normalizer (Day 32 text-only mode) ----------
def normalize_draft_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Pure function: take raw draft items and return normalized/cleaned items.

    IMPORTANT:
      - Only `name` and `description` are modified.
      - `price_cents`, `category`, `position`, and `confidence` are preserved
        exactly as they came from the OCR/draft pipeline.

    Used by:
      - apply_ai_cleanup(draft_id) for DB writes
      - Finalize/export paths to ensure the exported structured JSON matches
        what we would store in the draft.
    """
    if not items:
        return []

    updated: List[Dict[str, Any]] = []

    for it in items:
        name_raw = (it.get("name") or "").strip()
        desc_raw = (it.get("description") or "").strip()

        # Clean name
        name_clean = clean_item_name(name_raw)

        # Soft-clean description
        desc_clean, salvage_ratio = clean_description_soft(desc_raw)
        desc_final = _decide_description(desc_raw, desc_clean, salvage_ratio)

        # Preserve structured fields exactly
        price_cents = it.get("price_cents")
        category = it.get("category")
        position = it.get("position")
        confidence = it.get("confidence")

        item_id = it.get("id")
        if item_id is None:
            # For safety: skip rows without a primary key when doing DB-backed cleanup.
            # (Export paths that want to normalize anonymous items can ignore this and
            #  call the text cleaners directly.)
            continue

        updated.append(
            {
                "id": item_id,
                "name": name_clean or name_raw,
                "description": desc_final,
                "price_cents": price_cents,
                "category": category,
                "position": position,
                "confidence": confidence,
            }
        )

    return updated



# ---------- Public entrypoint ----------
def apply_ai_cleanup(draft_id: int) -> int:
    """
    DB-backed cleanup: load items for a draft, normalize them, and persist.

    Returns:
        Number of rows upserted (updated + inserted).

    Behavior:
        - Only `name` and `description` may change.
        - All other fields (price_cents, category, position, confidence, etc.)
          are passed through unchanged.
    """
    items = get_draft_items(int(draft_id))
    if not items:
        return 0

    updated = normalize_draft_items(items)
    res = upsert_draft_items(int(draft_id), updated)
    return len(res.get("updated_ids", [])) + len(res.get("inserted_ids", []))
