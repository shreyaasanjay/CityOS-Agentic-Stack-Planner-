param(
  [int]$Port = 8788,
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

$RunnerPython = if ($env:TRACEFIX_PYTHON_EXE) { $env:TRACEFIX_PYTHON_EXE } else { "python" }
if ($RunnerPython -ne "python" -and -not (Test-Path -LiteralPath $RunnerPython)) {
  throw "Configured TRACEFIX_PYTHON_EXE does not exist: $RunnerPython"
}

$DefaultJava = "C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot\bin\java.exe"
if ((Test-Path -LiteralPath $DefaultJava)) {
  if (-not $env:TLA_VERIFY_JAVA) {
    $env:TLA_VERIFY_JAVA = $DefaultJava
  }
  if (-not $env:JAVA_EXE) {
    $env:JAVA_EXE = $DefaultJava
  }
  if (-not $env:JAVA_HOME) {
    $env:JAVA_HOME = Split-Path -Parent (Split-Path -Parent $DefaultJava)
  }
  $JavaBin = Split-Path -Parent $DefaultJava
  $PathParts = @($env:PATH -split ';' | Where-Object { $_ })
  if ($PathParts -notcontains $JavaBin) {
    $env:PATH = $JavaBin + ';' + $env:PATH
  }
}

$OutLog = Join-Path $LogDir "runner.out.log"
$ErrLog = Join-Path $LogDir "runner.err.log"
$Process = Start-Process -FilePath $RunnerPython `
  -ArgumentList @("-B", "-m", "tracefix.runner_ui", "--port", "$Port") `
  -WorkingDirectory $RepoRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $OutLog `
  -RedirectStandardError $ErrLog `
  -PassThru

Set-Content -Path (Join-Path $StateDir "runner.pid") -Value $Process.Id
$Url = "http://127.0.0.1:$Port/"
Write-Host "TraceFix LLM runner: $Url"
Write-Host "TraceFix UI Python: $RunnerPython"
Write-Host "TraceFix subprocess Python: $($env:TRACEFIX_PYTHON_EXE)"
Write-Host "TraceFix TLA_VERIFY_JAVA: $($env:TLA_VERIFY_JAVA)"
Write-Host "TraceFix JAVA_EXE: $($env:JAVA_EXE)"
Write-Host "TraceFix JAVA_HOME: $($env:JAVA_HOME)"
Write-Host "PID: $($Process.Id)"
Write-Host "Logs: $OutLog"
Write-Host "      $ErrLog"

if ($Open) {
  Start-Process $Url
}
