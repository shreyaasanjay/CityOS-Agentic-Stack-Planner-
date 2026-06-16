param(
  [switch]$Open
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

& (Join-Path $ScriptDir "start-viewer.ps1") -Port 8787
& (Join-Path $ScriptDir "start-runner.ps1") -Port 8788

Write-Host ""
Write-Host "Viewer: http://127.0.0.1:8787/"
Write-Host "Runner: http://127.0.0.1:8788/"

if ($Open) {
  Start-Process "http://127.0.0.1:8787/"
  Start-Process "http://127.0.0.1:8788/"
}

