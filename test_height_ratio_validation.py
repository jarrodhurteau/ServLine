"""
Day 50 - Phase 8 pt.1a: Validation Testing
Test the height ratio fix (2.0x threshold) on multiple real-world menus.

This script:
1. Processes multiple test menus through OCR
2. Logs all height ratio rejections
3. Analyzes extracted items for quality markers
4. Reports on edge cases and threshold effectiveness
"""

import sys
from pathlib import Path
from typing import List, Dict, Any
from collections import defaultdict
import re

sys.path.insert(0, str(Path(__file__).parent))

from storage import ocr_pipeline, ocr_utils


class HeightRatioValidator:
    """Validates height ratio fix across multiple menus."""

    def __init__(self):
        self.results = []
        self.height_ratio_log = []

    def validate_menu(self, menu_path: str, label: str) -> Dict[str, Any]:
        """Process a single menu and collect validation metrics."""
        print(f"\n{'='*80}")
        print(f"VALIDATING: {label}")
        print(f"Path: {menu_path}")
        print(f"{'='*80}\n")

        result = {
            "label": label,
            "path": menu_path,
            "pages": 0,
            "total_lines": 0,
            "suspicious_lines": [],  # Lines that look like garbage merges
            "height_rejections": [],  # Height ratio rejections logged
            "quality_markers": {
                "clean_items": 0,      # Lines that look like valid menu items
                "price_lines": 0,      # Lines containing prices
                "garbage_lines": 0,    # Lines with obvious garbage
            },
            "sample_lines": [],
        }

        try:
            # Handle both PDF and images
            path = Path(menu_path)
            if path.suffix.lower() == '.pdf':
                pages = ocr_utils.pdf_to_images_from_path(menu_path, dpi=400)
            else:
                from PIL import Image
                pages = [Image.open(menu_path)]

            result["pages"] = len(pages)

            for page_num, im in enumerate(pages):
                print(f"\n--- Page {page_num + 1} ---")

                # Normalize and preprocess
                im, deg = ocr_utils.normalize_orientation(im)
                im_pre = ocr_utils.preprocess_page(im, do_deskew=True)

                # Split columns
                columns = ocr_utils.split_columns(im_pre, min_gap_px=33)
                print(f"Columns detected: {len(columns)}")

                for col_idx, col_img in enumerate(columns):
                    print(f"\n  Column {col_idx + 1}:")

                    # Run multipass OCR
                    meta = {}
                    data = ocr_pipeline.run_multipass_ocr(col_img, page_num, col_idx, meta_out=meta)

                    # Convert to words
                    words = []
                    for i in range(len(data.get('text', []))):
                        w = ocr_pipeline._make_word(i, data)
                        if w:
                            words.append(w)

                    print(f"    Words: {len(words)}")

                    # Group to lines (this is where height ratio check happens)
                    lines = ocr_pipeline._group_words_to_lines(words)
                    print(f"    Lines: {len(lines)}")

                    result["total_lines"] += len(lines)

                    # Analyze each line
                    for ln in lines:
                        text = ln['text'].strip()
                        bbox = ln['bbox']
                        words_in_line = ln.get('words', [])

                        # Check for suspicious patterns
                        analysis = self._analyze_line(text, bbox, words_in_line)

                        if analysis["is_suspicious"]:
                            result["suspicious_lines"].append({
                                "text": text[:80],
                                "reason": analysis["reason"],
                                "word_count": len(words_in_line),
                                "width": bbox['w'],
                            })

                        # Classify quality
                        if analysis["is_garbage"]:
                            result["quality_markers"]["garbage_lines"] += 1
                        elif analysis["has_price"]:
                            result["quality_markers"]["price_lines"] += 1
                            result["quality_markers"]["clean_items"] += 1
                        elif analysis["is_clean"]:
                            result["quality_markers"]["clean_items"] += 1

                        # Collect sample lines (first 20)
                        if len(result["sample_lines"]) < 20:
                            result["sample_lines"].append(text[:60])

        except Exception as e:
            result["error"] = str(e)
            print(f"ERROR: {e}")

        self.results.append(result)
        return result

    def _analyze_line(self, text: str, bbox: Dict, words: List) -> Dict[str, Any]:
        """Analyze a line for quality markers."""
        analysis = {
            "is_suspicious": False,
            "is_garbage": False,
            "is_clean": True,
            "has_price": False,
            "reason": "",
        }

        # Check for price patterns
        price_pattern = r'\$?\d+\.?\d{0,2}'
        if re.search(price_pattern, text):
            analysis["has_price"] = True

        # Garbage detection
        # 1. Too many special characters
        alpha_chars = sum(1 for c in text if c.isalpha())
        total_chars = len(text.replace(' ', ''))
        if total_chars > 0:
            alpha_ratio = alpha_chars / total_chars
            if alpha_ratio < 0.5 and not analysis["has_price"]:
                analysis["is_garbage"] = True
                analysis["is_clean"] = False
                analysis["reason"] = f"low alpha ratio: {alpha_ratio:.2f}"

        # 2. Mixed case chaos (alternating case like "ChEeSY")
        if len(text) > 5:
            case_changes = sum(1 for i in range(1, len(text))
                             if text[i].isalpha() and text[i-1].isalpha()
                             and text[i].isupper() != text[i-1].isupper())
            if case_changes > 5 and len(words) <= 3:
                analysis["is_suspicious"] = True
                analysis["reason"] = f"case chaos: {case_changes} changes"

        # 3. Line too wide with many words (potential cross-item merge)
        if bbox['w'] > 500 and len(words) > 8:
            analysis["is_suspicious"] = True
            analysis["reason"] = f"wide+many words: w={bbox['w']}, words={len(words)}"

        # 4. Underscore or special character sequences
        if '_' in text or '|' in text:
            analysis["is_suspicious"] = True
            analysis["is_clean"] = False
            analysis["reason"] = "special char sequences"

        return analysis

    def print_summary(self):
        """Print summary of all validation results."""
        print("\n\n" + "="*80)
        print("VALIDATION SUMMARY - Height Ratio Fix (2.0x threshold)")
        print("="*80)

        for r in self.results:
            print(f"\n{r['label']}")
            print("-" * 40)

            if "error" in r:
                print(f"  ERROR: {r['error']}")
                continue

            print(f"  Pages: {r['pages']}")
            print(f"  Total lines: {r['total_lines']}")
            print(f"  Quality breakdown:")
            print(f"    - Clean items: {r['quality_markers']['clean_items']}")
            print(f"    - With prices: {r['quality_markers']['price_lines']}")
            print(f"    - Garbage: {r['quality_markers']['garbage_lines']}")

            # Calculate quality score
            if r['total_lines'] > 0:
                quality_pct = (r['quality_markers']['clean_items'] / r['total_lines']) * 100
                print(f"  Quality score: {quality_pct:.1f}%")

            if r['suspicious_lines']:
                print(f"\n  Suspicious lines ({len(r['suspicious_lines'])}):")
                for s in r['suspicious_lines'][:5]:
                    print(f"    - [{s['reason']}] {s['text'][:50]}...")

            if r['sample_lines']:
                print(f"\n  Sample extracted lines:")
                for line in r['sample_lines'][:10]:
                    print(f"    {line}")

        # Overall assessment
        print("\n" + "="*80)
        print("THRESHOLD ASSESSMENT")
        print("="*80)

        total_clean = sum(r['quality_markers']['clean_items'] for r in self.results if 'error' not in r)
        total_lines = sum(r['total_lines'] for r in self.results if 'error' not in r)
        total_garbage = sum(r['quality_markers']['garbage_lines'] for r in self.results if 'error' not in r)
        total_suspicious = sum(len(r['suspicious_lines']) for r in self.results if 'error' not in r)

        print(f"\nAcross all menus:")
        print(f"  Total lines processed: {total_lines}")
        print(f"  Clean lines: {total_clean} ({(total_clean/max(total_lines,1))*100:.1f}%)")
        print(f"  Garbage lines: {total_garbage} ({(total_garbage/max(total_lines,1))*100:.1f}%)")
        print(f"  Suspicious lines: {total_suspicious}")

        if total_garbage / max(total_lines, 1) < 0.10:
            print("\n[OK] Threshold appears effective (< 10% garbage)")
        elif total_garbage / max(total_lines, 1) < 0.20:
            print("\n[WARN] Threshold may need adjustment (10-20% garbage)")
        else:
            print("\n[FAIL] Threshold needs review (> 20% garbage)")


def main():
    """Run validation on multiple test menus."""
    validator = HeightRatioValidator()

    # Test menus to validate
    test_menus = [
        # Already tested - baseline
        ("fixtures/sample_menus/pizza_real.pdf", "Pizza Real (baseline)"),

        # New menus for validation
        ("uploads/bb96e443_rinaldis_pizza_1.pdf", "Rinaldi's Pizza"),
        ("uploads/ef62eb60_parker_new_pdf_menu.pdf", "Parker's PDF Menu"),

        # Image format test
        ("uploads/795f59d8_parkers_new_pizza_menu_picture_V8.jpg", "Parker's JPG Menu"),
    ]

    for path, label in test_menus:
        if Path(path).exists():
            validator.validate_menu(path, label)
        else:
            print(f"\n⚠ Skipping {label} - file not found: {path}")

    validator.print_summary()

    print("\n\nValidation complete!")
    print("Check the console output above for height ratio rejections (OCR_DEBUG logs)")


if __name__ == "__main__":
    main()
