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
    imports.html  
    import.html  
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
  menus/                               # e.g. pizza_real.pdf, sample_structured_menu.csv  

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
- CSV upload produces canonical structured items  
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

# 🚀 Day 38 — Phase 6 pt.3–4: AI Finalize Wiring + Structured Import UI

### Phase 6 pt.3 — AI Finalize → Draft Editor Integration
- `imports_ai_finalize` now uses the **One Brain cleanup pipeline**  
- AI Finalize rewrites draft items safely  
- Draft status updated to `finalized`  
- Clean redirect to Draft Editor  
- No regressions in OCR import path  
- End-to-end test passed

### Phase 6 pt.4 — Structured Import UI (Portal)
- Added **Structured CSV import panel** to `import.html`  
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
✅ Structured CSV-ready (Phase 6 foundation)  
✅ Portal UI supports both OCR and structured ingestion paths  

---

# 🧭 Roadmap: Best-in-Class OCR & Import Plan

(unchanged except Phase 6 progress — omitted for brevity)

---

# ⭐ Next Execution Phase

Next up in Phase 6:

- JSON structured import  
- XLSX structured import  
- Live preview + column mapping  
- POS-grade ingestion layer  

You’ll pick this up with:

