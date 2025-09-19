# storage/ocr_helper.py
from __future__ import annotations
import os
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

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
import math

# robust price + sizes regexes
DOT_LEADER_RX = re.compile(r"\.{2,}")  # dot leaders "....."
# $12 | 12 | 12.5 | 12.50 | $ 12 . 50 | 12 50
PRICE_RX = re.compile(r"""
    (?<!\w)                # not preceded by word-char
    \$?\s*
    (?P<int>\d{1,3}(?:[,\s]\d{3})*|\d+)
    (?:\s*[\.\s]\s*(?P<dec>\d{1,2}))?
    (?!\w)                 # not followed by word-char
""", re.X)

# formats like "Small 9.99  Medium 12.99  Large 15.49"
SIZE_PAIR_RX = re.compile(
    r"""(?P<label>[A-Za-z]{2,}|\b(Regular|Small|Medium|Large|XL|Half|Full)\b)\s*[:\-]?\s*(?P<price>\$?\s*\d+(?:[\.\s]\d{1,2})?)""",
    re.I,
)

ALL_CAPS_HDR = re.compile(r"^[A-Z][A-Z0-9&\s'\-]{2,}$")

def _to_float_price(txt: str) -> float:
    if not txt:
        return 0.0
    s = txt.replace("$", " ").replace(",", " ").strip()
    parts = [p for p in re.split(r"\s+", s) if p]
    if len(parts) >= 2 and parts[-1].isdigit() and len(parts[-1]) <= 2:
        # e.g. "12 5" -> 12.05 or 12.5 ? Use cents.
        return float(f"{parts[0]}.{parts[-1].rjust(2,'0')}")
    s = s.replace(" ", "")
    if s.count(".") > 1:
        # e.g. "12. . 50" after stripping could become "12..50": keep last dot
        left, _, right = s.rpartition(".")
        s = left.replace(".", "") + "." + right
    try:
        return float(s)
    except Exception:
        return 0.0

def _preprocess_image(img_bgr):
    """Light cleanup to aid OCR if OpenCV available."""
    if cv2 is None:
        return img_bgr
    try:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        # slight blur reduces salt-and-pepper noise
        gray = cv2.medianBlur(gray, 3)
        # adaptive threshold keeps contrast across lighting
        th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                   cv2.THRESH_BINARY, 31, 9)
        # optional deskew via moments
        coords = cv2.findNonZero(255 - th)
        if coords is not None:
            rect = cv2.minAreaRect(coords)
            angle = rect[-1]
            if angle < -45:
                angle = 90 + angle
            M = cv2.getRotationMatrix2D((th.shape[1] / 2, th.shape[0] / 2), angle, 1.0)
            th = cv2.warpAffine(th, M, (th.shape[1], th.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        return cv2.cvtColor(th, cv2.COLOR_GRAY2BGR)
    except Exception:
        return img_bgr

def _image_to_words(image_bgr) -> List[dict]:
    """
    Use pytesseract TSV to get word boxes + confidences.
    """
    config = "--psm 6 -l eng"  # assume uniform blocks, adjust if needed
    data = pytesseract.image_to_data(image_bgr, output_type=Output.DATAFRAME, config=config)  # type: ignore
    if data is None or len(data) == 0:
        return []
    # Clean rows
    data = data[(data.conf != -1) & data.text.notna()]
    out = []
    for _, r in data.iterrows():
        try:
            out.append({
                "text": str(r["text"]),
                "conf": float(r["conf"]),
                "x": int(r["left"]), "y": int(r["top"]),
                "w": int(r["width"]), "h": int(r["height"]),
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
        X = df[["cx"]].values  # 1D clustering by horizontal position
        k = min(k, max(1, len(words)//30)) or 1
        k = max(k, 1)
        if k == 1:
            return [0]*len(words)
        model = KMeans(n_clusters=k, n_init="auto", random_state=0)
        labels = model.fit_predict(X)
        return list(labels)
    except Exception:
        return None

def _group_lines(words: List[dict], labels: Optional[List[int]]) -> List[str]:
    """
    Group words into lines per column label, then sort by (page, y, x).
    """
    if not words:
        return []
    items = list(words)
    if labels and len(labels) == len(items):
        for w, lab in zip(items, labels):
            w["col"] = int(lab)
    else:
        for w in items:
            w["col"] = 0

    # group by (page, col, approx line y)
    # use line_num when available; fall back to y binning
    grouped: Dict[Tuple[int,int,int], List[dict]] = {}
    for w in items:
        page = int(w.get("page_num", 0))
        col = int(w["col"])
        line_key = int(w.get("line_num") or round(w["cy"] / 12))  # coarse bin
        key = (page, col, line_key)
        grouped.setdefault(key, []).append(w)

    lines = []
    for (page, col, line_key), ws in sorted(grouped.items(), key=lambda t: (t[0][0], t[0][1], t[0][2])):
        ws_sorted = sorted(ws, key=lambda x: x["x"])
        txt = " ".join(x["text"] for x in ws_sorted if x["text"].strip())
        avg_conf = sum(x["conf"] for x in ws_sorted) / max(1, len(ws_sorted))
        lines.append({"text": txt.strip(), "conf": avg_conf, "page": page, "col": col, "y": min(x["y"] for x in ws_sorted)})
    # finally, return readable strings but keep conf for parsing
    return lines

def _parse_lines_to_categories(lines: List[dict]) -> Dict[str, List[dict]]:
    """
    Build categories and items from OCR lines.
    """
    cats: Dict[str, List[dict]] = {}
    current = "Uncategorized"
    cats[current] = []

    def new_cat(name: str):
        nonlocal current
        current = (name or "Misc").strip()
        cats.setdefault(current, [])

    for row in lines:
        raw = row["text"]
        if not raw:
            continue
        line = DOT_LEADER_RX.sub(" ", raw).strip()  # remove dot leaders
        # Category markers
        if line.lower().startswith("category:"):
            new_cat(line.split(":", 1)[1].strip() or "Misc")
            continue
        if len(line) <= 40 and ALL_CAPS_HDR.match(line):
            new_cat(line.title())
            continue

        # Multiple size/price pairs on one line
        pairs = list(SIZE_PAIR_RX.finditer(line))
        if pairs and len(pairs) >= 2:
            base = SIZE_PAIR_RX.split(line)[0].strip()  # crude base (before first size)
            base = base.rstrip(":-").strip() or "Untitled"
            for m in pairs:
                label = (m.group("label") or "").strip().title()
                price = _to_float_price(m.group("price") or "")
                if price <= 0:
                    continue
                cats[current].append({
                    "name": f"{base} ({label})",
                    "description": "",
                    "price": price,
                    "confidence": round(float(row.get("conf", 80)), 1),
                    "raw": raw,
                })
            continue

        # Single trailing price
        m = None
        last = None
        for m2 in PRICE_RX.finditer(line):
            last = m2
        if last:
            name = line[: last.start()].rstrip(" -·:").strip()
            price = _to_float_price(last.group(0))
            if name and price > 0:
                cats[current].append({
                    "name": name,
                    "description": "",
                    "price": price,
                    "confidence": round(float(row.get("conf", 80)), 1),
                    "raw": raw,
                })
                continue

        # Otherwise, treat as description or freestanding item
        if cats[current] and (cats[current][-1]["name"] or ""):
            prev = cats[current][-1]
            desc = (prev.get("description") or "").strip()
            prev["description"] = (desc + " " + line).strip()
        else:
            cats[current].append({"name": line, "description": "", "price": 0.0, "confidence": round(float(row.get("conf", 80)),1), "raw": raw})

    # drop empty categories
    cats = {k: [it for it in v if any([it.get("name"), it.get("price"), it.get("description")])] for k, v in cats.items()}
    cats = {k: v for k, v in cats.items() if v}
    return cats or {"Uncategorized": [{"name":"No items recognized","description":"OCR returned no items.","price":0.0}]}

def _load_images_from_path(path: str) -> List:
    p = Path(path)
    if not p.exists():
        return []
    if p.suffix.lower() in [".jpg", ".jpeg", ".png"]:
        import PIL.Image as Image
        img = Image.open(str(p)).convert("RGB")
        return [img]
    if p.suffix.lower() == ".pdf" and convert_from_path is not None:
        # moderate DPI to balance speed/quality
        return convert_from_path(str(p), dpi=220, poppler_path=os.getenv("POPPLER_PATH") or None)
    return []

def extract_items_from_path(path: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Returns: { "Category": [ { name, description, price, confidence, raw }, ... ], ... }
    """
    images = _load_images_from_path(path)
    if not images:
        return {"Uncategorized":[{"name":"OCR not configured or file type unsupported","description":"","price":0.0}]}

    all_lines: List[dict] = []
    for im in images:
        # Pillow image to OpenCV for preprocess (if cv2 available)
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
            # fallback: let pytesseract read directly from PIL image
            config = "--psm 6 -l eng"
            try:
                # derive pseudo-words using TSV too (works with PIL)
                df = pytesseract.image_to_data(im, output_type=Output.DATAFRAME, config=config)  # type: ignore
                df = df[(df.conf != -1) & df.text.notna()]
                words = [{
                    "text": str(r["text"]),
                    "conf": float(r["conf"]),
                    "x": int(r["left"]), "y": int(r["top"]),
                    "w": int(r["width"]), "h": int(r["height"]),
                    "cx": int(r["left"]) + int(r["width"]) / 2.0,
                    "cy": int(r["top"]) + int(r["height"]) / 2.0,
                    "page_num": int(r.get("page_num", 0))
                } for _, r in df.iterrows()]
            except Exception:
                words = []

        labels = _cluster_columns(words, k=2)  # 2 columns common in menus; robust guards inside
        lines = _group_lines(words, labels)
        all_lines.extend(lines)

    cats = _parse_lines_to_categories(all_lines)
    return cats
