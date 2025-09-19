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
  - `GET /` → simple “ServLine Portal online” page
  - `GET /health` → returns `{ "status": "ok" }`
- **Infra**
  - VS Code tasks run `infra/run_infra.ps1` on folder open
  - Ngrok + Flask auto-start, Twilio webhook update

---

## 🚀 Day 2: Restaurants & Menus

- Added tables: `restaurants`, `menus`, `menu_items`
- Portal pages to list restaurants, menus, items
- API endpoints under `/api/...`

---

## 🚀 Day 3: Menu Items + UI

- Added menu item listing
- Added new item form
- Price stored as cents for accuracy

---

## 🚀 Day 4: Git + POS Handshake

- Git version control set up
- Fallback Git saves
- POS order send/handshake scaffolding in place

---

## 🚀 Day 5: Router & Ordering Logic

- Voice router: one-time welcome, gentle reprompts
- Per-shop order logging
- Added `store_id` and POS secret overrides
- Emits upsell accept events (`size_upsell_yes`, `cheese_upsell_yes`)

---

## 🚀 Day 6: Auth System

- Login + Logout (PRG pattern + flash messages)
- Session-based admin mode
- `/login`, `/logout`
- Navbar shows imports/uploads when logged in

---

## 🚀 Day 7: OCR Raw Capture

- OCR ingestion stub added (Tesseract + PDF fallback)
- Captures raw OCR text to `storage/drafts/raw`
- Raw artifacts sweep logic
- Draft JSON scaffolding
- Git fallback saves in place

---

## 🚀 Day 8: Uploads & Recycle Bin

- Uploads page lists files in `/uploads`
- Recycle Bin added (`/uploads/trash`)
- Move-to-bin + restore from bin
- Artifact sweep endpoints
- Secure serving of uploads (blocks `.trash`)

---

## 🚀 Day 9: Draft Review (Editing Flow Prep)

- Draft Review page added (`/drafts/<job_id>`)
- Imports cleanup + per-job delete
- Import job status sync on delete/restore
- JSON preview & draft editor scaffolding
- Error pages added (404, 500)

---

## 🚀 Day 10: Portal Polish

- Unified **button styling** (consistent blue/secondary/danger buttons sitewide)
- Cleaned redundant navigation buttons (kept top banner links authoritative)
- Imports table polished:  
  - “View” button now styled blue (`btn-primary`)  
  - Actions area aligned consistently
- Forms cleaned: dark theme inputs standardized (black text on white background for readability)
- 404 and 500 error pages styled to match site
- Import landing page (`/import`) aligned with portal polish

✅ **Day 10 complete — ServLine Portal is now visually consistent, navigable, and polished.**

---

## 🚀 Day 11: Portal Polish Round 2

- **Global**
  - All buttons unified: blue `btn-primary` for all actions, red `btn-danger` for destructive actions only.
  - Logout styled as compact blue pill (`btn-primary btn-sm`).

- **Restaurants**
  - Added right-aligned “Add Restaurant” button.
  - Clean empty state message.

- **Uploads**
  - Toolbar row with Artifact Sweep, Delete Selected, and file count aligned.
  - Fixed delete bug (no nested forms; button disabled until a file is checked).
  - Empty state muted: “No files in Uploads.”

- **Recycle Bin**
  - Toolbar row with Artifact Sweep + item count aligned.
  - Empty state muted: “Recycle Bin is empty.”

- **Imports**
  - Toolbar row with Cleanup + Recycle Bin buttons.
  - Recycle Bin now primary button.
  - Empty state muted.

- **Import a Menu**
  - Added spacing under OCR Health button.
  - Upload cards balanced and styled consistently.

- **Index (Home)**
  - Headline cleaned up: bold “ServLine Portal” with muted “System is online.” subtitle.
  - Admin & Maintenance block spaced neatly; all buttons now blue primary.

✅ **Day 11 complete — ServLine Portal is now fully consistent, polished, and debugged (Uploads delete fixed).**

---

## 🚀 Day 12: Drafts (DB-backed Editor)

- **Database**
  - Added `drafts` + `draft_items` tables in schema.sql.
  - Migration ensured `import_jobs.draft_id` and `drafts.source_job_id`.

- **Storage Layer**
  - New `storage/drafts.py` for managing drafts and draft items:
    - `list_drafts`, `get_draft`, `get_draft_items`
    - `create_draft`, `save_draft_metadata`, `upsert_draft_items`
    - `delete_draft_items`, `submit_draft`

- **App Integration**
  - `/drafts` → list drafts with filter options.
  - `/drafts/<id>/edit` → full-featured Draft Editor.
  - `/drafts/<id>/save` → JSON API for saving title + items.
  - `/drafts/<id>/submit` → mark draft as submitted.
  - Import jobs now bridge into drafts via **Edit** button.

- **UI**
  - New `draft_editor.html` page:
    - Search, add, duplicate, delete rows.
    - Auto price formatting + validation.
    - Unsaved change indicator + save/submit keyboard shortcuts.
  - Darker background + black text inputs for readability.

✅ **Day 12 complete — ServLine now supports full DB-backed draft editing (menus editable in portal).**

---

## 🚀 Day 13: OCR Online + Imports → Drafts → Approve

- **OCR fully wired**
  - Images (PNG/JPG): Tesseract.
  - PDFs: `pdf2image` + Poppler → Tesseract per page.
  - Health endpoint: `GET /ocr/health` shows detected paths/versions.
  - Raw OCR dumps saved under `storage/drafts/raw/` for debugging.

- **Import Detail page upgrades (`/imports/<job_id>`)**
  - **Open Draft Editor**: first open migrates legacy JSON → DB draft and links it to the import.
  - **Assign Restaurant** (admin): dropdown + `POST /imports/<job_id>/set_restaurant`.
  - **Approve Draft**: `POST /imports/<job_id>/approve` inserts items into the restaurant’s **active menu** (or creates one). Simple dedupe on `(name, price_cents)`.
  - **Discard Draft**: `POST /imports/<job_id>/discard` removes current draft items (keeps the draft shell).
  - **Exports**: CSV / JSON / XLSX links auto-resolve to the DB-backed draft.

- **Admin vs Customer scoping**
  - **Customer** users are automatically scoped to their own `restaurant_id` for imports/approval.
  - **Admin** users can select the restaurant at upload time or on the import page.  
    If missing during approval, user gets: _“No restaurant selected. Choose a restaurant for this import before approving.”_

- **Endpoints recap**
  - Open Editor: `GET /imports/<job_id>/draft` → redirects to `/drafts/<draft_id>/edit`
  - Set Restaurant: `POST /imports/<job_id>/set_restaurant`
  - Approve: `POST /imports/<job_id>/approve`
  - Discard: `POST /imports/<job_id>/discard`
  - Raw OCR JSON: `GET /imports/raw/<job_id>`

- **Troubleshooting**
  - If you see `sqlite3.OperationalError: no such column: source_job_id`, run:
    ```bash
    python migrate_drafts.py
    ```
  - If PDF OCR returns empty: ensure `POPPLER_PATH` points to the Poppler **bin** folder.
  - If Tesseract isn’t detected on Windows: set `TESSERACT_CMD` in `.env` to the full `tesseract.exe` path.

✅ **Day 13 complete — End-to-end intake is live: upload → OCR → DB-backed draft → approve to live menu with restaurant scoping and exports.**

---

## 🚀 Day 14: Draft Editor Revamp + Smarter OCR

- **Draft Editor functional**
  - Fixed broken routes (`draft_publish_now` vs `draft_publish`).
  - Safe rendering wrapper prevents infinite 500 loops when a template is missing or broken.
  - Added debug utilities (`/__ping`, `/__routes`, `/__boom`, `/__debug/...`).

- **OCR pipeline upgraded**
  - Uses `storage/ocr_helper.extract_items_from_path` if available.
  - Fallback regex parser handles categories, names, descriptions, trailing prices.
  - Normalizes parsed results into JSON drafts: categories → items → sizes.
  - Stray text lines are attached as descriptions to previous items.
  - Output is much cleaner than raw Tesseract dumps.

- **Exports**
  - CSV, JSON, XLSX (via `openpyxl`) working from Draft Editor and Import Detail.
  - “Export Visible as CSV” (client-side) for filtered tables.

✅ **Day 14 complete — OCR now produces structured menu data, and the Draft Editor is stable and ready for polish.**
