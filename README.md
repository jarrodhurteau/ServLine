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
infra/ # Infra scripts (ngrok, Flask runner, stop scripts)
run_infra.ps1
stop_infra.ps1
.gitignore
.vscode/ # VS Code tasks (auto-run infra, stop infra)
tasks.json
.gitignore # Root ignore rules
README.md # This file

markdown
Copy code

---

## 🚀 Day 1: Portal Skeleton

- **Endpoints**
  - `GET /` → simple “ServLine Portal online” page
  - `GET /health` → returns `{ "status": "ok" }`

- **Infra**
  - `infra/run_infra.ps1` → launches virtual env, installs Flask, runs app + ngrok
  - `infra/stop_infra.ps1` → halts Flask + ngrok processes

- **VS Code Tasks**
  - **ServLine: infra (ngrok + portal)** → auto-runs on folder open
  - **ServLine: stop infra** → stops both processes

---

## ✅ Acceptance Demo (Day 1)

1. Open the repo in **VS Code**.  
   (The infra task should auto-run and launch Flask + ngrok.)
2. Copy the printed **Public URL** from the task output.
3. Visit:
   - `https://<public>/health` → should show `{ "status": "ok" }`
   - `https://<public>/` → should show **ServLine Portal online**
4. Lock into Git:
   ```bash
   git init
   git add .
   git commit -m "Day 1: Portal skeleton + health check online"
ℹ️ Notes
Requires Python 3.11+ installed and available as python in PATH.

Requires ngrok installed and available in PATH.

If running ngrok for the first time, you may need to add your auth token:

powershell
Copy code
ngrok config add-authtoken <YOUR_TOKEN>
Task output also writes the current public URL to infra/current_url.txt.

## 🟢 Day 2: SQLite + DB Health

- **Added SQLite database** at `storage/servline.db`
- **Schema:** `restaurants`, `menus`, `menu_items`
- **Seed data:** one demo restaurant, menu, and 2 items
- **New endpoint:**  
  - `GET /db/health` → returns counts of restaurants, menus, and menu items

**Acceptance Demo (Day 2)**  
1. Run `python storage/init_db.py` to create + seed the DB.  
2. Restart infra (Stop → Start tasks in VS Code).  
3. Open `https://<ngrok>/db/health` → expect:  
   ```json
   {
     "status": "ok",
     "data": {
       "restaurants": 1,
       "menus": 1,
       "menu_items": 2
     }
   }
Lock into Git:

bash
Copy code
git add .
git commit -m "Day 2: SQLite schema + seed + /db/health wired"
✅ Day 2 is complete when the JSON response shows the seeded counts and commit is saved.

---

## 🟠 Day 3: API Endpoints for Restaurants + Menus

- **New API routes:**
  - `GET /api/restaurants` → list all active restaurants
  - `GET /api/restaurants/<id>/menus` → list menus for a restaurant
  - `GET /api/menus/<id>/items` → list items for a menu

**Acceptance Demo (Day 3)**  
1. Restart infra (Stop → Start tasks in VS Code).  
2. Open:
   - `/api/restaurants` → seeded restaurant(s)  
   - `/api/restaurants/1/menus` → seeded menu(s)  
   - `/api/menus/1/items` → seeded menu items  
3. Lock into Git:  
   ```bash
   git add .
   git commit -m "Day 3: API endpoints for restaurants, menus, and menu_items"
✅ Day 3 is complete when all 3 endpoints return JSON as expected and commit is saved.

---

## 🔵 Day 4: Portal UI (Restaurants → Menus → Items) Read-Only

- **New HTML pages (with navigation):**
  - `/restaurants` → list of restaurants
  - `/restaurants/<id>/menus` → menus for a restaurant
  - `/menus/<id>/items` → items in a menu
- Added **Jinja templates**:
  - `base.html` (layout + nav)
  - `restaurants.html`, `menus.html`, `items.html`
- Navigation links and breadcrumbs connect the flow.

**Acceptance Demo (Day 4)**  
1. Restart infra (Stop → Start tasks in VS Code).  
2. Visit:
   - `/restaurants` → seeded restaurant appears  
   - `/restaurants/1/menus` → seeded menu appears  
   - `/menus/1/items` → seeded items appear  
3. All pages link correctly, styled in basic dark theme.  
4. Lock into Git:
   ```bash
   git add .
   git commit -m "Day 4: Portal UI (restaurants → menus → items) read-only"
✅ Day 4 is complete when the restaurant → menus → items flow works in browser and commit is saved.

---

## 🟣 Day 4.5: Tailwind CDN + Base Tokens (No Layout Changes)

- Added **Tailwind CSS (CDN)** to templates.
- Defined base **design tokens** (colors, shadow, radius) in an inline Tailwind config.
- Kept existing CSS to avoid visual shifts; only minimal utility classes added.

**Acceptance Demo (Day 4.5)**
1. Restart infra (Stop → Start tasks in VS Code).
2. Open the homepage and restaurants pages — they should look the **same** (no layout changes).
3. View page source and confirm Tailwind is loaded.
4. We’re now ready to style future components (Hero, Cards, Buttons) without touching backend logic.
✅ Day 4.5 is complete when Tailwind is present in page source and the site still looks unchanged.

---

## 🟢 Day 5: Admin Forms (Add Menu Items)

- Added **form routes**:
  - `GET /menus/<id>/items/new` → show form
  - `POST /menus/<id>/items/new` → save new item
- New template: `item_form.html` with Tailwind-styled inputs + button.
- Items insert into SQLite and appear immediately.

**Acceptance Demo (Day 5)**
1. Restart infra (Stop → Start tasks in VS Code).
2. Visit `/menus/1/items/new`, fill out a form (e.g., “Mozzarella Sticks”).
3. Submit → redirected to `/menus/1/items` and see the new item in the list.
4. Lock into Git:
   ```bash
   git add .
   git commit -m "Day 5: Admin forms (add menu items)"
✅ Day 5 is complete when new items can be added via form and appear in the menu immediately.

---

## 🟤 Day 6: Basic Auth (Login/Logout) + Protected Admin Routes

- Added **login** + **logout** routes with session-based auth (dev-only).
- Protected admin pages (e.g., **Add Item** form) so only logged-in users can access.
- Navbar shows **Login/Logout** based on session state.

**Dev Credentials**
- **Username:** `admin`
- **Password:** `letmein`  
*(Dev-only; to be replaced with real auth later.)*

**Acceptance Demo (Day 6)**
1. Restart infra (Stop → Start tasks in VS Code).
2. Visit `/login`, sign in with `admin / letmein`.
3. Go to `/menus/1/items/new` — form should load (no redirect).
4. **Logout**, then try `/menus/1/items/new` — should redirect to `/login`.
5. Lock into Git:
   ```bash
   git add .
   git commit -m "Day 6: Basic auth (login/logout) + protected admin routes"
✅ Day 6 is complete when the Add Item form is only reachable after login, and logout removes access.

🔴 Day 7: OCR Import + Raw Viewer

Added OCR import flow:

Upload image/PDF via /import

Background worker saves draft JSON + raw OCR text

New drafts list page (/drafts) shows uploaded jobs

New raw OCR viewer:

Route: /drafts/<job_id>/raw

Monospace formatting + Copy All button

Tested with Sam’s Pizza menu:

Verified OCR output stored + viewable

Logged common OCR issues (price misreads, garbled specials, missing descriptions)

Decided to build Draft Editor MVP in Day 8 to handle structured cleanup

Acceptance Demo (Day 7)

Upload menu at /import.

Wait for job status → draft_created.

Visit /drafts → see new job.

Click into /drafts/<job_id>/raw → raw OCR text displayed, copyable.

Compare with source menu → observe OCR quirks.

Lock into Git:

git add .
git commit -m "Day 7: OCR import + raw viewer with Sam's Pizza test"
git tag v0.0.7-day7


✅ Day 7 is complete when raw OCR is viewable in browser and ready for cleanup on Day 8.