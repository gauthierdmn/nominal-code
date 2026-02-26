# agent/

Handles Claude agent invocation, session persistence, prompt composition, and error handling.

## Key concepts

- **Session continuity** — `SessionStore` maps (platform, repo, PR, bot_type) to a Claude session ID so the agent resumes prior context when a user posts follow-up comments on the same PR.
- **Job serialisation** — `SessionQueue` ensures only one agent job executes per PR/bot pair at a time, preventing concurrent writes to the same workspace.
- **SDK patching** — `runner.py` monkey-patches the Claude Agent SDK message parser to gracefully handle unknown message types (returns a `SystemMessage` placeholder instead of raising).
- **Retry on malformed output** — the reviewer workflow retries up to 2 times when the agent produces invalid JSON, using a retry prompt that feeds back the previous output.

## File tree

```
agent/
├── runner.py      # Wraps claude_agent_sdk.query(); parses messages, captures session ID, measures timing
├── session.py     # SessionStore (in-memory dict) and SessionQueue (per-PR async job queue)
├── tracking.py    # run_and_track_session(): looks up/stores session IDs, delegates to run_agent()
├── prompts.py     # Guideline loading (.nominal/ overrides), language detection, system prompt composition
└── errors.py      # handle_agent_errors(): async context manager that catches and posts error replies
```

## Important details

- `run_agent()` accepts `allowed_tools` to restrict tool access (reviewer is limited; worker is unrestricted).
- `run_agent()` uses `permission_mode="bypassPermissions"` — the agent runs without interactive permission prompts.
- `SessionStore` uses no locking; it relies on the single-threaded asyncio event loop for thread safety.
- `SessionQueue` auto-spawns a consumer task per session key and self-cleans when the queue drains.
- `resolve_system_prompt()` is the one-call composition entry point: resolves repo guidelines, detects languages, builds the full prompt.
- Language detection currently supports Python only (`.py`, `.pyi`); extend via `EXTENSION_TO_LANGUAGE` in `prompts.py`.
- Guideline priority: repo `.nominal/guidelines.md` overrides built-in defaults; repo `.nominal/languages/{lang}.md` overrides built-in language guidelines.
