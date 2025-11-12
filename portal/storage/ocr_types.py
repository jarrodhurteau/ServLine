"""
ServLine OCR Types — extended for Phase 3 segmentation
Defines lightweight TypedDicts for bbox, words, lines, blocks, and new text-block groupings.
"""

from __future__ import annotations
from typing import TypedDict, List, NotRequired, Dict, Optional


# ────────────────────────────────────────────────
# 🧩 Base geometric unit: bounding box
# ────────────────────────────────────────────────

class BBox(TypedDict):
    x: int
    y: int
    w: int
    h: int


# ────────────────────────────────────────────────
# 🔤 OCR primitives
# ────────────────────────────────────────────────

class Word(TypedDict):
    text: str
    bbox: BBox
    conf: float  # 0–100 from Tesseract (normalized later if needed)


class Line(TypedDict):
    text: str
    bbox: BBox
    words: List[Word]


# ────────────────────────────────────────────────
# 🧱 OCR Block (original Phase 1/2)
# ────────────────────────────────────────────────

class Block(TypedDict):
    id: str
    page: int           # 1-based page index
    bbox: BBox
    lines: List[Line]
    # Optional metadata for Phase 2 and beyond
    category: NotRequired[str]
    rule_trace: NotRequired[List[str]]
    confidence: NotRequired[float]  # 0.0–1.0


# ────────────────────────────────────────────────
# 🧠 NEW: TextBlock and OCRBlock for Phase 3 segmentation
# ────────────────────────────────────────────────

class TextBlock(TypedDict):
    """
    Represents a merged logical region of related lines (item name + description + price).
    """
    bbox: BBox
    lines: List[Line]
    merged_text: str
    block_type: NotRequired[str]  # "item" | "price" | "header" | None


class OCRBlock(TypedDict):
    """
    Optional normalized variant used in OCR pipeline results and previews.
    """
    bbox: List[int]  # [x1, y1, x2, y2]
    merged_text: str
    block_type: Optional[str]
    lines: List[Dict[str, object]]  # flattened line representation


# ────────────────────────────────────────────────
# 📦 High-level container for OCR job results
# ────────────────────────────────────────────────

class OCRResult(TypedDict):
    """
    Represents the full OCR output of a page or job, including
    raw lines, merged blocks, and metadata.
    """
    filename: str
    page_index: int
    lines: List[Line]
    blocks: NotRequired[List[OCRBlock]]
    meta: NotRequired[Dict[str, object]]


# ────────────────────────────────────────────────
# 📘 Segmented wrapper (legacy compatibility)
# ────────────────────────────────────────────────

Segmented = Dict[str, object]  # {"pages": int, "dpi": int, "blocks": List[Block], "meta": {...}}

__all__ = [
    "BBox",
    "Word",
    "Line",
    "Block",
    "TextBlock",
    "OCRBlock",
    "OCRResult",
    "Segmented",
]
