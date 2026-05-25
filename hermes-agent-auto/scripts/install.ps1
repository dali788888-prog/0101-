$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

$ProjectName = "hermes_agent_auto_isolated"
$ServiceName = "hermes-agent"
$Port = if ($env:HERMES_AGENT_HOST_PORT) { $env:HERMES_AGENT_HOST_PORT } else { "8099" }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "Docker is required. Install Docker Desktop first."
}

try {
  docker compose version | Out-Null
} catch {
  throw "Docker Compose plugin is required. Install Docker Desktop first."
}

if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  Write-Host "Created .env from .env.example. Edit .env to configure search keys and model settings."
}

New-Item -ItemType Directory -Force -Path storage\reports | Out-Null

Write-Host "Starting Hermes Agent with Docker Compose..."
docker compose -p $ProjectName up -d --build $ServiceName

Write-Host ""
Write-Host "Hermes Agent Docker service started."
Write-Host "Web console: http://localhost:$Port"
Write-Host "Health:      http://localhost:$Port/health"
Write-Host "Logs:        docker compose -p $ProjectName logs -f $ServiceName"
Write-Host "Stop:        docker compose -p $ProjectName down"
