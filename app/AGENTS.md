# nominal-code

AI-powered code review bot that monitors GitHub PRs and GitLab MRs. When a user @mentions a bot in a comment, or when a lifecycle event fires (PR opened, pushed, etc.), the bot spins up an LLM agent to review code or apply fixes and posts the results back to the platform.

## Architecture

- **Async-first** — built on aiohttp + asyncio; all I/O (HTTP, git, agent) is non-blocking.
- **Protocol-based platforms** — GitHub and GitLab implement the same `Platform` / `ReviewerPlatform` protocols, making it easy to add new providers.
- **Per-PR job serialisation** — `JobQueue` protocol with two implementations (`AsyncioJobQueue` for in-process, `RedisJobQueue` for Kubernetes) guarantees only one agent job runs per PR at a time, preventing race conditions on the same workspace.
- **Multi-turn conversations** — `ConversationStore` maps (platform, repo, PR, bot) to conversation IDs and message histories so conversations resume across comments.
- **Workspace isolation** — each PR gets its own shallow clone; a shared `.deps/` directory is available for cross-PR dependencies.
- **Dual agent runners** — CLI and webhook modes use the Claude Code CLI (supports subscriptions); CI mode calls the LLM provider API directly (requires a provider API key).
- **LLM cost tracking** — both runners capture token usage and compute dollar costs using a bundled pricing table. Cost summaries are logged after each review and surfaced in CI output.

## Entry points

- `nominal-code` (no args) — starts the webhook server.
- `nominal-code review owner/repo#42` — one-shot CLI review (supports `--dry-run`, `--platform`, `--prompt`).
- `nominal-code ci {platform}` — CI mode review (GitHub Actions / GitLab CI). Uses the LLM provider API runner.

## Agent runner selection

| Mode | Runner | Config flag |
|------|--------|-------------|
| Webhook server | Claude Code CLI (`agent/cli/runner.py`) | `CliAgentConfig` |
| CLI review | Claude Code CLI (`agent/cli/runner.py`) | `CliAgentConfig` |
| CI (`commands/ci.py`) | LLM provider API (`agent/api/runner.py`) | `ApiAgentConfig` |

The dispatcher in `agent/router.py` routes based on the agent config type (`CliAgentConfig` or `ApiAgentConfig`). The API runner implements its own tool execution (Read, Glob, Grep, Bash with allowlist) and supports multiple providers (Anthropic, OpenAI, Google Gemini, DeepSeek, Groq, Together, Fireworks).

## Bot types

| Bot | Purpose | Tool restrictions |
|-----|---------|-------------------|
| **Reviewer** | Posts structured code reviews with inline comments | `Read`, `Glob`, `Grep`, `Bash(git clone*)` |
| **Worker** | Applies code fixes, pushes commits | Unrestricted |

## Guideline resolution

1. Load repo-level `.nominal/guidelines.md` (overrides built-in `prompts/coding_guidelines.md`).
2. Detect languages from changed file extensions.
3. Load per-language `.nominal/languages/{lang}.md` (overrides built-in `prompts/languages/{lang}.md`).
4. Concatenate general + language-specific guidelines into the system prompt.

## Environment variables

### Required (webhook mode)

- `ALLOWED_USERS` — comma-separated usernames authorised to trigger jobs.
- At least one of `WORKER_BOT_USERNAME` / `REVIEWER_BOT_USERNAME`.
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
- `AGENT_MODEL`, `AGENT_MAX_TURNS`, `AGENT_CLI_PATH` — agent configuration.
- `ALLOWED_REPOS` — comma-separated repository full names to process (e.g. `owner/repo-a,owner/repo-b`). When unset, all repos are accepted.
- `REVIEWER_TRIGGERS` — comma-separated lifecycle events that auto-trigger the reviewer (e.g. `pr_opened,pr_push`).
- `CLEANUP_INTERVAL_HOURS` — workspace cleanup frequency (default: `6`; `0` disables).
- `LOG_LEVEL` — logging verbosity (default: `INFO`).

## File tree

```
nominal_code/
├── main.py              # Entry point: dispatches to webhook server, CLI, or CI
├── config.py            # Frozen dataclass config loaded from env vars / files
├── models.py            # Shared enums (EventType, BotType, FileStatus) and dataclasses (ReviewFinding, AgentReview, ChangedFile)
├── http.py              # request_with_retry(): HTTP request helper with transient error retries
├── commands/            # Entry points: CLI review, CI mode, job runner
├── llm/                 # LLM provider abstraction, cost tracking, canonical message types
├── agent/               # Dual agent runners, prompt composition, error handling
├── conversation/        # Conversation persistence (memory + Redis stores)
├── handlers/            # Bot handlers: reviewer (structured review) and worker (code fixes)
├── server/              # aiohttp webhook server, @mention extraction, job dispatch
├── jobs/                # Job payload, process runner, Kubernetes runner
├── platforms/           # Platform protocol + GitHub/GitLab implementations (subpackages)
└── workspace/           # Git workspace management and cleanup
```
