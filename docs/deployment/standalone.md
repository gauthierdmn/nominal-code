# Standalone

The simplest deployment model. The webhook server runs as a single process — it receives webhooks and executes review jobs in the same process using an asyncio job queue.

## Running the Server

```bash
cd app
uv run nominal-code
```

The process runs until terminated. It handles multiple concurrent webhook events using asyncio, with per-PR serial processing enforced by the job queue.

For multi-platform configuration (GitHub + GitLab simultaneously), see [Webhook Mode — Multi-Platform Setup](../modes/webhook.md#multi-platform).

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
