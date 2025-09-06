# portal/ocr_worker.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Tuple, List
import re
from PIL import Image, ImageFilter, ImageOps
import pytesseract
import numpy as np
import cv2

# Point pytesseract to your install path if needed
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ---------- preprocessing ----------
def _prep_cv(img: Image.Image) -> np.ndarray:
    # upscale small images
    w, h = img.size
    scale = 1.6 if max(w, h) < 1600 else 1.0
    if scale != 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # grayscale, denoise, equalize, adaptive threshold
    gray = np.array(ImageOps.grayscale(img))
    gray = cv2.medianBlur(gray, 3)
    gray = cv2.equalizeHist(gray)
    bw = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 9
    )
    return bw

def _split_columns(bw: np.ndarray) -> List[np.ndarray]:
    """
    Very light-weight column splitter using vertical whitespace.
    Works well on 2-column menus like your example.
    """
    vproj = (bw == 255).sum(axis=0)  # white pixels per column
    thresh = 0.98 * bw.shape[0]
    gaps = (vproj > thresh).astype(np.uint8)

    cols = []
    start = 0
    i = 0
    while i < gaps.size:
        if gaps[i]:
            j = i
            while j < gaps.size and gaps[j]:
                j += 1
            if (j - i) > 20:  # wide gap => split
                cols.append((start, i))
                start = j
            i = j
        else:
            i += 1
    cols.append((start, gaps.size))

    regions = []
    for x0, x1 in cols:
        pad = 6
        x0 = max(0, x0 - pad)
        x1 = min(bw.shape[1], x1 + pad)
        region = bw[:, x0:x1]
        if region.shape[1] >= 100:
            regions.append(region)
    return regions if len(regions) >= 2 else [bw]

def _ocr_block(bw_block: np.ndarray) -> str:
    pil = Image.fromarray(bw_block)
    # PSM 3 (auto page segmentation) helps on multi-column blocks; keep spaces
    config = "--oem 3 --psm 3 -l eng -c preserve_interword_spaces=1"
    return pytesseract.image_to_string(pil, config=config)

def ocr_image(image_path: Path) -> str:
    pil = Image.open(image_path)
    bw = _prep_cv(pil)
    blocks = _split_columns(bw)
    texts = [_ocr_block(b) for b in blocks]
    return "\n".join(t.strip() for t in texts if t.strip())

# ---------- normalization ----------
def _normalize_text(s: str) -> str:
    repl = {
        "\u201c": '"', "\u201d": '"', "\u2019": "'", "\u2018": "'",
        "\u2013": "-", "\u2014": "-", "\u00b7": "·", "\u2026": "...",
        "”": '"', "“": '"', "’": "'", "‘": "'",
        "—": "-", "–": "-",
        "OZ": "oz", "Oz": "oz",
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    # dot leaders -> one space
    s = re.sub(r"[·\.]{3,}", " ", s)
    # OCR sometimes reads $ as S
    s = re.sub(r"\bS\s*(\d+\.\d{2})", r"$\1", s)
    # collapse spaces
    s = re.sub(r"[ \t]+", " ", s)
    # remove long decorative lines
    s = "\n".join(ln for ln in s.splitlines() if not re.fullmatch(r"[-=_.]{3,}", ln.strip()))
    return s

# ---------- price helpers ----------
def _fix_cents(p: float) -> float:
    """
    If OCR drops the decimal (e.g., 475 for 4.75), nudge it.
    Treat 100-999 as 'X.YY' cents. Leave legit big pies (>= 1000) alone.
    """
    return p / 100.0 if 100.0 <= p < 1000.0 else p

# ---------- parsing ----------
# Tail with a NAMED price group (fixes the crash you hit)
PRICE_TAIL = r"(?:\$|S)?\s*(?P<price>\d{1,3}(?:\.\d{2})?)\s*$"

# loose price detector for "short non-price" checks
PRICE_RE = re.compile(rf"(?ix) (?:^|[\s\.\-–—\(\)]+) \$?\s*(\d{{1,3}}(?:\.\d{{2}})?) \s*(?:$|[^0-9])")

# category cues we expect (helps avoid false positives)
KNOWN_CATS = [
    "PIZZA",
    "TOPPINGS",
    "PERSONAL PIES",
    "PIZZA BY THE SLICE",
    "PIZZA BY",          # first half of split heading
    "THE SLICE",         # second half of split heading
    "DESSERTS",
]

NOISE_LINES = [
    r"^additional charge", r"^topping charge",
    r"^tax(es)? not included", r"^prices subject to change",
    r"^available .+ only$",
]

TOPPING_WORDS = [
    "sausage","meatball","pepperoni","ham","peppers","mushrooms","onion",
    "black olives","garlic","extra cheese","anchovies"
]

def _is_noise(line: str) -> bool:
    s = line.strip().lower()
    if not s:
        return True
    for pat in NOISE_LINES:
        if re.search(pat, s, flags=re.I):
            return True
    if len(s) <= 1:
        return True
    return False

def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if any(s.upper() == k for k in KNOWN_CATS):
        return True
    # short, mostly uppercase
    letters = re.sub(r"[^A-Za-z]", "", s)
    if letters and (s.upper() == s) and len(s) <= 28:
        return True
    return False

def _extract_name_and_size(name: str) -> tuple[str, str | None]:
    m = re.search(r"\(([^)]+)\)\s*$", name)
    if m:
        return name[:m.start()].strip(), m.group(1).strip()
    return name.strip(), None

def _parse_same_line_sizes(line: str) -> tuple[str, List[tuple[str, float]]] | None:
    """
    Matches: 'Margherita 12" $13.00 18" $31.25'
    Returns: ('Margherita', [('12"', 13.00), ('18"', 31.25)])
    """
    m = re.match(r'^(?P<base>.+?)\s+(?P<pairs>(?:\d{1,2}"\s*(?:\$|S)?\s*\d{1,3}(?:\.\d{2})?\s*){2,})$', line)
    if not m:
        return None
    base = m.group("base").strip(" .-")
    pairs = re.findall(r'(\d{1,2}")\s*(?:\$|S)?\s*(\d{1,3}(?:\.\d{2})?)', m.group("pairs"))
    if not pairs:
        return None
    return base, [(sz, _fix_cents(float(p))) for sz, p in pairs]

def _maybe_topping_price(line: str) -> tuple[str, float] | None:
    # "1/2 Topping $4.75", "1 Topping $6.50", "2 Toppings $8.75"
    m = re.search(r'(?i)\b(\d\/2|1\/2|1|2)\s+Topping(s)?\s*(?:\$|S)?\s*(\d{1,3}(?:\.\d{2})?)', line)
    if not m:
        return None
    label = m.group(1).replace("1/2", "Half").replace("2", "Two").replace("1", "One")
    price = _fix_cents(float(m.group(3)))
    return f"{label} Topping", price

def parse_menu_text(text: str) -> Dict[str, Any]:
    text = _normalize_text(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    categories: List[Dict[str, Any]] = []
    current = {"name": "Menu", "items": []}
    categories.append(current)

    pending_heading: str | None = None
    toppings_buffer: List[str] = []
    topping_prices: List[tuple[str, float]] = []

    def start_category(name: str):
        nonlocal current, pending_heading
        current = {"name": name.title(), "items": []}
        categories.append(current)
        pending_heading = None

    def add_item(name: str, price: float, maybe_size: str | None, desc: str = ""):
        base, size = _extract_name_and_size(name)
        size = size or maybe_size or "One Size"
        current["items"].append({
            "name": base or "Untitled",
            "description": desc.strip(),
            "sizes": [{"name": size, "price": float(price)}],
            "options": [],
            "tags": [],
        })

    for ln in lines:
        if _is_noise(ln):
            continue

        # merge split heading: "PIZZA BY" then "THE SLICE"
        if _is_heading(ln):
            up = ln.upper()
            if up in ("PIZZA BY", "THE SLICE"):
                if pending_heading is None:
                    pending_heading = up
                    continue
                else:
                    combined = f"{pending_heading} {up}".replace("BY THE", "BY THE").title()
                    start_category(combined)
                    continue
            start_category(ln)
            continue

        # if we had a single pending heading but next line wasn't a heading, start it now
        if pending_heading:
            start_category(pending_heading)

        # special handling while inside Toppings section:
        if current["name"].lower().startswith("topping"):
            if "," in ln and not PRICE_RE.search(ln):
                toppings_buffer.extend([p.strip() for p in ln.split(",") if p.strip()])
                continue
            mp = _maybe_topping_price(ln)
            if mp:
                topping_prices.append(mp)
                continue
            continue

        # sizes on same line (e.g., Margherita 12" $13.00 18" $31.25)
        same = _parse_same_line_sizes(ln)
        if same:
            base, pairs = same
            for sz, pr in pairs:
                current["items"].append({
                    "name": f"{base} ({sz})",
                    "description": "",
                    "sizes": [{"name": sz, "price": pr}],
                    "options": [],
                    "tags": [],
                })
            continue

        # standard: name ... price
        m = None
        m_end = re.search(rf"^(?P<name>.+?)\s*{PRICE_TAIL}", ln)
        if m_end:
            m = m_end
        else:
            m = re.search(rf"^(?P<name>.+?)\s+(?:\.+\s+)?{PRICE_TAIL}", ln)

        if m:
            nm = m.group("name").strip(" .-")
            pr = _fix_cents(float(m.group("price")))
            add_item(nm, pr, None)
            continue

        # handle “12" $13.00 18" $31.25” on the next line after a base item
        if current["items"]:
            sizes = re.findall(r'(\d{1,2}")\s*(?:\$|S)?\s*(\d{1,3}(?:\.\d{2})?)', ln)
            if sizes:
                last = current["items"].pop()
                base = last["name"]
                desc = last.get("description", "")
                for sz, price in sizes:
                    current["items"].append({
                        "name": f"{base} ({sz})",
                        "description": desc,
                        "sizes": [{"name": sz, "price": _fix_cents(float(price))}],
                        "options": [],
                        "tags": [],
                    })
                continue

        # otherwise a short non-price line is a description for the last item
        if current["items"] and len(ln.split()) <= 16 and not PRICE_RE.search(ln):
            last = current["items"][-1]
            last["description"] = (last["description"] + " " + ln).strip() if last["description"] else ln
            continue

    # If we ended inside Toppings, persist as a structured item
    if categories and categories[-1]["name"].lower().startswith("topping"):
        if toppings_buffer or topping_prices:
            opts = [{"name": t} for t in toppings_buffer]
            surcharge = ", ".join([f"{lbl}: ${price:.2f}" for lbl, price in topping_prices]) if topping_prices else ""
            categories[-1]["items"].append({
                "name": "Additional Toppings",
                "description": surcharge,
                "sizes": [{"name": "Per Pie", "price": 0.0}],
                "options": opts,
                "tags": []
            })

    # drop empty categories
    categories = [c for c in categories if c["items"]]

    # merge pluralization variants
    merged: Dict[str, Dict[str, Any]] = {}
    for c in categories:
        key = c["name"].rstrip("s").lower()
        if key not in merged:
            merged[key] = {"name": c["name"], "items": []}
        merged[key]["items"].extend(c["items"])
    categories = list(merged.values())

    return {"categories": categories}

def build_draft(job_id: str, source_filename: str, text: str) -> Dict[str, Any]:
    parsed = parse_menu_text(text)
    nonempty = len([ln for ln in text.splitlines() if ln.strip()])
    item_count = sum(len(c["items"]) for c in parsed["categories"])
    denom = max(6, nonempty / 8.0)
    conf = 0.0 if nonempty == 0 else min(1.0, item_count / denom)

    from datetime import datetime, timezone
    return {
        "job_id": job_id,
        "restaurant_id": None,
        "currency": "USD",
        "categories": parsed["categories"],
        "source": {
            "type": "upload",
            "file": source_filename,
            "ocr_engine": "tesseract+columns",
            "confidence": round(conf, 2),
        },
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }

def run_image_pipeline(image_path: Path, job_id: str) -> Tuple[str, Dict[str, Any]]:
    raw = ocr_image(image_path)
    draft = build_draft(job_id=job_id, source_filename=image_path.name, text=raw)
    return raw, draft
