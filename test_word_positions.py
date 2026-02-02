"""
Check the actual X,Y positions of words in the first suspicious line.
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

    # Run multipass OCR
    meta = {}
    data = ocr_pipeline.run_multipass_ocr(col_img, 0, 0, meta_out=meta)

    # Convert to words
    words = []
    for i in range(len(data.get('text', []))):
        w = ocr_pipeline._make_word(i, data)
        if w:
            words.append(w)

    # Group to lines (without debug spam)
    import io
    import contextlib

    with contextlib.redirect_stdout(io.StringIO()):
        lines = ocr_pipeline._group_words_to_lines(words)

    # Find the line with "Olive CHEESY NO STEAK BBQ"
    target_line = None
    for ln in lines:
        if "Olive" in ln['text'] and "CHEESY" in ln['text']:
            target_line = ln
            break

    if not target_line:
        print("Target line not found!")
        return

    print("=" * 80)
    print("SUSPICIOUS LINE: " + target_line['text'])
    print("=" * 80)
    print()

    words_in_line = target_line.get('words', [])
    print(f"Number of words: {len(words_in_line)}")
    print()

    print("Word positions:")
    for i, w in enumerate(words_in_line, start=1):
        bbox = w['bbox']
        x = bbox['x']
        y = bbox['y']
        width = bbox['w']
        height = bbox['h']
        text = w['text']

        # Calculate gap to previous word
        if i > 1:
            prev_bbox = words_in_line[i-2]['bbox']
            prev_right = prev_bbox['x'] + prev_bbox['w']
            gap = x - prev_right
        else:
            gap = 0

        print(f"  Word {i}: '{text:12s}' | x={x:4d} y={y:4d} w={width:3d} h={height:2d} | gap from prev: {gap:4d}px")

if __name__ == "__main__":
    main()
