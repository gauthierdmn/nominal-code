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
6. Click **Add webhook**.

## Token Requirements

### Worker token (`GITHUB_TOKEN`)

Create a **Personal Access Token** (classic) with the `repo` scope, or a **fine-grained token** with read/write access to:

- **Pull Requests** — to read PR metadata, post comments, and submit reviews
- **Contents** — to clone private repositories and read file contents

This token is used for both API calls and git clone operations.

### Reviewer token (`GITHUB_REVIEWER_TOKEN`)

Optional. When set, the reviewer bot uses this token for `git clone` instead of `GITHUB_TOKEN`. This lets you issue a token with **read-only** access to Contents, limiting what the reviewer agent can do at the git level.

The reviewer still uses `GITHUB_TOKEN` for API calls (posting reviews, fetching diffs).

## Supported Event Types

| GitHub Event | Trigger |
|---|---|
| `issue_comment` (action: `created`) | A new comment on a PR conversation |
| `pull_request_review_comment` (action: `created`) | A new inline comment on a code review |
| `pull_request_review` (action: `submitted`) | A review is submitted with a non-empty body |

Comments on issues (not PRs) are ignored. Events without an `@mention` of the bot are also ignored.

## Webhook Verification

When `GITHUB_WEBHOOK_SECRET` is set, the bot verifies the `X-Hub-Signature-256` header using HMAC-SHA256. If the signature does not match, the request is rejected. If the secret is not set, signature verification is skipped.
