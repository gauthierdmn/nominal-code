# CLI Mode

Run a one-off code review on any pull request without deploying a webhook server. All you need is a platform token and the [Claude Code CLI](https://claude.ai/code) installed.

!!! note "Billing"
    CLI mode uses the Claude Code CLI as its agent runner. It uses the authentication method configured on your Claude Code CLI, which means it can leverage **Claude Pro** and **Claude Max** subscriptions instead of paying per-token via the Anthropic API. For a lighter setup that only needs an API key (no CLI installation), see [CI Mode](ci.md).

## Usage

```bash
cd app
uv run nominal-code review owner/repo#42
```

The CLI resolves the PR branch, clones the repository, fetches the diff, runs the AI agent, and prints a structured review to stdout. By default it also posts the review to the PR.

## Options

| Flag | Short | Default | Description |
|---|---|---|---|
| `pr_ref` | — | (required) | PR reference in `owner/repo#number` format |
| `--prompt` | `-p` | — | Custom review instructions |
| `--platform` | — | `github` | Platform type (`github` or `gitlab`) |
| `--model` | — | SDK default | Agent model override (e.g. `claude-sonnet-4-6`) |
| `--provider` | — | — | LLM provider (e.g. `anthropic`, `openai`, `google`). When set, uses the API runner instead of the Claude Code CLI |
| `--dry-run` | — | `false` | Print results to stdout without posting to the PR |

## Examples

=== "GitHub (PAT)"

    ```bash
    export GITHUB_TOKEN=ghp_...
    uv run nominal-code review myorg/myrepo#123
    ```

=== "GitHub (App)"

    ```bash
    export GITHUB_APP_ID=12345
    export GITHUB_APP_PRIVATE_KEY_PATH=/path/to/private-key.pem
    export GITHUB_INSTALLATION_ID=67890
    uv run nominal-code review myorg/myrepo#123
    ```

=== "GitLab"

    ```bash
    export GITLAB_TOKEN=glpat-...
    uv run nominal-code review mygroup/myproject#10 --platform gitlab
    ```

=== "Self-Hosted GitLab"

    ```bash
    export GITLAB_TOKEN=glpat-...
    export GITLAB_API_BASE=https://gitlab.internal.company.com
    uv run nominal-code review mygroup/myproject#10 --platform gitlab
    ```

### Dry Run with Custom Prompt

```bash
uv run nominal-code review myorg/myrepo#123 --dry-run --prompt "focus on error handling"
```

### Model Override

```bash
uv run nominal-code review myorg/myrepo#42 --model claude-sonnet-4-6
```

## Output

The CLI prints a structured review to stdout:

```
Summary: Found 2 issues in the authentication module.

Findings (2):

  src/auth.py:42
    The password hash comparison is not constant-time. Use hmac.compare_digest().

  src/auth.py:58
    Missing rate limiting on the login endpoint.
```

When `--dry-run` is not set, the review is also posted to the PR as native inline comments (on GitHub) or discussion notes (on GitLab).

## What's Different

CLI mode is a one-shot command — no server, no conversation continuity. It does not require bot usernames or `ALLOWED_USERS`. See the [mode comparison](../reference/configuration.md#mode-comparison) for a full breakdown.

For the complete list of environment variables, see [Environment Variables](../reference/env-vars.md).
