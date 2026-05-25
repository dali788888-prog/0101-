# Hermes Agent API

## Health

`GET /health`

## Run one-off task

`POST /run`

```json
{
  "prompt": "Search and summarize public information...",
  "max_results": 8,
  "notify": false
}
```

## Create scheduled task

`POST /tasks`

```json
{
  "title": "OKX monitoring",
  "prompt": "Search public information every 2 hours and write Chinese report.",
  "interval_minutes": 120,
  "max_results": 8,
  "notify": true,
  "run_now": true
}
```

## Reports

- `GET /reports`
- `GET /reports/{report_id}`
