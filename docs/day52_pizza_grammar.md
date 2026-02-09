# Day 52: Pizza-Specific Grammar Rules & Real OCR Testing

**Date**: February 9, 2026
**Sprint**: 8.1 — Core Grammar & Structure (Days 51-55)
**Status**: Complete

---

## Summary

Made the grammar parser work on real pizza menu OCR output. Added 8 enhancements to `storage/parsers/menu_grammar.py` that handle the messy patterns Tesseract produces from physical menus: garbled dot leaders, ALL CAPS item names, multi-price lines, size grid headers, topping sections, and orphaned prices.

---

## Changes

### 1. OCR Dot-Leader Garble Stripping (Step 0.5)

Tesseract reads physical dot leaders between item names and prices as garbled lowercase runs:
- `coseeee` → stripped
- `ssssvvssseecsscssssssssescstvsesneneeosees` → stripped
- `Rcccccerccrrrerseessrsessstessesssssrressesrsorsrrsmrcermesees` → stripped

Detection uses dual-signal validation: a span must have 2+ of these signals to be classified as garble:
- Triple character repeat (e.g., `sss`, `eee`)
- High ratio (55%+) of hallucination characters (s, e, c, r, n, o, t, v, w)
- Low unique character ratio (45% or less)
- Long run (12+ chars without space)

Real food words pass safely: "pepperoni", "mozzarella", "sausage", "Hamburger" all have high character diversity and no triple repeats.

### 2. Comma-Decimal Price Support

Extended `_PRICE_RE` and `_TRAILING_PRICE_RE` to match `\d{1,3}[.,]\d{2}`. Real OCR frequently reads `34,75` instead of `34.75`.

### 3. Size Grid Header Detection (new line_type: `size_header`)

Recognizes lines like `10"Mini 12" Sml 16"lrg Family Size` and `8 Slices 12 Slices 24 Slices`. Requires 2+ size mentions and no prices.

### 4. Topping List / Info Line Detection (new line_types: `topping_list`, `info_line`)

Detects informational context lines that are not menu items:
- `MEAT TOPPINGS: Pepperoni -Chicken - Bacon...` → topping_list
- `PIZZA & CALZONE TOPPINGS` → topping_list
- `Choice of Sauce; Red, White, Pesto...` → info_line
- `All calzones stuffed with ricotta...` → info_line

Runs **before** heading detection so "PIZZA & CALZONE TOPPINGS" is classified as `topping_list` (not generic `heading`).

### 5. Orphaned Price-Only Line Detection (new line_type: `price_only`)

Detects prices separated from items by OCR line breaks: `. 34.75`, `-- $4.75`, `» 34,75`, bare `34.75`.

### 6. ALL CAPS Name + Mixed-Case Description Split (Step 5a)

The dominant pattern in gourmet pizza sections:
- `MEAT LOVERS Pepperoni, Sausage, Bacon, Ham & Hamburger` → name: "MEAT LOVERS", desc: "Pepperoni, Sausage, Bacon, Ham & Hamburger"
- `ALFREDO PIZZA Broccoli & Chicken w/ Alfredo Sauce` → name: "ALFREDO PIZZA", desc: "Broccoli & Chicken w/ Alfredo Sauce"

Conservative with 1-word CAPS abbreviations (BBQ, BLT): only splits when description starts lowercase or has early commas.

### 7. Multi-Price Text Stripping

When 2+ prices found, strips ALL price tokens from text (not just trailing). Fixes lines like `CHEESE 8.00 11.50 13.95 22.50` leaving prices in the item name.

### 8. Enhanced parse_menu_block

Handles new line types: skips `size_header`/`topping_list`/`info_line` metadata, merges `price_only` into preceding item.

---

## Updated Parse Flow

```
Step 0.5: Strip OCR dot-leader garble       [NEW]
Step 0.7: Topping/info line detection        [NEW] → topping_list | info_line
Step 1:   Heading detection                  [existing]
Step 1.5: Size header detection              [NEW] → size_header
Step 1.7: Price-only line detection          [NEW] → price_only
Step 2:   Extract prices (multi-price fix)   [MODIFIED]
Step 3:   Extract size mentions              [existing]
Step 4:   Extract modifiers                  [existing]
Step 5:   Split name/desc via separator      [existing]
Step 5a:  ALL CAPS name + mixed-case split   [NEW]
Step 6:   Fallback classification            [existing]
```

---

## Test Results

### Day 52 Tests (66 cases — 100%)

| Group | Tests | Pass Rate |
|-------|-------|-----------|
| OCR garble stripping | 12 | 100% |
| ALL CAPS + mixed-case split | 10 | 100% |
| Size header detection | 5 | 100% |
| Topping / info lines | 7 | 100% |
| Price-only detection | 8 | 100% |
| Multi-price handling | 5 | 100% |
| Baseline regression | 18 | 100% |
| Real OCR accuracy | 1 | 100% |
| **TOTAL** | **66** | **100%** |

### Baseline Regression (92 cases — 100%)

All Day 51 baseline tests continue to pass.

### Real OCR Accuracy (pizza_real_p01.ocr_used_psm3.txt)

| Metric | Value |
|--------|-------|
| Total lines | 258 |
| Non-empty lines | 195 |
| Classified (not "unknown") | 195 (100%) |
| Menu items with name | 117 |
| Items with prices | 48 |
| Headings detected | 31 |
| Size headers detected | 4 |
| Topping lists detected | 3 |
| Info lines detected | 5 |
| Orphaned prices detected | 16 |
| Description-only lines | 18 |

**Classification rate: 100%** (target was 75%)

---

## Key Learnings

1. **Garble stripping needs dual-signal validation** — single-signal (e.g., just "high garble char ratio") would false-positive on real food words like "lettuce" (71% garble chars). Requiring 2+ signals prevents this.

2. **Topping/info detection must run before heading detection** — "PIZZA & CALZONE TOPPINGS" is both ALL CAPS and contains "toppings". The more specific classification (topping_list) should win.

3. **Single-word CAPS abbreviations need conservative handling** — "BBQ Chicken Pizza" should NOT split at "BBQ" because it's an abbreviation prefix. Only split 1-word CAPS when description starts lowercase or has commas.

4. **Comma decimals are common in OCR** — Tesseract frequently reads `34.75` as `34,75` from real menus. The price regex must accept both.

---

## Artifacts

- `storage/parsers/menu_grammar.py` — Updated grammar parser (~610 LOC, +180 from Day 51)
- `tests/test_day52_pizza_grammar.py` — Day 52 test suite (66 cases)
- `docs/day52_pizza_grammar.md` — This document
