# ServLine

ServLine is a **portal + API + AI brain** system for restaurant menu onboarding (OCR + structured imports → living editable menu → export to POS).

This repo follows a phased build plan (**Day 1 → onward**), with Git commits marking each milestone.

---

## 📁 Folder Structure

servline/  
portal/  # Flask portal website  
  app.py  
  requirements.txt  
  contracts.py                          # lightweight draft schema validator (added Day 19 landmark)  
  ocr_worker.py                         # active OCR worker (image pipeline + parsing + debug artifacts)  
  templates/  
    base.html  
    index.html  
    login.html  
    restaurants.html  
    menus.html  
    items.html  
    item_form.html  
    imports.html                        # Import page (image/PDF + structured CSV/XLSX/JSON panels)  
    import.html                         # Legacy import view (per-job)  
    drafts.html  
    draft_editor.html  
    uploads.html  
    uploads_trash.html  
    draft_review.html  
    raw.html  
    errors/404.html  
    errors/500.html  

infra/   # Infra scripts (ngrok, Flask runner, stop scripts)  
  run_infra.ps1  
  stop_infra.ps1  

storage/ # SQLite database + OCR brain + semantic engines (ONE BRAIN)  
  servline.db  
  schema.sql  
  seed_dev.sql  
  drafts.py  
  ocr_pipeline.py  
  ocr_utils.py  
  ocr_types.py  
  ocr_facade.py                        # ✅ Canonical OCR library entrypoint (One Brain)  
  ai_ocr_helper.py  
  ai_cleanup.py  
  semantic_engine.py                   # Phase 4 pt.3  
  variant_engine.py                    # Phase 4 pt.3  
  category_hierarchy.py                # Phase 4 pt.4  
  price_integrity.py                   # Phase 4 pt.5–10  
  import_jobs.py                       # Import jobs + structured CSV/XLSX/JSON helpers (Phase 6)  
  contracts.py                         # One Brain structured-item contracts (Phase 6 pt.1–2)  

fixtures/                              # Sample menus & test assets  
  menus/                               # e.g. pizza_real.pdf, sample_structured_menu.csv, sample_structured_menu.xlsx  

uploads/                               # User-uploaded menu files  

.gitignore  
.vscode/  
README.md  

---

# ✅ Completed Milestones

## 🚀 Day 1–14 — Portal, Data Model, Draft Editor
Core UI, database schema, reviews, workflow, auth, exports, error handling.

---

## 🚀 Day 15 — Failed Split Attempt (Reverted)

---

## 🚀 Day 16–19 — OCR Infrastructure & Precision
- OCR pipeline stabilized  
- CLAHE, grayscale, sharpening  
- Categories, chips, editor refinements  

---

## 🚀 Day 20–22 — AI Cleanup Phase A
- AI Preview  
- AI Finalize  
- Safe cleanup baseline  
- Unified export path  

---

## 🚀 Day 23–25 — Phase 3: Semantic Reconstruction
- Rotation preview  
- Category inference  
- Two-column merge  
- Variant detection  
- Confidence overlays  
- Garbage tuning  

Phase 3 complete.

---

## 🚀 Day 26–31 — Phase 4: Structural OCR System

### Phase 4 Highlights
- Semantic block understanding  
- Multi-line merging  
- Variant normalization  
- Category hierarchy v2  
- Price Integrity Engine v2  
- Structured Draft Output v2  
- Superimport bundle  
- Stability hardening  

Phase 4 complete.

---

## 🚀 Day 32–35 — Phase 5: AI Text Surgeon

### Phase 5 Achievements
- Long-name rescue  
- Non-hallucinated cleanup  
- Ingredient smoothing  
- Ingredient list normalization  
- Safety tagging (`[AI Cleaned]`)  
- Size / variant aware cleanup  
- Strict protection for:
  - prices  
  - categories  
  - variants  

Phase 5 complete.

---

## 🛠️ Day 36 — Phase 5 Cleanup Day

Stabilization and validation phase.

### ✔ Finalize Flow Verification
- Tested OCR → Draft → AI Finalize end-to-end  
- No crashes  
- No data loss  
- No category drift  

### ✔ Integrity Guarantees Proven
- Prices frozen  
- Categories frozen  
- Variants frozen  
- Names cleaned safely  
- Descriptions stabilized  
- Salvage ratio working  

### ✔ Quality Guard Validation
- No high-junk flags  
- No casing disasters  
- No empty-content failure  

**Day 36 complete.**

---

## 🧠 ONE BRAIN MIGRATION (SPECIAL MILESTONE)

### ✅ One Brain OCR Unification — COMPLETE

All OCR, AI, and semantic logic has been centralized into `/storage` as a reusable library.  
Legacy OCR entrypoints were retired or shimmed to route through the unified architecture.

### Achievements:
- 🧠 Single canonical OCR library entrypoint (`storage/ocr_facade.py`)  
- 🔎 Health endpoint confirmed green  
- ♻ Legacy imports shimmed then removed  
- 🔐 Draft pipeline using unified AI cleanup  
- 🧾 Finalize confirmed using One Brain end-to-end  

### Result:
ServLine operates with a **true unified brain layer**.  
OCR utilities, cleanup, semantic logic, and validation live in `/storage` and are callable from the portal.

**One Brain migration complete.**

---

## 🧮 Day 37 — Phase 6 pt.1–2: Structured CSV Import Foundation

Phase 6 begins the **no-OCR structured import path**, letting ServLine ingest POS-style menu data directly.

### Phase 6 pt.1 — Structured Draft Import API
- Added `/api/drafts/import_structured`  
- Structured uploads produce canonical structured items  
- Draft created via One Brain validation  
- JSON response returns `draft_id` + `redirect_url`

### Phase 6 pt.2 — Structured CSV Pipeline & Import Jobs
- Full structured CSV parser in `storage/import_jobs.py`  
- Header aliasing, canonical mapping, row normalization  
- One Brain row validation  
- `import_jobs` rows created with `ingest_mode="structured_csv"`  
- Output: summary, errors, header_map, clean_items  

**Phase 6 foundation complete.**

---

## 🚀 Day 38 — Phase 6 pt.3–4: AI Finalize Wiring + Structured Import UI

### Phase 6 pt.3 — AI Finalize → Draft Editor Integration
- `imports_ai_finalize` now uses the **One Brain cleanup pipeline**  
- AI Finalize rewrites draft items safely  
- Draft status updated to `finalized`  
- Clean redirect to Draft Editor  
- No regressions in OCR import path  
- End-to-end test passed

### Phase 6 pt.4 — Structured Import UI (Portal)
- Added **Structured CSV import panel** to `imports.html`  
- CSV uploads now create drafts directly from the portal  
- Progress bar unified across image/PDF/CSV imports  
- Added **Finalize with AI** button to `imports.html` job rows  
- Status polling toggles AI Finalize and Edit buttons dynamically  
- No impact on legacy OCR upload UX  

**Phase 6 pt.3–4 complete.**

---

## 📊 Day 39 — Phase 6 pt.5–6: Structured CSV/XLSX Imports → Draft Editor

### Phase 6 pt.5 — Structured CSV Import Route
- `/import/csv` route  
- CSV normalized, validated, and turned into draft items  
- Summary banner  
- Redirect to Draft Editor  

### Phase 6 pt.6 — Structured XLSX Import Route + UI
- `/import/xlsx` route  
- Excel rows normalized, validated, and turned into draft items  
- Summary banner  
- Redirect to Draft Editor  
- Import page updated with XLSX card  

**Phase 6 pt.5–6 complete.**

---

## 📐 Day 40 — Phase 6 pt.7–8: JSON Imports + Column Mapping Skeleton

### Phase 6 pt.7 — JSON Structured Import (foundation validated)
- JSON files brought into the structured-import flow  
- JSON drafts validated through One Brain structured-item contracts  
- Draft Editor works for structured JSON just like CSV/XLSX  
- Export buttons (CSV/JSON/XLSX) verified  
- Finalize with AI works on structured JSON

### Phase 6 pt.8 — Column Mapping (Initial Skeleton)
- Added route: `/imports/<job_id>/mapping`  
- Initial template: `import_mapping.html`  
- Mapping page shows:
  - filename  
  - status  
  - Column Mapping panel  
  - Sample Rows panel  
- Graceful empty state when `header_map` and `sample_rows` are missing  

**Day 40 completes the structured import trifecta at the engine level (CSV + XLSX + JSON) and lays the groundwork for column mapping.**

---

## 🧭 Day 41 — Phase 6 pt.9–10: Live Column Mapping + JSON Portal Upload

### Phase 6 pt.9 — Column Mapping Wired to One Brain Metadata
- `/imports/<job_id>/mapping` now reads real metadata from `import_jobs`:
  - `header_map`  
  - `sample_rows`  
- Mapping page shows both:
  - Left: original → canonical mappings  
  - Right: sample row table using same header order  
- Graceful degrade for partial metadata  
- Robust handling for CSV/XLSX import jobs  

### Phase 6 pt.10 — JSON Import Panel + Mapping Eligibility Rules
- Structured JSON card added to `imports.html`  
- `/import/json` route implemented  
- JSON jobs redirect to Draft Editor automatically  
- Column Mapping button enabled **only** for CSV/XLSX jobs  
- JSON, PDF, and image-based jobs show disabled Mapping button with tooltip  

**Day 41 completes Phase 6 pt.9–10.**

---

## 🧠 Day 42 — Phase 7 pt.1–2: One Brain OCR Verification & Draft Pipeline Hardening

### Phase 7 pt.1 — Enforce One Brain OCR Everywhere
- Verified OCR extraction calls route exclusively through the intended pipeline for imports.  
- Removed remaining legacy fallback paths where found.  
- Confirmed worker OCR active (`ocr_engine: "ocr_worker"`).  
- Added explicit pipeline metadata for debugging and audits.  
- Ensured draft creation prefers AI Preview payload when present.  
- Added strict debugging hooks to confirm no legacy OCR is invoked.

### Phase 7 pt.2 — Draft Construction + Debug Layer Hardening
- Refactored `_get_or_create_draft_for_job` for clarity & correctness  
- Removed duplicate function body accidentally introduced in past patches  
- Ensured:
  - AI payload → draft creation is first choice  
  - Legacy draft_path is used only when AI payload missing  
  - Debug metadata correctly indicates pipeline path  
  - No stray “fix attempts” remain  
- Verified full end-to-end import → draft → AI Preview → debug path  

**Day 42 complete — Phase 7 pt.1–2 stable.**

---

## 🧠 Day 43 — Phase 7 pt.3–4: OCR Ingestion Audit & Debug Stabilization

### Phase 7 pt.3 — OCR Ingestion Path Audit
- Full read-only audit of OCR → Draft ingestion flow  
- Verified a single authoritative OCR → Draft creation path  
- Confirmed raw OCR persistence, draft hydration, and Draft Editor visibility  
- Identified and removed duplicate Flask routes causing runtime assertion errors  
- No OCR behavior changes introduced  

### Phase 7 pt.4 — Debug & Route Hardening
- Stabilized layout / geometry debug endpoints  
- Ensured debug routes are read-only and non-invasive  
- Confirmed no legacy OCR helpers are reachable  
- System remains fully operational post-audit  

**Day 43 complete — Phase 7 pt.3–4 closed.**

---

# 🔧 Day 44 — Maintenance Day (COMPLETED)

**Not a Phase day. Not Phase 7 pt.5–6.**

Maintenance focus:
- OCR work-image correctness vs segmentation artifacts  
- Investigation of noisy OCR output quality  
- Pre-cleanup diagnostics only (no feature expansion)  
- Stability, inspection, and confidence improvements  

### Maintenance Day 44 Findings (Authoritative)
- OCR input image confirmed **human-readable and non-binary** (grayscale OCR input; bw used only for splitter/debug).  
- Debug artifacts confirmed present and consistent per job (work image, pre_gray, pre_bw, OCR input, block crops, main/fallback OCR outputs).  
- OCR noise source is upstream logic behavior:
  - Orientation normalization + PSM interaction  
  - Quality scoring allowing gibberish to pass  

### Roadmap Updates Created By Day 44 Diagnosis
The following fixes were scheduled into Phase 7 continuation work (pt.5–6):

#### Phase 7 pt.5 — Orientation & OCR Mode Hardening (Added Scope)
- Make rotation normalization deterministic and first-class before OCR  
- Tie PSM selection to orientation + layout characteristics  
- Persist debug metadata:
  - orientation_applied  
  - psm_selected  
- Add upright OCR input artifact (post-rotation, pre-OCR)  

#### Phase 7 pt.6 — OCR Quality Scoring Reality Fix (Added Scope)
- Harden item quality scoring against gibberish:
  - alpha-density guard  
  - vowel/token ratio floor  
  - penalize long low-entropy tokens  
- Add quality_flags metadata (flag-only, do not delete)  

**Day 44 complete.**

---

## 🧠 Day 45 — Phase 7 pt.5–6: Orientation Enforcement + OCR Reality Fixes

### Phase 7 pt.5 — Orientation & OCR Mode Hardening (Completed)
- Enforced deterministic orientation normalization inside the OCR worker:
  - `normalize_orientation()` is applied before preprocessing and before building OCR inputs.
  - Legacy Tesseract OSD auto-rotate is explicitly disabled to prevent double-rotation or unpredictable orientation changes.
- Added stronger OCR input provenance:
  - Saved the exact processed grayscale artifact used for OCR (`.ocr_input_gray.png` and `.final_ocr_input.png`).
  - Saved a capped set of per-column OCR grayscale blocks (`.ocr_block_XX.png`) to prove what was actually sent to Tesseract.
- Logged OCR execution clarity:
  - Explicit logging of OCR configs (`psm6` primary, `psm3` fallback).
  - Persisted main, fallback, and selected OCR outputs as debug text files.

### Phase 7 pt.6 — OCR Output Reality Fixes (Completed)
- Hardened OCR postprocessing against common numeric corruption:
  - Repaired split-decimal artifacts (for example: `9 98` → `9.98`, `17 95` → `17.95`).
  - Removed trailing orphan numeric garbage after valid prices (for example: `25.50 475` → `25.50`).
- Confirmed multi-price lines remain intact and usable for downstream parsing.
- Verified fixes against real OCR jobs using saved debug artifacts (`ocr_main`, `ocr_fallback`, `ocr_used_*`).

**Day 45 complete — Phase 7 pt.5–6 closed.**

---

## 🧠 Day 46 — Phase 7 pt.8: Rotation Sweep (Worker Wiring) (COMPLETED)

Phase 7 pt.8 addresses a real-world import problem: **PDFs/images are frequently uploaded sideways or upside down**.  
We now brute-force rotations and select the best OCR output by a deterministic quality score.

### What was added
- Rotation sweep flags and defaults (env-driven):
  - `OCR_ENABLE_ROTATION_SWEEP` (default on)
  - degrees tested: `0, 90, 180, 270`
- Rotation sweep wired into the actual OCR path:
  - sweep happens inside `_ocr_block_gray()` (the function that calls Tesseract)
  - best rotation is chosen using `_quality_score()`
- Debug clarity:
  - logs show rotation selection:  
    `"[RotationSweep] block_best_rotation_cw=90 score=..."`
- Verified alongside orientation enforcement:
  - `normalize_orientation()` still runs first
  - legacy OSD remains disabled to prevent double-rotation

### End-to-end verification (manual test)
- Dev: Start All → upload PDF → import completes → draft created
- Verified log signals:
  - orientation applied (worker-enforced)
  - rotation sweep chooses best OCR pass when needed
  - fallback PSM selection still functions
- Draft Editor loads with items and AI Finalize works without regressions

**Day 46 complete — Phase 7 pt.8 closed.**

---

# 🌄 System State

ServLine menu understanding is now:

✅ Unified OCR brain (library layer in `/storage`)  
✅ End-to-end stable import flow (upload → OCR → draft → editor)  
✅ Non-hallucinating AI cleanup  
✅ Price-safe  
✅ Categorization-safe  
✅ Structurally parsed  
✅ Ingredient-aware  
✅ Debuggable (full OCR artifacts + logs)  
✅ Human-editable Draft Editor  
✅ Structured CSV/XLSX/JSON-ready  
✅ Column Mapping view wired to real metadata  
✅ One Brain OCR verified + hardened (Day 42–46)  
✅ Orientation enforcement + debug artifacts (Day 45)  
✅ Rotation sweep for mis-rotated uploads (Day 46)

---

# ⭐ Next Execution Phase

**Day 47 — Phase 7 pt.7**

- Multi-pass OCR execution across full-page rotations (0°, 90°, 180°, 270°) using the same grayscale-first pipeline.
- Deterministic winner selection using improved quality scoring.
- Persist per-job metadata:
  - rotation_selected  
  - psm_selected  
  - quality_score  
  - rejection reasons (flag-only)  
- Confidence fusion across OCR passes.
- Improved block → item grouping stability.
