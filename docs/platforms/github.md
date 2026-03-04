# GitHub

## Webhook Setup

1. Go to your repository (or organization) **Settings → Webhooks → Add webhook**.
2. Set the **Payload URL** to `https://your-server:8080/webhooks/github`.
3. Set **Content type** to `application/json`.
4. Set a **Secret** — this becomes your `GITHUB_WEBHOOK_SECRET` environment variable.
5. Under **Which events would you like to trigger this webhook?**, select **Let me select individual events** and check:
   - **Issue comments** — triggers on PR conversation comments
   - **Pull request review comments** — triggers on inline code review comments
   - **Pull request reviews** — triggers on review submissions with a body
   - **Pull requests** — required if using `REVIEWER_TRIGGERS` for auto-triggered reviews on PR open/push/reopen
6. Click **Add webhook**.

## Authentication

Nominal Code supports two authentication methods for GitHub: a **Personal Access Token (PAT)** or a **GitHub App**. When both are configured, the GitHub App takes precedence.

### Option A: Personal Access Token

Create a **Personal Access Token** (classic) with the `repo` scope, or a **fine-grained token** with read/write access to:

- **Pull Requests** — to read PR metadata, post comments, and submit reviews
- **Contents** — to clone private repositories and read file contents

```bash
export GITHUB_TOKEN=ghp_...
```

This token is used for both API calls and git clone operations.

### Option B: GitHub App

Using a GitHub App provides automatic token rotation, fine-grained permissions scoped at the installation level, and no need for a separate reviewer token.

1. [Create a GitHub App](https://docs.github.com/en/apps/creating-github-apps) with the following permissions:
   - **Pull Requests**: Read & Write
   - **Contents**: Read & Write
   - Subscribe to the same webhook events listed in [Webhook Setup](#webhook-setup)
2. Generate a private key from the App settings page and download the `.pem` file.
3. Install the App on your repository or organization.

```bash
export GITHUB_APP_ID=12345
export GITHUB_APP_PRIVATE_KEY_PATH=/path/to/private-key.pem
```

Alternatively, pass the key inline via `GITHUB_APP_PRIVATE_KEY` instead of a file path.

In **webhook mode**, the installation ID is extracted automatically from webhook payloads. In **CLI mode**, you must also set:

```bash
export GITHUB_INSTALLATION_ID=67890
```

Tokens are generated on the fly and automatically refreshed before they expire.

For all authentication variables, see [Environment Variables](../reference/env-vars.md#github).

## Supported Event Types

### Comment Events

| GitHub Event | Trigger |
|---|---|
| `issue_comment` (action: `created`) | A new comment on a PR conversation |
| `pull_request_review_comment` (action: `created`) | A new inline comment on a code review |
| `pull_request_review` (action: `submitted`) | A review is submitted with a non-empty body |

Comments on issues (not PRs) are ignored. Events without an `@mention` of the bot are also ignored.

### Lifecycle Events (Auto-Trigger)

These events are only processed when `REVIEWER_TRIGGERS` includes the corresponding event type. See [Auto-Trigger](../reference/configuration.md#auto-trigger).

| GitHub Event | Action | Event Type | Notes |
|---|---|---|---|
| `pull_request` | `opened` | `pr_opened` | New PR created |
| `pull_request` | `synchronize` | `pr_push` | New commits pushed |
| `pull_request` | `reopened` | `pr_reopened` | PR reopened |
| `pull_request` | `ready_for_review` | `pr_ready_for_review` | Draft PR marked ready |

Draft PRs (`draft: true`) are skipped for all lifecycle events. Other `pull_request` actions (e.g. `closed`, `labeled`) are ignored.

## Webhook Verification

When `GITHUB_WEBHOOK_SECRET` is set, the bot verifies the `X-Hub-Signature-256` header using HMAC-SHA256. See [Security — Webhook Verification](../security.md#webhook-verification) for implementation details.
