# Nominal Code

A bot that monitors GitHub PRs and GitLab MRs for review comments mentioning it, then uses an AI agent to respond, review code, and optionally push changes. Comment `@your-bot fix this bug` on a pull request, and the bot clones the repo, runs the agent, and replies with comments and/or code commits.

## Features

- **Worker bot** — receives a prompt, clones the repo, runs an agent with full tool access, commits and pushes changes
- **Reviewer bot** — fetches the PR diff, runs an agent with read-only tools, posts structured inline code reviews
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

## Documentation

<div class="grid cards" markdown>

- :material-rocket-launch: **[Getting Started](getting-started.md)** — from zero to a working bot
- :material-console: **[CLI Mode](cli.md)** — run one-off reviews without a server
- :material-cog: **[Configuration](configuration.md)** — full environment variable reference
- :material-github: **[GitHub](platforms/github.md)** — webhook setup, tokens, supported events
- :material-gitlab: **[GitLab](platforms/gitlab.md)** — webhook setup, self-hosted support
- :material-robot: **[Worker Bot](bots/worker.md)** — full-access agent that pushes code changes
- :material-eye: **[Reviewer Bot](bots/reviewer.md)** — read-only agent that posts structured reviews
- :material-sitemap: **[Architecture](architecture.md)** — request flow, components, workspace layout
- :material-server: **[Deployment](deployment.md)** — production setup, health checks, reverse proxy

</div>

## Security

- Only users listed in `ALLOWED_USERS` can trigger the agent — comments from other users are silently ignored
- Webhook signatures are verified when secrets are configured
- The worker bot runs with full tool access (`bypassPermissions`)
- The reviewer bot is restricted to read-only tools (`Read`, `Glob`, `Grep`, `Bash(git clone*)`)
