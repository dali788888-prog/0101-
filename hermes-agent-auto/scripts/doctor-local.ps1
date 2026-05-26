$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

function Read-EnvValue {
  param([string]$Name)
  if (-not (Test-Path ".env")) { return "" }
  $line = Get-Content ".env" | Where-Object { $_ -like "$Name=*" } | Select-Object -Last 1
  if (-not $line) { return "" }
  return ($line.Substring($Name.Length + 1)).Trim()
}

$Port = Read-EnvValue "HERMES_AGENT_HOST_PORT"
if ([string]::IsNullOrWhiteSpace($Port)) { $Port = "8099" }
$BaseUrl = "http://127.0.0.1:" + $Port
$ProjectName = "hermes_agent_auto_isolated"
$ServiceName = "hermes-agent"
$ApiKey = Read-EnvValue "HERMES_AGENT_API_KEY"

Write-Host "== Hermes Agent Local Doctor =="
Write-Host ("Base URL: " + $BaseUrl)
Write-Host ("Project:  " + $ProjectName)

Write-Host ""
Write-Host "[1/6] Docker status"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "Docker command not found. Install Docker Desktop first."
}
docker --version

Write-Host ""
Write-Host "[2/6] Docker Compose status"
docker compose version

Write-Host ""
Write-Host "[3/6] Container status"
docker compose -p $ProjectName ps

Write-Host ""
Write-Host "[4/6] Health endpoint"
$HealthUri = $BaseUrl + "/health"
$Health = Invoke-RestMethod -Uri $HealthUri -Method GET -TimeoutSec 10
$Health | ConvertTo-Json -Depth 5
if ($Health.status -ne "ok") {
  throw "Health endpoint returned non-ok status."
}
Write-Host "PASS: Hermes Agent service is reachable."

Write-Host ""
Write-Host "[5/6] Search configuration"
$Provider = Read-EnvValue "SEARCH_PROVIDER"
if ([string]::IsNullOrWhiteSpace($Provider)) { $Provider = $Health.search_provider }
if ([string]::IsNullOrWhiteSpace($Provider)) { $Provider = "none" }
Write-Host ("SEARCH_PROVIDER=" + $Provider)

switch ($Provider) {
  "none" {
    Write-Host "WARN: Live web search is disabled. Set SEARCH_PROVIDER=brave/tavily/serpapi/searxng and configure the matching key."
    Write-Host "RESULT: Service is running, but automatic live web search is NOT enabled."
    exit 0
  }
  "brave" {
    if ([string]::IsNullOrWhiteSpace((Read-EnvValue "BRAVE_SEARCH_API_KEY"))) { throw "BRAVE_SEARCH_API_KEY is empty." }
  }
  "tavily" {
    if ([string]::IsNullOrWhiteSpace((Read-EnvValue "TAVILY_API_KEY"))) { throw "TAVILY_API_KEY is empty." }
  }
  "serpapi" {
    if ([string]::IsNullOrWhiteSpace((Read-EnvValue "SERPAPI_API_KEY"))) { throw "SERPAPI_API_KEY is empty." }
  }
  "searxng" {
    Write-Host "INFO: SearXNG selected. The end-to-end test will verify whether it is reachable."
  }
  default {
    throw ("Unknown SEARCH_PROVIDER=" + $Provider)
  }
}
Write-Host "PASS: Search provider appears configured."

Write-Host ""
Write-Host "[6/6] End-to-end live search and report test"
$Headers = @{}
$Headers.Add("Content-Type", "application/json")
if (-not [string]::IsNullOrWhiteSpace($ApiKey)) {
  $Headers.Add("X-Hermes-Api-Key", $ApiKey)
}

$PayloadObject = @{
  title = "Hermes Local Doctor Live Search"
  prompt = "Search public information about GitHub Actions self-hosted runner official documentation. Write a short Chinese summary and list sources."
  max_results = 3
  notify = $false
}
$Payload = $PayloadObject | ConvertTo-Json -Depth 5
$RunUri = $BaseUrl + "/run"

$RunResult = Invoke-RestMethod -Uri $RunUri -Method POST -Headers $Headers -Body $Payload -TimeoutSec 240
$RunResult | ConvertTo-Json -Depth 8

if ($RunResult.status -ne "success") {
  throw ("End-to-end run failed: " + $RunResult.error)
}

if (-not $RunResult.sources -or $RunResult.sources.Count -lt 1) {
  throw "Run succeeded but no sources were captured. Search provider may not be working."
}

Write-Host "PASS: Hermes Agent can search the web, read sources, call the model, and generate a report."
Write-Host ("Report path: " + $RunResult.report_path)
