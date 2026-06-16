param(
  [int]$Port = 8788,
  [switch]$Open
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$StateDir = Join-Path $RepoRoot ".tracefix-ui"
$LogDir = Join-Path $StateDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$OutLog = Join-Path $LogDir "runner.out.log"
$ErrLog = Join-Path $LogDir "runner.err.log"
$Process = Start-Process -FilePath python `
  -ArgumentList @("-B", "-m", "tracefix.runner_ui", "--port", "$Port") `
  -WorkingDirectory $RepoRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $OutLog `
  -RedirectStandardError $ErrLog `
  -PassThru

Set-Content -Path (Join-Path $StateDir "runner.pid") -Value $Process.Id
$Url = "http://127.0.0.1:$Port/"
Write-Host "TraceFix LLM runner: $Url"
Write-Host "PID: $($Process.Id)"
Write-Host "Logs: $OutLog"
Write-Host "      $ErrLog"

if ($Open) {
  Start-Process $Url
}
