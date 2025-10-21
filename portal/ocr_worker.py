# portal/ocr_worker.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
import re
from PIL import Image, ImageOps
import pytesseract
import numpy as np
import cv2

# -------- version banner (so you can verify on server start) ----------
OCR_WORKER_VERSION = "Day19 step5 / CLAHE+denoise+unsharp / psm4 / price-fixes + debug save"
print(f"[OCR] Loaded ocr_worker.py -> {OCR_WORKER_VERSION}")

# Debug: save a preprocessed preview next to the input image
DEBUG_SAVE_PRE: bool = True

# Point pytesseract to your install path if needed
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ======================================================================
#                         PREPROCESSING
# ======================================================================

def _ensure_gray_np(img: Image.Image) -> np.ndarray:
    """PIL Image -> uint8 grayscale numpy array."""
    return np.array(ImageOps.grayscale(img), dtype=np.uint8)

def _unsharp(img_u8: np.ndarray, radius: int = 1, amount: float = 1.5) -> np.ndarray:
    """Simple unsharp mask using Gaussian blur."""
    blur = cv2.GaussianBlur(img_u8, (0, 0), sigmaX=radius, sigmaY=radius)
    sharp = cv2.addWeighted(img_u8, 1 + amount, blur, -amount, 0)
    return np.clip(sharp, 0, 255).astype(np.uint8)

def _threshold_with_fallback(gray: np.ndarray, adaptive_block: int = 31, adaptive_C: int = 9) -> np.ndarray:
    """
    Try adaptive threshold first; if too dark/too light, fall back to Otsu.
    Returns 0/255 uint8 binary image (text = black, background = white).
    """
    bw_adap = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, adaptive_block, adaptive_C
    )

    white_ratio = float((bw_adap == 255).sum()) / bw_adap.size
    if 0.15 < white_ratio < 0.95:
        return bw_adap

    _, bw_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return bw_otsu

def _prep_cv(pil: Image.Image, source_path: Optional[Path] = None) -> np.ndarray:
    """
    Robust preprocessing:
      - upscale small images a bit
      - grayscale
      - denoise (median + light NLM)
      - CLAHE contrast
      - unsharp
      - threshold (adaptive with Otsu fallback)
      - remove tiny speckles (morph open)
    Returns: binary uint8 (0/255), text roughly black (0), bg white (255).

    If DEBUG_SAVE_PRE is True and a source_path is provided, saves a
    '<name>.preprocessed.png' file next to the original for visual inspection.
    """
    w, h = pil.size
    scale = 1.6 if max(w, h) < 1600 else 1.0
    if scale != 1.0:
        pil = pil.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    gray = _ensure_gray_np(pil)

    # Denoise — median for salt/pepper
    gray = cv2.medianBlur(gray, 3)
    # Light NLM denoise (fast path)
    try:
        gray = cv2.fastNlMeansDenoising(gray, None, h=7, templateWindowSize=7, searchWindowSize=21)
    except Exception:
        pass

    # CLAHE (adaptive contrast)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    # Unsharp (tighten glyph edges)
    gray = _unsharp(gray, radius=1, amount=1.2)

    # Threshold with fallback
    bw = _threshold_with_fallback(gray, adaptive_block=31, adaptive_C=9)

    # Remove tiny speckles (noise)
    kernel = np.ones((2, 2), np.uint8)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel, iterations=1)

    # --- DEBUG: save preprocessed image for visual confirmation ---
    try:
        if DEBUG_SAVE_PRE and source_path:
            out_path = Path(source_path).with_suffix(".preprocessed.png")
            Image.fromarray(bw).save(str(out_path))
            print(f"[OCR] Saved preprocessed preview -> {out_path}")
    except Exception as _e:
        print(f"[OCR] (warn) could not save preview: {_e}")

    return bw

# ======================================================================
#                         COLUMN SPLITTING
# ======================================================================

def _split_columns(bw: np.ndarray) -> List[np.ndarray]:
    """
    Light-weight column splitter using vertical whitespace.
    Works well on 2–3 column menus. Returns list of column images.
    """
    vproj = (bw == 255).sum(axis=0)  # white pixels per column
    thresh = 0.985 * bw.shape[0]

    gaps = (vproj > thresh).astype(np.uint8)
    cols: List[Tuple[int, int]] = []

    start = 0
    i = 0
    while i < gaps.size:
        if gaps[i]:
            j = i
            while j < gaps.size and gaps[j]:
                j += 1
            if (j - i) > 24:
                cols.append((start, i))
                start = j
            i = j
        else:
            i += 1
    cols.append((start, gaps.size))

    regions: List[np.ndarray] = []
    for x0, x1 in cols:
        pad = 8
        x0 = max(0, x0 - pad)
        x1 = min(bw.shape[1], x1 + pad)
        region = bw[:, x0:x1]
        if region.shape[1] >= 120:
            regions.append(region)

    return regions if len(regions) >= 2 else [bw]

# ======================================================================
#                         OCR CALL
# ======================================================================

def _ocr_block(bw_block: np.ndarray) -> str:
    pil = Image.fromarray(bw_block)
    # psm 4: single column of text of variable sizes — ideal for our column chunks
    config = "--oem 3 --psm 4 -l eng -c preserve_interword_spaces=1"
    return pytesseract.image_to_string(pil, config=config)

def ocr_image(image_path: Path) -> str:
    print(f"[OCR] ocr_image() called with: {image_path}")
    pil = Image.open(image_path)
    bw = _prep_cv(pil, source_path=image_path)
    blocks = _split_columns(bw)
    print(f"[OCR] blocks detected: {len(blocks)}")
    texts = [_ocr_block(b) for b in blocks]
    raw = "\n".join(t.strip() for t in texts if t.strip())
    return _postprocess_ocr_text(raw)

# ======================================================================
#                     NORMALIZATION & POST-FIX
# ======================================================================

def _normalize_text_basic(s: str) -> str:
    repl = {
        "\u201c": '"', "\u201d": '"', "\u2019": "'", "\u2018": "'",
        "\u2013": "-", "\u2014": "-", "\u00b7": "·", "\u2026": "...",
        "”": '"', "“": '"', "’": "'", "‘": "'",
        "—": "-", "–": "-",
        "OZ": "oz", "Oz": "oz",
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    s = re.sub(r"[·\.]{3,}", " ", s)      # dot leaders → single space
    s = re.sub(r"[ \t]+", " ", s)         # collapse spaces
    s = "\n".join(ln for ln in s.splitlines() if not re.fullmatch(r"[-=_.]{3,}", ln.strip()))
    return s

_PRICE_FIXES = [
    (re.compile(r"(\d+)\s*[·\.\,]\s*(\d{2})(?!\d)"), r"\1.\2"),  # 12 . 99 → 12.99
    (re.compile(r"(\d+),(\d{2})(?!\d)"), r"\1.\2"),              # 12,99 → 12.99
    (re.compile(r"\$\s+(\d)"), r"$\1"),                          # $ 12.99 → $12.99
    (re.compile(r"\${2,}"), r"$"),                               # $$12.99 → $12.99
    (re.compile(r"\bS\s*(\d{1,3}(?:\.\d{2})?)"), r"$\1"),        # S12.99 → $12.99
    (re.compile(r"(?<=\d)O(?=[\d\.])"), "0"),                    # O in numeric contexts → 0
    (re.compile(r"(?<=\$)O"), "0"),
    (re.compile(r"(?<=\d)l(?=\d)"), "1"),                        # l between digits → 1
    (re.compile(r"(\d)\s*[\.·]\s*(\d{1,2})\b"), r"\1.\2"),       # 9 . 5 0 → 9.50
]

def _postprocess_ocr_text(s: str) -> str:
    if not s:
        return s

    out = _normalize_text_basic(s)

    fixed_lines = []
    for line in out.splitlines():
        ln = line
        for rx, repl in _PRICE_FIXES:
            ln = rx.sub(repl, ln)
        ln = re.sub(r"\s+([,:;\.])", r"\1", ln)  # trim space before punctuation
        ln = re.sub(r"\s{2,}", " ", ln).strip()
        fixed_lines.append(ln)

    return "\n".join(fixed_lines).strip()

# ======================================================================
#                         PARSING
# ======================================================================

def _fix_cents(p: float) -> float:
    """If OCR drops the decimal (e.g., 475 for 4.75), nudge it."""
    return p / 100.0 if 100.0 <= p < 1000.0 else p

PRICE_TAIL = r"(?:\$|S)?\s*(?P<price>\d{1,3}(?:\.\d{2})?)\s*$"
PRICE_RE = re.compile(rf"(?ix) (?:^|[\s\.\-–—\(\)]+) \$?\s*(\d{{1,3}}(?:\.\d{{2}})?) \s*(?:$|[^0-9])")

KNOWN_CATS = [
    "PIZZA",
    "TOPPINGS",
    "PERSONAL PIES",
    "PIZZA BY THE SLICE",
    "PIZZA BY",
    "THE SLICE",
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
    m = re.match(r'^(?P<base>.+?)\s+(?P<pairs>(?:\d{1,2}"\s*(?:\$|S)?\s*\d{1,3}(?:\.\d{2})?\s*){2,})$', line)
    if not m:
        return None
    base = m.group("base").strip(" .-")
    pairs = re.findall(r'(\d{1,2}")\s*(?:\$|S)?\s*(\d{1,3}(?:\.\d{2})?)', m.group("pairs"))
    if not pairs:
        return None
    return base, [(sz, _fix_cents(float(p))) for sz, p in pairs]

def _maybe_topping_price(line: str) -> tuple[str, float] | None:
    m = re.search(r'(?i)\b(\d\/2|1\/2|1|2)\s+Topping(s)?\s*(?:\$|S)?\s*(\d{1,3}(?:\.\d{2})?)', line)
    if not m:
        return None
    label = m.group(1).replace("1/2", "Half").replace("2", "Two").replace("1", "One")
    price = _fix_cents(float(m.group(3)))
    return f"{label} Topping", price

def parse_menu_text(text: str) -> Dict[str, Any]:
    text = _normalize_text_basic(text)
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

        if pending_heading:
            start_category(pending_heading)

        if current["name"].lower().startswith("topping"):
            if "," in ln and not PRICE_RE.search(ln):
                toppings_buffer.extend([p.strip() for p in ln.split(",") if p.strip()])
                continue
            mp = _maybe_topping_price(ln)
            if mp:
                topping_prices.append(mp)
                continue
            continue

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

        m_end = re.search(rf"^(?P<name>.+?)\s*{PRICE_TAIL}", ln)
        m = m_end if m_end else re.search(rf"^(?P<name>.+?)\s+(?:\.+\s+)?{PRICE_TAIL}", ln)

        if m:
            nm = m.group("name").strip(" .-")
            pr = _fix_cents(float(m.group("price")))
            add_item(nm, pr, None)
            continue

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

        if current["items"] and len(ln.split()) <= 16 and not PRICE_RE.search(ln):
            last = current["items"][-1]
            last["description"] = (last["description"] + " " + ln).strip() if last["description"] else ln
            continue

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

    categories = [c for c in categories if c["items"]]

    merged: Dict[str, Dict[str, Any]] = {}
    for c in categories:
        key = c["name"].rstrip("s").lower()
        if key not in merged:
            merged[key] = {"name": c["name"], "items": []}
        merged[key]["items"].extend(c["items"])
    categories = list(merged.values())

    return {"categories": categories}

# ======================================================================
#                         DRAFT BUILD + PIPELINE
# ======================================================================

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
    print(f"[OCR] run_image_pipeline(image_path={image_path}, job_id={job_id})")
    raw = ocr_image(image_path)
    draft = build_draft(job_id=job_id, source_filename=image_path.name, text=raw)
    return raw, draft
