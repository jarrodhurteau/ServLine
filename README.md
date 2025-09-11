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

---

✅ **Day 10 complete — ServLine Portal is now visually consistent, navigable, and polished.**
