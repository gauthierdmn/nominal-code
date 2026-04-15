# GitLab

## Webhook Setup

1. Go to your project **Settings → Webhooks → Add new webhook**.
2. Set the **URL** to `https://your-server:8080/webhooks/gitlab`.
3. Set a **Secret token** — this becomes your `GITLAB_WEBHOOK_SECRET` environment variable.
4. Under **Trigger**, check:
   - **Note events** — triggers on MR comments
   - **Merge request events** — required if using `REVIEWER_TRIGGERS` for auto-triggered reviews
5. Click **Add webhook**.

## Self-Hosted Support

To use a self-hosted GitLab instance, set `GITLAB_API_BASE` to your instance URL:

```bash
GITLAB_API_BASE=https://gitlab.example.com
```

This affects both API calls and clone URLs. Defaults to `https://gitlab.com`.

## Token Requirements

### API token (`GITLAB_TOKEN`)

Create a **Personal Access Token** with the `api` scope. This grants full API access, which is needed to:

- Clone private repositories
- Post comments and reviews on merge requests
- Fetch diffs and MR metadata

For all authentication variables, see [Environment Variables](../reference/env-vars.md#gitlab).

## Lifecycle Events (Auto-Trigger)

These events are only processed when `REVIEWER_TRIGGERS` includes the corresponding event type. See [Auto-Trigger](../reference/configuration.md#auto-trigger).

| GitLab Event | Action | Event Type | Notes |
|---|---|---|---|
| `merge_request` | `open` | `pr_opened` | New MR created |
| `merge_request` | `update` (with `oldrev`) | `pr_push` | New commits pushed |
| `merge_request` | `reopen` | `pr_reopened` | MR reopened |

WIP merge requests (`work_in_progress: true`) are skipped for all lifecycle events. MR updates without `oldrev` (e.g. title or label changes) are ignored. GitLab does not have an equivalent of GitHub's `ready_for_review` event.

## Webhook Verification

When `GITLAB_WEBHOOK_SECRET` is set, the bot checks the `X-Gitlab-Token` header against the configured secret. See [Security — Webhook Verification](../security.md#webhook-verification) for implementation details.
