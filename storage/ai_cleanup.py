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

# Light menu vocab (currently unused; reserved for future smarter mode)
_VOCAB: tuple[str, ...] = tuple(sorted(set(map(str.lower, [
    "pizza","pepperoni","margherita","calzone","stromboli","slice","pie","wings",
    "burger","cheeseburger","sandwich","sub","hoagie","panini","wrap","gyro","philly",
    "pasta","spaghetti","alfredo","ziti","lasagna","ravioli","penne","salad","caesar","greek","garden",
    "fries","rings","mozzarella","sticks","garlic","knots","coleslaw","side",
    "soda","coke","pepsi","tea","lemonade","coffee","water",
    "dessert","tiramisu","cannoli","brownie","cheesecake","cookie","ice","cream",
    "mushroom","onion","olive","bacon","sausage","meatball","ham","chicken","buffalo","boneless",
    "parmesan","mozzarella","ricotta","basil","tomato","marinara","pesto","bbq","ranch",
    "small","medium","large","xl","xxl","bottle","can","fountain",
]))))

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

# ---- (Fuzzy vocab helpers kept for future, not used now) ----
def _bigrams(w: str) -> set[str]:
    w = f"^{w.lower()}$"
    return {w[i:i+2] for i in range(len(w)-1)} if len(w) >= 2 else {w}

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

# ---------- Core cleaners (ULTRA SAFE) ----------

def clean_item_name(s: str) -> str:
    """
    ULTRA SAFE:
    - Do NOT join tokens
    - Do NOT run fuzzy vocab fixes
    - Only:
      * Unicode-normalize
      * Collapse extreme repeated chars
      * Strip obviously junky punctuation
      * Normalize spaces
      * Title-case the result
    """
    if not s:
        return ""
    t = _unicode_norm(s)
    t = _collapse_runs(t)
    t = _cleanup_punct(t)
    t = _TRAIL_PUNCT_RX.sub("", t).strip()
    t = smart_title(t)
    return t

def clean_description(s: str) -> str:
    """
    ULTRA SAFE:
    - Preserve structure as much as possible
    - No joining / fancy guessing
    - Just unicode-normalize, collapse crazy repeats, strip junk, normalize spaces.
    """
    if not s:
        return ""
    t = _unicode_norm(s)
    t = _collapse_runs(t)
    t = _cleanup_punct(t)
    return _normalize_spaces(t)

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

        # ULTRA SAFE clean – keep structure, just light polish
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
        ai_signal = 75 if (name_clean != name_raw or desc_clean != desc_raw) else None
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
