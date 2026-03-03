<p align="center">
  <img src="assets/nominal-code-banner.png" alt="Nominal Code" width="600">
</p>

<p align="center">
  <a href="https://github.com/gauthierdmn/nominal-code/actions/workflows/ci.yml"><img src="https://github.com/gauthierdmn/nominal-code/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://gauthierdmn.github.io/nominal-code/"><img src="https://github.com/gauthierdmn/nominal-code/actions/workflows/docs.yml/badge.svg" alt="Docs"></a>
  <a href="https://www.python.org/downloads/release/python-3130/"><img src="https://img.shields.io/badge/python-3.13-blue.svg" alt="Python 3.13"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License: Apache 2.0"></a>
</p>

Nominal Code is an AI-powered code review and fix agent for GitHub and GitLab pull requests. It uses Claude to read your diffs, post structured inline reviews, and optionally push fixes — all without leaving your PR.

It runs anywhere: as a **CI job** (GitHub Actions or GitLab CI), from the **command line**, or as a **self-hosted webhook server** for real-time interaction.

## What it does

Nominal Code ships two bots, each with a distinct role:

| | Reviewer | Worker |
|---|---|---|
| **Purpose** | Posts structured inline code reviews | Applies code changes and pushes commits |
| **Tool access** | Read-only (safe to run on any PR) | Full (clones, edits, commits, pushes) |
| **Output** | Review comments anchored to specific diff lines | Commits pushed to the PR branch |

Both bots accept a **custom prompt** to steer the review (e.g. *"focus on security"* or *"check for SQL injection"*), and respect **per-repo coding guidelines** placed in `.nominal/` at the root of your repository.

## How to run it

### CI job

The fastest way to get started. The example below uses GitHub Actions — GitLab CI is also supported (see [Configuration](docs/configuration.md)).

```yaml
# .github/workflows/review.yml
name: Code Review
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: gauthierdmn/nominal-code@main
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
```

You can also pass `model`, `max_turns`, `prompt`, and `coding_guidelines` as inputs.

> CI mode calls the Anthropic API directly and does not require the Claude Code CLI.

### CLI

Run a one-off review on any PR without deploying anything:

```bash
cd nominal-code/app && uv sync

export GITHUB_TOKEN=ghp_...

uv run nominal-code review owner/repo#42
uv run nominal-code review owner/repo#42 --dry-run
uv run nominal-code review owner/repo#42 --prompt "focus on security"
```

Supports `--platform`, `--model`, and `--max-turns`. Works with GitLab too (`--platform gitlab`).

### Webhook server

For teams that want real-time interaction — mention the bot in a PR comment and it responds:

```bash
cd nominal-code/app && uv sync

export REVIEWER_BOT_USERNAME=my-reviewer
export ALLOWED_USERS=alice,bob
export GITHUB_TOKEN=ghp_...
export GITHUB_WEBHOOK_SECRET=your-secret

uv run nominal-code
```

The server supports **GitHub App authentication** as an alternative to PATs, **auto-triggering** reviews on PR lifecycle events, and **multi-turn conversations** that carry context across comments. See [Getting Started](docs/getting-started.md) for the full setup.

## Configuration highlights

| What | How |
|---|---|
| Claude model | `AGENT_MODEL` env var, `--model` flag, or `model` Action input |
| Review prompt | `--prompt` flag, `INPUT_PROMPT` env var, or `prompt` Action input |
| Coding guidelines | Global via `CODING_GUIDELINES`, per-repo via `.nominal/guidelines.md` |
| Language-specific rules | `prompts/languages/` or `.nominal/languages/{lang}.md` per repo |
| Auto-trigger | `REVIEWER_TRIGGERS=pr_opened,pr_push,pr_reopened,pr_ready_for_review` |
| Allowed users | `ALLOWED_USERS=alice,bob` (webhook mode) |

Full reference: [Configuration](docs/configuration.md)

## Documentation

- [Getting Started](docs/getting-started.md) — from zero to a working bot
- [CLI Mode](docs/cli.md) — one-off reviews without a server
- [Configuration](docs/configuration.md) — environment variables and options
- [Architecture](docs/architecture.md) — request flow, agent runners, workspace layout
- [Deployment](docs/deployment.md) — production setup, Docker, health checks
- **Platforms:** [GitHub](docs/platforms/github.md) | [GitLab](docs/platforms/gitlab.md)
- **Bots:** [Reviewer](docs/bots/reviewer.md) | [Worker](docs/bots/worker.md)

## Development

```bash
cd app
uv sync

uv run ruff check nominal_code/ tests/
uv run ruff format nominal_code/ tests/
uv run mypy nominal_code/
uv run pytest
```

## Security

- Only users in `ALLOWED_USERS` can trigger the bots — other comments are silently ignored
- Webhook signatures are verified when secrets are configured
- GitHub App auth provides auto-rotating installation tokens
- The reviewer bot is restricted to read-only tools; the worker bot has full access
