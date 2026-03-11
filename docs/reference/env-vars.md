# Environment Variables

Environment variables serve as **overrides** — they always take precedence over values in the [YAML config file](configuration.md#yaml-config-file). For webhook and Kubernetes deployments, the YAML file is the recommended primary configuration method. Environment variables remain the right choice for secrets, CI-provided values, and simple setups without a config file.

Each variable is tagged with the modes where it applies: `webhook` `cli` `ci`.

## Config File

| Variable | Modes | Default | Description |
|---|---|---|---|
| `CONFIG_PATH` | `webhook` `cli` | — | Path to a YAML config file. When unset, `config.yaml` in the current working directory is used if it exists |

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

These can also be set in the YAML config file under `reviewer`, `worker`, and `access`.

| Variable | YAML path | Modes | Default | Description |
|---|---|---|---|---|
| `REVIEWER_BOT_USERNAME` | `reviewer.bot_username` | `webhook` | — | The `@mention` name for the reviewer bot |
| `WORKER_BOT_USERNAME` | `worker.bot_username` | `webhook` | — | The `@mention` name for the worker bot *(beta)* |
| `ALLOWED_USERS` | `access.allowed_users` | `webhook` | — | Comma-separated usernames allowed to trigger the bot |

At least one of `WORKER_BOT_USERNAME` or `REVIEWER_BOT_USERNAME` must be set in webhook mode. You can deploy worker-only, reviewer-only, or both.

## Server

| Variable | YAML path | Modes | Default | Description |
|---|---|---|---|---|
| `WEBHOOK_HOST` | `webhook.host` | `webhook` | `0.0.0.0` | Host to bind the server |
| `WEBHOOK_PORT` | `webhook.port` | `webhook` | `8080` | Port to bind the server |

## Agent

| Variable | YAML path | Modes | Default | Description |
|---|---|---|---|---|
| `AGENT_PROVIDER` | `agent.provider` | `webhook` `cli` `ci` | — | LLM provider name (`anthropic`, `openai`, `google`, `deepseek`, `groq`, `together`, `fireworks`). When set in webhook/CLI mode, uses the API runner instead of the Claude Code CLI |
| `AGENT_MODEL` | `agent.model` | `webhook` `cli` `ci` | SDK/provider default | Model override (e.g. `claude-sonnet-4-6`, `gpt-4.1`) |
| `AGENT_MAX_TURNS` | `agent.max_turns` | `webhook` `cli` `ci` | `0` (unlimited) | Maximum agentic turns per invocation |
| `AGENT_CLI_PATH` | `agent.cli_path` | `webhook` `cli` | Bundled | Path to the `claude` CLI binary (ignored when `AGENT_PROVIDER` is set) |

## Prompts and Guidelines

| Variable | YAML path | Modes | Default | Description |
|---|---|---|---|---|
| `REVIEWER_SYSTEM_PROMPT` | `reviewer.system_prompt_path` | `webhook` `cli` | `prompts/reviewer_prompt.md` | Path to the reviewer bot system prompt file |
| `WORKER_SYSTEM_PROMPT` | `worker.system_prompt_path` | `webhook` | `prompts/system_prompt.md` | Path to the worker bot system prompt file |
| `CODING_GUIDELINES` | `prompts.coding_guidelines_path` | `webhook` `cli` `ci` | `prompts/coding_guidelines.md` | Path to a coding guidelines file appended to the system prompt |
| `LANGUAGE_GUIDELINES_DIR` | `prompts.language_guidelines_dir` | `webhook` `cli` | `prompts/languages` | Directory containing language-specific guideline files (e.g. `python.md`) |

See [Prompt File Configuration](configuration.md#prompt-file-configuration) for how these files are loaded and composed.

## Behavior

| Variable | YAML path | Modes | Default | Description |
|---|---|---|---|---|
| `ALLOWED_REPOS` | `access.allowed_repos` | `webhook` | — | Comma-separated repository full names to process (e.g. `owner/repo-a,owner/repo-b`). When unset, all repos are accepted |
| `REVIEWER_TRIGGERS` | `reviewer.triggers` | `webhook` | — | Comma-separated PR lifecycle events that auto-trigger the reviewer (e.g. `pr_opened,pr_push`) |
| `PR_TITLE_INCLUDE_TAGS` | `access.pr_title_include_tags` | `webhook` | — | Comma-separated allowlist of tags. Only events whose PR title contains `[tag]` are processed |
| `PR_TITLE_EXCLUDE_TAGS` | `access.pr_title_exclude_tags` | `webhook` | — | Comma-separated blocklist of tags. Events whose PR title contains `[tag]` are skipped |
| `CLEANUP_INTERVAL_HOURS` | `workspace.cleanup_interval_hours` | `webhook` | `6` | Hours between workspace cleanup runs (`0` to disable) |
| `WORKSPACE_BASE_DIR` | `workspace.base_dir` | `webhook` `cli` | System temp dir | Directory for cloning repos |

See [Repository Filtering](configuration.md#repository-filtering), [Auto-Trigger](configuration.md#auto-trigger), and [PR Title Tag Filtering](configuration.md#pr-title-tag-filtering) for rules and examples.

## Redis

| Variable | YAML path | Modes | Default | Description |
|---|---|---|---|---|
| `REDIS_URL` | `redis.url` | `webhook` | — | Redis connection URL. Required when using Kubernetes job runner. Used for job queue serialization, pub/sub completion, and conversation persistence |
| `REDIS_KEY_TTL_SECONDS` | `redis.key_ttl_seconds` | `webhook` | `86400` | TTL for Redis conversation keys in seconds |

## CI Inputs

CI mode reads its configuration from action inputs (mapped to `INPUT_*` environment variables) and CI-provided variables. These are **not** read from the YAML config file.

| Variable | Modes | Source | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | `ci` | Secret | API key for the Anthropic provider |
| `OPENAI_API_KEY` | `ci` | Secret | API key for the OpenAI provider |
| `DEEPSEEK_API_KEY` | `ci` | Secret | API key for the DeepSeek provider |
| `GROQ_API_KEY` | `ci` | Secret | API key for the Groq provider |
| `TOGETHER_API_KEY` | `ci` | Secret | API key for the Together provider |
| `FIREWORKS_API_KEY` | `ci` | Secret | API key for the Fireworks provider |
| `GOOGLE_API_KEY` | `ci` | Secret | API key for the Google provider |
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

## Kubernetes

These can also be set in the YAML config file under `kubernetes`. The `kubernetes` section in YAML replaces the need for `JOB_RUNNER=kubernetes` — when `kubernetes.image` is set, the Kubernetes job runner is automatically enabled.

| Variable | YAML path | Modes | Default | Description |
|---|---|---|---|---|
| `K8S_IMAGE` | `kubernetes.image` | `webhook` | — | Container image for Job pods. When set (via YAML or env var), enables the Kubernetes job runner |
| `K8S_NAMESPACE` | `kubernetes.namespace` | `webhook` | `default` | Namespace for spawned Job pods |
| `K8S_IMAGE_PULL_POLICY` | `kubernetes.image_pull_policy` | `webhook` | — | Image pull policy (`Always`, `Never`, `IfNotPresent`) |
| `K8S_SERVICE_ACCOUNT` | `kubernetes.service_account` | `webhook` | — | ServiceAccount for Job pods |
| `K8S_ENV_FROM_SECRETS` | `kubernetes.env_from_secrets` | `webhook` | — | Comma-separated Secret names to mount as env vars in Job pods |
| `K8S_BACKOFF_LIMIT` | `kubernetes.backoff_limit` | `webhook` | `0` | Job retry attempts |
| `K8S_ACTIVE_DEADLINE_SECONDS` | `kubernetes.active_deadline_seconds` | `webhook` | `600` | Per-job timeout in seconds |
| `K8S_TTL_AFTER_FINISHED` | `kubernetes.ttl_after_finished` | `webhook` | `3600` | Seconds before completed Jobs are cleaned up |
| `K8S_RESOURCE_REQUESTS_CPU` | `kubernetes.resources.requests.cpu` | `webhook` | — | CPU request for Job pods |
| `K8S_RESOURCE_REQUESTS_MEMORY` | `kubernetes.resources.requests.memory` | `webhook` | — | Memory request for Job pods |
| `K8S_RESOURCE_LIMITS_CPU` | `kubernetes.resources.limits.cpu` | `webhook` | — | CPU limit for Job pods |
| `K8S_RESOURCE_LIMITS_MEMORY` | `kubernetes.resources.limits.memory` | `webhook` | — | Memory limit for Job pods |

See [Kubernetes Deployment](../deployment/kubernetes.md) for the full setup guide.

## Logging

| Variable | Modes | Default | Description |
|---|---|---|---|
| `LOG_LEVEL` | `webhook` `cli` `ci` | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
