# ServLine

The ServLine project is a portal + API + AI brain system for restaurant call handling.  
This repo follows a phased build plan (Day 1 → onward), with Git commits marking each milestone.

---

## 📁 Folder Structure

servline/  
├── portal/ # Flask portal website  
│   ├── app.py  
│   ├── requirements.txt  
│   └── templates/  
├── infra/ # Infra scripts (ngrok, Flask runner, stop scripts)  
│   ├── run_infra.ps1  
│   └── stop_infra.ps1  
├── storage/ # DB + schema + drafts  
│   ├── schema.sql  
│   ├── init_db.py  
│   ├── seed_dev.sql  
│   ├── servline.db  
│   └── drafts/  
├── uploads/ # User-uploaded menu files (+ .trash bin)  
└── README.md

---

## 🚀 Day 1: Portal Skeleton
- Endpoints: `/`, `/health`
- Infra: ngrok + Flask auto-start, Twilio webhook update

## 🚀 Day 2: Restaurants & Menus
- Tables: `restaurants`, `menus`, `menu_items`
- Portal pages: list restaurants, menus, items
- API endpoints under `/api/...`

## 🚀 Day 3: Menu Items + UI
- Menu item listing page
- New item form
- Prices stored as cents for accuracy

## 🚀 Day 4: Git + POS Handshake
- Git version control + fallback saves
- POS order send/handshake scaffolding

## 🚀 Day 5: Router Polish
- Once-only welcome
- Gentle reprompts
- Per-shop POS logging & upsell events

## 🚀 Day 6: Auth
- Login/logout routes
- Session-based protection for portal pages

## 🚀 Day 7: Uploads + OCR Stub
- Uploads folder wired
- OCR health endpoint
- Import flow stubbed (image/PDF → draft JSON)

## 🚀 Day 8: Uploads Recycle Bin + Imports Cleanup
- **Recycle Bin**: soft-delete uploads into `/uploads/.trash`
- **Restore**: bring trashed files back into `/uploads`
- **Empty Bin**: permanently delete all trashed uploads/artifacts
- **Artifact Sweep**: sweep raw + draft junk into proper `.trash`
- **Imports Cleanup**: per-job delete + orphan cleanup
- **Secure Serving**: block `.trash` from direct access
- E2E tested: Upload → Delete → Recycle Bin → Restore/Empty

---

✅ **Day 8 complete** — system stable with recycle bin & cleanup flows.  
🔜 **Day 9 (Polish)** — UI/UX tweaks (contrast, navbar, smoother flows, auto-refresh, notices).  
