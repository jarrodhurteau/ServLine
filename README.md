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

## 🚀 Day 5: Admin Forms

- Portal form to add menu items
- Items linked to menus and restaurants
- Validations for price, required fields

---

## 🚀 Day 6: Auth

- Login / Logout added
- Session-based auth for protected routes
- Default dev creds: `admin / letmein`

---

## 🚀 Day 7: Draft Flow + Uploads Recycle Bin

- **Draft Flow Setup**
  - Portal routes for `/drafts`, `/drafts/<id>`, `/drafts/<id>/publish`
  - Draft JSONs reviewable in the portal
  - Publish → inserts new menu & items into DB
  - OCR health endpoint (`/ocr/health`)
  - Raw OCR viewer (`/drafts/<id>/raw`)

- **Uploads Recycle Bin**
  - Deleting from `/uploads` moves files to `uploads/.trash/<timestamp>/FILE`.
  - Restoring returns them to `uploads/` (auto-renames to `NAME (restored N).ext` on conflict).
  - Empty Trash clears `uploads/.trash` and also empties artifact trash bins.

- **Artifact Sweep tied to uploads**
  - On delete, related OCR artifacts are trashed:
    - Draft JSON → `storage/drafts/.trash/<timestamp>/draft_*.json`
    - Raw OCR (new) → `storage/drafts/raw/.trash/<timestamp>/JOBID.txt`
    - Raw OCR (legacy) → `storage/raw/.trash/<timestamp>/...`
  - Orphan draft JSONs whose `source.file` matches the deleted upload are also trashed.

- **Imports list hygiene**
  - `/imports` hides rows with `status='deleted'`.
  - Bulk cleanup: `GET|POST /imports/cleanup` marks jobs as `deleted` if their original upload file is missing.
  - Per-job soft delete: `POST /imports/<job_id>/delete`.

- **Status sync**
  - Deleting an upload → `import_jobs.status = 'deleted'` for that filename.
  - Restoring from trash → `import_jobs.status = 'restored'`.

- **Security/serving**
  - Direct access to anything under `.trash` is blocked by the upload file server.

- **Touched files**
  - `portal/app.py` only (templates unchanged).

---

## ✅ Next Steps

- Day 8: Draft Editor UI for cleaning OCR output  
- Day 9: Real OCR v1 (Tesseract for images, pdfplumber for PDFs)  
- Day 10+: Improve heuristics, categories, sizes, etc.

---
