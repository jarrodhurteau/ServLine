#requires -version 5
$ErrorActionPreference = 'Stop'

# --- Paths ---
$Root  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Portal = Join-Path $Root 'portal'
$Infra  = Join-Path $Root 'infra'
$Venv   = Join-Path $Root '.venv'
$PidsFile = Join-Path $Infra '.pids'

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
$flaskCmd = @"
`$env:PYTHONIOENCODING = 'utf-8';
`$env:FLASK_APP = 'portal.app:app';
`$env:FLASK_RUN_PORT = '5000';
`$env:FLASK_ENV = 'development';
& '$Venv\Scripts\python.exe' portal/app.py
"@

Write-Host "[ServLine] Launching Flask in a new window (port 5000)" -ForegroundColor DarkCyan
$flask = Start-Process -FilePath powershell.exe `
  -ArgumentList @('-NoLogo','-NoExit','-ExecutionPolicy','Bypass','-Command', $flaskCmd) `
  -PassThru -WorkingDirectory $Root -WindowStyle Normal

Start-Sleep -Seconds 1

# --- Save PIDs ---
"FLASK=$($flask.Id)" | Out-File -FilePath $PidsFile -Encoding ascii

Write-Host "[ServLine] Flask running on http://127.0.0.1:5000" -ForegroundColor Green
Write-Host "[ServLine] Use the VS Code task or run infra/stop_infra.ps1 to stop." -ForegroundColor Cyan

# --- Keep task alive by waiting on Flask window ---
if ($flask -and (Get-Process -Id $flask.Id -ErrorAction SilentlyContinue)) {
  Wait-Process -Id $flask.Id -ErrorAction SilentlyContinue
} else {
  Write-Host "[ServLine] Flask process already exited (PID $($flask.Id))." -ForegroundColor Yellow
}
