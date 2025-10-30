"""
Variant Parser — Day 20 (Phase A)
Parses sizes and portion variants from text lines.
"""

import re

VARIANT_PATTERNS = [
    r"\\b(Small|Medium|Large|XL|XXL)\\b",
    r"\\b(\\d{1,2}\\\")\\b",
    r"\\b(Slice|Pie|Half|Whole)\\b"
]

def parse_variants(text):
    variants = []
    for pat in VARIANT_PATTERNS:
        for match in re.findall(pat, text, re.I):
            variants.append({"name": match, "confidence": 0.8})
    return variants
