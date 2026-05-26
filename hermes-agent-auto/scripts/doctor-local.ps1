$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RootDir

function Read-EnvValue($Name) {
  if (-not (Test-Path ".env")) { return "" }
  $line = Get-Content ".env" | Where-Object { $_ -match "^$Name=" } | Select-Object -Last 1
  if (-not $line) { return "" }
  return ($line -replace "^$Name=", "").Trim()
}

$Port = Read-EnvValue "HERMES_AGENT_HOST_PORT"
if (-not $Port) { $Port = "8099" }
$BaseUrl = "http://127.0.0.1:$Port"
$ProjectName = "hermes_agent_auto_isolated"
$ServiceName = "hermes-agent"
$ApiKey = Read-EnvValue "HERMES_AGENT_API_KEY"

Write-Host "== Hermes Agent Local Doctor =="
Write-Host "Base URL: $BaseUrl"
Write-Host "Project:  $ProjectName"

Write-Host "`n[1/6] Docker status"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "Docker command not found. Install Docker Desktop first."
}
docker --version

Write-Host "`n[2/6] Docker Compose status"
docker compose version

Write-Host "`n[3/6] Container status"
docker compose -p $ProjectName ps

Write-Host "`n[4/6] Health endpoint"
$Health = Invoke-RestMethod -Uri "$BaseUrl/health" -Method GET -TimeoutSec 10
$Health | ConvertTo-Json -Depth 5
if ($Health.status -ne "ok") {
  throw "Health endpoint returned non-ok status."
}
Write-Host "PASS: Hermes Agent service is reachable."

Write-Host "`n[5/6] Search configuration"
$Provider = Read-EnvValue "SEARCH_PROVIDER"
if (-not $Provider) { $Provider = $Health.search_provider }
if (-not $Provider) { $Provider = "none" }
Write-Host "SEARCH_PROVIDER=$Provider"

switch ($Provider) {
  "none" {
    Write-Host "WARN: Live web search is disabled. Set SEARCH_PROVIDER=brave/tavily/serpapi/searxng and configure the matching key."
    Write-Host "RESULT: Service is running, but automatic live web search is NOT enabled."
    exit 0
  }
  "brave" {
    if (-not (Read-EnvValue "BRAVE_SEARCH_API_KEY")) { throw "BRAVE_SEARCH_API_KEY is empty." }
  }
  "tavily" {
    if (-not (Read-EnvValue "TAVILY_API_KEY")) { throw "TAVILY_API_KEY is empty." }
  }
  "serpapi" {
    if (-not (Read-EnvValue "SERPAPI_API_KEY")) { throw "SERPAPI_API_KEY is empty." }
  }
  "searxng" {
    Write-Host "INFO: SearXNG selected. The end-to-end test will verify whether it is reachable."
  }
  default {
    throw "Unknown SEARCH_PROVIDER=$Provider"
  }
}
Write-Host "PASS: Search provider appears configured."

Write-Host "`n[6/6] End-to-end live search + report test"
$Headers = @{ "Content-Type" = "application/json" }
if ($ApiKey) { $Headers["X-Hermes-Api-Key"] = $ApiKey }
$Payload = @{
  title = "Hermes Local Doctor Live Search"
  prompt = "搜索公开资料：GitHub Actions self-hosted runner official documentation，输出简短中文摘要并列出来源。"
  max_results = 3
  notify = $false
} | ConvertTo-Json -Depth 5

$RunResult = Invoke-RestMethod -Uri "$BaseUrl/run" -Method POST -Headers $Headers -Body $Payload -TimeoutSec 240
$RunResult | ConvertTo-Json -Depth 8

if ($RunResult.status -ne "success") {
  throw "End-to-end run failed: $($RunResult.error)"
}

if (-not $RunResult.sources -or $RunResult.sources.Count -lt 1) {
  throw "Run succeeded but no sources were captured. Search provider may not be working."
}

Write-Host "PASS: Hermes Agent can search the web, read sources, call the model, and generate a report."
Write-Host "Report path: $($RunResult.report_path)"
