# Webhook Mode

The webhook server provides real-time, interactive code reviews. Mention the bot in a PR comment and it responds — with full conversation continuity across multiple interactions.

## What It Does

- Listens for GitHub/GitLab webhook events on PR comments and lifecycle events
- Runs the reviewer or worker bot in response to `@mentions`
- Supports multi-turn conversations within the same PR (conversation continuity)
- Auto-triggers reviews on PR open, push, reopen, or ready-for-review events

## Configuration

=== "GitHub (PAT)"

    ```bash
    REVIEWER_BOT_USERNAME=my-reviewer
    ALLOWED_USERS=alice,bob
    GITHUB_TOKEN=ghp_...
    GITHUB_WEBHOOK_SECRET=your-secret
    ```

=== "GitHub (App)"

    ```bash
    REVIEWER_BOT_USERNAME=my-reviewer
    ALLOWED_USERS=alice,bob
    GITHUB_APP_ID=12345
    GITHUB_APP_PRIVATE_KEY_PATH=/path/to/private-key.pem
    GITHUB_WEBHOOK_SECRET=your-secret
    ```

=== "GitLab"

    ```bash
    REVIEWER_BOT_USERNAME=my-reviewer
    ALLOWED_USERS=alice,bob
    GITLAB_TOKEN=glpat-...
    GITLAB_WEBHOOK_SECRET=your-secret
    ```

You also need a publicly reachable server (or a tunnel like ngrok for development). See [GitHub](../platforms/github.md) or [GitLab](../platforms/gitlab.md) for webhook setup instructions.

## Running the Server

```bash
cd app
uv run nominal-code
```

You should see:

```
INFO     nominal_code.main Starting server on 0.0.0.0:8080 | platforms=['github'] | reviewer=@my-reviewer | allowed_users=...
INFO     nominal_code.main Server is running, waiting for webhooks...
```

## Triggering Reviews

### @mention

Mention the bot in a PR comment:

```
@my-reviewer please review this
@my-reviewer focus on security
```

The bot reacts with an eyes emoji and then posts a structured code review.

### Auto-Trigger

The reviewer can run automatically on PR lifecycle events without requiring an `@mention`. Set `REVIEWER_TRIGGERS`:

```bash
REVIEWER_TRIGGERS=pr_opened,pr_push
```

See [Auto-Trigger](../reference/configuration.md#auto-trigger) for the full event mapping and rules.

## Multi-Platform Setup {: #multi-platform }

To handle both GitHub and GitLab simultaneously, set tokens for both:

```bash
# Bot config
REVIEWER_BOT_USERNAME=my-reviewer
WORKER_BOT_USERNAME=my-worker        # optional, beta
ALLOWED_USERS=alice,bob

# GitHub
GITHUB_TOKEN=ghp_...
GITHUB_WEBHOOK_SECRET=gh-secret

# GitLab
GITLAB_TOKEN=glpat-...
GITLAB_WEBHOOK_SECRET=gl-secret
GITLAB_API_BASE=https://gitlab.example.com
```

Both platforms share the same bot usernames and allowed users list. Each platform gets its own webhook route (`/webhooks/github` and `/webhooks/gitlab`).

## What's Different

Webhook mode is the only mode with conversation continuity — multi-turn conversations carry context across comments on the same PR. It requires a running server and the Claude Code CLI. See the [mode comparison](../reference/configuration.md#mode-comparison) for a full breakdown.

For the complete list of environment variables, see [Environment Variables](../reference/env-vars.md).

For production deployment, see [Deployment](../deployment.md).
