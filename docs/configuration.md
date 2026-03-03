# Configuration

The bot is configured entirely via environment variables. You can set them in a `.env` file or export them directly.

> **CLI mode** uses a subset of these variables. Bot usernames, `ALLOWED_USERS`, webhook host/port, and webhook secrets are not required. See [CLI Mode](cli.md) for details.

> **CI mode** uses a different set of variables — inputs are passed through the GitHub Action or GitLab CI template, and CI-provided variables are read automatically. See [CI Mode](ci.md) for details.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `WORKER_BOT_USERNAME` | No* | — | The `@mention` name for the worker bot |
| `REVIEWER_BOT_USERNAME` | No* | — | The `@mention` name for the reviewer bot |
| `ALLOWED_USERS` | Yes | — | Comma-separated usernames allowed to trigger the bot |
| `WEBHOOK_HOST` | No | `0.0.0.0` | Host to bind the server |
| `WEBHOOK_PORT` | No | `8080` | Port to bind the server |
| `GITHUB_TOKEN` | No** | — | GitHub PAT for authentication |
| `GITHUB_APP_ID` | No** | — | GitHub App ID (used instead of `GITHUB_TOKEN`) |
| `GITHUB_APP_PRIVATE_KEY` | No | — | Inline PEM-encoded private key for the GitHub App |
| `GITHUB_APP_PRIVATE_KEY_PATH` | No | — | Path to a PEM private key file (alternative to inline) |
| `GITHUB_INSTALLATION_ID` | No | — | GitHub App installation ID (required for CLI mode with App auth) |
| `GITHUB_WEBHOOK_SECRET` | No | — | HMAC secret for GitHub webhook verification |
| `GITHUB_REVIEWER_TOKEN` | No | — | Separate read-only GitHub token for reviewer bot clones (PAT mode only) |
| `GITLAB_TOKEN` | No** | — | GitLab API token for authentication |
| `GITLAB_WEBHOOK_SECRET` | No | — | Secret token for GitLab webhook verification |
| `GITLAB_API_BASE` | No | `https://gitlab.com` | GitLab instance URL (for self-hosted) |
| `GITLAB_REVIEWER_TOKEN` | No | — | Separate read-only GitLab token for reviewer bot clones |
| `WORKSPACE_BASE_DIR` | No | System temp dir | Directory for cloning repos |
| `AGENT_MAX_TURNS` | No | `0` (unlimited) | Maximum agentic turns per invocation |
| `AGENT_MODEL` | No | SDK default | Model override (e.g. `claude-sonnet-4-20250514`) |
| `AGENT_CLI_PATH` | No | Bundled | Path to the `claude` CLI binary (webhook and CLI modes only) |
| `WORKER_SYSTEM_PROMPT` | No | `system_prompt.md` | Path to a system prompt file for the worker bot |
| `REVIEWER_SYSTEM_PROMPT` | No | `reviewer_prompt.md` | Path to a system prompt file for the reviewer bot |
| `CODING_GUIDELINES` | No | `coding_guidelines.md` | Path to a coding guidelines file appended to the system prompt |
| `LANGUAGE_GUIDELINES_DIR` | No | `prompts/languages` | Directory containing language-specific guideline files (e.g. `python.md`) |
| `CLEANUP_INTERVAL_HOURS` | No | `6` | Hours between workspace cleanup runs (`0` to disable) |
| `REVIEWER_TRIGGERS` | No | — | Comma-separated PR lifecycle events that auto-trigger the reviewer (e.g. `pr_opened,pr_push`) |
| `LOG_LEVEL` | No | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

\*At least one of `WORKER_BOT_USERNAME` or `REVIEWER_BOT_USERNAME` must be set. You can deploy worker-only, reviewer-only, or both.

\*\*At least one GitHub auth method (`GITHUB_TOKEN` or `GITHUB_APP_ID` + private key) or `GITLAB_TOKEN` must be set. When both a PAT and a GitHub App are configured, the App takes precedence.

## Prompt File Configuration

The bot loads system prompts and coding guidelines from files at startup.

### `WORKER_SYSTEM_PROMPT`

Path to the system prompt used when the worker bot runs the agent. Defaults to `system_prompt.md` in the project root.

### `REVIEWER_SYSTEM_PROMPT`

Path to the system prompt used when the reviewer bot runs the agent. Defaults to `reviewer_prompt.md` in the project root.

### `CODING_GUIDELINES`

Path to a coding guidelines file that gets appended to both the worker and reviewer system prompts. Defaults to `coding_guidelines.md`. This file is read once at startup and included in every agent invocation.

### `LANGUAGE_GUIDELINES_DIR`

Path to a directory containing language-specific guideline files. Each file should be named `{language}.md` (e.g. `python.md`). When the PR diff contains files matching a known language, the corresponding guideline file is appended to the system prompt. Defaults to `prompts/languages`.

Languages are detected from file extensions in the PR diff. Currently supported: `.py` and `.pyi` (Python).

## Per-Repo Overrides

Repositories can override the global guidelines by placing files in a `.nominal/` directory at the repository root. Per-repo overrides take priority over the built-in defaults.

### General guidelines

A `.nominal/guidelines.md` file replaces the global `CODING_GUIDELINES` for that repository. When present, the built-in guidelines are **not** appended — the repo file is used exclusively.

### Language-specific guidelines

A `.nominal/languages/{language}.md` file (e.g. `.nominal/languages/python.md`) replaces the built-in language guideline for that language. This allows teams to specify project-specific coding conventions per language without changing the global configuration.

## Auto-Trigger

The reviewer bot can be configured to automatically run on PR lifecycle events, without requiring an `@mention` in a comment. Set `REVIEWER_TRIGGERS` to a comma-separated list of event types:

```bash
REVIEWER_TRIGGERS=pr_opened,pr_push
```

| Event Type | GitHub Source | GitLab Source |
|---|---|---|
| `pr_opened` | PR opened | MR opened |
| `pr_push` | New commits pushed to PR | MR updated with new commits |
| `pr_reopened` | PR reopened | MR reopened |
| `pr_ready_for_review` | PR marked ready (was draft) | _(not available)_ |

When unset or empty, auto-triggering is disabled and the reviewer only responds to `@mentions` (backward compatible).

Auto-triggered reviews skip the `ALLOWED_USERS` check since there is no comment author. Draft PRs on GitHub and WIP merge requests on GitLab are automatically skipped.

## Reviewer Token Separation

By default, the reviewer bot clones repositories using the same token as the worker bot (`GITHUB_TOKEN` / `GITLAB_TOKEN`). If you want the reviewer to clone with a read-only token (recommended for security), set:

- `GITHUB_REVIEWER_TOKEN` — used for GitHub reviewer clone URLs (PAT mode only)
- `GITLAB_REVIEWER_TOKEN` — used for GitLab reviewer clone URLs

When set, the reviewer's clone URL uses this token instead, limiting its git-level access to read-only operations.

> **Note:** When using GitHub App authentication, reviewer permissions are scoped through the App's installation settings in GitHub. A separate reviewer token is not needed.

## Private Dependencies

Both bots can `git clone` private repositories into a shared `.deps/` directory inside the workspace. This is useful when a PR depends on internal libraries not available on PyPI — the agent can clone them to inspect source code for context.

- Dependencies are cloned with `--depth=1` to minimize download time.
- The `.deps/` directory is shared across PRs for the same repository, so a dependency only needs to be cloned once.
- The reviewer bot is restricted to read-only tools plus `git clone` — it cannot modify files in cloned dependencies.

## Agent Runner

Nominal Code uses two agent execution backends depending on the execution mode:

| Mode | Agent Runner | Key |
|---|---|---|
| **CI** (`nominal-code ci`) | Anthropic API (direct) | `ANTHROPIC_API_KEY` required |
| **CLI** (`nominal-code review`) | Claude Code CLI | Claude Code CLI required on `PATH` |
| **Webhook server** | Claude Code CLI | Claude Code CLI required on `PATH` |

In CI mode, the bot calls the Anthropic Messages API directly with tool use. It provides four tools locally (Read, Glob, Grep, Bash) and does not need the Claude Code CLI installed. This is controlled internally by the `use_api` flag in `AgentConfig`.

In CLI and webhook modes, the bot spawns the Claude Code CLI as a subprocess via the [Claude Code SDK](https://github.com/anthropics/claude-code-sdk-python). The CLI uses its configured login method — including **Claude Pro** and **Claude Max** subscriptions — so reviews can run against your subscription instead of per-token API billing. The CLI runner also supports session continuity for multi-turn conversations.

## CI Mode Variables

CI mode reads its configuration from action inputs (mapped to `INPUT_*` environment variables) and CI-provided variables. These are separate from the webhook/CLI variables above.

| Variable | Source | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Secret | Anthropic API key (required in CI mode) |
| `INPUT_MODEL` | Action/template input | Claude model override |
| `INPUT_MAX_TURNS` | Action/template input | Maximum agentic turns |
| `INPUT_PROMPT` | Action/template input | Custom review instructions |
| `INPUT_CODING_GUIDELINES` | Action/template input | Path to coding guidelines file |
| `GITHUB_EVENT_PATH` | GitHub Actions | Path to event payload JSON |
| `GITHUB_WORKSPACE` | GitHub Actions | Repository checkout path |
| `CI_PROJECT_PATH` | GitLab CI | Repository path |
| `CI_MERGE_REQUEST_IID` | GitLab CI | Merge request IID |
| `CI_MERGE_REQUEST_SOURCE_BRANCH_NAME` | GitLab CI | Source branch name |
| `CI_PROJECT_DIR` | GitLab CI | Repository checkout path |
| `CI_SERVER_URL` | GitLab CI | GitLab instance URL (self-hosted) |

See [CI Mode](ci.md) for full setup instructions.

## Workspace Cleanup

The bot clones repositories into `WORKSPACE_BASE_DIR`. Over time, workspaces for closed or merged PRs accumulate. A built-in cleaner handles this automatically:

- Runs once immediately on startup to remove stale workspaces left from a previous run.
- Then runs periodically in the background at the interval set by `CLEANUP_INTERVAL_HOURS`.

A workspace is deleted only when no configured platform reports the PR as open. If an API check fails, the workspace is kept as a safety measure. Empty parent directories are cleaned up as well.

Set `CLEANUP_INTERVAL_HOURS=0` to disable cleanup entirely.
