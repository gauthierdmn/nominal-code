# Deployment

## Running the Server

The bot is a single-process asyncio application with no external dependencies (no database, no message broker, no Redis). All state is held in memory.

```bash
cd app
uv run nominal-code
```

The process runs until terminated. It handles multiple concurrent webhook events using asyncio, with per-PR serial processing enforced by the session queue.

For multi-platform configuration (GitHub + GitLab simultaneously), see [Webhook Mode — Multi-Platform Setup](modes/webhook.md#multi-platform).

## Health Endpoint

The server exposes a health check at:

```
GET /health
```

Returns `{"status": "ok"}` with a 200 status code. Use this for load balancer health checks or uptime monitoring.

## Log Levels

Set `LOG_LEVEL` to control verbosity:

```bash
LOG_LEVEL=DEBUG    # verbose, includes agent output and API calls
LOG_LEVEL=INFO     # default, startup info and key events
LOG_LEVEL=WARNING  # only warnings and errors
LOG_LEVEL=ERROR    # only errors
```

## Workspace Disk Usage

The bot clones repositories into `WORKSPACE_BASE_DIR` (defaults to a system temp directory). Each PR gets its own shallow clone.

To control disk usage:

- Set `WORKSPACE_BASE_DIR` to a volume with sufficient space.
- Tune `CLEANUP_INTERVAL_HOURS` (default: 6) to clean up stale workspaces more or less frequently.
- Set `CLEANUP_INTERVAL_HOURS=0` to disable automatic cleanup and manage disk space manually.

The cleaner only removes workspaces for PRs that are no longer open. If an API check fails, the workspace is kept as a safety measure. See [Configuration — Workspace Cleanup](reference/configuration.md#workspace-cleanup) for details.

## Exposing Webhooks

The server binds to `WEBHOOK_HOST:WEBHOOK_PORT` (default: `0.0.0.0:8080`). You need to make this endpoint reachable from GitHub/GitLab.

### Reverse Proxy

To serve over HTTPS, place the bot behind a reverse proxy (nginx, Caddy, etc.) that handles TLS termination:

```
https://bot.example.com/webhooks/github  →  http://localhost:8080/webhooks/github
```

### Tunnels for Development

For local development, use a tunnel to expose your local server:

```bash
# ngrok
ngrok http 8080

# cloudflared
cloudflared tunnel --url http://localhost:8080

# tailscale funnel
tailscale funnel 8080
```

Then use the tunnel URL as your webhook Payload URL.
