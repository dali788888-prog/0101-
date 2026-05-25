$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "Docker is required. Install Docker Desktop first."
}

if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
}

New-Item -ItemType Directory -Force -Path storage\reports | Out-Null

docker compose up -d --build

Write-Host "Hermes Agent is starting at http://localhost:8099"
Write-Host "Health: http://localhost:8099/health"
