# portal/ocr_worker.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
import re
from PIL import Image, ImageOps
import pytesseract
import numpy as np
import cv2

# -------- version banner (visible on server start) ----------
OCR_WORKER_VERSION = "Day22 / grayscale-first + upscale + psm6→psm3 fallback / debug-save + multi-price parse v3.7 (fix: multi-token size header) [legacy auto-rotate disabled]"
print(f"[OCR] Loaded ocr_worker.py -> {OCR_WORKER_VERSION}")

# Debug saves
DEBUG_SAVE_PRE: bool = True      # save preprocessed rasters
DEBUG_SAVE_TEXT: bool = True     # save OCR text (primary+fallback) next to the image

# Point pytesseract to your install path if needed
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ======================================================================
#                         OCR CONFIG (unified)
# ======================================================================

# Primary (grayscale, preserve spaces helps dot-leaders & wide gaps)
OCR_CONFIG_MAIN = "--oem 3 --psm 6 -l eng -c preserve_interword_spaces=1"
# Fallback: auto layout sometimes rescues mixed/odd scans
OCR_CONFIG_FALLBACK = "--oem 3 --psm 3 -l eng -c preserve_interword_spaces=1"

# ======================================================================
#                    ORIENTATION CONTROL (disable legacy)
# ======================================================================

# We now normalize orientation upstream in servline/storage/ocr_facade.py.
# Keep this True to prevent double-rotation here.
DISABLE_LEGACY_AUTOROTATE: bool = True

# ======================================================================
#                         PREPROCESSING
# ======================================================================

def _ensure_gray_np(img: Image.Image) -> np.ndarray:
    """PIL Image -> uint8 grayscale numpy array."""
    return np.array(ImageOps.grayscale(img), dtype=np.uint8)

def _unsharp(img_u8: np.ndarray, radius: int = 1, amount: float = 1.2) -> np.ndarray:
    """Simple unsharp mask using Gaussian blur (light touch to preserve strokes)."""
    blur = cv2.GaussianBlur(img_u8, (0, 0), sigmaX=radius, sigmaY=radius)
    sharp = cv2.addWeighted(img_u8, 1 + amount, blur, -amount, 0)
    return np.clip(sharp, 0, 255).astype(np.uint8)

def _rotate_any(gray: np.ndarray, angle_deg: float) -> np.ndarray:
    """Rotate image by arbitrary degrees around center, keeping full canvas."""
    if abs(angle_deg) < 0.5:
        return gray
    (h, w) = gray.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    cos = abs(M[0, 0]); sin = abs(M[0, 1])
    nW = int((h * sin) + (w * cos))
    nH = int((h * cos) + (w * sin))
    M[0, 2] += (nW / 2) - center[0]
    M[1, 2] += (nH / 2) - center[1]
    return cv2.warpAffine(gray, M, (nW, nH), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

def _auto_orient(gray: np.ndarray) -> np.ndarray:
    """
    (LEGACY) Use Tesseract OSD to auto-rotate if needed.
    Disabled by default; rotation now handled upstream to avoid double-rotation.
    """
    if DISABLE_LEGACY_AUTOROTATE:
        print("[Auto-rotate] Skipped in ocr_worker (handled upstream by ocr_facade)")
        return gray

    try:
        osd = pytesseract.image_to_osd(gray, config="--psm 0")
        m = re.search(r"Rotate:\s*([0-9]+)", osd)
        if m:
            angle = int(m.group(1)) % 360
            return _rotate_any(gray, -float(angle))  # Tesseract clockwise → cv2 CCW
    except Exception as _e:
        print(f"[OCR] (info) OSD orientation not applied: {_e}")
    return gray

def _threshold_with_fallback(gray: np.ndarray, adaptive_block: int = 31, adaptive_C: int = 9) -> np.ndarray:
    """Adaptive threshold with Otsu fallback → binary (0/255)."""
    bw_adap = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, adaptive_block, adaptive_C
    )
    white_ratio = float((bw_adap == 255).sum()) / bw_adap.size
    if 0.15 < white_ratio < 0.95:
        return bw_adap
    _, bw_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return bw_otsu

def _prep_images(pil: Image.Image, source_path: Optional[Path] = None) -> Tuple[np.ndarray, np.ndarray]:
    """
    Robust preprocessing producing BOTH:
      - gray_proc: clean grayscale for OCR
      - bw: binary for column splitting / debug
    """
    w, h = pil.size
    long_side = max(w, h)
    scale = 1.0
    if long_side < 1200:
        scale = 2.5
    elif long_side < 1600:
        scale = 2.0
    if scale != 1.0:
        pil = pil.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    gray = _ensure_gray_np(pil)
    gray = cv2.medianBlur(gray, 3)
    try:
        gray = cv2.fastNlMeansDenoising(gray, None, h=5, templateWindowSize=7, searchWindowSize=21)
    except Exception:
        pass
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    gray = _unsharp(gray, radius=1, amount=1.1)
    gray = _auto_orient(gray)

    bw = _threshold_with_fallback(gray, adaptive_block=31, adaptive_C=9)
    kernel = np.ones((2, 2), np.uint8)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel, iterations=1)

    if DEBUG_SAVE_PRE and source_path:
        try:
            base = source_path.with_suffix("")
            Image.fromarray(gray).save(str(base.parent / f"{base.name}.pre_gray.png"))
            Image.fromarray(bw).save(str(base.parent / f"{base.name}.pre_bw.png"))
            print(f"[OCR] Saved preprocess previews -> {base.name}.pre_gray.png / .pre_bw.png")
        except Exception as _e:
            print(f"[OCR] (warn) could not save preview images: {_e}")

    return gray, bw

# --- Legacy API shim for routes_debug_preocr ---------------------------------
def _prep_cv(pil: Image.Image, source_path: Optional[Path] = None) -> np.ndarray:
    """Back-compat: return the binary (bw) for the debug route."""
    gray, bw = _prep_images(pil, source_path=source_path)
    return bw

# ======================================================================
#                         COLUMN SPLITTING
# ======================================================================

def _column_spans(bw: np.ndarray) -> List[Tuple[int, int]]:
    """Whitespace-based column splitter → spans [(x0,x1), ...]."""
    vproj = (bw == 255).sum(axis=0)
    thresh = 0.985 * bw.shape[0]
    gaps = (vproj > thresh).astype(np.uint8)
    spans: List[Tuple[int, int]] = []
    start = 0
    i = 0
    while i < gaps.size:
        if gaps[i]:
            j = i
            while j < gaps.size and gaps[j]:
                j += 1
            if (j - i) > 24:
                spans.append((start, i))
                start = j
            i = j
        else:
            i += 1
    spans.append((start, gaps.size))

    padded: List[Tuple[int, int]] = []
    for x0, x1 in spans:
        pad = 8
        x0 = max(0, x0 - pad)
        x1 = min(bw.shape[1], x1 + pad)
        if (x1 - x0) >= 120:
            padded.append((x0, x1))
    return padded if len(padded) >= 2 else [(0, bw.shape[1])]

def _extract_grayscale_blocks(gray: np.ndarray, spans: List[Tuple[int, int]]) -> List[np.ndarray]:
    """Cut grayscale blocks using spans from the binary splitter."""
    return [gray[:, x0:x1] for (x0, x1) in spans]

# ======================================================================
#                         OCR + QUALITY / FALLBACK
# ======================================================================

_PRICE_TOKEN = re.compile(r"\$?\d{1,3}(?:[.,]\d{2})\b")

def _letters_ratio(s: str) -> float:
    if not s:
        return 0.0
    letters = sum(1 for c in s if c.isalpha())
    return letters / max(1, len(s))

def _quality_score(s: str) -> float:
    if not s:
        return 0.0
    lr = _letters_ratio(s)
    price_hits = len(_PRICE_TOKEN.findall(s))
    length = max(50, len(s))
    price_component = min(1.0, (price_hits * 8.0) / length)
    return 0.7 * lr + 0.3 * price_component

def _ocr_block_gray(gray_block: np.ndarray, config: str) -> str:
    pil = Image.fromarray(gray_block)
    return pytesseract.image_to_string(pil, config=config)

def _run_ocr_with_config(blocks_gray: List[np.ndarray], config: str) -> str:
    texts = [_ocr_block_gray(b, config=config) for b in blocks_gray]
    return "\n".join(t.strip() for t in texts if t.strip())

def ocr_image(image_path: Path) -> str:
    print(f"[OCR] ocr_image() called with: {image_path}")
    pil = Image.open(image_path)
    gray, bw = _prep_images(pil, source_path=image_path)

    spans = _column_spans(bw)
    blocks_gray = _extract_grayscale_blocks(gray, spans)
    print(f"[OCR] column blocks detected: {len(blocks_gray)}")

    text_main = _run_ocr_with_config(blocks_gray, OCR_CONFIG_MAIN)
    score_main = _quality_score(text_main)

    need_fallback = (score_main < 0.48) or (_letters_ratio(text_main) < 0.52)
    text_best, used = text_main, "psm6"
    if need_fallback:
        text_fb = _run_ocr_with_config(blocks_gray, OCR_CONFIG_FALLBACK)
        score_fb = _quality_score(text_fb)
        if score_fb > score_main * 1.05:
            text_best, used = text_fb, "psm3"
        print(f"[OCR] fallback tried (main={score_main:.3f}, fb={score_fb:.3f}) -> using {used}")
    else:
        print(f"[OCR] fallback not needed (main score={score_main:.3f}) -> using psm6")

    try:
        if DEBUG_SAVE_TEXT:
            base = image_path.with_suffix("")
            (base.parent / f"{base.name}.ocr_main.txt").write_text(text_main, encoding="utf-8", errors="ignore")
            if need_fallback:
                (base.parent / f"{base.name}.ocr_fallback.txt").write_text(text_fb, encoding="utf-8", errors="ignore")
            (base.parent / f"{base.name}.ocr_used_{used}.txt").write_text(text_best, encoding="utf-8", errors="ignore")
    except Exception as _e:
        print(f"[OCR] (warn) could not save debug text: {_e}")

    return _postprocess_ocr_text(text_best)

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
        "}": ")",  # fix stray curly
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    s = re.sub(r"[·\.]{3,}", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = "\n".join(ln for ln in s.splitlines() if not re.fullmatch(r"[-=_.]{3,}", ln.strip()))
    return s

_PRICE_FIXES = [
    (re.compile(r"(\d+)\s*[·\.\,]\s*(\d{2})(?!\d)"), r"\1.\2"),
    (re.compile(r"(\d+),(\d{2})(?!\d)"), r"\1.\2"),
    (re.compile(r"\$\s+(\d)"), r"$\1"),
    (re.compile(r"\${2,}"), r"$"),
    (re.compile(r"\bS\s*(\d{1,3}(?:\.\d{2})?)"), r"$\1"),
    (re.compile(r"(?<=\d)O(?=[\d\.])"), "0"),
    (re.compile(r"(?<=\$)O"), "0"),
    (re.compile(r"(?<=\d)l(?=\d)"), "1"),
    (re.compile(r"(\d)\s*[\.·]\s*(\d{1,2})\b"), r"\1.\2"),
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
        ln = re.sub(r"\s+([,:;\.])", r"\1", ln)
        ln = re.sub(r"\s{2,}", " ", ln).strip()
        fixed_lines.append(ln)
    return "\n".join(fixed_lines).strip()

# ======================================================================
#                         PARSING
# ======================================================================

def _fix_cents(p: float) -> float:
    return p / 100.0 if 100.0 <= p < 1000.0 else p

PRICE_TAIL = r"(?:\$|S)?\s*(?P<price>\d{1,3}(?:\.\d{2})?)\s*$"
PRICE_RE = re.compile(rf"(?ix) (?:^|[\s\.\-–—\(\)]+) \$?\s*(\d{{1,3}}(?:\.\d{{2}})?) \s*(?:$|[^0-9])")
ALL_PRICES_RE = re.compile(r"(?i)\$?\s*(\d{1,3}(?:[.,]\d{2}))")

KNOWN_CATS = [
    "PIZZA",
    "TOPPINGS",
    "PERSONAL PIES",
    "PIZZA BY THE SLICE",
    "PIZZA BY",
    "THE SLICE",
    "DESSERTS",
    "GOURMET PIZZA",
]

NOISE_LINES = [
    r"^additional charge", r"^topping charge",
    r"^tax(es)? not included", r"^prices subject to change",
    r"^available .+ only$",
    r"^\d+\s*slices$", r"^\d+x\d+\"?$",
]

TOPPING_WORDS = [
    "sausage","meatball","pepperoni","ham","peppers","mushrooms","onion",
    "black olives","garlic","extra cheese","anchovies"
]

def _normalize_size_label(lbl: str) -> str:
    s = lbl.strip()
    s = s.replace("Smt", "Sml").replace("smt", "Sml").replace("trg", "Lrg").replace("irg", "Lrg")
    s = s.replace("lrg", "Lrg").replace("sml", "Sml").replace("mini", "Mini").replace("MINI", "Mini")
    s = s.replace("Family size", "Family Size").replace("family size", "Family Size")
    s = re.sub(r'(\d{1,2})[”"]', r'\1"', s)
    s = re.sub(r'\s+', " ", s).strip()
    return s

SIZE_TOKEN_RE = re.compile(r'(?i)\b(?:10|12|14|16)\s*[”"]?\s*(?:Mini|Sml|Lrg)?\b|Family\s*Size')

def _maybe_size_header(line: str) -> Optional[List[str]]:
    """
    Parse headers like:
      '10°Mini 12" Smt 16"trg Family Size'  OR  '12° Smt 16"lrg Family Size'
    Return 3–4 normalized labels in order, if found.
    """
    l = line.replace("°", '"')
    labels: List[str] = []
    parts = re.split(r'\s{2,}|[|•·]', l)  # keep whole line if single-spaced
    for part in parts:
        # 'Family Size'
        if re.search(r'(?i)\bfamily\s*size\b', part):
            labels.append("Family Size")
        # capture ALL size tokens in this part (fix: use finditer, not single search)
        for m in re.finditer(r'(?i)\b(10|12|14|16)\s*[”"]?\s*(Mini|Sml|Lrg)?\b', part):
            num = m.group(1)
            tag = m.group(2) or ""
            if not tag and re.search(r'(?i)\bmini\b', part):
                tag = "Mini"
            lbl = f'{num}" {tag}'.strip()
            labels.append(_normalize_size_label(lbl))

    # dedupe preserving order
    seen = set()
    ordered = []
    for x in labels:
        if x and x not in seen:
            ordered.append(x); seen.add(x)

    if not ordered:
        return None

    # ensure Family Size is last if present
    if "Family Size" in ordered:
        ordered = [h for h in ordered if h != "Family Size"] + ["Family Size"]

    # normalize bare 12"/16"
    ordered = [h.replace('12"', '12" Sml') if h == '12"' else h for h in ordered]
    ordered = [h.replace('16"', '16" Lrg') if h == '16"' else h for h in ordered]

    return ordered if len(ordered) >= 3 else None

def _is_noise(line: str) -> bool:
    s = line.strip().lower()
    if not s:
        return True
    for pat in NOISE_LINES:
        if re.search(pat, s, flags=re.I):
            return True
    return len(s) <= 1

def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if any(s.upper() == k for k in KNOWN_CATS):
        return True
    letters = re.sub(r"[^A-Za-z]", "", s)
    return bool(letters and (s.upper() == s) and len(s) <= 28)

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

def _clean_item_name(raw: str) -> str:
    cut = ALL_PRICES_RE.search(raw)
    base = raw[:cut.start()] if cut else raw
    base = re.sub(r"[·\.]{3,}", " ", base)
    base = re.sub(r"\s{2,}", " ", base).strip(" .-")
    base = re.sub(r"\s(?:[A-Za-z]{1,2})?$", "", base).strip(" .-")
    return base or "Untitled"

def parse_menu_text(text: str) -> Dict[str, Any]:
    text = _normalize_text_basic(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    categories: List[Dict[str, Any]] = []
    current = {"name": "Menu", "items": []}
    categories.append(current)

    pending_heading: str | None = None
    toppings_buffer: List[str] = []
    topping_prices: List[tuple[str, float]] = []
    size_header: Optional[List[str]] = None  # rolling header

    def start_category(name: str):
        nonlocal current, pending_heading, size_header
        current = {"name": name.title(), "items": []}
        categories.append(current)
        pending_heading = None
        size_header = None  # reset on new category

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
        hdr = _maybe_size_header(ln)
        if hdr:
            size_header = [_normalize_size_label(h) for h in hdr]
            if "Family Size" in size_header:
                size_header = [h for h in size_header if h != "Family Size"] + ["Family Size"]
            size_header = [h.replace('12"', '12" Sml') if h == '12"' else h for h in size_header]
            size_header = [h.replace('16"', '16" Lrg') if h == '16"' else h for h in size_header]
            continue

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

        all_prices = [p.replace(",", ".") for p in ALL_PRICES_RE.findall(ln)]
        prices = [_fix_cents(float(p)) for p in all_prices]

        if size_header and len(prices) >= 2:
            base_name = _clean_item_name(ln)
            header_use = size_header[:]
            if len(header_use) >= 4 and len(prices) >= 4:
                header_use = header_use[:4]; take = 4
            elif len(header_use) >= 3 and len(prices) >= 3:
                header_use = header_use[:3]; take = 3
            else:
                take = 0

            if take:
                sz_objs = [{"name": header_use[i], "price": prices[i]} for i in range(take)]
                current["items"].append({
                    "name": base_name,
                    "description": "",
                    "sizes": sz_objs,
                    "options": [],
                    "tags": [],
                })
                continue

        m_end = re.search(rf"^(?P<name>.+?)\s*{PRICE_TAIL}", ln)
        m = m_end if m_end else re.search(rf"^(?P<name>.+?)\s+(?:\.+\s+)?{PRICE_TAIL}", ln)
        if m:
            nm = _clean_item_name(m.group("name"))
            pr = _fix_cents(float(m.group("price")))
            add_item(nm, pr, None)
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
    item_count = sum([len(c["items"]) for c in parsed["categories"]])
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
            "ocr_engine": "grayscale-first(tesseract psm6→psm3) + column-split(binary)",
            "confidence": round(conf, 2),
        },
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }

def run_image_pipeline(image_path: Path, job_id: str) -> Tuple[str, Dict[str, Any]]:
    print(f"[OCR] run_image_pipeline(image_path={image_path}, job_id={job_id})")
    raw = ocr_image(image_path)
    draft = build_draft(job_id=job_id, source_filename=image_path.name, text=raw)
    return raw, draft
