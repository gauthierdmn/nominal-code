<p align="center">
  <img src="assets/nominal-code-banner.png" alt="Nominal Code" width="600">
</p>

<p align="center">
  <a href="https://github.com/gauthierdmn/nominal-code/actions/workflows/ci.yml"><img src="https://github.com/gauthierdmn/nominal-code/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://gauthierdmn.github.io/nominal-code/"><img src="https://github.com/gauthierdmn/nominal-code/actions/workflows/docs.yml/badge.svg" alt="Docs"></a>
  <a href="https://www.python.org/downloads/release/python-3130/"><img src="https://img.shields.io/badge/python-3.13-blue.svg" alt="Python 3.13"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License: Apache 2.0"></a>
</p>

Nominal Code is an AI-powered code review agent for GitHub and GitLab pull requests. It uses an LLM to read your diffs and post structured inline reviews — all without leaving your PR.

It runs anywhere: as a **CI job** (GitHub Actions or GitLab CI), from the **command line**, or as a **self-hosted webhook server** for real-time interaction.

## What it does

The **reviewer bot** fetches the PR diff, runs an AI agent with **read-only tools**, and posts structured inline code reviews anchored to specific diff lines.

It accepts a **custom prompt** to steer the review (e.g. *"focus on security"* or *"check for SQL injection"*), and respects **per-repo coding guidelines** placed in `.nominal/` at the root of your repository.

> **Beta:** A **worker bot** is also available — it can apply code changes and push commits directly to the PR branch. See [Worker Bot](https://gauthierdmn.github.io/nominal-code/bots/worker/) for details.

## How to run it

### CI job

The fastest way to get started. The example below uses GitHub Actions — GitLab CI is also supported (see [CI Mode](https://gauthierdmn.github.io/nominal-code/modes/ci/)).

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

Multiple LLM providers are supported (Anthropic, OpenAI, DeepSeek, Groq, Together, Fireworks). Pass `provider` and the matching API key input. See [CI Mode](https://gauthierdmn.github.io/nominal-code/modes/ci/) for all provider examples.

Pin to a specific release tag (e.g. `@0.1.0`) for stability, or use `@main` to track the latest changes. You can also pass `model`, `max_turns`, `prompt`, and `coding_guidelines` as inputs.

> CI mode calls the LLM provider API directly and does not require the Claude Code CLI.

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

The server supports **GitHub App authentication** as an alternative to PATs, **auto-triggering** reviews on PR lifecycle events, and **multi-turn conversations** that carry context across comments. See [Getting Started](https://gauthierdmn.github.io/nominal-code/getting-started/) for the full setup.

## Configuration highlights

| What | How |
|---|---|
| Model | `AGENT_MODEL` env var, `--model` flag, or `model` Action input |
| Review prompt | `--prompt` flag, `INPUT_PROMPT` env var, or `prompt` Action input |
| Coding guidelines | Global via `CODING_GUIDELINES`, per-repo via `.nominal/guidelines.md` |
| Language-specific rules | `prompts/languages/` or `.nominal/languages/{lang}.md` per repo |
| Auto-trigger | `REVIEWER_TRIGGERS=pr_opened,pr_push,pr_reopened,pr_ready_for_review` |
| Allowed users | `ALLOWED_USERS=alice,bob` (webhook mode) |

Full reference: [Configuration](https://gauthierdmn.github.io/nominal-code/reference/configuration/) | [Environment Variables](https://gauthierdmn.github.io/nominal-code/reference/env-vars/)

## Documentation

- [Getting Started](https://gauthierdmn.github.io/nominal-code/getting-started/) — from zero to a working bot
- **Modes:** [CI](https://gauthierdmn.github.io/nominal-code/modes/ci/) | [CLI](https://gauthierdmn.github.io/nominal-code/modes/cli/) | [Webhook](https://gauthierdmn.github.io/nominal-code/modes/webhook/)
- **Platforms:** [GitHub](https://gauthierdmn.github.io/nominal-code/platforms/github/) | [GitLab](https://gauthierdmn.github.io/nominal-code/platforms/gitlab/)
- **Bots:** [Reviewer](https://gauthierdmn.github.io/nominal-code/bots/reviewer/) | [Worker (Beta)](https://gauthierdmn.github.io/nominal-code/bots/worker/)
- **Reference:** [Configuration](https://gauthierdmn.github.io/nominal-code/reference/configuration/) | [Environment Variables](https://gauthierdmn.github.io/nominal-code/reference/env-vars/)
- [Architecture](https://gauthierdmn.github.io/nominal-code/architecture/) — request flow, agent runners, workspace layout
- [Deployment](https://gauthierdmn.github.io/nominal-code/deployment/) — production setup, Docker, health checks
- [Security](https://gauthierdmn.github.io/nominal-code/security/) — trust model, LLM risks, authentication

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

Nominal Code includes webhook signature verification, tool restrictions, token separation, and resource limits. See the **[Security](https://gauthierdmn.github.io/nominal-code/security/)** page for the full trust model, LLM prompt injection risks, and hardening recommendations.
