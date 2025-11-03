"""
ServLine OCR Pipeline — Phase 1
PDF/Image → Tesseract → words → lines → blocks (with bboxes), page-ordered.
"""

from __future__ import annotations
import uuid
from typing import Any, Dict, List, Optional

from PIL import Image
import pytesseract
from pytesseract import image_to_osd
from PIL import ImageOps

from . import ocr_utils
from .ocr_types import Block, Line, Word, BBox


# -----------------------------
# Tunable heuristics (sane defaults)
# -----------------------------

DEFAULT_DPI = 300
LOW_CONF_DROP = 35.0  # drop words with conf < 35 (Tesseract conf is 0..100 or -1)
GRAYSCALE = True
CONTRAST = 1.15
UNSHARP_RADIUS = 1.0
UNSHARP_PERCENT = 120
UNSHARP_THRESHOLD = 3


def _ocr_page(im: Image.Image) -> Dict[str, List]:
    """Run Tesseract on a PIL image and return image_to_data dict."""
    return pytesseract.image_to_data(
        im, output_type=pytesseract.Output.DICT
    )


def _make_word(i: int, data: Dict[str, List], conf_floor: float = LOW_CONF_DROP) -> Optional[Word]:
    text = (data["text"][i] or "").strip()
    conf_raw = float(data["conf"][i])
    if conf_raw < 0:
        return None
    if not text:
        return None
    if conf_raw < conf_floor:
        return None

    x, y, w, h = int(data["left"][i]), int(data["top"][i]), int(data["width"][i]), int(data["height"][i])
    if w <= 0 or h <= 0:
        return None

    return {
        "text": text,
        "bbox": {"x": x, "y": y, "w": w, "h": h},
        "conf": conf_raw,
    }


def _group_words_to_lines(words: List[Word]) -> List[Line]:
    """Group words into lines using y-proximity (Δy) and build line bbox/text."""
    if not words:
        return []

    heights = [w["bbox"]["h"] for w in words]
    widths = [w["bbox"]["w"] for w in words]
    median_h = max(1.0, ocr_utils.median([float(h) for h in heights]))
    median_w = max(1.0, ocr_utils.median([float(w) for w in widths]))

    # Thresholds
    line_y_tol = 0.6 * median_h
    word_gap_thr = 0.5 * median_w

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
        bbox: BBox = {
            "x": min(xs),
            "y": min(ys),
            "w": max(xe) - min(xs),
            "h": max(ye) - min(ys),
        }

        text_pieces: List[str] = []
        last_right: Optional[int] = None
        for w in cur_words:
            if last_right is None:
                text_pieces.append(w["text"])
            else:
                text_pieces.append(" " + w["text"])
            last_right = w["bbox"]["x"] + w["bbox"]["w"]
        line_text = "".join(text_pieces).strip()

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
    """Group lines into blocks using vertical adjacency and horizontal overlap."""
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
        blocks.append(
            {
                "id": str(uuid.uuid4()),
                "page": 1,
                "bbox": bbox,
                "lines": cur[:],
            }
        )
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
        dy = ln["bbox"]["y"] - (prev["bbox"]["y"])
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


def segment_document(
    pdf_path: Optional[str] = None,
    pdf_bytes: Optional[bytes] = None,
    dpi: int = DEFAULT_DPI
) -> Dict[str, Any]:
    """Render a PDF or image file to images, OCR each page with Tesseract, and group words → lines → blocks."""
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

        # Light normalization to help OCR
        im_norm = ocr_utils.normalize_image(
            im,
            to_grayscale=GRAYSCALE,
            contrast_boost=CONTRAST,
            sharpen_radius=UNSHARP_RADIUS,
            unsharp_percent=UNSHARP_PERCENT,
            unsharp_threshold=UNSHARP_THRESHOLD,
        )

        data = _ocr_page(im_norm)

        # Build word list
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
        },
    }
    return segmented


if __name__ == "__main__":
    # Manual smoke test (adjust path as needed)
    sample = segment_document(pdf_path="fixtures/sample_menus/one_col_simple.pdf")
    print(list(sample.keys()), "Blocks:", len(sample["blocks"]))
