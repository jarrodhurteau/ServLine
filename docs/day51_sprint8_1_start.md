# Day 51 — Phase 8 Sprint 8.1: Core Grammar & Structure (Start)

**Date**: February 9, 2026
**Sprint**: 8.1 (Days 51-55)
**Focus**: Grammar parser, phrase keywords, variant expansion, long-name heuristics

---

## Deliverables

### 1. Menu Item Grammar Parser (`storage/parsers/menu_grammar.py`)

New module that decomposes OCR text lines into structured components:

| Component | Description |
|-----------|-------------|
| `item_name` | Core menu item name |
| `description` | Toppings, ingredients, detail text |
| `modifiers` | Qualifier phrases ("extra cheese", "no onions") |
| `size_mentions` | Detected size/portion words |
| `price_mentions` | Detected price values |
| `line_type` | `menu_item` / `heading` / `description_only` / `modifier_line` / `unknown` |
| `confidence` | 0.0–1.0 parse confidence |

**Key functions:**
- `parse_menu_line(text)` — single line parsing
- `parse_menu_block(text)` — multi-line block parsing
- `parse_items(items)` — batch processing with grammar metadata

**Design**: Pizza-first grammar, pure regex + heuristic, no ML deps, non-destructive.

### 2. Phrase-Level Category Keywords (`storage/category_infer.py`)

Added `CATEGORY_PHRASES` dict with weighted multi-word patterns:
- 90+ phrases across all 10 categories
- Higher weight than single keywords (3-4x multiplier)
- Resolves ambiguity: "buffalo chicken pizza" → Pizza (not Wings)
- New `_phrase_score()` function integrated into scoring loop

### 3. Expanded Variant Vocabulary (`storage/variant_engine.py`)

| Category | Additions |
|----------|-----------|
| **Portions** | half, whole, slice, personal, family, party, single, double, triple |
| **Pizza crusts** | pan, hand-tossed, brooklyn, sicilian, detroit, neapolitan, flatbread, cauliflower, gluten-free |
| **Wing prep** | fried, grilled, baked, breaded, naked, dry rub, tossed |
| **Flavors** | mango habanero, sweet chili, sriracha, korean bbq, carolina gold, thai chili, old bay, cajun |

### 4. Improved Long-Name Split Heuristics (`storage/ai_cleanup.py`)

New semantic break-point detection before fallback splitting:
- **Parenthetical extraction**: `"Pizza (ham, pineapple)"` → name + description (works regardless of name length)
- **Descriptor phrases**: `"topped with"`, `"served with"`, `"comes with"`, `"includes"`, `"featuring"`
- **"with" connector**: `"Pizza with pepperoni sausage mushrooms"` → splits at "with"
- Token fallback threshold lowered from 10 → 8 words, head from 8 → 6 tokens

### 5. Baseline Metrics Test (`tests/test_phase8_baseline.py`)

92 test cases across 4 categories:

| Category | Tests | Pass Rate |
|----------|-------|-----------|
| Grammar parse | 18 | 100% |
| Category inference | 26 | 100% |
| Variant detection | 42 | 100% |
| Long-name rescue | 6 | 100% |
| **TOTAL** | **92** | **100%** |

---

## Files Changed

| File | Action | LOC |
|------|--------|-----|
| `storage/parsers/menu_grammar.py` | **Created** | ~310 |
| `storage/parsers/__init__.py` | **Created** | 0 |
| `storage/category_infer.py` | Modified | +160 |
| `storage/variant_engine.py` | Modified | +35 |
| `storage/ai_cleanup.py` | Modified | +30 |
| `tests/test_phase8_baseline.py` | **Created** | ~280 |

---

## Git Commits

1. `cf02dbe` — Tasks 1-3 checkpoint (grammar parser + phrase keywords + variant expansion)
2. (pending) — Tasks 4-5 (long-name heuristics + baseline test)

---

## Next Steps (Day 52)

Per Sprint 8.1 plan:
- Implement pizza-specific grammar rules (compound items, topping patterns)
- Test grammar parser on real OCR output from Day 50 validated menus
- Iterate on edge cases in grammar + long-name splitting
