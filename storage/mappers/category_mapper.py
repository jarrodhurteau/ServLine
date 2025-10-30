"""
Category Mapper — Day 20 (Phase A)
Maps raw headers to normalized taxonomy labels.
"""

import difflib

def map_category(header_text, taxonomy):
    if not header_text or not taxonomy:
        return None
    header = header_text.lower().strip()
    best_match = difflib.get_close_matches(header, taxonomy, n=1, cutoff=0.6)
    return best_match[0] if best_match else None
