"""
CRITICAL TEST: Trace the EXACT flow that the web app uses.

This script replicates portal/app.py → ocr_facade.build_structured_menu()
to show what text is being extracted and where garbage appears.
"""

import sys
from pathlib import Path
import json

sys.path.insert(0, str(Path(__file__).parent))

from storage.ocr_facade import build_structured_menu
from storage.ocr_pipeline import segment_document

def main():
    pdf_path = "fixtures/sample_menus/pizza_real.pdf"

    if not Path(pdf_path).exists():
        print(f"ERROR: Test file not found: {pdf_path}")
        return

    print("=" * 80)
    print("TEST 1: Direct segment_document call (same as debug scripts)")
    print("=" * 80)
    print()

    # Call segment_document directly (what debug scripts do)
    seg_result = segment_document(pdf_path=pdf_path, dpi=400)
    text_blocks_direct = seg_result.get("text_blocks", [])

    print(f"Total text blocks: {len(text_blocks_direct)}")
    print()
    print("First 10 text blocks:")
    print("-" * 80)

    for idx, tb in enumerate(text_blocks_direct[:10], start=1):
        merged = tb.get("merged_text") or ""
        bbox = tb.get("bbox", {})
        role = tb.get("role", "?")
        print(f"{idx:3d}. [{role:10s}] {merged[:60]}")
        print(f"      bbox: x={bbox.get('x', 0):4d} y={bbox.get('y', 0):4d} w={bbox.get('w', 0):4d}")
        print()

    print()
    print("=" * 80)
    print("TEST 2: build_structured_menu call (what web app does)")
    print("=" * 80)
    print()

    # Call build_structured_menu (what web app does)
    try:
        structured, debug_payload = build_structured_menu(pdf_path)

        print("STRUCTURED OUTPUT (menu items):")
        print("-" * 80)

        categories = structured.get("categories", [])
        print(f"Total categories: {len(categories)}")
        print()

        total_items = 0
        for cat in categories[:5]:
            cat_name = cat.get("name", "?")
            items = cat.get("items", [])
            total_items += len(items)
            print(f"Category: {cat_name} ({len(items)} items)")

            for item in items[:3]:
                name = item.get("name", "?")
                desc = item.get("description", "")[:50]
                sizes = item.get("sizes", [])
                print(f"  - {name}")
                if desc:
                    print(f"    Desc: {desc}")
                if sizes:
                    print(f"    Sizes: {sizes}")
                print()

        print(f"Total items extracted: {total_items}")
        print()

        # Compare layout text blocks in both
        layout_blocks = debug_payload.get("layout", {}).get("text_blocks", [])
        print(f"Layout text blocks in debug_payload: {len(layout_blocks)}")

        # Save full output for inspection
        output_file = "test_full_ocr_output.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({
                "structured": structured,
                "debug_layout_blocks": layout_blocks[:30],
                "direct_text_blocks": text_blocks_direct[:30],
            }, f, indent=2, ensure_ascii=False)

        print(f"Full output saved to: {output_file}")

    except Exception as e:
        print(f"ERROR in build_structured_menu: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
