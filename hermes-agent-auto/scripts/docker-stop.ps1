$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

$ProjectName = "hermes_agent_auto_isolated"

docker compose -p $ProjectName down

Write-Host "Hermes Agent Docker service stopped."
