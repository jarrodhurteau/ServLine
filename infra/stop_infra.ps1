#requires -version 5
$ErrorActionPreference = 'SilentlyContinue'

$Infra    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root     = Split-Path -Parent $Infra
$Venv     = Join-Path $Root '.venv'
$PidsFile = Join-Path $Infra '.pids'
$UrlFile  = Join-Path $Infra 'current_url.txt'

Write-Host "[ServLine] Stopping infra..." -ForegroundColor Cyan

# Stop processes from PID file
if (Test-Path $PidsFile) {
  $lines = Get-Content $PidsFile | Where-Object { $_ -match '=' }
  foreach ($l in $lines) {
    $parts = $l -split '='
    if ($parts.Length -eq 2) {
      $name = $parts[0]; $pid = [int]$parts[1]
      try {
        Write-Host "[ServLine] Stopping $name (PID $pid)..."
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
      } catch {}
    }
  }
  Remove-Item $PidsFile -Force -ErrorAction SilentlyContinue
}

# Kill common leftovers just in case
Get-Process -Name "python","flask","ngrok" -ErrorAction SilentlyContinue | ForEach-Object {
  try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch {}
}

# Clear current URL file
Remove-Item $UrlFile -Force -ErrorAction SilentlyContinue

# Deactivate venv if active (harmless if not)
$act = Join-Path $Venv "Scripts\deactivate.ps1"
if (Test-Path $act) { . $act }

Write-Host "[ServLine] Infra stopped." -ForegroundColor Green
