# Environment Variables

Canonical reference for all environment variables. Each variable is tagged with the modes where it applies: `webhook` `cli` `ci`.

## Authentication

### GitHub

| Variable | Modes | Default | Description |
|---|---|---|---|
| `GITHUB_TOKEN` | `webhook` `cli` | — | GitHub PAT for authentication. Either this or GitHub App credentials are required |
| `GITHUB_APP_ID` | `webhook` `cli` | — | GitHub App ID (used instead of `GITHUB_TOKEN`) |
| `GITHUB_APP_PRIVATE_KEY` | `webhook` `cli` | — | Inline PEM-encoded private key for the GitHub App |
| `GITHUB_APP_PRIVATE_KEY_PATH` | `webhook` `cli` | — | Path to a PEM private key file (alternative to inline) |
| `GITHUB_INSTALLATION_ID` | `cli` | — | GitHub App installation ID (required for CLI mode with App auth) |
| `GITHUB_WEBHOOK_SECRET` | `webhook` | — | HMAC secret for GitHub webhook verification |
| `GITHUB_REVIEWER_TOKEN` | `webhook` `cli` | — | Separate read-only token for reviewer bot clones (PAT mode only) |

When both a PAT and a GitHub App are configured, the App takes precedence. See [GitHub authentication](../platforms/github.md#authentication) for setup details.

### GitLab

| Variable | Modes | Default | Description |
|---|---|---|---|
| `GITLAB_TOKEN` | `webhook` `cli` | — | GitLab API token for authentication |
| `GITLAB_WEBHOOK_SECRET` | `webhook` | — | Secret token for GitLab webhook verification |
| `GITLAB_API_BASE` | `webhook` `cli` | `https://gitlab.com` | GitLab instance URL (for self-hosted) |
| `GITLAB_REVIEWER_TOKEN` | `webhook` `cli` | — | Separate read-only token for reviewer bot clones |

## Bot Identity

| Variable | Modes | Default | Description |
|---|---|---|---|
| `REVIEWER_BOT_USERNAME` | `webhook` | — | The `@mention` name for the reviewer bot |
| `WORKER_BOT_USERNAME` | `webhook` | — | The `@mention` name for the worker bot *(beta)* |
| `ALLOWED_USERS` | `webhook` | — | Comma-separated usernames allowed to trigger the bot |

At least one of `WORKER_BOT_USERNAME` or `REVIEWER_BOT_USERNAME` must be set in webhook mode. You can deploy worker-only, reviewer-only, or both.

## Server

| Variable | Modes | Default | Description |
|---|---|---|---|
| `WEBHOOK_HOST` | `webhook` | `0.0.0.0` | Host to bind the server |
| `WEBHOOK_PORT` | `webhook` | `8080` | Port to bind the server |

## Agent

| Variable | Modes | Default | Description |
|---|---|---|---|
| `AGENT_PROVIDER` | `webhook` `cli` `ci` | — | LLM provider name (`anthropic`, `openai`, `deepseek`, `groq`, `together`, `fireworks`). When set in webhook/CLI mode, uses the API runner instead of the Claude Code CLI |
| `AGENT_MODEL` | `webhook` `cli` `ci` | SDK/provider default | Model override (e.g. `claude-sonnet-4-6`, `gpt-4.1`) |
| `AGENT_MAX_TURNS` | `webhook` `cli` `ci` | `0` (unlimited) | Maximum agentic turns per invocation |
| `AGENT_CLI_PATH` | `webhook` `cli` | Bundled | Path to the `claude` CLI binary (ignored when `AGENT_PROVIDER` is set) |

## Prompts and Guidelines

| Variable | Modes | Default | Description |
|---|---|---|---|
| `REVIEWER_SYSTEM_PROMPT` | `webhook` `cli` | `reviewer_prompt.md` | Path to the reviewer bot system prompt file |
| `WORKER_SYSTEM_PROMPT` | `webhook` | `system_prompt.md` | Path to the worker bot system prompt file |
| `CODING_GUIDELINES` | `webhook` `cli` `ci` | `coding_guidelines.md` | Path to a coding guidelines file appended to the system prompt |
| `LANGUAGE_GUIDELINES_DIR` | `webhook` `cli` | `prompts/languages` | Directory containing language-specific guideline files (e.g. `python.md`) |

See [Prompt File Configuration](configuration.md#prompt-file-configuration) for how these files are loaded and composed.

## Behavior

| Variable | Modes | Default | Description |
|---|---|---|---|
| `ALLOWED_REPOS` | `webhook` | — | Comma-separated repository full names to process (e.g. `owner/repo-a,owner/repo-b`). When unset, all repos are accepted |
| `REVIEWER_TRIGGERS` | `webhook` | — | Comma-separated PR lifecycle events that auto-trigger the reviewer (e.g. `pr_opened,pr_push`) |
| `PR_TITLE_INCLUDE_TAGS` | `webhook` | — | Comma-separated allowlist of tags. Only events whose PR title contains `[tag]` are processed |
| `PR_TITLE_EXCLUDE_TAGS` | `webhook` | — | Comma-separated blocklist of tags. Events whose PR title contains `[tag]` are skipped |
| `CLEANUP_INTERVAL_HOURS` | `webhook` | `6` | Hours between workspace cleanup runs (`0` to disable) |
| `WORKSPACE_BASE_DIR` | `webhook` `cli` | System temp dir | Directory for cloning repos |

See [Repository Filtering](configuration.md#repository-filtering), [Auto-Trigger](configuration.md#auto-trigger), and [PR Title Tag Filtering](configuration.md#pr-title-tag-filtering) for rules and examples.

## CI Inputs

CI mode reads its configuration from action inputs (mapped to `INPUT_*` environment variables) and CI-provided variables. These are separate from the webhook/CLI variables.

| Variable | Modes | Source | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | `ci` | Secret | API key for the Anthropic provider |
| `OPENAI_API_KEY` | `ci` | Secret | API key for the OpenAI provider |
| `DEEPSEEK_API_KEY` | `ci` | Secret | API key for the DeepSeek provider |
| `GROQ_API_KEY` | `ci` | Secret | API key for the Groq provider |
| `TOGETHER_API_KEY` | `ci` | Secret | API key for the Together provider |
| `FIREWORKS_API_KEY` | `ci` | Secret | API key for the Fireworks provider |
| `INPUT_MODEL` | `ci` | Action/template input | Model override |
| `INPUT_MAX_TURNS` | `ci` | Action/template input | Maximum agentic turns |
| `INPUT_PROMPT` | `ci` | Action/template input | Custom review instructions |
| `INPUT_CODING_GUIDELINES` | `ci` | Action/template input | Path to coding guidelines file |
| `GITHUB_TOKEN` | `ci` | Secret / CI | GitHub token (required for GitHub CI) |
| `GITLAB_TOKEN` | `ci` | Secret / CI | GitLab token (required for GitLab CI) |
| `GITHUB_EVENT_PATH` | `ci` | GitHub Actions | Path to event payload JSON |
| `GITHUB_WORKSPACE` | `ci` | GitHub Actions | Repository checkout path |
| `CI_PROJECT_PATH` | `ci` | GitLab CI | Repository path |
| `CI_MERGE_REQUEST_IID` | `ci` | GitLab CI | Merge request IID |
| `CI_MERGE_REQUEST_SOURCE_BRANCH_NAME` | `ci` | GitLab CI | Source branch name |
| `CI_PROJECT_DIR` | `ci` | GitLab CI | Repository checkout path |
| `CI_SERVER_URL` | `ci` | GitLab CI | GitLab instance URL (self-hosted) |

See [CI Mode](../modes/ci.md) for full setup instructions.

## Logging

| Variable | Modes | Default | Description |
|---|---|---|---|
| `LOG_LEVEL` | `webhook` `cli` `ci` | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
