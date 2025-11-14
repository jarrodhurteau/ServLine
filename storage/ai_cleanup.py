# servline/storage/ai_cleanup.py
from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
import re
import unicodedata

from .drafts import get_draft_items, upsert_draft_items
from . import drafts as _drafts_mod  # for ocr_utils price clamp
from portal.storage import category_infer as _cat_infer  # Phase-3 category engine

TAG = "[AI Cleaned]"

# ---------- Text cleaning (ultra-safe) ----------
_WS_RX = re.compile(r"\s+")
_DOT_LEADERS_RX = re.compile(r"\.{2,}\s*")      # "Garlic Knots .... 5.99"
_TRAIL_PUNCT_RX = re.compile(r"[^\w)\]]+$")
_MULTI_PUNCT_RX = re.compile(r"[^\w\s$.,&()/+'-]{2,}")
_HARD_JUNK_RX = re.compile(r"[|]{2,}")          # vertical bars etc.
_NONALNUM_BURST_RX = re.compile(r"(?<=\w)[^\w\s]{1,}(?=\w)")  # junk glued inside words

# Tokenizer that keeps letter/number runs and separators (currently unused, but kept for future use)
_TOKEN_RX = re.compile(r"[A-Za-z]{1,3}|[A-Za-z]{4,}|[0-9]+|[^A-Za-z0-9]+")

# "A B C" style token pattern (currently unused, but kept for future use)
_DESPACER_RX = re.compile(r"^(?:[A-Za-z]\s){2,}[A-Za-z]$")

# OCR swaps (currently unused in cleaners – we’re in ultra-safe mode)
_OCR_FIXES = {
    " rn ": " m ",
    " ii ": " n ",
    " l ": " I ",
    " 1 ": " I ",
    " 0 ": " O ",
    "—": "-",
}

# Light menu vocab (used in description smoothing, not in names)
_VOCAB: tuple[str, ...] = tuple(sorted(set(map(str.lower, [
    "pizza", "pepperoni", "margherita", "calzone", "stromboli", "slice", "pie", "sicilian",
    "wing", "wings", "buffalo", "boneless",
    "burger", "cheeseburger", "patty", "bacon", "cheddar",
    "sandwich", "sub", "hoagie", "panini", "wrap", "gyro", "philly",
    "pasta", "spaghetti", "alfredo", "ziti", "lasagna", "ravioli", "penne",
    "salad", "caesar", "greek", "garden", "antipasto",
    "fries", "rings", "mozzarella", "sticks", "garlic", "knots", "coleslaw", "side",
    "soda", "pop", "pepsi", "coke", "cola", "tea", "lemonade", "coffee", "water",
    "dessert", "tiramisu", "cannoli", "brownie", "cheesecake", "cookie", "ice", "cream",
    "mushroom", "onion", "olive", "bacon", "sausage", "meatball", "ham", "chicken", "buffalo", "boneless",
    "parmesan", "parm", "mozzarella", "ricotta", "basil", "tomato", "marinara", "pesto", "bbq", "ranch",
    "small", "medium", "large", "xl", "xxl", "bottle", "can", "fountain",
]))))

# Tail tokens that often indicate the end of a menu item name
_MULTI_ITEM_TAILS = {
    "pizza", "pie", "calzone", "stromboli", "slice",
    "wings", "wing",
    "burger", "cheeseburger",
    "sandwich", "sub", "hoagie", "wrap", "panini", "gyro",
    "salad",
    "fries", "rings",
    "parm", "parmesan",
}


def _normalize_spaces(s: str) -> str:
    return _WS_RX.sub(" ", s or "").strip()


def _unicode_norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def _collapse_runs(s: str) -> str:
    # Collapse long "aaaaa" -> "aa"
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


# ---- (Fuzzy vocab helpers – used in descriptions only) ----
def _bigrams(w: str) -> set[str]:
    w = f"^{w.lower()}$"
    return {w[i:i + 2] for i in range(len(w) - 1)} if len(w) >= 2 else {w}


def _sim(a: str, b: str) -> float:
    A, B = _bigrams(a), _bigrams(b)
    inter = len(A & B)
    union = len(A | B) or 1
    return inter / union


def _maybe_correct_token(tok: str, *, threshold: float = 0.56) -> str:
    t = tok.lower()
    if len(t) < 4:
        return tok
    if t in _VOCAB:
        return tok
    best = None
    best_s = 0.0
    for v in _VOCAB:
        s = _sim(t, v)
        if s > best_s:
            best, best_s = v, s
    if best and best_s >= threshold:
        fixed = best
        if tok.istitle():
            fixed = best.title()
        elif tok.isupper():
            fixed = best.upper()
        return fixed
    return tok


def _correct_by_vocab(line: str) -> str:
    toks = line.split()
    return " ".join(_maybe_correct_token(t) for t in toks)


# ---------- Pt.6B: Smarter name shaping & ingredient smoothing ----------

def _reshape_multi_item_name(name: str) -> str:
    """
    Pt.6B: Make obviously combined titles more 'menu-like' without changing semantics.

    Example:
        "Meatball Parm Mamas Burger"
        -> "Meatball Parm / Mamas Burger"

    We DO NOT split database rows here, only insert a readable separator.

    Heuristic:
      - Look for 2+ 'tail' tokens (burger, parm, pizza, salad, etc.)
      - If found and name is reasonably long, we cut once:
          * seg1 = tokens up to and including the FIRST tail
          * seg2 = everything after that
    """
    if not name:
        return name

    tokens = name.split()
    if len(tokens) < 4 or len(name) < 20:
        return name

    # Find all indices where we see a 'tail' token
    tail_indices: List[int] = []
    for i, tok in enumerate(tokens):
        if tok.lower().strip("()") in _MULTI_ITEM_TAILS:
            tail_indices.append(i)

    # We only reshape when we see at least 2 'tail' clues.
    if len(tail_indices) < 2:
        return name

    first = tail_indices[0]

    seg1 = " ".join(tokens[: first + 1]).strip()
    seg2 = " ".join(tokens[first + 1 :]).strip()

    # Safety: both segments should be at least a few characters long
    if not seg1 or not seg2:
        return name
    if len(seg1) < 8 or len(seg2) < 8:
        return name

    return f"{seg1} / {seg2}"


def _smooth_ingredients(desc: str) -> str:
    """
    Pt.6B: Gentle ingredient string smoothing.
    - Normalize commas/spaces
    - Trim obvious dangling connectors (with / and / or / on / in)
    - Remove trailing 1-letter junk tokens at the very end
    - Apply very light vocab correction for common food words
    """
    t = desc or ""
    if not t:
        return t

    # Normalize comma spacing: "tomato,onion" -> "tomato, onion"
    t = re.sub(r"\s*,\s*", ", ", t)
    t = re.sub(r",\s*,+", ", ", t)

    # Collapse spaces again to be safe
    t = _normalize_spaces(t)

    # Drop obvious dangling connectors at the very end, e.g. "with", "and", "or", "on", "in"
    lower = t.lower()
    for conn in (" with", " and", " or", " on", " in"):
        if lower.endswith(conn):
            t = t[: -len(conn)].rstrip()
            lower = t.lower()
            break

    # Remove trailing 1-letter junk tokens (except typical size shorthand)
    parts = t.split()
    while parts:
        last = parts[-1]
        # Keep size-ish tokens (oz, xl, lg, sm etc.)
        if last.lower() in {"oz", "xl", "lg", "sm"}:
            break
        if len(last) == 1 and last.isalpha():
            parts.pop()
        else:
            break
    t = " ".join(parts)

    # Light vocab correction (ingredients only, not titles)
    if t:
        t = _correct_by_vocab(t)

    return t


# ---------- Core cleaners (ULTRA SAFE + Pt.6B shaping) ----------

def clean_item_name(s: str) -> str:
    """
    ULTRA SAFE + Pt.6B shaping:
    - Do NOT join tokens out of nowhere
    - Do NOT run fuzzy vocab fixes on names
    - Only:
      * Unicode-normalize
      * Collapse extreme repeated chars
      * Strip obviously junky punctuation
      * Normalize spaces
      * Title-case the result
      * Optionally reshape clearly 'merged' multi-item names with a safe separator
    """
    if not s:
        return ""
    t = _unicode_norm(s)
    t = _collapse_runs(t)
    t = _cleanup_punct(t)
    t = _TRAIL_PUNCT_RX.sub("", t).strip()
    t = smart_title(t)
    # Pt.6B: visually separate obvious multi-item names
    t = _reshape_multi_item_name(t)
    return t


def clean_description(s: str) -> str:
    """
    ULTRA SAFE + Pt.6B ingredient smoothing:
    - Preserve structure as much as possible
    - No wild joining / hallucination
    - Unicode-normalize, collapse crazy repeats, strip junk, normalize spaces
    - Then gently tidy ingredient-style strings (commas, dangling fillers, light vocab).
    """
    if not s:
        return ""
    t = _unicode_norm(s)
    t = _collapse_runs(t)
    t = _cleanup_punct(t)
    t = _normalize_spaces(t)
    t = _smooth_ingredients(t)
    return t


# ---------- Price helpers ----------
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


def _to_cents(dollars: str | None, cents: str | None, compact: str | None, dotonly: str | None) -> int | None:
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
    for c in reversed(hits):  # prefer rightmost
        ok = _clamp_price(int(c))
        if ok is not None:
            return ok
    return None


# ---------- Categorizer ----------
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
    """
    Legacy keyword-based classifier (kept as a fallback when the Phase-3
    category_infer helper can't decide).
    """
    text = f"{name} {(description or '')}".lower()
    for cat, keys in _BUCKETS.items():
        if any(k in text for k in keys):
            return cat
    return "Uncategorized"


def infer_item_category(name: str, description: str | None = None) -> Tuple[str, Optional[int], Optional[str]]:
    """
    Use Phase-3 category_infer.infer_category_for_text directly on a synthesized text,
    then fall back to the legacy buckets.

    Returns: (category, category_confidence, rule_trace)
    """
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
        # leave conf/trace as-is or None
    return cat, conf if cat else None, trace


# ---------- Confidence ----------
def normalize_confidence(ocr_score: int | None, ai_score: int | None) -> int:
    if ocr_score is None and ai_score is None:
        return 50
    if ocr_score is None:
        return max(0, min(100, int(round(ai_score or 0))))
    if ai_score is None:
        return max(0, min(100, int(round(ocr_score or 0))))
    blended = 0.4 * (ocr_score or 0) + 0.6 * (ai_score or 0)
    return max(0, min(100, int(round(blended))))


# ---------- Public entrypoint ----------
def _maybe_prefix_tag(desc: str | None) -> str:
    base = (desc or "").strip()
    if not base:
        return TAG
    if not base.startswith(TAG):
        return f"{TAG} {base}".strip()
    return base


def apply_ai_cleanup(draft_id: int) -> int:
    items = get_draft_items(int(draft_id))
    if not items:
        return 0

    updated: List[Dict[str, Any]] = []
    for it in items:
        name_raw = (it.get("name") or "").strip()
        desc_raw = (it.get("description") or "").strip()

        # ULTRA SAFE clean + Pt.6B shaping
        name_clean = clean_item_name(name_raw)
        desc_clean = clean_description(desc_raw)

        price_cents = int(it.get("price_cents") or 0)
        if price_cents <= 0:
            found = _pick_price(name_raw, desc_raw)
            if found is not None:
                price_cents = int(found)

        # Category: respect existing, otherwise infer via Phase-3 helper + fallback
        existing_cat = (it.get("category") or "").strip() or None
        if not existing_cat or existing_cat == "Uncategorized":
            cat, cat_conf, cat_trace = infer_item_category(name_clean, desc_clean)
        else:
            cat = existing_cat
            cat_conf, cat_trace = None, None  # reserved for future use

        ocr_conf = it.get("confidence")

        # Pt.6B: slightly smarter AI signal using structural hints
        changed = (name_clean != name_raw) or (desc_clean != desc_raw)
        if changed:
            # Baseline 'good' AI signal
            ai_signal = 75

            # If we have both price + non-Uncategorized category and name isn't crazy-long,
            # nudge confidence a bit upward (structure looks strong).
            if price_cents > 0 and cat and cat != "Uncategorized" and len(name_clean) <= 60:
                ai_signal = 82

            # If the name is still very long or we have no price at all, be a bit more cautious.
            if len(name_clean) > 70 or price_cents <= 0:
                ai_signal = min(ai_signal, 70)
        else:
            ai_signal = None

        norm_conf = normalize_confidence(
            int(ocr_conf) if isinstance(ocr_conf, int) or (isinstance(ocr_conf, str) and str(ocr_conf).isdigit()) else None,
            ai_signal,
        )

        updated.append({
            "id": it["id"],
            "name": name_clean or name_raw,
            "description": _maybe_prefix_tag(desc_clean or desc_raw),
            "price_cents": price_cents,
            "category": cat,
            "position": it.get("position"),
            "confidence": norm_conf,
        })

    res = upsert_draft_items(int(draft_id), updated)
    return len(res.get("updated_ids", [])) + len(res.get("inserted_ids", []))
