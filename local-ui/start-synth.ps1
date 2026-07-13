param(
  [int]$Port = 8790,
  [string]$BindHost = "127.0.0.1",
  [switch]$Lan,
  [int]$StartupTimeoutSeconds = 15,
  [switch]$Open
)

# This launcher inherits OPENAI_API_KEY / TELLME_API_KEY and TELLME_MODEL from
# the calling PowerShell process. Do not place real credentials in this file.

$ErrorActionPreference = "Stop"
if ($Lan) {
  $BindHost = "0.0.0.0"
}

function Get-LanAddress {
  $virtualPattern = "vEthernet|WSL|Docker|Loopback|Hyper-V|VirtualBox|VMware"
  $configs = @(Get-NetIPConfiguration -ErrorAction SilentlyContinue |
    Where-Object {
      $_.IPv4DefaultGateway -and $_.IPv4Address -and
      ($_.InterfaceAlias -notmatch $virtualPattern) -and
      ($_.InterfaceDescription -notmatch $virtualPattern)
    })
  if ($configs.Count -gt 0) { return @($configs[0].IPv4Address)[0].IPAddress }

  $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
      $_.IPAddress -notlike "127.*" -and
      $_.IPAddress -notlike "169.254.*" -and
      $_.InterfaceAlias -notmatch $virtualPattern
    })
  if ($addresses.Count -gt 0) { return $addresses[0].IPAddress }
  return $null
}

function Stop-StaleServer {
  param(
    [string]$PidFile,
    [int]$Port
  )
  if (Test-Path $PidFile) {
    $OldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($OldPid) {
      $OldPidInt = [int]$OldPid
      $OldPidOnPort = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -eq $OldPidInt }
      if ($OldPidOnPort) {
        Stop-Process -Id $OldPidInt -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped stored PID $OldPid"
      } else {
        Write-Host "Stored PID $OldPid was not listening on port $Port"
      }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
  }

  $Listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($Listening) {
    foreach ($conn in $Listening) {
      Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
      Write-Host "Cleared stale process $($conn.OwningProcess) on port $Port"
    }
    Start-Sleep -Milliseconds 700
  }

  $StillListening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($StillListening) {
    throw "Port $Port is still in use after kill attempts. Manual intervention required."
  }
}

function Wait-ServerBind {
  param(
    [System.Diagnostics.Process]$Process,
    [int]$Port,
    [int]$TimeoutSeconds,
    [string]$ErrLog
  )
  $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $Bound = $null
  do {
    $Bound = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($Bound) { break }
    $Process.Refresh()
    if ($Process.HasExited) { break }
    Start-Sleep -Milliseconds 300
  } while ((Get-Date) -lt $Deadline)

  if ($Bound) {
    $Owner = @($Bound)[0].OwningProcess
    Write-Host "Server confirmed listening on port $Port (PID $Owner)" -ForegroundColor Green
  } else {
    Write-Warning "Server did not bind to port $Port within ${TimeoutSeconds}s. Check $ErrLog for errors."
    if (Test-Path $ErrLog) {
      Write-Host "--- last 20 lines of stderr ---"
      Get-Content $ErrLog -Tail 20
    }
  }
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$StateDir = Join-Path $RepoRoot ".tracefix-ui"
$LogDir = Join-Path $StateDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$PidFile = Join-Path $StateDir "cityos-synth.pid"
Stop-StaleServer -PidFile $PidFile -Port $Port

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
  -ArgumentList @("-B", "-m", "tracefix.runner_ui", "--host", "$BindHost", "--port", "$Port") `
  -WorkingDirectory $RepoRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $OutLog `
  -RedirectStandardError $ErrLog `
  -PassThru

Set-Content -Path $PidFile -Value $Process.Id
$LocalUrl = "http://127.0.0.1:$Port/"
$LaunchUrl = if ($BindHost -eq "0.0.0.0") { $LocalUrl } else { "http://${BindHost}:$Port/" }
$LanAddress = if ($BindHost -eq "0.0.0.0") { Get-LanAddress } else { $null }
Write-Host "TeLLMe + TraceFix + CityOS Synthesizer local: $LocalUrl"
if ($LanAddress) { Write-Host "TeLLMe + TraceFix + CityOS Synthesizer LAN  : http://${LanAddress}:$Port/" }
elseif ($BindHost -ne "127.0.0.1") { Write-Host "TeLLMe + TraceFix + CityOS Synthesizer bind : http://${BindHost}:$Port/" }
Write-Host "TraceFix bind host: $BindHost"
Write-Host "TraceFix UI Python: $SynthPython"
Write-Host "TraceFix RepoRoot  : $RepoRoot"
Write-Host "PID: $($Process.Id)"
Write-Host "Logs: $OutLog"
Write-Host "      $ErrLog"

Wait-ServerBind -Process $Process -Port $Port -TimeoutSeconds $StartupTimeoutSeconds -ErrLog $ErrLog

if ($Open) {
  Start-Process $LaunchUrl
}
