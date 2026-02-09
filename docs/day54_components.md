# Day 54: Item Component Detection & Multi-Column Merge

**Sprint 8.1, Day 54 of 55** | Phase 8 — Semantic Menu Intelligence

## Summary

Added two features to the grammar parser:
1. **Item component detection** — decomposes description strings into structured toppings, sauce, preparation method, and flavor options
2. **Multi-column merge detection** — identifies OCR lines where multiple columns were merged into one line

## What Changed

### `storage/parsers/menu_grammar.py` (~1140 LOC, +207 LOC)

**New data structures:**
- `ItemComponents` dataclass: `toppings`, `sauce`, `preparation`, `flavor_options`
- `ParsedMenuItem.components` field (Optional[ItemComponents])
- `ParsedMenuItem.column_segments` field (Optional[List[str]])

**New vocabularies:**
- `_SAUCE_TOKENS` (26 entries): marinara, alfredo, pesto, bbq sauce, ranch, etc.
- `_PREPARATION_TOKENS` (17 entries): grilled, fried, baked, crispy, smoked, etc.
- `_COMPONENT_FLAVOR_TOKENS` (24 entries): hot, mild, bbq, honey bbq, buffalo, etc.
- `_INGREDIENT_CATEGORY` dict: maps every known token to its category

**New functions:**
- `_tokenize_description()` — splits descriptions on comma, &, and, or, semicolon, w/
- `_classify_components()` — classifies tokens into toppings/sauce/prep/flavors
- `_extract_components()` — convenience wrapper combining tokenize + classify
- `detect_column_merge()` — detects 5+ space gaps indicating column merges

**Integration points:**
- Component detection runs at every path that sets `result.description` in `parse_menu_line`
- Component detection runs after description merge in `parse_menu_block`
- `parse_items` includes components in the grammar dict
- Column merge detection runs as Pass 0 in `classify_menu_lines`

### `tests/test_day54_components.py` — 105 tests

| Group | Tests | What |
|-------|-------|------|
| 1 | 11 | Description tokenization |
| 2 | 8 | Sauce detection |
| 3 | 15 | Topping extraction |
| 4 | 5 | Preparation method detection |
| 5 | 3 | Flavor options detection |
| 6 | 16 | Full component integration |
| 7 | 12 | Multi-column merge detection |
| 8 | 8 | Column merge in classify_menu_lines |
| 9 | 4 | parse_items integration |
| 10 | 21 | Baseline regression |
| 11 | 4 | Full-file accuracy regression |

## Key Design Decisions

### Component Classification Priority
1. All-flavor lists detected first (2+ tokens all in flavor vocab → flavor_options)
2. Sauce tokens matched by longest-match (first sauce wins, rest → toppings)
3. Preparation prefix check (e.g., "Grilled Chicken" → prep=grilled, topping=chicken)
4. Flavor tokens in mixed lists → flavor_options
5. Everything else → toppings

### Multi-Column Merge Strategy
- **Detection only** — lines are flagged with `line_type="multi_column"` and `column_segments` populated
- **No expansion** — output list stays same length as input (1:1 mapping preserved)
- **Downstream consumers** decide whether to expand segments into separate items
- Signal: 5+ consecutive whitespace characters between text content

## Metrics

| Metric | Value |
|--------|-------|
| Day 54 tests | 105/105 (100%) |
| Day 51 baseline | 92/92 (100%) |
| Day 52 pizza grammar | 66/66 (100%) |
| Day 53 multi-menu | 86/86 (100%) |
| **Total** | **349/349 (100%)** |
| pizza_real OCR accuracy | 195/195 (100%) |
| multi-menu OCR accuracy | 188/188 (100%) |
| Multi-column detections (pizza_real) | 24 lines |
| Multi-column detections (multi-menu) | 17 lines |

## Learnings

- **Garble detection false-positives on real words** — "CHEESEBURGER" has 67% garble chars (e/s/c/r/e/e) and is 12+ chars. Must not run garble detection on individual column-split segments — only do lightweight dot-run removal.
- **Preparation words often prefix toppings** — "Grilled Chicken", "Fried Chicken", "Crispy Chicken" are single comma-delimited tokens. Need first-word prep check before longest-match topping lookup.
- **Noise stripping removes dashes** — `_strip_short_noise` drops isolated `-` tokens, which prevents separator-split path from triggering for "Meat Lovers - pepperoni" style lines. This is pre-existing behavior.
- **All-flavors heuristic is clean** — when every token in a comma-list is a known flavor, classify them as flavor_options (choose-one). This correctly handles "Hot, Mild, BBQ Honey BBQ" vs "Pepperoni, Sausage, Bacon".

## Not in Scope (deferred)

- **Pipeline integration** — wiring classify_menu_lines into ocr_pipeline.py (Sprint 8.2+)
- **Column segment expansion** — automatically splitting multi_column lines into separate items (needs downstream consumer design)
- **Dash-separator preservation** — the `-` in "Meat Lovers - pepperoni" is stripped by noise cleanup before separator detection. Low impact — CAPS-split path handles most real cases.
