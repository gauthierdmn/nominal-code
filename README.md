<p align="center">
  <img src="assets/nominal-code-banner.png" alt="Nominal Code" width="600">
</p>

<p align="center">
  <a href="https://github.com/gauthierdmn/nominal-code/actions/workflows/ci.yml"><img src="https://github.com/gauthierdmn/nominal-code/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://gauthierdmn.github.io/nominal-code/"><img src="https://github.com/gauthierdmn/nominal-code/actions/workflows/docs.yml/badge.svg" alt="Docs"></a>
  <a href="https://www.python.org/downloads/release/python-3130/"><img src="https://img.shields.io/badge/python-3.13-blue.svg" alt="Python 3.13"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License: Apache 2.0"></a>
</p>

<p align="center">
  Automated code reviews posted inline on your pull requests — GitHub and GitLab, any LLM provider, scales from solo dev to org-wide Kubernetes deployment.
</p>

---

Nominal Code reads your PR diffs, runs an AI agent with access to the repository, and posts structured inline reviews anchored to specific lines of code. It works as a **CI job**, a **CLI command**, or a **self-hosted webhook server** with real-time interaction.

In **API mode** (CI, or webhook/CLI with a provider key), a multi-turn reviewer agent investigates the codebase directly and spawns explore sub-agents on demand for deep investigation (callers, test coverage, type hierarchies), then produces findings via structured output. In **CLI mode**, it delegates to the Claude Code CLI which handles exploration and review in one conversation.

## Key Features

- **Agentic review in API mode** — a multi-turn reviewer agent reads annotated diffs and investigates the codebase with Read, Grep, and Glob tools. For deep analysis it spawns explore sub-agents that trace callers, check tests, and verify types — then produces findings with full context. No guessing, no hallucinated line numbers.
- **Inline reviews with code suggestions** — comments land exactly where the issue is, with one-click-apply fixes.
- **7 LLM providers or Claude Code CLI** — use any provider API (Anthropic, OpenAI, Google Gemini, DeepSeek, Groq, Together, Fireworks), or run via the Claude Code CLI with a Pro/Max subscription — no API key needed.
- **GitHub + GitLab** — same bot, both platforms simultaneously. GitHub App and PAT authentication supported.
- **Multi-turn conversations** — mention the bot again and it remembers the full PR discussion (webhook mode).
- **Custom prompts and per-repo guidelines** — steer reviews with instructions like *"focus on security"*, or drop a `.nominal/guidelines.md` in your repo for persistent rules.
- **Language-aware** — automatically applies language-specific guidelines when the diff contains Python, Go, TypeScript, etc.
- **Auto-trigger or `@mention`** — run reviews automatically on PR open, push, reopen, or ready-for-review events, or trigger them on demand by mentioning the bot in a comment.
- **Scales to any org size** — runs as a single process for small teams, or deploy to Kubernetes where each review runs as an isolated Job with automatic queuing and horizontal scaling.
- **YAML config** — one structured file for all settings. Environment variables as overrides for secrets and runtime tuning.

## How It Works (API Mode)

```
PR opened / @mention
       │
       v
  +─────────────────────+
  │   Reviewer Agent    │    Multi-turn loop (up to 8 turns)
  │                     │    Tools: Read, Glob, Grep, Bash,
  │                     │    WriteNotes, submit_review, Agent
  +──────────+──────────+
             │
             ├── [simple lookup] ──> Read / Grep / Glob
             │
             ├── [deep investigation] ──> Agent tool
             │                               │
             │              +────────────────+────────────────+
             │              │  Explore sub-agent (32 turns)   │
             │              │  Read, Glob, Grep, Bash,        │
             │              │  WriteNotes                     │
             │              +────────────────+────────────────+
             │                               │
             │<── notes content ─────────────+
             │
             v
       submit_review          structured JSON review
             │
             v
      GitHub / GitLab         inline comments + suggestions
```

In **CLI mode**, the Claude Code CLI handles both exploration and review in a single multi-turn conversation with its own tool set.

## Get Started in 60 Seconds

Add your API key as a repository secret, then create a workflow file:

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

Open a pull request — the review runs automatically. Pass `provider` and the matching API key to use a different LLM. See [CI Mode](https://gauthierdmn.github.io/nominal-code/modes/ci/) for all provider examples and GitLab CI setup.

## All the Ways to Run It

| Mode | Best for | What happens |
|---|---|---|
| [**CI**](https://gauthierdmn.github.io/nominal-code/modes/ci/) | Easiest setup | Runs in GitHub Actions or GitLab CI on every PR event |
| [**CLI**](https://gauthierdmn.github.io/nominal-code/modes/cli/) | One-off reviews | `uv run nominal-code review owner/repo#42` from your terminal |
| [**Webhook**](https://gauthierdmn.github.io/nominal-code/modes/webhook/) | Teams | Self-hosted server with `@mention` triggers and multi-turn conversations |
| [**Kubernetes**](https://gauthierdmn.github.io/nominal-code/deployment/kubernetes/) | Production scale | Webhook server dispatches each review as a K8s Job |

### CLI

```bash
cd nominal-code/app && uv sync
export GITHUB_TOKEN=ghp_...

uv run nominal-code review owner/repo#42
uv run nominal-code review owner/repo#42 --prompt "focus on security"
uv run nominal-code review owner/repo#42 --dry-run
```

### Webhook Server

```bash
cd nominal-code/app && uv sync

# config.yaml
# reviewer:
#   bot_username: "my-reviewer"
#   triggers: [pr_opened]
# access:
#   allowed_users: [alice, bob]

export GITHUB_TOKEN=ghp_...
export GITHUB_WEBHOOK_SECRET=your-secret
export CONFIG_PATH=config.yaml

uv run nominal-code serve
```

Mention `@my-reviewer` in a PR comment — the bot responds with a structured review. Supports **GitHub App auth**, **auto-triggering**, and **multi-turn conversations** that carry context across comments.

## Configuration

Nominal Code uses a [YAML config file](https://gauthierdmn.github.io/nominal-code/reference/configuration/#yaml-config-file) as the primary configuration method. Environment variables always override the YAML file — use them for secrets and runtime tuning.

```yaml
# config.yaml
reviewer:
  bot_username: "my-reviewer"
  triggers:
    - pr_opened
    - pr_push

agent:
  provider: "anthropic"
  model: "claude-sonnet-4-6"

access:
  allowed_users:
    - alice
    - bob
  allowed_repos:
    - myorg/backend
    - myorg/frontend
```

Full reference: [Configuration](https://gauthierdmn.github.io/nominal-code/reference/configuration/) | [Environment Variables](https://gauthierdmn.github.io/nominal-code/reference/env-vars/)

## Documentation

- [Getting Started](https://gauthierdmn.github.io/nominal-code/getting-started/) — from zero to a working review
- **Modes:** [CI](https://gauthierdmn.github.io/nominal-code/modes/ci/) | [CLI](https://gauthierdmn.github.io/nominal-code/modes/cli/) | [Webhook](https://gauthierdmn.github.io/nominal-code/modes/webhook/)
- **Platforms:** [GitHub](https://gauthierdmn.github.io/nominal-code/platforms/github/) | [GitLab](https://gauthierdmn.github.io/nominal-code/platforms/gitlab/)
- [Review Process](https://gauthierdmn.github.io/nominal-code/review/) | [Sub-Agents](https://gauthierdmn.github.io/nominal-code/reference/explore/) | [Compaction](https://gauthierdmn.github.io/nominal-code/reference/compaction/)
- **Reference:** [Configuration](https://gauthierdmn.github.io/nominal-code/reference/configuration/) | [Environment Variables](https://gauthierdmn.github.io/nominal-code/reference/env-vars/)
- [Architecture](https://gauthierdmn.github.io/nominal-code/architecture/) | [Deployment](https://gauthierdmn.github.io/nominal-code/deployment/) | [Security](https://gauthierdmn.github.io/nominal-code/security/)

## Development

```bash
cd app && uv sync

uv run ruff check nominal_code/ tests/
uv run ruff format nominal_code/ tests/
uv run mypy nominal_code/
uv run pytest
```

## Security

Nominal Code includes webhook signature verification, tool restrictions, token separation, and resource limits. See [Security](https://gauthierdmn.github.io/nominal-code/security/) for the full trust model and hardening recommendations.
