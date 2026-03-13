# platforms/

Platform abstraction layer and concrete implementations for GitHub and GitLab.

## Key concepts

- **Protocol-based design** — `Platform` and `ReviewerPlatform` are `typing.Protocol` classes. Implementations satisfy them structurally (no inheritance required).
- **Self-registering factories** — each platform subpackage calls `register_platform()` at import time. `build_platforms()` invokes all factories; a factory returns `None` if its env vars are missing, so unconfigured platforms are silently skipped. Factories can be replaced with `allow_replace=True`.
- **Side-effect imports** — `__init__.py` imports the `github` and `gitlab` subpackages to trigger registration.
- **Unified auth ABC** — `PlatformAuth` in `base.py` is the single abstract base class for all authentication strategies. It defines `get_api_token()`, `get_clone_token()`, and `ensure_auth()`. Both `GitHubPatAuth`, `GitHubAppAuth`, and `GitLabPatAuth` subclass `PlatformAuth` directly.
- **Pluggable GitHub auth** — `GitHubPlatform` delegates all token access to a `PlatformAuth` instance (`GitHubPatAuth` for PATs, `GitHubAppAuth` for GitHub App JWT + installation tokens). The platform itself is auth-mode-agnostic.
- **Pluggable GitLab auth** — `GitLabPlatform` delegates all token access to a `PlatformAuth` instance (`GitLabPatAuth` for static PATs). The ABC is extensible for custom auth strategies (e.g. OAuth).

## File tree

```
platforms/
├── __init__.py        # Side-effect imports (github, gitlab); re-exports build_platforms(), PlatformAuth
├── base.py            # PlatformAuth ABC, Protocol definitions (Platform, ReviewerPlatform) and shared dataclasses
├── registry.py        # register_platform() / build_platforms() — service locator pattern
├── http.py            # request_with_retry(): HTTP request helper with transient error retries
├── github/
│   ├── __init__.py    # Re-exports: GitHubPlatform, auth classes, load_private_key
│   ├── auth.py        # GitHubPatAuth, GitHubAppAuth (both subclass PlatformAuth), load_private_key()
│   ├── ci.py          # CI mode: build_event(), build_platform(), resolve_workspace() from GitHub Actions env vars
│   └── platform.py    # GitHubPlatform, _create_github_platform factory
└── gitlab/
    ├── __init__.py    # Re-exports: GitLabPlatform, GitLabPatAuth
    ├── auth.py        # GitLabPatAuth (subclasses PlatformAuth)
    ├── ci.py          # CI mode: build_event(), build_platform(), resolve_workspace() from GitLab CI env vars
    └── platform.py    # GitLabPlatform, _create_gitlab_platform factory
```

## Platform protocol surface

| Method | Platform | ReviewerPlatform |
|--------|----------|------------------|
| `verify_webhook()` | Yes | Yes |
| `parse_event()` | Yes | Yes |
| `authenticate()` | Yes | Yes |
| `post_reply()` | Yes | Yes |
| `post_reaction()` | Yes | Yes |
| `is_pr_open()` | Yes | Yes |
| `fetch_pr_branch()` | Yes | Yes |
| `build_clone_url(read_only=)` | Yes | Yes |
| `fetch_pr_comments()` | — | Yes |
| `fetch_pr_diff()` | — | Yes |
| `submit_review()` | — | Yes |

## PlatformAuth ABC

All auth strategies subclass `PlatformAuth` from `base.py`:

| Method | Purpose |
|--------|---------|
| `get_api_token(account_id=0)` | Return the API token for the given account |
| `get_clone_token(account_id=0)` | Return a read-only clone token (falls back to API token) |
| `ensure_auth(account_id=0)` | Refresh/load tokens as needed (no-op for static PATs) |

## GitHub authentication

Two auth modes, detected automatically by the factory:

| | PAT mode | App mode |
|---|---|---|
| **Detection** | `GITHUB_TOKEN` set | `GITHUB_APP_ID` + private key set |
| **Class** | `GitHubPatAuth` | `GitHubAppAuth` |
| **Token lifecycle** | Static, never expires | JWT → installation token, cached 1hr, refreshed at 5min margin |
| **Clone token** | Separate `GITHUB_REVIEWER_TOKEN` (optional) via `get_clone_token()` | Same installation token via `get_clone_token()` (permissions handle scoping) |
| **Installation ID** | N/A | Extracted from webhook payload via `authenticate(webhook_body=)`; `GITHUB_INSTALLATION_ID` env var for CLI/CI mode |
| **`ensure_auth()`** | No-op | Calls private `_refresh_token()` to rotate expiring tokens |

- `GitHubPlatform` constructor takes a `PlatformAuth` instance — no raw tokens.
- All HTTP calls pass `headers=self._auth_headers()` per-request (no static headers on the client).
- `authenticate(webhook_body=body)` extracts `installation.id` from webhook payloads, sets a request-scoped ContextVar, and refreshes the token. Call without arguments for CLI/CI mode.
- `load_private_key()` checks `GITHUB_APP_PRIVATE_KEY` (inline PEM) then `GITHUB_APP_PRIVATE_KEY_PATH` (file).
- App mode prefers over PAT mode when both are configured.

## GitLab authentication

| | PAT mode |
|---|---|
| **Detection** | `GITLAB_TOKEN` set |
| **Class** | `GitLabPatAuth` |
| **Token lifecycle** | Static, never expires |
| **Clone token** | Separate `GITLAB_REVIEWER_TOKEN` (optional) via `get_clone_token()` |
| **`ensure_auth()`** | No-op |

- `GitLabPlatform` constructor takes a `PlatformAuth` instance — no raw tokens.
- Auth header is set on the `httpx.AsyncClient` at init, and refreshed after `authenticate()` via `_refresh_client_headers()`.
- `authenticate(webhook_body=body)` delegates to the auth strategy's `ensure_auth()` and refreshes client headers. The `webhook_body` parameter is unused for GitLab PAT auth but available for future OAuth strategies.

## Important details

- **GitHub clone URLs** use `x-access-token:{token}@github.com` format.
- **GitLab clone URLs** use `oauth2:{token}@{host}` format; supports self-hosted via `GITLAB_API_BASE`.
- **Clone URL selection** — `build_clone_url(repo, read_only=True)` uses `get_clone_token()` for read-only reviewer clones; `build_clone_url(repo)` uses `get_api_token()` for read-write worker clones.
- **Webhook verification** — GitHub uses HMAC-SHA256 (`X-Hub-Signature-256`); GitLab uses a shared secret header (`X-Gitlab-Token`). Both skip verification if no secret is configured. Verification is identical for PAT and App modes.
- **GitHub event routing** — `X-GitHub-Event` header determines handler: `issue_comment`, `pull_request_review_comment`, `pull_request_review`, `pull_request`.
- **GitLab event routing** — `X-Gitlab-Event` header: `Note Hook` (comments on MRs), `Merge Request Hook` (lifecycle).
- **Draft/WIP handling** — both platforms skip draft PRs for lifecycle events.
- **GitHub submit_review()** posts a native review with inline comments via `POST /repos/{repo}/pulls/{pr}/reviews`.
- **GitLab submit_review()** posts a top-level note for the summary, then creates individual diff discussions per finding (requires fetching MR versions for base/head SHAs).
- **Pagination** — `fetch_pr_diff()` on GitHub paginates at 100 files per page. GitLab fetches all diffs in one call.
- **is_pr_open()** defaults to `True` on API errors (safe default for workspace cleanup).
- **Auth call sites** — `authenticate(webhook_body=body)` is called once per webhook request (before `parse_event()`). `authenticate()` (no args) is called before `post_reaction()` in dispatch, inside each job closure (to handle token expiry during queue wait), and before `fetch_pr_branch()` in CLI mode. GitLab PAT mode is a no-op.
- **Registry replacement** — `register_platform("gitlab", factory, allow_replace=True)` allows downstream packages (e.g. nominal-cloud) to override default platform factories.
- **Job extra_env** — `JobPayload.extra_env` carries environment variables (e.g. `GITLAB_TOKEN`) into K8s job pods.
