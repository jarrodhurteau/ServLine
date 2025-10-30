"""
AI OCR Helper (Heuristics Baseline)
Day 20 — ServLine
"""

from storage.parsers.price_parser import parse_prices
from storage.parsers.variant_parser import parse_variants
from storage.mappers.category_mapper import map_category
from storage.layout.layout_segmenter import segment_layout
from storage.scoring.confidence import score_confidence

def heuristics_extract(blocks, taxonomy):
    """
    Baseline heuristic extraction from segmented OCR blocks.
    Returns a partial structured draft with sections, items, and basic fields.
    """
    structured = {"sections": [], "items": []}
    for block in blocks:
        category = map_category(block.get("header_text", ""), taxonomy)
        for line in block.get("lines", []):
            item = {
                "name": line.get("text", "").strip(),
                "description": None,
                "category": category,
                "price_candidates": parse_prices(line.get("text", "")),
                "variants": parse_variants(line.get("text", "")),
                "confidence": 0.5,  # updated later
                "provenance": {"block_id": block.get("id")}
            }
            structured["items"].append(item)
    return structured


def analyze_ocr_text(raw_text, layout=None, taxonomy=None, restaurant_profile=None):
    """
    Main entrypoint. For Phase A, runs heuristics only (no LLM).
    """
    taxonomy = taxonomy or []
    layout_blocks = segment_layout(raw_text, layout)
    draft = heuristics_extract(layout_blocks, taxonomy)
    draft = score_confidence(draft)
    return draft
