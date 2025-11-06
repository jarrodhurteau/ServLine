# storage/ai_ocr_helper.py
"""
AI OCR Helper — Day20/21 → Phase 2 pt.4  (rev9)

Cleans noisy OCR lines into sane menu items.

Core (Phase A):
- Picks best price(s) from a line (prefers decimals; filters tiny counts)
- Strips stray section tokens (e.g., "SALADS", "WINGS") glued to names
- Splits inline pipes '|' into separate chunks
- Attaches ingredient-only lines to previous item as description
- Re-inferrs category from header/keywords
- Post-pass repair to stitch split names ("Soft"+"Drink", "Garden"+"Salad",
  "BBQ"+"Chicken", "Buffalo"+"Chicken", "Meat"+"Lovers", "Bell"+"Peppers")
- Description sanitizer (remove “ee/ie/nics”, stray numbers, duplicate commas; tidy casing)

Phase B upgrades:
- Dot-leader support:  "Cheeseburger .... 12.99"  and wide-gap leaders
- Next-line prices:    Name on one line, price (or size pairs) on the next
- Size pairs/multiples:"Small 9.99 / Large 14.99", '12" 11.99  16" 16.99', "S 8.50 M 10.50 L 12"
- Two-column-ish lines: tolerate long mid-line gaps (≥3 spaces) as soft split tokens
- Better provenance + confidence scoring per rule

rev6 changes:
- Add sensible price bounds and cents-first parsing ("833" -> 8.33)
- Clamp absurd highs and filter lows consistently across all paths

rev7 changes:
- Name gating + symbol/vowel checks to drop garbled strings
- Stronger header detection (caps-ish, short, or keyworded)
- Confidence bumps based on price sanity
- Orphan price-only line attach (attach loose price lines to the last item lacking a price)

rev8 changes:
- Drop-lines/sections blacklist (e.g., TOPPINGS / BUILD YOUR OWN / ADD-ONS / BYO / EXTRA)
- Ignore “Slices”, “By the Slice”, and similar non-item counters
- Guard against lone size label items (“s”, “l”, etc.)
- Category-aware price sanity hints (very cheap beverages; toppings ≤ ~$10)
- Additional junk-name filters and header-leak scrub

rev9 changes:
- **Multi-item splitter**: explode a single OCR line containing two-or-more
  dot-leader segments (NAME .... PRICE NAME .... PRICE …) into separate
  pseudo-lines before normal parsing.
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

# ---------- noise / name gating helpers ----------
def _alpha_ratio(s: str) -> float:
    if not s:
        return 0.0
    a = sum(c.isalpha() for c in s)
    return a / max(1, len(s))

def _vowel_ratio(s: str) -> float:
    if not s:
        return 0.0
    vset = set("aeiouyAEIOUY")
    v = sum(c in vset for c in s)
    a = sum(c.isalpha() for c in s)
    return v / max(1, a)

def _symbol_ratio(s: str) -> float:
    if not s:
        return 1.0
    sym = sum(not (c.isalnum() or c.isspace()) for c in s)
    return sym / max(1, len(s))

_FOODISH = {
    "pizza","cheese","pepperoni","sausage","salad","wing","wings","burger","sub",
    "fries","soda","drink","drinks","water","lemonade","tea","coffee","bread",
    "garlic","parmesan","mozzarella","bbq","buffalo","chicken","meat","veggie",
    "mushroom","onion","tomato","olive","ham","bacon","tuna","philly","steak",
}

def _looks_foodish(s: str) -> bool:
    low = (s or "").lower()
    return any(w in low for w in _FOODISH)

def _passes_name_gate(name: str, has_price: bool) -> bool:
    """Reject heavy OCR mush; allow a bit more noise if a price is present."""
    if not name:
        return False
    # hard drop if lone size shorthand
    if name.strip().lower() in {"s","m","l","xl","lg"}:
        return False
    a = _alpha_ratio(name)
    v = _vowel_ratio(name)
    sym = _symbol_ratio(name)
    if has_price:
        return a >= 0.35 and v >= 0.22 and sym <= 0.25
    if _looks_foodish(name):
        return a >= 0.40 and v >= 0.24 and sym <= 0.20
    return a >= 0.55 and v >= 0.28 and sym <= 0.15

def _price_conf_bump(cands: List[Dict[str, float]]) -> float:
    """Modulate confidence from price sanity."""
    if not cands:
        return 0.7
    vals = [c.get("value", 0) for c in cands]
    if not vals:
        return 0.7
    lo, hi = min(vals), max(vals)
    if lo < 2.50:
        return 0.70
    if hi > 99.0:
        return 0.78
    if hi >= 6.0:
        return 0.90
    return 0.85

# ---------- price & size helpers ----------
# Sensible menu price bounds (tune as needed)
_PRICE_MIN = 2.50
_PRICE_MAX = 60.00

_PRICE_RX = re.compile(r"\$?\s*(\d{1,3})(?:[.,](\d{1,2}))?\b")
_PRICE_FULL_RX = re.compile(r"^\$?\s*\d{1,3}(?:[.,]\d{1,2})?\s*$")

# Dot leaders or wide-gap leaders (single)
_DOT_LEADER_RX = re.compile(
    r"(?P<left>.+?)(?:\s?\.{2,}\s?|\s{3,})(?P<right>\$?\s*\d{1,3}(?:[.,]\d{1,2})?)\s*$"
)

# Dot leaders global (multi) — for multi-item splitter
_MULTI_LEADER_RX = re.compile(
    r"(?P<left>.+?)(?:\s?\.{2,}\s?|\s{3,})(?P<right>\$?\s*\d{1,3}(?:[.,]\d{1,2})?)"
)

# Size tokens and pairs (Small/Large, S/M/L, inches like 12")
_SIZE_TOKEN = r'(?:XS|S|SM|M|MD|L|LG|XL|XXL|Kids|Kid|Junior|Large|Small|Medium|12"|14"|16"|18"|20"|10"|8")'
_SIZE_PAIR_RX = re.compile(
    rf"(?P<left>{_SIZE_TOKEN})\s*(?P<lp>\$?\s*\d{{1,3}}(?:[.,]\d{{1,2}})?)\s*(?:[\/,;]\s*|\s{{2,}})"
    rf"(?P<right>{_SIZE_TOKEN})\s*(?P<rp>\$?\s*\d{{1,3}}(?:[.,]\d{{1,2}})?)",
    re.IGNORECASE
)
# Multi pairs in one line: "S 8.5 M 10.5 L 12"
_SIZE_MULTI_RX = re.compile(
    rf"(?P<size>{_SIZE_TOKEN})\s*(?P<price>\$?\s*\d{{1,3}}(?:[.,]\d{{1,2}})?)",
    re.IGNORECASE
)

def _to_price(txt: str) -> float:
    s = txt.replace("$", "").replace(",", ".").strip()
    s = re.sub(r"\s+", "", s)
    # normalize multi-dots like "12.9.9"
    if s.count(".") > 1:
        a, _, b = s.rpartition(".")
        s = a.replace(".", "") + "." + b
    # cents-first: "833" -> 8.33, "1299" -> 12.99
    if s.isdigit() and 3 <= len(s) <= 4:
        try:
            return float(s[:-2] + "." + s[-2:])
        except Exception:
            return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0

def _best_price_candidates(line: str) -> List[float]:
    cands: List[float] = []
    for m in _PRICE_RX.finditer(line):
        n, d = m.group(1), m.group(2)
        val = _to_price(f"{n}.{d}" if d is not None else n)
        cands.append(val)
    # filter low and absurd highs
    cands = [p for p in cands if _PRICE_MIN <= p <= _PRICE_MAX]
    # Round/dedupe; sort high→low so the first is our base
    uniq = sorted({round(p, 2) for p in cands}, reverse=True)
    return uniq

def _strip_inline_prices(text: str) -> str:
    return _PRICE_RX.sub("", text).strip(" |,-")

def _parse_size_pairs(text: str) -> Tuple[List[Tuple[str, float]], str]:
    """
    Returns (sizes, remaining_text)
    sizes = list of (label, price)
    """
    t = text
    sizes: List[Tuple[str, float]] = []

    # exact pair first (left / right)
    m = _SIZE_PAIR_RX.search(t)
    if m:
        sizes.append((m.group("left").strip(), _to_price(m.group("lp"))))
        sizes.append((m.group("right").strip(), _to_price(m.group("rp"))))
        # remove the matched segment
        start, end = m.span()
        t = (t[:start] + " " + t[end:]).strip()

    # multi-pairs pass
    found = list(_SIZE_MULTI_RX.finditer(t))
    # only accept if we see at least 2 distinct pairs (avoid false positives)
    if len(found) >= 2:
        for m2 in found:
            sizes.append((m2.group("size").strip(), _to_price(m2.group("price"))))
        # strip them from the text
        t = _SIZE_MULTI_RX.sub("", t).strip()

    # filter junk to sensible range
    sizes = [(lbl, pr) for (lbl, pr) in sizes if _PRICE_MIN <= pr <= _PRICE_MAX]

    # light dedupe on repeated labels in same line (keep median-ish)
    if sizes:
        by_label: Dict[str, List[float]] = {}
        for lbl, pr in sizes:
            by_label.setdefault(lbl.lower(), []).append(pr)
        norm = []
        for lbl_lower, plist in by_label.items():
            plist = sorted(plist)
            keep = plist[len(plist)//2]  # median
            # recover original-cased label from first occurrence
            for lbl, pr in sizes:
                if lbl.lower() == lbl_lower and pr == keep:
                    norm.append((lbl, pr))
                    break
        sizes = norm

    return sizes, re.sub(r"\s{2,}", " ", t).strip(" -·|,")

# ---------- name/desc cleanup ----------
def _basic_clean(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s{2,}", " ", s).strip(" |,-")
    return s

def _looks_ingredients(s: str) -> bool:
    low = (s or "").strip().lower()
    if not low:
        return False
    if "," in low or low.startswith((
        "with ","lettuce","tomato","onion","onions","basil","mozzarella","mushroom","peppers",
        "pepper","olives","olive","bbq","garlic","parmesan","cucumber","greens","red","green",
    )):
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

# ---------- category price sanity windows (hints; non-blocking) ----------
def _category_price_window(cat: str) -> Tuple[float, float]:
    low = (cat or "").lower()
    if "beverage" in low or "drink" in low:
        return (0.99, 8.50)
    if "sides" in low or "apps" in low:
        return (2.50, 14.99)
    if "wing" in low:
        return (5.00, 24.99)
    if "salad" in low:
        return (4.00, 18.99)
    if "burger" in low or "sandwich" in low:
        return (6.00, 24.99)
    if "pizza" in low:
        return (6.00, 60.00)
    return (_PRICE_MIN, _PRICE_MAX)

def _clamp_by_category(cands: List[Dict[str, float]], cat: str) -> List[Dict[str, float]]:
    lo, hi = _category_price_window(cat)
    out = []
    seen = set()
    for c in cands:
        v = round(float(c.get("value", 0.0) or 0.0), 2)
        if lo <= v <= hi and v not in seen:
            out.append({"type": c.get("type","base"), "value": v})
            seen.add(v)
    return out

# ---------- drop-lines / skip heuristics ----------
_DROP_SECTION_WORDS = {
    "topping", "toppings", "build your own", "build-your-own", " byo ", "add-ons", "add on", "extras", "extra",
    "choose your", "pick your", "any topping", "each topping", "additional topping", "additional top",
}
_SLICES_WORDS = {"slice", "slices", "by the slice", "per slice"}

def _is_drop_line(s: str) -> bool:
    low = (s or "").lower().strip()
    if not low:
        return False
    # clear “SLICES / By the slice” counters
    if any(w in low for w in _SLICES_WORDS):
        return True
    # clear “TOPPINGS / BUILD YOUR OWN / ADD-ONS / EXTRAS”
    if any(w in low for w in _DROP_SECTION_WORDS):
        return True
    # obvious non-item housekeeping
    if low.startswith(("tax","delivery","fees","minimum","we reserve","no substitutions")):
        return True
    return False

# ---------- multi-item splitter (new in rev9) ----------
def _explode_multi_items(line: str) -> List[str]:
    """
    If a single OCR line contains two-or-more dot-leader item patterns,
    split it into separate 'name .... price' pseudo-lines.
    Example:
      'MEATBALL PARM .... 13.99  MAMA'S BURGER .... 11.99'
      -> ['MEATBALL PARM .... 13.99', 'MAMA'S BURGER .... 11.99']
    """
    if not line:
        return []
    matches = list(_MULTI_LEADER_RX.finditer(line))
    if len(matches) < 2:
        return [line]  # nothing to explode

    # Build chunks from consecutive leader matches (use spans to slice cleanly)
    chunks: List[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = m.end()
        # Determine right bound: up to next match start, else to end of string
        right_bound = matches[i+1].start() if (i+1) < len(matches) else len(line)
        segment = line[start:right_bound].strip(" ,;-—–")
        # Ensure the segment actually has a leader price; otherwise skip
        if _DOT_LEADER_RX.search(segment) or _MULTI_LEADER_RX.search(segment):
            chunks.append(segment.strip())
    # Fallback if something went weird
    return chunks or [line]

# ---------- splitter ----------
def _split_line_into_chunks(line: str) -> List[str]:
    """
    Split on pipes and very wide gaps (≥3 spaces) to simulate two columns.
    Then, if a chunk contains multiple dot-leader patterns, explode it into
    multiple pseudo-lines (rev9).
    Keep order; remove empties.
    """
    if not line:
        return []

    # First: split by pipe
    parts = re.split(r"\s*\|\s*", line)
    chunks: List[str] = []
    for p in parts:
        # Then split by long spaces if present
        subs = re.split(r"\s{3,}", p)
        for s in subs:
            s2 = _basic_clean(s)
            if not s2:
                continue
            # NEW: explode multi-item leaders inside each sub-chunk
            exploded = _explode_multi_items(s2)
            if exploded and len(exploded) > 1:
                for e in exploded:
                    e2 = _basic_clean(e)
                    if e2:
                        chunks.append(e2)
            else:
                chunks.append(s2)
    return chunks or [line]

# ---------- main API ----------
def analyze_ocr_text(raw_text: str, layout: Optional[Any] = None, taxonomy: Optional[Any] = None, restaurant_profile: Optional[Any] = None) -> Dict[str, Any]:
    lines = [l.rstrip() for l in (raw_text or "").splitlines()]
    blocks: List[Dict[str, Any]] = []
    cur = {"id": str(uuid.uuid4()), "header_text": None, "lines": []}

    def _push():
        if cur["lines"]:
            blocks.append(cur.copy())

    for l in lines:
        raw = l or ""
        t = _basic_clean(raw)
        if not t:
            continue
        just_letters = re.sub(r"[^A-Za-z& ]", "", t)
        all_capsish = just_letters and (
            sum(c.isupper() for c in just_letters if c.isalpha())
            >= 0.8 * max(1, sum(c.isalpha() for c in just_letters))
        )
        shortish = len(just_letters.split()) <= 5
        has_kw = any(h in t.lower() for h in _HEADER_WORDS)
        if (all_capsish and shortish and _alpha_ratio(t) >= 0.5) or has_kw:
            _push()
            cur = {"id": str(uuid.uuid4()), "header_text": _normalize_header(t), "lines": []}
        else:
            cur["lines"].append(t)
    _push()

    items: List[Dict[str, Any]] = []
    for b in blocks:
        header = _normalize_header(b.get("header_text") or "") if b.get("header_text") else None
        fallback_cat = header or "Uncategorized"

        # Pre-tokenize block into "chunks with index" so we can look ahead for next-line prices
        raw_chunks: List[Tuple[int, str]] = []
        for idx, raw_line in enumerate(b["lines"]):
            for ch in _split_line_into_chunks(raw_line):
                # skip global drop-lines up front
                if _is_drop_line(ch):
                    continue
                raw_chunks.append((idx, ch))

        i = 0
        carry_item: Optional[Dict[str, Any]] = None

        while i < len(raw_chunks):
            _, chunk = raw_chunks[i]
            chunk_strip = (chunk or "").strip()

            # drop obvious header fragments that slipped as lines
            if re.fullmatch(r"[:;,\-–—•·]*[A-Z]{2,}[\w &]*", chunk) and any(h in chunk.lower() for h in _HEADER_WORDS):
                header = _normalize_header(chunk)
                fallback_cat = header
                carry_item = None
                i += 1
                continue

            # re-check drop-line here as well (after potential header update)
            if _is_drop_line(chunk):
                i += 1
                continue

            # 0) ORPHAN PRICE-ONLY LINE
            # If a chunk is just a price (e.g., "12.99" or "$12.99") and we already have an item without prices,
            # attach it to the most recent item.
            if _PRICE_FULL_RX.fullmatch(chunk_strip or ""):
                pr_list = _best_price_candidates(chunk_strip)
                if pr_list and items:
                    tgt = carry_item or items[-1]
                    if tgt is not None:
                        pcs = list(tgt.get("price_candidates") or [])
                        base = round(pr_list[0], 2)
                        if not any(round(p.get("value", 0), 2) == base for p in pcs):
                            pcs.append({"type": "orphan_attach", "value": base})
                            # category-aware clamp
                            pcs = _clamp_by_category(pcs, tgt.get("category") or fallback_cat)
                            tgt["price_candidates"] = pcs
                            tgt["confidence"] = max(float(tgt.get("confidence") or 0.6), _price_conf_bump(pcs))
                        carry_item = None
                        i += 1
                        continue

            # 1) Dot-leader rule (or wide gap leaders)
            dl = _DOT_LEADER_RX.search(chunk)
            if dl:
                left = _basic_clean(dl.group("left"))
                pr = _to_price(dl.group("right"))
                sizes, left_rem = _parse_size_pairs(left)
                name_core = _basic_clean(_strip_inline_prices(left_rem))
                if not name_core:
                    name_core = "Untitled"
                desc = ""
                if " - " in name_core:
                    left2, right2 = name_core.split(" - ", 1)
                    name_core, desc = _basic_clean(left2), _basic_clean(right2)

                cat = _guess_category(name_core, desc, fallback_cat)
                price_candidates = [{"type": "leaders", "value": round(pr, 2)}] if (_PRICE_MIN <= pr <= _PRICE_MAX) else []
                variants = [{"label": lbl, "price": round(pv, 2)} for (lbl, pv) in sizes] if sizes else []

                # gate junky names
                if not _passes_name_gate(name_core, bool(price_candidates or variants)):
                    i += 1
                    continue

                # cat-aware clamp
                price_candidates = _clamp_by_category(price_candidates, cat)

                items.append({
                    "name": name_core,
                    "description": (desc or None),
                    "category": cat,
                    "price_candidates": price_candidates or ([{"type": "base", "value": round(pr, 2)}] if (_PRICE_MIN <= pr <= _PRICE_MAX) else []),
                    "confidence": _price_conf_bump(price_candidates) if price_candidates else 0.75,
                    "variants": variants,
                    "provenance": {"block_id": b["id"], "matched_rule": "dot_leader"},
                })
                carry_item = None
                i += 1
                continue

            # 2) Inline prices + (optional) size pairs on same chunk
            prices = _best_price_candidates(chunk)
            sizes_here, name_after_sizes = _parse_size_pairs(chunk)
            has_sizes = len(sizes_here) >= 1

            # Build base name (strip prices; also remove size-pairs text if we parsed any)
            name_core_src = name_after_sizes if has_sizes else chunk
            name_core = _strip_inline_prices(name_core_src)
            # remove embedded header words within name
            name_core = re.sub(r"\b(SALADS|WINGS|BEVERAGES|PIZZAS?)\b", "", name_core, flags=re.I)
            name_core = _basic_clean(name_core)

            if prices or has_sizes:
                carry_item = None
                base_price = prices[0] if prices else (sizes_here[0][1] if has_sizes else 0.0)
                variants = [{"label": "Alt", "price": round(prices[1],2)}] if (len(prices) > 1 and prices[1] >= max(_PRICE_MIN, base_price*0.5)) else []
                if has_sizes:
                    # prefer explicit sizes over generic Alt
                    variants = [{"label": lbl, "price": round(val, 2)} for (lbl, val) in sizes_here]

                desc = ""
                if " - " in name_core:
                    left, right = name_core.split(" - ", 1)
                    name_core, desc = _basic_clean(left), _basic_clean(right)

                tail_words = name_core.split(None, 1)
                if len(tail_words) >= 2 and _looks_ingredients(tail_words[1]):
                    name_core, desc = _basic_clean(tail_words[0]), _basic_clean((desc + " " + tail_words[1]).strip())

                cat = _guess_category(name_core, desc, fallback_cat)

                # gate junky names
                if not _passes_name_gate(name_core, True):
                    i += 1
                    continue

                rule = "inline_sizes" if has_sizes else "inline_price"
                price_candidates = (
                    [{"type": "base", "value": round(p,2)} for p in prices] if prices
                    else [{"type": "sizepair", "value": round(sizes_here[0][1],2)}]
                )
                # cat-aware clamp
                price_candidates = _clamp_by_category(price_candidates, cat)
                conf = _price_conf_bump(price_candidates)

                items.append({
                    "name": name_core or "Untitled",
                    "description": (desc or None),
                    "category": cat,
                    "price_candidates": price_candidates,
                    "confidence": conf,
                    "variants": variants,
                    "provenance": {"block_id": b["id"], "matched_rule": rule},
                })
                i += 1
                continue

            # 3) No inline price: maybe next chunk is a price-only / size-pair line
            # Decide whether this chunk is a likely name (vs. ingredient)
            if not _looks_ingredients(name_core):
                # peek next
                nxt = raw_chunks[i+1][1] if (i+1) < len(raw_chunks) else ""
                nxt_sizes, _ = _parse_size_pairs(nxt)
                nxt_prices = _best_price_candidates(nxt)
                nxt_is_price_only = (bool(nxt_prices) and _PRICE_FULL_RX.fullmatch((nxt or "").strip()) is not None)

                if nxt_is_price_only or nxt_sizes:
                    desc = ""
                    if " - " in name_core:
                        left, right = name_core.split(" - ", 1)
                        name_core, desc = _basic_clean(left), _basic_clean(right)
                    cat = _guess_category(name_core, desc, fallback_cat)

                    if nxt_sizes:
                        variants = [{"label": lbl, "price": round(val, 2)} for (lbl, val) in nxt_sizes]
                        pc = [{"type": "sizepair", "value": round(nxt_sizes[0][1], 2)}]
                        rule = "nextline_sizepair"
                    else:
                        variants = []
                        pc = [{"type": "nextline", "value": round(nxt_prices[0], 2)}]
                        rule = "nextline_price"

                    # name gate
                    if not _passes_name_gate(name_core, True):
                        i += 2
                        continue

                    pc = _clamp_by_category(pc, cat)

                    items.append({
                        "name": name_core or "Untitled",
                        "description": (desc or None),
                        "category": cat,
                        "price_candidates": pc,
                        "confidence": _price_conf_bump(pc),
                        "variants": variants,
                        "provenance": {"block_id": b["id"], "matched_rule": rule},
                    })
                    i += 2  # consume next chunk too
                    carry_item = None
                    continue

            # 4) Otherwise treat as description or standalone nameline (no price yet)
            if carry_item is None and items:
                carry_item = items[-1]
            if carry_item and _looks_ingredients(chunk):
                prev = carry_item.get("description") or ""
                new_desc = (prev + " " + chunk).strip() if prev else chunk
                carry_item["description"] = re.sub(r"\s{2,}", " ", new_desc).strip(", -")
            else:
                # gate bare names a bit more strictly since no price yet
                if not _passes_name_gate(name_core, False):
                    i += 1
                    continue
                cat = _guess_category(name_core, "", fallback_cat)
                # extra guard: don't create items from obvious non-item counters
                if _is_drop_line(name_core):
                    i += 1
                    continue
                ni = {
                    "name": name_core,
                    "description": None,
                    "category": cat,
                    "price_candidates": [],
                    "confidence": 0.6,
                    "variants": [],
                    "provenance": {"block_id": b["id"], "matched_rule": "name_only"},
                }
                items.append(ni)
                carry_item = ni

            i += 1

    # --- NOISE SCRUB / STITCHERS ---
    _NONWORD_RUN = re.compile(r"\b(?![aeiouyAEIOUY])([A-Za-z]){3,}\b")  # 3+ letter no-vowel junk
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
                                     ["salad","salads","wings","beverage","beverages","pizza","pizzas","andwich","topping","toppings"])
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
        # If we have variants but no price_candidates (rare), seed one from first variant
        if (not it.get("price_candidates")) and it.get("variants"):
            v0 = it["variants"][0]
            it["price_candidates"] = [{"type": "variant_seed", "value": round(float(v0.get("price", 0)) or 0, 2)}]

        # final cat-aware clamp for candidates
        if it.get("price_candidates"):
            it["price_candidates"] = _clamp_by_category(list(it["price_candidates"]), it.get("category") or "")

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
