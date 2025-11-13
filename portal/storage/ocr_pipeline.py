# storage/ocr_pipeline.py
"""
ServLine OCR Pipeline — Phase 3 (Segmentation + Category Inference)

Phase 2 kept:
- Re-raster PDF pages at 400 DPI for sharper glyphs.
- Apply preprocess_page() → CLAHE + adaptive threshold + denoise + unsharp + deskew.
- Split two-column layouts with split_columns().
- Word→Line→Block grouping for legacy consumers.

Phase 3 pt.1:
- Text-block segmentation via ocr_utils.group_text_blocks()
- Preview-friendly blocks (xyxy + merged_text + block_type) for debug overlay

Phase 3 pt.2 (Day 23):
- Category inference on text blocks, now delegated to storage/category_infer.py
- Adds: category, category_confidence, rule_trace to text_blocks
- Mirrors category & confidence to preview_blocks for overlay UI
"""

from __future__ import annotations
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image
import pytesseract
from pytesseract import image_to_osd

from . import ocr_utils
from . import category_infer
from .ocr_types import Block, Line, Word, BBox  # TypedDicts; Phase-2 compatibility

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

# Lightweight heading detector to boost confidence when a block looks like a header
_HEADING_HINT = re.compile(r"^[A-Z][A-Z\s&/0-9\-]{2,}$")


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
# Category inference via storage/category_infer
# -----------------------------

def infer_categories_on_text_blocks(text_blocks: List[Dict[str, Any]]) -> None:
    """
    Mutate each text_block dict, adding:
      - category: str|None
      - category_confidence: int
      - rule_trace: str (human-readable reason)

    This is a thin wrapper around storage/category_infer.infer_category_for_text
    so all category logic lives in one place.
    """
    for idx, tb in enumerate(text_blocks):
        merged = tb.get("merged_text") or tb.get("text") or ""
        if not merged:
            tb["category"] = None
            tb["category_confidence"] = 0
            tb["rule_trace"] = "empty_text"
            continue

        block_type = (tb.get("block_type") or "").lower()
        heading_like = (
            block_type in {"heading", "section", "title"}
            or bool(_HEADING_HINT.match(merged.strip()))
        )

        # Neighbor categories (if already assigned) – gives mild context.
        neighbors: List[str] = []
        if idx > 0:
            prev_cat = text_blocks[idx - 1].get("category")
            if prev_cat:
                neighbors.append(prev_cat)
        if idx + 1 < len(text_blocks):
            next_cat = text_blocks[idx + 1].get("category")
            if next_cat:
                neighbors.append(next_cat)

        guess = category_infer.infer_category_for_text(
            name=merged,
            description=None,
            price_cents=0,
            neighbor_categories=neighbors,
            fallback="Uncategorized",
        )

        # We treat fallback "Uncategorized" as "no strong category" at block level.
        final_category = guess.category if guess.category and guess.category != "Uncategorized" else None

        reason = guess.reason or "heuristic match"
        if heading_like:
            reason = reason + "; heading_like"

        tb["category"] = final_category
        tb["category_confidence"] = int(guess.confidence)
        tb["rule_trace"] = reason


# -----------------------------
# Grouping (Phase 2 legacy)
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
    """Render a PDF or image file, run high-clarity OCR, and return blocks + Phase-3 text blocks."""
    if not pdf_path and not pdf_bytes:
        raise ValueError("Either pdf_path or pdf_bytes must be provided.")

    if pdf_path:
        pages = ocr_utils.pdf_to_images_from_path(pdf_path, dpi=dpi)
        source = pdf_path
    else:
        pages = ocr_utils.pdf_to_images_from_bytes(pdf_bytes, dpi=dpi)
        source = "bytes"

    all_blocks: List[Block] = []                 # Phase-2 block groups (legacy)
    all_text_blocks: List[Dict[str, Any]] = []   # Phase-3 raw text blocks ({bbox{x,y,w,h}, lines, merged_text, block_type})
    all_preview_blocks: List[Dict[str, Any]] = []  # Phase-3 preview blocks ({bbox[x1..], merged_text, block_type, lines[], page, column, category, category_confidence})

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

            # Phase-2 legacy lines/blocks
            lines = _group_words_to_lines(words)
            blocks = _group_lines_to_blocks(lines)
            for b in blocks:
                b["page"] = page_index
                b.setdefault("meta", {})["column"] = col_idx
            all_blocks.extend(blocks)

            # Phase-3: text-block segmentation
            tblocks = ocr_utils.group_text_blocks(lines)

            # ---- Category inference (mutates tblocks in place via shared helper)
            infer_categories_on_text_blocks(tblocks)

            all_text_blocks.extend(tblocks)

            # Compact preview records (xyxy coords), annotate page/column for overlay UI
            pblocks = ocr_utils.blocks_for_preview(tblocks)
            for pb in pblocks:
                pb["page"] = page_index
                pb["column"] = col_idx
                # Mirror category info for overlay
                pb["category"] = pb.get("category") or next(
                    (tb.get("category") for tb in tblocks if tb.get("id") == pb.get("id")), None
                )
                pb["category_confidence"] = pb.get("category_confidence") or next(
                    (tb.get("category_confidence") for tb in tblocks if tb.get("id") == pb.get("id")), None
                )
            all_preview_blocks.extend(pblocks)

        page_index += 1

    segmented: Dict[str, Any] = {
        "pages": len(pages),
        "dpi": dpi,
        "blocks": all_blocks,                  # Phase-2 compatible
        "text_blocks": all_text_blocks,        # Phase-3 TextBlock dicts (+category fields)
        "preview_blocks": all_preview_blocks,  # Phase-3 compact overlay records (+category fields)
        "meta": {
            "source": source,
            "engine": "tesseract",
            "version": str(pytesseract.get_tesseract_version()),
            "config": OCR_CONFIG,
            "conf_floor": LOW_CONF_DROP,
            "mode": "high_clarity+segmentation+category_infer",
            "preprocess": "clahe+adaptive+denoise+unsharp+deskew",
        },
    }
    return segmented


if __name__ == "__main__":
    sample = segment_document(pdf_path="fixtures/sample_menus/pizza_real.pdf")
    print(
        list(sample.keys()),
        "Blocks:", len(sample["blocks"]),
        "TextBlocks:", len(sample.get("text_blocks", [])),
        "PreviewBlocks:", len(sample.get("preview_blocks", []))
    )
    # Quick glance at inferred categories
    cats = [(tb.get("category"), tb.get("category_confidence"), (tb.get("merged_text") or "")[:40])
            for tb in sample.get("text_blocks", [])]
    print("Sample categories:", [c for c in cats if c[0]])
