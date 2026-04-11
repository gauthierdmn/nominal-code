# Nominal Code

An AI-powered code review agent for GitHub and GitLab pull requests. It uses an LLM to read your diffs and post structured inline reviews — all without leaving your PR.

It runs anywhere: as a **CI job** (GitHub Actions or GitLab CI), from the **command line**, or as a **self-hosted webhook server** for real-time interaction.

## Choose Your Mode

| Mode | Best For | Setup | Details |
|---|---|---|---|
| **[CI](modes/ci.md)** | Teams wanting zero-infrastructure automated reviews on every PR | Add a workflow file — no server, no CLI | Uses the LLM provider API directly |
| **[CLI](modes/cli.md)** | Developers running one-off reviews from their terminal | Install the Claude Code CLI and run a command | Uses the Claude Code CLI as agent runner |
| **[Webhook](modes/webhook.md)** | Teams wanting real-time, interactive reviews via `@mention` | Deploy a webhook server | Conversation continuity, auto-trigger, multi-turn |

New here? Start with the **[Getting Started](getting-started.md)** guide.

## Features

- **Reviewer bot** — fetches the PR diff, runs an agent with read-only tools, posts structured inline code reviews
- **Worker bot** *(beta)* — receives a prompt, clones the repo, runs an agent with full tool access, commits and pushes changes
- **Three execution modes** — CI job, CLI one-off, or webhook server
- **GitHub and GitLab** — supports both platforms simultaneously
- **Conversation continuity** — multi-turn conversations within the same PR (webhook mode)
- **Auto-trigger** — run reviews automatically on PR open, push, reopen, or ready-for-review events
- **Per-repo guidelines** — coding standards via `.nominal/guidelines.md` and `.nominal/languages/{lang}.md`

## Documentation

<div class="grid cards" markdown>

- :material-rocket-launch: **[Getting Started](getting-started.md)** — from zero to a working review
- :material-play-circle: **[CI Mode](modes/ci.md)** — automated reviews in GitHub Actions and GitLab CI
- :material-console: **[CLI Mode](modes/cli.md)** — run one-off reviews without a server
- :material-webhook: **[Webhook Mode](modes/webhook.md)** — real-time interactive reviews via `@mention`
- :material-github: **[GitHub](platforms/github.md)** — webhook setup, tokens, supported events
- :material-gitlab: **[GitLab](platforms/gitlab.md)** — webhook setup, self-hosted support
- :material-eye: **[Review Process](review.md)** — how the bot reviews code, tool restrictions, output format
- :material-cog: **[Configuration](reference/configuration.md)** — modes, prompts, guidelines, behavior
- :material-filter: **[Policies](reference/policies.md)** — filtering and routing policy models
- :material-format-list-bulleted: **[Environment Variables](reference/env-vars.md)** — full variable reference by feature
- :material-sitemap: **[Architecture](architecture.md)** — request flow, agent runners, workspace layout
- :material-account-group: **[Exploration Pipeline](reference/explore.md)** — planner, parallel explorers, WriteNotes, result types
- :material-archive-arrow-down: **[Compaction](reference/compaction.md)** — notes-based context compaction for long sessions
- :material-shield-lock: **[Security](security.md)** — trust model, LLM risks, authentication
- :material-server: **[Deployment](deployment/index.md)** — standalone server, Kubernetes, health checks

</div>

## Security

Nominal Code includes webhook signature verification, tool restrictions, token separation, and resource limits. See the **[Security](security.md)** page for the full trust model, LLM prompt injection risks, and hardening recommendations.
