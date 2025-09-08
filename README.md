
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

## 🚀 Day 7: Draft Flow Setup

- Portal routes for `/drafts`, `/drafts/<id>`, `/drafts/<id>/publish`
- Draft JSONs reviewable in the portal
- Publish → inserts new menu & items into DB
- OCR health endpoint (`/ocr/health`)
- Raw OCR viewer (`/drafts/<id>/raw`)

---

## 🚀 Day 8: Upload → Import Jobs → Draft JSON

- **DB**
  - New `import_jobs` table to track uploads & OCR pipeline
  - Fields: `id`, `restaurant_id`, `filename`, `status`, `draft_path`, `error`, timestamps
- **Portal**
  - New pages:
    - `/import` → upload menu (image or PDF)
    - `/imports` → track import jobs
  - Navbar updated with **Import / Imports / Drafts**
- **Pipeline**
  - Upload saved to `/uploads/`
  - `import_jobs` row created
  - Background OCR worker runs (stub for now)
  - Draft JSON written to `storage/drafts/`
- **Verification**
  - Jobs progress `pending → processing → done`
  - Drafts visible on `/drafts/<id>` and can be published into real menu/items
- **Git**
  - `.gitignore` updated to exclude `/uploads/`, `/storage/drafts/`, and `servline.db`

---

## ✅ Next Steps

- Day 9: Draft Editor UI for cleaning up OCR output  
- Day 10: Real OCR v1 (Tesseract for images, pdfplumber for PDFs)  
- Day 11+: Improve heuristics, categories, sizes, etc.

---
