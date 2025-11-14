"""
ServLine OCR Types — Phase 3 (Segmentation + Category Inference)
Defines TypedDicts for bbox, words, lines, blocks, and segmented text blocks with
optional category metadata for overlay/debug UI.
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
    rule_trace: NotRequired[str]           # string trace (e.g., "rule[...]|ml[...]")
    confidence: NotRequired[float]         # 0.0–1.0


# ────────────────────────────────────────────────
# 🧠 TextBlock and OCRBlock for Phase 3 segmentation
# ────────────────────────────────────────────────

class TextBlock(TypedDict):
    """
    Represents a merged logical region of related lines (e.g., header or item cluster).
    """
    bbox: BBox
    lines: List[Line]
    merged_text: str
    block_type: NotRequired[str]           # "item" | "price" | "header" | "section" | etc.
    # NEW in Phase 3 pt.2 (optional fields populated by category inference):
    id: NotRequired[str]                   # stable identifier if assigned upstream
    category: NotRequired[Optional[str]]   # canonical label or None
    category_confidence: NotRequired[Optional[float]]  # 0.0–1.0 or None
    rule_trace: NotRequired[Optional[str]] # explanation string (rules/ML fusion)


class OCRBlock(TypedDict):
    """
    Compact block representation used in pipeline results and preview overlays.
    """
    bbox: List[int]                        # [x1, y1, x2, y2]
    merged_text: str
    block_type: Optional[str]
    lines: List[Dict[str, object]]         # flattened line representation
    # NEW: mirrored category info for debug overlay coloring/tooltips
    id: NotRequired[str]
    category: NotRequired[Optional[str]]
    category_confidence: NotRequired[Optional[float]]
    rule_trace: NotRequired[Optional[str]]


# ────────────────────────────────────────────────
# 💵 Price candidates & variants (Phase 3 pt.6)
# ────────────────────────────────────────────────

class OCRPriceCandidate(TypedDict):
    """
    Raw price candidate detected in a line/block.

    text:        Raw price string as seen in OCR (e.g., "12.99").
    confidence:  0.0–1.0 confidence for this price extraction.
    price_cents: Optional normalized integer cents if we parse text successfully.
    """
    text: str
    confidence: float              # 0.0–1.0
    price_cents: NotRequired[int]  # 1299, etc. when parsed


class OCRVariant(TypedDict):
    """
    Represents a size/option variant with its own price.
    Used in OCR preview items before they become DraftItems.
    """
    label: str                     # e.g. "Small", "Lg", '16"' etc.
    price_cents: int               # normalized cents value
    confidence: float              # 0.0–1.0 for this variant


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
    "OCRPriceCandidate",
    "OCRVariant",
]
