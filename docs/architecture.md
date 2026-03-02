# Architecture

## Request Flow

```
PR comment "@bot do something"  ──or──  PR opened/pushed/reopened
        │                                        │
        ▼                                        ▼
GitHub/GitLab sends webhook             GitHub/GitLab sends webhook
        │                                        │
        └──────────────┬─────────────────────────┘
                       ▼
            POST /webhooks/{platform}
                       │
                       ├─ verify_webhook()         ← signature/token check
                       ├─ parse_event()            ← normalize into PullRequestEvent
                       │
                       ├─ [lifecycle event in REVIEWER_TRIGGERS?]
                       │       ▼
                       │   enqueue_job()            ← no auth check, no reaction
                       │       │
                       │       ▼
                       │   session_queue.enqueue()  ← reviewer with empty prompt
                       │
                       ├─ [comment event?]
                       │       ▼
                       │   extract_mention()        ← identify bot type + prompt
                       │       │
                       │       ▼
                       │   enqueue_job()
                       │       │
                       │       ├─ allowed_users check
                       │       ├─ post_reaction("eyes")
                       │       │
                       │       ▼
                       │   session_queue.enqueue()
                       │
                       └─ [otherwise] → ignored
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
_parse_pr_ref()                     ← validate owner/repo#N format
        │
        ▼
_build_platform()                   ← construct platform from env token
        │
        ├─ fetch_pr_branch()       ← resolve HEAD branch via API
        │
        ▼
review()
        │
        ├─ clone/update workspace
        ├─ fetch diff + comments (parallel)
        ├─ build prompt + run agent
        ├─ parse JSON + filter findings
        │
        ▼
_print_review()                     ← format results for terminal
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

### Webhook Dispatch (`webhooks/dispatch.py`)

- **`enqueue_job()`** — central pre-flight for all events. For comment events: checks authorization against `ALLOWED_USERS`, posts the eyes reaction, and enqueues the job. For lifecycle events: skips authorization and reaction, enqueues a reviewer job with an empty prompt.

### Handlers

- **`worker.handler.review_and_fix()`** — clones the repo, runs the agent with full tools, posts the reply.
- **`review.handler.review()`** — core review logic (clone, fetch diff + comments, run agent, parse JSON, filter findings). Returns a `ReviewResult` without posting. Used by both webhook and CLI modes.
- **`review.handler.review_and_post()`** — webhook entry point. Calls `review()` then posts results to the platform.

### CLI Module (`cli.py`)

- **`_parse_pr_ref()`** — parses `owner/repo#42` into a repo name and PR number.
- **`_build_platform()`** — constructs a platform client from environment tokens (no webhook secret needed).
- **`_run_review()`** — orchestrates the CLI flow: resolve branch, call `review()`, print results, optionally post.

### Agent Runner (`agent/runner.py`)

Wraps the [claude-agent-sdk](https://github.com/anthropics/claude-code-sdk-python) library. Streams messages from the agent process, captures the session ID for multi-turn continuity, and returns the final output. Monkey-patches the SDK message parser to gracefully handle unknown message types.

### Prompt Composition (`agent/prompts.py`)

Loads and composes the system prompt from multiple sources: the bot's base prompt, global coding guidelines, and per-repo/per-language overrides from the `.nominal/` directory. Language detection is based on file extensions in the PR diff.

### Session Tracking (`agent/tracking.py`)

Bridges the session store and the agent runner. Looks up the existing session ID for a PR/bot pair, passes it to the agent for multi-turn continuity, and stores the new session ID after execution.

### Git Workspace (`workspace/git.py`)

Manages per-PR cloned repositories. Handles initial cloning, updating (fetch + reset), pushing changes, and provides a shared `.deps/` directory for private dependency cloning.

### Workspace Setup (`workspace/setup.py`)

Helper functions for branch resolution and workspace construction. `resolve_branch()` fetches the PR branch from the platform API when the webhook payload doesn't include it. `create_workspace()` and `setup_workspace()` construct and initialise `GitWorkspace` instances.

### Session Store and Queue (`agent/session.py`)

- **SessionStore** — an in-memory dict mapping `(platform, repo, pr_number, bot_type)` to a session ID. Used to resume agent sessions across multiple interactions on the same PR.
- **SessionQueue** — per-PR async job queue. Each PR key gets its own `asyncio.Queue` with a single consumer task, ensuring that agent invocations on the same PR run serially (no race conditions). The consumer and queue are cleaned up when drained.

### Workspace Cleaner (`workspace/cleanup.py`)

A background task that periodically scans the workspace directory and deletes workspaces for PRs that are no longer open. Queries each configured platform's API to check PR state. On API failure, workspaces are kept (safe default). Also cleans up orphaned `.deps/` directories and empty parent directories.

### Error Handling (`agent/errors.py`)

An async context manager (`handle_agent_errors()`) that wraps handler execution. Catches workspace setup failures and agent runtime errors, posts user-facing error messages to the platform, and prevents unhandled exceptions from crashing the event loop.

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

## Source Layout

```
nominal_code/
├── main.py              # Entry point: dispatches to webhook server or CLI
├── cli.py               # One-shot review CLI (argparse, platform construction)
├── config.py            # Frozen dataclass config loaded from env vars / files
├── models.py            # Shared enums (EventType, BotType, FileStatus) and dataclasses
├── agent/
│   ├── runner.py        # Wraps claude-agent-sdk; streams messages, captures session ID
│   ├── session.py       # SessionStore (in-memory dict) and SessionQueue (per-PR async queue)
│   ├── tracking.py      # Bridges session store and agent runner for multi-turn continuity
│   ├── prompts.py       # Guideline loading, language detection, system prompt composition
│   └── errors.py        # Async context manager for handler error handling
├── platforms/
│   ├── base.py          # Protocol definitions and shared dataclasses
│   ├── registry.py      # Self-registering platform factory pattern
│   ├── github.py        # GitHub webhook handler and REST API client
│   └── gitlab.py        # GitLab webhook handler and REST API client
├── review/
│   └── handler.py       # Reviewer bot: structured code review with inline comments
├── webhooks/
│   ├── server.py        # aiohttp app with /health and /webhooks/{platform} routes
│   ├── mention.py       # @mention extraction from comment text
│   └── dispatch.py      # Auth check, reaction posting, job enqueueing
├── worker/
│   └── handler.py       # Worker bot: full-access agent that pushes code changes
└── workspace/
    ├── git.py           # GitWorkspace: clone, update, push per-PR workspaces
    ├── setup.py         # Branch resolution and workspace construction helpers
    └── cleanup.py       # Background task to delete stale PR workspaces
```
