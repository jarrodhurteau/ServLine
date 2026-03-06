#!/usr/bin/env python3
"""CLI tool for testing menu extraction with debug logging.

Usage:
    python tools/run_extraction.py uploads/XXXX_pizza_real.pdf

Runs the full Call 1 extraction (multimodal + thinking if enabled)
and prints a summary. Debug log written to storage/logs/.
"""
import sys
import os
import json
import time

# Add project root to path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Load .env before importing modules that need API keys
from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

from storage.ai_menu_extract import (
    extract_menu_items_via_claude,
    EXTENDED_THINKING,
    THINKING_MODEL,
)


def get_ocr_text(pdf_path: str) -> str:
    """Run Tesseract OCR on the PDF to get hint text."""
    try:
        from pdf2image import convert_from_path
        import pytesseract

        images = convert_from_path(pdf_path, dpi=300)
        texts = []
        for img in images:
            text = pytesseract.image_to_string(img, config="--oem 1 --psm 3")
            texts.append(text)
        return "\n".join(texts)
    except Exception as e:
        print(f"OCR failed: {e}")
        return "(OCR failed)"


def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/run_extraction.py <menu_pdf_path>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    if not os.path.exists(pdf_path):
        print(f"File not found: {pdf_path}")
        sys.exit(1)

    print(f"Menu: {pdf_path}")
    print(f"EXTENDED_THINKING: {EXTENDED_THINKING}")
    print(f"THINKING_MODEL: {THINKING_MODEL}")
    print("-" * 60)

    # Step 1: OCR
    print("[OCR] Running Tesseract...")
    t0 = time.time()
    ocr_text = get_ocr_text(pdf_path)
    ocr_time = time.time() - t0
    print(f"[OCR] Done: {len(ocr_text)} chars in {ocr_time:.1f}s")

    # Step 2: Claude extraction
    print(f"\n[Call 1] Calling Claude ({THINKING_MODEL if EXTENDED_THINKING else 'claude-sonnet-4-5'})...")
    t0 = time.time()
    items = extract_menu_items_via_claude(
        ocr_text,
        image_path=pdf_path,
        use_thinking=EXTENDED_THINKING,
    )
    api_time = time.time() - t0

    if items is None:
        print(f"\n[FAIL] No items returned ({api_time:.1f}s)")
        print("Check storage/logs/ for debug details")
        sys.exit(1)

    # Step 3: Summary
    print(f"\n{'=' * 60}")
    print(f"RESULTS: {len(items)} items extracted in {api_time:.1f}s")
    print(f"{'=' * 60}")

    # Category breakdown
    cats = {}
    for it in items:
        c = it.get("category", "Other")
        cats[c] = cats.get(c, 0) + 1
    print("\nCategory Breakdown:")
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    # Size coverage
    with_sizes = sum(1 for it in items if it.get("sizes"))
    total_sizes = sum(len(it.get("sizes", [])) for it in items)
    print(f"\nSize Variants: {with_sizes}/{len(items)} items have sizes ({total_sizes} total size entries)")

    # Items by category
    print(f"\n{'=' * 60}")
    print("ITEMS BY CATEGORY:")
    print(f"{'=' * 60}")
    by_cat = {}
    for it in items:
        c = it.get("category", "Other")
        by_cat.setdefault(c, []).append(it)

    for cat in sorted(by_cat.keys()):
        items_in_cat = by_cat[cat]
        print(f"\n--- {cat} ({len(items_in_cat)}) ---")
        for it in items_in_cat:
            name = it["name"]
            price = it.get("price", 0)
            sizes = it.get("sizes", [])
            desc = it.get("description", "")
            if sizes:
                size_str = ", ".join(f'{s.get("label", "?")}=${s.get("price", 0):.2f}' for s in sizes)
                print(f"  {name} [{len(sizes)} sizes: {size_str}]")
            elif price > 0:
                print(f"  {name} ${price:.2f}")
            else:
                print(f"  {name}")
            if desc:
                print(f"    -> {desc[:80]}")

    # Find latest debug log
    logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "logs")
    if os.path.exists(logs_dir):
        logs = sorted(os.listdir(logs_dir))
        if logs:
            print(f"\nDebug log: storage/logs/{logs[-1]}")


if __name__ == "__main__":
    main()
