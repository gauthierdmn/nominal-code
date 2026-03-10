# Kubernetes Deployment

Deploy Nominal Code to Kubernetes to decouple the webhook server from job execution. The server pod receives webhooks and dispatches each review or worker task as a separate Kubernetes Job, enabling horizontal scaling with no shared process state.

## Architecture

```
GitHub/GitLab webhook
        │
        ▼
┌──────────────────────┐
│  Webhook Server Pod  │
│  (aiohttp)           │
│  JOB_RUNNER=kubernetes│
└──────────┬───────────┘
           │ POST /apis/batch/v1/...
           ▼
┌──────────────────────┐       ┌─────────┐
│  Kubernetes Job      │──────▶│  Redis   │
│  nominal-code run-job│       │ (conversations)
└──────────────────────┘       └─────────┘
```

**Server pod** — runs the webhook server with `JOB_RUNNER=kubernetes`. On each webhook event, it serializes a `JobPayload` and creates a Kubernetes Job via the in-cluster API.

**Job pod** — runs `nominal-code run-job`, deserializes the payload, calls the LLM provider API, and posts results back to the PR. Each job is independent and short-lived.

**Redis** — required. Provides per-PR job serialization via Redis queues and event-driven completion via pub/sub. Also stores conversation history so multi-turn interactions work across separate Job pods.

## What Changes vs. In-Process Mode

| | In-process (default) | Kubernetes |
|---|---|---|
| Job execution | Same process, asyncio queue | Separate K8s Job pod per event |
| Agent runner | Claude Code CLI (default) or LLM provider API | LLM provider API (requires `AGENT_PROVIDER`) |
| Conversation store | In-memory | Redis (required) |
| Scaling | Single process | Unlimited concurrent Jobs |
| Dependencies | Claude Code CLI on `PATH`, or an LLM provider API key | K8s cluster, container image, LLM provider API key |

## Local Development with Minikube

### Prerequisites

- [minikube](https://minikube.sigs.k8s.io/docs/start/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- Docker (used by minikube)

### Setup

Start minikube and create your secrets file:

```bash
minikube start

cp deploy/overlays/local/secret.yaml.template deploy/overlays/local/secret.yaml
```

Edit `deploy/overlays/local/secret.yaml` with your credentials:

```yaml
stringData:
  ANTHROPIC_API_KEY: "sk-ant-..."    # or whichever provider you use
  GITHUB_TOKEN: "ghp_..."
  GITHUB_WEBHOOK_SECRET: "your-webhook-secret"
```

### Build and Deploy

```bash
make -C deploy deploy
```

This builds the container image inside minikube's Docker daemon, applies the kustomize overlay, and waits for the rollout.

### Expose the Server

Forward the service to your local machine:

```bash
make -C deploy port-forward   # localhost:9090 → service:80
```

For GitHub/GitLab webhooks to reach your local server, use a tunnel:

```bash
ngrok http 9090
# or
cloudflared tunnel --url http://localhost:9090
# or
tailscale funnel 9090
```

Use the tunnel URL as your webhook Payload URL (e.g. `https://abc123.ngrok.io/webhooks/github`).

### Useful Commands

```bash
make -C deploy status       # Show pods, jobs, and services
make -C deploy logs         # Tail server logs
make -C deploy teardown     # Delete the namespace and all resources
```

## Production Cluster Setup

### Container Image

Build and push the image to your registry:

```bash
docker build -f ci/Dockerfile -t your-registry.com/nominal-code:latest .
docker push your-registry.com/nominal-code:latest
```

### Manifests

The base manifests live in `deploy/base/`. Use kustomize overlays to customize for your environment. The key resources are:

| Manifest | Purpose |
|---|---|
| `namespace.yaml` | Creates the `nominal-code` namespace |
| `deployment.yaml` | Webhook server pod |
| `service.yaml` | ClusterIP service (port 80 → 8080) |
| `rbac.yaml` | ServiceAccount + Role granting `batch/v1 jobs` permissions |
| `redis.yaml` | Redis deployment + service for conversation persistence |

### Create an Overlay

Create a new overlay directory for your cluster:

```bash
mkdir -p deploy/overlays/production
```

Create a `kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: nominal-code
resources:
  - ../../base
patchesStrategicMerge:
  - deployment-patch.yaml
```

Create a `deployment-patch.yaml` to override the image, pull policy, and environment:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nominal-code-server
  namespace: nominal-code
spec:
  template:
    spec:
      containers:
        - name: server
          image: your-registry.com/nominal-code:latest
          imagePullPolicy: Always
          env:
            - name: K8S_IMAGE
              value: "your-registry.com/nominal-code:latest"
            - name: K8S_IMAGE_PULL_POLICY
              value: "Always"
            - name: ALLOWED_USERS
              value: "alice,bob"
            - name: AGENT_PROVIDER
              value: "anthropic"
```

Create secrets in the namespace:

```bash
kubectl -n nominal-code create secret generic nominal-code-secrets \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  --from-literal=GITHUB_TOKEN=ghp_... \
  --from-literal=GITHUB_WEBHOOK_SECRET=your-secret
```

Deploy:

```bash
kubectl apply -k deploy/overlays/production
```

### Ingress

The service exposes port 80 internally. Configure an Ingress or load balancer to route external webhook traffic:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: nominal-code
  namespace: nominal-code
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt
spec:
  tls:
    - hosts: [bot.example.com]
      secretName: nominal-code-tls
  rules:
    - host: bot.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: nominal-code-server
                port:
                  number: 80
```

Then set your GitHub/GitLab webhook URL to `https://bot.example.com/webhooks/github`.

## Configuration

All Kubernetes-specific behavior is controlled via environment variables on the server pod. These are set in `deploy/base/deployment.yaml` and can be overridden in your overlay.

| Variable | Default | Description |
|---|---|---|
| `JOB_RUNNER` | — | Set to `kubernetes` to enable K8s job dispatch |
| `K8S_NAMESPACE` | `default` | Namespace for spawned Job pods |
| `K8S_IMAGE` | — | Container image for Job pods (required) |
| `K8S_IMAGE_PULL_POLICY` | — | `Always`, `Never`, or `IfNotPresent` |
| `K8S_SERVICE_ACCOUNT` | — | ServiceAccount for Job pods |
| `K8S_ENV_FROM_SECRETS` | — | Comma-separated Secret names to mount as env vars in Job pods |
| `K8S_BACKOFF_LIMIT` | `0` | Job retry attempts |
| `K8S_ACTIVE_DEADLINE_SECONDS` | `600` | Per-job timeout in seconds |
| `K8S_TTL_AFTER_FINISHED` | `3600` | Seconds before completed Jobs are cleaned up |
| `K8S_RESOURCE_REQUESTS_CPU` | — | CPU request for Job pods |
| `K8S_RESOURCE_REQUESTS_MEMORY` | — | Memory request for Job pods |
| `K8S_RESOURCE_LIMITS_CPU` | — | CPU limit for Job pods |
| `K8S_RESOURCE_LIMITS_MEMORY` | — | Memory limit for Job pods |
| `REDIS_URL` | — | Redis connection URL (required). Used for job queue serialization, pub/sub completion, and conversation persistence |

## Job Serialization

When multiple webhook events arrive for the same PR, only one K8s Job runs at a time. This prevents race conditions, duplicate reviews, and wasted compute.

The server uses Redis for two purposes:

1. **Per-PR job queue** — each PR key (`platform:repo:pr_number:bot_type`) gets its own Redis list. Jobs are pushed onto the list and consumed serially by a per-PR consumer task.
2. **Event-driven completion** — when a Job pod finishes, it publishes a completion signal to a Redis pub/sub channel (`nc:job:{job_name}:done`). The server subscribes to this channel and moves on to the next queued job immediately — no K8s API polling required.

This architecture enables safe multi-replica server deployments: any replica can enqueue jobs, and the Redis-backed queue ensures serialization regardless of which replica received the webhook.

If a Job pod crashes before publishing its completion signal, the server-side timeout (`K8S_ACTIVE_DEADLINE_SECONDS` + 10s margin) fires and the consumer moves on to the next job.

`REDIS_URL` is **required** when `JOB_RUNNER=kubernetes`. The server will refuse to start without it.

## RBAC

The server pod needs permission to create and manage Jobs. The base manifests include a ServiceAccount (`nominal-code-server`) with a Role that grants:

```yaml
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "watch", "delete"]
```

This is namespace-scoped — the server can only manage Jobs in its own namespace.

## Monitoring

- **Health checks** — the server pod exposes `/health` with liveness (30s) and readiness (10s) probes already configured in the base deployment.
- **Job status** — use `kubectl -n nominal-code get jobs` to see running and completed review jobs. Jobs are labeled with `nominal-code/platform`, `nominal-code/repo`, and `nominal-code/pr-number` for easy filtering.
- **Logs** — `make -C deploy logs` tails the server pod. For job pod logs: `kubectl -n nominal-code logs job/<job-name>`.
