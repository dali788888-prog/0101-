# Hermes Agent Auto Executor

This folder contains an isolated local-first Hermes Agent runtime.

## What it adds

- Ollama / Hermes model integration
- Search adapters: Brave, Tavily, SerpAPI, SearXNG, or none
- Public web page reading
- Scheduled task execution
- Markdown report generation
- SQLite task and run storage
- FastAPI API and simple web UI
- Optional Telegram or webhook notifications

## Isolation rule

Everything is contained under `hermes-agent-auto/`. It does not modify the parent project, does not require root-level files, and stores runtime data only in `hermes-agent-auto/storage/`.

## Start

```bash
cd hermes-agent-auto
cp .env.example .env
ollama pull hermes3:8b
docker compose up -d --build
```

Open:

```text
http://localhost:8099
```

## Enable live search

Edit `.env`:

```env
SEARCH_PROVIDER=brave
BRAVE_SEARCH_API_KEY=your_key_here
```

Supported providers: `brave`, `tavily`, `serpapi`, `searxng`, `none`.

## Create an automatic task

```bash
curl -X POST http://localhost:8099/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "OKX public research",
    "prompt": "每2小时搜索公开资料：OKX Web3 钱包/API/SDK/项目对接要求，输出中文更新报告，标注来源。",
    "interval_minutes": 120,
    "max_results": 8,
    "run_now": true
  }'
```

## Boundary

Hermes Agent is limited to legal public-source research and reporting.
