# webhooks/

HTTP webhook server, @mention extraction, and job dispatch.

## Key concepts

- **aiohttp-based server** — `create_app()` builds an `aiohttp.web.Application` with a health check and per-platform webhook routes.
- **Event routing** — webhook handler verifies the signature, parses the event, then routes to the reviewer (auto-trigger or @mention) or worker (@mention).
- **Authorization** — comment events are gated by `config.allowed_users`; lifecycle auto-triggers bypass auth.
- **Eyes reaction** — the bot posts an "eyes" emoji reaction on comment events to acknowledge receipt before processing.

## File tree

```
webhooks/
├── server.py      # create_app(), webhook handler, event routing (POST /webhooks/{platform}, GET /health)
├── mention.py     # extract_mention(): regex-based @botname extraction from comment text
└── dispatch.py    # enqueue_job(): auth check, reaction posting, job enqueueing via SessionQueue
```

## Important details

- **Route structure** — `/health` (GET) returns 200; `/webhooks/{platform_name}` (POST) handles events for each configured platform.
- **Webhook handler flow**: verify signature → parse event → check auto-triggers → extract mentions → enqueue job.
- **Auto-trigger** — if the event is a `LifecycleEvent` and its `event_type` is in `config.reviewer_triggers`, the reviewer runs automatically (no @mention needed).
- **Mention priority** — if both worker and reviewer are mentioned in the same comment, only the worker is dispatched.
- **Response codes** — 401 for invalid signatures; 200 with JSON status for everything else (processed, ignored, no mention).
- `extract_mention()` is case-insensitive and returns the text after `@botname`, or `None` if no mention found.
- `enqueue_job()` logs the first 100 characters of comment bodies for brevity.
