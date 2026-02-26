# nominal-code

AI-powered code review bot that monitors GitHub PRs and GitLab MRs. When a user @mentions a bot in a comment, or when a lifecycle event fires (PR opened, pushed, etc.), the bot spins up a Claude agent to review code or apply fixes and posts the results back to the platform.

## Architecture

- **Async-first** — built on aiohttp + asyncio; all I/O (HTTP, git, agent) is non-blocking.
- **Protocol-based platforms** — GitHub and GitLab implement the same `Platform` / `ReviewerPlatform` protocols, making it easy to add new providers.
- **Per-PR job serialisation** — `SessionQueue` guarantees only one agent job runs per PR at a time, preventing race conditions on the same workspace.
- **Multi-turn sessions** — `SessionStore` maps (platform, repo, PR, bot) to a Claude session ID so conversations resume across comments.
- **Workspace isolation** — each PR gets its own shallow clone; a shared `.deps/` directory is available for cross-PR dependencies.

## Supported platforms

| Platform | Webhook events | Auth | Self-hosted |
|----------|---------------|------|-------------|
| GitHub | `issue_comment`, `pull_request_review_comment`, `pull_request_review`, `pull_request` | Token + HMAC-SHA256 | No |
| GitLab | Note Hook, Merge Request Hook | Token + header secret | Yes (`GITLAB_BASE_URL`) |

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

## Entry points

- `nominal-code` (no args) — starts the webhook server.
- `nominal-code review owner/repo#42` — one-shot CLI review (supports `--dry-run`, `--platform`, `--prompt`).

## Environment variables

### Required (webhook mode)

- `ALLOWED_USERS` — comma-separated usernames authorised to trigger jobs.
- At least one of `WORKER_BOT_USERNAME` / `REVIEWER_BOT_USERNAME`.
- `GITHUB_TOKEN` and/or `GITLAB_TOKEN`.

### Optional

- `GITHUB_WEBHOOK_SECRET` / `GITLAB_WEBHOOK_SECRET` — webhook signature validation.
- `GITHUB_REVIEWER_TOKEN` / `GITLAB_REVIEWER_TOKEN` — read-only token for reviewer clones.
- `WORKSPACE_BASE_DIR` — workspace root (default: `/tmp/nominal-code`).
- `AGENT_MODEL`, `AGENT_MAX_TURNS`, `AGENT_CLI_PATH` — agent configuration.
- `REVIEWER_TRIGGERS` — comma-separated lifecycle events that auto-trigger the reviewer (e.g. `pr_opened,pr_push`).
- `CLEANUP_INTERVAL_HOURS` — workspace cleanup frequency (default: `6`; `0` disables).
- `LOG_LEVEL` — logging verbosity (default: `INFO`).

## File tree

```
nominal_code/
├── main.py              # Entry point: dispatches to webhook server or CLI
├── cli.py               # One-shot review CLI (argparse, platform construction)
├── config.py            # Frozen dataclass config loaded from env vars / files
├── models.py            # Shared enums (EventType, BotType, FileStatus) and dataclasses (ReviewFinding, AgentReview, ChangedFile)
├── agent/               # Agent invocation, session management, prompt composition
├── platforms/           # Platform protocol + GitHub/GitLab implementations
├── review/              # Reviewer bot handler (structured code review)
├── webhooks/            # aiohttp webhook server, @mention extraction, job dispatch
├── worker/              # Worker bot handler (code fixes)
└── workspace/           # Git workspace management and cleanup
```
