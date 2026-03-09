# Deployment

Nominal Code can be deployed as a **standalone server** (single process, no orchestrator) or on **Kubernetes** (webhook server + Job-per-review scaling). Both options run the same webhook server — the difference is how review jobs are executed.

| | Standalone | Kubernetes |
|---|---|---|
| Job execution | Same process, asyncio queue | Separate K8s Job pod per event |
| Agent runner | Claude Code CLI (default) or LLM provider API | LLM provider API (requires `AGENT_PROVIDER`) |
| Conversation store | In-memory | Redis (required) |
| Scaling | Single process | Unlimited concurrent Jobs |
| Dependencies | Claude Code CLI on `PATH`, or an LLM provider API key | K8s cluster, container image, LLM provider API key |
| Best for | Small teams, simple setup | Production, high-volume orgs |

Choose your deployment model:

- **[Standalone](standalone.md)** — run the server directly, no orchestrator needed
- **[Kubernetes](kubernetes.md)** — deploy to a K8s cluster with per-review Job isolation

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

The cleaner only removes workspaces for PRs that are no longer open. If an API check fails, the workspace is kept as a safety measure. See [Configuration — Workspace Cleanup](../reference/configuration.md#workspace-cleanup) for details.
