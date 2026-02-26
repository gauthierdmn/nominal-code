# Configuration

The bot is configured entirely via environment variables. You can set them in a `.env` file or export them directly.

> **CLI mode** uses a subset of these variables. Bot usernames, `ALLOWED_USERS`, webhook host/port, and webhook secrets are not required. See [CLI Mode](cli.md) for details.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `WORKER_BOT_USERNAME` | No* | — | The `@mention` name for the worker bot |
| `REVIEWER_BOT_USERNAME` | No* | — | The `@mention` name for the reviewer bot |
| `ALLOWED_USERS` | Yes | — | Comma-separated usernames allowed to trigger the bot |
| `WEBHOOK_HOST` | No | `0.0.0.0` | Host to bind the server |
| `WEBHOOK_PORT` | No | `8080` | Port to bind the server |
| `GITHUB_TOKEN` | No** | — | GitHub API token for authentication |
| `GITHUB_WEBHOOK_SECRET` | No | — | HMAC secret for GitHub webhook verification |
| `GITHUB_REVIEWER_TOKEN` | No | — | Separate read-only GitHub token for reviewer bot clones |
| `GITLAB_TOKEN` | No** | — | GitLab API token for authentication |
| `GITLAB_WEBHOOK_SECRET` | No | — | Secret token for GitLab webhook verification |
| `GITLAB_BASE_URL` | No | `https://gitlab.com` | GitLab instance URL (for self-hosted) |
| `GITLAB_REVIEWER_TOKEN` | No | — | Separate read-only GitLab token for reviewer bot clones |
| `WORKSPACE_BASE_DIR` | No | System temp dir | Directory for cloning repos |
| `AGENT_MAX_TURNS` | No | `0` (unlimited) | Maximum agentic turns per invocation |
| `AGENT_MODEL` | No | SDK default | Model override (e.g. `claude-sonnet-4-6`) |
| `AGENT_CLI_PATH` | No | Bundled | Path to the `claude` CLI binary |
| `WORKER_SYSTEM_PROMPT` | No | `system_prompt.md` | Path to a system prompt file for the worker bot |
| `REVIEWER_SYSTEM_PROMPT` | No | `reviewer_prompt.md` | Path to a system prompt file for the reviewer bot |
| `CODING_GUIDELINES` | No | `coding_guidelines.md` | Path to a coding guidelines file appended to the system prompt |
| `CLEANUP_INTERVAL_HOURS` | No | `6` | Hours between workspace cleanup runs (`0` to disable) |
| `LOG_LEVEL` | No | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

\*At least one of `WORKER_BOT_USERNAME` or `REVIEWER_BOT_USERNAME` must be set. You can deploy worker-only, reviewer-only, or both.

\*\*At least one of `GITHUB_TOKEN` or `GITLAB_TOKEN` must be set.

## Prompt File Configuration

The bot loads system prompts and coding guidelines from files at startup.

### `WORKER_SYSTEM_PROMPT`

Path to the system prompt used when the worker bot runs the agent. Defaults to `system_prompt.md` in the project root.

### `REVIEWER_SYSTEM_PROMPT`

Path to the system prompt used when the reviewer bot runs the agent. Defaults to `reviewer_prompt.md` in the project root.

### `CODING_GUIDELINES`

Path to a coding guidelines file that gets appended to both the worker and reviewer system prompts. Defaults to `coding_guidelines.md`. This file is read once at startup and included in every agent invocation.

## Per-Repo Overrides

Repositories can include a `.nominal/guidelines.md` file at their root. When present, its contents are appended to the system prompt for that repository — allowing teams to specify project-specific conventions, frameworks, or review criteria without changing the global configuration.

## Reviewer Token Separation

By default, the reviewer bot clones repositories using the same token as the worker bot (`GITHUB_TOKEN` / `GITLAB_TOKEN`). If you want the reviewer to clone with a read-only token (recommended for security), set:

- `GITHUB_REVIEWER_TOKEN` — used for GitHub reviewer clone URLs
- `GITLAB_REVIEWER_TOKEN` — used for GitLab reviewer clone URLs

When set, the reviewer's clone URL uses this token instead, limiting its git-level access to read-only operations.

## Private Dependencies

Both bots can `git clone` private repositories into a shared `.deps/` directory inside the workspace. This is useful when a PR depends on internal libraries not available on PyPI — the agent can clone them to inspect source code for context.

- Dependencies are cloned with `--depth=1` to minimize download time.
- The `.deps/` directory is shared across PRs for the same repository, so a dependency only needs to be cloned once.
- The reviewer bot is restricted to read-only tools plus `git clone` — it cannot modify files in cloned dependencies.

## Workspace Cleanup

The bot clones repositories into `WORKSPACE_BASE_DIR`. Over time, workspaces for closed or merged PRs accumulate. A built-in cleaner handles this automatically:

- Runs once immediately on startup to remove stale workspaces left from a previous run.
- Then runs periodically in the background at the interval set by `CLEANUP_INTERVAL_HOURS`.

A workspace is deleted only when no configured platform reports the PR as open. If an API check fails, the workspace is kept as a safety measure. Empty parent directories are cleaned up as well.

Set `CLEANUP_INTERVAL_HOURS=0` to disable cleanup entirely.
