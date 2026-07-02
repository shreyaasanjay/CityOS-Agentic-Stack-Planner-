param(
  [int]$Port = 8798,
  [switch]$Open
)

# This launcher inherits OPENAI_API_KEY / TELLME_API_KEY and TELLME_MODEL from
# the calling PowerShell process. Do not place real credentials in this file.

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$StateDir = Join-Path $RepoRoot ".tracefix-ui"
$LogDir = Join-Path $StateDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# ---- Kill any stale server so the new process binds to a clean port --------
#
# Two-pass kill: stored PID first (fast path), then port scan (catches cases
# where the PID file is stale, missing, or the process outlived the PID file).
# A running server holds Python modules in memory from startup — editing .py
# files has no effect until the process is restarted.

$PidFile = Join-Path $StateDir "cityos-synth.pid"

# Pass 1: kill by stored PID
if (Test-Path $PidFile) {
    $OldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($OldPid) {
        Stop-Process -Id ([int]$OldPid) -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped stored PID $OldPid"
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

# Pass 2: belt-and-suspenders — kill any process still bound to the port
$Listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Listening) {
    foreach ($conn in $Listening) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        Write-Host "Cleared stale process $($conn.OwningProcess) on port $Port"
    }
    Start-Sleep -Milliseconds 700
}

# Verify port is now free before starting
$StillListening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($StillListening) {
    throw "Port $Port is still in use after kill attempts. Manual intervention required."
}

$DefaultPython = "C:\Users\razer\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if (-not $env:TRACEFIX_PYTHON_EXE -and (Test-Path -LiteralPath $DefaultPython)) {
  $env:TRACEFIX_PYTHON_EXE = $DefaultPython
}

$SynthPython = if ($env:TRACEFIX_PYTHON_EXE) { $env:TRACEFIX_PYTHON_EXE } else { "python" }
if ($SynthPython -ne "python" -and -not (Test-Path -LiteralPath $SynthPython)) {
  throw "Configured TRACEFIX_PYTHON_EXE does not exist: $SynthPython"
}

$OutLog = Join-Path $LogDir "unified-ui.out.log"
$ErrLog = Join-Path $LogDir "unified-ui.err.log"
$Process = Start-Process -FilePath $SynthPython `
  -ArgumentList @("-B", "-m", "tracefix.runner_ui", "--port", "$Port") `
  -WorkingDirectory $RepoRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $OutLog `
  -RedirectStandardError $ErrLog `
  -PassThru

Set-Content -Path (Join-Path $StateDir "cityos-synth.pid") -Value $Process.Id
$Url = "http://127.0.0.1:$Port/"
Write-Host "TeLLMe + TraceFix + CityOS Synthesizer: $Url"
Write-Host "TraceFix UI Python: $SynthPython"
Write-Host "TraceFix RepoRoot  : $RepoRoot"
Write-Host "PID: $($Process.Id)"
Write-Host "Logs: $OutLog"
Write-Host "      $ErrLog"

# Wait briefly and confirm the new process bound to the port
Start-Sleep -Milliseconds 1200
$Bound = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Bound) {
    Write-Host "Server confirmed listening on port $Port (PID $($Bound.OwningProcess))" -ForegroundColor Green
    Write-Host "Startup diagnostics in: $OutLog"
} else {
    Write-Warning "Server did not bind to port $Port within 1.2s. Check $ErrLog for errors."
    if (Test-Path $ErrLog) {
        Write-Host "--- last 20 lines of stderr ---"
        Get-Content $ErrLog -Tail 20
    }
}

if ($Open) {
  Start-Process $Url
}
