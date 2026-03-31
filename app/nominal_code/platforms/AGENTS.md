# platforms/

Platform abstraction layer and concrete implementations for GitHub and GitLab.

## Key concepts

- **Protocol-based design** — `Platform` is a `typing.Protocol` class. Implementations satisfy it structurally (no inheritance required).
- **Config-driven factories** — each platform has an explicit factory function (`create_github_platform(config)`, `create_gitlab_platform(config)`) that accepts a frozen `GitHubConfig`/`GitLabConfig` and returns a client or `None` if unconfigured.
- **Top-level build functions** — `build_platforms(config)` builds all configured platforms; `build_platform(name, config)` builds a single one or raises.
- **Unified auth ABC** — `PlatformAuth` in `base.py` is the single abstract base class for all authentication strategies. It defines `get_api_token()` and `ensure_auth()`. Both `GitHubPatAuth`, `GitHubAppAuth`, and `GitLabPatAuth` subclass `PlatformAuth` directly.
- **Pluggable GitHub auth** — `GitHubPlatform` delegates all token access to a `PlatformAuth` instance (`GitHubPatAuth` for PATs, `GitHubAppAuth` for GitHub App JWT + installation tokens). The platform itself is auth-mode-agnostic.
- **Pluggable GitLab auth** — `GitLabPlatform` delegates all token access to a `PlatformAuth` instance (`GitLabPatAuth` for static PATs). The ABC is extensible for custom auth strategies (e.g. OAuth).

## File tree

```
platforms/
├── __init__.py        # build_platforms(config), build_platform(name, config), re-exports PlatformAuth
├── base.py            # PlatformAuth ABC, Platform protocol, shared dataclasses
├── http.py            # request_with_retry(): HTTP request helper with transient error retries
├── github/
│   ├── __init__.py    # Re-exports: GitHubPlatform, auth classes
│   ├── auth.py        # GitHubPatAuth, GitHubAppAuth (both subclass PlatformAuth)
│   └── platform.py    # GitHubPlatform, create_github_platform(config) factory
└── gitlab/
    ├── __init__.py    # Re-exports: GitLabPlatform, GitLabPatAuth
    ├── auth.py        # GitLabPatAuth (subclasses PlatformAuth)
    └── platform.py    # GitLabPlatform, create_gitlab_platform(config) factory
```

## Platform protocol surface

| Method | Purpose |
|--------|---------|
| `verify_webhook()` | Verify webhook signature |
| `parse_event()` | Parse webhook payload into event |
| `authenticate()` | Ensure valid auth (refresh tokens if needed) |
| `post_reply()` | Post a reply to a PR comment |
| `post_reaction()` | Add a reaction to a comment |
| `post_pr_reaction()` | Add a reaction to a PR |
| `fetch_pr_branch()` | Resolve head branch name |
| `build_clone_url()` | Build authenticated clone URL |
| `fetch_pr_comments()` | Fetch existing PR comments |
| `fetch_pr_diff()` | Fetch changed files with patches |
| `submit_review()` | Submit native code review with inline comments |

## PlatformAuth ABC

All auth strategies subclass `PlatformAuth` from `base.py`:

| Method | Purpose |
|--------|---------|
| `get_api_token(account_id=0)` | Return the API token for the given account |
| `ensure_auth(account_id=0)` | Refresh/load tokens as needed (no-op for static PATs) |

## GitHub authentication

Two auth modes, detected automatically by the factory:

| | PAT mode | App mode |
|---|---|---|
| **Detection** | `github.token` set | `github.app_id` + `github.private_key` set |
| **Class** | `GitHubPatAuth` | `GitHubAppAuth` |
| **Token lifecycle** | Static, never expires | JWT → installation token, cached 1hr, refreshed at 5min margin |
| **Clone token** | Same as API token | Same installation token (permissions handle scoping) |
| **Installation ID** | N/A | Extracted from webhook payload via `authenticate(webhook_body=)`; `github.installation_id` config for CLI/CI mode |
| **`ensure_auth()`** | No-op | Calls private `_refresh_token()` to rotate expiring tokens |

- `GitHubPlatform` constructor takes a `PlatformAuth` instance — no raw tokens.
- All HTTP calls pass `headers=self._auth_headers()` per-request (no static headers on the client).
- `authenticate(webhook_body=body)` extracts `installation.id` from webhook payloads, sets a request-scoped ContextVar, and refreshes the token. Call without arguments for CLI/CI mode.
- Private key resolution (inline PEM or file path) is handled during config loading in `config/loader.py`, not at platform construction time.
- App mode prefers over PAT mode when both are configured.

## GitLab authentication

| | PAT mode |
|---|---|
| **Detection** | `gitlab.token` set |
| **Class** | `GitLabPatAuth` |
| **Token lifecycle** | Static, never expires |
| **Clone token** | Same as API token |
| **`ensure_auth()`** | No-op |

- `GitLabPlatform` constructor takes a `PlatformAuth` instance — no raw tokens.
- Auth header is set on the `httpx.AsyncClient` at init, and refreshed after `authenticate()` via `_refresh_client_headers()`.
- `authenticate(webhook_body=body)` delegates to the auth strategy's `ensure_auth()` and refreshes client headers. The `webhook_body` parameter is unused for GitLab PAT auth but available for future OAuth strategies.

## Important details

- **GitHub clone URLs** use `x-access-token:{token}@github.com` format.
- **GitLab clone URLs** use `oauth2:{token}@{host}` format; supports self-hosted via `gitlab.api_base` config.
- **Clone URL selection** — `build_clone_url(repo)` uses `get_api_token()` to build an authenticated clone URL.
- **Webhook verification** — GitHub uses HMAC-SHA256 (`X-Hub-Signature-256`); GitLab uses a shared secret header (`X-Gitlab-Token`). Both skip verification if no secret is configured.
- **GitHub event routing** — `X-GitHub-Event` header determines handler: `issue_comment`, `pull_request_review_comment`, `pull_request_review`, `pull_request`.
- **GitLab event routing** — `X-Gitlab-Event` header: `Note Hook` (comments on MRs), `Merge Request Hook` (lifecycle).
- **Draft/WIP handling** — both platforms skip draft PRs for lifecycle events.
- **GitHub submit_review()** posts a native review with inline comments via `POST /repos/{repo}/pulls/{pr}/reviews`.
- **GitLab submit_review()** posts a top-level note for the summary, then creates individual diff discussions per finding (requires fetching MR versions for base/head SHAs).
- **Pagination** — `fetch_pr_diff()` on GitHub paginates at 100 files per page. GitLab fetches all diffs in one call.
- **Auth call sites** — `authenticate(webhook_body=body)` is called once per webhook request (before `parse_event()`). `authenticate()` (no args) is called before `post_reaction()` in dispatch, inside each job closure (to handle token expiry during queue wait), and before `fetch_pr_branch()` in CLI mode. GitLab PAT mode is a no-op.
- **Job extra_env** — `JobPayload.extra_env` carries environment variables (e.g. `GITLAB_TOKEN`) into K8s job pods.
