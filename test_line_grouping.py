"""
Test to see what LINES are being created from words.
This will show if the garbage is happening at the line level.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from storage import ocr_pipeline, ocr_utils

def main():
    pdf_path = "fixtures/sample_menus/pizza_real.pdf"

    # Load and process
    pages = ocr_utils.pdf_to_images_from_path(pdf_path, dpi=400)
    im = pages[0]
    im, deg = ocr_utils.normalize_orientation(im)
    im_pre = ocr_utils.preprocess_page(im, do_deskew=True)
    columns = ocr_utils.split_columns(im_pre, min_gap_px=33)
    col_img = columns[0]

    print(f"Column image size: {col_img.size}")
    print()

    # Run multipass OCR
    meta = {}
    data = ocr_pipeline.run_multipass_ocr(col_img, 0, 0, meta_out=meta)

    # Convert to words
    words = []
    for i in range(len(data.get('text', []))):
        w = ocr_pipeline._make_word(i, data)
        if w:
            words.append(w)

    print(f"Total words: {len(words)}")
    print()

    # Group to lines
    lines = ocr_pipeline._group_words_to_lines(words)
    print(f"Total lines: {len(lines)}")
    print()

    # Show first 50 lines to see if garbage is here
    print("=" * 80)
    print("FIRST 50 LINES (checking for garbage merges)")
    print("=" * 80)
    print()

    for idx, ln in enumerate(lines[:50], start=1):
        bbox = ln['bbox']
        text = ln['text']
        word_count = len(ln.get('words', []))

        # Highlight suspicious lines (multiple words spanning wide distances)
        if word_count > 5 or bbox['w'] > 400:
            marker = " ⚠️ SUSPICIOUS"
        else:
            marker = ""

        print(f"Line {idx:3d} | w={word_count:2d} words | bbox: x={bbox['x']:4d} w={bbox['w']:4d} | {text[:70]}{marker}")

if __name__ == "__main__":
    main()
