"""
ServLine OCR Types — Phase 4 (Semantic Blocks, Variants, Category Hierarchy, Structured Output)
Defines TypedDicts for bbox, words, lines, blocks, segmented text blocks with
optional category and variant metadata for overlay/debug UI and downstream pipeline.

Phase 4 pt.11–12 additions:
- Structured item/section/menu payload types shared across Preview → Draft → Finalize → Export.
- Rich metadata: confidence maps, cleanup flags, provenance.
- Normalized ordering + section path/slug/position + auto_group hooks for Superimport.

Phase 7 pt.3–4 additions (BC-safe):
- Geometry-first dataclasses (WordGeom/Span/BlockGeom) for layout engine prototypes.
- Keep existing TypedDict API untouched for production extraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, NotRequired, Optional, TypedDict, Tuple


# ────────────────────────────────────────────────
# 🧩 Base geometric unit: bounding box
# ────────────────────────────────────────────────

class BBox(TypedDict):
    x: int
    y: int
    w: int
    h: int


# ────────────────────────────────────────────────
# 🔤 OCR primitives (TypedDicts — production / API surface)
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
# 🧱 OCR Block (original Phase 1/2) — production / API surface
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
# 🧠 TextBlock and OCRBlock for Phase 3/4 segmentation
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
    # NEW in Phase 4 pt.4: richer hierarchy support
    subcategory: NotRequired[Optional[str]]           # nested subcategory label, if any
    section_path: NotRequired[List[str]]             # e.g. ["PIZZA", "Specialty Pizzas"]


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
    # NEW in Phase 4 pt.4: hierarchy debug info
    subcategory: NotRequired[Optional[str]]
    section_path: NotRequired[List[str]]


# ────────────────────────────────────────────────
# 💵 Price candidates & variants (Phase 3 pt.6 + Phase 4 pt.3)
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

    # NEW in Phase 4 pt.3 — richer variant intelligence (all optional, BC-safe):
    kind: NotRequired[str]                 # "size" | "flavor" | "style" | "combo" | "other"
    normalized_size: NotRequired[str]      # e.g. "10in", "14in", "6pc", "12pc"
    group_key: NotRequired[str]            # family key for clustering variants for an item

    # Sprint 8.2 Day 58 — combo hint from variant building:
    kind_hint: NotRequired[str]            # "combo" when building detected combo context

    # Sprint 8.2 Day 60 — multi-signal confidence scoring:
    confidence_details: NotRequired[Dict[str, float]]  # audit trail: base, label_mod, etc.


# ────────────────────────────────────────────────
# 🎯 Structured Draft Output v2 (Phase 4 pt.11)
# Shared across Preview → Draft → Finalize → Export
# ────────────────────────────────────────────────

class ItemConfidence(TypedDict):
    """
    Fine-grained confidence map for a structured menu item.
    All fields are optional, 0–100 integer scores.
    """
    overall: NotRequired[int]
    name: NotRequired[int]
    description: NotRequired[int]
    category: NotRequired[int]
    price: NotRequired[int]
    variants: NotRequired[int]


class ItemProvenance(TypedDict):
    """
    Provenance info for downstream debugging/traceability.
    All fields optional and BC-safe.
    """
    block_id: NotRequired[Optional[str]]       # primary OCR block id, if known
    page_index: NotRequired[Optional[int]]     # 0-based or 1-based page index (pipeline-defined)
    rule_trace: NotRequired[Optional[str]]     # winning rule/ML trace at item level
    source: NotRequired[Optional[str]]         # "ocr", "ai_cleanup", "manual", etc.
    extra: NotRequired[Dict[str, object]]      # any additional debug metadata


class PreviewItem(TypedDict):
    """
    Unified normalized menu item structure used in:
    - OCR Preview JSON
    - Draft creation payloads
    - Finalized export (Phase 4+)

    Earlier phases may omit some optional fields; new ones are BC-safe.
    """
    # Core normalized item fields
    name: str
    description: Optional[str]
    category: str                   # normalized category label (e.g., "Pizza")
    subcategory: Optional[str]      # optional subcategory label
    section_path: List[str]         # e.g. ["PIZZA", "Specialty Pizzas"]
    price_cents: int                # primary/anchor price (0 if variants-only)
    variants: List[OCRVariant]      # variant list (can be empty)
    confidence: int                 # 0–100 normalized item-level confidence

    # Legacy / preview-only fields (kept BC-safe)
    price_candidates: NotRequired[List[OCRPriceCandidate]]

    # Rich metadata layer (Phase 4 pt.11)
    confidence_map: NotRequired[ItemConfidence]   # per-field confidence map
    provenance: NotRequired[ItemProvenance]       # where this item came from
    cleanup_flags: NotRequired[List[str]]         # e.g. ["name_ocr_fix", "desc_merged"]
    warnings: NotRequired[List[str]]              # non-fatal issues for UI badges

    # Ordering + Superimport prep (Phase 4 pt.12)
    section_slug: NotRequired[str]                # slugified from section_path
    section_position: NotRequired[int]            # section ordering index
    item_position: NotRequired[int]               # stable item ordering within section
    auto_group_id: NotRequired[str]               # hook for auto-grouped sections/items


# Alias for clarity in downstream code: StructuredItem == PreviewItem
StructuredItem = PreviewItem


class StructuredSection(TypedDict):
    """
    Represents a logical menu section (category/subcategory group) in the normalized output.
    Used for both Preview UI grouping and Superimport preparation.
    """
    path: List[str]                        # ["PIZZA"], ["PIZZA", "Specialty Pizzas"]
    slug: str                              # "pizza", "pizza-specialty-pizzas"
    position: int                          # ordering index for sections
    items: List[StructuredItem]            # items belonging to this section

    # Optional hooks for future phases (BC-safe)
    auto_group_id: NotRequired[str]        # section-level group key, if used
    meta: NotRequired[Dict[str, object]]   # arbitrary section metadata


class StructuredMenuPayload(TypedDict):
    """
    Top-level normalized payload shared across:
    - OCR preview endpoints
    - Draft creation (Phase 4 pt.12 Superimport prep)
    - Finalized export

    Existing endpoints can continue to return flat item lists; this wrapper is
    introduced in a BC-safe way and can be adopted incrementally.
    """
    sections: List[StructuredSection]

    # Optional high-level metadata (BC-safe)
    meta: NotRequired[Dict[str, object]]

    # Draft/finalize context (populated when applicable)
    draft_id: NotRequired[int]
    restaurant_id: NotRequired[Optional[int]]
    title: NotRequired[Optional[str]]
    source_job_id: NotRequired[Optional[int]]


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

Segmented = Dict[str, object]  # {"pages": int, "dpi": int, "blocks": List[Block], "meta": {...}]


# ────────────────────────────────────────────────
# 🧱 Phase 7 pt.3 — Geometry-first dataclasses (layout engine prototypes)
# These are intentionally separate from the production TypedDict API above.
# ────────────────────────────────────────────────

BBoxTuple = Tuple[int, int, int, int]  # (x1, y1, x2, y2)


@dataclass(frozen=True, slots=True)
class WordGeom:
    """
    Geometry-first word token for layout engine research.

    Coordinates use absolute pixel space unless normalized upstream.
    bbox: (x1, y1, x2, y2)
    """
    text: str
    bbox: BBoxTuple
    conf: float = 0.0  # 0–100 or 0–1 depending on source; pipeline can normalize
    page_index: int = 0
    source: Optional[str] = None
    meta: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Span:
    """
    A horizontally-merged run of words (typically within a single line).
    """
    words: Tuple[WordGeom, ...]
    bbox: BBoxTuple
    text: str
    page_index: int = 0
    meta: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BlockGeom:
    """
    A geometric block cluster (paragraph/region) made of spans/lines.
    """
    id: str
    page_index: int
    bbox: BBoxTuple
    lines: Tuple[Tuple[Span, ...], ...]  # lines -> spans
    merged_text: str

    # Proto-labels (pt.4) — BC-safe, optional
    label: Optional[str] = None           # "header" | "item" | "price" | "junk" | ...
    section_hint: Optional[str] = None    # e.g. "PIZZA", "WINGS"
    meta: Dict[str, object] = field(default_factory=dict)


# ────────────────────────────────────────────────
# Exports
# ────────────────────────────────────────────────

__all__ = [
    # Production/API TypedDicts
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
    # Structured Draft Output v2 / Superimport prep
    "ItemConfidence",
    "ItemProvenance",
    "PreviewItem",
    "StructuredItem",
    "StructuredSection",
    "StructuredMenuPayload",
    # Phase 7 layout prototypes
    "BBoxTuple",
    "WordGeom",
    "Span",
    "BlockGeom",
]
