# Getting Started

Minimal steps to go from zero to a working review.

## Option A: CI Job (fastest)

No server, no CLI installation — just add a workflow file to your repository.

### GitHub Actions

1. Add an `ANTHROPIC_API_KEY` secret to your repository (Settings > Secrets and variables > Actions).
2. Create a workflow file:

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

3. Open a pull request — the review runs automatically.

CI mode calls the Anthropic API directly and does not require the Claude Code CLI. See [CI Mode](ci.md) for GitLab CI setup, inputs, and examples.

## Option B: CLI Mode (quickest local setup)

No server, no webhooks — just a token and a PR reference.

### Prerequisites

- **Python 3.13+**
- **[uv](https://github.com/astral-sh/uv)** for dependency management
- **[Claude Code CLI](https://claude.ai/code)** installed and on `PATH`
- A **GitHub** or **GitLab** account with API tokens

### Install

```bash
git clone https://github.com/gauthierdmn/nominal-code.git
cd nominal-code/app
uv sync
```

### Run

```bash
export GITHUB_TOKEN=ghp_...

# Review a PR (prints results and posts to the PR)
uv run nominal-code review owner/repo#42

# Dry run (prints results without posting)
uv run nominal-code review owner/repo#42 --dry-run
```

See [CLI Mode](cli.md) for all options and examples.

## Option C: Webhook Server

For automated reviews triggered by PR comments, deploy the webhook server.

### Prerequisites

- **Python 3.13+**
- **[uv](https://github.com/astral-sh/uv)** for dependency management
- **[Claude Code CLI](https://claude.ai/code)** installed and on `PATH`
- A **GitHub** or **GitLab** account with API tokens

### Install

```bash
git clone https://github.com/gauthierdmn/nominal-code.git
cd nominal-code/app
uv sync
```

### Configure

Create a `.env` file (or export the variables directly). The simplest setup is a **reviewer-only bot on GitHub**.

**Using a PAT:**

```bash
REVIEWER_BOT_USERNAME=my-reviewer
ALLOWED_USERS=alice,bob
GITHUB_TOKEN=ghp_...
GITHUB_WEBHOOK_SECRET=your-secret
```

**Using a GitHub App:**

```bash
REVIEWER_BOT_USERNAME=my-reviewer
ALLOWED_USERS=alice,bob
GITHUB_APP_ID=12345
GITHUB_APP_PRIVATE_KEY_PATH=/path/to/private-key.pem
GITHUB_WEBHOOK_SECRET=your-secret
```

You will also need a publicly reachable server (or a tunnel like ngrok for development). Set up a webhook on your GitHub repository pointing to `https://your-server:8080/webhooks/github`. See [GitHub platform setup](platforms/github.md) for full instructions.

### Run

```bash
cd app
uv run nominal-code
```

You should see:

```
INFO     nominal_code.main Starting server on 0.0.0.0:8080 | platforms=['github'] | reviewer=@my-reviewer | allowed_users=...
INFO     nominal_code.main Server is running, waiting for webhooks...
```

### Verify

Open a pull request on your repository and leave a comment:

```
@my-reviewer please review this
```

The bot should react with an eyes emoji and then post a structured code review.

## Next Steps

- Add a worker bot for code changes — see [Worker bot](bots/worker.md)
- See [Configuration](configuration.md) for the full environment variable reference
- Set up GitLab — see [GitLab platform setup](platforms/gitlab.md)
- Try CI mode for zero-infrastructure reviews — see [CI Mode](ci.md)
