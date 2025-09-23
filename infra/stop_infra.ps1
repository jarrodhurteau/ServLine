#requires -version 5
$ErrorActionPreference = 'SilentlyContinue'

Write-Host "[ServLine] Stopping infra..." -ForegroundColor Cyan

$Infra    = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidsFile = Join-Path $Infra '.pids'

function Get-DescendantProcIds {
  param([int]$RootId)

  $seen = New-Object 'System.Collections.Generic.HashSet[int]'
  $queue = New-Object 'System.Collections.Generic.Queue[int]'
  if ($RootId) { $null = $queue.Enqueue($RootId) }

  $all = @()
  while ($queue.Count -gt 0) {
    $curId = $queue.Dequeue()
    if ($seen.Contains($curId)) { continue }
    $null = $seen.Add($curId)
    $all += $curId
    try {
      $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $curId" |
                  Select-Object -ExpandProperty ProcessId
      foreach ($c in $children) { $null = $queue.Enqueue([int]$c) }
    } catch {}
  }
  return $all
}

function Stop-ByProcTree {
  param([int]$RootId, [string]$label)

  if (-not $RootId) { return }
  $tree = Get-DescendantProcIds -RootId $RootId | Select-Object -Unique
  if (-not $tree -or $tree.Count -eq 0) {
    Write-Host " - $label PID $RootId not running." -ForegroundColor Yellow
    return
  }

  # Kill children before parents
  foreach ($procId in ($tree | Sort-Object -Descending)) {
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc) {
      try {
        Write-Host " - Stopping $label proc PID $procId..." -ForegroundColor DarkCyan
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
      } catch {}
    }
  }
}

# 1) Read PID file and stop both by tree
if (Test-Path $PidsFile) {
  $map = @{}
  foreach ($line in (Get-Content $PidsFile | Where-Object { $_ -match '=' })) {
    try {
      $k,$v = $line -split '=', 2
      if ($k -and $v) { $map[$k] = [int]$v }
    } catch {}
  }

  if ($map['FLASK']) { Stop-ByProcTree -RootId $map['FLASK'] -label 'Flask (window)' }
  if ($map['NGROK']) { Stop-ByProcTree -RootId $map['NGROK'] -label 'ngrok' }

  Remove-Item $PidsFile -Force -ErrorAction SilentlyContinue
} else {
  Write-Host "No pid file found (infra/.pids). Trying fallbacks..." -ForegroundColor Yellow
}

# 2) Belt + suspenders: kill any python running "flask run"
try {
  $flaskProcs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
                Where-Object { $_.CommandLine -match 'flask\s+run' }
  foreach ($p in $flaskProcs) {
    Write-Host " - Killing python running 'flask run' (PID $($p.ProcessId))..." -ForegroundColor DarkCyan
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
} catch {}

# 3) Kill anything still listening on :5000
$killedByPort = @()
if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
  try {
    $conns = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
    foreach ($c in ($conns | Select-Object -ExpandProperty OwningProcess -Unique)) {
      if ($c) {
        Write-Host " - Forcing process on port 5000 (PID $c) to stop..." -ForegroundColor DarkCyan
        Stop-Process -Id $c -Force -ErrorAction SilentlyContinue
        $killedByPort += $c
      }
    }
  } catch {}
} else {
  # Fallback: netstat parse (older PS)
  try {
    $lines = netstat -ano | Select-String -Pattern 'LISTENING' | Select-String -Pattern '[:\.]5000\s' -SimpleMatch
    $foundPids = @()
    foreach ($ln in $lines) {
      if ($ln.Line -match '\s+(\d+)\s*$') { $foundPids += [int]$Matches[1] }
    }
    foreach ($p in ($foundPids | Select-Object -Unique)) {
      Write-Host " - Forcing process on port 5000 (PID $p) to stop..." -ForegroundColor DarkCyan
      Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
      $killedByPort += $p
    }
  } catch {}
}

# 4) Extra sweep: orphan ngrok (rare)
try { Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}

Write-Host "[ServLine] Infra stopped." -ForegroundColor Green
