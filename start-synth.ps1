param(
  [int]$Port = 8798,
  [switch]$Open
)

# Optional TeLLMe LLM mode setup:
#   $env:OPENAI_API_KEY="your_key_here"
#   $env:TELLME_MODEL="gpt-4.1-mini"
#   .\start-synth.ps1
#
# TELLME_API_KEY may be used instead of OPENAI_API_KEY. Keys are inherited from
# this PowerShell process and are never stored in this script.

$ErrorActionPreference = "Stop"
$Launcher = Join-Path $PSScriptRoot "local-ui\start-synth.ps1"

& $Launcher -Port $Port -Open:$Open
