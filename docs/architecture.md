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
                    └─ clone/update → fetch diff + comments → run agent (read-only) → submit review
```

### CLI Flow

```
nominal-code review owner/repo#42 [--dry-run] [--prompt "..."]
        │
        ▼
_parse_pr_ref()                     ← validate owner/repo#N format
        │
        ▼
build_platform()                    ← construct platform from env token
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
build_platform(name, config)        ← construct platform from CI config
        │                              GitHub: commands/ci/github.py
        │                              GitLab: commands/ci/gitlab.py
        │
        ├─ build_event()            ← read event from CI env vars
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

## Sub-Agents

The `agent/sub_agents/` package provides first-class support for parallel sub-agent execution. Sub-agents are lightweight, isolated agent instances that compose on top of `run_api_agent()`.

### Agent Types

`AgentType` is a `StrEnum` with per-type tool restrictions:

| Type | Tools | Purpose |
|---|---|---|
| `explore` | Read, Glob, Grep, Bash | Read-only codebase exploration |
| `plan` | Read, Glob, Grep, Bash | Planning and analysis |

No agent type includes the `submit_review` or `Agent` tool — sub-agents cannot produce reviews or spawn other sub-agents.

### Parallel Exploration

The primary entry point is `run_explore_with_planner()`:

```
changed_files + diffs
        │
        ├─ if len(files) >= file_threshold (default 8):
        │       ▼
        │   plan_exploration_groups()
        │       │
        │       ├─ build_planner_user_message() → file paths + line counts
        │       ├─ provider.send() → single LLM call (no tools, JSON output)
        │       └─ parse_planner_response() → list[ExploreGroup]
        │
        ├─ if planner produced ≥2 groups:
        │       ▼
        │   run_explore(groups)
        │       │
        │       ├─ allocate_turns(total, num_groups) → per_group turns (min 4)
        │       ├─ asyncio.gather([
        │       │    _run_single_sub_agent(group) → run_api_agent(allowed_tools=...)
        │       │    for group in groups
        │       │  ], return_exceptions=True)
        │       └─ aggregate_metrics() → AggregatedMetrics
        │
        └─ else (fallback):
                ▼
            Single agent with all files in one group
```

Each sub-agent receives:
- A system prompt with the sub-agent suffix ("You are a background sub-agent...")
- Tool restrictions from `AGENT_TYPE_TOOLS[AgentType.EXPLORE]`
- The group's exploration prompt (authored by the planner)
- An isolated turn budget

### Key Types

- `ExploreGroup(label, files, prompt)` — a partition of changed files with a focused exploration prompt.
- `SubAgentResult(group, output, is_error, num_turns, duration_ms, messages, cost)` — result from one sub-agent.
- `AggregatedMetrics` — summed token counts, API calls, and costs across sub-agents with wall-clock duration.
- `ParallelExploreResult(sub_results, metrics)` — aggregated result from parallel execution.

### Prompts

Bundled prompt files in `prompts/sub_agents/`:
- `explore.md` — system prompt for exploration sub-agents (read-only context gathering).
- `planner.md` — system prompt for the planner (file grouping into JSON).

## Policies

Event handling is governed by two frozen Pydantic models that separate **what** gets processed from **how** it gets dispatched:

- **`FilteringPolicy`** — repository allowlists, user authorization, and PR title tag matching. Applied before any dispatch decision.
- **`RoutingPolicy`** — reviewer auto-trigger events and bot usernames for `@mention` matching.

Both policies are fields on the top-level `Config` (`config.filtering`, `config.routing`) and are constructed from the YAML file and environment variables at startup.

The webhook handler's public dispatch functions (`dispatch_lifecycle_event`, `dispatch_comment_event`, `filter_event`) accept policies directly rather than the full `Config`, making them reusable in contexts that construct their own policies — such as a multi-tenant enterprise wrapper that builds per-organization policies from a database.

See **[Policies](reference/policies.md)** for the full reference.

## Configuration Architecture

The `config/` package uses a two-layer pattern: mutable **Settings** models for input, frozen **Config** models for output.

### Layer 1: Settings (input) — `config/models.py`

`AppSettings` and its nested `*Settings` models (`KubernetesSettings`, `RedisSettings`, etc.) are mutable Pydantic models that mirror the shape of the YAML file and environment variables. Their job is purely structural — they hold raw values exactly as the user provided them.

`AppSettings.from_env()` merges three sources in priority order: model defaults → YAML file → environment variables. The `_ENV_MAP` table flattens legacy env var names (e.g. `K8S_NAMESPACE`) into the nested structure (`["kubernetes", "namespace"]`).

### Layer 2: Config (output) — `config/config.py`, `config/kubernetes.py`

`Config`, `WebhookConfig`, `KubernetesConfig`, `RedisConfig`, etc. are frozen (`frozen=True`) Pydantic models that represent the validated, resolved configuration the application consumes. They are immutable and safe to pass around.

### The bridge: `config/loader.py`

`loader.py` transforms Settings into Config. This is where business logic lives:

- **Validation** — e.g. "at least one bot must be configured", "ALLOWED_USERS is required".
- **Derivation** — reading prompt files from disk, parsing trigger strings into `EventType` frozensets, resolving agent provider configs.
- **Reshaping** — the Config models don't mirror the YAML structure. Fields get flattened, renamed, or combined (e.g. `resources.requests.cpu` → `resource_requests_cpu`).
- **Conditional construction** — `KubernetesConfig` is `None` when no image is set, `RedisConfig` is `None` when no URL is set.

### Why two layers?

Settings and Config serve different masters:

| | Settings | Config |
|---|---|---|
| **Serves** | The user writing YAML/env vars | The application code consuming config |
| **Shape** | Mirrors the config file structure | Mirrors what the code needs |
| **Mutability** | Mutable (intermediate merge target) | Frozen (safe to pass around) |
| **Content** | Raw strings, file paths, lists | Resolved values — file contents loaded, enums parsed, frozensets built |
| **Optionality** | Everything has defaults | Missing-means-disabled expressed as `None` |

A single-layer approach would force one model to serve both roles — you'd either leak file paths and parsing logic into application code, or leak validation rules into the YAML schema. The two-layer split keeps the config file ergonomic for users while giving application code exactly the types it needs.

### Adding a new config field

1. Add the field with a default to the appropriate `*Settings` model in `models.py`.
2. If it needs an env var, add a `(ENV_NAME, ["section", "field"])` entry to `_ENV_MAP` and the appropriate type set (`INT_KEYS`, `BOOL_KEYS`, or `COMMA_LIST_KEYS`).
3. Add the field to the corresponding `*Config` model in `settings.py` or `kubernetes.py`.
4. Forward the value in the relevant `_resolve_*()` or `load_config()` function in `loader.py`.
5. Add tests for the env var override in `tests/test_config.py`.

## Components

### Webhook Server

An [aiohttp](https://docs.aiohttp.org/) application that exposes:

- `GET /health` — returns `{"status": "ok"}`
- `POST /webhooks/{platform}` — one route per enabled platform

Each incoming request is verified, parsed, and dispatched. The HTTP response is returned immediately; actual processing happens asynchronously via the job queue.

### Pre-flight Checks (`commands/webhook/helpers.py`)

- **`run_pre_flight()`** — central pre-flight for all events. For comment events: validates the author against `ALLOWED_USERS`, logs the event, posts the eyes reaction. For lifecycle events: logs with event type/title/author, posts a PR reaction, and skips auth and comment reaction. Returns whether the job should proceed.

### Job Processing (`commands/webhook/jobs/runner/process.py`)

- **`ProcessRunner`** — sets clone URLs on the event, builds the handler closure, and enqueues jobs for serial execution via the job queue.

### Handlers

- **`review.handler.review()`** — core review logic (clone, fetch diff + comments, run agent, parse JSON, filter findings). Returns a `ReviewResult` without posting. Used by webhook, CLI, and CI modes.
- **`review.handler.run_and_post_review()`** — webhook/CI entry point. Calls `review()` then posts results to the platform.

### CLI Module (`commands/cli/main.py`)

- **`_parse_pr_ref()`** — parses `owner/repo#42` into a repo name and PR number.
- **`_run_review()`** — orchestrates the CLI flow: resolve branch, call `review()`, print results, optionally post. Uses `build_platform()` from `platforms/` to construct the platform client.

### CI Module (`commands/ci/main.py`)

- **`run_ci_review()`** — main entry point for CI-triggered reviews. Dispatches to the platform-specific CI module, runs the review, and posts results.

### Platform CI Modules (`commands/ci/{github,gitlab}.py`)

Each platform provides a CI module with three functions:

- **`build_event()`** — reads CI environment variables and returns a `PullRequestEvent`. GitHub reads `$GITHUB_EVENT_PATH`; GitLab reads `$CI_PROJECT_PATH`, `$CI_MERGE_REQUEST_IID`, etc.
- **`build_platform()`** — constructs a `Platform` from CI config (`GitHubConfig` or `GitLabConfig`).
- **`resolve_workspace()`** — returns the CI runner's checkout directory (`$GITHUB_WORKSPACE` or `$CI_PROJECT_DIR`).

### Agent Dispatcher (`agent/invoke.py`)

Single entry point for agent execution. Routes to the API or CLI runner based on the agent config type (`CliAgentConfig` or `ApiAgentConfig`), with conversation persistence. See [Agent Runners](#agent-runners) above.

### API Runner (`agent/api/runner.py`)

Implements the LLM provider API agentic loop with local tool execution. See [LLM Provider API Runner](#llm-provider-api-runner) above.

### API Tools (`agent/api/tools.py`)

Defines and executes tools for the API runner: `Read`, `Glob`, `Grep`, and `Bash`. Bash commands are validated against an allowlist when `allowed_tools` restricts the agent (e.g. the reviewer is limited to `Bash(git clone*)`).

### CLI Runner (`agent/cli/runner.py`)

Wraps the Claude Code SDK to stream messages from the CLI subprocess. See [Claude Code CLI Runner](#claude-code-cli-runner) above.

### Prompt Composition

The reviewer prompt is composed from multiple sources across two layers:

**System prompt** (instructions — who you are, how to behave):
1. Base reviewer prompt (`prompts/reviewer_prompt.md`)
2. Suggestions instructions (`prompts/reviewer_suggestions.md`) — appended when `inline_suggestions` is enabled in config
3. Repository guidelines (`.nominal/guidelines.md` or built-in) — wrapped in `<repo-guidelines>` tags

**User message** (the review input — what to review):
1. Branch header and user prompt (wrapped in `<untrusted-request>`)
2. Changed files with diffs (wrapped in `<untrusted-diff>`)
3. Existing PR comments (wrapped in `<untrusted-comment>`)
4. Context — optional pre-review context via the `context` parameter on `review()`. Typically the output from codebase exploration sub-agents, but can be any additional information the caller wants the reviewer to consider. Inserted verbatim.
5. Review instruction (verify line numbers, call `submit_review`)

System prompt composition is handled by `agent/prompts.py`, which loads guidelines from the `.nominal/` directory with per-repo and per-language overrides. Language detection is based on file extensions in the PR diff.

### Conversation Store and Job Queue

- **ConversationStore** (`conversation/memory.py`) — a unified in-memory store with two parallel dicts keyed by `(platform, repo, pr_number)`: lightweight conversation IDs and full message histories (API mode only). Used to resume conversations across multiple interactions on the same PR.
- **AsyncioJobQueue** (`commands/webhook/jobs/queue/asyncio.py`) — per-PR async job queue for in-process mode. Each PR key gets its own `asyncio.Queue` with a single consumer task, ensuring that agent invocations on the same PR run serially (no race conditions). The consumer and queue are cleaned up when drained.
- **RedisJobQueue** (`commands/webhook/jobs/queue/redis.py`) — Redis-backed per-PR job queue for Kubernetes mode. Uses Redis lists for serial execution and Redis pub/sub for event-driven job completion. Each PR key gets a consumer task that loops with `BRPOP`, creates K8s Jobs, and awaits completion signals — no K8s API polling required.

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

Jobs are keyed by `(platform_name, repo_full_name, pr_number)`. When a job is enqueued:

1. If no queue exists for that key, one is created along with a consumer task.
2. The job is put into the queue.
3. The consumer processes jobs serially, one at a time.
4. When the queue drains, the consumer task and queue are cleaned up.

In **Kubernetes mode**, the `RedisJobQueue` replaces the in-memory `AsyncioJobQueue`. The flow is:

1. Webhook arrives → `KubernetesRunner.run()` enqueues the job payload to a Redis list keyed by `nc:queue:{platform}:{repo}:{pr}`.
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
├── models.py            # Shared enums (EventType, FileStatus) and dataclasses
├── commands/
│   ├── cli/             # One-shot review CLI (package)
│   │   └── main.py      # argparse, platform construction, review orchestration
│   ├── ci/              # CI mode
│   │   ├── main.py      # CI review entry point (run_ci_review)
│   │   ├── github.py    # CI mode: build event, platform, and workspace from GitHub Actions env vars
│   │   └── gitlab.py    # CI mode: build event, platform, and workspace from GitLab CI env vars
│   └── webhook/         # Webhook server and K8s job entrypoint
│       ├── main.py      # aiohttp app with /health and /webhooks/{platform} routes
│       ├── helpers.py   # Pre-flight checks (auth, reactions, logging), mention extraction
│       ├── result.py    # DispatchResult dataclass
│       └── jobs/        # Job processing
│           ├── main.py          # K8s pod entry point (run-job)
│           ├── payload.py       # JobPayload serializable dataclass
│           ├── dispatch.py      # Job dispatch logic (execute_job)
│           ├── handler.py       # JobHandler protocol
│           ├── runner/          # Job runner implementations
│           │   ├── base.py      # JobRunner protocol + build_runner() factory
│           │   ├── process.py   # ProcessRunner: job enqueueing and handler dispatch
│           │   └── kubernetes.py # Kubernetes Job dispatcher with Redis queue integration
│           └── queue/           # Job queue implementations
│               ├── base.py      # JobQueue protocol
│               ├── asyncio.py   # AsyncioJobQueue (per-PR in-memory async queue)
│               └── redis.py     # RedisJobQueue (Redis-backed per-PR queue + pub/sub)
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
│   ├── compaction.py    # Deterministic message compaction (no LLM call)
│   ├── api/
│   │   ├── runner.py    # LLM provider API agentic loop (tool use)
│   │   └── tools.py     # Tool definitions and execution (Read, Glob, Grep, Bash)
│   ├── cli/
│   │   └── runner.py    # Claude Code CLI subprocess wrapper (SDK integration)
│   └── sub_agents/      # Parallel sub-agent orchestration
│       ├── types.py     # AgentType enum, per-type tool mappings
│       ├── result.py    # ExploreGroup, SubAgentResult, AggregatedMetrics
│       ├── planner.py   # LLM-based file grouping planner
│       ├── runner.py    # run_explore(), run_explore_with_planner()
│       └── prompts.py   # Bundled prompt loading
├── conversation/
│   ├── base.py          # Conversation store protocol
│   ├── memory.py        # In-memory conversation store
│   └── redis.py         # Redis-backed conversation store
├── review/
│   ├── handler.py       # Review orchestration: diff fetching, prompt building, output parsing
│   ├── diff.py          # Diff handling utilities
│   └── output.py        # Output parsing and JSON repair
├── platforms/
│   ├── base.py          # Platform protocol and shared dataclasses
│   ├── http.py          # request_with_retry(): HTTP request helper with transient error retries
│   ├── github/
│   │   ├── auth.py      # GitHubAuth ABC, PAT and App auth implementations
│   │   └── platform.py  # GitHub webhook handler and REST API client
│   └── gitlab/
│       └── platform.py  # GitLab webhook handler and REST API client
└── workspace/
    ├── git.py           # GitWorkspace: clone, update, push per-PR workspaces
    └── setup.py         # Branch resolution and workspace construction helpers
```
