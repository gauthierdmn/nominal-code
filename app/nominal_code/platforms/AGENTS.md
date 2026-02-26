# platforms/

Platform abstraction layer and concrete implementations for GitHub and GitLab.

## Key concepts

- **Protocol-based design** — `Platform` and `ReviewerPlatform` are `typing.Protocol` classes. Implementations satisfy them structurally (no inheritance required).
- **Self-registering factories** — each platform module calls `register_platform()` at import time. `build_platforms()` invokes all factories; a factory returns `None` if its env vars are missing, so unconfigured platforms are silently skipped.
- **Side-effect imports** — `__init__.py` imports `github` and `gitlab` modules to trigger registration.

## File tree

```
platforms/
├── __init__.py    # Side-effect imports (github, gitlab); re-exports build_platforms()
├── base.py        # Protocol definitions (Platform, ReviewerPlatform) and shared dataclasses (PullRequestEvent, CommentEvent, LifecycleEvent, CommentReply, ExistingComment, PlatformName)
├── registry.py    # register_platform() / build_platforms() — service locator pattern
├── github.py      # GitHubPlatform: HMAC-SHA256 webhooks, REST v3 API, native PR reviews
└── gitlab.py      # GitLabPlatform: token-header webhooks, REST v4 API, discussion-based reviews
```

## Platform protocol surface

| Method | Platform | ReviewerPlatform |
|--------|----------|------------------|
| `verify_webhook()` | Yes | Yes |
| `parse_event()` | Yes | Yes |
| `post_reply()` | Yes | Yes |
| `post_reaction()` | Yes | Yes |
| `is_pr_open()` | Yes | Yes |
| `fetch_pr_branch()` | Yes | Yes |
| `fetch_pr_comments()` | — | Yes |
| `fetch_pr_diff()` | — | Yes |
| `submit_review()` | — | Yes |
| `build_reviewer_clone_url()` | — | Yes |

## Important details

- **GitHub clone URLs** use `x-access-token:{token}@github.com` format.
- **GitLab clone URLs** use `oauth2:{token}@{host}` format; supports self-hosted via `GITLAB_BASE_URL`.
- **Webhook verification** — GitHub uses HMAC-SHA256 (`X-Hub-Signature-256`); GitLab uses a shared secret header (`X-Gitlab-Token`). Both skip verification if no secret is configured.
- **GitHub event routing** — `X-GitHub-Event` header determines handler: `issue_comment`, `pull_request_review_comment`, `pull_request_review`, `pull_request`.
- **GitLab event routing** — `X-Gitlab-Event` header: `Note Hook` (comments on MRs), `Merge Request Hook` (lifecycle).
- **Draft/WIP handling** — both platforms skip draft PRs for lifecycle events.
- **GitHub submit_review()** posts a native review with inline comments via `POST /repos/{repo}/pulls/{pr}/reviews`.
- **GitLab submit_review()** posts a top-level note for the summary, then creates individual diff discussions per finding (requires fetching MR versions for base/head SHAs).
- **Pagination** — `fetch_pr_diff()` on GitHub paginates at 100 files per page. GitLab fetches all diffs in one call.
- **is_pr_open()** defaults to `True` on API errors (safe default for workspace cleanup).
- **Reviewer token** — optional separate read-only token for cloning; falls back to the main token.
