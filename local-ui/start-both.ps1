param(
  [string]$BindHost = "127.0.0.1",
  [switch]$Lan,
  [int]$StartupTimeoutSeconds = 15,
  [switch]$Open
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
if ($Lan) {
  $BindHost = "0.0.0.0"
}

& (Join-Path $ScriptDir "start-viewer.ps1") -Port 8787 -BindHost $BindHost -StartupTimeoutSeconds $StartupTimeoutSeconds
& (Join-Path $ScriptDir "start-runner.ps1") -Port 8788 -BindHost $BindHost -StartupTimeoutSeconds $StartupTimeoutSeconds
& (Join-Path $ScriptDir "start-synth.ps1") -Port 8790 -BindHost $BindHost -StartupTimeoutSeconds $StartupTimeoutSeconds

$LanAddress = $null
if ($BindHost -eq "0.0.0.0") {
  $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" })
  if ($addresses.Count -gt 0) { $LanAddress = $addresses[0].IPAddress }
}

Write-Host ""
Write-Host "Viewer local: http://127.0.0.1:8787/"
Write-Host "Runner local: http://127.0.0.1:8788/"
Write-Host "Synthesizer local: http://127.0.0.1:8790/"
if ($LanAddress) {
  Write-Host ""
  Write-Host "Viewer LAN: http://${LanAddress}:8787/"
  Write-Host "Runner LAN: http://${LanAddress}:8788/"
  Write-Host "Synthesizer LAN: http://${LanAddress}:8790/"
} elseif ($BindHost -ne "127.0.0.1") {
  Write-Host ""
  Write-Host "Viewer bind: http://${BindHost}:8787/"
  Write-Host "Runner bind: http://${BindHost}:8788/"
  Write-Host "Synthesizer bind: http://${BindHost}:8790/"
}

if ($Open) {
  Start-Process "http://127.0.0.1:8787/"
  Start-Process "http://127.0.0.1:8788/"
  Start-Process "http://127.0.0.1:8790/"
}
