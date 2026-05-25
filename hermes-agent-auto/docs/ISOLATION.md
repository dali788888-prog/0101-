# Hermes Agent Isolation Plan

This module is committed as a separated folder and is not wired into the parent repository by default.

## Repository isolation

- All code lives under `hermes-agent-auto/`.
- No existing files outside this folder are modified.
- Runtime secrets stay in `hermes-agent-auto/.env`.
- Generated data stays in `hermes-agent-auto/storage/`.

## Docker isolation

| Resource | Name |
|---|---|
| Compose project | `hermes_agent_auto_isolated` |
| Container | `hermes-agent-auto-runtime` |
| Network | `hermes-agent-auto-net` |
| Default host port | `8099` |

Change `HERMES_AGENT_HOST_PORT` in `.env` if port `8099` is already in use.

## Deployment boundary

Run only from this folder:

```bash
cd hermes-agent-auto
cp .env.example .env
docker compose up -d --build
```

Stop only this isolated module:

```bash
cd hermes-agent-auto
docker compose down
```
