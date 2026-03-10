# platforms/

Platform abstraction layer and concrete implementations for GitHub and GitLab.

## Key concepts

- **Protocol-based design** — `Platform` and `ReviewerPlatform` are `typing.Protocol` classes. Implementations satisfy them structurally (no inheritance required).
- **Self-registering factories** — each platform subpackage calls `register_platform()` at import time. `build_platforms()` invokes all factories; a factory returns `None` if its env vars are missing, so unconfigured platforms are silently skipped.
- **Side-effect imports** — `__init__.py` imports the `github` and `gitlab` subpackages to trigger registration.
- **Pluggable GitHub auth** — `GitHubPlatform` delegates all token access to a `GitHubAuth` abstraction (`GitHubPatAuth` for PATs, `GitHubAppAuth` for GitHub App JWT + installation tokens). The platform itself is auth-mode-agnostic.

## File tree

```
platforms/
├── __init__.py        # Side-effect imports (github, gitlab); re-exports build_platforms()
├── base.py            # Protocol definitions (Platform, ReviewerPlatform) and shared dataclasses
├── registry.py        # register_platform() / build_platforms() — service locator pattern
├── http.py            # request_with_retry(): HTTP request helper with transient error retries
├── github/
│   ├── __init__.py    # Re-exports: GitHubPlatform, auth classes, factory, load_private_key
│   ├── auth.py        # GitHubAuth ABC, GitHubPatAuth, GitHubAppAuth, load_private_key()
│   ├── ci.py          # CI mode: build_event(), build_platform(), resolve_workspace() from GitHub Actions env vars
│   └── platform.py    # GitHubPlatform, _create_github_platform factory
└── gitlab/
    ├── __init__.py    # Re-exports: GitLabPlatform, factory
    ├── ci.py          # CI mode: build_event(), build_platform(), resolve_workspace() from GitLab CI env vars
    └── platform.py    # GitLabPlatform, _create_gitlab_platform factory
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
| `ensure_auth()` | Yes | Yes |
| `fetch_pr_comments()` | — | Yes |
| `fetch_pr_diff()` | — | Yes |
| `submit_review()` | — | Yes |
| `build_reviewer_clone_url()` | — | Yes |

## GitHub authentication

Two auth modes, detected automatically by the factory:

| | PAT mode | App mode |
|---|---|---|
| **Detection** | `GITHUB_TOKEN` set | `GITHUB_APP_ID` + private key set |
| **Class** | `GitHubPatAuth` | `GitHubAppAuth` |
| **Token lifecycle** | Static, never expires | JWT → installation token, cached 1hr, refreshed at 5min margin |
| **Reviewer token** | Separate `GITHUB_REVIEWER_TOKEN` (optional) | Same installation token (permissions handle scoping) |
| **Installation ID** | N/A | Extracted from webhook `installation.id`; `GITHUB_INSTALLATION_ID` env var for CLI mode |
| **`ensure_auth()`** | No-op | Calls `refresh_if_needed()` to rotate expiring tokens |

- `GitHubPlatform` constructor takes a `GitHubAuth` instance — no raw tokens.
- All HTTP calls pass `headers=self._auth_headers()` per-request (no static headers on the client).
- `parse_event()` extracts `installation.id` from webhook payloads and calls `auth.set_installation_id()`.
- `load_private_key()` checks `GITHUB_APP_PRIVATE_KEY` (inline PEM) then `GITHUB_APP_PRIVATE_KEY_PATH` (file).
- App mode prefers over PAT mode when both are configured.

## Important details

- **GitHub clone URLs** use `x-access-token:{token}@github.com` format.
- **GitLab clone URLs** use `oauth2:{token}@{host}` format; supports self-hosted via `GITLAB_API_BASE`.
- **Webhook verification** — GitHub uses HMAC-SHA256 (`X-Hub-Signature-256`); GitLab uses a shared secret header (`X-Gitlab-Token`). Both skip verification if no secret is configured. Verification is identical for PAT and App modes.
- **GitHub event routing** — `X-GitHub-Event` header determines handler: `issue_comment`, `pull_request_review_comment`, `pull_request_review`, `pull_request`.
- **GitLab event routing** — `X-Gitlab-Event` header: `Note Hook` (comments on MRs), `Merge Request Hook` (lifecycle).
- **Draft/WIP handling** — both platforms skip draft PRs for lifecycle events.
- **GitHub submit_review()** posts a native review with inline comments via `POST /repos/{repo}/pulls/{pr}/reviews`.
- **GitLab submit_review()** posts a top-level note for the summary, then creates individual diff discussions per finding (requires fetching MR versions for base/head SHAs).
- **Pagination** — `fetch_pr_diff()` on GitHub paginates at 100 files per page. GitLab fetches all diffs in one call.
- **is_pr_open()** defaults to `True` on API errors (safe default for workspace cleanup).
- **ensure_auth() call sites** — called after `parse_event()` in the webhook handler, before `post_reaction()` in dispatch, inside each job closure (to handle token expiry during queue wait), and before `fetch_pr_branch()` in CLI mode. GitLab's implementation is a no-op.
