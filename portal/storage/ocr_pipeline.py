# storage/ocr_pipeline.py
"""
ServLine OCR Pipeline — Phase 2 (High-Clarity OCR)

Upgrades from Phase 1.5:
- Re-raster PDF pages at 400 DPI for sharper glyphs.
- Apply preprocess_page() → CLAHE + adaptive threshold + denoise + unsharp + deskew.
- Split two-column layouts with split_columns().
- Keep all existing gating, grouping, and metadata logic intact.
"""

from __future__ import annotations
import re
import uuid
from typing import Any, Dict, List, Optional
from PIL import Image
import pytesseract
from pytesseract import image_to_osd

from . import ocr_utils
from .ocr_types import Block, Line, Word, BBox


# -----------------------------
# Tunable heuristics
# -----------------------------

DEFAULT_DPI = 400  # bumped from 300 for high-clarity raster
LOW_CONF_DROP = 55.0  # drop words with conf < 55
GRAYSCALE = True
CONTRAST = 1.15
UNSHARP_RADIUS = 1.0
UNSHARP_PERCENT = 120
UNSHARP_THRESHOLD = 3

# Robust Tesseract settings
OCR_CONFIG = r"--oem 3 --psm 6 -c preserve_interword_spaces=1"

_ALLOWED_CHARS = r"A-Za-z0-9\$\.\,\-\/&'\"°\(\):;#\+ "
_ALLOWED_RE = re.compile(f"[^{_ALLOWED_CHARS}]+")
_REPEAT3 = re.compile(r"(.)\1\1+")
_NO_VOWEL_LONG = re.compile(r"\b[b-df-hj-np-tv-z]{4,}\b", re.I)


# -----------------------------
# Token helpers
# -----------------------------

def _alpha_ratio(s: str) -> float:
    if not s:
        return 0.0
    a = sum(c.isalpha() for c in s)
    return a / max(1, len(s))


def _symbol_ratio(s: str) -> float:
    if not s:
        return 1.0
    sym = sum(not (c.isalnum() or c.isspace()) for c in s)
    return sym / max(1, len(s))


def _clean_token(text: str) -> str:
    if not text:
        return ""
    t = _ALLOWED_RE.sub(" ", text)
    t = _REPEAT3.sub(r"\1\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def _token_is_garbage(tok: str) -> bool:
    if not tok:
        return True
    if _alpha_ratio(tok) < 0.45 and not any(ch.isdigit() for ch in tok):
        return True
    if _NO_VOWEL_LONG.search(tok):
        return True
    if len(tok) > 28 and _alpha_ratio(tok) < 0.6:
        return True
    if len(tok) <= 2 and not any(ch.isalnum() for ch in tok):
        return True
    if _symbol_ratio(tok) > 0.35:
        return True
    return False


# -----------------------------
# OCR primitives
# -----------------------------

def _ocr_page(im: Image.Image) -> Dict[str, List]:
    return pytesseract.image_to_data(
        im, output_type=pytesseract.Output.DICT, config=OCR_CONFIG
    )


def _make_word(i: int, data: Dict[str, List], conf_floor: float = LOW_CONF_DROP) -> Optional[Word]:
    raw = (data["text"][i] or "").strip()
    try:
        conf_raw = float(data["conf"][i])
    except Exception:
        conf_raw = -1.0
    if conf_raw < conf_floor:
        return None

    cleaned = _clean_token(raw)
    if not cleaned or _token_is_garbage(cleaned):
        return None

    x, y, w, h = int(data["left"][i]), int(data["top"][i]), int(data["width"][i]), int(data["height"][i])
    if w <= 0 or h <= 0:
        return None

    return {"text": cleaned, "bbox": {"x": x, "y": y, "w": w, "h": h}, "conf": conf_raw}


# -----------------------------
# Grouping
# -----------------------------

def _group_words_to_lines(words: List[Word]) -> List[Line]:
    if not words:
        return []
    heights = [w["bbox"]["h"] for w in words]
    widths = [w["bbox"]["w"] for w in words]
    median_h = max(1.0, ocr_utils.median([float(h) for h in heights]))
    line_y_tol = 0.6 * median_h

    lines: List[Line] = []
    cur_words: List[Word] = []

    def flush_line():
        nonlocal lines, cur_words
        if not cur_words:
            return
        xs = [w["bbox"]["x"] for w in cur_words]
        ys = [w["bbox"]["y"] for w in cur_words]
        xe = [w["bbox"]["x"] + w["bbox"]["w"] for w in cur_words]
        ye = [w["bbox"]["y"] + w["bbox"]["h"] for w in cur_words]
        bbox: BBox = {"x": min(xs), "y": min(ys), "w": max(xe) - min(xs), "h": max(ye) - min(ys)}
        line_text = " ".join(w["text"] for w in cur_words)
        line_text = _ALLOWED_RE.sub(" ", line_text)
        line_text = _REPEAT3.sub(r"\1\1", line_text)
        line_text = re.sub(r"\s{2,}", " ", line_text).strip()
        letters = sum(1 for c in line_text if c.isalpha())
        digits = sum(1 for c in line_text if c.isdigit())
        if len(line_text) < 3 or (letters < 2 and digits == 0):
            cur_words.clear()
            return
        lines.append({"text": line_text, "bbox": bbox, "words": cur_words[:]})
        cur_words = []

    last_y: Optional[float] = None
    for w in words:
        wy = w["bbox"]["y"]
        if last_y is None:
            cur_words = [w]
            last_y = wy
            continue
        if abs(wy - last_y) <= line_y_tol:
            cur_words.append(w)
            last_y = (last_y + wy) / 2.0
        else:
            cur_words.sort(key=lambda ww: ww["bbox"]["x"])
            flush_line()
            cur_words = [w]
            last_y = wy
    cur_words.sort(key=lambda ww: ww["bbox"]["x"])
    flush_line()
    lines.sort(key=lambda ln: (ln["bbox"]["y"], ln["bbox"]["x"]))
    return lines


def _group_lines_to_blocks(lines: List[Line]) -> List[Block]:
    if not lines:
        return []
    line_heights = [ln["bbox"]["h"] for ln in lines]
    median_line_h = max(1.0, ocr_utils.median([float(h) for h in line_heights]))
    line_gap_thr = 1.25 * median_line_h

    blocks: List[Block] = []
    cur: List[Line] = []

    def flush_block():
        nonlocal blocks, cur
        if not cur:
            return
        xs = [l["bbox"]["x"] for l in cur]
        ys = [l["bbox"]["y"] for l in cur]
        xe = [l["bbox"]["x"] + l["bbox"]["w"] for l in cur]
        ye = [l["bbox"]["y"] + l["bbox"]["h"] for l in cur]
        bbox: BBox = {"x": min(xs), "y": min(ys), "w": max(xe) - min(xs), "h": max(ye) - min(ys)}
        blocks.append({"id": str(uuid.uuid4()), "page": 1, "bbox": bbox, "lines": cur[:]})
        cur = []

    def overlap_ratio(a: BBox, b: BBox) -> float:
        ax1, ax2 = a["x"], a["x"] + a["w"]
        bx1, bx2 = b["x"], b["x"] + b["w"]
        inter = max(0, min(ax2, bx2) - max(ax1, bx1))
        denom = max(1, min(a["w"], b["w"]))
        return inter / float(denom)

    prev = None
    for ln in lines:
        if prev is None:
            cur = [ln]
            prev = ln
            continue
        dy = ln["bbox"]["y"] - prev["bbox"]["y"]
        horiz = overlap_ratio(prev["bbox"], ln["bbox"])
        if dy <= line_gap_thr or horiz >= 0.25:
            cur.append(ln)
        else:
            flush_block()
            cur = [ln]
        prev = ln
    flush_block()
    blocks.sort(key=lambda b: (b["bbox"]["x"], b["bbox"]["y"]))
    return blocks


# -----------------------------
# Main pipeline
# -----------------------------

def segment_document(
    pdf_path: Optional[str] = None,
    pdf_bytes: Optional[bytes] = None,
    dpi: int = DEFAULT_DPI,
) -> Dict[str, Any]:
    """Render a PDF or image file, run high-clarity OCR, and return blocks."""
    if not pdf_path and not pdf_bytes:
        raise ValueError("Either pdf_path or pdf_bytes must be provided.")

    if pdf_path:
        pages = ocr_utils.pdf_to_images_from_path(pdf_path, dpi=dpi)
        source = pdf_path
    else:
        pages = ocr_utils.pdf_to_images_from_bytes(pdf_bytes, dpi=dpi)
        source = "bytes"

    all_blocks: List[Block] = []
    page_index = 1

    for im in pages:
        # Auto-rotate page if sideways
        try:
            osd = image_to_osd(im)
            if "Rotate: 90" in osd:
                im = im.rotate(-90, expand=True)
                print(f"[Auto-rotate] Page {page_index}: rotated -90°")
            elif "Rotate: 270" in osd:
                im = im.rotate(90, expand=True)
                print(f"[Auto-rotate] Page {page_index}: rotated 90°")
            elif "Rotate: 180" in osd:
                im = im.rotate(180, expand=True)
                print(f"[Auto-rotate] Page {page_index}: rotated 180°")
        except Exception:
            pass

        # 🔹 High-clarity preprocessing and column split
        im_pre = ocr_utils.preprocess_page(im, do_deskew=True)
        columns = ocr_utils.split_columns(im_pre, min_gap_px=40)

        for col_idx, col_img in enumerate(columns, start=1):
            data = _ocr_page(col_img)
            words: List[Word] = []
            n = len(data.get("text", []))
            for i in range(n):
                w = _make_word(i, data)
                if w:
                    words.append(w)
            words.sort(key=lambda ww: (ww["bbox"]["y"], ww["bbox"]["x"]))
            lines = _group_words_to_lines(words)
            blocks = _group_lines_to_blocks(lines)
            for b in blocks:
                b["page"] = page_index
                b.setdefault("meta", {})["column"] = col_idx
            all_blocks.extend(blocks)

        page_index += 1

    segmented: Dict[str, Any] = {
        "pages": len(pages),
        "dpi": dpi,
        "blocks": all_blocks,
        "meta": {
            "source": source,
            "engine": "tesseract",
            "version": str(pytesseract.get_tesseract_version()),
            "config": OCR_CONFIG,
            "conf_floor": LOW_CONF_DROP,
            "mode": "high_clarity",
            "preprocess": "clahe+adaptive+denoise+unsharp+deskew",
        },
    }
    return segmented


if __name__ == "__main__":
    sample = segment_document(pdf_path="fixtures/sample_menus/pizza_real.pdf")
    print(list(sample.keys()), "Blocks:", len(sample["blocks"]))
