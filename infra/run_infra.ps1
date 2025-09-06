#requires -version 5
$ErrorActionPreference = 'Stop'

# --- Paths ---
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Portal = Join-Path $Root 'portal'
$Infra = Join-Path $Root 'infra'
$Venv = Join-Path $Root '.venv'
$PidsFile = Join-Path $Infra '.pids'
$UrlFile = Join-Path $Infra 'current_url.txt'

Write-Host "[ServLine] Starting infra..." -ForegroundColor Cyan

# --- Python venv ---
if (!(Test-Path $Venv)) {
  Write-Host "[ServLine] Creating .venv" -ForegroundColor DarkCyan
  python -m venv $Venv
}
. "$Venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip > $null
pip install -r "$Portal/requirements.txt"

# --- Flask app ---
$env:FLASK_APP = 'portal.app:app'
$env:FLASK_RUN_PORT = '5000'
$env:FLASK_ENV = 'development'

Write-Host "[ServLine] Launching Flask (port 5000)" -ForegroundColor DarkCyan
$flask = Start-Process -FilePath python -ArgumentList '-m','flask','run','--host','0.0.0.0','--port','5000' -PassThru

Start-Sleep -Seconds 1

# --- ngrok ---
if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
  throw "ngrok not found. Install from https://ngrok.com/download and ensure it's on PATH."
}

# Optional: first-time token
if ($env:NGROK_AUTHTOKEN) {
  try { ngrok config add-authtoken $env:NGROK_AUTHTOKEN | Out-Null } catch {}
}

Write-Host "[ServLine] Launching ngrok (http 5000)" -ForegroundColor DarkCyan
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

# Keep this host process alive so clicking the task's stop button halts children
Wait-Process -Id $flask.Id
