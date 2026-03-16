# Multi-Tenancy Roadmap

This document identifies the current limitations of nominal-code for operating as a multi-tenant SaaS running in Kubernetes, and proposes solutions for each.

The system today is designed as a **single-tenant, single-deployment** application: one GitHub App, one set of credentials, one global configuration, one shared Redis namespace. Every limitation below stems from that fundamental assumption.

---

## Table of Contents

1. [Authentication & GitHub App Model](#1-authentication--github-app-model)
2. [Configuration is Global and Static](#2-configuration-is-global-and-static)
3. [No Tenant/Organization Data Model](#3-no-tenantorganization-data-model)
4. [Data Isolation in Redis](#4-data-isolation-in-redis)
5. [Workspace & Filesystem Isolation](#5-workspace--filesystem-isolation)
6. [Authorization Model](#6-authorization-model)
7. [LLM Provider & API Key Management](#7-llm-provider--api-key-management)
8. [Cost Tracking & Billing](#8-cost-tracking--billing)
9. [Rate Limiting & Quotas](#9-rate-limiting--quotas)
10. [Kubernetes Job Isolation](#10-kubernetes-job-isolation)
11. [Webhook Routing & Tenant Resolution](#11-webhook-routing--tenant-resolution)
12. [Tenant Onboarding & Lifecycle](#12-tenant-onboarding--lifecycle)
13. [Observability & Audit Logging](#13-observability--audit-logging)
14. [Security & Secrets Management](#14-security--secrets-management)

---

## 1. Authentication & GitHub App Model

### Current Limitation

The server runs with a **single `GitHubAppAuth` instance** (one `GITHUB_APP_ID` + one private key). The `set_installation_id()` mechanism was designed for single-org use: the installation ID is extracted from each incoming webhook payload and set on the shared auth object. This works when all webhooks come from the same GitHub App, but introduces **two critical problems** in a multi-tenant context:

- **Token cache invalidation race**: When webhooks from different installations arrive concurrently, `set_installation_id()` invalidates the cached token for the _previous_ installation. Concurrent API calls using the old token will fail with 401 errors. The current code has no locking and a single `_cached_token` / `_token_expires_at` pair.

- **Single GitHub App identity**: All tenants share the same bot username and avatar. There is no way for Org A to have `@acme-reviewer` and Org B to have `@bigcorp-reviewer` — they all see the same GitHub App identity.

- **One webhook secret**: `GITHUB_WEBHOOK_SECRET` is global. All installations must use the same secret for signature verification, or verification must be disabled.

### Proposed Solution

**Per-installation token cache**: Replace the single `_cached_token` field with a `dict[int, tuple[str, float]]` mapping `installation_id → (token, expires_at)`. Each webhook is served with its own cached token, eliminating cross-installation invalidation. The `ensure_auth()` call already receives the installation ID from the webhook payload — it just needs to key the cache by it.

```
GitHubAppAuth:
    _token_cache: dict[int, CachedToken]   # keyed by installation_id

    async def get_token_for_installation(self, installation_id: int) -> str
```

**Thread the installation context**: Instead of mutating shared state via `set_installation_id()`, pass the installation ID through the request context. Each webhook handler should resolve the installation at parse time and carry it as part of the event or a request-scoped context, never mutating the auth singleton.

**Support multiple GitHub Apps** (optional, for white-label): If tenants need distinct bot identities, support a registry of GitHub App credentials keyed by app ID. The webhook payload contains the app ID — use it to look up the correct private key. This would be a `dict[str, GitHubAppCredentials]` loaded from a database or secret store at startup.

**Per-tenant webhook secrets**: Store webhook secrets per installation (or per GitHub App) in the tenant configuration. Verification looks up the correct secret based on the `app_id` or `installation_id` from the webhook header before comparing signatures.

---

## 2. Configuration is Global and Static

### Current Limitation

`Config` is a **frozen dataclass loaded once at startup** from environment variables. Every behavioral setting is global and immutable:

| Setting | Env Var | Impact |
|---------|---------|--------|
| Allowed users | `ALLOWED_USERS` | Global allowlist — no per-org user management |
| Allowed repos | `ALLOWED_REPOS` | Global — all orgs share the same repo filter |
| Bot usernames | `REVIEWER_BOT_USERNAME`, `WORKER_BOT_USERNAME` | One identity for all tenants |
| LLM provider/model | `AGENT_PROVIDER`, `AGENT_MODEL` | All tenants use the same model |
| System prompts | `REVIEWER_SYSTEM_PROMPT`, `WORKER_SYSTEM_PROMPT` | Same prompt for everyone |
| Coding guidelines | `CODING_GUIDELINES` | Same guidelines for everyone |
| Auto-review triggers | `REVIEWER_TRIGGERS` | Same trigger rules for all orgs |
| PR title filters | `PR_TITLE_INCLUDE_TAGS`, `PR_TITLE_EXCLUDE_TAGS` | Global |
| K8s job resources | `K8S_RESOURCE_*` | Same resource allocation for all tenants |

This means it is impossible for Org A to use Claude Sonnet while Org B uses GPT-4, or for Org A to auto-review on PR open while Org B only reviews on mention.

### Proposed Solution

**Introduce a `TenantConfig` model** stored in a database (PostgreSQL) rather than env vars. Each tenant (identified by GitHub installation ID or org name) has its own configuration:

```
TenantConfig:
    tenant_id: str
    installation_id: int
    allowed_users: set[str]
    allowed_repos: set[str]
    reviewer_bot_username: str
    worker_bot_username: str
    agent_provider: str
    agent_model: str
    system_prompt_override: str | None
    coding_guidelines_override: str | None
    reviewer_triggers: set[str]
    pr_title_include_tags: set[str]
    pr_title_exclude_tags: set[str]
    k8s_resource_requests_cpu: str
    k8s_resource_requests_memory: str
    k8s_resource_limits_cpu: str
    k8s_resource_limits_memory: str
    max_concurrent_jobs: int
    created_at: datetime
    updated_at: datetime
```

**Resolution flow**: On every incoming webhook, resolve the tenant from the installation ID (GitHub) or group/project token (GitLab). Load the `TenantConfig` and pass it through the request pipeline instead of the global `Config`. Cache tenant configs in memory with a short TTL (e.g. 60s) to avoid DB round-trips on every webhook.

**Keep env vars for infrastructure defaults**: Global infrastructure settings (webhook host/port, K8s namespace, Redis URL, log level) remain as env vars. Only tenant-facing behavior moves to the database.

**Management API**: Expose a REST API (or admin CLI) for CRUD on tenant configs. This is the prerequisite for self-service onboarding.

---

## 3. No Tenant/Organization Data Model

### Current Limitation

There is **no concept of tenant, organization, or account** anywhere in the data model. All domain objects are keyed by `(platform, repo_full_name, pr_number, bot_type)`. There is no `tenant_id`, no `org_id`, no user table. The system cannot answer questions like:

- "Which organization does this repo belong to?"
- "How many reviews has Org A consumed this month?"
- "What is Org B's subscription tier?"

### Proposed Solution

**Introduce a persistent data layer** with at minimum these entities:

```
Tenant:
    id: UUID
    name: str
    slug: str                    # URL-safe identifier
    plan: str                    # free, pro, enterprise
    status: str                  # active, suspended, trial
    created_at: datetime

TenantInstallation:
    id: UUID
    tenant_id: UUID              # FK → Tenant
    platform: str                # github, gitlab
    installation_id: int         # GitHub App installation ID
    org_name: str                # GitHub org or GitLab group
    webhook_secret: str          # Per-installation secret
    status: str                  # active, suspended, uninstalled
    installed_at: datetime

TenantMember:
    id: UUID
    tenant_id: UUID              # FK → Tenant
    platform_username: str
    role: str                    # owner, admin, member
    added_at: datetime

ReviewUsage:
    id: UUID
    tenant_id: UUID              # FK → Tenant
    repo_full_name: str
    pr_number: int
    bot_type: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    created_at: datetime
```

**Database choice**: PostgreSQL is the natural fit — structured data, relational queries, ACID transactions, good async driver support (`asyncpg`). No need for a full ORM initially; raw SQL or a lightweight query builder (e.g. `databases` + `sqlalchemy core`) is sufficient.

**Tenant resolution**: Build a lookup index `(platform, installation_id) → tenant_id` cached in memory. Every webhook first resolves the tenant, then all downstream operations carry the `tenant_id`.

---

## 4. Data Isolation in Redis

### Current Limitation

All Redis keys share a **flat `nc:` prefix** with no tenant scoping:

```
nc:conv:{platform}:{repo}:{pr_number}:{bot_type}
nc:msgs:{platform}:{repo}:{pr_number}:{bot_type}
nc:queue:{platform}:{repo}:{pr_number}:{bot_type}
nc:job:{job_name}:done
```

Any code with Redis access can read or overwrite data from any tenant. There is no encryption at rest — conversation histories (which may contain proprietary code) sit in plaintext.

### Proposed Solution

**Prefix all keys with tenant ID**:

```
nc:{tenant_id}:conv:{platform}:{repo}:{pr_number}:{bot_type}
nc:{tenant_id}:msgs:{platform}:{repo}:{pr_number}:{bot_type}
nc:{tenant_id}:queue:{platform}:{repo}:{pr_number}:{bot_type}
```

This is a minimal change — the key-building functions in `RedisConversationStore` and `RedisJobQueue` just need to accept and prepend the tenant ID.

**Per-tenant Redis databases** (optional): Redis supports 16 logical databases (0–15). For moderate tenant counts, assigning one DB per tenant provides basic isolation. For larger scale, use separate Redis instances or a managed Redis cluster with ACLs.

**Encryption at rest**: Enable Redis TLS and, if the hosting provider supports it, encryption at rest. For sensitive data like conversation histories containing code, consider encrypting values before storing them (AES-256-GCM with per-tenant keys from a KMS).

**TTL enforcement**: Keep the 7-day TTL but make it configurable per tenant (enterprise tenants may want longer retention).

---

## 5. Workspace & Filesystem Isolation

### Current Limitation

All git clones land in a shared `WORKSPACE_BASE_DIR` directory:

```
/tmp/nominal-code/
└── {owner}/
    └── {repo}/
        ├── pr-1/
        ├── pr-2/
        └── ...
```

There is no tenant-level directory scoping. A malicious or buggy agent running on Org A's PR could theoretically traverse to Org B's cloned repos if they share the same Kubernetes node/pod filesystem.

The `WorkspaceCleaner` iterates over all repos globally — it cannot clean per-tenant or enforce per-tenant disk quotas.

### Proposed Solution

**Scope workspaces by tenant**:

```
/tmp/nominal-code/
└── {tenant_id}/
    └── {owner}/
        └── {repo}/
            ├── pr-1/
            └── pr-2/
```

**Per-tenant disk quotas**: Track disk usage per tenant directory. The workspace cleaner should enforce tenant-level limits (e.g. 10 GB for free tier, 50 GB for enterprise). If a tenant exceeds their quota, reject new jobs until space is freed.

**Ephemeral job pods** (Kubernetes mode): In K8s mode, each job already runs in its own pod with its own filesystem — the isolation is inherent. Ensure that job pods use `emptyDir` volumes (not hostPath) and that pods are not scheduled with access to shared persistent volumes. This is already the case today, so K8s mode is naturally better isolated.

**Standalone mode**: For the in-process runner (standalone deployment), filesystem isolation is weaker since everything runs in one process. Document this limitation and recommend K8s mode for multi-tenant deployments.

---

## 6. Authorization Model

### Current Limitation

Authorization is a **single global `ALLOWED_USERS` frozenset** checked in `acknowledge_event()`. This is binary: you're either in the list or you're not. There is:

- No per-organization user management
- No role differentiation (admin vs. member vs. viewer)
- No per-repo permissions
- No way for an org admin to self-manage their own allowed users
- No way to authorize all members of a GitHub org automatically

Lifecycle events (PR opened, push) bypass user checks entirely — they auto-trigger regardless of who opened the PR.

### Proposed Solution

**Replace `ALLOWED_USERS` with tenant-scoped membership**:

1. When a GitHub App is installed on an org, fetch the org's member list via the GitHub API and populate `TenantMember` records.
2. On each webhook, resolve the tenant, then check if the comment author is a member of that tenant.
3. Support roles: `owner` (can manage config and members), `admin` (can manage config), `member` (can trigger bots).

**GitHub org membership sync**: Periodically sync org members using the GitHub API (`GET /orgs/{org}/members`). Alternatively, listen for `organization.member_added` / `member_removed` webhook events (requires the GitHub App to subscribe to the organization events).

**Per-repo overrides**: Allow tenants to restrict bot access to specific repos within their org, or override settings per repo (e.g. different guidelines for frontend vs. backend repos).

**Lifecycle event authorization**: For auto-triggered events, check that the PR author is a tenant member (or the repo is in the tenant's allowed list). This prevents the bot from reviewing PRs by external contributors unless the tenant opts in.

---

## 7. LLM Provider & API Key Management

### Current Limitation

The LLM provider is configured **globally via a single `AGENT_PROVIDER` and `{PROVIDER}_API_KEY` env var**. All tenants use the same provider, model, and API key. This means:

- All usage is billed to the operator's API key
- Tenants cannot bring their own API keys (BYOK)
- Tenants cannot choose their preferred model
- If the API key is rate-limited, all tenants are affected
- There is no way to enforce per-tenant usage limits

### Proposed Solution

**BYOK (Bring Your Own Key) support**: Allow tenants to configure their own LLM API keys in their `TenantConfig`. When a tenant provides their key, use it for their reviews; otherwise fall back to the platform's shared key (and bill accordingly).

```
TenantLLMConfig:
    tenant_id: UUID
    provider: str                  # anthropic, openai, google, ...
    model: str                     # claude-sonnet-4-20250514, gpt-4.1, ...
    api_key_encrypted: bytes       # AES-256-GCM encrypted, key from KMS
    base_url: str | None           # Custom endpoint for self-hosted models
    max_turns: int
    monthly_token_budget: int      # 0 = unlimited
```

**API key encryption**: Never store API keys in plaintext. Use a KMS (AWS KMS, GCP KMS, or HashiCorp Vault) to encrypt keys at rest. Decrypt only in memory when making LLM calls.

**Provider resolution at job time**: When building the `AgentConfig` for a job, resolve the tenant's provider preference and API key. Pass them through the job payload so that K8s job pods can use tenant-specific credentials.

**Shared key metering**: When tenants use the platform's shared API key, track token usage per tenant in the `ReviewUsage` table for billing purposes.

---

## 8. Cost Tracking & Billing

### Current Limitation

The `cost.py` module computes per-invocation costs and logs them, but:

- Costs are not attributed to any tenant
- There is no aggregation (monthly totals, per-repo breakdown)
- There is no billing integration
- There is no way to enforce spending limits
- The `ReviewUsage` data is not persisted — it only appears in logs

### Proposed Solution

**Persist usage records**: After each review completes, write a `ReviewUsage` row to PostgreSQL with the tenant ID, token counts, cost, and metadata.

**Aggregation queries**: Expose queries for monthly usage per tenant, per repo, per model. These power the billing dashboard and spending alerts.

**Spending limits**: Add `monthly_budget_usd` to `TenantConfig`. Before dispatching a job, check the tenant's month-to-date spend. If they're over budget, reject the job with a comment on the PR ("Review budget exceeded, contact your admin").

**Billing integration**: For SaaS billing, integrate with Stripe or a similar provider:
- Free tier: N reviews/month or M tokens/month
- Pro tier: higher limits, priority processing
- Enterprise: custom limits, BYOK, SLA

**Usage webhook/callback**: Optionally notify tenants of their usage via a callback URL or in-app dashboard.

---

## 9. Rate Limiting & Quotas

### Current Limitation

There is **no rate limiting** at any level:

- No per-tenant request rate limiting
- No per-user request rate limiting
- No concurrent job limits
- No protection against a single tenant monopolizing resources
- No backpressure mechanism when the system is overloaded

A single noisy tenant could flood the system with webhooks and consume all available K8s job slots or LLM API quota.

### Proposed Solution

**Per-tenant rate limiting**: Use Redis-based sliding window or token bucket rate limiting. Check on every webhook before enqueuing a job.

```
Limits per tenant (configurable per plan):
    max_requests_per_minute: int     # webhook rate limit
    max_concurrent_jobs: int         # job parallelism cap
    max_reviews_per_day: int         # daily review cap
    max_tokens_per_month: int        # monthly token budget
```

**Concurrent job tracking**: Before dispatching a K8s job, check how many active jobs the tenant has. If at the limit, queue the job and process it when a slot frees up. Use a Redis counter: `nc:{tenant_id}:active_jobs`.

**Global backpressure**: Monitor the overall K8s job queue depth. If it exceeds a threshold, start rejecting new jobs with a 503 or delay them. This protects the cluster from being overwhelmed.

**Priority queues**: Enterprise tenants get priority processing. Implement a weighted queue where higher-tier tenants' jobs are dequeued first.

---

## 10. Kubernetes Job Isolation

### Current Limitation

All K8s jobs run in the **same namespace** (`nominal-code`) with the **same service account**, **same image**, **same resource limits**, and **same secrets**. This means:

- All job pods can access the same K8s secrets (including all tenants' API keys if they were stored there)
- There is no per-tenant resource quota in K8s
- Job pods share the same RBAC permissions
- A misbehaving job pod from one tenant could affect others on the same node
- The `K8S_ENV_FROM_SECRETS` injects the same secret into every job pod

### Proposed Solution

**Per-tenant namespaces** (strongest isolation): Create a K8s namespace per tenant. Each namespace gets its own ResourceQuota, LimitRange, NetworkPolicy, and ServiceAccount. Job pods for Tenant A run in `nc-tenant-a` and cannot access resources in `nc-tenant-b`.

```yaml
# Per-tenant namespace template
apiVersion: v1
kind: Namespace
metadata:
  name: nc-{tenant_slug}
  labels:
    app: nominal-code
    tenant: {tenant_id}
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: tenant-quota
  namespace: nc-{tenant_slug}
spec:
  hard:
    requests.cpu: "4"
    requests.memory: "8Gi"
    limits.cpu: "8"
    limits.memory: "16Gi"
    count/jobs.batch: "10"
```

**Per-tenant secrets**: Store each tenant's API keys in their namespace's secrets. The job pod only mounts secrets from its own namespace. The server creates the K8s Secret dynamically when a tenant configures their API keys.

**Network policies**: Restrict job pod network access. Job pods need egress to: GitHub/GitLab APIs, LLM provider APIs, and Redis. Block everything else with a `NetworkPolicy`.

**Pod security**: Run job pods with a restricted security context (non-root, read-only root filesystem, dropped capabilities). This is good practice regardless of multi-tenancy.

**Alternative: shared namespace with RBAC** (weaker but simpler): Keep a single namespace but use different service accounts and secrets per tenant. This is less isolated but easier to manage. Suitable for trusted tenants (e.g. internal teams within a company).

---

## 11. Webhook Routing & Tenant Resolution

### Current Limitation

The webhook endpoint is `POST /webhooks/{platform_name}` — a single route for all tenants. There is no mechanism to identify which tenant a webhook belongs to before processing it. The installation ID is extracted _during_ event parsing, which happens _after_ webhook verification. This creates a chicken-and-egg problem: to verify the webhook signature, we need the tenant's webhook secret, but to find the tenant, we need to parse the webhook.

### Proposed Solution

**Two-pass webhook processing**:

1. **Pre-parse**: Extract the `installation.id` (GitHub) or project/group info (GitLab) from the raw JSON body before full parsing. This is a lightweight JSON key lookup, not a full event parse.
2. **Tenant lookup**: Use the installation ID to find the tenant and their webhook secret.
3. **Verify signature**: Verify the webhook signature using the tenant's specific secret.
4. **Full parse**: Parse the event and proceed with the tenant context.

For GitHub, the installation ID is reliably present in the webhook payload at `installation.id` for all event types that the app subscribes to.

**Fallback for unknown installations**: If a webhook arrives from an installation ID that isn't registered (e.g. someone just installed the app), route it to a tenant onboarding flow rather than rejecting it.

**Tenant context propagation**: Once resolved, attach the `tenant_id` to a request-scoped context object that flows through the entire pipeline: event parsing → authorization → job dispatch → agent execution → result posting.

---

## 12. Tenant Onboarding & Lifecycle

### Current Limitation

There is **no onboarding flow**. Today, setting up nominal-code for an org means:

1. Manually creating a GitHub App (or using the shared one)
2. Installing the app on the target org
3. Manually configuring env vars (`ALLOWED_USERS`, `ALLOWED_REPOS`, etc.)
4. Deploying or restarting the server

There is no self-service, no installation webhook handler, no uninstall cleanup.

### Proposed Solution

**GitHub App installation webhook handler**: Listen for `installation.created` and `installation.deleted` events:

- **On install**: Create a `Tenant` record, populate `TenantInstallation`, sync org members, set default config. Optionally send a welcome comment/issue on the org's repos.
- **On uninstall**: Mark the tenant as `uninstalled`, stop processing their webhooks, clean up their workspaces and Redis keys.
- **On `installation_repositories` events**: Track which repos the app has access to. Update the tenant's repo list accordingly.

**Self-service dashboard** (future): A web UI where org admins can:
- View and modify their configuration (model, triggers, guidelines)
- Manage allowed users and repos
- View usage and cost breakdowns
- Configure their own API keys
- Upgrade/downgrade their plan

**Trial and suspension**: Support tenant states: `trial` → `active` → `suspended` → `deleted`. Suspended tenants' webhooks are acknowledged but jobs are not dispatched (with a PR comment explaining the status).

---

## 13. Observability & Audit Logging

### Current Limitation

Logging is unstructured `logging.basicConfig` output to stdout with no tenant attribution. There is no audit trail of:

- Who triggered which review
- What the bot did (API calls, file changes)
- Configuration changes
- Errors per tenant
- Performance metrics per tenant

### Proposed Solution

**Structured logging with tenant context**: Switch to structured JSON logging (e.g. `structlog` or `python-json-logger`). Include `tenant_id`, `installation_id`, `repo`, `pr_number` in every log line.

```json
{
  "timestamp": "2026-03-10T12:00:00Z",
  "level": "info",
  "event": "review_completed",
  "tenant_id": "abc-123",
  "repo": "acme/backend",
  "pr_number": 42,
  "provider": "anthropic",
  "model": "claude-sonnet-4-20250514",
  "input_tokens": 15000,
  "output_tokens": 3200,
  "cost_usd": 0.12,
  "duration_seconds": 8.5
}
```

**Audit log table**: Persist significant events to a `AuditLog` table in PostgreSQL:

```
AuditLog:
    id: UUID
    tenant_id: UUID
    actor: str                  # username or "system"
    action: str                 # review_triggered, config_updated, member_added, ...
    resource_type: str          # pr, config, member, ...
    resource_id: str
    metadata: jsonb
    created_at: datetime
```

**Metrics**: Export Prometheus metrics per tenant: review count, latency, token usage, error rate, queue depth. Use labels for `tenant_id`, `provider`, `model`. This enables per-tenant alerting and capacity planning.

---

## 14. Security & Secrets Management

### Current Limitation

Secrets are managed via **environment variables and K8s Secrets in plaintext**:

- `GITHUB_APP_PRIVATE_KEY` is an env var or file path
- LLM API keys are env vars (`ANTHROPIC_API_KEY`, etc.)
- Webhook secrets are env vars (`GITHUB_WEBHOOK_SECRET`)
- K8s Secrets store all of these — accessible to any pod with the secret mount
- There is no secret rotation mechanism
- There is no per-tenant secret isolation

In a multi-tenant setup, a single K8s Secret cannot hold per-tenant API keys securely — any job pod with the secret mount would have access to all tenants' keys.

### Proposed Solution

**External secret store**: Use HashiCorp Vault, AWS Secrets Manager, or GCP Secret Manager to store tenant secrets. The server fetches secrets at runtime via API calls, caching them in memory with TTLs.

**Per-tenant secret paths**: Organize secrets by tenant:

```
vault:
  nominal-code/
    tenants/
      {tenant_id}/
        github_webhook_secret
        llm_api_key
```

**Secret injection for K8s jobs**: Instead of mounting a shared K8s Secret, the server creates a short-lived K8s Secret per job containing only that tenant's credentials. The secret is deleted when the job completes (or has a TTL via the `ttl_after_finished` mechanism).

**Secret rotation**: Support rotating GitHub App private keys and webhook secrets without downtime. The server should accept the new key while still validating the old one during a rotation window.

**Minimal privilege**: Job pods should only receive the secrets they need for the specific review (the tenant's LLM API key and the platform auth token). They should not have access to other tenants' secrets or to the server's administrative credentials.

---

## Implementation Priority

The following order reflects dependencies and impact:

| Phase | Items | Rationale |
|-------|-------|-----------|
| **P0 — Foundation** | #3 (Tenant model), #11 (Webhook routing), #1 (Auth per-installation) | Nothing works without tenant resolution |
| **P1 — Isolation** | #4 (Redis isolation), #5 (Workspace isolation), #2 (Per-tenant config) | Data must be isolated before onboarding real tenants |
| **P2 — Operations** | #6 (Authorization), #9 (Rate limiting), #10 (K8s job isolation) | Protect the system from abuse and enforce boundaries |
| **P3 — Business** | #7 (API key management), #8 (Cost tracking), #12 (Onboarding) | Enable self-service and billing |
| **P4 — Polish** | #13 (Observability), #14 (Secrets management) | Operational maturity for production SaaS |
