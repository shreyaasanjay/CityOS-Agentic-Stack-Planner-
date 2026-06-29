param(
  [int]$Port = 8790,
  [switch]$Open
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$StateDir = Join-Path $RepoRoot ".tracefix-ui"
$LogDir = Join-Path $StateDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$DefaultPython = "C:\Users\razer\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if (-not $env:TRACEFIX_PYTHON_EXE -and (Test-Path -LiteralPath $DefaultPython)) {
  $env:TRACEFIX_PYTHON_EXE = $DefaultPython
}

$SynthPython = if ($env:TRACEFIX_PYTHON_EXE) { $env:TRACEFIX_PYTHON_EXE } else { "python" }
if ($SynthPython -ne "python" -and -not (Test-Path -LiteralPath $SynthPython)) {
  throw "Configured TRACEFIX_PYTHON_EXE does not exist: $SynthPython"
}

$OutLog = Join-Path $LogDir "cityos-synth.out.log"
$ErrLog = Join-Path $LogDir "cityos-synth.err.log"
$Process = Start-Process -FilePath $SynthPython `
  -ArgumentList @("-B", "-m", "tracefix.cityos_synth_ui", "--port", "$Port") `
  -WorkingDirectory $RepoRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $OutLog `
  -RedirectStandardError $ErrLog `
  -PassThru

Set-Content -Path (Join-Path $StateDir "cityos-synth.pid") -Value $Process.Id
$Url = "http://127.0.0.1:$Port/"
Write-Host "TraceFix CityOS synthesizer: $Url"
Write-Host "TraceFix UI Python: $SynthPython"
Write-Host "PID: $($Process.Id)"
Write-Host "Logs: $OutLog"
Write-Host "      $ErrLog"

if ($Open) {
  Start-Process $Url
}
