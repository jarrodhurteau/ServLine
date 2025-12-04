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
  import_jobs.py                       # Import job lookup helper (Day 31)  

uploads/  
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

# 🌄 System State

ServLine OCR is now:

✅ Unified  
✅ End-to-end stable  
✅ Non-hallucinating  
✅ Price-safe  
✅ Categorization-safe  
✅ Structurally parsed  
✅ Ingredient-aware  
✅ Debuggable  
✅ Human-editable  

---

# 🧭 Roadmap: Best-in-Class OCR Plan

This is the roadmap that will put ServLine in the top tier of OCR systems.

---

## 🔹 Phase 6 — Structured Menu Import (No OCR)

Goal: Allow direct CSV / JSON menu ingestion.

Planned:
- Canonical import schema
- CSV validation
- JSON import
- Draft creation without OCR
- POS-safe ingestion layer

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

You will start **Phase 6 — Structured Import Foundation**  
when you say:

**“Start Phase 6 pt.1.”**
