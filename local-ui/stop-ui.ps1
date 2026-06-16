$ErrorActionPreference = "Continue"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$StateDir = Join-Path $RepoRoot ".tracefix-ui"

foreach ($Name in @("viewer", "runner")) {
  $PidFile = Join-Path $StateDir "$Name.pid"
  if (Test-Path $PidFile) {
    $ProcessId = (Get-Content $PidFile -Raw).Trim()
    if ($ProcessId) {
      Stop-Process -Id ([int]$ProcessId) -ErrorAction SilentlyContinue
      Write-Host "Stopped $Name PID $ProcessId"
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
  }
}

