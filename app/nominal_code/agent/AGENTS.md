# agent/

Handles Claude agent invocation via two backends, session persistence, prompt composition, and error handling.

## Dual runner architecture

`runner.py` dispatches to one of two backends based on `AgentConfig.use_api`:

- **`api/runner.py`** — calls the Anthropic Messages API directly with tool use. Implements the agentic loop locally: send prompt → process `tool_use` blocks → execute tools via `api/tools.py` → send results back → repeat. Used in CI mode. Stateless (no session continuity).
- **`cli/runner.py`** — spawns the Claude Code CLI via `claude_agent_sdk.query()`. Streams messages, captures session IDs. Used in webhook and CLI modes. Supports session resumption.

The API runner's default model is `claude-sonnet-4-20250514` (hardcoded in `runner.py` as `DEFAULT_API_MODEL`). The CLI runner defers to the Claude Code CLI's configured model unless overridden.

## SDK monkey-patching

`cli/runner.py` patches `claude_agent_sdk._internal.message_parser.parse_message` at import time. The upstream implementation raises `MessageParseError` for unknown message types (e.g. `rate_limit_event`), which kills the async generator and the subprocess transport. The patch catches the error and returns a `SystemMessage` placeholder. Both `_sdk_parser.parse_message` and `_sdk_client.parse_message` must be patched since the SDK copies the function reference.

## API runner tools

`api/tools.py` provides four tools with local execution:

- **Read** — reads files with line numbers, supports offset/limit.
- **Glob** — finds files by pattern, capped at 200 results.
- **Grep** — runs `grep -rn` as a subprocess, 30s timeout.
- **Bash** — runs shell commands. When `allowed_tools` contains patterns like `Bash(git clone*)`, commands are validated against those patterns via `fnmatch`. Unrestricted when no patterns are set.

## Session management (CLI runner only)

- **`SessionStore`** — in-memory dict mapping `(platform, repo, PR, bot_type)` → session ID. No locking; relies on single-threaded asyncio event loop.
- **`SessionQueue`** — per-PR async job queue. Auto-spawns a consumer task per key, self-cleans when drained.
- **`tracking.py`** — `run_and_track_session()` looks up/stores session IDs around `run_agent()` calls.

## File tree

```
agent/
├── runner.py        # Dispatcher: routes to api/ or cli/ runner based on AgentConfig.use_api
├── result.py        # AgentResult dataclass (output, is_error, num_turns, duration_ms, session_id)
├── prompts.py       # Guideline loading (.nominal/ overrides), language detection, system prompt composition
├── errors.py        # handle_agent_errors(): async context manager that catches and posts error replies
├── api/
│   ├── runner.py    # Anthropic API agentic loop (Messages API + tool use)
│   └── tools.py     # Tool definitions and local execution (Read, Glob, Grep, Bash)
└── cli/
    ├── runner.py    # Claude Code CLI wrapper (claude_agent_sdk.query + SDK monkey-patch)
    ├── session.py   # SessionStore (in-memory dict) and SessionQueue (per-PR async queue)
    └── tracking.py  # run_and_track_session(): session lookup/store around agent runs
```

## Non-obvious details

- `AgentResult.session_id` is always empty for the API runner (no session continuity in CI mode).
- The CLI runner captures session ID from both the `init` system message and the `ResultMessage` — whichever is available. The `ResultMessage` takes precedence if it has one.
- `resolve_system_prompt()` in `prompts.py` is the single composition entry point: resolves repo guidelines, detects languages, builds the full prompt.
- Language detection currently supports Python only (`.py`, `.pyi`); extend via `EXTENSION_TO_LANGUAGE` in `prompts.py`.
- Guideline priority: repo `.nominal/guidelines.md` replaces (not appends to) built-in defaults.
- The API runner's `_extract_last_text()` walks the message history in reverse to find the last assistant text — used as fallback output when `max_turns` is reached mid-tool-use.
