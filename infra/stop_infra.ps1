#requires -version 5
$ErrorActionPreference = 'SilentlyContinue'

$Infra = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidsFile = Join-Path $Infra '.pids'

if (!(Test-Path $PidsFile)) {
  Write-Host "No pid file found (infra/.pids). Nothing to stop."
  exit 0
}

$lines = Get-Content $PidsFile | Where-Object { $_ -match '=' }
$map = @{}
foreach ($l in $lines) {
  $k,$v = $l -split '='
  $map[$k] = [int]$v
}

foreach ($name in @('FLASK','NGROK')) {
  if ($map[$name]) {
    try { Stop-Process -Id $map[$name] -Force } catch {}
  }
}

Remove-Item $PidsFile -Force -ErrorAction SilentlyContinue
Write-Host "[ServLine] Stopped Flask and ngrok."
