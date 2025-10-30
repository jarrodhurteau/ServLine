# storage/ai_ocr_helper.py
"""
AI OCR Helper — Day20 Phase A/B (rev4)
Cleans noisy OCR lines into sane menu items:
- Picks best price(s) from a line (prefers decimals; filters tiny counts)
- Strips stray section tokens (e.g., "SALADS", "WINGS") glued to names
- Splits inline pipes '|' into separate chunks
- Attaches ingredient-only lines to previous item as description
- Re-inferrs category from header/keywords
- Post-pass repair to stitch split names ("Soft"+"Drink", "Garden"+"Salad",
  "BBQ"+"Chicken", "Buffalo"+"Chicken", "Meat"+"Lovers", "Bell"+"Peppers")
- NEW (rev4): Description sanitizer (remove “ee/ie/nics”, stray numbers, duplicate commas; tidy casing)
"""

from __future__ import annotations
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

# ---------- light header normalizer ----------
_SECTION_FIXES = {
    "andwich": "Sandwiches",
    "andwiches": "Sandwiches",
    "andwiche": "Sandwiches",
    "beverage": "Beverages",
    "beverages": "Beverages",
    "wings": "Wings",
    "salads": "Salads",
    "sides": "Sides & Apps",
    "apps": "Sides & Apps",
    "appetizers": "Sides & Apps",
    "pizza": "Pizza",
    "pizzas": "Pizza",
    "specialty pizzas": "Specialty Pizzas",
    "burgers & sandwiches": "Burgers & Sandwiches",
}
_HEADER_WORDS = {"pizza","pizzas","specialty","wings","salads","beverages","drinks","burgers","sandwiches","subs","sides","apps"}

def _normalize_header(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^[\s:;,\-–—•·]+", "", s)
    s = re.sub(r"^[A-Z]{1,2}\s+(?=[A-Z])", "", s)  # drop tiny all-caps prefixes like "EE "
    low = s.lower()
    for k, v in _SECTION_FIXES.items():
        if low == k or low.rstrip("e") == k or k in low:
            return v
    return s.title() if s.isupper() else s

# ---------- price helpers ----------
_PRICE_RX = re.compile(r"\$?\s*(\d{1,3})(?:[.,](\d{1,2}))?\b")
_PRICE_FULL_RX = re.compile(r"^\$?\s*\d{1,3}(?:[.,]\d{1,2})?\s*$")

def _to_price(txt: str) -> float:
    s = txt.replace("$", "").replace(",", ".").strip()
    s = re.sub(r"\s+", "", s)
    if s.count(".") > 1:
        a, _, b = s.rpartition(".")
        s = a.replace(".", "") + "." + b
    try:
        return float(s)
    except Exception:
        if s.isdigit() and 3 <= len(s) <= 4:
            return float(s[:-2] + "." + s[-2:])
        return 0.0

def _best_price_candidates(line: str) -> List[float]:
    cands: List[float] = []
    for m in _PRICE_RX.finditer(line):
        n = m.group(1)
        d = m.group(2)
        cands.append(_to_price(f"{n}.{d}") if d is not None else _to_price(n))
    cands = [p for p in cands if p >= 2.50]  # filter tiny counts like "2"
    uniq = []
    for p in sorted(set(round(p, 2) for p in cands), reverse=True):
        uniq.append(p)
    return uniq

def _strip_inline_prices(text: str) -> str:
    return _PRICE_RX.sub("", text).strip(" |,-")

# ---------- name/desc cleanup ----------
def _basic_clean(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s{2,}", " ", s).strip(" |,-")
    return s

def _looks_ingredients(s: str) -> bool:
    low = (s or "").strip().lower()
    if not low:
        return False
    if "," in low or low.startswith(("with ","lettuce","tomato","onion","onions","basil","mozzarella","mushroom","peppers","pepper","olives","olive","bbq","garlic","parmesan","cucumber","greens","red","green")):
        return True
    words = re.findall(r"[A-Za-z]+", low)
    return bool(words) and low == " ".join(words) and 1 <= len(words) <= 6

# ---------- category inference ----------
_CAT_HINTS = {
    "Beverages": ["soda","soft drink","coke","sprite","pepsi","water","lemonade","tea","iced","coffee","root beer","mountain dew","dr pepper","drink"],
    "Wings": ["wings","buffalo","garlic parmesan","honey bbq","parmesan","bbq","wing"],
    "Burgers & Sandwiches": ["burger","cheeseburger","patty","sandwich","sub","ham","salami","provolone","grilled chicken","bacon"],
    "Salads": ["salad","romaine","cucumber","greens","feta","garden","greek"],
    "Specialty Pizzas": ["margherita","hawaiian","veggie","meat lovers","pepperoni","mozzarella","basil","bbq chicken","buffalo chicken"],
    "Pizza": ["pizza","cheese"],
}

def _guess_category(name: str, desc: str, fallback: str) -> str:
    text = f"{name} {desc}".lower()
    best = (0, fallback or "Uncategorized")
    for cat, kws in _CAT_HINTS.items():
        hits = sum(1 for k in kws if k in text)
        if hits > best[0]:
            best = (hits, cat)
    return best[1]

# ---------- splitter ----------
def _split_line_into_chunks(line: str) -> List[str]:
    parts = re.split(r"\s*\|\s*", line)
    out: List[str] = []
    for p in parts:
        p = _basic_clean(p)
        if p:
            out.append(p)
    return out or [line]

# ---------- main API ----------
def analyze_ocr_text(raw_text: str, layout: Optional[Any] = None, taxonomy: Optional[Any] = None, restaurant_profile: Optional[Any] = None) -> Dict[str, Any]:
    lines = [l.strip() for l in (raw_text or "").splitlines()]
    blocks: List[Dict[str, Any]] = []
    cur = {"id": str(uuid.uuid4()), "header_text": None, "lines": []}

    def _push():
        if cur["lines"]:
            blocks.append(cur.copy())

    for l in lines:
        t = _basic_clean(l)
        if not t:
            continue
        just_letters = re.sub(r"[^A-Za-z& ]", "", t)
        if just_letters.isupper() and any(h in t.lower() for h in _HEADER_WORDS):
            _push()
            cur = {"id": str(uuid.uuid4()), "header_text": _normalize_header(t), "lines": []}
        else:
            cur["lines"].append(t)
    _push()

    items: List[Dict[str, Any]] = []
    for b in blocks:
        header = _normalize_header(b.get("header_text") or "") if b.get("header_text") else None
        fallback_cat = header or "Uncategorized"
        carry_item: Optional[Dict[str, Any]] = None

        for raw in b["lines"]:
            for chunk in _split_line_into_chunks(raw):
                # drop obvious header fragments that slipped as lines
                if re.fullmatch(r"[:;,\-–—•·]*[A-Z]{2,}[\w &]*", chunk) and any(h in chunk.lower() for h in _HEADER_WORDS):
                    header = _normalize_header(chunk)
                    fallback_cat = header
                    carry_item = None
                    continue

                prices = _best_price_candidates(chunk)
                name_core = _strip_inline_prices(chunk)
                # remove embedded header words within name
                name_core = re.sub(r"\b(SALADS|WINGS|BEVERAGES|PIZZAS?)\b", "", name_core, flags=re.I)
                name_core = _basic_clean(name_core)
                if not name_core and not prices:
                    continue

                if prices:
                    carry_item = None
                    base_price = prices[0]
                    variants = []
                    if len(prices) > 1 and prices[1] >= max(2.50, base_price * 0.5):
                        variants.append({"label": "Alt", "price": round(prices[1], 2)})

                    desc = ""
                    if " - " in name_core:
                        left, right = name_core.split(" - ", 1)
                        name_core, desc = _basic_clean(left), _basic_clean(right)

                    tail_words = name_core.split(None, 1)
                    if len(tail_words) >= 2 and _looks_ingredients(tail_words[1]):
                        name_core, desc = _basic_clean(tail_words[0]), _basic_clean((desc + " " + tail_words[1]).strip())

                    cat = _guess_category(name_core, desc, fallback_cat)
                    items.append({
                        "name": name_core or "Untitled",
                        "description": (desc or None),
                        "category": cat,
                        "price_candidates": [{"type": "base", "value": round(p,2)} for p in prices],
                        "confidence": 0.8 if prices else 0.6,
                        "variants": variants,
                        "provenance": {"block_id": b["id"]},
                    })
                else:
                    if carry_item is None and items:
                        carry_item = items[-1]
                    if carry_item and _looks_ingredients(chunk):
                        prev = carry_item.get("description") or ""
                        new_desc = (prev + " " + chunk).strip() if prev else chunk
                        carry_item["description"] = re.sub(r"\s{2,}", " ", new_desc).strip(", -")
                    else:
                        cat = _guess_category(name_core, "", fallback_cat)
                        ni = {
                            "name": name_core,
                            "description": None,
                            "category": cat,
                            "price_candidates": [],
                            "confidence": 0.6,
                            "variants": [],
                            "provenance": {"block_id": b["id"]},
                        }
                        items.append(ni)
                        carry_item = ni

    # --- NOISE SCRUB / STITCHERS ---
    _NONWORD_RUN = re.compile(r"\b(?![aeiouyAEIOUY])([A-Za-z]){3,}\b")  # 3+ letter no-vowel junk (e.g., "eee", "ncs")
    _REPEAT_CHARS = re.compile(r"(.)\1\1+")  # any char repeated 3+ times
    _NOISE_TOKENS = {"ee","ie","e","nics","ncs","ees"}  # frequent stray OCR syllables
    _TOK_SPLIT = re.compile(r"[,\s/;]+")

    def _scrub_noise(text: str) -> str:
        if not text:
            return text
        t = _REPEAT_CHARS.sub(r"\1\1", text)           # compress loooong repeats → "loo"
        t = _NONWORD_RUN.sub("", t)                     # drop vowel-less junk tokens
        t = re.sub(r"\s{2,}", " ", t).strip(" ,.-")
        return t

    def _title_token(tok: str) -> str:
        # Keep "BBQ" upper; otherwise Title Case
        return tok if tok.upper() == "BBQ" else tok.capitalize()

    def _clean_desc(desc: Optional[str]) -> Optional[str]:
        if not desc:
            return None
        d = _scrub_noise(desc)
        if not d:
            return None
        toks = [t for t in _TOK_SPLIT.split(d) if t]
        norm: List[str] = []
        seen = set()
        for t in toks:
            tl = t.lower().strip(".,-:()")
            if not tl or tl in _NOISE_TOKENS:
                continue
            # drop isolated small integers (e.g., "2") unless clearly a price (we already moved prices out)
            if tl.isdigit() and len(tl) <= 2:
                continue
            if tl not in seen:
                seen.add(tl)
                norm.append(_title_token(tl))
        if not norm:
            return None
        return ", ".join(norm)

    # -------- POST-PASS REPAIRS --------
    def _starts_with_word(s: Optional[str], w: str) -> bool:
        return bool(s) and s.strip().lower().startswith(w.lower()+" ")

    repaired: List[Dict[str, Any]] = []
    for it in items:
        name = (it.get("name") or "").strip()
        desc = (it.get("description") or "") or ""

        # Drop junk header-ish residue (e.g., ": ANDWICHE")
        headerish = re.fullmatch(r"[:;,\-–—•·\s]*[A-Z &]{2,}$", name or "")
        looks_like_header_word = any(w in (name or "").lower() for w in
                                     ["salad","salads","wings","beverage","beverages","pizza","pizzas","andwich"])
        if headerish or (looks_like_header_word and not re.search(r"[a-z]", name or "")):
            continue

        # Stitch split two-word names
        if name.lower() in {"soft","garden","greek","buffalo","bbq","meat","bell","chicken"}:
            first = desc.split()[0] if desc else ""
            if first and first[0].isalpha() and first[0].isupper():
                if first.lower() in {"drink","salad","chicken","lovers","peppers"}:
                    name = f"{name} {first}".strip()
                    desc = desc[len(first):].lstrip(" ,.-")

        # Specific combos normalizer
        name = re.sub(r"\bBbq\b", "BBQ", name)
        name = re.sub(r"\b(Buffalo Chicken|BBQ Chicken)\b", lambda m: m.group(1).title().replace("Bbg","BBQ"), name)

        # “Meat Lovers”
        if name.lower() == "meat" and _starts_with_word(desc, "lovers"):
            name = "Meat Lovers"
            desc = desc[len("lovers"):].lstrip(" ,.-")

        # “Bell Peppers” and strip accidental salad tails
        if name.lower() == "bell" and _starts_with_word(desc, "peppers"):
            name = "Bell Peppers"
            desc = desc[len("peppers"):].lstrip(" ,.-")
        if name.lower() in {"bell peppers","meat lovers","buffalo chicken","bbq chicken"}:
            desc = re.sub(r"\b(garden|greek)\s+salad\b", "", desc, flags=re.I).strip(" ,.-")

        # Scrub + normalize description tokens
        desc = _clean_desc(desc)

        # finalize
        it["name"] = _basic_clean(name)
        it["description"] = desc
        it["category"] = (_normalize_header(it.get("category") or "Uncategorized")).title()
        repaired.append(it)
    items = repaired

    # Final tidy: enforce canonical category names
    for it in items:
        low = (it["category"] or "").lower()
        it["category"] = {
            "pizza":"Pizza",
            "specialty pizzas":"Specialty Pizzas",
            "wings":"Wings",
            "salads":"Salads",
            "beverages":"Beverages",
            "burgers & sandwiches":"Burgers & Sandwiches",
            "sides & apps":"Sides & Apps",
        }.get(low, _normalize_header(it["category"] or "Uncategorized"))

    doc = {
        "sections": [],
        "items": items,
    }
    return doc
