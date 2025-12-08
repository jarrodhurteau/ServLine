# ServLine
The ServLine project is a portal + API + AI brain system for restaurant call handling.  
This repo follows a phased build plan (Day 1 → onward), with Git commits marking each milestone.

---

## 📁 Folder Structure

servline/  
portal/  # Flask portal website  
  app.py  
  requirements.txt  
  contracts.py                          # lightweight draft schema validator (added Day 19 landmark)  
  templates/  
    base.html  
    index.html  
    login.html  
    restaurants.html  
    menus.html  
    items.html  
    item_form.html  
    imports.html                        # Import page (image/PDF + structured CSV/XLSX panels)  
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
  ocr_facade.py                        # ✅ Canonical OCR entrypoint (One Brain)  
  ai_ocr_helper.py  
  ai_cleanup.py  
  semantic_engine.py                   # Phase 4 pt.3  
  variant_engine.py                    # Phase 4 pt.3  
  category_hierarchy.py                # Phase 4 pt.4  
  price_integrity.py                   # Phase 4 pt.5–10  
  import_jobs.py                       # Import jobs + structured CSV/XLSX helpers (Phase 6)  
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

All OCR, AI, and semantic logic has been centralized into `/storage`.  
Legacy OCR paths have been phased out.

### Achievements:
- 🔁 Portal OCR retired  
- 🧠 Single canonical brain (`storage/ocr_facade.py`)  
- 🔎 Health endpoint confirmed green  
- ♻ Legacy imports shimmed then removed  
- 🔐 Draft pipeline using unified AI cleanup  
- 🧾 Finalize confirmed using One Brain end-to-end  

### Result:
ServLine now operates with a **true unified OCR engine**.  
All text extraction, cleanup, semantic logic, and validation flow through one brain.

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
- Status polling now toggles AI Finalize + Edit buttons dynamically  
- No impact on legacy OCR upload UX  
- Full portal workflow now supports:
  - OCR imports → Drafts  
  - Structured CSV imports → Drafts  
  - AI Finalize → Draft Editor  

**Phase 6 pt.3–4 complete.**

---

## 📊 Day 39 — Phase 6 pt.5–6: Structured CSV/XLSX Imports → Draft Editor

Day 39 completes the first **file-type family** for structured ingestion: CSV and XLSX both behave like first-class menu sources and land directly in the Draft Editor.

### Phase 6 pt.5 — Structured CSV Import Route

- Added `/import/csv` route in `portal.app` for **structured CSV** uploads.  
- Validates file presence and extension (`.csv` only).  
- Saves the upload into `uploads/` with a unique name.  
- Calls `import_jobs_store.create_csv_import_job_from_file(...)` to:
  - parse the CSV  
  - normalize rows through One Brain contracts  
  - create an `import_jobs` row with `ingest_mode="structured_csv"`.  
- Uses `drafts_store.create_draft_from_structured_items(...)` to build a DB-backed draft from `items`.  
- Stores linkage (`job_id`, row counts, filename) inside `source_meta`.  
- Flashes a concise summary banner:  
  - `job_id`, total rows, valid rows, skipped rows.  
- On success, redirects straight to the Draft Editor: `/drafts/<draft_id>/edit`.  
- On failure (size, bad type, missing helper), falls back with clear flash messages.

### Phase 6 pt.6 — Structured XLSX Import Route + UI

- Added `/import/xlsx` route in `portal.app` for **Excel-style (one row per item) menus**.  
- Validates file presence and extension (`.xlsx` only).  
- Saves XLSX files into `uploads/` with unique names.  
- Calls `import_jobs_store.create_xlsx_import_job_from_file(...)` to:
  - read the workbook  
  - normalize rows using the same structured contracts as CSV  
  - create `import_jobs` entries with `ingest_mode="structured_xlsx"`.  
- Uses `drafts_store.create_draft_from_structured_items(...)` to create the draft with `source_type="structured_xlsx"`.  
- Flashes a summary banner mirroring the CSV path (rows imported / skipped).  
- Redirects directly to the Draft Editor on success, or back to `/import` with friendly errors on failure.  

#### Import Page UI (imports.html)

- Import landing page now supports **four** primary flows:
  - Upload Image → OCR → Draft  
  - Upload PDF → OCR → Draft  
  - Structured CSV (POS export) → Draft  
  - Structured Excel (XLSX) → Draft  
- Adds a dedicated **Structured Excel (XLSX)** card with:
  - file selector (`accept=".xlsx"`)  
  - “Import XLSX to Draft” CTA button  
  - explanation copy for row-per-item POS exports.  
- Keeps a single shared progress bar + upload UX for image/PDF/structured imports.  
- Structured CSV/XLSX paths both reuse the One Brain structured contracts and draft creation, so everything downstream (AI Finalize, exports, price integrity, etc.) behaves exactly like OCR-based drafts.

**Phase 6 pt.5–6 complete: ServLine can now ingest real POS-style CSV/XLSX files into drafts with no OCR involved.**

---

# 🌄 System State

ServLine menu understanding is now:

✅ Unified OCR brain  
✅ End-to-end stable  
✅ Non-hallucinating  
✅ Price-safe  
✅ Categorization-safe  
✅ Structurally parsed  
✅ Ingredient-aware  
✅ Debuggable  
✅ Human-editable  
✅ Structured CSV/XLSX-ready (Phase 6 foundation)  
✅ Portal UI supports both OCR and structured ingestion paths  

---

# 🧭 Roadmap: Best-in-Class OCR & Import Plan

(High-level roadmap unchanged; Phase 6 progress updated.)

---

# ⭐ Next Execution Phase

Next up in Phase 6:

- **JSON structured import** (API + file) built on the same contracts  
- **Live preview + column mapping UI** for structured sources  
- **POS-grade ingestion layer** (multi-location behavior, taxes/fees, and export-quality guarantees)  

You’ll pick this up with Day 40 (Phase 6 pt.7–8).
