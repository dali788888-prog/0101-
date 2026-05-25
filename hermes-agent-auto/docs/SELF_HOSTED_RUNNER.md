# Hermes Agent Self-Hosted Runner Deployment

This guide turns your own machine or VPS into a GitHub Actions runner that keeps Hermes Agent running through Docker Compose.

## 1. Prepare the machine

Use a Linux VPS or physical server with Docker installed.

Minimum recommendation:

- 2 CPU cores
- 4 GB RAM
- 20 GB disk
- Docker + Docker Compose plugin
- Network access to GitHub and Ollama endpoint

## 2. Add the runner in GitHub

Open the repository page:

```text
Settings -> Actions -> Runners -> New self-hosted runner
```

Choose Linux x64 unless your server uses another architecture.

GitHub will show commands similar to:

```bash
mkdir actions-runner && cd actions-runner
curl -o actions-runner-linux-x64.tar.gz -L <github-runner-download-url>
tar xzf ./actions-runner-linux-x64.tar.gz
./config.sh --url https://github.com/dali788888-prog/0101- --token <one-time-token> --labels hermes-agent
./run.sh
```

Important: add the label `hermes-agent`. The workflow requires:

```yaml
runs-on: [self-hosted, hermes-agent]
```

## 3. Keep the runner online

For production, install the runner as a service:

```bash
sudo ./svc.sh install
sudo ./svc.sh start
sudo ./svc.sh status
```

## 4. Configure Hermes Agent on the runner

After the first workflow checkout, configure:

```bash
cd <runner-workdir>/0101-/hermes-agent-auto
cp .env.example .env
nano .env
```

Recommended production settings:

```env
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=hermes3:8b
SEARCH_PROVIDER=brave
BRAVE_SEARCH_API_KEY=your_key_here
HERMES_AGENT_HOST_PORT=8099
```

## 5. Run deployment workflow

Open:

```text
Actions -> Hermes Agent Self Hosted Runtime -> Run workflow -> main
```

The workflow will:

1. Checkout the repository on your self-hosted runner.
2. Build Hermes Agent Docker image.
3. Start the container with Docker Compose.
4. Check `http://127.0.0.1:8099/health`.
5. Keep the container running after workflow completion.

## 6. Verify runtime

On the server:

```bash
docker compose -p hermes_agent_auto_selfhosted ps
curl http://127.0.0.1:8099/health
```

Open from browser if firewall allows:

```text
http://SERVER_IP:8099
```

## 7. Stop runtime

```bash
cd hermes-agent-auto
docker compose -p hermes_agent_auto_selfhosted down
```

## Notes

- GitHub-hosted runners are temporary. They cannot keep Hermes Agent online.
- Self-hosted runners run on your machine, so the container can stay alive after the workflow ends.
- Store real API keys only on the runner machine or GitHub Secrets. Do not commit `.env`.
