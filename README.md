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
  import_jobs.py                       # Import jobs + structured CSV helpers (Day 37, Phase 6 pt.2)  
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

### Decision:
No warning UI added — signal too weak vs noise.

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

- Added `POST /api/drafts/import_structured` in `portal/app.py`.  
- Accepts a CSV upload of menu items (`multipart/form-data`, `file=`).  
- Normalizes CSV rows into structured items via a shared helper.  
- Calls `storage.drafts.create_draft_from_structured_items(...)` to create a draft.  
- Returns JSON with `draft_id` and `redirect_url` for the Draft Editor.  
- Reuses One Brain safety rules (no price/category/variant corruption).

### Phase 6 pt.2 — Structured CSV Pipeline & Import Jobs

- Extended `storage/import_jobs.py` with a **full structured CSV engine**:
  - Header normalization & alias detection (e.g. `Name`, `Item`, `Item Name` → `name`).  
  - Row → canonical item mapping (`name`, `description`, `category`, `price_cents`, `size`, `sku`, etc.).  
  - Leftover columns captured into `meta` for future POS / analytics use.  
- Added `parse_structured_csv(...)` to:
  - Read CSVs from disk.  
  - Normalize rows into canonical item dicts.  
  - Validate via `storage.contracts` (One Brain structured-item contracts).  
  - Produce `clean_items`, `errors`, `summary`, and `header_map`.  
- Added `create_structured_import_job(...)` to write resilient rows into `import_jobs`:
  - Introspects table columns via `PRAGMA table_info(import_jobs)`.  
  - Populates `filename`, `source_path`, `source_type`, `restaurant_id`, `status`.  
  - Stores `summary_json` and (optionally) `payload_json`.  
- Added `create_csv_import_job_from_file(...)`:
  - Parses a CSV on disk into structured items.  
  - Stores a job row with `ingest_mode="structured_csv"`, header map, and validation summary.  
  - Returns:
    - `job_id`  
    - `items` (clean items)  
    - `errors` (row-level validation issues)  
    - `summary` (total/valid/error counts)  
    - `header_map`  
    - `job_summary` (what went into `summary_json`).  
- Created a sample test asset: `fixtures/menus/sample_structured_menu.csv`.  
  - Verified end-to-end in REPL: job row created, four items ingested successfully, zero error rows.

**Result:**  
ServLine can now **ingest structured CSV menus**, normalize them through One Brain, and track each import as a first-class job in `import_jobs`.

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

---

# 🧭 Roadmap: Best-in-Class OCR & Import Plan

This is the roadmap that will put ServLine in the top tier of OCR + structured import systems.

---

## 🔹 Phase 6 — Structured Menu Import (No OCR)

Goal: Allow direct CSV / JSON menu ingestion.

Status so far (Day 37):
- ✅ Canonical structured item schema & contracts (One Brain).  
- ✅ CSV validation and normalization pipeline.  
- ✅ `import_jobs` integration for structured imports.  
- ✅ API endpoint to create drafts from structured CSV uploads.  

Upcoming:
- JSON structured import route.  
- UI wiring from the portal (CSV/JSON upload UX).  
- POS-safe ingestion layer and mapping helpers.

---

## 🔹 Phase 7 — Vision Upgrade Layer

Goal: Compete with enterprise OCR engines.

Planned:
- Multi-pass OCR  
- Rotation auto-detection  
- Column confidence mapping  
- Bounding box learning  
- OCR confidence calibration  
- Table detection  
- Font-style analysis  

---

## 🔹 Phase 8 — Language Intelligence Layer

Goal: Understand menus, not just read them.

Planned:
- Menu grammar parser  
- Dish intent detection  
- Price pattern models  
- Portion detection  
- Modifier logic (extras, combos, meals)  
- Ingredient authority map  

---

## 🔹 Phase 9 — Trust & Autonomy

Goal: Production-grade AI system.

Planned:
- Rule engine  
- Trust scoring  
- Change tracking  
- Human-approval gates  
- Versioned drafts  
- POS diff engine  
- Audit logs  

---

# ⭐ Next Execution Phase

Day 37 (Phase 6 pt.1–2) is complete.  
Next up: continue **Phase 6 — Structured Import** (JSON ingest + UI wiring).

You’ll pick this up when you say something like:

**“ready for day 38 — Phase 6 pt.3–4.”**
