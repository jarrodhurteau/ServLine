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

## 🚀 Day 24 – Phase 3 pt.3–4: Category Infer + Two-Column Merge  
Massive OCR breakthrough: category inference, geometry-based merging, S/M/L and 10/14/18” variants parsed correctly.  
Menu accuracy jumped significantly.

✅ **Day 24 complete.**

---

## 🚀 Day 25 – Phase 3 pt.5–7 (FINAL PHASE 3)

### ✔ **Phase 3 pt.5 — Confidence Heat-Map + Editor Slider**
- Heat-map tinting per row  
- Confidence badges in-name  
- Confidence threshold slider  
- Sidebar outline filtering aware of confidence  

### ✔ **Phase 3 pt.6A — AI Cleanup Safe Mode**
- Finalize-with-AI endpoint  
- Live status pill  
- Auto-refresh  
- Safe normalization across all item fields  

### ✔ **Phase 3 pt.6B — Smarter Text Shaping**
- `_reshape_multi_item_name()`  
- `_smooth_ingredients()`  
- Better comma/ingredient handling  
- Cleaner merged names/descriptions  

### ⭐ **Phase 3 pt.7 — Item Quality Score (FINAL DELIVERABLE)**
Delivered in the Day 25 live session:

#### ✔ Item Quality Scoring (0–100)
Based on:
- OCR confidence  
- Price validity  
- Name length sanity  
- Junk-symbol density  
- Cleanup load  

#### ✔ Quality badges in Draft Editor  
`Quality: 80/100` shown inline  
Green/Yellow/Red tinting  
Always non-destructive  

### ⭐ Result  
**Phase 3 is officially COMPLETE.**  
OCR v2 pipeline is stable, accurate, and production-ready.

Tags:  
`phase-3-complete`  
`phase-3-pt-7-quality-score`

---

# 🌄 **NEXT UP – PHASE 4: Structured OCR (The Big One)**

Phase 4 is where ServLine evolves from “OCR + cleanup” → **true structured understanding**.

This is the phase that makes ServLine *commercial-grade*.

---

## 🚀 **Phase 4 – Structured OCR (Semantic Menu Engine)**

### **🎯 Goal**  
Transform messy PDF/JPG text into **perfectly structured, AI-ready menu JSON**, suitable for:
- Voice ordering  
- POS mapping  
- Auto-category detection  
- Auto-variants  
- Price-logic  
- Large restaurant onboarding at scale  

---

## 🔥 **Phase 4 Core Modules**

### **1. Block→Item Semantic Grouping**  
Smarter than geometry:  
Use AI thinking + OCR metadata to understand:
- What is a menu item  
- What is a description  
- What is a category  
- What is a variant  
- What is a combo  
- What is a size mapping  

This replaces guesswork with **semantic clustering**.

---

### **2. Description Reconstruction Engine**  
Automatically:
- Remove bullet symbols  
- Merge wrapped lines  
- Detect ingredient lists  
- Normalize commas, slashes, & separators  
- Fix unnatural line breaks

---

### **3. AI Variant Deduction**  
Automatic extraction of:
- Size families  
- Flavor sets  
- Sub-variant groups (e.g., “(Grilled/Fried) Chicken”)  
- Combo upgrade logic  
- Wing counts (“6pc / 12pc / 24pc”)  

---

### **4. Category Hierarchy Inference (v2)**  
Category-level grouping powered by:
- Block positions  
- Font weight  
- Geometry  
- Keywords  
- AI semantic reading  

---

### **5. Price Reasoning Engine**  
Price clustering + corrections:
- Detect misread decimals  
- Detect swapped digits  
- Detect outliers  
- Match prices to sizes/variants  

---

### **6. Draft Editor Auto-Grouping Layer**  
Finally tie the structured output into the UI:
- Items auto-bucketed by category  
- Variants grouped under one parent  
- Clean S/M/L + 10/14/18 logic  
- Description clean by default  
- Zero junk  

---

## ⭐ **Phase 4 Result**  
By the end of Phase 4:

**You’ll be able to upload ANY menu and get perfectly structured menu JSON with almost zero manual fixes.**

This is when ServLine becomes **ready for restaurant onboarding and real customer usage**.

---

# 🌟 Current Status  
OCR v2 pipeline completed (Phase 3).  
Draft Editor is powerful and stable.  
We now begin **Phase 4: Structured OCR** — the biggest accuracy jump yet.

