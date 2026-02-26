# GitLab

## Webhook Setup

1. Go to your project **Settings → Webhooks → Add new webhook**.
2. Set the **URL** to `https://your-server:8080/webhooks/gitlab`.
3. Set a **Secret token** — this becomes your `GITLAB_WEBHOOK_SECRET` environment variable.
4. Under **Trigger**, check **Note events**.
5. Click **Add webhook**.

Only **Note events** on merge requests are handled. All other event types are ignored.

## Self-Hosted Support

To use a self-hosted GitLab instance, set `GITLAB_BASE_URL` to your instance URL:

```bash
GITLAB_BASE_URL=https://gitlab.example.com
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
| Webhook event | `issue_comment`, `pull_request_review_comment`, `pull_request_review` | `Note Hook` (note on a merge request) |
| Signature verification | HMAC-SHA256 via `X-Hub-Signature-256` | Plain string comparison via `X-Gitlab-Token` |
| PR open state | `state == "open"` | `state == "opened"` |
| Clone URL format | `https://x-access-token:{token}@github.com/...` | `https://oauth2:{token}@{host}/...` |
| Inline review | Single API call with all comments | One discussion per finding + version SHAs |

## Webhook Verification

When `GITLAB_WEBHOOK_SECRET` is set, the bot checks the `X-Gitlab-Token` header against the configured secret (plain string comparison, not HMAC). If the header does not match, the request is rejected. If the secret is not set, verification is skipped.
