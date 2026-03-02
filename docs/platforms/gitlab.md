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

### Worker token (`GITLAB_TOKEN`)

Create a **Personal Access Token** with the `api` scope. This grants full API access, which is needed to:

- Clone private repositories
- Post comments and reviews on merge requests
- Fetch diffs and MR metadata

### Reviewer token (`GITLAB_REVIEWER_TOKEN`)

Optional. When set, the reviewer bot uses this token for `git clone` instead of `GITLAB_TOKEN`. This lets you issue a token with **read-only** access, limiting what the reviewer agent can do at the git level.

The reviewer still uses `GITLAB_TOKEN` for API calls (posting reviews, fetching diffs).

## Differences from GitHub

| Aspect | GitHub | GitLab |
|---|---|---|
| Comment webhook | `issue_comment`, `pull_request_review_comment`, `pull_request_review` | `Note Hook` (note on a merge request) |
| Lifecycle webhook | `pull_request` (opened, synchronize, reopened, ready_for_review) | `Merge Request Hook` (open, update+oldrev, reopen) |
| Signature verification | HMAC-SHA256 via `X-Hub-Signature-256` | Plain string comparison via `X-Gitlab-Token` |
| PR open state | `state == "open"` | `state == "opened"` |
| Clone URL format | `https://x-access-token:{token}@github.com/...` | `https://oauth2:{token}@{host}/...` |
| Inline review | Single API call with all comments | One discussion per finding + version SHAs |

## Lifecycle Events (Auto-Trigger)

These events are only processed when `REVIEWER_TRIGGERS` includes the corresponding event type. See [Auto-Trigger](../configuration.md#auto-trigger).

| GitLab Event | Action | Event Type | Notes |
|---|---|---|---|
| `merge_request` | `open` | `pr_opened` | New MR created |
| `merge_request` | `update` (with `oldrev`) | `pr_push` | New commits pushed |
| `merge_request` | `reopen` | `pr_reopened` | MR reopened |

WIP merge requests (`work_in_progress: true`) are skipped for all lifecycle events. MR updates without `oldrev` (e.g. title or label changes) are ignored. GitLab does not have an equivalent of GitHub's `ready_for_review` event.

## Webhook Verification

When `GITLAB_WEBHOOK_SECRET` is set, the bot checks the `X-Gitlab-Token` header against the configured secret (plain string comparison, not HMAC). If the header does not match, the request is rejected. If the secret is not set, verification is skipped.
