#requires -version 5
$ErrorActionPreference = 'Stop'

# --- Paths ---
$Root  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Portal = Join-Path $Root 'portal'
$Infra  = Join-Path $Root 'infra'
$Venv   = Join-Path $Root '.venv'
$PidsFile = Join-Path $Infra '.pids'
$UrlFile  = Join-Path $Infra 'current_url.txt'

Write-Host "[ServLine] Starting infra..." -ForegroundColor Cyan

# --- Python venv ---
if (!(Test-Path $Venv)) {
  Write-Host "[ServLine] Creating .venv" -ForegroundColor DarkCyan
  python -m venv $Venv
}

. "$Venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip > $null
pip install -r "$Portal/requirements.txt"

# --- Flask app (own window) ---
# Environment for Flask (child window inherits the current env, but we set explicit vars inside the child too)
$env:FLASK_APP  = 'portal.app:app'
$env:FLASK_RUN_PORT = '5000'
$env:FLASK_ENV  = 'development'

# Build a child PowerShell command that sets env vars, then runs Flask using the venv's Python.
# -NoExit keeps the new window open so you can see logs and errors.
$flaskCmd = @"
`$env:FLASK_APP = 'portal.app:app';
`$env:FLASK_RUN_PORT = '5000';
`$env:FLASK_ENV = 'development';
& '$Venv\Scripts\python.exe' -m flask run --host 0.0.0.0 --port 5000 --no-reload
"@

Write-Host "[ServLine] Launching Flask in a new window (port 5000)" -ForegroundColor DarkCyan
$flask = Start-Process -FilePath powershell.exe `
  -ArgumentList @('-NoLogo','-NoExit','-ExecutionPolicy','Bypass','-Command', $flaskCmd) `
  -PassThru -WorkingDirectory $Root -WindowStyle Normal

Start-Sleep -Seconds 1

# --- ngrok ---
if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
  throw "ngrok not found. Install from https://ngrok.com/download and ensure it's on PATH."
}

# Optional: first-time token
if ($env:NGROK_AUTHTOKEN) {
  try { ngrok config add-authtoken $env:NGROK_AUTHTOKEN | Out-Null } catch {}
}

Write-Host "[ServLine] Launching ngrok (http 5000) in a new window" -ForegroundColor DarkCyan
$ngrok = Start-Process -FilePath ngrok -ArgumentList 'http','5000' -PassThru

# --- Save PIDs ---
"FLASK=$($flask.Id)" | Out-File -FilePath $PidsFile -Encoding ascii
"NGROK=$($ngrok.Id)" | Add-Content -Path $PidsFile -Encoding ascii

# --- Wait for public URL via ngrok API ---
$publicUrl = $null
for ($i=0; $i -lt 50; $i++) {
  try {
    $tunnels = Invoke-RestMethod -Uri 'http://127.0.0.1:4040/api/tunnels' -UseBasicParsing -TimeoutSec 2
    foreach ($t in $tunnels.tunnels) {
      if ($t.public_url -like 'https://*') { $publicUrl = $t.public_url }
    }
    if ($publicUrl) { break }
  } catch {}
  Start-Sleep -Milliseconds 250
}

if ($publicUrl) {
  $publicUrl | Out-File -FilePath $UrlFile -Encoding ascii
  Write-Host "[ServLine] Public URL: $publicUrl" -ForegroundColor Green
  Write-Host "[ServLine] Health: $publicUrl/health" -ForegroundColor Green
  Write-Host "[ServLine] Home:   $publicUrl/" -ForegroundColor Green
} else {
  Write-Warning "[ServLine] Could not detect ngrok public URL. Open http://127.0.0.1:4040 to view."
}

Write-Host "[ServLine] Infra is running. Use the VS Code task or run infra/stop_infra.ps1 to stop." -ForegroundColor Cyan

# --- Safe wait so this host stays alive for VS Code's stop button ---
function Wait-OnProc($p) {
  if ($null -eq $p) { return }
  $exists = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
  if ($exists) { Wait-Process -Id $p.Id -ErrorAction SilentlyContinue }
}

# Keep the task alive by waiting on Flask; if Flask already exited, just warn.
if ($flask -and (Get-Process -Id $flask.Id -ErrorAction SilentlyContinue)) {
  Wait-Process -Id $flask.Id -ErrorAction SilentlyContinue
} else {
  Write-Host "[ServLine] Flask process already exited (PID $($flask.Id))." -ForegroundColor Yellow
}
