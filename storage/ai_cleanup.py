# servline/storage/ai_cleanup.py
from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
import re
import unicodedata

from .drafts import get_draft_items, upsert_draft_items
from . import drafts as _drafts_mod
from portal.storage import category_infer as _cat_infer  # Phase-3 category engine

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


# ---------- Name cleanup (unchanged ultra-safe) ----------
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


# ---------- Price extraction ----------
_PRICE_RX = re.compile(
    r"""
    (?<!\d)
    (?:\$?\s*)
    (?:
        (?P<dollars>\d{1,3})(?:\.(?P<cents>\d{1,2}))?
        |
        (?P<compact>\d{3,4})
        |
        \.(?P<dotonly>\d{2})
    )
    (?!\d)
    """,
    re.X,
)

def _to_cents(dollars, cents, compact, dotonly):
    try:
        if dotonly:
            return int(dotonly)
        if compact:
            if len(compact) in (3, 4):
                return int(compact)
            return None
        if dollars is not None:
            d = int(dollars)
            c = int((cents or "0").ljust(2, "0")[:2])
            return d * 100 + c
    except Exception:
        return None
    return None

def extract_price_candidates(text: str) -> list[int]:
    hits = []
    for m in _PRICE_RX.finditer(text or ""):
        cents = _to_cents(m.group("dollars"), m.group("cents"), m.group("compact"), m.group("dotonly"))
        if cents is not None:
            hits.append(int(cents))
    return hits

def _clamp_price(cents: Optional[int]) -> Optional[int]:
    if cents is None:
        return None
    if cents < _drafts_mod.ocr_utils.PRICE_MIN or cents > _drafts_mod.ocr_utils.PRICE_MAX:
        return None
    return int(cents)

def _pick_price(name: str, desc: Optional[str]) -> Optional[int]:
    text = f"{name} {(desc or '')}".strip()
    hits = extract_price_candidates(text)
    for c in reversed(hits):
        ok = _clamp_price(int(c))
        if ok is not None:
            return ok
    return None


# ---------- Category inference ----------
_BUCKETS = {
    "Pizza":      ["pizza", "margherita", "calzone", "stromboli", "slice", "pie"],
    "Wings":      ["wing", "buffalo", "boneless"],
    "Burgers":    ["burger", "cheeseburger"],
    "Sandwiches": ["sandwich", "sub", "hoagie", "panini", "wrap", "gyro", "philly"],
    "Pasta":      ["pasta", "spaghetti", "alfredo", "ziti", "lasagna", "ravioli", "penne"],
    "Salads":     ["salad", "caesar", "greek", "garden"],
    "Sides":      ["fries", "rings", "sticks", "garlic knots", "coleslaw", "side"],
    "Beverages":  ["soda", "pop", "pepsi", "coke", "tea", "lemonade", "coffee", "water"],
    "Desserts":   ["tiramisu", "cannoli", "brownie", "cheesecake", "cookie", "ice cream"],
}

def classify_category(name: str, description: str | None = None) -> str:
    text = f"{name} {(description or '')}".lower()
    for cat, keys in _BUCKETS.items():
        if any(k in text for k in keys):
            return cat
    return "Uncategorized"

def infer_item_category(name: str, description: str | None = None):
    merged = f"{name or ''} {(description or '' )}".strip()
    if not merged:
        return "Uncategorized", None, None

    try:
        guess = _cat_infer.infer_category_for_text(
            name=merged,
            description=None,
            price_cents=0,
            neighbor_categories=[],
            fallback="Uncategorized",
        )
        cat = guess.category
        conf = int(guess.confidence)
        trace = guess.reason or "heuristic match"
    except Exception:
        cat, conf, trace = None, None, None

    if not cat or cat == "Uncategorized":
        cat = classify_category(name, description)
    return cat, conf, trace


# ---------- Confidence blending ----------
def normalize_confidence(ocr_score: int | None, ai_score: int | None) -> int:
    if ocr_score is None and ai_score is None:
        return 50
    if ocr_score is None:
        return max(0, min(100, int(round(ai_score or 0))))
    if ai_score is None:
        return max(0, min(100, int(round(ocr_score or 0))))
    blended = 0.4 * (ocr_score or 0) + 0.6 * (ai_score or 0)
    return max(0, min(100, int(round(blended))))


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


# ---------- Public entrypoint ----------
def apply_ai_cleanup(draft_id: int) -> int:
    items = get_draft_items(int(draft_id))
    if not items:
        return 0

    updated = []
    for it in items:
        name_raw = (it.get("name") or "").strip()
        desc_raw = (it.get("description") or "").strip()

        # Clean name
        name_clean = clean_item_name(name_raw)

        # Soft-clean description
        desc_clean, salvage_ratio = clean_description_soft(desc_raw)
        desc_final = _decide_description(desc_raw, desc_clean, salvage_ratio)

        # Price fix
        price_cents = int(it.get("price_cents") or 0)
        if price_cents <= 0:
            found = _pick_price(name_raw, desc_raw)
            if found is not None:
                price_cents = int(found)

        # Category
        existing_cat = (it.get("category") or "").strip() or None
        if not existing_cat or existing_cat == "Uncategorized":
            cat, cat_conf, cat_trace = infer_item_category(name_clean, desc_clean)
        else:
            cat = existing_cat
            cat_conf, cat_trace = None, None

        # Confidence
        ocr_conf = it.get("confidence")
        changed = (name_clean != name_raw) or (desc_clean != desc_raw)
        ai_signal = None

        if changed:
            ai_signal = 80  # softer default
            if price_cents <= 0:
                ai_signal -= 10
            if len(name_clean) > 60:
                ai_signal -= 5

        norm_conf = normalize_confidence(
            int(ocr_conf) if isinstance(ocr_conf, int) or (isinstance(ocr_conf, str) and str(ocr_conf).isdigit()) else None,
            ai_signal,
        )

        updated.append({
            "id": it["id"],
            "name": name_clean or name_raw,
            "description": desc_final,
            "price_cents": price_cents,
            "category": cat,
            "position": it.get("position"),
            "confidence": norm_conf,
        })

    res = upsert_draft_items(int(draft_id), updated)
    return len(res.get("updated_ids", [])) + len(res.get("inserted_ids", []))
