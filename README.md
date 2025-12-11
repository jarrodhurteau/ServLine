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
  ocr_facade.py                        # ✅ Canonical OCR entrypoint (One Brain)  
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
  - `header_map` (original header → canonical field)  
  - `sample_rows` (up to five normalized rows)  
- `import_mapping.html` shows:
  - Left panel: Original column names and their current canonical mapping (Name, Description, Category, Price)  
  - Right panel: Table of sample rows using the same headers  
- Handles mixed and partial metadata:
  - If only headers exist, mapping panel still renders  
  - If only sample rows exist, table still renders with inferred headers  
  - If neither exists, shows friendly “no column metadata yet” messaging  

### Phase 6 pt.10 — JSON Import Panel + Mapping Eligibility Rules
- Import page (`imports.html`) updated with **Structured JSON** upload card:
  - JSON uploads post to `/import/json`  
  - Route wires through `storage.import_jobs.create_json_import_job_from_file`  
  - Valid JSON creates a structured draft and redirects to Draft Editor with summary flashes  
- Import jobs table now distinguishes mapping-capable jobs:
  - **Column Mapping button enabled only for table-shaped structured imports (CSV/XLSX)**  
  - JSON, PDFs, and image-based jobs show a disabled Column Mapping button with tooltip  
- Column Mapping page validated for:
  - Structured CSV imports  
  - Structured XLSX imports  
- JSON structured jobs continue to flow through:
  - Draft Editor  
  - AI Finalize  
  - Export endpoints  

**Day 41 completes Phase 6 pt.9–10: live column mapping previews for CSV/XLSX and a first-class JSON upload path in the portal.**

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
✅ Structured CSV/XLSX/JSON-ready  
✅ Column Mapping view wired to real metadata  

---

# 🧭 Roadmap: Best-in-Class OCR & Import Plan

(High-level roadmap unchanged; Phase 6 progress updated.)

---

# ⭐ Next Execution Phase

Next up in Phase 6:

- Full **Column Mapping Editor** (interactive overrides for each header)  
- Persist user mappings back to `import_jobs` and any dedicated mapping tables  
- Re-run structured parsing under updated mappings to regenerate draft items  
- POS-grade ingestion layer (multi-location formatting, tax and fee fields, sections, and modifiers)
