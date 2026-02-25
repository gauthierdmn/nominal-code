# Architecture

## Request Flow

```
PR comment "@bot do something"
        │
        ▼
GitHub/GitLab sends webhook
        │
        ▼
POST /webhooks/{platform}
        │
        ├─ verify_webhook()        ← signature/token check
        ├─ parse_webhook()         ← normalize into ReviewComment
        ├─ extract_mention()       ← identify bot type + prompt
        │
        ▼
handle_comment()
        │
        ├─ allowed_users check     ← reject unauthorized users
        ├─ post_reaction("eyes")   ← immediate acknowledgment
        │
        ▼
session_queue.enqueue(job)         ← returns HTTP 200 immediately
        │
        ▼
job runs serially per PR
        │
        ├─ [WORKER]  clone/update → run agent (all tools) → post reply
        └─ [REVIEWER] clone/update → fetch diff + comments → run agent (read-only) → submit review
```

## CLI Flow

```
nominal-code review owner/repo#42 [--dry-run] [--prompt "..."]
        │
        ▼
parse_pr_ref()                     ← validate owner/repo#N format
        │
        ▼
build_platform()                   ← construct platform from env token
        │
        ├─ fetch_pr_branch()       ← resolve HEAD branch via API
        │
        ▼
execute_review()
        │
        ├─ clone/update workspace
        ├─ fetch diff + comments (parallel)
        ├─ build prompt + run agent
        ├─ parse JSON + filter findings
        │
        ▼
print_review()                     ← format results for terminal
        │
        ├─ [unless --dry-run] submit_review() or post_reply()
        │
        ▼
exit 0
```

## Components

### Webhook Server

An [aiohttp](https://docs.aiohttp.org/) application that exposes:

- `GET /health` — returns `{"status": "ok"}`
- `POST /webhooks/{platform}` — one route per enabled platform

Each incoming request is verified, parsed, and dispatched. The HTTP response is returned immediately; actual processing happens asynchronously via the session queue.

### Platform Registry

A factory-based registry where each platform module self-registers at import time. At startup, `build_platforms()` calls each factory and returns only the platforms that are configured (i.e. have their required tokens set).

### Handlers

- **`shared.handle_comment()`** — central dispatch. Checks authorization, posts the eyes reaction, and enqueues the job.
- **`worker.process_comment()`** — clones the repo, runs the agent with full tools, posts the reply.
- **`reviewer.execute_review()`** — core review logic (clone, fetch diff + comments, run agent, parse JSON, filter findings). Returns an `ExecuteReviewResult` without posting. Used by both webhook and CLI modes.
- **`reviewer.process_comment()`** — webhook entry point. Calls `execute_review()` then posts results to the platform.

### CLI Module

- **`cli.parse_pr_ref()`** — parses `owner/repo#42` into a repo name and PR number.
- **`cli.build_platform()`** — constructs a platform client from environment tokens (no webhook secret needed).
- **`cli.run_review()`** — orchestrates the CLI flow: resolve branch, call `execute_review()`, print results, optionally post.

### Agent Runner

Wraps the [claude-agent-sdk](https://github.com/anthropics/claude-code-sdk-python) library. Streams messages from the agent process, captures the session ID for multi-turn continuity, and returns the final output.

### Git Workspace

Manages per-PR cloned repositories. Handles initial cloning, updating (fetch + reset), and provides a shared `.deps/` directory for private dependency cloning.

### Session Store and Queue

- **SessionStore** — an in-memory dict mapping `(platform, repo, pr_number, bot_type)` to a session ID. Used to resume agent sessions across multiple interactions on the same PR.
- **SessionQueue** — per-PR async job queue. Each PR key gets its own `asyncio.Queue` with a single consumer task, ensuring that agent invocations on the same PR run serially (no race conditions). The consumer and queue are cleaned up when drained.

### Workspace Cleaner

A background task that periodically scans the workspace directory and deletes workspaces for PRs that are no longer open. Queries each configured platform's API to check PR state. On API failure, workspaces are kept (safe default). Also cleans up orphaned `.deps/` directories and empty parent directories.

## Workspace Directory Layout

```
{WORKSPACE_BASE_DIR}/
└── {owner}/
    └── {repo}/
        ├── .deps/           ← shared private dependency clones
        ├── pr-1/            ← workspace for PR #1
        ├── pr-2/            ← workspace for PR #2
        └── pr-N/
```

Each `pr-{N}` directory is a shallow clone of the repository checked out to the PR's head branch.

## Session Queue

The session queue ensures that only one agent runs per PR at a time. This prevents race conditions when multiple comments arrive in quick succession on the same PR.

Jobs are keyed by `(platform_name, repo_full_name, pr_number, bot_type)`. When a job is enqueued:

1. If no queue exists for that key, one is created along with a consumer task.
2. The job is put into the queue.
3. The consumer processes jobs serially, one at a time.
4. When the queue drains, the consumer task and queue are cleaned up.

## Cleanup Loop

The workspace cleaner lifecycle:

1. **Startup** — `run_once()` immediately removes stale workspaces from a previous run.
2. **Periodic** — `start()` launches a background task that sleeps for `CLEANUP_INTERVAL_HOURS`, then runs cleanup, and repeats.
3. **Shutdown** — the background task is cancelled when the server stops.

Set `CLEANUP_INTERVAL_HOURS=0` to disable the periodic loop entirely.
