# Getting Started

Minimal steps to go from zero to a working review.

## Prerequisites

- **Python 3.13+** and **[uv](https://github.com/astral-sh/uv)** — required for CLI and webhook modes
- **[Claude Code CLI](https://claude.ai/code)** installed and on `PATH` — required for CLI and webhook modes
- CI mode needs **none of the above** — it runs inside a Docker container

## Quick Start

=== "CI (fastest)"

    Add your LLM provider API key as a repository secret, then create a workflow file. The example below uses Anthropic — see [CI Mode](modes/ci.md) for other providers (OpenAI, DeepSeek, Groq, etc.).

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

    Open a pull request — the review runs automatically.

    **Next:** [CI Mode full guide](modes/ci.md) (all providers, GitLab CI setup, inputs, examples)

=== "CLI"

    ```bash
    git clone https://github.com/gauthierdmn/nominal-code.git
    cd nominal-code/app
    uv sync

    export GITHUB_TOKEN=ghp_...

    uv run nominal-code review owner/repo#42
    ```

    The review prints to stdout and posts to the PR.

    **Next:** [CLI Mode full guide](modes/cli.md) (all options, platform examples)

=== "Webhook"

    ```bash
    git clone https://github.com/gauthierdmn/nominal-code.git
    cd nominal-code/app
    uv sync

    export REVIEWER_BOT_USERNAME=my-reviewer
    export ALLOWED_USERS=alice,bob
    export GITHUB_TOKEN=ghp_...
    export GITHUB_WEBHOOK_SECRET=your-secret

    uv run nominal-code
    ```

    Set up a webhook on your repository pointing to `https://your-server:8080/webhooks/github`, then mention `@my-reviewer` in a PR comment.

    **Next:** [Webhook Mode full guide](modes/webhook.md) (platform setup, auto-trigger, multi-platform)

## Next Steps

- **[Configuration](reference/configuration.md)** — prompts, guidelines, auto-trigger, per-repo overrides
- **[Environment Variables](reference/env-vars.md)** — full variable reference grouped by feature
- **Platforms:** [GitHub](platforms/github.md) | [GitLab](platforms/gitlab.md)
- **Bots:** [Reviewer](bots/reviewer.md) | [Worker (Beta)](bots/worker.md)
