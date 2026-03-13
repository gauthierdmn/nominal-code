# Configuration

Nominal Code supports two configuration methods: a **YAML config file** (recommended for webhook and Kubernetes deployments) and **environment variables** (for secrets, CI-provided values, and simple setups). Both can be used together — environment variables always override the YAML file.

For the full environment variable reference, see [Environment Variables](env-vars.md).

## YAML Config File

The YAML file is the primary way to configure the webhook server. It replaces the flat list of environment variables with a structured, reviewable file that works well with kustomize overlays and GitOps workflows.

### Loading

The app looks for a config file in this order:

1. The path in the `CONFIG_PATH` environment variable (if set)
2. `config.yaml` in the current working directory (if it exists)
3. No file — all settings come from defaults and environment variables

### Full Schema

```yaml
# config.yaml
webhook:
  host: "0.0.0.0"
  port: 8080

worker:
  bot_username: ""
  system_prompt_path: "prompts/system_prompt.md"

reviewer:
  bot_username: "nominalbot"
  system_prompt_path: "prompts/reviewer_prompt.md"
  triggers:
    - pr_opened

agent:
  provider: "google"
  model: ""
  max_turns: 0
  cli_path: ""

access:
  allowed_users:
    - alice
    - bob
  allowed_repos: []
  pr_title_include_tags: []
  pr_title_exclude_tags: []

workspace:
  base_dir: "/tmp/nominal-code"

prompts:
  coding_guidelines_path: "prompts/coding_guidelines.md"
  language_guidelines_dir: "prompts/languages"

redis:
  url: "redis://redis:6379/0"
  key_ttl_seconds: 86400

kubernetes:
  image: "your-registry.com/nominal-code:latest"
  namespace: "default"
  service_account: ""
  image_pull_policy: ""
  backoff_limit: 0
  active_deadline_seconds: 600
  ttl_after_finished: 3600
  env_from_secrets: []
  resources:
    requests:
      cpu: ""
      memory: ""
    limits:
      cpu: ""
      memory: ""
```

All sections and fields are optional — omitted fields use the defaults shown above.

### Minimal Examples

=== "Webhook (reviewer only)"

    ```yaml
    reviewer:
      bot_username: "nominalbot"
      triggers:
        - pr_opened

    access:
      allowed_users:
        - alice
        - bob

    agent:
      provider: "google"
    ```

=== "Webhook + Kubernetes"

    ```yaml
    reviewer:
      bot_username: "nominalbot"
      triggers:
        - pr_opened

    agent:
      provider: "google"

    redis:
      url: "redis://redis:6379/0"

    kubernetes:
      image: "your-registry.com/nominal-code:latest"
      namespace: "nominal-code"
      env_from_secrets:
        - "nominal-code-secrets"
    ```

=== "CLI / local dev"

    ```yaml
    agent:
      provider: "anthropic"
      model: "claude-sonnet-4-6"
      max_turns: 10

    workspace:
      base_dir: "/tmp/nominal-code"
    ```

## How Configuration Is Loaded

| Source | Priority | Notes |
|---|---|---|
| Model defaults | Lowest | Built-in defaults for every field |
| YAML config file | Medium | Static config loaded from `CONFIG_PATH` or `config.yaml` |
| Environment variables | Highest | Always override YAML — use for secrets, CI-provided vars, and runtime tuning |
| CLI flags (`--prompt`, `--model`, etc.) | Highest | CLI mode only — override everything |
| Action/template inputs | Highest | CI mode only — mapped to `INPUT_*` env vars |
| Per-repo `.nominal/` files | Runtime | Override global prompt/guidelines per-repository |

**Env-only mode** (no YAML file) works identically to the legacy behavior. All legacy flat environment variable names are still supported — see [Environment Variables](env-vars.md).

## Mode Comparison

| | CI Mode | CLI Mode | Webhook Mode |
|---|---|---|---|
| **Trigger** | PR event in CI pipeline | Manual command | PR comment via webhook |
| **Config file** | Not used (CI inputs + env vars) | Optional | Recommended |
| **Agent runner** | LLM provider API (direct) | Claude Code CLI | Claude Code CLI |
| **Requires Claude Code CLI** | No | Yes | Yes |
| **Billing** | Provider API key (per-token) | Claude Code CLI login (Pro/Max or API key) | Claude Code CLI login (Pro/Max or API key) |
| **Server** | Not needed | Not needed | aiohttp server required |
| **Auth check** | None (CI handles it) | None (you are the user) | `ALLOWED_USERS` allowlist |
| **Conversation continuity** | No (one-shot) | No (one-shot) | Yes (multi-turn per PR) |
| **Cost tracking** | Yes (logged + CI output) | Yes (logged) | Yes (logged) |
| **Workspace** | CI runner checkout | Cloned to temp dir | Cloned to `WORKSPACE_BASE_DIR` |
| **Workspace cleanup** | N/A (CI runner) | Manual | Manual (K8s uses ephemeral pods) |
| **Bot username** | Not needed | Not needed | Required for `@mention` |

## Prompt File Configuration

The bot loads system prompts and coding guidelines from files at startup. Paths in the YAML file or environment variables point to these files.

### Worker system prompt

YAML: `worker.system_prompt_path` / Env: `WORKER_SYSTEM_PROMPT`

Path to the system prompt used when the worker bot runs the agent. Defaults to `prompts/system_prompt.md`.

### Reviewer system prompt

YAML: `reviewer.system_prompt_path` / Env: `REVIEWER_SYSTEM_PROMPT`

Path to the system prompt used when the reviewer bot runs the agent. Defaults to `prompts/reviewer_prompt.md`.

### Coding guidelines

YAML: `prompts.coding_guidelines_path` / Env: `CODING_GUIDELINES`

Path to a coding guidelines file that gets appended to both the worker and reviewer system prompts. Defaults to `prompts/coding_guidelines.md`. This file is read once at startup and included in every agent invocation.

### Language guidelines directory

YAML: `prompts.language_guidelines_dir` / Env: `LANGUAGE_GUIDELINES_DIR`

Path to a directory containing language-specific guideline files. Each file should be named `{language}.md` (e.g. `python.md`). When the PR diff contains files matching a known language, the corresponding guideline file is appended to the system prompt. Defaults to `prompts/languages`.

## Per-Repo Overrides

Repositories can override the global guidelines by placing files in a `.nominal/` directory at the repository root. Per-repo overrides take priority over the built-in defaults.

### General guidelines

A `.nominal/guidelines.md` file replaces the global coding guidelines for that repository. When present, the built-in guidelines are **not** appended — the repo file is used exclusively.

### Language-specific guidelines

A `.nominal/languages/{language}.md` file (e.g. `.nominal/languages/python.md`) replaces the built-in language guideline for that language.

## Repository Filtering

!!! tip "Policy reference"
    The fields in `access` and `reviewer.triggers` map to two internal Pydantic models: `FilteringPolicy` and `RoutingPolicy`. For programmatic usage and multi-tenant override patterns, see **[Policies](policies.md)**.

Restrict which repositories the bot processes using `access.allowed_repos` in YAML or `ALLOWED_REPOS` as an env var:

=== "YAML"

    ```yaml
    access:
      allowed_repos:
        - owner/repo-a
        - owner/repo-b
    ```

=== "Env var"

    ```bash
    ALLOWED_REPOS=owner/repo-a,owner/repo-b
    ```

**Rules:**

- When set, only events from the listed repositories are processed. Events from unlisted repositories are silently filtered out.
- When unset or empty, all repositories are accepted (backward compatible).
- Repository names must match exactly (e.g. `owner/repo`), and are case-sensitive.

## Auto-Trigger

The reviewer bot can run automatically on PR lifecycle events without requiring an `@mention`. Set `reviewer.triggers` in YAML or `REVIEWER_TRIGGERS` as an env var:

=== "YAML"

    ```yaml
    reviewer:
      triggers:
        - pr_opened
        - pr_push
    ```

=== "Env var"

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

## PR Title Tag Filtering

Filter events based on tags in the PR/MR title. Tags are substrings enclosed in square brackets (e.g. `[nominalbot]`).

=== "YAML"

    ```yaml
    access:
      pr_title_include_tags:
        - nominalbot
      pr_title_exclude_tags:
        - skip
        - wip
    ```

=== "Env var"

    ```bash
    PR_TITLE_INCLUDE_TAGS=nominalbot
    PR_TITLE_EXCLUDE_TAGS=skip,wip
    ```

**Rules:**

- **Include tags** (allowlist) — when set, only events whose PR title contains at least one `[tag]` are processed.
- **Exclude tags** (blocklist) — events whose PR title contains any `[tag]` from this list are skipped.
- Exclude takes priority over include.
- Both empty = no filtering (backward compatible).
- Matching is case-insensitive.

| PR Title | Include Tags | Exclude Tags | Result |
|---|---|---|---|
| `feat: add login [nominalbot]` | `nominalbot` | — | Processed |
| `feat: add login` | `nominalbot` | — | Skipped |
| `feat: add login [skip]` | — | `skip` | Skipped |
| `feat: add login [nominalbot] [skip]` | `nominalbot` | `skip` | Skipped (exclude wins) |
| `feat: add login` | — | `skip` | Processed |

## Private Dependencies

Both bots can `git clone` private repositories into a shared `.deps/` directory inside the workspace. This is useful when a PR depends on internal libraries not available on PyPI — the agent can clone them to inspect source code for context.

- Dependencies are cloned with `--depth=1` to minimize download time.
- The `.deps/` directory is shared across PRs for the same repository, so a dependency only needs to be cloned once.
- The reviewer bot is restricted to read-only tools plus `git clone` — it cannot modify files in cloned dependencies.

## Workspace Management

The bot clones repositories into the workspace base directory (YAML: `workspace.base_dir`, env: `WORKSPACE_BASE_DIR`, default: system temp dir). Each PR gets its own shallow clone.

In production Kubernetes deployments, reviews run in ephemeral Job pods — no disk management is needed. For local or persistent-disk deployments, periodically remove stale `pr-{N}` directories manually.
