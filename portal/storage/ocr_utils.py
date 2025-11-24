"""
ServLine OCR Utils — helpers for PDF→image, preprocessing, and env checks.

⚠️ New in Day 22 (Phase 2 pt.1):
- High-clarity preprocessing pipeline (CLAHE → adaptive threshold → denoise → unsharp)
- Optional deskew
- Simple two-column split via vertical projection
- Garbage guards + price normalization helpers

⚠️ New in Phase 2 pt.3 (orientation hardening):
- Deterministic orientation normalize: EXIF transpose → Tesseract OSD → probe 0/90/180/270

⚠️ Phase 4 pt.11–12:
- Propagate category/hierarchy metadata into OCRBlock preview structures for
  consistent structured output across Preview → Draft → Finalize.
"""

from __future__ import annotations

import os
import re
import glob
import math
import io
from typing import List, Optional, Tuple, Iterable, Any, TYPE_CHECKING, Dict

from PIL import Image, ImageOps, ImageFilter, ImageEnhance
from pdf2image import convert_from_path, convert_from_bytes
import pytesseract

# Optional deps
try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

# Pylance-safe alias for OpenCV/Numpy arrays
try:
    from numpy.typing import NDArray  # type: ignore
except Exception:  # pragma: no cover
    NDArray = Any  # type: ignore

# Phase 3/4 types
try:
    from .ocr_types import BBox, Line, TextBlock, OCRBlock  # TypedDicts
except Exception:
    # Soft fallback if local import style differs during IDE refactors
    from storage.ocr_types import BBox, Line, TextBlock, OCRBlock  # type: ignore


# =============================
# Poppler (for pdf2image)
# =============================

def get_poppler_path() -> Optional[str]:
    env = os.environ.get("POPPLER_PATH")
    if env and os.path.isdir(env):
        return env

    if os.name == "nt":
        candidates: List[str] = []
        candidates += glob.glob(r"C:\Program Files\poppler*\bin")
        candidates += glob.glob(r"C:\Program Files (x86)\poppler*\bin")
        candidates += glob.glob(r"C:\poppler*\bin")
        candidates += glob.glob(r"C:\tools\poppler*\bin")
        candidates += glob.glob(r"C:\Program Files\poppler*\Library\bin")
        candidates += glob.glob(r"C:\Program Files (x86)\poppler*\Library\bin")

        for path in candidates:
            if os.path.isfile(os.path.join(path, "pdfinfo.exe")):
                return path
    return None


def check_poppler() -> dict:
    path = get_poppler_path()
    ok = True
    if os.name == "nt":
        ok = bool(path and os.path.isfile(os.path.join(path, "pdfinfo.exe")))
    return {"found_on_disk": bool(ok), "path": path}


# =============================
# Tesseract
# =============================

def configure_tesseract_from_env() -> None:
    cmd = os.environ.get("TESSERACT_CMD")
    if cmd and os.path.isfile(cmd):
        pytesseract.pytesseract.tesseract_cmd = cmd


def check_tesseract() -> dict:
    try:
        configure_tesseract_from_env()
        ver = pytesseract.get_tesseract_version()
        return {"found_on_disk": True, "version": str(ver)}
    except Exception as e:
        return {"found_on_disk": False, "version": None, "error": str(e)}


# =============================
# PDF → PIL.Image sequence
# =============================

def pdf_to_images_from_path(pdf_path: str, dpi: int = 300) -> List[Image.Image]:
    poppler_path = get_poppler_path()
    if poppler_path and os.name == "nt":
        return convert_from_path(pdf_path, dpi=dpi, poppler_path=poppler_path)
    return convert_from_path(pdf_path, dpi=dpi)


def pdf_to_images_from_bytes(pdf_bytes: bytes, dpi: int = 300) -> List[Image.Image]:
    poppler_path = get_poppler_path()
    if poppler_path and os.name == "nt":
        return convert_from_bytes(pdf_bytes, dpi=dpi, poppler_path=poppler_path)
    return convert_from_bytes(pdf_bytes, dpi=dpi)


# =============================
# Orientation normalization (NEW)
# =============================

def apply_exif_orientation(img: Image.Image) -> Image.Image:
    """
    Rotate pixels according to EXIF Orientation, then strip EXIF so
    downstream consumers can't rotate again. Returns RGB image.
    """
    try:
        fixed = ImageOps.exif_transpose(img)
        buf = io.BytesIO()
        # Save to PNG to drop EXIF; reopen to get a clean image object
        fixed.save(buf, format="PNG")
        buf.seek(0)
        return Image.open(buf).convert("RGB")
    except Exception:
        return img.convert("RGB")


def detect_orientation_osd(img: Image.Image) -> Optional[int]:
    """
    Ask Tesseract OSD for the rotation. Returns 0/90/180/270 or None.
    """
    try:
        osd = pytesseract.image_to_osd(img)
        m = re.search(r"Rotate:\s*(\d+)", osd or "")
        if not m:
            return None
        deg = int(m.group(1)) % 360
        if deg in (0, 90, 180, 270):
            return deg
    except Exception:
        return None
    return None


def _quick_score(img: Image.Image) -> float:
    """
    Fast plausibility score of text for a given rotation.
    Uses text density + average token length; higher is better.
    """
    try:
        txt = pytesseract.image_to_string(
            img,
            config="--oem 3 --psm 6 -c preserve_interword_spaces=1",
        )
        if not txt:
            return 0.0
        letters = sum(ch.isalpha() for ch in txt)
        spaces = txt.count(" ")
        tokens = [t for t in re.split(r"\s+", txt) if t]
        avg_len = (sum(len(t) for t in tokens) / max(1, len(tokens)))
        alpha_ratio = letters / max(1, letters + spaces)
        return alpha_ratio * 0.6 + min(avg_len, 10) * 0.4
    except Exception:
        return 0.0


def probe_best_rotation(img: Image.Image) -> int:
    """
    Try 0/90/180/270 on thumbnails and pick the best-scoring rotation.
    Returns degrees clockwise to apply (0,90,180,270).
    """
    candidates = (0, 90, 180, 270)
    base = img.copy()
    best_deg, best_score = 0, -1.0
    for deg in candidates:
        test = base.rotate(-deg, expand=True)  # rotate CW by deg
        thumb = test.copy()
        thumb.thumbnail((1200, 1200))
        score = _quick_score(thumb)
        if score > best_score:
            best_deg, best_score = deg, score
    return best_deg


def normalize_orientation(img: Image.Image) -> Tuple[Image.Image, int]:
    """
    Deterministic orientation normalize:
    1) EXIF transpose & strip
    2) Tesseract OSD if available
    3) Probe 0/90/180/270 as fallback

    Returns (upright_image, degrees_applied_clockwise).
    """
    step1 = apply_exif_orientation(img)
    deg = detect_orientation_osd(step1)
    if deg is None:
        deg = probe_best_rotation(step1)
    upright = step1.rotate(-deg, expand=True) if deg else step1
    return upright, int(deg or 0)


# =============================
# PIL-only light normalization
# =============================

def normalize_image(
    im: Image.Image,
    to_grayscale: bool = True,
    contrast_boost: float = 1.15,
    sharpen_radius: float = 1.0,
    unsharp_percent: int = 120,
    unsharp_threshold: int = 3,
) -> Image.Image:
    out = im
    if to_grayscale and out.mode != "L":
        out = ImageOps.grayscale(out)
    if contrast_boost and contrast_boost != 1.0:
        out = ImageEnhance.Contrast(out).enhance(contrast_boost)
    if sharpen_radius > 0:
        out = out.filter(ImageFilter.UnsharpMask(radius=sharpen_radius,
                                                 percent=unsharp_percent,
                                                 threshold=unsharp_threshold))
    return out


# =============================
# High-clarity preprocessing
# =============================

def pil_to_cv(img: Image.Image) -> NDArray:
    if np is None or cv2 is None:
        raise RuntimeError("OpenCV/numpy not available")
    arr = np.array(img)
    if arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if arr.shape[2] == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def cv_to_pil(arr: NDArray) -> Image.Image:
    if cv2 is None:
        raise RuntimeError("OpenCV not available")
    rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def _cv_unsharp(img_gray: NDArray, amount: float = 1.2, radius: int = 3) -> NDArray:
    blur = cv2.GaussianBlur(img_gray, (0, 0), radius)
    sharp = cv2.addWeighted(img_gray, 1 + amount, blur, -amount, 0)
    return sharp


def deskew(img: Image.Image) -> Image.Image:
    if np is None or cv2 is None:
        return img
    mat = pil_to_cv(img)
    gray = cv2.cvtColor(mat, cv2.COLOR_BGR2GRAY)
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 1))
    mor = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, kernel, iterations=1)
    coords = np.column_stack(np.where(mor > 0))
    if coords.size == 0:
        return img
    rect = cv2.minAreaRect(coords)
    angle = rect[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.5:
        return img
    (h, w) = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rotated = cv2.warpAffine(mat, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return cv_to_pil(rotated)


def preprocess_page(img: Image.Image, *, do_deskew: bool = True) -> Image.Image:
    if np is None or cv2 is None:
        return normalize_image(img, to_grayscale=True, contrast_boost=1.25,
                               sharpen_radius=1.0, unsharp_percent=140, unsharp_threshold=2)
    bgr = pil_to_cv(img)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    g1 = clahe.apply(gray)
    bin_img = cv2.adaptiveThreshold(g1, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY, 35, 11)
    den = cv2.fastNlMeansDenoising(bin_img, None, h=10, templateWindowSize=7, searchWindowSize=21)
    sharp = _cv_unsharp(den, amount=0.8, radius=2)
    out = cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR)
    pil = cv_to_pil(out)
    if do_deskew:
        pil = deskew(pil)
    return pil


def split_columns(img: Image.Image, *, min_gap_px: int = 40) -> List[Image.Image]:
    if np is None or cv2 is None:
        return [img]
    mat = pil_to_cv(img)
    gray = cv2.cvtColor(mat, cv2.COLOR_BGR2GRAY)
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    col_sum = np.sum(thr == 255, axis=0)
    w = col_sum.shape[0]
    mid = w // 2
    gap_left, gap_right = mid, mid
    low = (thr.shape[0] * 0.03)
    while gap_left > 20 and col_sum[gap_left] < low:
        gap_left -= 1
    while gap_right < w - 20 and col_sum[gap_right] < low:
        gap_right += 1
    if (gap_right - gap_left) >= min_gap_px:
        left = mat[:, 0:gap_left]
        right = mat[:, gap_right:w]
        return [cv_to_pil(left), cv_to_pil(right)]
    return [img]


# =============================
# Garbage guards & prices
# =============================

_VOWELS = set("aeiouy")
_CONSONANTS = set("bcdfghjklmnpqrstvwxz")
VOWEL_RATIO_MIN = 0.20
MAX_CONSONANT_RUN = 6
NON_ALNUM_RATIO_MAX = 0.50
PRICE_MIN = 100
PRICE_MAX = 9999


def clean_text(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[\u2018\u2019\u201C\u201D]", "'", s)
    s = re.sub(r"\s+", " ", s)
    return s


def vowel_ratio(s: str) -> float:
    letters = [c.lower() for c in s if c.isalpha()]
    if not letters:
        return 0.0
    v = sum(1 for c in letters if c in _VOWELS)
    return v / len(letters)


def max_consonant_run(s: str) -> int:
    m = 0
    run = 0
    for c in s.lower():
        if c in _CONSONANTS:
            run += 1
            m = max(m, run)
        else:
            run = 0
    return m


def non_alnum_ratio(s: str) -> float:
    if not s:
        return 1.0
    non = sum(1 for c in s if not c.isalnum() and not c.isspace())
    return non / len(s)


_price_token_re = re.compile(
    r"""(?:(?:\$?\s*(\d{1,3})[.,](\d{2}))|(?:\$\s*(\d{1,3}))|(?:\b(\d{3})\b)|(?:\b(\d{1,2})\s*99\b))""",
    re.VERBOSE,
)


def sanitize_price(token: str) -> Optional[int]:
    token = token.strip()
    m = _price_token_re.search(token)
    if not m:
        return None
    cents: Optional[int] = None
    if m.group(1) and m.group(2):
        cents = int(m.group(1)) * 100 + int(m.group(2))
    elif m.group(3):
        cents = int(m.group(3)) * 100
    elif m.group(4):
        cents = int(m.group(4))
    elif m.group(5):
        cents = int(m.group(5)) * 100 + 99
    if cents is None:
        return None
    if cents < 1000 and len(re.sub(r"\D", "", token)) == 3:
        cents = (cents // 100) * 100 + (cents % 100)
    if cents < PRICE_MIN or cents > PRICE_MAX:
        return None
    return cents


def find_price_candidates(text: str) -> List[int]:
    vals: List[int] = []
    for m in _price_token_re.finditer(text):
        cents = sanitize_price(m.group(0))
        if cents is not None:
            vals.append(cents)
    return vals


def is_garbage_line(text: str, price_hit: bool) -> bool:
    t = clean_text(text)
    if not t or len(t) < 2:
        return True
    vr = vowel_ratio(t)
    mcr = max_consonant_run(t)
    nar = non_alnum_ratio(t)
    if price_hit:
        return (vr < (VOWEL_RATIO_MIN * 0.6)) or (mcr > MAX_CONSONANT_RUN + 2) or (nar > NON_ALNUM_RATIO_MAX * 1.25)
    if vr < VOWEL_RATIO_MIN:
        return True
    if mcr > MAX_CONSONANT_RUN:
        return True
    if nar > NON_ALNUM_RATIO_MAX:
        return True
    return False


# =============================
# Small stats helpers
# =============================

def median(values: List[float]) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    n = len(vals)
    mid = n // 2
    if n % 2:
        return float(vals[mid])
    return float((vals[mid - 1] + vals[mid]) / 2.0)


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# =============================
# Phase 3 — Text-Block Segmentation
# =============================

def bbox_to_x1y1x2y2(b: BBox) -> Tuple[int, int, int, int]:
    """Convert {x,y,w,h} → (x1,y1,x2,y2)."""
    x1, y1 = int(b["x"]), int(b["y"])
    x2, y2 = x1 + int(b["w"]), y1 + int(b["h"])
    return (x1, y1, x2, y2)


def _xyxy_to_bbox(x1: int, y1: int, x2: int, y2: int) -> BBox:
    """Convert (x1,y1,x2,y2) → {x,y,w,h} with non-negative w/h."""
    return {"x": int(x1), "y": int(y1), "w": max(0, int(x2 - x1)), "h": max(0, int(y2 - y1))}


def _expand_xyxy(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return (min(ax1, bx1), min(ay1, by1), max(ax2, bx2), max(ay2, by2))


def _vert_overlap_xy(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> int:
    """Return vertical overlap in pixels for two XYXY boxes."""
    top = max(a[1], b[1])
    bottom = min(a[3], b[3])
    return max(0, bottom - top)


def _horiz_gap_xy(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> int:
    """Return horizontal gap in pixels (0 if overlapping)."""
    if b[0] > a[2]:
        return b[0] - a[2]
    if a[0] > b[2]:
        return a[0] - b[2]
    return 0


def _rough_align_xy(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int], tol_px: int) -> bool:
    """Left/right edge rough alignment within tolerance."""
    left_aligned = abs(a[0] - b[0]) <= tol_px
    right_aligned = abs(a[2] - b[2]) <= tol_px
    return left_aligned or right_aligned


def _merge_lines_text(lines: List[Line]) -> str:
    """Join line texts top→bottom with newlines (preserve line breaks for parser)."""
    out: List[str] = []
    for ln in sorted(lines, key=lambda l: (l["bbox"]["y"], l["bbox"]["x"])):
        t = (ln.get("text") or "").rstrip()
        if t:
            out.append(t)
    return "\n".join(out)


def _guess_block_type(lines: List[Line]) -> Optional[str]:
    """Light heuristic: all-caps short → header; many digits → price; else item."""
    merged = _merge_lines_text(lines)
    m = merged.replace("\n", " ").strip()
    if not m:
        return None
    if m.isupper() and len(m) <= 40:
        return "header"
    digits = sum(ch.isdigit() for ch in m)
    if digits >= max(4, len(m) // 3):
        return "price"
    return "item"


def group_text_blocks(
    lines: List[Line],
    *,
    align_tol_px: int = 14,
    vert_overlap_px: int = 8,
    horiz_gap_px: int = 24,
) -> List[TextBlock]:
    """
    Cluster OCR lines into logical text blocks using simple geometric cues:
    - vertical overlap + (small horizontal gap OR rough left/right alignment)
    - merge bbox and maintain line order

    Returns a list of TextBlock dicts: {bbox:{x,y,w,h}, lines:[...], merged_text:str, block_type?:str}
    """
    if not lines:
        return []

    # Sort stable top→bottom, then left→right
    sorted_lines = sorted(lines, key=lambda l: (l["bbox"]["y"], l["bbox"]["x"]))

    # Each block stores: xyxy bbox and line list; we convert back to {x,y,w,h} later.
    blocks_xy: List[Tuple[Tuple[int, int, int, int], List[Line]]] = []

    for ln in sorted_lines:
        lb = bbox_to_x1y1x2y2(ln["bbox"])
        placed = False
        for i, (bb, lst) in enumerate(blocks_xy):
            v_overlap = _vert_overlap_xy(bb, lb)
            h_gap = _horiz_gap_xy(bb, lb)
            if v_overlap >= vert_overlap_px and (h_gap <= horiz_gap_px or _rough_align_xy(bb, lb, align_tol_px)):
                # append to this block
                lst.append(ln)
                blocks_xy[i] = (_expand_xyxy(bb, lb), lst)
                placed = True
                break
        if not placed:
            blocks_xy.append((lb, [ln]))

    out: List[TextBlock] = []
    for bb, lst in blocks_xy:
        lst.sort(key=lambda l: (l["bbox"]["y"], l["bbox"]["x"]))
        merged = _merge_lines_text(lst)
        btype = _guess_block_type(lst)
        out.append({
            "bbox": _xyxy_to_bbox(*bb),
            "lines": lst,
            "merged_text": merged,
            "block_type": btype,  # optional
        })
    return out


def blocks_for_preview(blocks: List[TextBlock]) -> List[OCRBlock]:
    """
    Convert TextBlock list to compact preview-friendly OCRBlock dicts:

    {
      "bbox": [x1,y1,x2,y2],
      "merged_text": "...",
      "block_type": "...",
      "lines": [{text,bbox,confidence}],
      # Optional mirrored hierarchy/inference fields when present on TextBlock:
      # "id", "category", "category_confidence", "rule_trace",
      # "subcategory", "section_path"
    }
    """
    preview: List[OCRBlock] = []
    for b in blocks:
        x1, y1, x2, y2 = bbox_to_x1y1x2y2(b["bbox"])
        p_lines: List[Dict[str, object]] = []
        for ln in b["lines"]:
            lb = ln["bbox"]
            p_lines.append({
                "text": ln.get("text", ""),
                "bbox": [lb["x"], lb["y"], lb["x"] + lb["w"], lb["y"] + lb["h"]],
                # Line may not carry explicit conf; derive avg from words if present
                "confidence": float(
                    (sum(w.get("conf", 0.0) for w in (ln.get("words") or [])) / max(1, len(ln.get("words") or [])))
                    if ln.get("words") else 0.0
                ),
            })

        block_payload: OCRBlock = {
            "bbox": [x1, y1, x2, y2],
            "merged_text": b.get("merged_text", ""),
            "block_type": b.get("block_type"),
            "lines": p_lines,
        }

        # Mirror optional TextBlock metadata into OCRBlock for overlays/debugging.
        if "id" in b:
            block_payload["id"] = b["id"]  # type: ignore[assignment]
        if "category" in b:
            block_payload["category"] = b.get("category")  # type: ignore[assignment]
        if "category_confidence" in b:
            block_payload["category_confidence"] = b.get("category_confidence")  # type: ignore[assignment]
        if "rule_trace" in b:
            block_payload["rule_trace"] = b.get("rule_trace")  # type: ignore[assignment]
        if "subcategory" in b:
            block_payload["subcategory"] = b.get("subcategory")  # type: ignore[assignment]
        if "section_path" in b:
            block_payload["section_path"] = b.get("section_path")  # type: ignore[assignment]

        preview.append(block_payload)
    return preview
