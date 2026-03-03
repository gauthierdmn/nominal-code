# Nominal Code

An AI-powered code review and fix agent for GitHub and GitLab pull requests. It uses Claude to read your diffs, post structured inline reviews, and optionally push fixes — all without leaving your PR.

It runs anywhere: as a **CI job** (GitHub Actions or GitLab CI), from the **command line**, or as a **self-hosted webhook server** for real-time interaction.

## Features

- **Worker bot** — receives a prompt, clones the repo, runs an agent with full tool access, commits and pushes changes
- **Reviewer bot** — fetches the PR diff, runs an agent with read-only tools, posts structured inline code reviews
- **Three execution modes** — CI job, CLI one-off, or webhook server
- **Two agent runners** — Anthropic API (direct, used in CI) or Claude Code CLI (used in CLI and webhook modes)
- **GitHub and GitLab** — supports both platforms simultaneously
- **Session continuity** — multi-turn conversations within the same PR (webhook mode)
- **Auto-trigger** — run reviews automatically on PR open, push, reopen, or ready-for-review events
- **Automatic cleanup** — stale workspaces for closed/merged PRs are removed periodically
- **Per-repo guidelines** — coding standards via `.nominal/guidelines.md` and `.nominal/languages/{lang}.md`
- **Private dependencies** — agents can clone internal libraries for context

## Quick Start

### CI Job (fastest)

Add a GitHub Actions workflow — no server, no CLI installation required:

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

CI mode calls the Anthropic API directly and does not require the Claude Code CLI. See [CI Mode](ci.md) for GitLab CI setup and all options.

### CLI (no server required)

```bash
git clone https://github.com/gauthierdmn/nominal-code.git
cd nominal-code/app
uv sync

export GITHUB_TOKEN=ghp_...

# Review any PR
uv run nominal-code review owner/repo#42

# Dry run (print results without posting)
uv run nominal-code review owner/repo#42 --dry-run

# Custom instructions
uv run nominal-code review owner/repo#42 --prompt "focus on security"
```

See [CLI Mode](cli.md) for all options.

### Webhook Server

```bash
cd nominal-code/app
uv sync

# Configure (see configuration.md for all options)
export REVIEWER_BOT_USERNAME=my-reviewer
export ALLOWED_USERS=alice,bob
export GITHUB_TOKEN=ghp_...
export GITHUB_WEBHOOK_SECRET=your-secret

uv run nominal-code
```

See [Getting Started](getting-started.md) for the full webhook setup.

## Documentation

<div class="grid cards" markdown>

- :material-rocket-launch: **[Getting Started](getting-started.md)** — from zero to a working bot
- :material-play-circle: **[CI Mode](ci.md)** — automated reviews in GitHub Actions and GitLab CI
- :material-console: **[CLI Mode](cli.md)** — run one-off reviews without a server
- :material-cog: **[Configuration](configuration.md)** — full environment variable reference
- :material-github: **[GitHub](platforms/github.md)** — webhook setup, tokens, supported events
- :material-gitlab: **[GitLab](platforms/gitlab.md)** — webhook setup, self-hosted support
- :material-robot: **[Worker Bot](bots/worker.md)** — full-access agent that pushes code changes
- :material-eye: **[Reviewer Bot](bots/reviewer.md)** — read-only agent that posts structured reviews
- :material-sitemap: **[Architecture](architecture.md)** — request flow, agent runners, workspace layout
- :material-server: **[Deployment](deployment.md)** — production setup, health checks, reverse proxy

</div>

## Security

- Only users listed in `ALLOWED_USERS` can trigger the agent — comments from other users are silently ignored
- Webhook signatures are verified when secrets are configured
- The worker bot runs with full tool access (`bypassPermissions`)
- The reviewer bot is restricted to read-only tools (`Read`, `Glob`, `Grep`, `Bash(git clone*)`)
- GitHub App auth provides auto-rotating installation tokens
