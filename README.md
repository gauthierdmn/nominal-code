<p align="center">
  <img src="assets/nominal-code-banner.png" alt="Nominal Code" width="600">
</p>

<p align="center">
  <a href="https://github.com/gauthierdmn/nominal-code/actions/workflows/ci.yml"><img src="https://github.com/gauthierdmn/nominal-code/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://gauthierdmn.github.io/nominal-code/"><img src="https://github.com/gauthierdmn/nominal-code/actions/workflows/docs.yml/badge.svg" alt="Docs"></a>
  <a href="https://www.python.org/downloads/release/python-3130/"><img src="https://img.shields.io/badge/python-3.13-blue.svg" alt="Python 3.13"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License: Apache 2.0"></a>
</p>

AI agents that review, comment, and fix code. Right from your pull requests.

## Features

- **Reviewer bot** — fetches the PR diff, runs an agent with read-only tools, posts structured inline code reviews
- **Worker bot** — receives a prompt, clones the repo, runs an agent with full tool access, commits and pushes changes
- **Auto-trigger** — optionally run the reviewer automatically on PR open, push, or reopen via `REVIEWER_TRIGGERS`
- **CLI mode** — run a one-off review on any PR without deploying a webhook server
- **GitHub and GitLab** — supports both platforms simultaneously
- **Session continuity** — multi-turn conversations within the same PR
- **Automatic cleanup** — stale workspaces for closed/merged PRs are removed periodically
- **Private dependencies** — agents can clone internal libraries for context

## Quick Start

### CLI Mode (no server required)

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

### Webhook Server Mode

Nominal Code supports two authentication methods for GitHub: a Personal Access Token (PAT) or a GitHub App.

**Using a PAT:**

```bash
cd nominal-code/app
uv sync

export REVIEWER_BOT_USERNAME=my-reviewer
export ALLOWED_USERS=alice,bob
export GITHUB_TOKEN=ghp_...
export GITHUB_WEBHOOK_SECRET=your-secret

uv run nominal-code
```

**Using a GitHub App:**

```bash
export GITHUB_APP_ID=12345
export GITHUB_APP_PRIVATE_KEY_PATH=/path/to/private-key.pem
export GITHUB_WEBHOOK_SECRET=your-secret

uv run nominal-code
```

See [Configuration](docs/configuration.md) for all options.

## Documentation

- [Getting Started](docs/getting-started.md) — from zero to a working bot
- [CLI Mode](docs/cli.md) — run one-off reviews without a server
- [Configuration](docs/configuration.md) — full environment variable reference
- **Platforms**
  - [GitHub](docs/platforms/github.md) — webhook setup, tokens, supported events
  - [GitLab](docs/platforms/gitlab.md) — webhook setup, self-hosted support, differences from GitHub
- **Bots**
  - [Worker](docs/bots/worker.md) — full-access agent that pushes code changes
  - [Reviewer](docs/bots/reviewer.md) — read-only agent that posts structured reviews
- [Architecture](docs/architecture.md) — request flow, components, workspace layout
- [Deployment](docs/deployment.md) — production setup, health checks, reverse proxy

## Development

```bash
cd app

# Install with dev dependencies
uv sync

# Lint and format
uv run ruff check nominal_code/ tests/
uv run ruff format nominal_code/ tests/

# Type check
uv run mypy nominal_code/

# Run tests
uv run pytest
```

## Security

- Only users listed in `ALLOWED_USERS` can trigger the agent — comments from other users are silently ignored
- Webhook signatures are verified when secrets are configured
- The worker bot runs with full tool access (`bypassPermissions`)
- The reviewer bot is restricted to read-only tools (`Read`, `Glob`, `Grep`, `Bash(git clone*)`)
