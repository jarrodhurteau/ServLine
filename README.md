# ServLine 
The ServLine project is a portal + API + AI brain system for restaurant call handling.  
This repo follows a phased build plan (Day 1 → onward), with Git commits marking each milestone. 

--- 

## 📁 Folder Structure 

servline/  
portal/ # Flask portal website  
  app.py  
  requirements.txt  
  templates/  
    index.html  
    base.html  
    login.html  
    restaurants.html  
    menus.html  
    items.html  
    item_form.html  
    imports.html  
    import_view.html  
    uploads.html  
    uploads_trash.html  
    import.html  
    draft_review.html  
    draft_editor.html  
    drafts.html  
    raw.html  
    errors/404.html  
    errors/500.html  
infra/ # Infra scripts (ngrok, Flask runner, stop scripts)  
  run_infra.ps1  
  stop_infra.ps1  
storage/ # SQLite database + seed + schema + artifacts  
  servline.db  
  schema.sql  
  seed_dev.sql  
  drafts.py  
uploads/ # User-uploaded menu files (+ .trash for recycle bin)  
.gitignore  
.vscode/ # VS Code tasks (auto-run infra, stop infra)  
README.md # This file 

--- 

## 🚀 Day 1: Portal Skeleton 

- **Endpoints**  
  - GET / → simple “ServLine Portal online” page  
  - GET /health → returns { "status": "ok" }  
- **Infra**  
  - VS Code tasks run infra/run_infra.ps1 on folder open  
  - Ngrok + Flask auto-start, Twilio webhook update  

✅ **Day 1 complete — project skeleton live.** 

--- 

## 🚀 Day 2: Restaurants & Menus 

- Added tables: restaurants, menus, menu_items  
- Portal pages to list restaurants, menus, items  
- API endpoints under /api/...  

✅ **Day 2 complete — restaurants/menus data model + UI in place.** 

--- 

## 🚀 Day 3: Menu Items + UI 

- Added menu item listing  
- Added new item form  
- Price stored as cents for accuracy  

✅ **Day 3 complete — menu items editable via portal.** 

--- 

## 🚀 Day 4: Git + POS Handshake 

- Git version control set up  
- Fallback Git saves  
- POS order send/handshake scaffolding in place  

✅ **Day 4 complete — Git + POS scaffolding done.** 

--- 

## 🚀 Day 5: Router & Ordering Logic 

- Voice router: one-time welcome, gentle reprompts  
- Per-shop order logging  
- Added store_id and POS secret overrides  
- Emits upsell accept events (size_upsell_yes, cheese_upsell_yes)  

✅ **Day 5 complete — call flow router and ordering logic working.** 

--- 

## 🚀 Day 6: Auth System 

- Login + Logout (PRG pattern + flash messages)  
- Session-based admin mode  
- /login, /logout  
- Navbar shows imports/uploads when logged in  

✅ **Day 6 complete — authentication + admin scoping live.** 

--- 

## 🚀 Day 7: OCR Raw Capture 

- OCR ingestion stub added (Tesseract + PDF fallback)  
- Captures raw OCR text to storage/drafts/raw  
- Raw artifacts sweep logic  
- Draft JSON scaffolding  
- Git fallback saves in place  

✅ **Day 7 complete — raw OCR capture pipeline exists.** 

--- 

## 🚀 Day 8: Uploads & Recycle Bin 

- Uploads page lists files in /uploads  
- Recycle Bin added (/uploads/trash)  
- Move-to-bin + restore from bin  
- Artifact sweep endpoints  
- Secure serving of uploads (blocks .trash)  

✅ **Day 8 complete — uploads + recycle bin fully functional.** 

--- 

## 🚀 Day 9: Draft Review (Editing Flow Prep) 

- Draft Review page added (/drafts/<job_id>)  
- Imports cleanup + per-job delete  
- Import job status sync on delete/restore  
- JSON preview & draft editor scaffolding  
- Error pages added (404, 500)  

✅ **Day 9 complete — draft review + cleanup live.** 

--- 

## 🚀 Day 10: Portal Polish 

- Unified **button styling** (consistent blue/secondary/danger buttons sitewide)  
- Cleaned redundant navigation buttons (kept top banner links authoritative)  
- Imports table polished:  
  - “View” button styled blue (btn-primary)  
  - Actions area aligned consistently  
- Forms cleaned: dark theme inputs standardized (black text on white background)  
- 404 and 500 error pages styled to match  
- Import landing page (/import) aligned with portal polish  

✅ **Day 10 complete — portal polished and consistent.** 

--- 

## 🚀 Day 11: Portal Polish Round 2 

- **Global**  
  - All buttons unified: blue btn-primary for actions, red btn-danger for destructive.  
  - Logout styled compact (btn-primary btn-sm).  
- **Restaurants**  
  - Right-aligned “Add Restaurant” button.  
  - Clean empty state message.  
- **Uploads**  
  - Toolbar row with Artifact Sweep, Delete Selected, and file count aligned.  
  - Fixed delete bug (no nested forms; button disabled until a file is checked).  
  - Empty state: “No files in Uploads.”  
- **Recycle Bin**  
  - Toolbar row with Artifact Sweep + item count.  
  - Empty state: “Recycle Bin is empty.”  
- **Imports**  
  - Toolbar row with Cleanup + Recycle Bin buttons.  
  - Recycle Bin now primary button.  
  - Empty state muted.  
- **Import a Menu**  
  - Added spacing under OCR Health button.  
  - Upload cards balanced.  
- **Index (Home)**  
  - Headline cleaned up.  
  - Admin & Maintenance block spaced neatly.  

✅ **Day 11 complete — portal fully consistent, polished, and bug-fixed.** 

--- 

## 🚀 Day 12: Drafts (DB-backed Editor) 

- Added drafts + draft_items tables  
- New storage/drafts.py with helpers  
- /drafts → list drafts  
- /drafts/<id>/edit → full Draft Editor  
- /drafts/<id>/save → save title/items  
- /drafts/<id>/submit → submit draft  
- Import jobs bridge into drafts  
- Draft Editor UI:  
  - Search, add, duplicate, delete rows  
  - Auto price formatting  
  - Unsaved change indicator  
  - Darker background + readable text  

✅ **Day 12 complete — DB-backed draft editing live.** 

--- 

## 🚀 Day 13: OCR Online + Imports → Drafts → Approve 

- OCR fully wired:  
  - Images: Tesseract  
  - PDFs: pdf2image + Poppler → Tesseract  
- Health endpoint (/ocr/health) shows paths/versions  
- Raw OCR dumps saved  
- Import Detail upgrades:  
  - Open Draft Editor migrates JSON → DB draft  
  - Assign Restaurant dropdown  
  - Approve Draft → inserts items into active menu  
  - Discard Draft → removes draft items  
  - Exports (CSV/JSON/XLSX) from DB draft  
- Scoping:  
  - Customers limited to their restaurant  
  - Admins can assign restaurant  

✅ **Day 13 complete — end-to-end intake live: upload → OCR → draft → approve.** 

--- 

## 🚀 Day 14: Draft Editor Revamp + Smarter OCR 

- Fixed broken routes  
- Safe rendering wrapper for missing templates  
- Added debug utilities (/__ping, /__routes, etc.)  
- OCR pipeline upgraded:  
  - Uses storage/ocr_helper if available  
  - Regex fallback parser for categories/items  
  - Stray lines → description attachment  
  - Cleaner normalized JSON output  
- Exports (CSV, JSON, XLSX) working  
- “Export Visible as CSV” in Draft Editor  

✅ **Day 14 complete — smarter OCR + stable Draft Editor.** 

--- 

## 🚀 Day 15: Failed App Split Attempt 

- Attempted to modularize portal/app.py into multiple files  
- Caused routing/template import errors  
- Repeated fixes failed → full rollback to Day 14 baseline  
- Lesson: split must be done with more care/tests later  

❌ **Day 15 failed — reset to Day 14.** 

--- 

## 🚀 Day 16: Infra & PDF OCR Success 

- **Infra scripts**  
  - run_infra.ps1 launches Flask + ngrok in separate windows  
  - stop_infra.ps1 reliably kills Flask/ngrok  
  - PIDs stored in .pids  
- **OCR stack**  
  - Tesseract v5.5.0 detected  
  - Poppler installed, .env configured  
  - /ocr/health shows both present  
  - PDF OCR pipeline (pdf2image + Poppler → Tesseract) functional  
- **Draft Editor integration**  
  - Importing PDF now generates draft with parsed items  
  - Messy but functional → baseline success  

✅ **Day 16 complete — infra stable and OCR processes PDFs end-to-end.** 

--- 

## 🚀 Day 17: OCR Helper Deep Fixes 

- storage/ocr_helper.py upgraded (v12):  
  - Column-aware category reassignment (nearest header beats semantic guess)  
  - Smarter stub+price stitching (bullets + next-line price merged)  
  - Heading parsing tuned (ALL-CAPS & title-case with menu keywords)  
  - Soda Can normalization + flavor merge (folds junk bullets like “- - Coke,”)  
  - Duplicate “( (6 pcs)” fixed → carried once from raw  
  - Caesar/desc noise cleanup (strips “- -” and dangling punctuation)  
  - Ingredient-price stitch: “tomato, basil - 12.99” merges into prior pizza bullet  
- Debug JSON enriched: version: 12, assignment logs for merges/reassigns  

✅ **Day 17 complete — OCR helper hardened, prices and categories cleaner.** 

--- 

## 🚀 Day 18: Stability, OCR Env, and Exports

**Commit ladder**
- **(1)** OCR environment setup — Installed & wired **Tesseract** and **Poppler**; `/ocr/health` shows both present.  
  - Guarded optional deps on this box via environment markers:  
    - `pandas==2.2.2; platform_machine == "AMD64" and python_version < "3.13"`  
    - `scikit-learn==1.5.1; platform_machine == "AMD64" and python_version < "3.13"`  
    - `symspellpy==6.7.8; platform_machine == "AMD64" and python_version < "3.13"`
- **(2)** Fixed 500s on **/restaurants**, **/import**, **/imports** by reinitializing SQLite schema (tables present, zero rows baseline).  
- **(3)** Draft Editor verified opens and functions for both **JPG** and **PDF** imports.  
- **(4)** Exports verified end-to-end:  
  - From **Import View** and **Draft Editor** → **CSV**, **JSON**, and **Excel (.xlsx)** all download correctly.  
- **(5)** Dev UX: Added VS Code tasks with command echoing:  
  - `Run Infra` (prints public ngrok URL + health)  
  - `Stop Infra`

✅ **Day 18 complete — end-to-end intake stable on this machine (OCR + pages + Draft Editor + exports).**

---

## 🔭 Roadmap: AI-Assisted Menu Extraction (Design)

- Keep regex/heuristics as a safe baseline.  
- Add AI cleanup/recovery layer (e.g., GPT) for missing prices & structure normalization.  
- Two-brain design later:  
  - Brain A: Menu extraction & structuring  
  - Brain B: Conversational ordering  
- Config switch to toggle AI cleanup layer.

