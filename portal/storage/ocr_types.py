"""
ServLine OCR Types — lightweight schemas for Phase 1+
Defines basic TypedDicts for bbox, words, lines, and blocks.
"""

from __future__ import annotations
from typing import TypedDict, List, NotRequired, Dict


class BBox(TypedDict):
    x: int
    y: int
    w: int
    h: int


class Word(TypedDict):
    text: str
    bbox: BBox
    conf: float  # 0-100 from Tesseract (will normalize later if needed)


class Line(TypedDict):
    text: str
    bbox: BBox
    words: List[Word]


class Block(TypedDict):
    id: str
    page: int           # 1-based page index
    bbox: BBox
    lines: List[Line]
    # Future phases may add:
    category: NotRequired[str]
    rule_trace: NotRequired[List[str]]
    confidence: NotRequired[float]  # 0.0-1.0


Segmented = Dict[str, object]  # {"pages": int, "dpi": int, "blocks": List[Block], "meta": {...}}

__all__ = ["BBox", "Word", "Line", "Block", "Segmented"]
