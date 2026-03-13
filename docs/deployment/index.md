# Deployment

Nominal Code can be deployed as a **standalone server** or on **Kubernetes**. Both run the same webhook server — the difference is how review jobs are executed and how the server is managed.

| | Standalone | Kubernetes |
|---|---|---|
| Job execution | Same process, asyncio queue | Separate K8s Job pod per event |
| Agent runner | Claude Code CLI (default) or LLM provider API | LLM provider API (requires `agent.provider`) |
| Conversation store | In-memory | Redis (required) |
| Scaling | Single process | Unlimited concurrent Jobs |
| Dependencies | Claude Code CLI on `PATH`, or an LLM provider API key | K8s cluster, container image, LLM provider API key |
| Best for | Small teams, local development | Production, high-volume orgs |

Both modes are driven by `make` targets in the `deploy/` directory:

```bash
make -C deploy serve      # standalone server
make -C deploy deploy     # kubernetes
```

Choose your deployment model:

- **[Standalone](standalone.md)** — run the server directly with `make serve`
- **[Kubernetes](kubernetes.md)** — deploy to a K8s cluster with per-review Job isolation

## Configuration Layout

```
deploy/
├── Makefile                          # All targets: serve, deploy, teardown, logs, ...
├── local/
│   └── config.yaml                   # App config (standalone mode)
├── k8s/
│   ├── base/                         # Kustomize base manifests
│   │   ├── config.yaml               # App config + redis/k8s settings
│   │   ├── deployment.yaml
│   │   ├── kustomization.yaml
│   │   ├── namespace.yaml
│   │   ├── rbac.yaml
│   │   ├── redis.yaml
│   │   └── service.yaml
│   ├── kustomization.yaml            # Includes base + secrets
│   ├── secret.yaml.template
│   └── secret.yaml                   # Your secrets (gitignored)
└── ci/                               # CI overlay (secrets from env vars)
    ├── kustomization.yaml
    └── deployment-patch.yaml
```

The config YAML files contain non-secret application settings (webhook host/port, reviewer bot, agent provider). The Kubernetes config adds `redis` and `kubernetes` sections that are only relevant in-cluster. Secrets (tokens, API keys, webhook secrets) are kept separate — as environment variables in standalone mode, or in Kubernetes Secret manifests for K8s deployments.

## Quick Reference

All commands are run from the repository root.

| Command | Description |
|---|---|
| `make -C deploy serve` | Start the standalone webhook server |
| `make -C deploy deploy` | Deploy to Kubernetes (pulls image from GHCR) |
| `make -C deploy deploy-ci` | Deploy to Kubernetes with secrets from env vars |
| `make -C deploy teardown` | Delete the `nominal-code` K8s namespace |
| `make -C deploy logs` | Tail the K8s server pod logs |
| `make -C deploy status` | Show K8s pods, jobs, and services |
| `make -C deploy port-forward` | Forward `localhost:8080` to the K8s service |
| `make -C deploy build` | Build the container image |
| `make -C deploy help` | Show all available targets |

## Health Endpoint

Both deployment modes expose a health check at:

```
GET /health
```

Returns `{"status": "ok"}` with a 200 status code. Use this for load balancer health checks, readiness probes, or uptime monitoring.

## Log Levels

Set `LOG_LEVEL` to control verbosity:

```bash
LOG_LEVEL=DEBUG    # verbose, includes agent output and API calls
LOG_LEVEL=INFO     # default, startup info and key events
LOG_LEVEL=WARNING  # only warnings and errors
LOG_LEVEL=ERROR    # only errors
```

## Workspace Disk Usage

The bot clones repositories into the workspace base directory (YAML: `workspace.base_dir`, env: `WORKSPACE_BASE_DIR`, default: system temp dir). Each PR gets its own shallow clone.

In production Kubernetes deployments, reviews run in ephemeral Job pods — no disk management is needed. For local or persistent-disk deployments, periodically remove stale `pr-{N}` directories manually (e.g. via a cron job or shell script).
