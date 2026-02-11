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

## ▶️ CURRENT POSITION

➡ **Phase 8 — Semantic Menu Intelligence (Sprint 8.2 IN PROGRESS — Day 57 Complete)**

Sprint 8.2 (Variant & Portion Logic) continues. Day 57 added within-item variant price validation — size variants are checked for monotonic price ordering (S < M < L), with track separation ensuring inches, word sizes, portions, and piece counts are validated independently. Price inversions from OCR errors are flagged for human review. 1,188 tests passing across all suites.

---

## 🌄 System State Summary

ServLine now has:

- ✅ Unified OCR brain
- ✅ Stable import flow (PDF/Image/CSV/XLSX/JSON)
- ✅ Deterministic orientation handling
- ✅ Rotation sweep for mis-rotated uploads
- ✅ Deterministic OCR scoring & selection
- ✅ Height-ratio line grouping (validated)
- ✅ Full debug artifacts and metadata
- ✅ Price-safe, category-safe AI cleanup
- ✅ Structured Draft Editor
- ✅ Column mapping for structured imports
- ✅ Menu item grammar parser (Phase 8)
- ✅ Phrase-level category keywords (Phase 8)
- ✅ Expanded variant vocabulary — portions, crusts, flavors (Phase 8)
- ✅ Semantic long-name splitting (Phase 8)
- ✅ OCR garble stripping — dot-leader noise removal (Phase 8)
- ✅ Pizza-specific grammar — CAPS split, size headers, topping lists (Phase 8)
- ✅ Real OCR validation — 100% classification on 258-line menu (Phase 8)
- ✅ Multi-menu grammar testing — full restaurant menu validated (Phase 8)
- ✅ Contextual multi-pass classification — heading/item resolution (Phase 8)
- ✅ Broader ingredient vocabulary — 60+ items for description detection (Phase 8)
- ✅ Post-garble noise cleanup — mid-length residue removal (Phase 8)
- ✅ Item component detection — toppings, sauce, preparation, flavors (Phase 8)
- ✅ Multi-column merge detection — whitespace-gap heuristic (Phase 8)
- ✅ Pipeline integration — grammar metadata in text_blocks + preview_blocks (Phase 8)
- ✅ OCR typo normalization — 88Q→BBQ, piZzA→PIZZA, bracket noise (Phase 8)
- ✅ Confidence tiers — high/medium/low/unknown scoring (Phase 8)
- ✅ Fallback OCR hardening — 100% on degraded Tesseract output (Phase 8)
- ✅ Shared size vocabulary — single source of truth for size detection (Phase 8)
- ✅ Size grid context propagation — headers map to item variants (Phase 8)
- ✅ Grammar-to-variant bridge — pipeline + website paths connected (Phase 8)
- ✅ Grammar-aware block building — prevents ALL-CAPS items becoming headers (Phase 8)
- ✅ Multi-price capture — all prices preserved, not just first two (Phase 8)
- ✅ Website OCR quality — psm 3 + preprocessing for cleaner extraction (Phase 8)

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
- Combo/meal detection (Day 58)
- Cross-variant consistency checks (Day 59-60)

### Sprint 8.3 — Cross-Item Consistency (Days 61-65)
- Price consistency checks across similar items
- Category consistency validation
- Duplicate detection with price conflicts

### Sprint 8.4 — Semantic Confidence (Days 66-70)
- Geometric heading detection from OCR blocks
- Multi-signal confidence scoring
- Confidence tiers (high/medium/low/unknown)

**Next Step:** Day 58 — Combo/meal detection
