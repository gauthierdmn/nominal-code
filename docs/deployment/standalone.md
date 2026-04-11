# Standalone Deployment

The standalone mode runs the webhook server as a single process. It receives webhooks and executes review jobs in-process using an asyncio job queue. No container runtime, no orchestrator, no Redis.

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) for dependency management
- A GitHub/GitLab token and webhook secret
- An LLM provider API key, or the [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed

## Environment Variables

The standalone server reads its app config from `deploy/local/config.yaml`. Secrets and credentials must be set as environment variables.

### Required

You need **platform authentication** (at least one):

| Variable | Description |
|---|---|
| `GITHUB_TOKEN` | GitHub Personal Access Token |
| `GITHUB_APP_ID` + `GITHUB_APP_PRIVATE_KEY` | GitHub App credentials (alternative to PAT) |
| `GITLAB_TOKEN` | GitLab Personal Access Token |

You need **access control**:

| Variable | Description |
|---|---|
| `ALLOWED_USERS` | Comma-separated GitHub/GitLab usernames authorized to trigger the bot |

You need an **LLM provider API key** (unless using Claude Code CLI):

| Variable | Description |
|---|---|
| `GOOGLE_API_KEY` | Google AI API key (if `agent.reviewer.provider: "google"` in config) |
| `ANTHROPIC_API_KEY` | Anthropic API key (if `agent.reviewer.provider: "anthropic"`) |
| `OPENAI_API_KEY` | OpenAI API key (if `agent.reviewer.provider: "openai"`) |

### Optional

| Variable | Description | Default |
|---|---|---|
| `GITHUB_WEBHOOK_SECRET` | Webhook signature validation secret | _(skip validation)_ |
| `GITLAB_WEBHOOK_SECRET` | GitLab webhook secret | _(skip validation)_ |
| `GITHUB_REVIEWER_TOKEN` | Read-only token for reviewer clones (PAT mode only) | Falls back to `GITHUB_TOKEN` |
| `GITHUB_INSTALLATION_ID` | Required for CLI mode with GitHub App auth | _(extracted from webhook payload)_ |
| `ALLOWED_REPOS` | Comma-separated repo full names to process | _(all repos)_ |
| `LOG_LEVEL` | Logging verbosity | `INFO` |
| `WORKSPACE_BASE_DIR` | Directory for PR clones | System temp dir |

## Start the Server

```bash
# Export your secrets
export GITHUB_APP_ID=12345
export GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----"
export GITHUB_WEBHOOK_SECRET=your-webhook-secret
export ALLOWED_USERS=alice,bob
export GOOGLE_API_KEY=AIza...

# Start the server
make -C deploy serve
```

The Makefile validates that required environment variables are set before starting. If any are missing, you get a clear error:

```
ERROR: GOOGLE_API_KEY or ANTHROPIC_API_KEY is required
```

On success, you should see:

```
INFO  Starting server on 0.0.0.0:8080 | platforms=['github'] | reviewer=@nominalbot | runner=in-process | allowed_users=frozenset({'alice', 'bob'})
INFO  Server is running, waiting for webhooks...
```

## Stop the Server

Press `Ctrl+C` in the terminal. The server shuts down gracefully, completing any in-progress jobs before exiting.

## Exposing Webhooks

The server binds to `0.0.0.0:8080` by default (configurable in `deploy/local/config.yaml`). GitHub and GitLab need to reach this endpoint to deliver webhooks.

### For Local Development

Use a tunnel to expose your local server to the internet:

```bash
# cloudflared (no account required)
cloudflared tunnel --url http://localhost:8080

# ngrok
ngrok http 8080

# tailscale funnel
tailscale funnel 8080
```

Then configure your GitHub/GitLab webhook to point to the tunnel URL:

```
https://<tunnel-url>/webhooks/github
https://<tunnel-url>/webhooks/gitlab
```

### For Production

Place the server behind a reverse proxy (nginx, Caddy, etc.) that handles TLS termination:

```
https://bot.example.com/webhooks/github  →  http://localhost:8080/webhooks/github
```

## Config File

The app config lives at `deploy/local/config.yaml`:

```yaml
webhook:
  host: "0.0.0.0"
  port: 8080

reviewer:
  bot_username: "nominalbot"
  triggers:
    - pr_opened

agent:
  provider: "google"
```

This file controls non-secret settings: which bot username to respond to, which events auto-trigger reviews, and which LLM provider to use. Edit it to match your setup. See [Configuration](../reference/configuration.md) for the full YAML schema.

Environment variables override any value in the YAML file. For example, setting `AGENT_PROVIDER=anthropic` overrides `agent.reviewer.provider: "google"` from the config.

## Verify the Server

Once the server is running, check the health endpoint:

```bash
curl http://localhost:8080/health
# {"status": "ok"}
```

Then trigger a review by mentioning the bot in a PR comment on a repository where the webhook is configured:

```
@nominalbot please review this PR
```
