"""
Layout Segmenter — Day 20 (Phase A)
Splits OCR text into logical blocks and attaches orphan prices
to the nearest item to the left or above within the same block.
"""

import re
import uuid

def segment_layout(raw_text, layout=None):
    """
    Very simple baseline segmentation.
    Future versions will use bbox and column detection.
    """
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    blocks = []
    current_block = {"id": str(uuid.uuid4()), "header_text": None, "lines": []}

    for line in lines:
        if line.isupper() and len(line.split()) < 6:
            # new section header
            if current_block["lines"]:
                blocks.append(current_block)
            current_block = {"id": str(uuid.uuid4()), "header_text": line, "lines": []}
        else:
            current_block["lines"].append({"text": line})

    if current_block["lines"]:
        blocks.append(current_block)

    # basic orphan price attachment
    attach_orphan_prices(blocks)
    return blocks


def attach_orphan_prices(blocks):
    """
    Naive orphan price logic:
    attach a standalone price line to the nearest item
    either to the left (same line) or above (previous line).
    """
    price_pattern = re.compile(r"\\$?\\d{1,3}(?:\\.\\d{2})?")
    for block in blocks:
        lines = block["lines"]
        for i, line in enumerate(lines):
            text = line["text"]
            if price_pattern.fullmatch(text):
                # orphan line, attach above if possible
                if i > 0:
                    lines[i - 1]["text"] += f" {text}"
                else:
                    # first line, append to next if exists
                    if len(lines) > 1:
                        lines[i + 1]["text"] = f"{text} {lines[i + 1]['text']}"
                lines[i]["text"] = ""  # clear orphan line
