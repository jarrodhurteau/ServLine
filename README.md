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
infra/  # Infra scripts (ngrok, Flask runner, stop scripts)  
  run_infra.ps1  
  stop_infra.ps1  
storage/  # SQLite database + OCR pipeline + seed + schema  
  servline.db  
  schema.sql  
  seed_dev.sql  
  drafts.py  
  ocr_pipeline.py (Phase 3 pipeline)  
  ocr_utils.py  
  ocr_types.py  
  ocr_facade.py  
uploads/  # User-uploaded menu files (+ .trash for recycle bin)  
.gitignore  
.vscode/  # VS Code tasks (auto-run infra, stop infra)  
README.md  # This file  

---

## ✅ Completed Milestones  

### 🚀 Day 1 – Portal Skeleton  
Basic Flask portal online with `/health`, VS Code infra tasks, and ngrok auto-start.  
✅ **Day 1 complete — project skeleton live.**

---

### 🚀 Day 2 – Restaurants & Menus  
Added DB tables (restaurants, menus, menu_items), list pages, and `/api/...` routes.  
✅ **Day 2 complete — restaurants/menus data model + UI in place.**

---

### 🚀 Day 3 – Menu Items + UI  
Item listing + form with price-in-cents accuracy.  
✅ **Day 3 complete — items editable via portal.**

---

### 🚀 Day 4 – Git + POS Handshake  
Version control, POS order scaffolding, event hooks.  
✅ **Day 4 complete — Git + POS scaffolding done.**

---

### 🚀 Day 5 – Router & Ordering Logic  
Voice router with upsell events and per-shop order logs.  
✅ **Day 5 complete — call-flow router working.**

---

### 🚀 Day 6 – Auth System  
Login/logout, session-based admin, navbar gating.  
✅ **Day 6 complete — authentication + admin scoping live.**

---

### 🚀 Day 7 – OCR Raw Capture  
Tesseract + PDF fallback, raw OCR to `storage/drafts/raw`, sweep logic.  
✅ **Day 7 complete — raw OCR capture pipeline exists.**

---

### 🚀 Day 8 – Uploads & Recycle Bin  
Uploads listing, move-to-bin + restore, artifact sweep, secure serve.  
✅ **Day 8 complete — uploads + recycle bin functional.**

---

### 🚀 Day 9 – Draft Review (Editing Flow Prep)  
Draft Review page, imports cleanup, job status sync, error pages.  
✅ **Day 9 complete — draft review + cleanup live.**

---

### 🚀 Day 10 – Portal Polish (1)  
Unified button styles, consistent forms, balanced layouts, styled error pages.  
✅ **Day 10 complete — portal polished + consistent.**

---

### 🚀 Day 11 – Portal Polish (2)  
Global UI standardization, improved toolbars, aligned tables + empty-states.  
✅ **Day 11 complete — fully consistent + bug-fixed portal.**

---

### 🚀 Day 12 – Drafts (DB-Backed Editor)  
New tables + `/drafts` list, full editor with search/add/duplicate/delete, auto-price formatting.  
✅ **Day 12 complete — DB-backed draft editing live.**

---

### 🚀 Day 13 – OCR Online → Draft → Approve  
Full OCR integration (Tesseract + Poppler), import → draft → approve workflow, CSV/JSON/XLSX export.  
✅ **Day 13 complete — upload → OCR → draft → approve works end-to-end.**

---

### 🚀 Day 14 – Draft Editor Revamp + Smarter OCR  
Safer rendering, debug utilities, improved OCR parsing (columns, headings, merges, cleanup).  
✅ **Day 14 complete — smarter OCR + stable Draft Editor.**

---

### 🚀 Day 15 – Failed App Split Attempt  
Modularization attempt caused routing issues → rolled back.  
❌ **Day 15 failed — reset to Day 14.**

---

### 🚀 Day 16 – Infra & PDF OCR Success  
`run_infra`/`stop_infra` scripts with PIDs, verified Tesseract + Poppler paths, PDF OCR passing.  
✅ **Day 16 complete — infra stable + PDF OCR functional.**

---

### 🚀 Day 17 – OCR Helper Deep Fixes  
v12 OCR helper: smarter category/header logic, price/desc merging, duplicate cleanup, richer debug JSON.  
✅ **Day 17 complete — OCR helper hardened and clean.**

---

### 🚀 Day 18 – Stability, OCR Env & Exports  
Fixed OCR/Poppler environment, confirmed pandas/scikit-learn, repaired all exports.  
✅ **Day 18 complete — OCR + Draft Editor + Exports stable.**

---

### 🚀 Day 19 – UI/UX Polish + OCR Precision  
- Auto-wrapping textareas  
- Category chips with deterministic hues  
- OCR preprocessing boost (CLAHE, denoise, unsharp, psm tuning, spell fixer)  
- `_safe_render` protection  
- Contract validator for draft export  
- AI OCR flag scaffold  

✅ **Day 19 complete — platform unified + OCR optimized.**

---

### 🚀 Day 20 – AI Heuristics Phase A + Editor Integration  
AI Preview + Commit endpoints  
“Finalize with AI Cleanup” integrated directly into editor with auto-refresh  
Baseline AI cleanup working end-to-end  

✅ **Day 20 complete — AI cleanup Phase A operational.**

---

### 🚀 Day 21 – OCR System Rebuild  
New modular pipeline, engine selector, legacy isolation, clean file tree.  
✅ **Day 21 complete — OCR rebuild framework in place.**

---

### 🚀 Day 22 – Phase 2 Wrap-Up  
Draft Editor polish, AI cleanup loop, status parity, export fixes, live pill refresh, outline rebuild.  
✅ **Day 22 complete — Phase 2 fully delivered.**

---

### 🚀 Day 23 – Phase 3 pts 1–2  
Rotation preview, status poller, AI finalize redirect, editor integration.  
All flows stable: Upload → Rotate → Preview → AI Finalize → Edit.  

✅ **Day 23 complete — Phase 3 (1–2) online.**

---

## 🚀 **Day 24 – Phase 3 pt.3–4: Category Infer + Two-Column Merge (MAJOR OCR BREAKTHROUGH)**

### ✔ What We Delivered
- **Category inference engine wired into final pipeline**, after segmentation + cleanup  
- **Integrated `category_infer.py` into `ai_cleanup` and ocr_pipeline → unified output**  
- **Added `_debug/ocr_text` category overlay + sample category report**  
- **Implemented *universal two-column price pairing* (Option-A geometry approach):**  
  - Works even when the menu is detected as *single column*  
  - No dependency on split_columns  
  - Uses bounding box + alignment heuristics  
  - Handles vertical text drift, skew, irregular spacing  
- **Massively improved price merging accuracy** (Wings, subs, sandwiches, calzones, wraps)  
- **Draft Editor reflects correct categories and prices**  
- Full end-to-end tests passed on **two real pizza menus**

### ⭐ Result
Day 24 produced the **cleanest imports ServLine has ever done**, and Phase 3 is now more than halfway complete.

✅ **Day 24 complete — Category Infer + Two-Column Merge fully working.**  
**Tags:** `day-24-phase-3-pt-4`, `ocr-two-column-merge`, `category-infer-integrated`

---

## 🔭 Next Up — Phase 3 (Days 25 → 26)

| Day | Focus | Key Deliverables |
|------|--------|----------------|
| **Day 25 – Phase 3 pt.5–6a** | 🔥 **Confidence Heat-Map + Multi-Price Extraction** | Add visual confidence overlay (`/debug/blocks/<id>`), add Draft Editor threshold slider, extract multi-size/multi-price variants (S/M/L, 10”/14”/18”), normalize all prices. |
| **Day 26 – Phase 3 pt.6b** | 🏁 **Final QA + Docs + Tagging** | Full regression test (upload → rotate → preview → finalize → edit → publish), end-to-end cleanup, README finalization, and tag `phase-3-complete`. |

---

## 🌟 Current Status  
🚀 **Day 24 is complete.**  
We now have a powerful pipeline:

**Rotate → Segment → Category Infer → Two-Column Merge → AI Cleanup → Draft Editor**  

The OCR engine is now smart, stable, and accurate — ready for Phase 3 pt.5.

