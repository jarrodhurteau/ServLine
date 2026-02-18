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

➡ **Phase 8 — Semantic Menu Intelligence (Sprint 8.4 in progress)**

Day 66 started Sprint 8.4 (Semantic Confidence) with a unified per-item `semantic_confidence` score in `storage/semantic_confidence.py`. Five weighted signals (grammar 0.30, name quality 0.20, price presence 0.20, variant quality 0.15, flag penalty 0.15) aggregated into a single 0.0-1.0 score with full audit trail. Polymorphic design works with both pipeline paths. 2,181 tests passing across all suites.

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
- ✅ Three-strategy extraction: Claude API → Heuristic AI → Legacy JSON
- ✅ Clean OCR text path (7,736 chars via image_to_string vs 762 chars fragmented)
- ✅ Price-safe, category-safe AI cleanup (non-hallucinating text surgeon)
- ✅ Auto-redirect from import view to draft editor on completion

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

---

## ⏭️ Phase 8 — Semantic Menu Intelligence

With OCR extraction stable and validated, Phase 8 focuses on semantic understanding:

### Sprint 8.1 — Core Grammar & Structure (Days 51-55) ✅ COMPLETE
- ✅ Menu item grammar parser (Day 51)
- ✅ Phrase-level category keywords (Day 51)
- ✅ Enhanced long-name parsing (Day 51)
- ✅ Pizza-specific grammar rules (Day 52)
- ✅ Real OCR testing — 100% classification (Day 52)
- ✅ OCR garble stripping (Day 52)
- ✅ New line types: size_header, topping_list, info_line, price_only (Day 52)
- ✅ Multi-menu grammar testing — full restaurant coverage (Day 53)
- ✅ Contextual multi-pass classification (Day 53)
- ✅ Broader description detection — 60+ ingredients, lowercase heuristic (Day 53)
- ✅ Post-garble noise cleanup & W/ normalization (Day 53)
- ✅ Item component detection — toppings, sauce, preparation, flavors (Day 54)
- ✅ Multi-column merge detection — whitespace-gap heuristic (Day 54)
- ✅ Pipeline integration — enrich_grammar_on_text_blocks (Day 55)
- ✅ OCR typo normalization — 88Q→BBQ, piZzA→PIZZA (Day 55)
- ✅ Confidence tiers — high/medium/low/unknown (Day 55)
- ✅ Fallback OCR hardening — 100% on degraded output (Day 55)

### Sprint 8.2 — Variant & Portion Logic (Days 56-60)
- ✅ Portion detection — half, whole, family, party (Day 51)
- ✅ Expanded crust/size vocabulary (Day 51)
- ✅ Shared size vocabulary — single source of truth (Day 56)
- ✅ Size grid context propagation — header → item variant mapping (Day 56)
- ✅ Grammar-to-variant bridge — pipeline + website integration (Day 56)
- ✅ Grammar-aware block building — ALL-CAPS item rescue (Day 56)
- ✅ Multi-price capture — all N prices preserved (Day 56)
- ✅ Website OCR quality — psm 3 + image preprocessing (Day 56)
- ✅ Variant price validation (S < M < L) — flag-only, track-separated (Day 57)
- ✅ Portion-aware price rules — half < whole, slice < pie (Day 57)
- ✅ Canonical size ordering — ordinal positions for all size types (Day 57)
- ✅ Combo modifier detection — "W/FRIES", "WIFRIES" normalization (Day 58)
- ✅ Combo food vocabulary — ~35 side items, truncation tolerance (Day 58)
- ✅ Combo kind classification — context-aware variant building (Day 58)
- ✅ Claude API extraction pipeline — 106 items at 90% confidence (Day 58)
- ✅ Three-strategy extraction — Claude → Heuristic AI → Legacy JSON (Day 58)
- ✅ Clean OCR text path — 7,736 chars via image_to_string (Day 58)
- ✅ Auto-redirect from import view to draft editor (Day 58)
- ✅ Cross-variant consistency checks — 6 validators, flag-only (Day 59)
- ✅ Variant confidence scoring — multi-signal per-variant scoring (Day 60)

### Sprint 8.3 — Cross-Item Consistency (Days 61-65) ✅ COMPLETE
- ✅ Cross-item consistency foundation — 3 checks, new module (Day 61)
- ✅ Fuzzy name matching — SequenceMatcher, 0.82 threshold, 4-char min (Day 62)
- ✅ Category reassignment suggestions — 4-signal neighbor smoothing (Day 63)
- ✅ Cross-category price coherence — 16 directional rules, median-based (Day 64)
- ✅ Cross-item variant pattern enforcement — 3 category-level checks (Day 65)

### Sprint 8.4 — Semantic Confidence (Days 66-70)
- ✅ Unified semantic confidence scoring — 5 weighted signals, audit trail (Day 66)
- Geometric heading detection from OCR blocks
- Confidence-based auto-review flagging

**Next Step:** Day 67 — Sprint 8.4 continues (Semantic Confidence)
