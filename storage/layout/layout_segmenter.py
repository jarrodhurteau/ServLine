"""
Layout Segmenter — Day 20 (Phase A)
Splits OCR text into logical blocks and attaches orphan prices
to the nearest item to the left or above within the same block.
"""

import re
import uuid
from typing import List, Dict, Any, Optional

# --- Helpers ---------------------------------------------------------------

_PRICE_FULL_RX = re.compile(r"^\$?\s*\d{1,3}(?:[.,]\d{2})?\s*$")

def _is_price_line(text: str) -> bool:
    return bool(_PRICE_FULL_RX.match(text or ""))

def _normalize_header(s: str) -> str:
    """
    Clean up noisy all-caps headers like ': ANDWICHE' or 'EE BEVERAGES'
    and map common variants to canon labels.
    """
    if not s:
        return s
    t = s.strip()
    # strip leading punctuation/bullets and extra colons
    t = re.sub(r"^[\s:;,\-–—•·]+", "", t)
    # drop tiny all-caps prefixes sometimes prepended by OCR ("E ", "EE ")
    t = re.sub(r"^[A-Z]{1,2}\s+(?=[A-Z])", "", t)

    low = t.lower()
    fixes = {
        "andwich": "Sandwiches",
        "andwiches": "Sandwiches",
        "andwiche": "Sandwiches",
        "beverage": "Beverages",
        "beverages": "Beverages",
        "wings": "Wings",
        "salads": "Salads",
        "sides": "Sides & Apps",
        "apps": "Sides & Apps",
        "appetizers": "Sides & Apps",
        "pizza": "Pizza",
        "pizzas": "Pizza",
        "specialty pizzas": "Specialty Pizzas",
        "burgers & sandwiches": "Burgers & Sandwiches",
    }
    # exact or nearly exact match
    for key, val in fixes.items():
        if low == key or low.rstrip("e") == key:
            return val
    # substring hint
    for key, val in fixes.items():
        if key in low:
            return val
    # fallback: Title-Case for loud all-caps headers
    return t.title() if t.isupper() else t

def _looks_like_header(text: str) -> bool:
    if not text:
        return False
    # a "header" is short and mostly caps/words
    words = text.split()
    if not words:
        return False
    if text.isupper() and len(words) <= 6:
        return True
    if len(words) <= 6 and sum(ch.isupper() for ch in text if ch.isalpha()) >= max(1, len([c for c in text if c.isalpha()]) * 0.6):
        return True
    return False

# --- Core ------------------------------------------------------------------

def segment_layout(raw_text: str, layout: Optional[Any] = None) -> List[Dict[str, Any]]:
    """
    Very simple baseline segmentation.
    Future versions will use bbox and column detection.
    """
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    blocks: List[Dict[str, Any]] = []
    current_block: Dict[str, Any] = {"id": str(uuid.uuid4()), "header_text": None, "lines": []}

    for line in lines:
        if _looks_like_header(line):
            # close current block if it has content
            if current_block["lines"]:
                blocks.append(current_block)
            current_block = {
                "id": str(uuid.uuid4()),
                "header_text": _normalize_header(line),
                "lines": [],
            }
        else:
            current_block["lines"].append({"text": line})

    if current_block["lines"]:
        blocks.append(current_block)

    # basic orphan price attachment
    attach_orphan_prices(blocks)

    # prune any cleared lines created during attachment
    for blk in blocks:
        blk["lines"] = [ln for ln in blk["lines"] if (ln.get("text") or "").strip()]

    return blocks


def attach_orphan_prices(blocks: List[Dict[str, Any]]) -> None:
    """
    Naive orphan price logic:
    attach a standalone price line to the nearest previous non-empty item
    in the same block; if none, prepend to the next non-empty line.
    Lines that are attached are cleared and later removed by the caller.
    """
    for block in blocks:
        lines = block["lines"]
        if not lines:
            continue

        i = 0
        while i < len(lines):
            text = (lines[i].get("text") or "").strip()
            if _is_price_line(text):
                # find previous non-empty, non-price line
                attached = False
                j = i - 1
                while j >= 0:
                    prev_txt = (lines[j].get("text") or "").strip()
                    if prev_txt:
                        # if previous line is *also* a pure price, keep searching upward
                        if not _is_price_line(prev_txt):
                            lines[j]["text"] = f"{prev_txt} {text}"
                            attached = True
                            break
                    j -= 1

                if not attached:
                    # attach to next non-empty if available
                    k = i + 1
                    while k < len(lines):
                        nxt_txt = (lines[k].get("text") or "").strip()
                        if nxt_txt and not _is_price_line(nxt_txt):
                            lines[k]["text"] = f"{text} {nxt_txt}"
                            attached = True
                            break
                        k += 1

                # clear this orphan line either way
                lines[i]["text"] = ""
            i += 1
