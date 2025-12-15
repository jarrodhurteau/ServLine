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

⚠️ Phase 7 pt.3–4 (layout research, parallel-only):
- Bounding-box normalization + geometric helpers
- Prototype word→span→line→block grouping utilities
- Skew estimate + lightweight block_map debug preview helpers

NOTE:
- This module intentionally keeps Phase 7 layout types importable at runtime without
  breaking execution environments, while still being Pylance/mypy friendly via
  TYPE_CHECKING and string annotations.
"""

from __future__ import annotations

import glob
import io
import math
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pdf2image import convert_from_bytes, convert_from_path
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

# Phase 3/4 types (TypedDicts)
try:
    from .ocr_types import BBox, Line, TextBlock, OCRBlock  # TypedDicts
except Exception:
    # Soft fallback if local import style differs during IDE refactors
    from storage.ocr_types import BBox, Line, TextBlock, OCRBlock  # type: ignore

# Phase 7 layout prototype dataclasses (typing-only; keeps Pylance happy)
if TYPE_CHECKING:
    from .ocr_types import BBoxTuple, WordGeom, Span, BlockGeom
else:
    BBoxTuple = Tuple[int, int, int, int]  # runtime fallback
    WordGeom = Any  # runtime fallback
    Span = Any      # runtime fallback
    BlockGeom = Any # runtime fallback


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
        fixed.save(buf, format="PNG")  # Save to PNG to drop EXIF
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
        out = out.filter(
            ImageFilter.UnsharpMask(
                radius=sharpen_radius,
                percent=unsharp_percent,
                threshold=unsharp_threshold,
            )
        )
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
    rotated = cv2.warpAffine(
        mat,
        M,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return cv_to_pil(rotated)


def preprocess_page(img: Image.Image, *, do_deskew: bool = True) -> Image.Image:
    """
    Maintenance Day 44:
    Produce a human-readable OCR work image.

    IMPORTANT:
    - This must NOT return adaptive-threshold/binary output for OCR.
    - Any thresholding should be treated as a mask/debug artifact, not OCR input.
    """
    if np is None or cv2 is None:
        return normalize_image(
            img,
            to_grayscale=True,
            contrast_boost=1.25,
            sharpen_radius=1.0,
            unsharp_percent=140,
            unsharp_threshold=2,
        )

    bgr = pil_to_cv(img)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    g1 = clahe.apply(gray)

    try:
        den = cv2.fastNlMeansDenoising(
            g1,
            None,
            h=7,
            templateWindowSize=7,
            searchWindowSize=21,
        )
    except Exception:
        den = g1

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

    # Use inverted threshold so "ink" becomes white (255). This makes
    # column-projection logic stable: gutters have LOW ink counts.
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    # Count ink pixels per x-column
    col_sum = np.sum(thr == 255, axis=0)

    w = int(col_sum.shape[0])
    mid = w // 2
    gap_left, gap_right = mid, mid

    # "Low ink" threshold: gutter should have very little ink
    low = float(thr.shape[0]) * 0.03

    while gap_left > 20 and float(col_sum[gap_left]) < low:
        gap_left -= 1
    while gap_right < w - 20 and float(col_sum[gap_right]) < low:
        gap_right += 1

    if (gap_right - gap_left) >= int(min_gap_px):
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

_MENU_HINT_WORDS = {
    "pizza",
    "pizzas",
    "calzone",
    "calzones",
    "sub",
    "subs",
    "sandwich",
    "sandwiches",
    "wrap",
    "wraps",
    "salad",
    "salads",
    "wing",
    "wings",
    "burger",
    "burgers",
    "fries",
    "pasta",
    "spaghetti",
    "lasagna",
    "garlic",
    "bread",
    "sticks",
    "cheese",
    "cheesy",
    "tenders",
    "nuggets",
    "combo",
    "special",
    "specials",
    "appetizer",
    "appetizers",
    "side",
    "sides",
    "dessert",
    "desserts",
}


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


def _looks_like_menu_line(text: str, price_hit: bool) -> bool:
    t = text.lower()
    if not t:
        return False

    has_alpha = any(c.isalpha() for c in t)
    has_space = " " in t
    length = len(t)

    for kw in _MENU_HINT_WORDS:
        if kw in t:
            return True

    if price_hit and has_alpha and length >= 8:
        return True

    if has_alpha and has_space and length >= 15:
        return True

    return False


def is_garbage_line(text: str, price_hit: bool) -> bool:
    t = clean_text(text)
    if not t or len(t) < 2:
        return True

    vr = vowel_ratio(t)
    mcr = max_consonant_run(t)
    nar = non_alnum_ratio(t)

    if _looks_like_menu_line(t, price_hit):
        if nar > 0.85 and vr < 0.10:
            return True
        if mcr > MAX_CONSONANT_RUN + 4:
            return True
        return False

    if price_hit:
        if vr < (VOWEL_RATIO_MIN * 0.6):
            return True
        if mcr > MAX_CONSONANT_RUN + 2:
            return True
        if nar > NON_ALNUM_RATIO_MAX * 1.25:
            return True
        return False

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


def group_text_blocks(
    lines: List[Line],
    *,
    max_y_gap_px: int = 18,
    align_tol_px: int = 28,
    min_vert_overlap_px: int = 6,
) -> List[TextBlock]:
    """
    Phase 3 compatibility function expected by storage.ocr_pipeline.segment_document().

    Input: a flat list of OCR "lines" (TypedDict Line with {"text", "bbox": {"x","y","w","h"}}).
    Output: a list of TextBlock-like dicts:
      {
        "bbox":  {"x","y","w","h"},
        "lines": [Line, ...],
        "text":  "merged text"
      }

    The grouping heuristic is intentionally simple and stable:
      - sort lines top→bottom
      - append line to current block if:
          (a) vertical gap is small, AND
          (b) there is some vertical overlap, AND
          (c) left/right edges roughly align
      - otherwise, start a new block
    """
    if not lines:
        return []

    # Sort lines by y, then x for stable grouping
    sorted_lines = sorted(lines, key=lambda l: (int(l["bbox"]["y"]), int(l["bbox"]["x"])))

    blocks: List[List[Line]] = []
    cur: List[Line] = []

    def line_xyxy(ln: Line) -> Tuple[int, int, int, int]:
        return bbox_to_x1y1x2y2(ln["bbox"])

    def flush() -> None:
        nonlocal cur, blocks
        if cur:
            blocks.append(cur)
            cur = []

    cur_bbox_xyxy: Optional[Tuple[int, int, int, int]] = None

    for ln in sorted_lines:
        b = line_xyxy(ln)

        if cur_bbox_xyxy is None:
            cur = [ln]
            cur_bbox_xyxy = b
            continue

        # Metrics vs current block bbox
        gap_y = b[1] - cur_bbox_xyxy[3]  # how far below current block
        v_ov = _vert_overlap_xy(cur_bbox_xyxy, b)
        align_ok = _rough_align_xy(cur_bbox_xyxy, b, align_tol_px)

        can_join = (
            gap_y <= max_y_gap_px
            and v_ov >= min_vert_overlap_px
            and align_ok
        )

        if can_join:
            cur.append(ln)
            cur_bbox_xyxy = _expand_xyxy(cur_bbox_xyxy, b)
        else:
            flush()
            cur = [ln]
            cur_bbox_xyxy = b

    flush()

    out: List[TextBlock] = []
    for blk_lines in blocks:
        # Compute bbox
        bb: Optional[Tuple[int, int, int, int]] = None
        for ln in blk_lines:
            b = line_xyxy(ln)
            bb = b if bb is None else _expand_xyxy(bb, b)

        x1, y1, x2, y2 = bb or (0, 0, 0, 0)
        bbox: BBox = _xyxy_to_bbox(x1, y1, x2, y2)

        out.append(
            {
                "bbox": bbox,
                "lines": blk_lines,
                "text": _merge_lines_text(blk_lines),
            }
        )

    return out

def blocks_for_preview(
    text_blocks: List[TextBlock],
    *,
    max_blocks: int = 200,
    max_chars: int = 180,
) -> List[OCRBlock]:
    """
    Phase 3/4 compatibility helper expected by storage.ocr_pipeline.segment_document().

    Input: TextBlock list (each has bbox + lines + merged text).
    Output: OCRBlock list for UI/debug preview.

    We keep the preview payload lightweight and stable:
      - id: deterministic per-page ordering
      - bbox: same bbox as the text block
      - text: a short, single-line snippet
      - meta: includes line_count for quick inspection

    This intentionally does NOT depend on Phase 7 layout dataclasses.
    """
    out: List[OCRBlock] = []

    if not text_blocks:
        return out

    for i, tb in enumerate(text_blocks[:max_blocks], start=1):
        bb = tb.get("bbox") or {"x": 0, "y": 0, "w": 0, "h": 0}

        # Prefer TextBlock["text"], else merge from lines.
        raw_text = (tb.get("text") or "").strip()
        if not raw_text:
            try:
                raw_text = _merge_lines_text(tb.get("lines") or [])
            except Exception:
                raw_text = ""

        # Normalize to a compact single-line snippet.
        snippet = raw_text.replace("\t", " ").replace("\r", " ").replace("\n", " ")
        snippet = re.sub(r"\s+", " ", snippet).strip()
        if len(snippet) > max_chars:
            snippet = snippet[:max_chars].rstrip()

        lines = tb.get("lines") or []
        line_count = 0
        try:
            line_count = int(len(lines))
        except Exception:
            line_count = 0

        out.append(
            {
                "id": f"tb_{i}",
                "bbox": bb,
                "text": snippet,
                "lines": lines,
                "meta": {"line_count": line_count},
            }
        )

    return out


# =============================
# Phase 7 — Geometry helpers (layout engine, parallel-only)
# =============================

def xyxy_norm(box: "BBoxTuple") -> "BBoxTuple":
    """Ensure (x1,y1,x2,y2) is ordered and non-inverted."""
    x1, y1, x2, y2 = box
    nx1 = int(min(x1, x2))
    nx2 = int(max(x1, x2))
    ny1 = int(min(y1, y2))
    ny2 = int(max(y1, y2))
    return (nx1, ny1, nx2, ny2)


def xyxy_w(box: "BBoxTuple") -> int:
    x1, y1, x2, y2 = xyxy_norm(box)
    return max(0, x2 - x1)


def xyxy_h(box: "BBoxTuple") -> int:
    x1, y1, x2, y2 = xyxy_norm(box)
    return max(0, y2 - y1)


def xyxy_area(box: "BBoxTuple") -> int:
    return xyxy_w(box) * xyxy_h(box)


def xyxy_center(box: "BBoxTuple") -> Tuple[float, float]:
    x1, y1, x2, y2 = xyxy_norm(box)
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def xyxy_expand(a: "BBoxTuple", b: "BBoxTuple") -> "BBoxTuple":
    ax1, ay1, ax2, ay2 = xyxy_norm(a)
    bx1, by1, bx2, by2 = xyxy_norm(b)
    return (min(ax1, bx1), min(ay1, by1), max(ax2, bx2), max(ay2, by2))


def xyxy_intersection(a: "BBoxTuple", b: "BBoxTuple") -> "BBoxTuple":
    ax1, ay1, ax2, ay2 = xyxy_norm(a)
    bx1, by1, bx2, by2 = xyxy_norm(b)
    x1 = max(ax1, bx1)
    y1 = max(ay1, by1)
    x2 = min(ax2, bx2)
    y2 = min(ay2, by2)
    if x2 <= x1 or y2 <= y1:
        return (0, 0, 0, 0)
    return (x1, y1, x2, y2)


def xyxy_iou(a: "BBoxTuple", b: "BBoxTuple") -> float:
    inter = xyxy_intersection(a, b)
    ia = xyxy_area(inter)
    if ia <= 0:
        return 0.0
    ua = xyxy_area(a) + xyxy_area(b) - ia
    if ua <= 0:
        return 0.0
    return float(ia) / float(ua)


def xyxy_vert_overlap(a: "BBoxTuple", b: "BBoxTuple") -> int:
    ax1, ay1, ax2, ay2 = xyxy_norm(a)
    bx1, by1, bx2, by2 = xyxy_norm(b)
    top = max(ay1, by1)
    bottom = min(ay2, by2)
    return max(0, bottom - top)


def xyxy_horiz_gap(a: "BBoxTuple", b: "BBoxTuple") -> int:
    ax1, ay1, ax2, ay2 = xyxy_norm(a)
    bx1, by1, bx2, by2 = xyxy_norm(b)
    if bx1 > ax2:
        return bx1 - ax2
    if ax1 > bx2:
        return ax1 - bx2
    return 0


def xyxy_vert_gap(a: "BBoxTuple", b: "BBoxTuple") -> int:
    ax1, ay1, ax2, ay2 = xyxy_norm(a)
    bx1, by1, bx2, by2 = xyxy_norm(b)
    if by1 > ay2:
        return by1 - ay2
    if ay1 > by2:
        return ay1 - by2
    return 0


def xyxy_left_right_align(a: "BBoxTuple", b: "BBoxTuple", tol_px: int) -> bool:
    ax1, ay1, ax2, ay2 = xyxy_norm(a)
    bx1, by1, bx2, by2 = xyxy_norm(b)
    return (abs(ax1 - bx1) <= tol_px) or (abs(ax2 - bx2) <= tol_px)


def estimate_skew_degrees(words: Iterable["WordGeom"], *, max_words: int = 250) -> float:
    """
    Rough skew estimate in degrees using word centers across lines.
    Uses a simple least-squares fit of y = m*x + b over word centers.
    Returns small angles near 0 for upright pages.
    """
    pts: List[Tuple[float, float]] = []
    n = 0
    for w in words:
        if n >= max_words:
            break
        try:
            wb = getattr(w, "bbox", None)
            if wb is None:
                continue
            cx, cy = xyxy_center(wb)
            pts.append((cx, cy))
            n += 1
        except Exception:
            continue

    if len(pts) < 8:
        return 0.0

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x_mean = sum(xs) / float(len(xs))
    y_mean = sum(ys) / float(len(ys))

    num = 0.0
    den = 0.0
    for x, y in pts:
        dx = x - x_mean
        dy = y - y_mean
        num += dx * dy
        den += dx * dx

    if den <= 1e-9:
        return 0.0

    m = num / den
    rad = math.atan(m)
    deg = rad * (180.0 / math.pi)

    if abs(deg) < 0.1:
        return 0.0
    if abs(deg) > 20:
        return 0.0
    return float(deg)


def words_to_spans(
    words: List["WordGeom"],
    *,
    y_overlap_ratio: float = 0.50,
    max_gap_factor: float = 1.35,
) -> List["Span"]:
    """
    Merge words into spans within a *single* line band (call after you've grouped by line).
    This function assumes the provided words belong to one line-ish cluster.

    y_overlap_ratio: require overlap >= ratio*min(h1,h2)
    max_gap_factor: allow horizontal gap <= max_gap_factor*median_word_height
    """
    if not words:
        return []

    ws = sorted(words, key=lambda w: (xyxy_norm(w.bbox)[0], xyxy_norm(w.bbox)[1]))
    heights = [max(1, xyxy_h(w.bbox)) for w in ws]
    med_h = median([float(h) for h in heights]) or 12.0
    max_gap = int(max(4.0, med_h * max_gap_factor))

    spans: List["Span"] = []

    cur_words: List["WordGeom"] = []
    cur_bbox: Optional["BBoxTuple"] = None
    cur_text_parts: List[str] = []

    def flush() -> None:
        nonlocal cur_words, cur_bbox, cur_text_parts, spans
        if not cur_words or cur_bbox is None:
            cur_words = []
            cur_bbox = None
            cur_text_parts = []
            return
        text = " ".join([t for t in cur_text_parts if t]).strip()
        spans.append(
            Span(
                words=tuple(cur_words),
                bbox=xyxy_norm(cur_bbox),
                text=text,
                page_index=getattr(cur_words[0], "page_index", 0),
                meta={},
            )
        )
        cur_words = []
        cur_bbox = None
        cur_text_parts = []

    for w in ws:
        wb = xyxy_norm(w.bbox)
        if cur_bbox is None:
            cur_words = [w]
            cur_bbox = wb
            cur_text_parts = [w.text]
            continue

        overlap = xyxy_vert_overlap(cur_bbox, wb)
        min_h = max(1, min(xyxy_h(cur_bbox), xyxy_h(wb)))
        overlap_ok = (overlap / float(min_h)) >= y_overlap_ratio
        gap = xyxy_horiz_gap(cur_bbox, wb)

        if overlap_ok and gap <= max_gap:
            cur_words.append(w)
            cur_bbox = xyxy_expand(cur_bbox, wb)
            cur_text_parts.append(w.text)
        else:
            flush()
            cur_words = [w]
            cur_bbox = wb
            cur_text_parts = [w.text]

    flush()
    return spans


def group_words_to_lines(
    words: List["WordGeom"],
    *,
    y_gap_factor: float = 0.60,
    min_words_per_line: int = 1,
) -> List[List["WordGeom"]]:
    """
    Cluster words into line bands using y-center proximity and height similarity.
    Returns list of line word-lists (each line sorted by x).
    """
    if not words:
        return []

    ws = sorted(words, key=lambda w: (xyxy_center(w.bbox)[1], xyxy_center(w.bbox)[0]))
    heights = [max(1, xyxy_h(w.bbox)) for w in ws]
    med_h = median([float(h) for h in heights]) or 12.0
    max_y_gap = float(med_h) * float(y_gap_factor)

    lines: List[List["WordGeom"]] = []
    cur: List["WordGeom"] = []
    cur_y: Optional[float] = None

    for w in ws:
        cx, cy = xyxy_center(w.bbox)
        if cur_y is None:
            cur = [w]
            cur_y = cy
            continue

        if abs(cy - cur_y) <= max_y_gap:
            cur.append(w)
            cur_y = (cur_y * 0.85) + (cy * 0.15)
        else:
            if len(cur) >= min_words_per_line:
                cur_sorted = sorted(cur, key=lambda ww: xyxy_norm(ww.bbox)[0])
                lines.append(cur_sorted)
            cur = [w]
            cur_y = cy

    if cur and len(cur) >= min_words_per_line:
        cur_sorted = sorted(cur, key=lambda ww: xyxy_norm(ww.bbox)[0])
        lines.append(cur_sorted)

    return lines


def lines_to_blocks(
    line_spans: List[List["Span"]],
    *,
    x_align_tol_factor: float = 0.40,
    y_gap_factor: float = 1.15,
    min_lines_per_block: int = 1,
) -> List["BlockGeom"]:
    """
    Cluster lines into blocks based on:
    - vertical proximity
    - left/right alignment
    - whitespace density proxy (span gaps)
    - text size similarity (median span height)
    """
    if not line_spans:
        return []

    all_heights: List[float] = []
    for line in line_spans:
        for sp in line:
            all_heights.append(float(max(1, xyxy_h(sp.bbox))))
    med_h = median(all_heights) or 12.0

    x_tol = int(max(6.0, med_h * x_align_tol_factor))
    max_y_gap = int(max(8.0, med_h * y_gap_factor))

    def line_bbox(line: List["Span"]) -> "BBoxTuple":
        b: Optional["BBoxTuple"] = None
        for sp in line:
            b = sp.bbox if b is None else xyxy_expand(b, sp.bbox)
        return xyxy_norm(b or (0, 0, 0, 0))

    blocks: List[List[List["Span"]]] = []
    cur_block: List[List["Span"]] = []
    cur_bbox: Optional["BBoxTuple"] = None

    for line in line_spans:
        lb = line_bbox(line)
        if cur_bbox is None:
            cur_block = [line]
            cur_bbox = lb
            continue

        y_gap = xyxy_vert_gap(cur_bbox, lb)
        align = xyxy_left_right_align(cur_bbox, lb, x_tol)

        if y_gap <= max_y_gap and align:
            cur_block.append(line)
            cur_bbox = xyxy_expand(cur_bbox, lb)
        else:
            if len(cur_block) >= min_lines_per_block:
                blocks.append(cur_block)
            cur_block = [line]
            cur_bbox = lb

    if cur_block and len(cur_block) >= min_lines_per_block:
        blocks.append(cur_block)

    out: List["BlockGeom"] = []
    for i, blk_lines in enumerate(blocks):
        bb: Optional["BBoxTuple"] = None
        merged_parts: List[str] = []
        page_index = 0

        for ln in blk_lines:
            lbb = line_bbox(ln)
            bb = lbb if bb is None else xyxy_expand(bb, lbb)

            if ln:
                page_index = int(getattr(ln[0], "page_index", page_index))

            line_text = " ".join([sp.text for sp in ln if getattr(sp, "text", "")]).strip()
            if line_text:
                merged_parts.append(line_text)

        merged_text = "\n".join(merged_parts).strip()
        out.append(
            BlockGeom(
                id=f"blk_{page_index}_{i+1}",
                page_index=int(page_index),
                bbox=xyxy_norm(bb or (0, 0, 0, 0)),
                lines=tuple(tuple(ln) for ln in blk_lines),
                merged_text=merged_text,
                label=None,
                section_hint=None,
                meta={},
            )
        )

    return out


def build_block_map_preview(
    blocks: List["BlockGeom"],
    *,
    max_blocks: int = 80,
    max_chars: int = 140,
) -> List[Dict[str, object]]:
    """
    Lightweight debug preview for layout-debug JSON.
    """
    out: List[Dict[str, object]] = []
    for b in blocks[:max_blocks]:
        x1, y1, x2, y2 = xyxy_norm(b.bbox)
        txt = (b.merged_text or "").strip().replace("\t", " ")
        txt = re.sub(r"\s+", " ", txt)
        if len(txt) > max_chars:
            txt = txt[:max_chars].rstrip()
        out.append(
            {
                "id": b.id,
                "page_index": b.page_index,
                "bbox": [int(x1), int(y1), int(x2), int(y2)],
                "w": int(x2 - x1),
                "h": int(y2 - y1),
                "label": b.label,
                "section_hint": b.section_hint,
                "text": txt,
            }
        )
    return out


def summarize_blocks(blocks: List["BlockGeom"]) -> Dict[str, object]:
    """
    Small numeric summaries for debug payload.
    """
    if not blocks:
        return {"block_count": 0, "avg_block_height": 0.0}

    hs = [float(max(0, xyxy_h(b.bbox))) for b in blocks]
    avg_h = sum(hs) / float(len(hs)) if hs else 0.0
    return {
        "block_count": int(len(blocks)),
        "avg_block_height": float(avg_h),
    }
