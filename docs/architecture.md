# Architecture

## Request Flow

### Webhook Mode

```
PR comment "@bot do something"  ──or──  PR opened/pushed/reopened
        │                                        │
        ▼                                        ▼
GitHub/GitLab sends webhook             GitHub/GitLab sends webhook
        │                                        │
        └──────────────┬─────────────────────────┘
                       ▼
            POST /webhooks/{platform}
                       │
                       ├─ verify_webhook()         ← signature/token check
                       ├─ parse_event()            ← normalize into PullRequestEvent
                       ├─ [repo in ALLOWED_REPOS?] ← skip if not listed
                       │
                       ├─ [lifecycle event in REVIEWER_TRIGGERS?]
                       │       ▼
                       │   enqueue_job()            ← no auth check, no reaction
                       │       │
                       │       ▼
                       │   job_queue.enqueue()  ← reviewer with empty prompt
                       │
                       ├─ [comment event?]
                       │       ▼
                       │   extract_mention()        ← identify bot type + prompt
                       │       │
                       │       ▼
                       │   enqueue_job()
                       │       │
                       │       ├─ allowed_users check
                       │       ├─ post_reaction("eyes")
                       │       │
                       │       ▼
                       │   job_queue.enqueue()
                       │
                       └─ [otherwise] → ignored
                               │
                               ▼
                    job runs serially per PR
                               │
                    ├─ [WORKER]  clone/update → run agent (all tools) → post reply
                    └─ [REVIEWER] clone/update → fetch diff + comments → run agent (read-only) → submit review
```

### CLI Flow

```
nominal-code review owner/repo#42 [--dry-run] [--prompt "..."]
        │
        ▼
_parse_pr_ref()                     ← validate owner/repo#N format
        │
        ▼
_build_platform()                   ← construct platform from env token
        │
        ├─ fetch_pr_branch()       ← resolve HEAD branch via API
        │
        ▼
review()
        │
        ├─ clone/update workspace
        ├─ fetch diff + comments (parallel)
        ├─ build prompt + run agent (Claude Code CLI)
        ├─ parse JSON + filter findings
        │
        ▼
_print_review()                     ← format results for terminal
        │
        ├─ [unless --dry-run] submit_review() or post_reply()
        │
        ▼
exit 0
```

### CI Flow

```
nominal-code ci {platform}
        │
        ▼
_load_platform_ci()                 ← import platform-specific CI module
        │                              GitHub: platforms/github/ci.py
        │                              GitLab: platforms/gitlab/ci.py
        │
        ├─ build_event()            ← read event from CI env vars
        ├─ build_platform()         ← construct platform from CI env vars
        ├─ resolve_workspace()      ← use CI runner checkout
        │
        ▼
Config.for_ci()                     ← build config with ApiAgentConfig
        │
        ▼
review()
        │
        ├─ diff + comments (parallel fetch)
        ├─ build prompt + run agent (LLM provider API)
        ├─ parse JSON + filter findings
        │
        ▼
submit_review() or post_reply()
        │
        ▼
exit 0
```

## Agent Runners

Nominal Code supports two agent execution backends. The mode is selected automatically based on the execution context.

### Claude Code CLI Runner

Used by **CLI mode** and **webhook server mode**. Wraps the [Claude Code SDK](https://github.com/anthropics/claude-code-sdk-python) to spawn the Claude Code CLI as a subprocess.

- Streams messages from the CLI process and captures the conversation ID for multi-turn continuity.
- Monkey-patches the SDK message parser to gracefully handle unknown message types (e.g. `rate_limit_event`), preventing the stream from crashing.
- Supports conversation resumption via stored conversation IDs.
- Requires the Claude Code CLI to be installed and on `PATH` (or set via `AGENT_CLI_PATH`).
- Uses the CLI's configured login method — supports Claude Pro and Claude Max subscriptions as an alternative to per-token API billing.
- Captures token usage and cost from the SDK's `ResultMessage` when available.

### LLM Provider API Runner

Used by **CI mode**. Calls the LLM provider API directly with tool use. Supports multiple providers (Anthropic, OpenAI, Google Gemini, DeepSeek, Groq, Together, Fireworks).

- Implements an agentic loop: sends a prompt, processes `tool_use` blocks by executing tools locally, sends results back, and repeats until the model produces a final text answer or `max_turns` is reached.
- Provides four tools: `Read` (file contents), `Glob` (file search), `Grep` (content search), and `Bash` (shell commands with allowlist validation).
- Does not require the Claude Code CLI — only a provider API key (per-token billing).
- Does not support conversation continuity (each run is stateless).
- Accumulates token usage across all turns and computes dollar cost using a bundled pricing table.

### Runner Selection

| Execution Mode | Agent Runner | Selected By |
|---|---|---|
| CI (`nominal-code ci`) | LLM provider API | `ApiAgentConfig` |
| CLI (`nominal-code review`) | Claude Code CLI | `CliAgentConfig` |
| Webhook server | Claude Code CLI | `CliAgentConfig` |

The dispatcher in `agent/invoke.py` routes to the appropriate backend based on whether the config is a `CliAgentConfig` or `ApiAgentConfig`.

## Policies

Event handling is governed by two frozen Pydantic models that separate **what** gets processed from **how** it gets dispatched:

- **`FilteringPolicy`** — repository allowlists, user authorization, and PR title tag matching. Applied before any dispatch decision.
- **`RoutingPolicy`** — reviewer auto-trigger events and bot usernames for `@mention` matching.

Both policies are fields on the top-level `Config` (`config.filtering`, `config.routing`) and are constructed from the YAML file and environment variables at startup.

The webhook handler's public dispatch functions (`dispatch_lifecycle_event`, `dispatch_comment_event`, `filter_event`) accept policies directly rather than the full `Config`, making them reusable in contexts that construct their own policies — such as a multi-tenant enterprise wrapper that builds per-organization policies from a database.

See **[Policies](reference/policies.md)** for the full reference.

## Components

### Webhook Server

An [aiohttp](https://docs.aiohttp.org/) application that exposes:

- `GET /health` — returns `{"status": "ok"}`
- `POST /webhooks/{platform}` — one route per enabled platform

Each incoming request is verified, parsed, and dispatched. The HTTP response is returned immediately; actual processing happens asynchronously via the job queue.

### Platform Registry

A factory-based registry where each platform module self-registers at import time. At startup, `build_platforms()` calls each factory and returns only the platforms that are configured (i.e. have their required tokens set).

### Pre-flight Checks (`commands/webhook/helpers.py`)

- **`run_pre_flight()`** — central pre-flight for all events. For comment events: validates the author against `ALLOWED_USERS`, logs the event, posts the eyes reaction. For lifecycle events: logs with event type/title/author, posts a PR reaction, and skips auth and comment reaction. Returns whether the job should proceed.

### Job Processing (`jobs/runner/process.py`)

- **`ProcessRunner`** — sets clone URLs on the event, builds the appropriate handler closure, and enqueues jobs for serial execution via the job queue.

### Handlers

- **`handlers.worker.review_and_fix()`** — clones the repo, runs the agent with full tools, posts the reply.
- **`handlers.review.review()`** — core review logic (clone, fetch diff + comments, run agent, parse JSON, filter findings). Returns a `ReviewResult` without posting. Used by webhook, CLI, and CI modes.
- **`handlers.review.review_and_post()`** — webhook entry point. Calls `review()` then posts results to the platform.

### CLI Module (`commands/cli.py`)

- **`_parse_pr_ref()`** — parses `owner/repo#42` into a repo name and PR number.
- **`_build_platform()`** — constructs a platform client from environment tokens (no webhook secret needed).
- **`_run_review()`** — orchestrates the CLI flow: resolve branch, call `review()`, print results, optionally post.

### CI Module (`commands/ci.py`)

- **`run_ci_review()`** — main entry point for CI-triggered reviews. Dispatches to the platform-specific CI module, runs the review, and posts results.
- **`_load_platform_ci()`** — imports the correct platform CI module (`platforms/github/ci.py` or `platforms/gitlab/ci.py`).

### Platform CI Modules (`platforms/{github,gitlab}/ci.py`)

Each platform provides a `ci.py` module with three functions:

- **`build_event()`** — reads CI environment variables and returns a `PullRequestEvent`. GitHub reads `$GITHUB_EVENT_PATH`; GitLab reads `$CI_PROJECT_PATH`, `$CI_MERGE_REQUEST_IID`, etc.
- **`build_platform()`** — constructs a `ReviewerPlatform` from CI tokens (`$GITHUB_TOKEN` or `$GITLAB_TOKEN`).
- **`resolve_workspace()`** — returns the CI runner's checkout directory (`$GITHUB_WORKSPACE` or `$CI_PROJECT_DIR`).

### Agent Dispatcher (`agent/invoke.py`)

Single entry point for agent execution. Routes to the API or CLI runner based on the agent config type (`CliAgentConfig` or `ApiAgentConfig`), with conversation persistence. See [Agent Runners](#agent-runners) above.

### API Runner (`agent/api/runner.py`)

Implements the LLM provider API agentic loop with local tool execution. See [LLM Provider API Runner](#llm-provider-api-runner) above.

### API Tools (`agent/api/tools.py`)

Defines and executes tools for the API runner: `Read`, `Glob`, `Grep`, and `Bash`. Bash commands are validated against an allowlist when `allowed_tools` restricts the agent (e.g. the reviewer is limited to `Bash(git clone*)`).

### CLI Runner (`agent/cli/runner.py`)

Wraps the Claude Code SDK to stream messages from the CLI subprocess. See [Claude Code CLI Runner](#claude-code-cli-runner) above.

### Prompt Composition (`agent/prompts.py`)

Loads and composes the system prompt from multiple sources: the bot's base prompt, global coding guidelines, and per-repo/per-language overrides from the `.nominal/` directory. Language detection is based on file extensions in the PR diff.

### Conversation Store and Job Queue (`conversation/memory.py`, `jobs/queue/asyncio.py`)

- **ConversationStore** — a unified in-memory store with two parallel dicts keyed by `(platform, repo, pr_number, bot_type)`: lightweight conversation IDs and full message histories (API mode only). Used to resume conversations across multiple interactions on the same PR.
- **AsyncioJobQueue** — per-PR async job queue for in-process mode (`jobs/queue/asyncio.py`). Each PR key gets its own `asyncio.Queue` with a single consumer task, ensuring that agent invocations on the same PR run serially (no race conditions). The consumer and queue are cleaned up when drained.
- **RedisJobQueue** — Redis-backed per-PR job queue for Kubernetes mode (`jobs/queue/redis.py`). Uses Redis lists for serial execution and Redis pub/sub for event-driven job completion. Each PR key gets a consumer task that loops with `BRPOP`, creates K8s Jobs, and awaits completion signals — no K8s API polling required.

### Cost Tracking (`llm/cost.py`)

Both agent runners capture token usage and compute dollar costs per invocation:

- **Pricing data** — a bundled `llm/data/pricing.json` file maps model IDs to per-token rates (input, output, cache write, cache read). Generated from the [LiteLLM community pricing database](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) by `scripts/update_pricing.py` and auto-updated weekly via a GitHub Actions workflow.
- **API runner** — accumulates `TokenUsage` from each provider response across the agentic loop, then calls `build_cost_summary()` to compute the total cost.
- **CLI runner** — extracts `total_cost_usd` and token usage from the SDK's `ResultMessage`.
- **Output** — cost is attached to `AgentResult.cost` and `ReviewResult.cost`, logged by the review handler, and formatted for CI output.

### Git Workspace (`workspace/git.py`)

Manages per-PR cloned repositories. Handles initial cloning, updating (fetch + reset), pushing changes, and provides a shared `.deps/` directory for private dependency cloning.

### Workspace Setup (`workspace/setup.py`)

Helper functions for branch resolution and workspace construction. `resolve_branch()` fetches the PR branch from the platform API when the webhook payload doesn't include it. `create_workspace()` and `setup_workspace()` construct and initialise `GitWorkspace` instances.

### Error Handling (`agent/errors.py`)

An async context manager (`handle_agent_errors()`) that wraps handler execution. Catches workspace setup failures and agent runtime errors, posts user-facing error messages to the platform, and prevents unhandled exceptions from crashing the event loop.

## Workspace Directory Layout

```
{WORKSPACE_BASE_DIR}/
└── {owner}/
    └── {repo}/
        ├── .deps/           ← shared private dependency clones
        ├── pr-1/            ← workspace for PR #1
        ├── pr-2/            ← workspace for PR #2
        └── pr-N/
```

Each `pr-{N}` directory is a shallow clone of the repository checked out to the PR's head branch.

In CI mode, the workspace is the CI runner's checkout directory (e.g. `$GITHUB_WORKSPACE` or `$CI_PROJECT_DIR`) — no cloning is needed.

## Job Queue

The job queue ensures that only one agent runs per PR at a time. This prevents race conditions when multiple comments arrive in quick succession on the same PR.

Jobs are keyed by `(platform_name, repo_full_name, pr_number, bot_type)`. When a job is enqueued:

1. If no queue exists for that key, one is created along with a consumer task.
2. The job is put into the queue.
3. The consumer processes jobs serially, one at a time.
4. When the queue drains, the consumer task and queue are cleaned up.

In **Kubernetes mode**, the `RedisJobQueue` replaces the in-memory `AsyncioJobQueue`. The flow is:

1. Webhook arrives → `KubernetesRunner.run()` enqueues the job payload to a Redis list keyed by `nc:queue:{platform}:{repo}:{pr}:{bot}`.
2. A per-PR consumer task `BRPOP`s from the list and creates a K8s Job for each dequeued payload.
3. The Job pod runs `nominal-code run-job`, performs the review, and publishes a completion signal to `nc:job:{job_name}:done` via Redis pub/sub.
4. The server receives the signal and the consumer moves on to the next queued job.

## Source Layout

```
nominal_code/
├── main.py              # Entry point: dispatches to webhook server, CLI, or CI
├── config/
│   ├── settings.py      # Frozen Config model loaded from env vars / files
│   ├── policies.py      # FilteringPolicy and RoutingPolicy (frozen Pydantic models)
│   ├── loader.py        # load_config(), load_config_for_cli(), load_config_for_ci()
│   ├── agent.py         # AgentConfig, CliAgentConfig, ApiAgentConfig
│   └── kubernetes.py    # KubernetesConfig
├── models.py            # Shared enums (EventType, BotType, FileStatus) and dataclasses
├── commands/
│   ├── cli.py           # One-shot review CLI (argparse, platform construction)
│   ├── ci.py            # CI mode dispatcher (delegates to platform-specific CI modules)
│   └── webhook/         # Webhook server and K8s job entrypoint
│       ├── server.py    # aiohttp app with /health and /webhooks/{platform} routes
│       ├── helpers.py   # Pre-flight checks (auth, reactions, logging), mention extraction
│       └── entrypoint.py # K8s job runner CLI command
├── llm/
│   ├── provider.py      # LLM provider protocol and base classes
│   ├── registry.py      # Provider registry and factory
│   ├── messages.py      # Canonical message types (LLMResponse, TokenUsage, etc.)
│   ├── cost.py          # CostSummary, pricing loader, cost computation
│   ├── data/
│   │   └── pricing.json # Bundled model pricing (auto-updated from LiteLLM)
│   ├── anthropic.py     # Anthropic provider implementation
│   ├── openai.py        # OpenAI provider implementation (also used by DeepSeek, Groq, Together, Fireworks)
│   └── google.py        # Google Gemini provider implementation
├── agent/
│   ├── invoke.py        # Single entry point: invoke_agent() (with persistence) + invoke_agent_stateless()
│   ├── result.py        # AgentResult dataclass (output, turns, conversation ID, cost)
│   ├── prompts.py       # Guideline loading, language detection, system prompt composition
│   ├── errors.py        # Async context manager for handler error handling
│   ├── api/
│   │   ├── runner.py    # LLM provider API agentic loop (tool use)
│   │   └── tools.py     # Tool definitions and execution (Read, Glob, Grep, Bash)
│   └── cli/
│       └── runner.py    # Claude Code CLI subprocess wrapper (SDK integration)
├── conversation/
│   ├── base.py          # Conversation store protocol
│   ├── memory.py        # In-memory conversation store
│   └── redis.py         # Redis-backed conversation store
├── handlers/
│   ├── review.py        # Reviewer bot: structured code review with inline comments
│   ├── worker.py        # Worker bot: full-access agent that pushes code changes
│   ├── diff.py          # Diff handling utilities
│   └── output.py        # Output parsing and JSON repair
├── jobs/
│   ├── payload.py       # ReviewJob serializable dataclass
│   ├── execute.py       # Shared execution logic: routes by bot type, prepares and runs jobs
│   ├── runner/          # Job runner implementations
│   │   ├── base.py      # JobRunner protocol + build_runner() factory
│   │   ├── process.py   # ProcessRunner: job enqueueing and handler dispatch
│   │   └── kubernetes.py # Kubernetes Job dispatcher with Redis queue integration
│   └── queue/           # Job queue implementations
│       ├── base.py      # JobQueue protocol
│       ├── asyncio.py   # AsyncioJobQueue (per-PR in-memory async queue)
│       └── redis.py     # RedisJobQueue (Redis-backed per-PR queue + pub/sub)
├── platforms/
│   ├── base.py          # Protocol definitions and shared dataclasses
│   ├── registry.py      # Self-registering platform factory pattern
│   ├── http.py          # request_with_retry(): HTTP request helper with transient error retries
│   ├── github/
│   │   ├── auth.py      # GitHubAuth ABC, PAT and App auth implementations
│   │   ├── ci.py        # CI mode: build event, platform, and workspace from GitHub Actions env vars
│   │   └── platform.py  # GitHub webhook handler and REST API client
│   └── gitlab/
│       ├── ci.py        # CI mode: build event, platform, and workspace from GitLab CI env vars
│       └── platform.py  # GitLab webhook handler and REST API client
└── workspace/
    ├── git.py           # GitWorkspace: clone, update, push per-PR workspaces
    └── setup.py         # Branch resolution and workspace construction helpers
```
