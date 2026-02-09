# Day 53: Multi-Menu Grammar Testing & Edge Case Hardening

**Date**: Day 53 (February 9, 2026)
**Sprint**: 8.1 — Core Grammar & Structure
**Status**: Complete

---

## Summary

Tested the grammar parser (built pizza-first on Days 51-52) against a full 244-line real restaurant menu covering pizza, calzones, appetizers, wings, burgers, sandwiches, and wraps. Identified 8 issue categories, implemented fixes for 6 of them, and achieved 100% classification on both the pizza-focused and full-menu OCR outputs.

---

## Issues Found & Fixed

### P1: Broader Description Detection (11 lines fixed)
- Expanded `_COMMON_TOPPINGS` from 33 to 60+ entries with broader restaurant ingredients: ranch, sour cream, pickles, mayo, lettuce, american cheese, hot sauce, tzatziki, french fries, etc.
- Raised `description_only` word limit from 8 to 14 words
- Added **lowercase-start continuation heuristic**: lines starting lowercase with commas or "and" and no price → `description_only`

### P2: Contextual Multi-Pass Classification (23 lines fixed)
New function `classify_menu_lines(lines)` with three-pass approach:
1. **First pass**: classify each line independently (existing `parse_menu_line`)
2. **Second pass**: neighbor-based heading resolution — reclassify headings followed by description_only/price_only, or sandwiched between items
3. **Third pass**: heading cluster detection — runs of 2+ consecutive headings that aren't known section headings get reclassified as menu items

This resolves the **stateless heading ambiguity** problem where items like FRENCH FRIES, CURLY FRIES, ONION RINGS, CHEESEBURGER MELT were indistinguishable from section headings without context.

### P3: Expanded Info Line Patterns (8 lines fixed)
- Added `_FLAVOR_LIST_RE` for ALL-CAPS comma-separated flavor/sauce lists
- Added `_OPTION_LINE_RE` for "X or Y" option choice lines (Naked or Breaded, White or Wheat)
- Added "Add [item] $X extra" and "X toppings same as Y" patterns to `_INFO_LINE_RE`

### P4: Post-Garble Residue Cleanup (~8 lines improved)
New `_strip_short_noise()` function removes:
- Isolated 1-3 char non-word tokens (but preserves prices, "&", "w/", real numbers)
- Mid-length (4-11 char) garble residue with 85%+ garble char ratio and low uniqueness
- Mixed digit/letter noise tokens (alpha < 40% of chars)
- Triple-repeat 3-char fragments like "eee"

### P5: W/ and Wi Normalization (4 lines fixed)
New `_normalize_w_slash()` preprocessor normalizes OCR variants:
- `W/` → "with" (case insensitive)
- `Wi ` before consonant → "with " (common Tesseract misread of "W/")

### Infrastructure: Known Section Headings
Promoted `_HEADING_PHRASES` from function-local to module-level `_KNOWN_SECTION_HEADINGS` for reuse in contextual pass. Added new entries: "wraps city", "build your own calzone!", "build your own!".

---

## Not in Scope (deferred)
- **Multi-column merge detection** (5 lines) — needs OCR spatial data, deferred to Days 54-55
- **CAPS split OCR typo edge cases** (3 lines) — low impact, deferred

---

## Test Results

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51 baseline | 92 | 100% |
| Day 52 pizza grammar | 66 | 100% |
| Day 53 multi-menu | 86 | 100% |
| **TOTAL** | **244** | **100%** |

### Multi-Menu Accuracy (3d7419be — 188 non-empty lines)

| Metric | Single-Pass | Multi-Pass |
|--------|-------------|------------|
| Classification rate | 100% | 100% |
| menu_item | 86 | 109 |
| heading | 36 | 13 |
| description_only | 33 | 33 |
| info_line | 10 | 10 |
| price_only | 16 | 16 |
| size_header | 4 | 4 |
| topping_list | 3 | 3 |

**23 headings reclassified to menu_item** by contextual pass.

### Pizza Real Regression (pizza_real_p01 — 195 non-empty lines)
- Classification rate: **100%** (unchanged)

---

## Artifacts
- `storage/parsers/menu_grammar.py` — Grammar parser (~810 LOC, +200 from Day 52)
- `tests/test_day53_multi_menu.py` — Day 53 test suite (86 cases)
- `docs/day53_multi_menu.md` — This document

---

## Key Learnings

1. **Heading vs item ambiguity is contextual** — single-line heuristics can't distinguish "FRENCH FRIES" (item) from "APPETIZERS" (heading). A multi-pass approach with known section headings resolves this.

2. **Heading clusters are strong signals** — real section headings are isolated; runs of 2+ consecutive "headings" that aren't known sections are almost always menu items.

3. **Post-garble cleanup needs a secondary pass** — the main garble detector's dual-signal threshold correctly avoids false positives on real food words, but leaves mid-length noise fragments. A targeted secondary cleanup with higher thresholds catches these.

4. **Lowercase-start is a powerful description signal** — lines starting with a lowercase letter, containing commas or "and", with no price are almost always description continuations.

5. **Broader ingredient vocabulary enables non-pizza menu parsing** — adding condiments, accompaniments, and proteins to the topping set extends description detection from pizza-only to full restaurant menus.
