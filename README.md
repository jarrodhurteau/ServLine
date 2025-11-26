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

storage/ # SQLite database + OCR pipeline + seed + schema  
  servline.db  
  schema.sql  
  seed_dev.sql  
  drafts.py  
  ocr_pipeline.py  
  ocr_utils.py  
  ocr_types.py  
  ocr_facade.py  
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

## ✅ Completed Milestones

### 🚀 Day 1 – Portal Skeleton
Basic Flask portal online with `/health`, VS Code infra tasks, and ngrok auto-start.  
Day 1 complete.

---

### 🚀 Day 2 – Restaurants & Menus
DB tables, UI pages, full REST endpoints.  
Day 2 complete.

---

### 🚀 Day 3 – Menu Items + UI
Item listing, editing, and price-cents accuracy.  
Day 3 complete.

---

### 🚀 Day 4 – Git + POS Handshake
POS scaffolding + Git integration.  
Day 4 complete.

---

### 🚀 Day 5 – Router & Ordering Logic
Voice routing with upsell events and call logs.  
Day 5 complete.

---

### 🚀 Day 6 – Auth System
Login/logout, session-based admin.  
Day 6 complete.

---

### 🚀 Day 7 – OCR Raw Capture
OCR pipeline with Tesseract + Poppler.  
Raw OCR stored in `storage/drafts/raw`.  
Day 7 complete.

---

### 🚀 Day 8 – Uploads & Recycle Bin
File uploads, delete/restore, secure serve.  
Day 8 complete.

---

### 🚀 Day 9 – Draft Review
Draft Review page, improved cleanup, job sync.  
Day 9 complete.

---

### 🚀 Day 10 – Portal Polish (1)
Global styling updates, layout balance, error pages.  
Day 10 complete.

---

### 🚀 Day 11 – Portal Polish (2)
Toolbar updates, alignment fixes, empty states.  
Day 11 complete.

---

### 🚀 Day 12 – Drafts (DB-Backed Editor)
Search, add/delete, duplicate items.  
Auto price formatting.  
Day 12 complete.

---

### 🚀 Day 13 – OCR → Draft → Approve
Full import → OCR → draft → approve workflow.  
CSV/JSON/XLSX exports fixed.  
Day 13 complete.

---

### 🚀 Day 14 – Draft Editor Revamp
Safer rendering, debug tools, improved OCR parsing.  
Day 14 complete.

---

### 🚀 Day 15 – Failed App Split Attempt
Attempt reverted. Reset to Day 14.

---

### 🚀 Day 16 – Infra & PDF OCR
Infra stabilized; PDF OCR fully passing.  
Day 16 complete.

---

### 🚀 Day 17 – OCR Helper Refinements
Category/header logic, multi-price merging, duplicate cleanup, rich preview JSON.  
Day 17 complete.

---

### 🚀 Day 18 – Stability & Exports
OCR environment, draft editor, and exports stabilized.  
Day 18 complete.

---

### 🚀 Day 19 – UI/UX Polish + OCR Precision
Auto-resizing textareas  
Category chips  
OCR preprocessing (CLAHE, denoise, unsharp)  
_safe_render protection  
Draft validator  
Day 19 complete.

---

### 🚀 Day 20 – AI Cleanup Phase A
AI Preview + Commit  
Finalize with AI Cleanup  
Baseline cleanup reliable  
Day 20 complete.

---

### 🚀 Day 21 – OCR System Rebuild
Modular pipeline, engine selector, clean file tree.  
Day 21 complete.

---

### 🚀 Day 22 – Phase 2 Wrap-Up
Editor polish, AI cleanup loop, unified exports.  
Day 22 complete.

---

### 🚀 Day 23 – Phase 3 pts.1–2
Rotation preview  
Status poller  
AI finalize redirect  
Stable end-to-end flow  
Day 23 complete.

---

### 🚀 Day 24 – Phase 3 pt.3–4
Category inference  
Two-column merge  
Variant detection  
Day 24 complete.

---

### 🚀 Day 25 – Phase 3 Final (pts.5–7)
Confidence heat-map  
Safe AI cleanup  
Text shaping  
Item quality scoring  
Phase 3 complete.

---

## 🚀 Day 26 – Phase 4 pts.1–2
Raw → Cleanup → Refine pipeline  
Safe normalization  
Light refinement  
Confidence blending  
Foundation for Semantic OCR  
Day 26 complete.

---

## 🚀 Day 27 – Phase 4 pts.3–4
Phase 4 pt.3 — Semantic Block Understanding  
Phase 4 pt.4 — Multi-Line Description Reconstruction  
Day 27 complete.

---

## 🚀 Day 28 – Phase 4 pts.5–6

### ✔ Phase 4 pt.5 — Price Integrity Engine
- Added `price_integrity.py`
- Detects outlier prices and unsafe OCR misreads
- Auto-corrects obvious cases
- Produces `corrected_price_cents` + flags
- Integrated into `ocr_pipeline`
- Finalize uses corrected prices
- All exports validated

### ✔ Phase 4 pt.6 — Draft-Friendly Variants
- Normalized variant/price mapping  
- Unified preview → draft → finalize flow  
- Editor warnings  
- Fully tested  

⭐ **Day 28 complete.**

---

## 🚀 Day 29 – Phase 4 pts.7–8
✔ Category Hierarchy v2  
✔ Structured grouping in Draft Editor  
⭐ Day 29 complete.

---

## 🚀 Day 30 – Phase 4 pts.9–10
✔ Price Integrity Engine v2  
✔ Category/subcategory normalization  
⭐ Day 30 complete.

---

## 🚀 Day 31 – Phase 4 pt.11
Structured Draft Output v2  
Hierarchy preview  
Superimport bundle  
OCR debug stability  
⭐ Day 31 complete.

---

## 🚀 Day 32 – Phase 5 pts.1–2
✔ Finalize safety fixes  
✔ Unified draft bridge  
✔ Text-only AI cleanup  
⭐ Day 32 complete.

---

## 🚀 Day 33 – Phase 5 pts.3–4

### ✔ Phase 5 pt.3 — Long-Name Rescue & Garbage Tuning
- Detect overlong OCR names containing multiple items  
- Split names cleanly; move trailing fragments into description  
- Garbage-line detection tuned so real food items never get dropped  
- Prices, variants, and categories preserved exactly  

### ✔ Phase 5 pt.4 — Description Cleanup v2
- Ingredient smoothing (better comma spacing, trimmed junk tails)  
- Token-soup reduction (removes `\, }, Ew, Wm, Ss` etc.)  
- Normalizes punctuation without overcorrecting  
- Salvage-ratio tagging (`[AI Cleaned]`) only when needed  
- Verified end-to-end on pizza_real import (Preview → Draft → Finalize → Export)

⭐ **Day 33 complete — text structure is now stable and readable, with no risk to prices or categories.**

---

## 🚀 Day 34 – Phase 5 pts.5–6

### ✔ Phase 5 pt.5 — Deep Ingredient Normalization
- Detects chopped or half-OCR’d ingredient fragments  
- Normalizes commas, spacing, and capitalization  
- Removes stray symbol noise (`°, }, \\, &&`)  
- Salvage-ratio driven cleanup — evaluates whether content is real or junk  
- Zero hallucinations — only original OCR tokens are preserved  

### ✔ Phase 5 pt.6 — Connector & Phrase Smoothing
- Removes dangling connectors (`with`, `and`, `or`, `&`) at phrase tails  
- Preserves important idioms like “the side”  
- Converts scattered tokens into readable ingredient lists  
- Smart rescues for `on the side` and sauce phrases  
- Names and descriptions now reliably hold correct structural meaning  

⭐ **Day 34 complete — ingredient cleanup is now intelligent, safe, and readable.**

---

# 🌄 Phase 5 – Remaining Roadmap (Text & Semantics)

- Phase 5 pt.7 — Ingredient List Normalization Mode (toggleable list-style descriptions)  
- Phase 5 pt.8 — Size / Variant-aware text cleanup  
- Prep for Phase 6 (Structured CSV / JSON import)

---

# ⭐ Next Steps
You will start **Day 35 – Phase 5 pt.7**  
when you say:

**“ready for day 35”**
