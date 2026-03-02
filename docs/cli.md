# CLI Mode

Run a one-off code review on any pull request without deploying a webhook server. All you need is a platform token.

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
| `--max-turns` | — | `0` (unlimited) | Maximum agentic turns |
| `--dry-run` | — | `false` | Print results to stdout without posting to the PR |

## Environment Variables

CLI mode reads a subset of the environment variables used by the webhook server. Bot usernames, `ALLOWED_USERS`, and webhook secrets are not required.

| Variable | Required | Description |
|---|---|---|
| `GITHUB_TOKEN` | Yes (for GitHub)* | GitHub PAT |
| `GITHUB_APP_ID` | Yes (for GitHub)* | GitHub App ID |
| `GITHUB_APP_PRIVATE_KEY` | No | Inline PEM private key for the GitHub App |
| `GITHUB_APP_PRIVATE_KEY_PATH` | No | Path to PEM private key file |
| `GITHUB_INSTALLATION_ID` | Yes (with App auth) | GitHub App installation ID |
| `GITLAB_TOKEN` | Yes (for GitLab) | GitLab API token |
| `GITLAB_BASE_URL` | No | GitLab instance URL (default: `https://gitlab.com`) |
| `REVIEWER_SYSTEM_PROMPT` | No | Path to a system prompt file |
| `CODING_GUIDELINES` | No | Path to a coding guidelines file |
| `LANGUAGE_GUIDELINES_DIR` | No | Path to language-specific guidelines directory |
| `WORKSPACE_BASE_DIR` | No | Directory for cloning repos (default: system temp) |
| `AGENT_MODEL` | No | Default agent model (overridden by `--model`) |
| `AGENT_MAX_TURNS` | No | Default max turns (overridden by `--max-turns`) |
| `AGENT_CLI_PATH` | No | Path to the `claude` CLI binary |
| `LOG_LEVEL` | No | Python log level (default: `INFO`) |

## Examples

\*Either `GITHUB_TOKEN` or `GITHUB_APP_ID` + private key is required for GitHub. When using App auth in CLI mode, `GITHUB_INSTALLATION_ID` is also required.

### Basic review (PAT)

```bash
export GITHUB_TOKEN=ghp_...
uv run nominal-code review myorg/myrepo#123
```

### Basic review (GitHub App)

```bash
export GITHUB_APP_ID=12345
export GITHUB_APP_PRIVATE_KEY_PATH=/path/to/private-key.pem
export GITHUB_INSTALLATION_ID=67890
uv run nominal-code review myorg/myrepo#123
```

### Dry run with custom prompt

```bash
uv run nominal-code review myorg/myrepo#123 --dry-run --prompt "focus on error handling"
```

### GitLab review

```bash
export GITLAB_TOKEN=glpat-...
uv run nominal-code review mygroup/myproject#10 --platform gitlab
```

### Self-hosted GitLab

```bash
export GITLAB_TOKEN=glpat-...
export GITLAB_BASE_URL=https://gitlab.internal.company.com
uv run nominal-code review mygroup/myproject#10 --platform gitlab
```

### Model override

```bash
uv run nominal-code review myorg/myrepo#42 --model claude-sonnet-4-6 --max-turns 5
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

## How It Differs from Webhook Mode

| | CLI Mode | Webhook Mode |
|---|---|---|
| Trigger | Manual command | PR comment via webhook |
| Server | Not needed | aiohttp server required |
| Auth check | None (you are the user) | `ALLOWED_USERS` allowlist |
| Session continuity | No (one-shot) | Yes (multi-turn per PR) |
| Workspace cleanup | Manual | Automatic periodic cleanup |
| Bot username | Not needed | Required for `@mention` |
