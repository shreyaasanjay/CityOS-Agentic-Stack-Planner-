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

# Kill any stale server that is still bound to the target port so the new
# process can bind successfully and picks up fresh environment variables.
$PidFile = Join-Path $StateDir "cityos-synth.pid"
if (Test-Path $PidFile) {
    $OldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($OldPid) {
        Stop-Process -Id $OldPid -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
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
Write-Host "PID: $($Process.Id)"
Write-Host "Logs: $OutLog"
Write-Host "      $ErrLog"

if ($Open) {
  Start-Process $Url
}
