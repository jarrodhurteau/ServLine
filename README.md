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
  ocr_pipeline.py  
  ocr_utils.py  
  ocr_types.py  
  ocr_facade.py  
  ai_ocr_helper.py  
  ai_cleanup.py  
  semantic_engine.py                   # Phase 4 pt.3  
uploads/  
.gitignore  
.vscode/  
README.md  

---

## ✅ Completed Milestones  

### 🚀 Day 1 – Portal Skeleton  
Basic Flask portal online with `/health`, VS Code infra tasks, and ngrok auto-start.  
✅ **Day 1 complete.**

---

### 🚀 Day 2 – Restaurants & Menus  
DB tables, UI pages, full REST endpoints.  
✅ **Day 2 complete.**

---

### 🚀 Day 3 – Menu Items + UI  
Item listing + editing + price-cents accuracy.  
✅ **Day 3 complete.**

---

### 🚀 Day 4 – Git + POS Handshake  
POS scaffolding + Git integration.  
✅ **Day 4 complete.**

---

### 🚀 Day 5 – Router & Ordering Logic  
Voice routing with upsell events and call logs.  
✅ **Day 5 complete.**

---

### 🚀 Day 6 – Auth System  
Login/logout, session-based admin.  
✅ **Day 6 complete.**

---

### 🚀 Day 7 – OCR Raw Capture  
Full OCR pipeline with Tesseract + Poppler.  
Raw OCR stored in `storage/drafts/raw`.  
✅ **Day 7 complete.**

---

### 🚀 Day 8 – Uploads & Recycle Bin  
File uploads, delete/restore, secure serve.  
✅ **Day 8 complete.**

---

### 🚀 Day 9 – Draft Review  
Draft Review page, improved cleanup, job sync.  
✅ **Day 9 complete.**

---

### 🚀 Day 10 – Portal Polish (1)  
Global styling updates, layout balance, error pages.  
✅ **Day 10 complete.**

---

### 🚀 Day 11 – Portal Polish (2)  
Toolbar updates, alignment fixes, empty states.  
✅ **Day 11 complete.**

---

### 🚀 Day 12 – Drafts (DB-Backed Editor)  
Draft editor with search/add/delete/duplicate.  
Auto price formatting.  
✅ **Day 12 complete.**

---

### 🚀 Day 13 – OCR → Draft → Approve  
Full import → OCR → draft → approve workflow.  
Exports (CSV/JSON/XLSX) fixed.  
✅ **Day 13 complete.**

---

### 🚀 Day 14 – Draft Editor Revamp  
Safer rendering, debug tools, improved OCR parsing (columns/headings/merges).  
✅ **Day 14 complete.**

---

### 🚀 Day 15 – Failed App Split Attempt  
Attempt reverted.  
❌ **Reset to Day 14.**

---

### 🚀 Day 16 – Infra & PDF OCR  
Infra stable, PDF OCR fully passing.  
✅ **Day 16 complete.**

---

### 🚀 Day 17 – OCR Helper Refinements  
Category/header logic, multi-price merging, duplicate cleanup, rich debug JSON.  
✅ **Day 17 complete.**

---

### 🚀 Day 18 – Stability & Exports  
OCR environment, draft editor, and all export formats stabilized.  
✅ **Day 18 complete.**

---

### 🚀 Day 19 – UI/UX Polish + OCR Precision  
- Auto-resizing textareas  
- Category chips with deterministic hues  
- OCR preprocessing boost (CLAHE, denoise, unsharp)  
- `_safe_render` protection  
- Draft contract validator  
- AI OCR scaffolding  

✅ **Day 19 complete.**

---

### 🚀 Day 20 – AI Cleanup Phase A  
AI Preview + Commit (safe mode)  
In-editor “Finalize with AI Cleanup” fully working.  
Baseline AI cleanup reliable.  
✅ **Day 20 complete.**

---

### 🚀 Day 21 – OCR System Rebuild  
Modular pipeline, engine selector, clean file tree.  
✅ **Day 21 complete.**

---

### 🚀 Day 22 – Phase 2 Wrap-Up  
Editor polish, AI cleanup loop, exports unified, outline rebuilt.  
✅ **Day 22 complete.**

---

### 🚀 Day 23 – Phase 3 pts.1–2  
Rotation preview, status poller, AI finalize redirect, stable end-to-end flow.  
✅ **Day 23 complete.**

---

### 🚀 Day 24 – Phase 3 pt.3–4  
Category inference + two-column merge + variants.  
Significant accuracy jump.  
✅ **Day 24 complete.**

---

### 🚀 Day 25 – Phase 3 Final (pts.5–7)  
- Confidence heat-map  
- Safe AI cleanup  
- Text shaping  
- Item quality scoring  

⭐ **Phase 3 complete.**

---

## 🚀 Day 26 – Phase 4 pts.1–2  
**Raw → Cleanup → Refine** pipeline introduced.  
Safe normalization, light refinement, confidence blending.  
Foundation for Semantic OCR.  
✅ **Day 26 complete.**

---

## 🚀 Day 27 – Phase 4 pts.3–4 (TODAY)

### ✔ Phase 4 pt.3 — Semantic Block Understanding  
- Added `semantic_engine.py`  
- Groups OCR lines into semantic roles: headings, items, descriptions, prices, variants  
- Geometry-aware block clustering  
- Stable across multiple menus

### ✔ Phase 4 pt.4 — Multi-Line Description Reconstruction  
- Auto-merges wrapped description lines  
- Removes bullet symbols, OCR noise, stray hyphens  
- Reconstructs ingredient-style descriptions cleanly  
- Runs before variant inference  
- Verified end-to-end in Finalize with AI Cleanup flow

⭐ **Day 27 complete — semantic structuring + description reconstruction now operational.**

---

# 🌄 Phase 4 – Remaining Roadmap

### Day 28 – Variant & Size Intelligence  
S/M/L, 10/14/18", flavor sets, wing counts, upcharges.

### Day 29 – Category Hierarchy v2  
Smart semantic category grouping.

### Day 30 – Price Integrity Engine  
Price clustering + correction + validation.

### Day 31 – Structured Draft Output  
Full category → item → variants structure.

### Day 32 – Superimport Mode  
Upload → structured menu → POS-ready output.

---

# ⭐ Next Steps  
You will start **Day 28 – Phase 4 pt.5: Variant Intelligence**  
when you say:

> **ready for day 28**
