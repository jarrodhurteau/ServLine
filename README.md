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