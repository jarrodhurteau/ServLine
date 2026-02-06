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

## ▶️ CURRENT POSITION

➡ **Phase 8 — Semantic Menu Intelligence (IN PROGRESS)**

Phase 7 OCR hardening validated. Now advancing into semantic reasoning.

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

---

## ⏭️ Phase 8 — Semantic Menu Intelligence

With OCR extraction stable and validated, Phase 8 focuses on semantic understanding:

### Sprint 8.1 — Core Grammar & Structure (Days 51-55)
- Menu item grammar parser
- Enhanced long-name parsing
- Item component detection (base, toppings, modifiers)

### Sprint 8.2 — Variant & Portion Logic (Days 56-60)
- Portion detection (half, whole, family, party)
- Expanded crust/size vocabulary
- Variant price validation (S < M < L)

### Sprint 8.3 — Cross-Item Consistency (Days 61-65)
- Price consistency checks across similar items
- Category consistency validation
- Duplicate detection with price conflicts

### Sprint 8.4 — Semantic Confidence (Days 66-70)
- Geometric heading detection from OCR blocks
- Phrase-level category matching
- Multi-signal confidence scoring

**Next Step:** Day 51 — Begin menu item grammar parser
