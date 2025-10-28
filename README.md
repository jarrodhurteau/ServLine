# ServLine  
The ServLine project is a portal + API + AI brain system for restaurant call handling.  
This repo follows a phased build plan (Day 1 → onward), with Git commits marking each milestone.  

---

## 📁 Folder Structure  

servline/  
portal/  # Flask portal website  
  app.py  
  requirements.txt  
  contracts.py                      # lightweight draft schema validator (added Day 19 landmark)  
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
storage/  # SQLite database + seed + schema + artifacts  
  servline.db  
  schema.sql  
  seed_dev.sql  
  drafts.py  
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
✅ **Day 5 complete — call flow router working.**

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
Full OCR integration (Tesseract + Poppler), import→draft→approve workflow, CSV/JSON/XLSX export.  
✅ **Day 13 complete — upload → OCR → draft → approve works end-to-end.**

---

### 🚀 Day 14 – Draft Editor Revamp + Smarter OCR  
Safer rendering, debug utilities, improved OCR parsing (columns, headings, merges, cleanup).  
✅ **Day 14 complete — smarter OCR + stable Draft Editor.**

---

### 🚀 Day 15 – Failed App Split Attempt  
Modularization attempt caused routing issues → rolled back to Day 14.  
❌ **Day 15 failed — reset to Day 14.**

---

### 🚀 Day 16 – Infra & PDF OCR Success  
run_infra/stop_infra scripts with PID tracking, verified Tesseract + Poppler + PDF OCR.  
✅ **Day 16 complete — infra stable + PDF OCR functional.**

---

### 🚀 Day 17 – OCR Helper Deep Fixes  
v12 OCR helper: smarter category/header logic, price/desc merging, duplicate cleanup, enriched debug JSON.  
✅ **Day 17 complete — OCR helper hardened and clean.**

---

### 🚀 Day 18 – Stability, OCR Env & Exports  
Confirmed env paths + deps (Tesseract, Poppler, pandas, scikit-learn).  
Fixed 500s → all pages functional, verified all exports (CSV/JSON/XLSX).  
✅ **Day 18 complete — OCR + Draft Editor + Exports stable.**

---

### 🚀 Day 19 – UX / UI Alignment + OCR Precision  
- Draft Editor UX polish (auto-wrapping textareas + color-coded category chips).  
- OCR preprocessing tuned (CLAHE, denoise, unsharp, psm config, spell fixer).  
- Template/UI alignment for **Imports**, **Import Detail**, **Drafts**, **Uploads**, and **Recycle Bin**.  
- `_safe_render` helper added to prevent template-caused 500 loops.  
- Live render verified via template debug traces.  
- **Contract Validator added** (`portal/contracts.py`) for draft save/export schema consistency.  
- **AI OCR flag scaffolded** (`AI_OCR_ENABLED=false`) for next-phase integration.  

✅ **Day 19 complete — UX/UI unified, OCR refined, and API contract frozen.**  
**Tags:** `day-19-ux`, `v19-landmark` — checkpoint before AI OCR phase.

---

## 🔜 Next Up — Day 20: AI-Assisted Menu Refinement  
- Integrate AI-based categorization + cleanup layer.  
- Smarter auto-grouping for pizzas, burgers, wings, sides etc.  
- Keep regex + heuristics as fallback for precision control.  
- Prep for dual-brain architecture (OCR Brain + Conversation Brain).  
- Maintain frozen contract and feature-flagged integration for safe UI iteration.
