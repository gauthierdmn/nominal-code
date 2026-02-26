# Getting Started

Minimal steps to go from zero to a working review.

## Prerequisites

- **Python 3.13+**
- **[uv](https://github.com/astral-sh/uv)** for dependency management
- **[Claude Code CLI](https://claude.ai/code)** installed and on `PATH`
- A **GitHub** or **GitLab** account with API tokens

## Install

```bash
git clone https://github.com/your-org/nominal-code.git
cd nominal-code/app
uv sync
```

## Option A: CLI Mode (quickest)

No server, no webhooks — just a token and a PR reference.

```bash
export GITHUB_TOKEN=ghp_...

# Review a PR (prints results and posts to the PR)
uv run nominal-code review owner/repo#42

# Dry run (prints results without posting)
uv run nominal-code review owner/repo#42 --dry-run
```

See [CLI Mode](cli.md) for all options and examples.

## Option B: Webhook Server

For automated reviews triggered by PR comments, deploy the webhook server.

### Configure

Create a `.env` file (or export the variables directly). The simplest setup is a **reviewer-only bot on GitHub**:

```bash
REVIEWER_BOT_USERNAME=my-reviewer
ALLOWED_USERS=alice,bob
GITHUB_TOKEN=ghp_...
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
