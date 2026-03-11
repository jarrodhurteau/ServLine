# ServLine

ServLine is a **portal + API + AI “One Brain” system** for restaurant menu onboarding:

**OCR + structured imports → living editable menu → export to POS**

The core mission is to eliminate manual menu onboarding by reliably parsing **real-world menus** (photos, PDFs, CSV/XLSX/JSON) into structured, editable data.

This repository follows a **phased, milestone-driven build plan**, with Git commits marking verified progress.

---

## 🎯 Project North Star

> Upload a real restaurant menu → accurately parsed → editable draft → POS-ready export  
> **No manual re-entry. No desktop dependency. No OCR surprises.**

Primary value:
- **Accuracy on messy, real menus**
- **Convenience during onboarding**

Target buyer:
- POS companies (Square, Toast, etc.)

---

## 📁 Repository Structure

```
servline/
portal/        # Flask portal (uploads, drafts, editor, imports)
infra/         # Local infra helpers (ngrok, run/stop scripts)
storage/       # 🧠 One Brain (OCR + semantics + validation)
fixtures/      # Sample menus and test assets
uploads/       # User-uploaded menu files
README.md
```

---

## 🧠 One Brain Architecture (Authoritative)

ServLine uses a **single canonical OCR + AI + semantic brain**:

- No duplicated OCR logic
- No legacy fallbacks
- No parallel pipelines

**Entrypoint:**  
`storage/ocr_facade.py`

Result:
- Predictable behavior
- Auditable OCR decisions
- Debuggable artifacts
- Safe AI cleanup

---

## ✅ Completed Milestones (Verified)

### 🚀 Day 1–14 — Portal, Data Model, Draft Editor
- Core Flask UI
- Database schema
- Draft lifecycle
- Exports
- Error handling

---

### 🚀 Day 15 — Failed Split Attempt (Reverted)
- Experimental change reverted
- Baseline preserved

---

### 🚀 Day 16–19 — OCR Infrastructure & Precision
- OCR pipeline stabilization
- CLAHE, grayscale, sharpening
- Draft Editor refinements

---

### 🚀 Day 20–22 — AI Cleanup (Phase A)
- AI Preview / AI Finalize
- Safe cleanup baseline
- Unified export path

---

### 🚀 Day 23–25 — Phase 3: Semantic Reconstruction
- Rotation preview
- Category inference
- Two-column merge
- Variant detection
- Confidence overlays
- Garbage tuning

**Phase 3 complete.**

---

### 🚀 Day 26–31 — Phase 4: Structural OCR System
- Semantic block understanding
- Multi-line merging
- Variant normalization
- Category hierarchy v2
- Price Integrity Engine v2
- Structured Draft Output v2
- Superimport bundle
- Stability hardening

**Phase 4 complete.**

---

### 🚀 Day 32–35 — Phase 5: AI Text Surgeon
- Non-hallucinating cleanup
- Ingredient smoothing
- Size/variant-aware rewrites
- Price/category/variant protection
- Safety tagging (`[AI Cleaned]`)

**Phase 5 complete.**

---

### 🛠️ Day 36 — Phase 5 Cleanup Day
- Full end-to-end validation
- Integrity guarantees proven
- Quality guards validated

**Day 36 complete.**

---

## 🧠 ONE BRAIN MIGRATION — COMPLETE

All OCR, AI, and semantic logic centralized into `/storage`.

Achievements:
- Single canonical OCR library
- Health endpoint verified
- Legacy OCR retired
- Draft + AI Finalize fully unified

**Result:** ServLine now operates with a true One Brain architecture.

---

## 🧮 Phase 6 — Structured Imports (No OCR)

### Day 37–41 — CSV / XLSX / JSON Imports
- Structured import APIs
- CSV/XLSX/JSON parsing & validation
- Draft Editor compatibility
- Column Mapping UI (CSV/XLSX)
- AI Finalize support
- Unified progress & export flow

**Phase 6 complete.**

---

## 🧠 Phase 7 — Vision & OCR Hardening (COMPLETED)

Phase 7 focused on eliminating OCR unpredictability and hardening the system so results on real-world menus are **deterministic, debuggable, and trustworthy**.

---

### 🧠 Day 42–43 — OCR Path Audit & Debug Stabilization
- Verified single OCR → Draft path
- Removed duplicate routes
- Hardened debug endpoints

---

### 🔧 Day 44 — Maintenance & Diagnosis
- Confirmed OCR input correctness
- Verified debug artifacts
- Identified orientation + scoring issues

---

### 🧠 Day 45 — Orientation Enforcement & OCR Reality Fixes
- Deterministic orientation normalization
- Legacy auto-rotate disabled
- OCR input artifacts persisted
- Numeric corruption fixes

---

### 🧠 Day 46 — Rotation Sweep (Worker Wiring)
- Rotation sweep across 0° / 90° / 180° / 270°
- Quality-based rotation selection
- Debug logging + artifacts
- Verified on rotated PDFs

---

### 🟢 Day 47 — Phase 7 pt.9: Multi-pass OCR Improvements
- Lowered fusion threshold (92 → 70)
- Disabled forced 2-column split
- Disabled incorrect multipass rotation restriction
- OCR reliably recovers text from rotated PDFs

---

### ✅ Day 48 — Phase 7 pt.10: Scoring & Selection (COMPLETE)

- Deterministic winner selection across OCR passes
- Confidence-weighted fusion finalized
- Persisted per-job OCR metadata:
  - `rotation_selected`
  - `psm_selected`
  - `quality_score`
  - rejection flags (non-destructive)
- OCR output now reflects **true recognition quality**, not orientation or scoring artifacts

---

### ✅ Day 49 — Phase 7 pt.11: Line Grouping Fix (COMPLETE)

**Problem:** Garbage OCR text extraction from real pizza menu (e.g., "'mindsmt Ttrq_familystre", "Olive CHEESY NO STEAK BBQ") persisted despite previous fixes.

**Root Cause Identified:**
- Words from different menu items were being merged into single lines
- Merging occurred because words had:
  - Same Y-coordinate (after 270° PDF rotation)
  - Small horizontal gaps (12-15px, below 84px threshold)
  - **But wildly different heights** (38px to 121px, up to 3x variation)
- Height variation proved words were from different items (different font sizes)

**Diagnostic Tools Created:**
- [test_full_ocr_flow.py](test_full_ocr_flow.py) — Traced web app execution flow, confirmed garbage in segment_document output
- [test_line_grouping.py](test_line_grouping.py) — Confirmed garbage at LINE grouping level
- [test_word_positions.py](test_word_positions.py) — **Critical discovery:** Revealed 3x height variation in merged words

**Fixes Applied:**
- [ocr_pipeline.py:1745](storage/ocr_pipeline.py#L1745) — Added height ratio check in `_group_words_to_lines()`
  - Rejects words with >2.0x height difference from line average
  - Prevents merging "Olive"(h=59) + "CHEESY"(h=121) → 2.05x ratio
- [ocr_utils.py:871](storage/ocr_utils.py#L871) — Removed dangerous `align_ok` fallback in `group_text_blocks()`

**Result:**
- Job #186 (pizza_real.pdf) extracted **22 recognizable menu items** vs. previous garbage
- Server logs confirm height ratio checks working correctly
- Items now have sensible names: "CHEESE", "mushrooms", "Roasted", "Choice", etc.

**Phase 7 complete.**

---

### ✅ Day 50 — Phase 8 pt.1: Validation & Planning (COMPLETE)

**Validation Testing (pt.1a):**
- Tested height ratio fix on 4 real-world menus
- Results: 1,778 lines processed, 100% clean, 0% garbage
- 2.0x threshold confirmed optimal — no adjustment needed

| Menu | Lines | Clean | Garbage |
|------|-------|-------|---------|
| Pizza Real (baseline) | 814 | 100% | 0 |
| Rinaldi's Pizza | 832 | 100% | 0 |
| Parker's PDF Menu | 94 | 100% | 0 |
| Parker's JPG Menu | 38 | 100% | 0 |

**Phase 8 Planning (pt.1b):**
- Reviewed all 5 semantic extraction modules
- Identified improvement priorities across 4 sprints
- Created detailed planning document

**Artifacts Created:**
- [test_height_ratio_validation.py](test_height_ratio_validation.py) — Reusable validation script
- [docs/day50_validation_results.md](docs/day50_validation_results.md) — Full validation results
- [docs/phase8_planning.md](docs/phase8_planning.md) — Phase 8 implementation plan

**Day 50 complete. Phase 8 implementation begins Day 51.**

---

### ✅ Day 51 — Sprint 8.1 Start: Grammar Parser & Semantic Expansion (COMPLETE)

**Menu Item Grammar Parser** (`storage/parsers/menu_grammar.py` — new):
- Decomposes OCR text lines into structured components: name, description, modifiers, sizes, prices
- Classifies lines as `menu_item`, `heading`, `description_only`, `modifier_line`, or `unknown`
- Pizza-first grammar with heading detection, separator parsing, and topping recognition
- Supports single-line (`parse_menu_line`) and multi-line block (`parse_menu_block`) parsing

**Phrase-Level Category Keywords** (`storage/category_infer.py`):
- Added 90+ weighted multi-word phrase patterns across all 10 categories
- Resolves ambiguity: "buffalo chicken pizza" now correctly scores as Pizza (not Wings)
- Phrases score 3-4x higher than single keywords for stronger semantic signal

**Expanded Variant Vocabulary** (`storage/variant_engine.py`):
- Portions: half, whole, slice, personal, family, party, single, double, triple
- Pizza crusts: pan, hand-tossed, brooklyn, sicilian, detroit, flatbread, cauliflower, gluten-free
- Wing prep: fried, grilled, baked, breaded, naked, dry rub, tossed
- Flavors: mango habanero, sweet chili, sriracha, korean bbq, carolina gold, thai chili

**Improved Long-Name Splitting** (`storage/ai_cleanup.py`):
- Parenthetical extraction runs before length check (always a strong split signal)
- Semantic break phrases: "topped with", "served with", "comes with", "includes"
- "with" connector splitting: "Supreme Pizza with pepperoni sausage..." splits at "with"
- Token fallback threshold lowered from 10 to 8 words for better menu coverage

**Baseline Metrics** (92 test cases):

| Category | Tests | Pass Rate |
|----------|-------|-----------|
| Grammar parse | 18 | 100% |
| Category inference | 26 | 100% |
| Variant detection | 42 | 100% |
| Long-name rescue | 6 | 100% |
| **TOTAL** | **92** | **100%** |

**Live Site Validation** (Job #187 — pizza_real.pdf):
- Full pipeline: upload → OCR → rotation sweep (270° selected) → multipass fusion (1174 tokens) → draft
- 22 items extracted and categorized (Pizza 7, Burgers & Sandwiches 10, Beverages 1, Wings, Salads)
- Height ratio + horizontal gap checks actively preventing garbage merges
- Low-confidence flagging working (1 item flagged < 65/100)
- AI Cleanup applied to all 22 items

**Artifacts:**
- [storage/parsers/menu_grammar.py](storage/parsers/menu_grammar.py) — Grammar parser (~310 LOC)
- [tests/test_phase8_baseline.py](tests/test_phase8_baseline.py) — Baseline metrics test (92 cases)
- [docs/day51_sprint8_1_start.md](docs/day51_sprint8_1_start.md) — Day 51 documentation

**Day 51 complete.**

---

### ✅ Day 52 — Sprint 8.1: Pizza-Specific Grammar Rules & Real OCR Testing (COMPLETE)

**OCR Dot-Leader Garble Stripping** (`storage/parsers/menu_grammar.py`):
- Strips Tesseract garble noise from dot leaders: `coseeee`, `ssssvvssseecsscssssssssescstvsesneneeosees`
- Dual-signal validation (triple repeats + garble char ratio + unique char ratio + length)
- Real food words preserved: "pepperoni", "mozzarella", "sausage" pass safely

**New Line Type Classifications**:
- `size_header` — Size grid headers (`10"Mini 12" Sml 16"Lrg Family Size`)
- `topping_list` — Topping section lines (`MEAT TOPPINGS: Pepperoni - Chicken...`)
- `info_line` — Informational context (`Choice of Sauce; Red, White, Pesto...`)
- `price_only` — Orphaned prices (`. 34.75`, `» 34,75`)

**ALL CAPS Name + Mixed-Case Description Split**:
- Detects dominant gourmet pizza pattern: `MEAT LOVERS Pepperoni, Sausage, Bacon, Ham & Hamburger`
- Correctly splits name from description at the case boundary
- Conservative with 1-word abbreviations (BBQ, BLT) — only splits when desc is lowercase or has commas

**Other Enhancements**:
- Comma-decimal price support (`34,75` → `34.75`)
- Multi-price text stripping (3-4 prices per line for size grids)
- Enhanced `parse_menu_block` for new line types

**Test Results** (158 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51 baseline | 92 | 100% |
| Day 52 pizza grammar | 66 | 100% |
| **TOTAL** | **158** | **100%** |

**Real OCR Accuracy** (pizza_real_p01.ocr_used_psm3.txt — 258 lines):
- Classification rate: **100%** (target was 75%)
- 118 menu items, 31 headings, 4 size headers, 3 topping lists, 5 info lines, 16 orphaned prices

**Artifacts:**
- [storage/parsers/menu_grammar.py](storage/parsers/menu_grammar.py) — Updated grammar parser (~610 LOC)
- [tests/test_day52_pizza_grammar.py](tests/test_day52_pizza_grammar.py) — Day 52 test suite (66 cases)
- [docs/day52_pizza_grammar.md](docs/day52_pizza_grammar.md) — Day 52 documentation

**Day 52 complete.**

---

### ✅ Day 53 — Sprint 8.1: Multi-Menu Grammar Testing & Edge Case Hardening (COMPLETE)

**Multi-Menu Grammar Testing** (`uploads/3d7419be_real_pizza_menu.ocr_used_psm3.txt` — 244 lines):
- Full restaurant menu: pizza, calzones, appetizers, wings, burgers, sandwiches, wraps
- 100% classification rate (single-pass and multi-pass)
- 23 heading-vs-item ambiguities resolved by contextual pass

**Contextual Multi-Pass Classification** (`storage/parsers/menu_grammar.py`):
- New `classify_menu_lines()` function with 3-pass approach
- Pass 1: Independent line classification (existing `parse_menu_line`)
- Pass 2: Neighbor-based heading resolution (heading followed by description → item)
- Pass 3: Heading cluster detection (runs of 2+ non-section headings → items)
- Resolves: FRENCH FRIES, CURLY FRIES, ONION RINGS, melts, etc. as items not headings

**Broader Description Detection**:
- Expanded ingredient vocabulary from 33 to 60+ entries (condiments, proteins, accompaniments)
- Lowercase-start continuation heuristic for description lines
- Word limit raised from 8 to 14 for description detection

**Other Enhancements**:
- Flavor list detection: ALL-CAPS comma-separated lists (HOT, MILD, BBQ...)
- Option line detection: "Naked or Breaded", "White or Wheat"
- Post-garble noise cleanup: removes mid-length garble residue and mixed-digit noise
- W/ and Wi OCR normalization: "Wi CHEESE" → "with CHEESE"

**Test Results** (244 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51 baseline | 92 | 100% |
| Day 52 pizza grammar | 66 | 100% |
| Day 53 multi-menu | 86 | 100% |
| **TOTAL** | **244** | **100%** |

**Artifacts:**
- [storage/parsers/menu_grammar.py](storage/parsers/menu_grammar.py) — Updated grammar parser (~810 LOC)
- [tests/test_day53_multi_menu.py](tests/test_day53_multi_menu.py) — Day 53 test suite (86 cases)
- [docs/day53_multi_menu.md](docs/day53_multi_menu.md) — Day 53 documentation

**Day 53 complete.**

---

### ✅ Day 54 — Sprint 8.1: Item Component Detection & Multi-Column Merge (COMPLETE)

**Item Component Detection** (`storage\parsers\menu_grammar.py`):
- Tokenizes menu item descriptions into individual components (comma, &, and, or, semicolon, w/ splits)
- Classifies tokens as toppings, sauces (30+ vocabulary), preparation methods (15+), or flavor options (20+)
- Longest-match lookup against ingredient vocabularies
- Preparation-prefix detection: "Grilled Chicken" → prep=grilled, topping=chicken
- All-flavors heuristic: when every comma-token is a known flavor → flavor_options (choose-one)

**Multi-Column Merge Detection**:
- Detects 5+ consecutive whitespace gaps as column boundaries
- Extracts text segments from each column
- Integrated into `classify_menu_lines` multi-pass (Pass 0)
- Detected 24 multi-column lines in pizza_real, 17 in multi-menu

**Test Results** (349 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51 baseline | 92 | 100% |
| Day 52 pizza grammar | 66 | 100% |
| Day 53 multi-menu | 86 | 100% |
| Day 54 components | 105 | 100% |
| **TOTAL** | **349** | **100%** |

**Artifacts:**
- [storage/parsers/menu_grammar.py](storage/parsers/menu_grammar.py) — Updated grammar parser (~1140 LOC)
- [tests/test_day54_components.py](tests/test_day54_components.py) — Day 54 test suite (105 cases)

**Day 54 complete.**

---

### ✅ Day 55 — Sprint 8.1 Finale: Pipeline Integration & Hardening (COMPLETE)

**Pipeline Integration** (`storage\ocr_pipeline.py` + `storage\parsers\menu_grammar.py`):
- New `enrich_grammar_on_text_blocks()` function wired into OCR pipeline
- Runs `classify_menu_lines()` on text blocks, attaches grammar metadata to each block
- Grammar dict includes: parsed_name, parsed_description, modifiers, sizes, prices, line_type, confidence, confidence_tier, components, column_segments
- Mirrored to preview_blocks for overlay UI access

**OCR Typo Normalization**:
- Dict-based: 88Q/880/8BQ → BBQ, Basi! → Basil
- Regex-based: piZzA → PIZZA, Smt → Sml, WI/ → W/, bracket-noise removal
- Applied before garble stripping for maximum coverage

**Confidence Tiers**:
- `confidence_tier()` maps scores to human-readable tiers: high (0.80+), medium (0.60-0.79), low (0.40-0.59), unknown (<0.40)
- Embedded in grammar metadata for every text_block

**Fallback OCR Hardening**:
- 100% classification on degraded fallback OCR files (both pizza-focused and full-menu)
- "Regular Deluxe" size header detection
- Dimension line detection (17x26", 17x24°) as info_line
- Early info-line detection before noise stripping

**Test Results** (691 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51 baseline | 92 | 100% |
| Day 52 pizza grammar | 66 | 100% |
| Day 53 multi-menu | 86 | 100% |
| Day 54 components | 105 | 100% |
| Day 55 integration | 342 | 100% |
| **TOTAL** | **691** | **100%** |

**Full OCR Coverage** — 4 files, 100% classification:

| OCR File | Lines | Non-empty | Unknown | Rate |
|----------|-------|-----------|---------|------|
| pizza_real_p01 (primary) | 258 | 195 | 0 | 100% |
| pizza_real_p01 (fallback) | 258 | 195 | 0 | 100% |
| 3d7419be_real_pizza (primary) | 244 | 188 | 0 | 100% |
| 3d7419be_real_pizza (fallback) | 244 | 188 | 0 | 100% |

**Artifacts:**
- [storage/parsers/menu_grammar.py](storage/parsers/menu_grammar.py) — Final grammar parser (~1260 LOC)
- [storage/ocr_pipeline.py](storage/ocr_pipeline.py) — Pipeline integration
- [tests/test_day55_integration.py](tests/test_day55_integration.py) — Day 55 test suite (342 cases)

**Day 55 complete. Sprint 8.1 complete.**

---

### ✅ Day 56 — Sprint 8.2 Start: Size Grid Context & Grammar-to-Variant Bridge (COMPLETE)

**Shared Size Vocabulary** (`storage/parsers/size_vocab.py` — new):
- Single source of truth for size/portion word detection and normalization
- Merges grammar parser's `_SIZE_WORDS` + variant engine's `_SIZE_WORD_MAP` into one canonical `SIZE_WORD_MAP` (~35 entries)
- Exports: `SIZE_WORD_MAP`, `SIZE_WORDS`, `SIZE_WORD_RE`, `NUMERIC_SIZE_RE`, `normalize_size_token()`
- Both `menu_grammar.py` and `variant_engine.py` now import from this shared module

**Size Grid Context Propagation** (`storage/variant_engine.py`):
- New `SizeGridContext` / `SizeGridColumn` dataclasses for tracking active column headers
- `_parse_size_header_columns()` scans size header text left-to-right, coalesces adjacent numeric+qualifier tokens (e.g., `10"` + `Mini` → `10" Mini`)
- `apply_size_grid_context()` — new pipeline Step 7.5 between price annotation and variant enrichment
- Grid lifecycle: starts at `size_header`, expires at known section headings, replaces on new `size_header`, survives info/topping/description lines
- Right-alignment for fewer prices: gourmet items with 3 prices in a 4-column grid skip the smallest size

**Grammar-to-Website Bridge** (`storage/ai_ocr_helper.py`):
- Grammar pre-scan: runs `classify_menu_lines()` on raw OCR text to build `_grid_map` (line index → active grid)
- Grammar-aware block building: `size_header` lines skipped entirely (grid metadata, not items); grammar-classified `menu_item` lines never treated as section headers even if ALL CAPS
- Grid post-pass: replaces generic "Price 1/2/3" or "Alt" variant labels with grid-mapped size labels (`10" Mini`, `12" S`, `16" L`, `Family`)

**Critical Bug Fixes**:
- **multi_column overwriting size_header** — Pass 0 in `classify_menu_lines()` unconditionally overwrote `size_header` to `multi_column` for lines with ≥5 space gaps. Size headers naturally have column gaps. Fix: skip `size_header` lines in multi-column merge pass.
- **ALL-CAPS menu items swallowed as headers** — Items like "COMBINATION", "HAWAIIAN", "ALFREDO PIZZA" treated as section headers on the website. Fix: grammar classification overrides header detection.
- **Multi-price data loss** — Old `ai_ocr_helper.py` only kept 2 of N prices. Fixed to capture ALL prices as `Price 1/2/3/...` variants when 3+ prices detected.
- **Website OCR quality** — Changed Tesseract config from `--psm 6` to `--psm 3` + preprocessing (grayscale, autocontrast, sharpen). psm 6 merged columns into 67 garbled lines; psm 3 produces 184 cleaner lines.

**Test Results** (946 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51 baseline | 92 | 100% |
| Day 52 pizza grammar | 66 | 100% |
| Day 53 multi-menu | 86 | 100% |
| Day 54 components | 105 | 100% |
| Day 55 integration | 342 | 100% |
| Day 56 variants | 237 | 100% |
| Rotation scoring | 18 | 100% |
| **TOTAL** | **946** | **100%** |

**Live Site Validation** (Import #197 — pizza_real.pdf):
- Items: 20 → 35 (75% improvement) after grammar-aware block building
- Categories detected: Pizza, Beverages, Burgers & Sandwiches, Sides & Apps
- Grid bridge working: items with multiple prices get size-labeled variants
- Previously swallowed items now visible: GRILLED CHICKEN BACON RANCH, ALFREDO, CHICKEN PARM, POTATO BACON

**Artifacts:**
- [storage/parsers/size_vocab.py](storage/parsers/size_vocab.py) — Shared size vocabulary (~95 LOC)
- [storage/variant_engine.py](storage/variant_engine.py) — Size grid bridge (~550 LOC)
- [storage/ai_ocr_helper.py](storage/ai_ocr_helper.py) — Grammar-aware website pipeline
- [storage/parsers/menu_grammar.py](storage/parsers/menu_grammar.py) — Updated grammar parser (~1265 LOC)
- [tests/test_day56_variants.py](tests/test_day56_variants.py) — Day 56 test suite (237 cases)

**Day 56 complete. Sprint 8.2 underway.**

---

### ✅ Day 57 — Variant Price Validation & Portion-Aware Rules (COMPLETE)

**Canonical Size Ordering** (`storage/parsers/size_vocab.py`):
- `size_ordinal()` returns ordinal positions for all normalized size values
- Non-overlapping ordinal ranges: inches (6-30), word sizes (10-55), portions (110-150), multiplicities (210-230), piece counts (300+)
- `size_track()` classifies sizes into tracks: "inch", "word", "portion", "piece", "multiplicity"
- Only variants on the same track are compared — items with mixed tracks validate each independently

**Variant Price Validation** (`storage/variant_engine.py`):
- `validate_variant_prices()` — new pipeline Step 8.5 after variant enrichment
- For each item with 2+ size variants: sort by canonical ordinal, check monotonic non-decreasing prices
- Flag-only (no auto-correct) — inversions produce `price_flags` with `severity="warn"`, `reason="variant_price_inversion"`
- Equal prices allowed (S=$10, M=$10, L=$14 is valid); only strict inversions flagged
- Wired into both `ocr_pipeline.py` (background) and `ai_ocr_helper.py` (website)
- `price_flags` mirrored to preview blocks for future UI display

**SIZE_WORD_MAP Gap Fix** (`storage/parsers/size_vocab.py`):
- Grid normalizes "Small" → "S", but enrichment couldn't recognize "S" back as a size
- Added canonical short forms ("s"→"S", "m"→"M", "l"→"L") — unambiguous in menu context

**Test Results** (1,188 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51 baseline | 92 | 100% |
| Day 52 pizza grammar | 66 | 100% |
| Day 53 multi-menu | 86 | 100% |
| Day 54 components | 105 | 100% |
| Day 55 integration | 342 | 100% |
| Day 56 variants | 237 | 100% |
| Day 57 price validation | 242 | 100% |
| Rotation scoring | 18 | 100% |
| **TOTAL** | **1,188** | **100%** |

**Artifacts:**
- [storage/parsers/size_vocab.py](storage/parsers/size_vocab.py) — Size vocabulary + ordinal ordering (~170 LOC)
- [storage/variant_engine.py](storage/variant_engine.py) — Variant engine + price validation (~720 LOC)
- [tests/test_day57_price_validation.py](tests/test_day57_price_validation.py) — Day 57 test suite (242 cases)

**Day 57 complete.**

---

### ✅ Day 58 — Combo Modifier Detection & Variant Labeling (COMPLETE)

**Combo Food Vocabulary** (`storage/parsers/combo_vocab.py` — new):
- ~35 side items (fries, chips, coleslaw, cheese, drink, etc.)
- Single source of truth for combo detection, mirrors `size_vocab.py` pattern
- Exports: `COMBO_FOODS`, `is_combo_food()`, `extract_combo_hints()`

**"WIFRIES" / "WI/FRIES" Normalization** (`storage/parsers/menu_grammar.py`):
- OCR patterns like "WIFRIES" → "with FRIES", "WI/FRIES" → "with FRIES"
- Built regex from single-word combo food entries
- OCR truncation tolerance: "FRIE", "CHIP" accepted as truncated forms

**Combo Kind Classification** (`storage/variant_engine.py`):
- New `kind="combo"` alongside size/flavor/style/other
- Detects "W/Food" labels and standalone food items
- Context-aware variant building with `combo_hints` from grammar parse
- Labels normalized to "W/Food" format

**Test Results** (1,463 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51-55 (Sprint 8.1) | 691 | 100% |
| Day 56 variants | 237 | 100% |
| Day 57 price validation | 242 | 100% |
| Day 58 combo modifiers | 275 | 100% |
| Rotation scoring | 18 | 100% |
| **TOTAL** | **1,463** | **100%** |

**Artifacts:**
- [storage/parsers/combo_vocab.py](storage/parsers/combo_vocab.py) — Combo food vocabulary (~80 LOC)
- [storage/variant_engine.py](storage/variant_engine.py) — Variant engine + combo detection (~720 LOC)
- [tests/test_day58_combo_modifiers.py](tests/test_day58_combo_modifiers.py) — Day 58 test suite (275 cases)

**Day 58 complete.**

---

### ✅ Day 60 — Variant Confidence Scoring + Sprint 8.2 Complete (COMPLETE)

Multi-signal per-variant confidence scoring (Pipeline Step 8.7). Each variant's confidence is now computed from 4 signal categories instead of inheriting a single price-parsing default:

| Signal | Modifier | Rationale |
|--------|----------|-----------|
| Label clarity | +0.05 (size), +0.03 (combo), +0.02 (flavor/style), -0.10 (other), -0.20 (empty) | Known vocabulary = higher confidence |
| Grammar context | +0.03 (high), 0 (medium), up to -0.10 (low) | Line parse quality affects variant reliability |
| Grid context | +0.05 when grid-applied | Structured column extraction is more reliable |
| Price flags | -0.12 (inversion), -0.15 (duplicate), -0.20 (zero price), -0.05 (mixed kinds), -0.03 (info flags) | Targeted to specific variant involved |

Each variant gets a `confidence_details` audit trail: `{base, label_mod, grammar_mod, grid_mod, flag_penalty, final}`.

**Test Results** (1,682 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51-55 (Sprint 8.1) | 691 | 100% |
| Day 56 variants | 237 | 100% |
| Day 57 price validation | 242 | 100% |
| Day 58 combo modifiers | 275 | 100% |
| Day 59 consistency | 113 | 100% |
| Day 60 confidence | 106 | 100% |
| Rotation scoring | 18 | 100% |
| **TOTAL** | **1,682** | **100%** |

**Artifacts:**
- [storage/variant_engine.py](storage/variant_engine.py) — `score_variant_confidence()` + 3 helpers (~110 LOC added)
- [storage/ocr_types.py](storage/ocr_types.py) — `confidence_details` and `kind_hint` fields on OCRVariant
- [tests/test_day60_variant_confidence.py](tests/test_day60_variant_confidence.py) — Day 60 test suite (106 cases)

**Day 60 complete. Sprint 8.2 complete.**

---

### ✅ Day 61 — Cross-Item Consistency Foundation (Sprint 8.3 Start) (COMPLETE)

**New Module: `storage/cross_item.py`** (~200 LOC):
- Three cross-item checks comparing items ACROSS the menu (per-item checks can't catch these)
- Entry function: `check_cross_item_consistency(text_blocks)` — Pipeline Step 9.1

**Check 1: Duplicate Name Detection:**
- Normalizes names (lowercase, strip "The"/"Our"/"Fresh"/"Homemade"/"Classic" prefixes, collapse whitespace)
- Groups by normalized name; min 3 chars to avoid false positives
- Same name + different prices → `cross_item_duplicate_name` (warn)
- Same name + same prices → `cross_item_exact_duplicate` (info)

**Check 2: Category Price Outlier Detection (MAD-based):**
- Groups items by category (3+ priced items required)
- Uses MAD (median absolute deviation) — robust to outliers unlike IQR
- Threshold: 3 × MAD_effective (floor: 10% of median)
- Flags: `cross_item_category_price_outlier` (warn) with direction (above/below)

**Check 3: Category Isolation Detection:**
- Linear walk with ±2 neighbor window
- Flags items whose category differs from ALL categorized neighbors (need 2+)
- Flags: `cross_item_category_isolated` (info) with dominant neighbor suggestion

**Day 61 complete.**

---

### ✅ Day 62 — Fuzzy Name Matching for Near-Duplicate Detection (COMPLETE)

**Fuzzy Matching via SequenceMatcher** (`storage/cross_item.py`):
- Extends Day 61's exact-duplicate detection with fuzzy similarity matching
- Uses Python's `difflib.SequenceMatcher` (zero new dependencies, already in codebase)
- Catches OCR typos: "BUFALO"→"BUFFALO", "MARGARITA"→"MARGHERITA", "CHEEZE"→"CHEESE"
- Also catches: space variations ("CHEESEBURGER"/"CHEESE BURGER"), character dropout, transpositions

**Three-Phase Detection Architecture:**
1. Phase 1: Collect and normalize item names (reuses Day 61 `_normalize_name()`)
2. Phase 2: Exact matching (unchanged Day 61 logic) → `cross_item_exact_duplicate` / `cross_item_duplicate_name`
3. Phase 3: Pairwise fuzzy comparison on remaining items → `cross_item_fuzzy_duplicate` / `cross_item_fuzzy_exact_duplicate`

**Fuzzy Match Rules:**
- Threshold: 0.82 similarity ratio (catches 1-2 char typos in typical menu names)
- Minimum name length: 4 chars (prevents false positives on short names like "Sub"/"Sup")
- Same-group pairs skipped (already flagged by exact matching)
- Price-aware: same price → info severity, different price → warn severity
- Flag details include `similarity` ratio, `matched_name`, `matched_index`, prices

**Test Results** (1,887 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51-55 (Sprint 8.1) | 691 | 100% |
| Day 56 variants | 237 | 100% |
| Day 57 price validation | 242 | 100% |
| Day 58 combo modifiers | 275 | 100% |
| Day 59 consistency | 113 | 100% |
| Day 60 confidence | 106 | 100% |
| Day 61 cross-item | 109 | 100% |
| Day 62 fuzzy names | 96 | 100% |
| Rotation scoring | 18 | 100% |
| **TOTAL** | **1,887** | **100%** |

**Artifacts:**
- [storage/cross_item.py](storage/cross_item.py) — Cross-item consistency module (~310 LOC, +110 from Day 61)
- [tests/test_day62_fuzzy_names.py](tests/test_day62_fuzzy_names.py) — Day 62 test suite (96 cases)

**Day 62 complete.**

---

### ✅ Day 63 — Category Reassignment Suggestions (Neighbor-Based Smoothing) (COMPLETE)

**Multi-Signal Category Suggestion** (`storage/cross_item.py`):
- 4th cross-item check: when neighbor context disagrees with an item's category, suggests reassignment
- Four scoring signals combined into a single confidence score:

| Signal | Weight | Description |
|--------|--------|-------------|
| Neighbor agreement (+-3 window) | `agreement * 0.40` | Primary signal — what category do surrounding items have? |
| Keyword fit | +/-0.20 | Do item name keywords favor current or suggested category? |
| Price band fit | +/-0.15 | Does the item's price fit the suggested category's expected range? |
| Original confidence | +0.10/-0.15 | Low initial confidence boosts suggestion; high confidence penalizes |

- **Keyword guard**: If current category has 2+ keyword matches (e.g., "Caesar Salad" → salad + caesar), suppresses suggestion entirely
- **Minimum 0.30 confidence** to emit flag; severity always "info" (suggestions, not errors)
- **Complements isolation** (Day 61): isolation is factual ("you're alone"), suggestion is prescriptive ("consider X")
- Reuses `CATEGORY_KEYWORDS` and `CATEGORY_PRICE_BANDS` from `category_infer.py` — no duplication

**Test Results** (1,938 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51-55 (Sprint 8.1) | 691 | 100% |
| Day 56 variants | 237 | 100% |
| Day 57 price validation | 242 | 100% |
| Day 58 combo modifiers | 275 | 100% |
| Day 59 consistency | 113 | 100% |
| Day 60 confidence | 106 | 100% |
| Day 61 cross-item | 109 | 100% |
| Day 62 fuzzy names | 96 | 100% |
| Day 63 category suggestions | 51 | 100% |
| Rotation scoring | 18 | 100% |
| **TOTAL** | **1,938** | **100%** |

**Artifacts:**
- [storage/cross_item.py](storage/cross_item.py) — Cross-item consistency module (~540 LOC, +180 from Day 62)
- [tests/test_day63_category_suggestions.py](tests/test_day63_category_suggestions.py) — Day 63 test suite (51 cases)

**Day 63 complete.**

---

### ✅ Day 64 — Cross-Category Price Coherence (COMPLETE)

**Cross-Category Price Ordering** (`storage/cross_item.py`):
- 5th cross-item check: detects items that violate expected price relationships between categories
- 16 directional rules encoding near-universal pricing expectations:
  - Beverages < {Sides, Salads, Wings, Subs, Burgers, Pizza, Pasta, Calzones}
  - Sides/Appetizers < {Subs, Burgers, Pizza, Pasta, Calzones}
  - Desserts < {Pizza, Pasta, Calzones}

**Algorithm:**
1. Compute per-category median prices from actual menu data (need 2+ priced items per category)
2. For each rule: check that medians have 30%+ gap (otherwise categories overlap in this menu)
3. Flag cheap-cat items priced above expensive-cat median → `cross_category_price_above` (warn)
4. Flag expensive-cat items priced below cheap-cat median → `cross_category_price_below` (warn)
5. Deduplication: each item gets at most one flag per direction (most dramatic violation kept)

**Catches:**
- OCR price errors: fries at $15.99 when pizza median is $13.99
- Miscategorization: pizza labeled as "Beverages" at $12.99
- Data entry errors: pasta at $3.99 when sides median is $6.99

**Test Results** (2,013 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51-55 (Sprint 8.1) | 691 | 100% |
| Day 56 variants | 237 | 100% |
| Day 57 price validation | 242 | 100% |
| Day 58 combo modifiers | 275 | 100% |
| Day 59 consistency | 113 | 100% |
| Day 60 confidence | 106 | 100% |
| Day 61 cross-item | 109 | 100% |
| Day 62 fuzzy names | 96 | 100% |
| Day 63 category suggestions | 51 | 100% |
| Day 64 cross-cat coherence | 75 | 100% |
| Rotation scoring | 18 | 100% |
| **TOTAL** | **2,013** | **100%** |

**Artifacts:**
- [storage/cross_item.py](storage/cross_item.py) — Cross-item consistency module (~640 LOC, +100 from Day 63)
- [tests/test_day64_cross_category_coherence.py](tests/test_day64_cross_category_coherence.py) — Day 64 test suite (75 cases)

**Day 64 complete.**

---

### ✅ Day 65 — Cross-Item Variant Pattern Enforcement (Sprint 8.3 Complete) (COMPLETE)

**Three Category-Level Variant Checks** (`storage/cross_item.py`):

**Check 6: Variant Count Consistency:**
- Within each category, computes MODE variant count among items with 2+ variants
- Flags items where `mode - actual >= 2` as `cross_item_variant_count_outlier` (info)
- Complements Day 59's grid_count_outlier (which uses size_grid_source grouping, not category)

**Check 7: Variant Label Set Consistency:**
- Finds dominant set of `normalized_size` labels (kind=="size" only) within each category
- Subset tolerance (gourmet right-alignment: {M,L} under {S,M,L} is OK)
- Superset tolerance (extra sizes: {S,M,L,XL} under {S,M,L} is OK)
- 60% agreement threshold; flags as `cross_item_variant_label_mismatch` (info)

**Check 8: Price Step Consistency:**
- Computes median price step between consecutive sizes within each category
- MAD-based outlier detection with 15% median floor
- Only positive steps counted (inversions already flagged Day 57)
- Flags as `cross_item_price_step_outlier` (info)

**Test Results** (2,088 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51-55 (Sprint 8.1) | 691 | 100% |
| Day 56 variants | 237 | 100% |
| Day 57 price validation | 242 | 100% |
| Day 58 combo modifiers | 275 | 100% |
| Day 59 consistency | 113 | 100% |
| Day 60 confidence | 106 | 100% |
| Day 61 cross-item | 109 | 100% |
| Day 62 fuzzy names | 96 | 100% |
| Day 63 category suggestions | 51 | 100% |
| Day 64 cross-cat coherence | 75 | 100% |
| Day 65 variant patterns | 75 | 100% |
| Rotation scoring | 18 | 100% |
| **TOTAL** | **2,088** | **100%** |

**Artifacts:**
- [storage/cross_item.py](storage/cross_item.py) — Cross-item consistency module (8 checks, ~830 LOC)
- [tests/test_day65_variant_patterns.py](tests/test_day65_variant_patterns.py) — Day 65 test suite (75 cases)

**Day 65 complete. Sprint 8.3 complete.**

---

### ✅ Day 67 — Confidence Tiers + Menu-Level Aggregation (COMPLETE)

**Extended Module: `storage/semantic_confidence.py`** (~350 LOC):
- Confidence tier classification: per-item `semantic_tier` + `needs_review` flagging
- Menu-level aggregation: `compute_menu_confidence_summary(items)` for menu-wide statistics

**Confidence Tier Classification (`classify_confidence_tiers`):**

| Tier | Threshold | needs_review |
|------|-----------|-------------|
| high | ≥ 0.80 | False |
| medium | 0.60 – 0.79 | True |
| low | 0.40 – 0.59 | True |
| reject | < 0.40 | True |

- Reads `semantic_confidence` from Day 66, writes `semantic_tier` + `needs_review` per item
- Defensive: missing `semantic_confidence` → reject + needs_review
- Pipeline Step 9.3, immediately after `score_semantic_confidence` (Step 9.2)

**Menu-Level Summary (`compute_menu_confidence_summary`):**
- `total_items`, `mean_confidence`, `median_confidence`, `stdev_confidence`
- `tier_counts`: {high, medium, low, reject} distribution
- `needs_review_count`: total items flagged for human review
- `quality_grade`: A (≥80% high), B (≥60%), C (≥40%), D (<40%)
- `category_summary`: per-category breakdown with count, mean, review count, tier counts
- Read-only — does NOT mutate items

**Pipeline Wiring:**
- Path A (`ocr_pipeline.py`): `classify_confidence_tiers()` after `score_semantic_confidence()`
- Path B (`ai_ocr_helper.py`): same placement
- Preview blocks: `semantic_tier` + `needs_review` mirrored for overlay UI

**Test Results** (2,284 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51-55 (Sprint 8.1) | 691 | 100% |
| Day 56 variants | 237 | 100% |
| Day 57 price validation | 242 | 100% |
| Day 58 combo modifiers | 275 | 100% |
| Day 59 consistency | 113 | 100% |
| Day 60 confidence | 106 | 100% |
| Day 61 cross-item | 109 | 100% |
| Day 62 fuzzy names | 96 | 100% |
| Day 63 category suggestions | 51 | 100% |
| Day 64 cross-cat coherence | 75 | 100% |
| Day 65 variant patterns | 75 | 100% |
| Day 66 semantic confidence | 93 | 100% |
| Day 67 confidence tiers | 103 | 100% |
| Rotation scoring | 18 | 100% |
| **TOTAL** | **2,284** | **100%** |

**Artifacts:**
- [storage/semantic_confidence.py](storage/semantic_confidence.py) — Extended with tiers + aggregation (~350 LOC)
- [tests/test_day67_confidence_tiers.py](tests/test_day67_confidence_tiers.py) — Day 67 test suite (103 cases)

**Day 67 complete. Sprint 8.4 continues.**

---

### ✅ Day 68 — Confidence-Driven Auto-Repair Recommendations (COMPLETE)

**Extended Module: `storage/semantic_confidence.py`** (~580 LOC):
- Per-item repair recommendations driven by confidence signal breakdowns and existing flags
- Menu-level repair statistics via `compute_repair_summary(items)`

**6 Recommendation Types:**

| Type | Trigger | Auto-fixable | Example |
|------|---------|-------------|---------|
| `garbled_name` | Name garble detected | Yes (if OCR correction available) | "eeeeccccrrrvvv" → corrected name |
| `name_quality` | Short or all-caps name | Yes (title case for all-caps) | "CHICKEN WINGS" → "Chicken Wings" |
| `price_missing` | No price found | No | Manual price entry needed |
| `category_reassignment` | Cross-item suggestion flag | Yes (proposed category) | "Sides" → "Pizza" |
| `variant_standardization` | Low variant score + flags | No | Price inversion, missing sizes |
| `flag_attention` | Many warning flags | No | Summary of top issues |

**Priority System:**
- reject tier → `critical` priority
- low tier → `important` priority
- medium tier → `suggested` priority
- high tier → empty recommendations (no issues)

**Pipeline Wiring:** Step 9.4, after `classify_confidence_tiers` (Step 9.3). Both paths wired. Preview blocks mirror `repair_recommendations`.

**Test Results** (2,372 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51-55 (Sprint 8.1) | 691 | 100% |
| Day 56 variants | 237 | 100% |
| Day 57 price validation | 242 | 100% |
| Day 58 combo modifiers | 275 | 100% |
| Day 59 consistency | 113 | 100% |
| Day 60 confidence | 106 | 100% |
| Day 61 cross-item | 109 | 100% |
| Day 62 fuzzy names | 96 | 100% |
| Day 63 category suggestions | 51 | 100% |
| Day 64 cross-cat coherence | 75 | 100% |
| Day 65 variant patterns | 75 | 100% |
| Day 66 semantic confidence | 93 | 100% |
| Day 67 confidence tiers | 103 | 100% |
| Day 68 repair recommendations | 88 | 100% |
| Rotation scoring | 18 | 100% |
| **TOTAL** | **2,372** | **100%** |

**Artifacts:**
- [storage/semantic_confidence.py](storage/semantic_confidence.py) — Extended with repair recommendations (~580 LOC)
- [tests/test_day68_repair_recommendations.py](tests/test_day68_repair_recommendations.py) — Day 68 test suite (88 cases)

**Day 68 complete. Sprint 8.4 continues.**

---

### ✅ Day 69 — Auto-Repair Execution Engine (COMPLETE)

**Extended Module: `storage/semantic_confidence.py`** (~930 LOC):
- Executes auto-fixable repair recommendations, updating item fields and recording audit trails
- Entry function: `apply_auto_repairs(items)` — Pipeline Step 9.5

**Auto-Repair Application:**

| Fix Type | proposed_fix Format | Fields Updated |
|----------|-------------------|----------------|
| `garbled_name` | `"Corrected Name"` (string) | `grammar.parsed_name` (Path A) and/or `name` (Path B) |
| `name_quality` (all-caps) | `"Title Cased"` (string) | same |
| `name_quality` (OCR fix) | `"Corrected Name"` (string) | same |
| `category_reassignment` | `{"category": "Pizza"}` (dict) | `item["category"]` |

**Execution Behavior:**
- Walks each item's `repair_recommendations`, applies only `auto_fixable: True` recs
- Sets `rec["applied"] = True` on executed recommendations
- Per-item `auto_repairs_applied` audit trail: `{type, field, old_value, new_value}`
- Returns summary: `{total_items_repaired, repairs_applied, by_type}`
- Idempotent: already-applied recs skipped on re-run
- Re-scores after repair: `score_semantic_confidence()` + `classify_confidence_tiers()` so final output reflects improved quality

**Pipeline Wiring:** Step 9.5, after `generate_repair_recommendations` (Step 9.4). Both paths wired. Preview blocks mirror `auto_repairs_applied`.

**Day 69 complete.**

---

### ✅ Day 70 — Semantic Quality Report (Phase 8 Capstone) (COMPLETE)

**Extended Module: `storage/semantic_confidence.py`** (~1,120 LOC):
- Unified quality report combining all Phase 8 signals into a single actionable output
- Entry function: `generate_semantic_report(items, repair_results)` — Pipeline Step 9.6

**Report Sections:**

| Section | Contents |
|---------|----------|
| `menu_confidence` | Mean/median/stdev, tier distribution, quality grade (A/B/C/D), category breakdowns |
| `repair_summary` | Recommendation counts by priority and type, auto-fixable count, category breakdown |
| `auto_repair_results` | Items repaired, repairs applied, by type |
| `pipeline_coverage` | % of items with each signal (grammar, confidence, tiers, flags, variants, repairs) |
| `issue_digest` | Top issues by frequency, bottom-10 worst items, most common flags |
| `category_health` | Per-category ranking sorted worst-first: mean confidence, needs-review %, grade |
| `quality_narrative` | Human-readable assessment: grade, tier breakdown, repair summary, weakest category |

**Pipeline Wiring:** Step 9.6 (final step). Both paths wired. Report attached to pipeline output (`segmented["semantic_report"]` for OCR path, `doc["semantic_report"]` for website path).

**Test Results** (2,521 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51-55 (Sprint 8.1) | 691 | 100% |
| Day 56 variants | 237 | 100% |
| Day 57 price validation | 242 | 100% |
| Day 58 combo modifiers | 275 | 100% |
| Day 59 consistency | 113 | 100% |
| Day 60 confidence | 106 | 100% |
| Day 61 cross-item | 109 | 100% |
| Day 62 fuzzy names | 96 | 100% |
| Day 63 category suggestions | 51 | 100% |
| Day 64 cross-cat coherence | 75 | 100% |
| Day 65 variant patterns | 75 | 100% |
| Day 66 semantic confidence | 93 | 100% |
| Day 67 confidence tiers | 103 | 100% |
| Day 68 repair recommendations | 88 | 100% |
| Day 69 auto-repair | 76 | 100% |
| Day 70 semantic report | 73 | 100% |
| Rotation scoring | 18 | 100% |
| **TOTAL** | **2,521** | **100%** |

**Artifacts:**
- [storage/semantic_confidence.py](storage/semantic_confidence.py) — Extended with semantic quality report (~1,120 LOC)
- [tests/test_day70_semantic_report.py](tests/test_day70_semantic_report.py) — Day 70 test suite (73 cases)

**Day 70 complete. Sprint 8.4 complete. Phase 8 complete.**

---

### ✅ Day 66 — Semantic Confidence Foundation (Sprint 8.4 Start) (COMPLETE)

**New Module: `storage/semantic_confidence.py`** (~200 LOC):
- Unified per-item `semantic_confidence` score (0.0-1.0) aggregating 5 independent signal sources
- Entry function: `score_semantic_confidence(items)` — Pipeline Step 9.2

**Five Weighted Signals:**

| Signal | Weight | Source | Default |
|--------|--------|--------|---------|
| Grammar/parse confidence | 0.30 | `grammar.parse_confidence` → item `confidence` fallback | 0.5 |
| Name quality | 0.20 | Length tiers + garble detection + all-caps penalty | varies |
| Price presence | 0.20 | Any positive price in variants/candidates/direct | 0.3 |
| Variant quality | 0.15 | Average variant confidence across all variants | 0.5 |
| Flag penalty | 0.15 | Severity-weighted: warn -0.15, info -0.05, auto_fix -0.02 | 1.0 |

**Name Quality Sub-Score (new logic):**
- Length: <3 chars → 0.3, 3-5 → 0.6, 6+ → 1.0
- Garble: inline triple-repeat + garble-char ratio + unique ratio detection (signals >= 2)
- All-caps: 0.9 penalty (OCR often produces all-caps; small ding, not a problem)
- Combined via `min()` (weakest-link model)

**Full Audit Trail:**
- Each item gets `semantic_confidence_details` dict with all 5 signals' raw values, weights, and weighted contributions
- Follows same pattern as variant `confidence_details` from Day 60

**Polymorphic Design:**
- Works with both Path A (text_block dicts from `ocr_pipeline.py`) and Path B (flat item dicts from `ai_ocr_helper.py`)
- All field access via `.get()` with graceful defaults — no KeyError on missing fields

**Pipeline Wiring:**
- Path B: after `check_cross_item_consistency()` in `ai_ocr_helper.py`
- Path A: after `check_cross_item_consistency()` in `ocr_pipeline.py`
- Preview blocks: `semantic_confidence` + `semantic_confidence_details` mirrored for overlay UI

**Test Results** (2,181 total — 100%):

| Suite | Tests | Pass Rate |
|-------|-------|-----------|
| Day 51-55 (Sprint 8.1) | 691 | 100% |
| Day 56 variants | 237 | 100% |
| Day 57 price validation | 242 | 100% |
| Day 58 combo modifiers | 275 | 100% |
| Day 59 consistency | 113 | 100% |
| Day 60 confidence | 106 | 100% |
| Day 61 cross-item | 109 | 100% |
| Day 62 fuzzy names | 96 | 100% |
| Day 63 category suggestions | 51 | 100% |
| Day 64 cross-cat coherence | 75 | 100% |
| Day 65 variant patterns | 75 | 100% |
| Day 66 semantic confidence | 93 | 100% |
| Rotation scoring | 18 | 100% |
| **TOTAL** | **2,181** | **100%** |

**Artifacts:**
- [storage/semantic_confidence.py](storage/semantic_confidence.py) — Semantic confidence scoring module (~200 LOC)
- [tests/test_day66_semantic_confidence.py](tests/test_day66_semantic_confidence.py) — Day 66 test suite (93 cases)

**Day 66 complete. Sprint 8.4 underway.**

---

### ✅ Day 59 — Cross-Variant Consistency Checks (COMPLETE)

Six new validators in `check_variant_consistency()` (Pipeline Step 8.6):

| Check | Severity | Catches |
|-------|----------|---------|
| Duplicate variants | warn | Same group_key on multiple variants (OCR duplication) |
| Zero-price variants | warn | $0.00 variant when siblings have real prices |
| Mixed kinds | info/warn | Unusual mixes like size + combo on same item |
| Size gaps | info | S and L present but M missing |
| Grid incomplete | info | Item has 2 variants under a 4-column grid |
| Grid count outlier | info | Item has far fewer variants than its grid group |

Key design: word sizes split into abbreviated (S/M/L) and named (Personal/Regular/Deluxe) sub-chains to avoid false gap positives. Inch/piece tracks skipped for gaps (naturally sparse).

**Day 59 complete.**

---

### ✅ Claude API Extraction Pipeline (COMPLETE)

**Problem:** Background imports used `segment_document()` which produced only ~762 chars of fragmented OCR text, resulting in 18 garbled items ("OLVLOd", "YSal", "3ma") in a single "Vals" category.

**Root Cause:** Two separate OCR paths — the `/ai/preview` endpoint used simple `pytesseract.image_to_string()` (full text, 7,736 chars) while the background import used the complex `segment_document()` pipeline (word-level fragments).

**Solution — Three-Strategy Item Extraction:**
1. **Claude API** (primary): Sends clean OCR text to Claude Sonnet for structured extraction. Produces 106 items with proper names, descriptions, prices, and categories at 90% confidence.
2. **Heuristic AI** (fallback): Uses `analyze_ocr_text()` on clean text — same path as `/ai/preview` endpoint.
3. **Legacy JSON** (last resort): Parses facade's structured output via `_draft_items_from_draft_json()`.

**Bug Fixes:**
- Removed duplicate `segment_document()` call that doubled processing time (~3 min wasted)
- Fixed draft_path timing: save to DB before `status="done"` so auto-redirect finds populated editor
- Added error visibility to bare `except: pass` blocks in draft creation
- Extracted `_draft_items_from_ai_preview()` helper, deduplicated `imports_ai_commit()`

**Results:**
- 115 items extracted from full restaurant menu (was 18 garbled)
- Proper categories: Pizza, Appetizers, Calzones, Burgers, Wings, Wraps, Sandwiches, Beverages
- All items have prices, descriptions, and 90% confidence scores
- Auto-redirect from import view to draft editor on completion

**New Files:**
- [storage/ai_menu_extract.py](storage/ai_menu_extract.py) — Claude API extraction module (~260 LOC)
- `anthropic>=0.79.0` added to requirements
- `ANTHROPIC_API_KEY` added to `.env.example`

---

## ▶️ CURRENT POSITION

➡ **Phase 11 — Production AI Pipeline — IN PROGRESS (Days 96-110)**

Sprint 11.1 COMPLETE (Days 96-100.5). Sprint 11.2 COMPLETE (Days 101-104). Sprint 11.3 IN PROGRESS (Days 105-110). Full 6-stage pipeline: OCR → Call 1 → Call 2 (vision verify) → Semantic → Call 3 (reconcile) → Confidence Gate. Day 105: gate module + signal #6 + rejection logging. Day 106: gate wired into pipeline — live-tested, thinking-mode bypass fix. Day 107: frontend rejection UI — banner + pill. Day 108: E2E gate integration tests — run_ocr_and_make_draft() tested end-to-end with all Claude calls mocked, verifying pass/fail/thinking/skip/empty-extraction paths. 2,114 tests pass.

---

## 🌄 System State Summary

ServLine now has:

**Core Infrastructure:**
- ✅ Unified OCR brain (One Brain architecture)
- ✅ Stable import flow (PDF/Image/CSV/XLSX/JSON)
- ✅ Structured Draft Editor with inline editing
- ✅ Column mapping for structured imports
- ✅ Full debug artifacts and metadata

**OCR & Vision (Phase 7):**
- ✅ Deterministic orientation handling
- ✅ Rotation sweep for mis-rotated uploads (0°/90°/180°/270°)
- ✅ Deterministic OCR scoring & selection (outlier penalty for token inflation)
- ✅ Height-ratio line grouping (validated on 4 real menus)
- ✅ Website OCR quality — psm 3 + preprocessing for cleaner extraction

**AI & Extraction:**
- ✅ Claude API menu extraction — 106 items from single menu, 90% confidence
- ✅ Single-strategy extraction: Claude API only (heuristic/legacy paths removed Day 100.5)
- ✅ Clean OCR text path (7,736 chars via image_to_string vs 762 chars fragmented)
- ✅ Price-safe, category-safe AI cleanup (non-hallucinating text surgeon)
- ✅ Auto-redirect from import view to draft editor on completion
- ✅ Vision verification module (Claude Call 2) — image-based item verification with changes log

**Semantic Intelligence (Phase 8):**
- ✅ Menu item grammar parser — multi-pass classification, 100% on real menus
- ✅ Phrase-level category keywords — 90+ weighted patterns
- ✅ OCR garble stripping — dot-leader noise, typo normalization (88Q→BBQ)
- ✅ Item component detection — toppings, sauces, preparation, flavors
- ✅ Shared size vocabulary — single source of truth for size detection
- ✅ Size grid context propagation — headers map to item variants
- ✅ Grammar-to-variant bridge — pipeline + website paths connected
- ✅ Variant price validation — S < M < L monotonic check, track-separated
- ✅ Combo modifier detection — "W/FRIES", "WIFRIES" → combo variants
- ✅ Confidence tiers — high/medium/low/unknown scoring
- ✅ Semantic confidence scoring — unified per-item score, 5 weighted signals
- ✅ Confidence-driven auto-repair recommendations — 6 types, priority system
- ✅ Auto-repair execution engine — applies fixes, audit trail, re-scoring
- ✅ Semantic quality report — unified quality report with grade, category health, narrative

**Structured Variants (Phase 9):**
- ✅ `draft_item_variants` child table — structured variant storage with FK cascade
- ✅ Variant CRUD — insert, update, delete, get with defensive normalization
- ✅ LEFT JOIN grouping — `get_draft_items()` returns items with nested `variants: []`
- ✅ Clone support — `clone_draft()` preserves variants
- ✅ Extraction pipeline → structured variants — all 3 strategies preserve `_variants`
- ✅ `_insert_items_bulk()` and `upsert_draft_items()` auto-insert child variant rows
- ✅ Backfill logic — `backfill_variants_from_names()` converts "Name (Size)" patterns
- ✅ Variant-aware publish flow — `get_publish_rows()` expands variants for `menu_items`
- ✅ Parent base price enforcement — `ensure_parent_base_price()` keeps parent = min(variants)
- ✅ Backward compatibility — old drafts (0 variants) load and publish identically
- ✅ Variant Sub-Row UI — indented sub-rows with kind badges, collapse/expand, add/delete
- ✅ Editor variant save — `_variants` in payload, `deleted_variant_ids` for orphan cleanup
- ✅ Sidebar structured variants — uses variant data instead of regex name parsing
- ✅ Variant reorder — up/down buttons, position from DOM order
- ✅ Inline price validation — S < M < L inversion warnings with ordinal mapping
- ✅ Variant template presets — 6 quick-add templates (S/M/L, Half/Whole, etc.)
- ✅ Contract validation — `_variants` schema + `deleted_variant_ids` validated
- ✅ Low-confidence panel — variant info display, click-to-scroll, auto-expand
- ✅ Bulk category change — toolbar button, preserves variants on category update
- ✅ Delete-selected cascade — bulk delete now removes associated variant rows
- ✅ JSON export with nested variants — `items[].variants[]` array per item
- ✅ CSV sub-row export — `type=item`/`type=variant` rows with kind and label
- ✅ CSV wide export — dynamic `price_Label` columns from all variant labels
- ✅ Export UI dropdown — 10 format choices organized into Standard + POS sections
- ✅ XLSX export with variant sub-rows — bold parents, gray indented variants, auto-generated label columns
- ✅ XLSX sheet-per-category export — one sheet per category with category-specific variant columns
- ✅ Square CSV export — items + modifier groups from variants (Size, Combo Add-on, Flavor, Style, Option)
- ✅ Toast CSV export — menu group / item / option hierarchy
- ✅ Generic POS JSON — universal `menu.categories[].items[].modifiers[]` schema
- ✅ Pre-export validation — missing prices, categories, names, zero-price variants
- ✅ Export preview modal — formatted output preview before download with format selector

---

## ⏭️ Phase 9 — Structured Variants & Export

### Sprint 9.1 — Structured Variant Storage (Days 71-73) *** COMPLETE ***
- ✅ Database schema & migration — `draft_item_variants` table (Day 71)
- ✅ CRUD functions — insert, update, delete, get with normalization (Day 71)
- ✅ LEFT JOIN grouping — single round-trip variant loading (Day 71)
- ✅ FK CASCADE + clone support (Day 71)
- ✅ Extraction pipeline → structured variants (Day 72)
- ✅ Migration & backward compatibility (Day 73)
- ✅ Variant-aware publish flow (Day 73)
- ✅ Parent base price enforcement (Day 73)

### Sprint 9.2 — Editor Redesign (Days 74-77) *** COMPLETE ***
- ✅ Variant Sub-Row UI (Day 74):
  - Indented variant rows with kind-colored badges (size/combo/flavor/style/other)
  - Collapse/expand toggle (auto-collapses 4+ variants)
  - Add Variant / Delete Variant buttons per item
  - Kind dropdown (select) with live badge update
  - Variant count pill on parent row
  - collectPayload() sends `_variants` per item
  - Save endpoint handles `deleted_variant_ids`
  - Sidebar outline uses structured variant data
  - Search/filter includes variant labels
  - Duplicate/delete cascades to variant sub-rows
- ✅ Inline Variant Validation & Reorder (Day 75):
  - Up/down reorder buttons on variant sub-rows
  - Real-time price-order validation (S < M < L inversion warnings)
  - SIZE_ORDINALS mapping for word sizes, inch sizes, portions, multiplicities
  - 6 variant template presets (S/M/L, Half/Whole, Slice/Pie, etc.)
  - `_variants` schema validation in save contract
  - `deleted_variant_ids` validation in save contract
  - Position from DOM order (0-indexed) for accurate reorder persistence
- ✅ Save/Load Round-Trip & Backfill (Day 76):
  - End-to-end save/load round-trip verified (upsert → reload → variants intact)
  - Backfill Flask endpoint: `POST /drafts/<id>/backfill_variants`
  - Backfill button in editor sidebar with async toast feedback
  - Guards: draft must exist and be in editing state
  - Full lifecycle: save → backfill → reload → publish expanded rows
- ✅ Low-Confidence & Bulk Operations (Day 77):
  - Low-confidence panel shows variant count + labels per flagged item
  - Click-to-scroll from panel to item row with auto-expand variants
  - Bulk "Set Category" button for selected items (preserves variants)
  - Delete-selected cascade fix — variant rows + IDs cleaned up
  - Sprint 9.2 complete: 147 tests across Days 74-77

### Sprint 9.3 — Export Formats (Days 78-82) — COMPLETE
- ✅ CSV & JSON Export with Variants (Day 78):
  - JSON export: nested `variants: [{label, price_cents, kind}]` per item
  - CSV sub-row export: `type=item` parent rows + `type=variant` child rows
  - CSV wide export: dynamic `price_Label` columns from unique variant labels
  - Export UI dropdown: 6 format choices (flat, sub-row, wide, JSON, Excel, debug)
  - Backward compat: original flat CSV unchanged
  - Day 78 test suite: 31 cases, 100% pass rate
- ✅ Excel Export with Variants (Day 79):
  - XLSX with variant sub-rows: bold parent rows, gray indented variant rows
  - Auto-generated `price_Label` columns from all unique variant labels
  - Header styling: dark fill, white bold text
  - Sheet-per-category: `GET /drafts/<id>/export_by_category.xlsx`
  - Category-specific variant columns per sheet (not global)
  - Uncategorized items → "Uncategorized" sheet; empty draft → placeholder
  - Export dropdown updated to 7 options
  - Day 79 test suite: 33 cases, 100% pass rate
- ✅ POS Export Templates (Day 80):
  - Square CSV: items + modifier groups (variants grouped by kind)
  - Toast CSV: menu group / item / option group hierarchy
  - Generic POS JSON: universal `menu.categories[].items[].modifiers[]` schema
  - Pre-export validation: warns on missing prices, categories, names, zero-price variants
  - Export preview: modal with format selector, formatted output, validation warnings
  - Export dropdown: 10 options organized into Standard + POS Formats sections
  - Day 80 test suite: 58 cases, 100% pass rate
- ✅ Export Metrics, Enhanced Validation & Round-Trip Tests (Day 81):
  - Export metrics endpoint: item/variant counts, by-kind breakdown, category breakdown, price stats
  - Enhanced validation: variant_missing_label, duplicate_variant_label, price_inversion
  - Round-trip verification: CSV, JSON, POS JSON export → parse → verify counts/structure
  - Edge cases: empty drafts (all 9 formats), 10+ variants, Unicode, variant-only, all kinds
  - Day 81 test suite: 67 cases, 100% pass rate
- ✅ Export Finalization & Sprint 9.3 Wrap-Up (Day 82):
  - E2E integration tests: realistic draft → all 9 export formats verified
  - Cross-format consistency: CSV/JSON/Square/Toast/POS JSON agree on counts
  - Edge case hardening: CSV-hostile chars, long names, mixed kinds, large drafts, price edges
  - Export pipeline Flask route round-trips verified end-to-end
  - Day 82 test suite: 103 cases, 100% pass rate
  - Sprint 9.3 complete: Days 78-82, 292 tests, all passing

### Sprint 9.4 — Approve & Export + API Foundation (Days 83-85) — COMPLETE
- ✅ "Approve & Export to POS" Button (Day 83):
  - Prominent green "Approve & Export to POS" button in editor toolbar
  - Auto-saves draft before export, runs validation, shows confirmation modal
  - Validation modal: item/variant counts, warning list, approve/cancel actions
  - Generic POS JSON download on approval (uses existing builder)
  - New `approved` draft status: read-only after approval, green status pill
  - Approved drafts block saves (403) while read-only exports remain functional
  - Export history tracking: `draft_export_history` table, `record_export()`, `get_export_history()`
  - Last export info displayed in editor header
  - New endpoints: `POST /approve_export`, `GET /export_history`
  - Day 83 test suite: 52 cases, 100% pass rate
- ✅ REST API Endpoints (Day 84):
  - `GET /api/drafts/<id>/items` — retrieve items with nested variants
  - `POST /api/drafts/<id>/items` — create items with optional variants (201)
  - `PUT /api/drafts/<id>/items/<item_id>` — update single item with variants
  - API key auth: `api_keys` table (SHA-256 hash, restaurant_id, label, active, rate_limit_rpm)
  - `create_api_key()`, `validate_api_key()`, `revoke_api_key()` CRUD in storage layer
  - `api_key_required` decorator: X-API-Key or Bearer header, 401/403/429 responses
  - Sliding-window rate limiter: per-key deque, thread-safe, configurable RPM
  - Rate limit headers on all authenticated responses
  - Status guards: approved/published drafts block POST/PUT (403)
  - Day 84 test suite: 50 cases, 100% pass rate
- Webhooks & API Documentation (Day 85):
  - Webhook notifications: POST callbacks on draft approval/export
  - `webhooks` table: url, event_types, secret, restaurant scoping
  - CRUD: `register_webhook()`, `list_webhooks()`, `delete_webhook()`
  - `fire_webhooks()`: async dispatch via daemon threads, HMAC-SHA256 signed
  - REST API: `POST/GET/DELETE /api/webhooks` (api_key_required)
  - Wired into `draft_approve_export()`: fires `draft.approved` + `draft.exported`
  - Public API documentation page: `GET /api/docs`
  - Documents: authentication, rate limiting, items CRUD, webhooks, error codes
  - Day 85 test suite: 55 cases, 100% pass rate
  - Sprint 9.4 complete: Days 83-85, 157 tests, all passing
  - Phase 9 complete: Days 71-85, 782 tests, all passing

## ⏭️ Phase 10 — Multi-Menu & Versioning (COMPLETE)

- Multi-Menu & Versioning Foundation (Day 86):
  - New `storage/menus.py` module (~380 LOC): menu + version CRUD
  - Schema: `menu_versions`, `menu_version_items`, `menu_version_item_variants` tables
  - ALTER `menus`: +menu_type, +description, +updated_at columns
  - ALTER `drafts`: +menu_id column for draft-to-menu linking
  - 11 valid menu types: breakfast, lunch, dinner, brunch, happy_hour, kids, dessert, drinks, catering, seasonal, other
  - `create_menu_version()` snapshots draft items + variants into immutable version
  - Auto-incrementing version numbers per menu, UNIQUE constraint
  - `get_menu_version()` LEFT JOIN retrieval with nested items + variants
  - `migrate_existing_menus()` backfills legacy menu_items → versioned model
  - Day 86 test suite: 59 cases, 100% pass rate

- Menu Management UI (Day 87):
  - Upgraded `menus.html` template: type/description/version columns, create form, edit/delete actions
  - New routes: `POST /restaurants/<id>/menus` (create), `POST /menus/<id>/update`, `POST /menus/<id>/delete`
  - Menu type selector dropdown (11 types) + description field on create/edit forms
  - `list_menus()` integration: version count, active filtering, restaurant scoping
  - Draft-to-menu assignment: `POST /drafts/<id>/assign_menu` route + sidebar dropdown in editor
  - Editor dynamically shows menu dropdown when restaurant assigned, hint link when no menus
  - Fixed pre-existing test schema gaps (menu_id column in Days 73/74/77 test DBs)
  - Day 87 test suite: 51 cases, 100% pass rate

- Publish to Versioned Menu (Day 88):
  - Wired `publish_now` to create menu versions when draft has `menu_id` assigned
  - Versioned path: detects `draft.menu_id` → `create_menu_version(menu_id, source_draft_id)`
  - Snapshots all items + variants into immutable `menu_version_items`/`menu_version_item_variants`
  - Legacy path preserved: drafts without `menu_id` still publish to flat `menu_items`
  - New menu detail page: `GET /menus/<id>/detail` — version history table with counts, source draft links
  - Current version highlighted with "(current)" badge, empty state for new menus
  - New version detail page: `GET /menus/versions/<id>` — full item table with variant sub-rows
  - Breadcrumb navigation: Restaurants → Restaurant → Menu → Version
  - Menus list now links to detail page instead of legacy items page
  - Day 88 test suite: 44 cases, 100% pass rate
  - Sprint 10.1 complete: Days 86-88, 154 tests, all passing

- Version Comparison & Diff Engine (Day 89):
  - New `compare_menu_versions(version_id_a, version_id_b)` in `storage/menus.py`
  - Item matching by normalized name (case-insensitive, whitespace-stripped)
  - Duplicate name disambiguation by `(name, category)` compound key
  - Field-level diff: name, description, price_cents, category, position changes with old/new values
  - Variant-level diff: added/removed/modified/unchanged variants per item
  - Items with only variant changes (no field changes) correctly classified as "modified"
  - Sorted output: modified → added → removed → unchanged for actionable-first display
  - Cross-menu validation: returns None if versions belong to different menus
  - New Flask route: `GET /menus/<id>/compare?a=<version_id>&b=<version_id>`
  - New comparison template: color-coded unified diff (green=added, red=removed, amber=modified)
  - Summary bar: +N added, -N removed, ~N modified, N unchanged
  - Unchanged items collapsed by default with toggle button
  - Version selector dropdowns on comparison page for easy re-comparison
  - Menu detail page: "Compare Versions" form when 2+ versions exist
  - Day 89 test suite: 52 cases, 100% pass rate

- Price Change Highlighting & Restore from Version (Day 90):
  - Diff engine enhanced with `price_direction` metadata ('increase'/'decrease') on price changes
  - `_price_direction()` helper normalizes None→0 for comparison
  - Variant diffs also include `price_direction` on variant price changes
  - Compare template: old price with strikethrough + new price with ▲/▼ direction arrows
  - CSS classes: `.price-increase` (red), `.price-decrease` (green), `.price-old` (strikethrough)
  - Jinja2 `namespace()` pattern for loop-scoped variable access
  - New `restore_version_to_draft(version_id)` in `storage/menus.py`
  - Copies all version items + variants into a new draft in "editing" status
  - Sets `source="version_restore"`, links `menu_id`, correct `restaurant_id`
  - Returns `{draft_id, version_id, version_label, item_count, variant_count}`
  - New Flask route: `POST /menus/versions/<id>/restore` with login required
  - Flash success with draft id and counts, redirects to draft editor
  - "Restore to Draft" button on version detail page with confirm dialog
  - "Restore" button per version in menu detail history table
  - Day 90 test suite: 49 cases, 100% pass rate

- Version Change Summaries & Annotations (Day 91):
  - `generate_change_summary(diff)`: human-readable one-liner from diff result
  - Counts added/modified/removed items + price increase/decrease aggregation
  - Variant price changes included in summary counts
  - `_auto_generate_change_summary()`: diffs new version vs previous automatically
  - `change_summary` TEXT column added to `menu_versions` (idempotent migration)
  - `create_menu_version()` auto-generates and stores change_summary on publish
  - First version: no summary (nothing to diff); subsequent versions: auto-generated
  - `update_menu_version(label=, notes=)`: edit mutable metadata post-creation
  - New Flask route: `POST /menus/versions/<id>/edit` with flash messages
  - Session user capture: publish route stores email/name in `created_by`
  - `menu_detail.html`: new Changes + Published By columns, Edit button per version
  - "Initial version" text for v1, change_summary inline for v2+
  - Version edit modal (JS) for label/notes editing
  - `menu_version_detail.html`: shows change_summary and created_by
  - Day 91 test suite: 43 cases, 100% pass rate

- Version Lifecycle — Pin, Delete & Activity Log (Day 92):
  - `pin_menu_version()` / `unpin_menu_version()`: mark versions as important
  - `delete_menu_version()`: remove versions with safety checks (no pinned, no sole version)
  - New `menu_activity` table: tracks version events (published, pinned, unpinned, deleted, restored, edited)
  - `record_menu_activity()`: inserts event with menu_id, version_id, action, detail, actor
  - `list_menu_activity()`: newest-first, supports limit/offset
  - `get_version_stats()`: total versions, pinned count, item trend, price change totals
  - Flask routes: `POST /menus/versions/<id>/pin`, `POST /menus/versions/<id>/delete`, `GET /menus/<id>/activity`
  - Activity wiring: publish, restore, edit, pin, delete all record activity
  - `menu_detail.html`: pin/delete buttons, pin badge, stats bar, recent activity section
  - New `menu_activity.html`: dedicated activity log page with color-coded action badges
  - Schema: `pinned` column on menu_versions, `menu_activity` table (idempotent migrations)
  - Day 92 test suite: 53 cases, 100% pass rate
  - Sprint 10.2 total: 197 tests (Days 89-92), all passing

- Seasonal Menu Management & Daypart Scheduling (Day 93):
  - 6 new columns on menus table: season, effective_from, effective_to, active_days, active_start_time, active_end_time
  - `VALID_SEASONS` (spring/summer/fall/winter), `VALID_DAYS` (mon-sun) constants
  - `set_menu_schedule()`: atomic schedule replacement with validation (season, date, time, day formats)
  - `clear_menu_schedule()`: reset all schedule fields to NULL
  - `get_scheduled_menus()`: filter by date/time/day, unscheduled menus always included
  - `get_seasonal_menus()`: filter by season or get all seasonal menus
  - `get_menu_schedule_summary()`: human-readable one-liner ("Summer | 2026-06-01 to 2026-08-31 | MON,WED,FRI | 11:00 - 14:00")
  - New route: `POST /menus/<id>/schedule` — set/clear schedule with activity recording
  - Schedule form in `menu_detail.html`: season dropdown, date pickers, day checkboxes, time inputs
  - Season badges in `menus.html` with color coding (spring=green, summer=yellow, fall=orange, winter=blue)
  - `schedule_updated` activity action with detail summary
  - Day 93 test suite: 54 cases, 100% pass rate

- Active Menu Switching & Rotation (Day 94):
  - `_schedule_field_count()`: count scheduling constraints on a menu (0-4)
  - `score_menu_specificity()`: rank menus by schedule specificity (0-100)
  - `get_active_menus()`: resolve currently active menus, ranked by specificity, with auto-datetime defaults
  - `get_menu_rotation()`: full-day rotation timeline (All Day + timed slots)
  - `get_next_transition()`: find next start/end transition after current time
  - `get_active_menu_summary()`: complete status (active menus, primary, next transition, rotation)
  - New routes: `GET /restaurants/<id>/active_menus` (dashboard), `GET /api/restaurants/<id>/active_menus` (JSON API)
  - `active_menus.html` template: date/time query form, primary menu card, specificity table, rotation timeline, next transition
  - "Active Now" link in menus list page
  - Day 94 test suite: 60 cases, 100% pass rate
  - Sprint 10.3 total: 114 tests (Days 93-94), all passing

- Restaurant CRUD Fix (Day 94 bonus):
  - Added `POST /restaurants` route (`create_restaurant`) with `@login_required`
  - Restaurants template: inline create form (name/phone/address), clickable restaurant names linking to menus page

- Menu Health Dashboard & Phase 10 Capstone (Day 95):
  - `_time_overlaps()`, `_days_overlap()`, `_date_ranges_overlap()`: helper predicates for conflict detection
  - `detect_schedule_conflicts()`: find menus with overlapping schedules across time, day, and date dimensions
  - Overlap types: "time" (both timed), "full" (both scheduled, no time), "partial" (scheduled vs unscheduled)
  - `analyze_schedule_coverage()`: weekly day coverage, hourly slot coverage, gap identification, coverage score (0-100)
  - `get_menu_health()`: per-menu health scoring (0-100) — versions +25, items +25, schedule +25, type +10, desc +5, multi-version +10
  - Issue tracking: "No published versions", "No schedule set", "No menu type set", etc.
  - `get_phase10_summary()`: unified dashboard payload — versions, items, conflicts, coverage, health, grade (A-D)
  - New routes: `GET /restaurants/<id>/menu_health` (dashboard), `GET /api/restaurants/<id>/menu_health` (JSON API)
  - `menu_health.html` template: grade card, conflict list with type badges, day coverage heatmap, per-menu health table
  - "Health" link added to menus list page
  - Day 95 test suite: 66 cases, 100% pass rate
  - Sprint 10.3 total: 180 tests (Days 93-95), all passing
  - Phase 10 complete: Days 86-95, 531 tests, all passing

- Vision Verification Module Foundation (Day 96):
  - New `storage/ai_vision_verify.py` (~300 LOC) — Claude Call 2 in production pipeline
  - `encode_menu_images(path)`: image/PDF to base64 for Claude vision API (PNG, JPEG, GIF, WebP, PDF)
  - Multi-page PDF support: all pages sent as separate image blocks in one API call
  - `verify_menu_with_vision(image_path, extracted_items)`: sends image + Call 1 items to Claude
  - Claude independently reads menu image, compares against extracted items, fixes errors
  - Corrects: misspellings, wrong prices, missing items, phantom items, category misassignments
  - `_parse_verification_response()`: JSON extraction with markdown fence stripping
  - `compute_changes_log()`: diffs original vs corrected with typed changes (name_fixed, price_fixed, item_added, item_removed, category_fixed, description_fixed, sizes_changed)
  - `verified_items_to_draft_rows()`: converts verified items to DB format (confidence=95)
  - Graceful fallback: returns original items on no API key, bad image, or API error
  - Reuses shared Anthropic client from `ai_menu_extract.py`
  - Day 96 test suite: 53 cases, 100% pass rate
  - Sprint 11.1 start (Phase 11: Production AI Pipeline)

- Vision Pipeline Integration (Day 97):
  - Wired `verify_menu_with_vision()` into `run_ocr_and_make_draft()` extraction pipeline
  - After Call 1 (Claude API text extraction) succeeds, Call 2 verifies items against menu image
  - On vision success: `verified_items_to_draft_rows()` with confidence=95, strategy="claude_api+vision"
  - Graceful fallback: if vision skips/errors, uses Call 1 items (confidence=90, strategy="claude_api")
  - Page batching: `_MAX_PAGES_PER_CALL=20` caps large PDFs, `_WARN_PAGES=8` logs info
  - `encode_menu_images()` accepts `max_pages` parameter for token limit management
  - Vision metadata stored in OCR debug payload (confidence, changes, model, skip_reason)
  - `pages_sent` field tracks image pages sent to Claude per verification
  - Day 97 test suite: 29 cases, 100% pass rate

- Semantic Pipeline Bridge (Day 98):
  - New `storage/semantic_bridge.py` (~200 LOC) — connects Claude extraction to Phase 8 semantic pipeline
  - `prepare_items_for_semantic()`: confidence 0-100→0.0-1.0, `_variants`→`variants`, `price_flags` init
  - `run_semantic_pipeline()`: full Phase 8 (cross-item, confidence, tiers, repair, auto-repair, report)
  - `apply_repairs_to_draft_items()`: copies name/category fixes back to draft items
  - Wired into `run_ocr_and_make_draft()` for Strategy 1 (claude_api / claude_api+vision)
  - Strategy 2 (heuristic AI) already runs semantic pipeline internally — no double-run
  - Semantic metadata saved in debug payload (quality_grade, tier_counts, repairs, per-item metadata)
  - Graceful fallback: semantic pipeline failure never blocks draft creation
  - Day 98 test suite: 68 cases, 100% pass rate
  - Cumulative: 1,481 passed (excl. Day 70 fixture errors)

- Pipeline Metrics & Observability (Day 99):
  - New `storage/pipeline_metrics.py` (~170 LOC) — `PipelineTracker` class for per-step timing & item counts
  - Step lifecycle: `start_step()`, `end_step(**extra)`, `skip_step(reason)`, `fail_step(error)`
  - `summary()` returns total_duration_ms, steps dict, item_flow, bottleneck, extraction_strategy
  - `format_duration(ms)` → human-readable ("450ms", "1.2s", "1m 5.0s")
  - Wired into `run_ocr_and_make_draft()` — tracks OCR, Call 1, Call 2, and semantic pipeline steps
  - Pipeline metrics saved in debug payload alongside vision_verification and semantic_pipeline blocks
  - Graceful fallback: tracker failure never blocks draft creation
  - Day 99 test suite: 72 cases, 100% pass rate
  - Cumulative: 1,553 passed (excl. Day 70 fixture errors)

- **Sprint 11.1 Capstone — End-to-End Pipeline Integration (Day 100):**
  - Comprehensive integration test suite: `tests/test_day100_pipeline_capstone.py` (56 tests)
  - 13 test classes validating all 4 Sprint 11.1 components working together
  - Full happy path: OCR → Call 1 → Call 2 (Vision) → Semantic → draft items + debug payload
  - Fallback paths: vision-skipped, vision-failed, Call 1 failed, parse failure, empty response
  - Strategy gating: semantic pipeline only runs on claude_api strategies
  - Debug payload completeness: vision_verification + semantic_pipeline + pipeline_metrics blocks
  - Confidence flow: 95→0.95 normalization, 90→0.9, deep-copy safety, repair flow-back
  - JSON round-trip, component interop, variant items, edge cases (empty/single/50-item)
  - **Sprint 11.1 complete: 278 tests (Days 96-100), 4 production modules (~900 LOC)**
  - Day 100 test suite: 56 cases, 100% pass rate
  - Cumulative: 1,609 passed (excl. Day 70 fixture errors)

- **Pipeline Cleanup & Debug View (Day 100.5):**
  - Removed heuristic AI fallback (Strategy 2) and legacy JSON fallback (Strategy 3) from pipeline
  - No API key = empty draft for manual input (free tier); heuristic garble eliminated
  - Removed 3 heuristic routes (`imports_ai_preview`, `imports_ai_commit`, `imports_ai_finalize`)
  - Removed `_draft_items_from_ai_preview` helper and `analyze_ocr_text` import
  - Removed "AI Preview (heuristics)" button from draft editor, "AI Tools" section from import view
  - New Pipeline Debug view (`/drafts/<id>/pipeline-debug`): renders pipeline summary, step timeline,
    vision verification details, semantic pipeline results, raw OCR text, and debug payload download
  - New template: `portal/templates/pipeline_debug.html` (~290 LOC)
  - Net: ~390 LOC removed, ~320 LOC added (template + route + tests)
  - Day 100.5 test suite: 26 cases, 100% pass rate
  - Cumulative: 1,635 passed (excl. Day 70 fixture errors)

- **Targeted Reconciliation Module (Day 101):**
  - New `storage/ai_reconcile.py` (~637 LOC): Claude Call 3 targeted reconciliation
  - Surgically reviews only 3-10 items flagged by semantic pipeline against original menu image
  - `collect_flagged_items()`: filter/prioritize by tier (reject→low→medium), cap at 10
  - `reconcile_flagged_items()`: send image + flagged items → confirmed/corrected/not_found
  - `merge_reconciled_items()`: merge corrections back (confirmed: +5 confidence, corrected: set 92)
  - Graceful skip on no flagged items, no API key, bad image; error handling returns originals
  - Day 101 test suite: 34 cases, 100% pass rate
  - Sprint 11.2 start (Phase 11: Production AI Pipeline)

- **Call 3 Pipeline Integration (Day 102):**
  - Wired `reconcile_flagged_items()` into `run_ocr_and_make_draft()` as pipeline Step 5
  - Full 5-stage flow: OCR → Call 1 → Call 2 (Vision) → Semantic → Call 3 (Reconciliation)
  - Gate: only runs when semantic pipeline flagged items (skip if zero flags)
  - Re-scores confidence after corrections (score + classify tiers)
  - Corrected fields propagate back to draft items (name, price, category, description)
  - Debug payload: new `targeted_reconciliation` block with full change log
  - Pipeline metrics: `STEP_CALL3_RECONCILE` tracked in success/skip/fail scenarios
  - Graceful degradation: Call 3 failure never blocks pipeline
  - Day 102 test suite: 36 cases, 100% pass rate
  - Cumulative: 1,705 passed (excl. Day 70 fixture errors)

- **Multimodal Call 1 (Day 102.5):** *** COMPLETE ***
  - Fixed imports.html: removed dead "Finalize with AI" button (route removed in Day 100.5)
  - Fixed draft_editor.html: variant toggle dropdown not responding (inline style override)
  - Live 200-item menu test: root cause — Call 1 was text-only, received garbled Tesseract OCR
  - Implemented multimodal Call 1: menu image as primary input + OCR text as secondary hint
  - extract_menu_items_via_claude() accepts image_path= kwarg, builds multimodal content
  - Dual prompts: _SYSTEM_PROMPT_MULTIMODAL (image-first) vs _SYSTEM_PROMPT_TEXT_ONLY (fallback)
  - Reuses encode_menu_images() from ai_vision_verify.py (lazy import, no circular deps)
  - All 3 Claude calls now multimodal: Call 1 (extract) + Call 2 (verify) + Call 3 (reconcile)
  - Day 102.5 test suite: 23 cases, 100% pass rate
  - Cumulative: 1,728 passed (excl. Day 70 fixture errors)

- **Prompt Rewrite + Category Normalizer (Day 102.6):** *** PARTIAL — REVERTED ***
  - Attempted Opus + adaptive thinking as single-call replacement for 3-call pipeline
  - First test (Draft #203) produced 259 items, but subsequent prompt tightening broke it
  - Root cause: Opus + thinking works with loose goal prompts, breaks with prescriptive rules
  - Debug blindspot: 50k-char log truncation hid Claude's actual response
  - Kept: minimal prompt (~1100 chars), category normalizer (22 categories + aliases),
    streaming API (messages.stream), debug prints. Reverted: back to Sonnet 3-call pipeline
  - Day 102.6 test suite: 52 cases, 100% pass rate
  - Cumulative: 1,780 passed (excl. Day 70 fixture errors)

- **Sonnet Thinking + File Debug Logging (Day 102.7):** *** COMPLETE ***
  - File-based debug logging: `_write_debug_log()` writes JSON to `storage/logs/call1_debug_{ts}.json`
    - Captures: model, thinking_active, multimodal, ocr_text_length, image_blocks_count,
      api_kwargs (sans image data), response metadata (stop_reason, tokens, block types),
      thinking_chars, response_text (first 2000 chars), parsed_item_count or error
    - Auto-creates `storage/logs/` directory, called on every API response (success or failure)
  - `THINKING_MODEL = "claude-sonnet-4-6"` — model used when `EXTENDED_THINKING=True`
    - Auto-overrides caller's model param when thinking is active
    - Portal wired: `use_thinking=_thinking_active` passed to Call 1
  - `EXTENDED_THINKING = False` remains default — 3-call Sonnet pipeline unchanged
  - Ready for live A/B comparison: flip flag to test single-call thinking vs 3-call pipeline
  - Day 102.7 test suite: 26 cases, 100% pass rate
  - Cumulative: 1,806 passed (excl. Day 70 fixture errors)

- **Prompt Iteration + Live Website Testing (Day 102.8b):** *** COMPLETE ***
  - Live site testing with real pizza menu (Draft #218): 108 → 133 items (+23%)
  - Section header propagation: "Wraps — Regular $10 / W/ Fries $14" → size variants on all wraps
  - Sauce/flavor handling: named sauces extracted as individual Sauces items (not collapsed)
  - Quantity split: "6 Pcs / 10 Pcs / 20 Pcs" wings → 3 separate items per size
  - Shared options: "Naked or Breaded", "White or Wheat" → size variants on each item
  - `PIPELINE_MODE = "thinking"` default (Opus single-call); "3call" toggle for pipeline validation
  - Day 102.8b test suite: 28 cases, 100% pass rate
  - Cumulative: 1,834 passed (excl. Day 70 fixture errors)

- **Full Pipeline E2E Validation (Day 103):** *** COMPLETE ***
  - PIPELINE_MODE toggle: "thinking" (Opus single-call) vs "3call" (full 3-call pipeline)
  - Per-call contribution analysis: tracks item count before/after each of 3 Claude calls
  - `EXTENDED_THINKING = PIPELINE_MODE == "thinking"` — mode auto-configures model + thinking
  - E2E test suite: 44 cases covering both pipeline modes, contribution deltas, graceful degradation
  - Day 103 test suite: 44 cases, 100% pass rate
  - Cumulative: 1,878 passed (excl. Day 70 fixture errors)

- **Sprint 11.2 Capstone — Targeted Reconciliation (Day 104):** *** COMPLETE ***
  - Comprehensive coverage: all 7 change types, all skip paths, all error recovery paths
  - Pre/post reconciliation metric comparison (items_confirmed, items_corrected, items_not_found)
  - Changes log: name_fix, price_fix, category_fix, description_fix, size_added, not_found, no_change
  - Debug payload completeness: full reconciliation metadata in debug JSON
  - Sprint 11.2 interoperability: reconciliation + semantic pipeline + vision verify all working together
  - Day 104 test suite: 59 cases, 100% pass rate
  - Sprint 11.2 COMPLETE: Targeted Reconciliation (Claude Call 3) fully integrated
  - Cumulative: 1,939 passed (excl. Day 70 fixture errors)

- **Confidence Gate Foundation (Day 105):** *** COMPLETE ***
  - Sprint 11.3 start (Phase 11: Production AI Pipeline)
  - Signal #6 in `storage/semantic_confidence.py`: Claude's self-reported call confidence
    - `stamp_claude_confidence(items, call_confidence)` — broadcasts call-level confidence to all items
    - 6-signal formula when `claude_confidence` set: grammar 0.27, name 0.18, price 0.18, variant 0.14, flags 0.13, claude 0.10
    - Fully backward-compatible: items without `claude_confidence` use original 5-signal formula unchanged
  - New `storage/confidence_gate.py`: binary pass/fail gate at the menu level
    - `evaluate_confidence_gate(items, call2_confidence, call3_confidence)` → GateResult
    - 4 signals: semantic 0.50, Call 2 0.25, Call 3 0.15, item count sanity 0.10
    - Unavailable call signals auto-redistribute weight to semantic
    - Default GATE_THRESHOLD = 0.90; customer_message never exposes numeric scores
  - Rejection logging in `storage/drafts.py`: new `pipeline_rejections` table
    - `log_pipeline_rejection()` — stores gate score, reason, all pipeline signals as JSON
    - `get_pipeline_rejections(restaurant_id, limit)` — retrieve for analysis/hardening
  - Day 105 test suite: 40 cases, 100% pass rate
  - Cumulative: 1,979 passed (excl. Day 70 fixture errors)

- **Gate Wiring into Live Pipeline (Day 106):** *** COMPLETE ***
  - `stamp_claude_confidence()` wired after Call 2 (before semantic pipeline) and after Call 3 (before re-score)
  - `evaluate_confidence_gate()` wired at end of pipeline — uses semantic items + both call confidences
  - Gate fail → `status="rejected"`, `error=customer_message`, rejection logged in `pipeline_rejections`
  - Gate pass → `status="done"` (unchanged behavior)
  - Debug payload includes `confidence_gate` block: passed, score, threshold, signals, reason
  - **Live-tested: Import #248 exposed false gate failure in thinking mode** (semantic pipeline skipped → score=0.10)
    - Fix: `if items and not _thinking_active:` guard mirrors existing Call 2/3 bypass logic
    - Import #249 confirmed fix: 145 items, status="done"
  - Day 106 test suite: 37 cases, 100% pass rate

- **Frontend Rejection UI (Day 107):** *** COMPLETE ***
  - `import_view.html`: rejection banner shown server-side on page load when `job.status == "rejected"`
    - `pollStatus()` also shows banner + populates customer_message from `data.error` on live transition
    - `rejected` → pill-red in JS `PILL_CLASSES`, added to `terminal` Set (polling stops)
    - No auto-redirect on rejected (only "done" triggers redirect to editor)
  - `imports.html`: "rejected" → red pill + "Rejected" label in Jinja2 and JS PILL_CLASS/LABEL
  - Day 107 test suite: 30 cases, 100% pass rate

- **E2E Gate Integration Tests (Day 108):** *** COMPLETE ***
  - `run_ocr_and_make_draft()` called directly with all external deps mocked (OCR text, 3 Claude calls, semantic pipeline, DB)
  - Verifies: gate pass → `status=done`, no rejection row; gate fail → `status=rejected`, `error=customer_message`, rejection row in `pipeline_rejections`
  - Covers: Call 2 skipped (redistributed weights), Call 3 skipped (no flagged items), thinking mode bypass (gate never runs), empty extraction (guard fires), threshold boundary + multi-rejection accumulation
  - Debug payload `confidence_gate` block verified: passed, score, threshold, signals (incl. `ocr_char_count`), reason
  - Day 108 test suite: 33 cases, 100% pass rate
  - Cumulative: 2,114 passed (excl. Day 70 fixture errors, Day 99 timing flakes)
