# servline/storage/ai_cleanup.py
"""
AI Cleanup for Draft Items — Day 33 (Text-only Safe Mode + Long-Name Rescue)

Responsibilities (current scope):
- Normalize item names & descriptions (soft cleanup; preserve as much as possible).
- Rescue overly-long names by moving trailing detail into the description when appropriate.
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

# Long-name heuristics
_LONG_NAME_CHAR_THRESHOLD = 80
_LONG_NAME_WORD_THRESHOLD = 12

# Short-token whitelist for descriptions (allowed to survive token-soup cleanup)
_DESC_SHORT_WHITELIST = {
    "bbq",
    "blt",
    "ny",
    "nyc",
    "sm",
    "lg",
    "xl",
    "oz",
    "jr",
    "md",
    "w/",
    "w",
    "&",
    "and",
    "or",
}


# Helpers
def _normalize_spaces(s: str) -> str:
    return _WS_RX.sub(" ", s or "").strip()


def _unicode_norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def _collapse_runs(s: str) -> str:
    # Collapse excessive repeated characters ("Soooo" -> "Soo")
    return re.sub(r"(.)\1{2,}", r"\1\1", s)


def _cleanup_punct(s: str) -> str:
    t = s
    t = _DOT_LEADERS_RX.sub(" ", t)          # "Garlic Knots .... 5.99"
    t = _MULTI_PUNCT_RX.sub(" ", t)          # Weird symbol clusters
    t = _HARD_JUNK_RX.sub(" ", t)            # Hard pipes, etc.
    t = _NONALNUM_BURST_RX.sub("", t)        # Non-alnum bursts between word chars
    return _normalize_spaces(t)


def smart_title(s: str) -> str:
    if not s:
        return s
    out: List[str] = []
    for tok in s.split(" "):
        if len(tok) <= 2 or tok.isupper():
            # Keep acronyms / short tokens as-is (BBQ, NY, XL, LG, etc.)
            out.append(tok)
        else:
            out.append(tok[:1].upper() + tok[1:].lower())
    return " ".join(out)


# ---------- Ingredient / description smoothing ----------

def _strip_token_soup(t: str) -> str:
    """
    Remove obvious 'token soup' from ingredient-style descriptions.

    Examples we want to kill:
        "&, A, Eb, Ss, \\, ]"  -> drop the stray single letters and junk.
    But we keep important short tokens like: BBQ, BLT, NY, LG, SM, XL, OZ, etc.
    """
    if not t:
        return t

    tokens = t.split()
    kept: List[str] = []

    for tok in tokens:
        # Strip trailing punctuation for decision, but keep original if kept
        core = tok.strip(",.;:/!?)(").strip()
        if not core:
            continue

        lower = core.lower()

        # Keep whitelisted short tokens
        if lower in _DESC_SHORT_WHITELIST:
            kept.append(tok)
            continue

        # Keep digits (sizes like "10", "12", etc.)
        if core.isdigit():
            kept.append(tok)
            continue

        # If it's very short and not whitelisted, it's probably junk ("A", "Eb", "Ss")
        if len(core) <= 2:
            continue

        # Otherwise, keep normal words
        kept.append(tok)

    return " ".join(kept)


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

    # Token-soup cleanup v2
    t = _strip_token_soup(t)

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


# ---------- Long-name rescue (Pt.3) ----------

def _looks_like_long_name(name: str) -> bool:
    if not name:
        return False
    if len(name) >= _LONG_NAME_CHAR_THRESHOLD:
        return True
    if len(name.split()) >= _LONG_NAME_WORD_THRESHOLD:
        return True
    return False


def _rescue_long_name(name: str, existing_desc: str) -> Tuple[str, str]:
    """
    If the name is clearly too long (multi-item / paragraph-like), attempt to split
    into (shorter_name, tail_for_description).

    Rules:
    - Only attempt when the name is long AND the existing description is empty or tiny.
    - Prefer explicit separators: " - ", " — ", " – ", ":", bullets, etc.
    - Fallback: split on comma.
    - Final fallback: keep the first ~6–8 tokens as name and push the rest into description.
    """
    if not name:
        return "", ""
    # If we already have a decent description, don't move text out of the name.
    if existing_desc and len(existing_desc.strip()) >= 10:
        return name, ""

    if not _looks_like_long_name(name):
        return name, ""

    # 1) Explicit separators
    separaters = [" - ", " — ", " – ", ":", " • ", " · "]
    for sep in separaters:
        idx = name.find(sep)
        if idx <= 0:
            continue
        head = name[:idx].strip(" -–—:·•")
        tail = name[idx + len(sep) :].strip()
        if len(head.split()) >= 2 and len(tail.split()) >= 3:
            return head, tail

    # 2) Comma-based split
    idx = name.find(", ")
    if idx > 10 and idx < len(name) - 10:
        head = name[:idx].strip(", ")
        tail = name[idx + 2 :].strip()
        if len(head.split()) >= 2 and len(tail.split()) >= 3:
            return head, tail

    # 3) Token-based split (fallback for double-sandwich style lines)
    tokens = name.split()
    if len(tokens) >= 10:
        # Keep the first chunk as the "true" name, push the rest into description.
        head_tokens = tokens[: min(8, len(tokens) - 3)]
        tail_tokens = tokens[len(head_tokens) :]
        head = " ".join(head_tokens)
        tail = " ".join(tail_tokens)
        return head, tail

    # No good split found
    return name, ""


# ---------- Description cleanup (SOFT MODE, v2) ----------

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
    t = _smooth_ingredients(t)  # now includes token-soup filtering v2

    cleaned_tokens = t.split()
    # Count how many cleaned tokens still appear in the raw description
    source_lower = s.lower()
    kept = sum(1 for tok in cleaned_tokens if tok.lower() in source_lower)
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


# ---------- Core normalizer (Day 33 text-only mode) ----------

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

        # Step 1: clean the raw name
        name_clean_initial = clean_item_name(name_raw)

        # Step 2 (Pt.3): rescue overly-long names:
        #    - shorten the display name
        #    - push the tail into the description when appropriate
        rescued_name, name_tail = _rescue_long_name(name_clean_initial, desc_raw)

        # Step 3: compose the "raw" description we will clean:
        #    original description + any rescued tail from the name
        combined_desc_raw = " ".join(
            x for x in (desc_raw, name_tail) if x
        ).strip()

        # Step 4: soft-clean description text (token-soup + punctuation)
        desc_clean, salvage_ratio = clean_description_soft(combined_desc_raw)
        desc_final = _decide_description(combined_desc_raw, desc_clean, salvage_ratio)

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
                "name": rescued_name or name_raw,
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
