# nominal-code

AI-powered code review bot that monitors GitHub PRs and GitLab MRs. When a user @mentions the bot in a comment, or when a lifecycle event fires (PR opened, pushed, etc.), the bot spins up an LLM agent to review code and posts the results back to the platform.

## Architecture

- **Async-first** — built on aiohttp + asyncio; all I/O (HTTP, git, agent) is non-blocking.
- **Two-layer config** — mutable `*Settings` models (`config/models.py`) parse YAML/env vars; `loader.py` transforms them into frozen `*Config` models (`config/settings.py`, `config/kubernetes.py`) that application code consumes. Platform credentials (GitHub/GitLab tokens, webhook secrets) flow through the same config pipeline.
- **Protocol-based platforms** — GitHub and GitLab implement the same `Platform` protocol, making it easy to add new providers. Platform clients are built from `GitHubConfig`/`GitLabConfig` via explicit factory functions.
- **Per-PR job serialisation** — `JobQueue` protocol with two implementations (`AsyncioJobQueue` for in-process, `RedisJobQueue` for Kubernetes) guarantees only one agent job runs per PR at a time, preventing race conditions on the same workspace.
- **Multi-turn conversations** — `ConversationStore` maps (platform, repo, PR) to conversation IDs and message histories so conversations resume across comments.
- **Workspace isolation** — each PR gets its own shallow clone; a shared `.deps/` directory is available for cross-PR dependencies.
- **Dual agent runners** — CLI and webhook modes use the Claude Code CLI (supports subscriptions); CI mode calls the LLM provider API directly (requires a provider API key).
- **LLM cost tracking** — both runners capture token usage and compute dollar costs using a bundled pricing table. Cost summaries are logged after each review and surfaced in CI output.

## Entry points

- `nominal-code serve` — starts the webhook server.
- `nominal-code review owner/repo#42` — one-shot CLI review (supports `--dry-run`, `--platform`, `--prompt`).
- `nominal-code ci {platform}` — CI mode review (GitHub Actions / GitLab CI). Uses the LLM provider API runner.
- `nominal-code run-job` — internal: executes a single job in a K8s pod (invoked by `KubernetesRunner`).

## Processing layers

The call chain follows four conceptual layers:

1. **Receive** — `commands/webhook/main.py` (webhooks), `commands/` (CLI/CI).
2. **Prepare** — `workspace/setup.py::prepare_job_event()` resolves clone URLs and branches; `commands/webhook/jobs/runner/process.py` wraps with error handling and queue management.
3. **Orchestrate** — `review/reviewer.py` (business logic: diff fetching, PR metadata fetching, prompt building, sub-agent configuration, output parsing).
4. **Invoke** — `agent/invoke.py` provides agent execution with explicit conversation lifecycle (`prepare_conversation`, `invoke_agent`, `save_conversation`).

## Agent runner selection

| Mode | Runner | Config flag |
|------|--------|-------------|
| Webhook server | Claude Code CLI (`agent/cli/runner.py`) | `CliAgentConfig` |
| CLI review | Claude Code CLI (`agent/cli/runner.py`) | `CliAgentConfig` |
| CI (`commands/ci/`) | LLM provider API (`agent/api/runner.py`) | `ApiAgentConfig` |

The dispatcher in `agent/invoke.py` routes based on the agent config type (`CliAgentConfig` or `ApiAgentConfig`). The API runner implements its own tool execution (Read, Glob, Grep, Bash with allowlist) and supports multiple providers (Anthropic, OpenAI, Google Gemini, DeepSeek, Groq, Together, Fireworks).

## Guideline resolution

1. Load repo-level `.nominal/guidelines.md` (or custom path via `CODING_GUIDELINES` env var). Defaults to empty.
2. Detect languages from changed file extensions.
3. Load per-language `.nominal/languages/{lang}.md` (overrides bundled `nominal_code/prompts/languages/{lang}.md`).
4. Concatenate general + language-specific guidelines into the system prompt.

## Environment variables

### Required (webhook mode)

- `ALLOWED_USERS` — comma-separated usernames authorised to trigger jobs.
- `REVIEWER_BOT_USERNAME` — the @mention name for the reviewer bot.
- GitHub auth (one of):
  - **PAT mode**: `GITHUB_TOKEN`.
  - **App mode**: `GITHUB_APP_ID` + one of `GITHUB_APP_PRIVATE_KEY` (inline PEM) / `GITHUB_APP_PRIVATE_KEY_PATH` (file path).
- And/or `GITLAB_TOKEN`.

### Required (CI mode)

- A provider API key (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`) — used by the API runner directly.
- `GITHUB_TOKEN` or `GITLAB_TOKEN` — for posting review comments.
- CI-provided variables (`GITHUB_EVENT_PATH`, `CI_PROJECT_PATH`, etc.) are read automatically.

### Optional

- `GITHUB_WEBHOOK_SECRET` / `GITLAB_WEBHOOK_SECRET` — webhook signature validation (shared by both PAT and App modes).
- `GITHUB_REVIEWER_TOKEN` / `GITLAB_REVIEWER_TOKEN` — read-only token for reviewer clones (PAT mode only; App mode scopes via installation permissions).
- `GITHUB_INSTALLATION_ID` — required for CLI mode with App auth; webhook mode extracts it from the payload.
- `WORKSPACE_BASE_DIR` — workspace root (default: `/tmp/nominal-code`).
- `AGENT_PROVIDER`, `AGENT_MODEL` — reviewer provider and model (also default for explorer).
- `AGENT_EXPLORER_PROVIDER`, `AGENT_EXPLORER_MODEL` — explorer sub-agent provider and model override.
- `AGENT_CLI_PATH` — path to the Claude Code CLI binary.
- `ALLOWED_REPOS` — comma-separated repository full names to process (e.g. `owner/repo-a,owner/repo-b`). When unset, all repos are accepted.
- `REVIEWER_TRIGGERS` — comma-separated lifecycle events that auto-trigger the reviewer (e.g. `pr_opened,pr_push`).
- `LOG_LEVEL` — logging verbosity (default: `INFO`).

## File tree

```
nominal_code/
├── main.py              # Entry point: dispatches to webhook server, CLI, or CI
├── config/
│   ├── models.py        # Mutable *Settings models (YAML/env input layer), includes GitHubSettings/GitLabSettings
│   ├── settings.py      # Frozen *Config models (application output layer), includes GitHubConfig/GitLabConfig
│   ├── loader.py        # Settings → Config transformation with validation
│   ├── policies.py      # FilteringPolicy and RoutingPolicy (frozen)
│   ├── agent.py         # AgentConfig, CliAgentConfig, ApiAgentConfig
│   └── kubernetes.py    # KubernetesConfig (frozen)
├── models.py            # Shared enums (EventType, FileStatus) and dataclasses (ReviewFinding, AgentReview, ChangedFile)
├── commands/
│   ├── cli/             # CLI review mode (package with main.py)
│   ├── ci/              # CI mode: entry point + platform-specific event/workspace parsing
│   └── webhook/         # Webhook server, helpers, mention extraction, K8s job runner
│       ├── main.py      # aiohttp webhook server (run_webhook_server)
│       ├── helpers.py   # Pre-flight checks, mention extraction
│       ├── result.py    # DispatchResult dataclass
│       └── jobs/        # Job payload, dispatch, handler, runner and queue subpackages
│           ├── main.py  # K8s pod entry point (run-job)
├── llm/                 # LLM provider abstraction, cost tracking, canonical message types
├── agent/               # Agent invocation (invoke.py), dual runners, prompt composition, error handling
├── conversation/        # Conversation persistence (memory + Redis stores)
├── review/            # Review pipeline (reviewer, prompts, diff utilities, output parsing)
├── platforms/           # Platform protocol + GitHub/GitLab implementations, build_platforms(config)
└── workspace/           # Git workspace management
```
