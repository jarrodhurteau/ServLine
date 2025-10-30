# storage/ocr_helper.py
from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

# ---- Canary to prove this helper is the one running
OCR_HELPER_CANARY = "ocr_helper_active_v20"

# optional deps
try:
    import cv2  # type: ignore
except Exception:
    cv2 = None

import pytesseract
from pytesseract import Output

# pdf → images
try:
    from pdf2image import convert_from_path
except Exception:
    convert_from_path = None

# Optional column mode
try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None

try:
    from sklearn.cluster import KMeans  # type: ignore
except Exception:
    KMeans = None

import re
import unicodedata

# =============================
# NORMALIZATION (common OCR fixes)
# =============================
import re as _re

_NORMALIZE_SUBS = [
    # leading 'l' read instead of 'I' on common words
    (_re.compile(r'(?<=^|\s)lced\b', _re.IGNORECASE), 'Iced'),
    # 'andwich' family (dropped leading 's')
    (_re.compile(r'(?<=^|\s)andwiches\b', _re.IGNORECASE), 'sandwiches'),
    (_re.compile(r'(?<=^|\s)andwich\b', _re.IGNORECASE), 'sandwich'),
    # common menu vocab 1-letter drops (frequent left-edge crop)
    (_re.compile(r'(?<=^|\s)oda\b', _re.IGNORECASE), 'Soda'),
    (_re.compile(r'(?<=^|\s)pecial\b', _re.IGNORECASE), 'Special'),
    # punctuation / spacing polish
    (_re.compile(r'\s+([,:;])'), r'\1'),
    (_re.compile(r'([(:])\s+'), r'\1'),
    # stray leading bullets/dashes/dots
    (_re.compile(r'^\s*[-–—•·]+\s*'), ''),
]

_NORMALIZE_DICT = {
    'mozerella': 'mozzarella',
    'mozerrella': 'mozzarella',
    'mozzarela': 'mozzarella',
    'parmesean': 'parmesan',
    'bbq': 'BBQ',
    'bleu': 'blue',
    'meduim': 'medium',
    'lunch spcial': 'lunch special',
    'fountain sode': 'fountain soda',
    'garic': 'garlic',
    'peperoni': 'pepperoni',
    'chese': 'cheese',
    'chesee': 'cheese',
    'hamurger': 'hamburger',
    'cheseburger': 'cheeseburger',
    # extra safety nets for common slips
    'peporoni': 'pepperoni',
    'mozzerella': 'mozzarella',
    'mozarella': 'mozzarella',
    'chiken': 'chicken',
    'chiicken': 'chicken',
}

_CATEGORY_LEX = {
    'appetizers','sides','salads','wings','pizzas','burgers','subs','sandwiches',
    'wraps','beverages','drinks','desserts','specials','combos','kids','pastas',
}

def _case_preserving_replace(s: str, new: str) -> str:
    if s.isupper():
        return new.upper()
    if s.istitle():
        return new.title()
    return new

def _normalize_menu_text(text: str, *, as_category: bool = False) -> str:
    if not text:
        return text
    original = text

    for rx, repl in _NORMALIZE_SUBS:
        text = rx.sub(repl, text)

    def fix_token(tok: str) -> str:
        key = tok.lower()
        if key in _NORMALIZE_DICT:
            return _case_preserving_replace(tok, _NORMALIZE_DICT[key])
        if key.endswith('andwich'):
            return _case_preserving_replace(tok, 'sandwich')
        if key.endswith('andwiches'):
            return _case_preserving_replace(tok, 'sandwiches')
        return tok

    tokens = _re.split(r'(\W+)', text)
    tokens = [fix_token(t) if i % 2 == 0 else t for i, t in enumerate(tokens)]
    text = ''.join(tokens)

    text = _re.sub(r'\s{2,}', ' ', text).strip(' \t-–—•·')

    if as_category:
        low = text.strip().lower()
        if low in _CATEGORY_LEX or (len(text) <= 32 and text.isupper()):
            text = text.title()

    return text if text else original
# =============================
# /NORMALIZATION
# =============================

# -----------------------------
# Regex & heuristics
# -----------------------------
DOT_LEADER_RX = re.compile(r"\.{2,}")  # dot leaders "....."

def _normalize_text(s: str) -> str:
    """Normalize bullets/dashes and compress whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.replace("•", "-").replace("·", "-").replace("‧", "-")
    s = s.replace("–", "-").replace("—", "-")
    s = DOT_LEADER_RX.sub(" ", s)
    s = " ".join(s.split())
    return s

PRICE_RX = re.compile(
    r"""
    (?<!\w)
    \$?\s*
    (?P<int>\d{1,3}(?:[,\s]\d{3})*|\d+)
    (?:\s*[\.\s]\s*(?P<dec>\d{1,2}))?
    (?!\w)
    """,
    re.X,
)

PRICE_ONLY_RX = re.compile(r"^\s*\$?\s*\d{1,3}(?:[,\s]\d{3})?(?:\s*[\.\s]\s*\d{1,2})?\s*$")

SIZE_PAIR_RX = re.compile(
    r"""(?P<label>[A-Za-z]{2,}|\b(Regular|Small|Medium|Large|XL|Half|Full)\b)\s*[:\-]?\s*(?P<price>\$?\s*\d+(?:[\.\s]\s*\d{1,2})?)""",
    re.I,
)

ALL_CAPS_HDR = re.compile(r"^[A-Z][A-Z0-9&\s'\-]{2,}$")
TITLE_CASE_HDR = re.compile(r"^[A-Z][A-Za-z0-9'&\-\s]{3,}$")

SECTION_HINTS = (
    "pizza", "pizzas", "burgers", "sandwiches", "wings", "sides", "apps", "appetizers",
    "salads", "beverages", "drinks", "desserts", "specialty"
)

CONTINUATION_PREFIXES = (
    "with", "served", "fresh", "spicy", "sweet", "savory", "romaine", "mixed",
    "golden", "crispy", "lettuce", "tomato", "cucumber", "onion", "onions",
    "bbq", "garlic", "parmesan", "basil", "marinara", "mozzarella", "mushrooms",
    "pepperoni", "olives", "peppers", "honey", "red", "green"
)

PAREN_PCS_RX = re.compile(r"\(\s*\d+\s*pcs?\s*\)", re.I)

# Canon headers and semantic hints
HEADER_CANON = {
    "pizza": "Pizza",
    "specialty pizzas": "Specialty Pizzas",
    "burgers & sandwiches": "Burgers & Sandwiches",
    "wings": "Wings",
    "sides & apps": "Sides & Apps",
    "salads": "Salads",
    "beverages": "Beverages",
}
FLAVOR_HINTS = {"coke", "diet", "sprite", "pepsi", "root beer", "orange", "lemonade", "dr pepper", "mountain dew"}

SEMANTIC_KEYWORDS = {
    "Beverages": [
        "tea", "iced", "soda", "cola", "coke", "sprite", "pepsi", "water", "lemonade", "juice",
        "coffee", "latte", "dr pepper", "mountain dew", "root beer"
    ],
    "Wings": [
        "wings", "buffalo", "bbq", "garlic parmesan", "honey bbq", "parmesan", "sweet & smoky"
    ],
    "Burgers & Sandwiches": [
        "burger", "cheeseburger", "patty", "sandwich", "bacon", "grilled chicken", "mayo"
    ],
    "Salads": [
        "salad", "romaine", "croutons", "cucumber", "greens", "parmesan", "mixed"
    ],
    "Sides & Apps": [
        "fries", "breadsticks", "mozzarella sticks", "marinara", "dip", "appetizer", "crispy"
    ],
    "Specialty Pizzas": [
        "margherita", "pepperoni", "mozzarella", "basil", "veggie", "bbq chicken",
        "peppers", "olives", "onions"
    ],
    "Pizza": [
        "pizza"
    ],
}

# -----------------------------
# Helpers
# -----------------------------
def _to_float_price(txt: str) -> float:
    if not txt:
        return 0.0
    s = txt.replace("$", " ").replace(",", " ").strip()
    parts = [p for p in re.split(r"\s+", s) if p]
    if len(parts) >= 2 and parts[-1].isdigit() and len(parts[-1]) <= 2:
        return float(f"{parts[0]}.{parts[-1].rjust(2,'0')}")
    s = s.replace(" ", "")
    if s.count(".") > 1:
        left, _, right = s.rpartition(".")
        s = left.replace(".", "") + "." + right
    try:
        return float(s)
    except Exception:
        return 0.0

def _preprocess_image(img_bgr):
    if cv2 is None:
        return img_bgr
    try:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.medianBlur(gray, 3)
        th = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 31, 9
        )
        coords = cv2.findNonZero(255 - th)
        if coords is not None:
            rect = cv2.minAreaRect(coords)
            angle = rect[-1]
            if angle < -45:
                angle = 90 + angle
            M = cv2.getRotationMatrix2D((th.shape[1] / 2, th.shape[0] / 2), angle, 1.0)
            th = cv2.warpAffine(
                th, M, (th.shape[1], th.shape[0]),
                flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
            )
        return cv2.cvtColor(th, cv2.COLOR_GRAY2BGR)
    except Exception:
        return img_bgr

def _image_to_words(image_bgr) -> List[dict]:
    config = "--psm 6 -l eng"
    data = pytesseract.image_to_data(
        image_bgr, output_type=Output.DATAFRAME, config=config  # type: ignore
    )
    if data is None or len(data) == 0:
        return []
    data = data[(data.conf != -1) & data.text.notna()]
    out = []
    for _, r in data.iterrows():
        try:
            out.append({
                "text": str(r["text"]),
                "conf": float(r["conf"]),
                "left": int(r["left"]), "top": int(r["top"]),
                "width": int(r["width"]), "height": int(r["height"]),
                "cx": int(r["left"]) + int(r["width"]) / 2.0,
                "cy": int(r["top"]) + int(r["height"]) / 2.0,
                "line_num": int(r.get("line_num", 0)),
                "block_num": int(r.get("block_num", 0)),
                "par_num": int(r.get("par_num", 0)),
                "page_num": int(r.get("page_num", 0)),
            })
        except Exception:
            continue
    return out

def _cluster_columns(words: List[dict], k: int = 2) -> Optional[List[int]]:
    if not words or pd is None or KMeans is None:
        return None
    try:
        df = pd.DataFrame(words)
        if "cx" not in df.columns or len(df) < 8:
            return None
        X = df[["cx"]].values
        k = min(k, max(1, len(df) // 40)) or 1
        if k <= 1:
            return [0] * len(df)
        model = KMeans(n_clusters=k, n_init="auto", random_state=0)
        labels = model.fit_predict(X)
        return list(labels)
    except Exception:
        return None

def _line_left(d: dict) -> int:
    toks = d.get("tokens") or []
    if not toks:
        return 0
    return min(int(t.get("left", 0)) for t in toks)

def _line_height(d: dict) -> int:
    toks = d.get("tokens") or []
    if not toks:
        return 22
    hs = [int(t.get("height", 14)) for t in toks if t.get("height") is not None]
    return max(14, int(sum(hs) / max(1, len(hs))))

def _has_price(s: str) -> bool:
    return bool(PRICE_RX.search(s))

# Normalizer for noisy category headers like ": ANDWICHE" / "EE BEVERAGES"
def _normalize_category_header(s: str) -> str:
    if not s:
        return s
    s = _normalize_text(s)
    # strip leading punctuation and bullets/colons
    s = re.sub(r"^[\s:;,\-–—•·]+", "", s)
    # drop tiny all-caps prefixes Tesseract sometimes prepends ("E ", "EE ")
    s = re.sub(r"^[A-Z]{1,2}\s+(?=[A-Z])", "", s)

    low = s.lower()
    fixes = {
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
    for key, val in fixes.items():
        if low == key or low.rstrip("e") == key:
            return val
    for key, val in fixes.items():
        if key in low:
            return val
    if s.isupper():
        s = s.title()
    return s

# -----------------------------
# Line grouping & merging
# -----------------------------
def _group_lines(words: List[dict], labels: Optional[List[int]]) -> List[dict]:
    if not words:
        return []
    items = list(words)
    if labels and len(labels) == len(items):
        for w, lab in zip(items, labels):
            w["col"] = int(lab)
    else:
        for w in items:
            w["col"] = 0

    grouped: Dict[Tuple[int, int, int], List[dict]] = {}
    for w in items:
        page = int(w.get("page_num", 0))
        col = int(w["col"])
        line_key = int(w.get("line_num") or max(0, round(w["cy"] / 18)))
        key = (page, col, line_key)
        grouped.setdefault(key, []).append(w)

    lines: List[dict] = []
    for (page, col, line_key), ws in sorted(grouped.items(), key=lambda t: (t[0][0], t[0][1], t[0][2])):
        ws_sorted = sorted(ws, key=lambda x: x.get("left", 0))
        raw_txt = " ".join(x["text"] for x in ws_sorted if str(x["text"]).strip())
        txt = _normalize_text(raw_txt)
        if not txt:
            continue
        avg_conf = sum(float(x["conf"]) for x in ws_sorted) / max(1, len(ws_sorted))
        lines.append({
            "text": txt,
            "conf": round(avg_conf, 1),
            "page": int(page),
            "col": int(col),
            "y": min(int(x.get("top", 0)) for x in ws_sorted),
            "tokens": ws_sorted,
        })
    return lines

def _is_stub(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    return s.endswith("-") or s.endswith(",")

def _stitch_stub_price(lines: List[dict]) -> List[dict]:
    if not lines:
        return []
    out: List[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        cur = lines[i]
        cur_text = _normalize_text(cur["text"])
        if _is_stub(cur_text) and i + 1 < n:
            nxt = lines[i + 1]
            if (nxt["page"] == cur["page"] and nxt["col"] == cur["col"] and
                PRICE_ONLY_RX.match(_normalize_text(nxt["text"]))):
                dy = abs(int(nxt["y"]) - int(cur["y"]))
                h = max(_line_height(cur), _line_height(nxt))
                if dy <= int(h * 1.35) + 6:
                    merged = _normalize_text(f"{cur_text} {nxt['text']}")
                    out.append({**cur, "text": merged, "conf": round((cur["conf"] + nxt["conf"]) / 2.0, 1)})
                    i += 2
                    continue
        if PRICE_ONLY_RX.match(cur_text) and i + 1 < n:
            nxt = lines[i + 1]
            nxt_text = _normalize_text(nxt["text"])
            if (nxt["page"] == cur["page"] and nxt["col"] == cur["col"] and _is_stub(nxt_text)):
                dy = abs(int(nxt["y"]) - int(cur["y"]))
                h = max(_line_height(cur), _line_height(nxt))
                if dy <= int(h * 1.35) + 6:
                    merged = _normalize_text(f"{nxt_text} {cur_text}")
                    out.append({**nxt, "text": merged, "conf": round((cur["conf"] + nxt["conf"]) / 2.0, 1), "y": min(cur["y"], nxt["y"])})
                    i += 2
                    continue
        out.append(cur)
        i += 1
    return out

def _merge_broken_lines(lines: List[dict]) -> List[dict]:
    if not lines:
        return []
    lines = _stitch_stub_price(lines)

    merged: List[dict] = []
    seq = sorted(lines, key=lambda r: (r.get("page", 0), r.get("col", 0), r.get("y", 0)))

    def _looks_like_new_item_title(s: str) -> bool:
        s = s.strip()
        if not s.startswith("- "):
            return False
        core = s[2:].strip()
        if " - " in core:
            return True
        if PRICE_ONLY_RX.match(core) or _has_price(core):
            return False
        words = core.split()
        if 1 <= len(words) <= 4 and core[:1].isupper():
            return True
        return False

    def can_merge(prev: dict, curr: dict) -> bool:
        if prev.get("page") != curr.get("page") or prev.get("col") != curr.get("col"):
            return False

        prev_text = _normalize_text(prev.get("text", ""))
        curr_text = _normalize_text(curr.get("text", ""))
        if not prev_text or not curr_text:
            return False

        dy = abs(int(curr.get("y", 0)) - int(prev.get("y", 0)))
        h = max(_line_height(prev), _line_height(curr))
        if dy > int(h * 1.25) + 6:
            return False

        if _looks_like_new_item_title(curr_text):
            return False

        prev_price = _has_price(prev_text)
        curr_price = _has_price(curr_text)

        if (_is_stub(prev_text) and (curr_price or len(curr_text) <= 24)) or \
           ((len(prev_text) <= 24) and curr_price):
            if abs(_line_left(prev) - _line_left(curr)) > 260:
                return False
            return True

        return False

    def order_merge(a: str, b: str) -> str:
        a = _normalize_text(a); b = _normalize_text(b)
        if _has_price(a) and not _has_price(b):
            return _normalize_text(f"{b} {a}")
        return _normalize_text(f"{a} {b}")

    for row in seq:
        if not merged:
            merged.append(row)
            continue
        prev = merged[-1]
        if can_merge(prev, row):
            new_text = order_merge(prev["text"], row["text"])
            prev_tok = len(prev.get("tokens") or [])
            row_tok = len(row.get("tokens") or [])
            tot = max(1, prev_tok + row_tok)
            conf = (prev.get("conf", 80) * prev_tok + row.get("conf", 80) * row_tok) / tot
            merged[-1] = {
                "text": new_text,
                "conf": round(conf, 1),
                "page": prev.get("page"),
                "col": prev.get("col"),
                "y": min(int(prev.get("y", 0)), int(row.get("y", 0))),
                "tokens": (prev.get("tokens") or []) + (row.get("tokens") or []),
            }
        else:
            merged.append(row)
    return merged

# -----------------------------
# Parsing heuristics
# -----------------------------
def _looks_like_section_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 48:
        return False
    if ALL_CAPS_HDR.match(s):
        return True
    if TITLE_CASE_HDR.match(s):
        low = s.lower()
        for w in SECTION_HINTS:
            if w in low:
                return True
    return False

# IMPROVED: choose best price on the line (prefer decimals, filter tiny numbers)
def _split_name_desc_price(line: str) -> Tuple[str, str, float]:
    s = _normalize_text(line)
    matches = list(PRICE_RX.finditer(s))
    if not matches:
        return s.strip(), "", 0.0

    def price_of(m):
        return _to_float_price(m.group(0))

    with_dec = [m for m in matches if m.group('dec')]
    cands = with_dec if with_dec else matches
    prices = [(price_of(m), m) for m in cands]
    if any(p >= 3.0 for p, _ in prices):
        prices = [(p, m) for p, m in prices if p >= 3.0]

    best_m = max(prices, key=lambda t: t[0])[1]
    price_val = price_of(best_m)

    head = s[: best_m.start()].strip().strip("-:").strip()
    if " - " in head:
        parts = [p.strip() for p in head.split(" - ", 1)]
    else:
        parts = [p.strip() for p in head.rsplit("-", 1)]
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1], price_val
    return head, "", price_val

BULLET_NAME_DESC_RX = re.compile(r"^\s*-\s*(?P<name>[^-].*?)\s*-\s*(?P<desc>.+?)\s*$")
_PUNCT_ONLY_RX = re.compile(r"^[\s\-,.&()]+$")

def _split_name_desc_no_price(line: str) -> Optional[Tuple[str, str]]:
    s = _normalize_text(line)
    if s in {"- -", "-"}:
        return None
    m = BULLET_NAME_DESC_RX.match(s)
    if not m:
        return None
    name = (m.group("name") or "").strip()
    desc = (m.group("desc") or "").strip()
    if not name or _PUNCT_ONLY_RX.fullmatch(name) or not re.search(r"[A-Za-z]", name):
        return None
    name = name.rstrip(",- ").strip()
    desc = desc.rstrip(",- ").strip()
    return name, desc

def _should_attach_as_description(line: str) -> bool:
    if not line:
        return False
    s = line.strip()
    if _PUNCT_ONLY_RX.fullmatch(s):
        return False
    if PRICE_ONLY_RX.match(s):
        return False
    if s.startswith("- "):
        if " - " in s[2:]:
            return False
        words = s[2:].strip().split()
        if 1 <= len(words) <= 4 and s[2:3].isupper():
            return False
    if len(s) <= 100 and (s[0] in "(-&"):
        return True
    low = s.lower()
    if low.startswith(tuple(CONTINUATION_PREFIXES)):
        return True
    if s.endswith(",") or s.endswith("-"):
        return True
    if len(s) <= 24 and s == s.lower():
        return True
    return False

# -----------------------------
# Lines → Categories & Items
# -----------------------------
def _parse_lines_to_categories(lines: List[dict]) -> Tuple[Dict[str, List[dict]], Dict[str, Any]]:
    cats: Dict[str, List[dict]] = {}
    current = "Uncategorized"
    cats[current] = []

    debug: Dict[str, Any] = {
        "version": 13,
        "lines": lines,
        "items": [],
        "assignments": [],
    }

    def new_cat(name: str, src_line: Optional[dict] = None, reason: str = ""):
        nonlocal current, prev_item_ref, prev_item_col, recent_items
        current = _normalize_category_header((name or "Misc").strip())
        cats.setdefault(current, [])
        prev_item_ref = None
        prev_item_col = None
        recent_items = []  # reset recent window on header switch
        if src_line:
            debug["assignments"].append({
                "type": "category_header",
                "category": current,
                "page": src_line.get("page"),
                "y": src_line.get("y"),
                "col": src_line.get("col"),
                "reason": reason or "header detection",
                "line": src_line.get("text"),
                "score": src_line.get("conf"),
            })

    SKIP_TITLES = {"menu", "menu:", "our menu"}
    prev_item_ref: Optional[dict] = None
    prev_item_col: Optional[int] = None
    # Track a small rolling window of recent items in the same column to help attach orphan prices.
    recent_items: List[Tuple[int, dict]] = []  # (col, item_dict)

    def _remember_item(col: int, item: dict):
        # Keep last 3 items per parsing run; they’re already normalized downstream.
        nonlocal recent_items
        recent_items.append((col, item))
        if len(recent_items) > 3:
            recent_items = recent_items[-3:]

    for idx, row in enumerate(lines):
        raw = row["text"]
        if not raw:
            continue
        line = _normalize_text(raw)
        if not line:
            continue

        if idx < 6 and line.lower() in SKIP_TITLES:
            continue

        if line.lower().startswith("category:"):
            new_cat(line.split(":", 1)[1].strip() or "Misc", row, reason="explicit 'Category:'")
            continue
        if _looks_like_section_heading(line):
            new_cat(line.title(), row, reason="heading heuristic (caps/title-case + keywords)")
            continue

        # --- Day 20: handle multi-size "Label: $Price" pairs on one line
        pairs = list(SIZE_PAIR_RX.finditer(line))
        if pairs and len(pairs) >= 2:
            base = SIZE_PAIR_RX.split(line)[0].strip().rstrip(":-").strip() or "Untitled"
            base = _normalize_menu_text(base)
            for m in pairs:
                label = _normalize_menu_text((m.group("label") or "").strip().title())
                price = _to_float_price(m.group("price") or "")
                if price <= 0:
                    continue
                item = {
                    "name": f"{base} ({label})",
                    "description": "",
                    "price": price,
                    "confidence": round(float(row.get("conf", 80)), 1),
                    "raw": raw,
                    "_src_idx": idx, "_page": row.get("page"), "_col": row.get("col"),
                }
                cats[current].append(item)
                debug["items"].append({
                    "name": item["name"], "desc": item["description"], "price": item["price"],
                    "category": current, "confidence": item["confidence"],
                    "source": {"page": row.get("page"), "line_idx": idx, "bbox": None, "matched_rule": "SIZE_PAIR_RX"},
                })
            prev_item_ref = None
            prev_item_col = None
            recent_items = []
            continue

        # --- Price-terminated (Name [- Desc]) ... Price
        name, desc, price = _split_name_desc_price(line)
        if price > 0 and name:
            name = _normalize_menu_text(name)
            desc = _normalize_menu_text(desc)
            item = {
                "name": name, "description": desc, "price": price,
                "confidence": round(float(row.get("conf", 80)), 1),
                "raw": raw, "_src_idx": idx, "_page": row.get("page"), "_col": row.get("col"),
            }
            cats[current].append(item)
            debug["items"].append({
                "name": item["name"], "desc": item["description"], "price": item["price"],
                "category": current, "confidence": item["confidence"],
                "source": {"page": row.get("page"), "line_idx": idx, "bbox": None, "matched_rule": "PRICE_RX_TRAIL+SPLIT"},
            })
            prev_item_ref = item
            prev_item_col = row.get("col")
            _remember_item(prev_item_col, item)
            continue

        # --- Day 20: handle PRICE-ONLY line → attach to nearest recent item in same column
        if PRICE_ONLY_RX.match(line):
            p = _to_float_price(line)
            if p > 0:
                attached = False
                # Prefer immediate previous item, same column
                if prev_item_ref and prev_item_col is not None and row.get("col") == prev_item_col:
                    if float(prev_item_ref.get("price") or 0.0) <= 0.0:
                        prev_item_ref["price"] = p
                        debug["assignments"].append({
                            "type": "price_attach",
                            "to_item": prev_item_ref.get("name"),
                            "category": current,
                            "page": row.get("page"),
                            "line": raw,
                            "score": row.get("conf"),
                            "reason": "price-only line attached to previous item (same column)",
                        })
                        attached = True
                # Fallback: look back up to two earlier items in same column
                if not attached:
                    for col, it in reversed(recent_items[:-1]):  # skip the immediate prev already tested
                        if col == row.get("col") and float(it.get("price") or 0.0) <= 0.0:
                            it["price"] = p
                            debug["assignments"].append({
                                "type": "price_attach_backfill",
                                "to_item": it.get("name"),
                                "category": current,
                                "page": row.get("page"),
                                "line": raw,
                                "score": row.get("conf"),
                                "reason": "price-only line attached to nearest prior item (same column)",
                            })
                            attached = True
                            break
                if attached:
                    # do not create a new item for a lone price line
                    continue
            # fall-through: price line that couldn't be attached → ignore as noise
            continue

        # Bullet "- Name - Desc" with no price (ignore junk bullets)
        nd = _split_name_desc_no_price(line)
        if nd:
            n, d = nd
            n = _normalize_menu_text(n)
            d = _normalize_menu_text(d)
            item = {
                "name": n, "description": d, "price": 0.0,
                "confidence": round(float(row.get("conf", 80)), 1),
                "raw": raw, "_src_idx": idx, "_page": row.get("page"), "_col": row.get("col"),
            }
            cats[current].append(item)
            debug["items"].append({
                "name": item["name"], "desc": item["description"], "price": 0.0,
                "category": current, "confidence": item["confidence"],
                "source": {"page": row.get("page"), "line_idx": idx, "bbox": None, "matched_rule": "BULLET_NAME_DESC"},
            })
            prev_item_ref = item
            prev_item_col = row.get("col")
            _remember_item(prev_item_col, item)
            continue

        # Continuation → description (same column)
        if prev_item_ref and prev_item_col is not None:
            if row.get("col") == prev_item_col and _should_attach_as_description(line):
                old = (prev_item_ref.get("description") or "").strip()
                new_desc = (old + " " + line).strip() if old else line
                new_desc = _normalize_text(new_desc).rstrip(", -").strip()
                new_desc = _normalize_menu_text(new_desc)
                if not _PUNCT_ONLY_RX.fullmatch(new_desc):
                    prev_item_ref["description"] = new_desc
                    debug["assignments"].append({
                        "type": "description_attach", "to_item": prev_item_ref.get("name"),
                        "category": current, "page": row.get("page"), "line": raw,
                        "score": row.get("conf"), "reason": "continuation heuristic (same column)",
                    })
                    continue

        if _PUNCT_ONLY_RX.fullmatch(line):
            continue

        # Freestanding item (no price; keep normalized)
        item = {
            "name": _normalize_menu_text(line), "description": "", "price": 0.0,
            "confidence": round(float(row.get("conf", 80)), 1),
            "raw": raw, "_src_idx": idx, "_page": row.get("page"), "_col": row.get("col"),
        }
        cats[current].append(item)
        debug["items"].append({
            "name": item["name"], "desc": "", "price": 0.0, "category": current,
            "confidence": item["confidence"],
            "source": {"page": row.get("page"), "line_idx": idx, "bbox": None, "matched_rule": "FREESTANDING_LINE"},
        })
        prev_item_ref = item
        prev_item_col = row.get("col")
        _remember_item(prev_item_col, item)

    # Drop empties (keep non-empty)
    cats = {k: [it for it in v if any([it.get("name"), it.get("price"), it.get("description")])] for k, v in cats.items()}
    cats = {k: v for k, v in cats.items() if v}
    if not cats:
        cats = {"Uncategorized": [{"name": "No items recognized", "description": "OCR returned no items.", "price": 0.0}]}
    return cats, debug

# -----------------------------
# Post-processing
# -----------------------------
def _canonical_header(text: str) -> Optional[str]:
    t = (text or "").strip().lower()
    return HEADER_CANON.get(t)

def _fix_common_ocr_name(name: str) -> str:
    s = _normalize_text(name)
    s = re.sub(r"^\s*[-•]+\s*", "", s)
    s = re.sub(r"\s*-\s*$", "", s)
    s = s.rstrip(",")
    s = re.sub(r"\blced\b", "Iced", s, flags=re.I)
    s = re.sub(r"\(\s*\(", "(", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()

def _fix_common_desc(desc: str) -> str:
    if not desc:
        return ""
    d = _normalize_text(desc).rstrip(", -").strip()
    d = re.sub(r"(?:^|\s)-\s-$", "", d)
    return d

def _gather_headings(lines: List[dict]) -> List[dict]:
    heads = []
    for ln in lines:
        txt = _normalize_category_header(_normalize_text(ln.get("text", "")))
        if _looks_like_section_heading(txt):
            canon = _canonical_header(txt.lower()) or txt.title()
            heads.append({"name": canon, "page": ln.get("page"), "col": ln.get("col"), "y": ln.get("y")})
    heads.sort(key=lambda r: (int(r.get("page") or 0), int(r.get("col") or 0), int(r.get("y") or 0)))
    return heads

def _nearest_header_above(heads: List[dict], page: int, col: int, y: int) -> Optional[str]:
    candidates = [h for h in heads if h["page"] == page and h["col"] == col and int(h["y"] or 0) <= y]
    if not candidates:
        return None
    best = max(candidates, key=lambda h: int(h["y"] or 0))
    return best["name"]

def _guess_category(name: str, desc: str = "") -> Optional[str]:
    text = f"{name} {desc}".lower()
    if "(6 pcs)" in text:
        return "Wings"
    best = None
    best_hits = 0
    for cat, kws in SEMANTIC_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in text)
        if hits > best_hits:
            best_hits = hits
            best = cat
    return best if best_hits > 0 else None

def _fold_soda_flavors(name: str, desc: str, raw: str) -> Tuple[str, str]:
    base = name
    if "soda can" not in base.lower():
        return name, desc
    bag = []
    src = " ".join([desc or "", raw or ""]).lower()
    for word in FLAVOR_HINTS:
        if word in src:
            bag.append(word)
    if not bag:
        return name, desc
    seen = []
    for t in bag:
        tt = " ".join(w.capitalize() for w in t.split())
        if tt not in seen:
            seen.append(tt)
    new_name = f"Soda Can - {', '.join(seen)}"
    return new_name, ""

def _merge_pcs(item: dict) -> None:
    raw = (item.get("raw") or "")
    n = (item.get("name") or "")
    has_in_raw = bool(PAREN_PCS_RX.search(raw))
    has_in_name = bool(PAREN_PCS_RX.search(n))
    if has_in_raw and not has_in_name:
        nn = n.rstrip(", -")
        item["name"] = f"{nn} (6 pcs)"

def _strip_private_keys(item: dict) -> dict:
    return {k: v for k, v in item.items() if not str(k).startswith("_")}

def _consolidate_beverages(cats: Dict[str, List[Dict[str, Any]]], debug: Dict[str, Any]) -> None:
    if "Beverages" not in cats:
        return
    items = cats["Beverages"]
    if not items:
        return

    soda_idx = None
    for i, it in enumerate(items):
        if "soda can" in (it.get("name", "").lower()):
            soda_idx = i
            break
    if soda_idx is None:
        return

    base = items[soda_idx]
    flavor_items = []
    for i, it in enumerate(items):
        if i == soda_idx:
            continue
        nm = (it.get("name") or "")
        desc = (it.get("description") or "")
        is_flavorish = any(t in nm.lower() for t in FLAVOR_HINTS) or any(t in desc.lower() for t in FLAVOR_HINTS)
        if is_flavorish:
            flavor_items.append(i)

    if not flavor_items:
        return

    price = next((items[i]["price"] for i in flavor_items if items[i].get("price", 0) > 0), base.get("price", 0.0))
    flavors = []

    def add_flavors_from_text(t: str):
        for token in re.split(r"[,\s]+", t.lower()):
            token = token.strip().strip("-")
            if token in FLAVOR_HINTS:
                tt = " ".join(w.capitalize() for w in token.split())
                if tt not in flavors:
                    flavors.append(tt)

    add_flavors_from_text(base.get("name", ""))
    add_flavors_from_text(base.get("description", ""))
    for i in flavor_items:
        add_flavors_from_text(items[i].get("name", ""))
        add_flavors_from_text(items[i].get("description", ""))

    if not flavors:
        return

    new_name = f"Soda Can - {', '.join(flavors)}"
    base["name"] = new_name
    base["description"] = ""
    if price:
        base["price"] = price

    for i in sorted(set(flavor_items), reverse=True):
        removed = items.pop(i)
        debug.setdefault("assignments", []).append({
            "type": "merge_beverage_flavors",
            "from_item": (removed.get("name") or removed.get("description")),
            "into": new_name,
            "price_used": price,
            "reason": "consolidate soda flavors into Soda Can item (incl. junk bullets)"
        })

def _looks_like_ingredient_list(text: str) -> bool:
    s = (text or "").strip()
    low = s.lower()
    if not s:
        return False
    if "," in s:
        return True
    if low.startswith("with "):
        return True
    words = re.findall(r"[A-Za-z]+", s)
    if words and s == low and 1 <= len(words) <= 6:
        return True
    toppings = {"pepperoni", "mozzarella", "basil", "onion", "onions", "olive", "olives", "peppers", "mushrooms", "bbq"}
    if any(w in low for w in toppings):
        return True
    return False

def _merge_ingredient_prices_in_specialty(cats: Dict[str, List[Dict[str, Any]]], debug: Dict[str, Any]) -> None:
    key = "Specialty Pizzas"
    if key not in cats:
        return
    items = cats[key]
    if not items:
        return

    to_remove = set()
    last_bullet_idx = None
    last_bullet_src = None

    def get_src_idx(it: dict) -> Optional[int]:
        return it.get("_src_idx") if isinstance(it.get("_src_idx"), int) else None

    for i, it in enumerate(items):
        nm = it.get("name", "") or ""
        raw = it.get("raw", "") or ""
        price = float(it.get("price") or 0.0)
        src = get_src_idx(it)

        if raw.strip().startswith("- ") and (price <= 0.0):
            last_bullet_idx = i
            last_bullet_src = src
            continue

        if price > 0 and _looks_like_ingredient_list(nm):
            if last_bullet_idx is not None:
                ok = True
                if last_bullet_src is not None and src is not None:
                    ok = (0 <= (src - last_bullet_src) <= 4)
                if ok:
                    tgt = items[last_bullet_idx]
                    if float(tgt.get("price") or 0.0) <= 0.0:
                        tgt["price"] = price
                    desc = (tgt.get("description") or "").strip()
                    addon = nm
                    if desc:
                        new_desc = f"{desc} {addon}"
                    else:
                        new_desc = addon
                    tgt["description"] = _fix_common_desc(new_desc)
                    to_remove.add(i)
                    debug.setdefault("assignments", []).append({
                        "type": "merge_ingredient_into_bullet",
                        "category": key,
                        "from_item": nm,
                        "into": tgt.get("name"),
                        "price_moved": price,
                        "reason": "priced ingredient line merged into preceding bullet pizza"
                    })
                    continue

        if price > 0:
            last_bullet_idx = None
            last_bullet_src = None

    if to_remove:
        cats[key] = [it for j, it in enumerate(items) if j not in to_remove]

def _cleanup_descriptions(cats: Dict[str, List[Dict[str, Any]]]) -> None:
    for items in cats.values():
        for it in items:
            desc = _fix_common_desc(it.get("description") or "")
            if desc and re.fullmatch(r"[\s,\-]+", desc):
                desc = ""
            it["description"] = desc

def _post_process(cats: Dict[str, List[Dict[str, Any]]], debug: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    lines: List[dict] = debug.get("lines") or []
    heads = _gather_headings(lines)
    idx_map: Dict[int, Tuple[int, int, int]] = {i: (int(ln.get("page") or 0), int(ln.get("col") or 0), int(ln.get("y") or 0)) for i, ln in enumerate(lines)}

    new_cats: Dict[str, List[Dict[str, Any]]] = {}
    for cat_name, items in cats.items():
        norm_cat_name = _normalize_menu_text(cat_name, as_category=True)
        for it in items:
            nm = _normalize_menu_text(_fix_common_ocr_name(it.get("name") or ""))
            ds = _normalize_menu_text(_fix_common_desc(it.get("description") or ""))
            it["name"] = nm
            it["description"] = ds
            _merge_pcs(it)
            it["name"], it["description"] = _fold_soda_flavors(it["name"], it.get("description") or "", it.get("raw") or "")

            tgt_cat = norm_cat_name
            guess = _guess_category(it.get("name", ""), it.get("description", ""))

            if "_src_idx" in it:
                page, col, y = idx_map.get(int(it["_src_idx"] or -1), (None, None, None))
                same_col_hdr = _nearest_header_above(heads, page, col, y) if page is not None else None
            else:
                same_col_hdr = None

            if same_col_hdr:
                tgt_cat = _normalize_menu_text(same_col_hdr, as_category=True)
            elif guess:
                tgt_cat = _normalize_menu_text(guess, as_category=True)

            if cat_name == "Uncategorized" and (it.get("price", 0) or 0) > 0 and tgt_cat != cat_name:
                debug.setdefault("assignments").append({
                    "type": "category_reassign", "from": cat_name, "to": tgt_cat,
                    "item": it.get("name"), "price": it.get("price"),
                    "reason": "semantic guess" if not same_col_hdr else "nearest header above in same column",
                })

            new_cats.setdefault(tgt_cat, []).append(it)

    _merge_ingredient_prices_in_specialty(new_cats, debug)
    _consolidate_beverages(new_cats, debug)
    _cleanup_descriptions(new_cats)
    new_cats = {k: [_strip_private_keys(it) for it in v] for k, v in new_cats.items() if v}
    return new_cats

# -----------------------------
# Image/PDF loader
# -----------------------------
def _load_images_from_path(path: str) -> List:
    p = Path(path)
    if not p.exists():
        return []
    if p.suffix.lower() in [".jpg", ".jpeg", ".png"]:
        import PIL.Image as Image
        img = Image.open(str(p)).convert("RGB")
        return [img]
    if p.suffix.lower() == ".pdf" and convert_from_path is not None:
        return convert_from_path(str(p), dpi=280, poppler_path=os.getenv("POPPLER_PATH") or None)
    return []

# -----------------------------
# Public API
# -----------------------------
def extract_items_from_path(path: str) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    # Canary marker to prove code path
    debug_base = {"canary": OCR_HELPER_CANARY}

    # helpful console breadcrumb
    print(f"[OCR_HELPER] Canary={OCR_HELPER_CANARY} path={path}", file=sys.stderr)

    images = _load_images_from_path(path)
    if not images:
        cats = {"Uncategorized": [{"name":"OCR not configured or file type unsupported","description":"","price":0.0}]}
        dbg = {**debug_base, "version": 13, "lines": [], "items": [], "assignments": [], "note": "no images loaded"}
        return cats, dbg

    all_lines: List[dict] = []
    for im in images:
        im_bgr = None
        try:
            import numpy as np
            im_bgr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR) if cv2 is not None else None
        except Exception:
            im_bgr = None

        if im_bgr is not None:
            im_bgr = _preprocess_image(im_bgr)
            words = _image_to_words(im_bgr)
        else:
            config = "--psm 6 -l eng"
            try:
                df = pytesseract.image_to_data(im, output_type=Output.DATAFRAME, config=config)  # type: ignore
                df = df[(df.conf != -1) & df.text.notna()]
                words = [{
                    "text": str(r["text"]),
                    "conf": float(r["conf"]),
                    "left": int(r["left"]), "top": int(r["top"]),
                    "width": int(r["width"]), "height": int(r["height"]),
                    "cx": int(r["left"]) + int(r["width"]) / 2.0,
                    "cy": int(r["top"]) + int(r["height"]) / 2.0,
                    "page_num": int(r.get("page_num", 0))
                } for _, r in df.iterrows()]
            except Exception:
                words = []

        labels = _cluster_columns(words, k=2)
        lines = _group_lines(words, labels)
        all_lines.extend(lines)

    merged_lines = _merge_broken_lines(all_lines)
    cats, debug = _parse_lines_to_categories(merged_lines)
    debug["pre_merged_line_count"] = len(all_lines)
    debug["post_merged_line_count"] = len(merged_lines)

    # merge canary into debug payload
    debug = {**debug_base, **debug}

    cats = _post_process(cats, debug)
    return cats, debug
