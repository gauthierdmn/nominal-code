# agent/

Handles LLM agent invocation via two backends, prompt composition, and error handling. LLM providers live in `llm/`, conversation persistence in `conversation/`.

## Dual runner architecture

`router.py` dispatches to one of two backends based on the agent config type (`CliAgentConfig` or `ApiAgentConfig`):

- **`api/runner.py`** — provider-agnostic agentic loop. Uses the `LLMProvider` protocol from `llm/provider.py` to call any LLM API. Implements the loop locally: send prompt → process tool_use blocks → execute tools via `api/tools.py` → send results back → repeat. Used in CI mode. Exports `run()` (stateless) and `handle_event()` (with conversation persistence).
- **`cli/runner.py`** — spawns the Claude Code CLI via `claude_agent_sdk.query()`. Streams messages, captures conversation IDs. Used in webhook and CLI modes. Exports `run()` (stateless) and `handle_event()` (with conversation persistence).

The API runner's default model is resolved from `llm.registry.DEFAULT_MODELS` based on the provider name. The dispatcher in `router.py` creates the provider via `create_provider()` and resolves the default model. The CLI runner defers to the Claude Code CLI's configured model unless overridden.

## SDK monkey-patching

`cli/runner.py` patches `claude_agent_sdk._internal.message_parser.parse_message` at import time. The upstream implementation raises `MessageParseError` for unknown message types (e.g. `rate_limit_event`), which kills the async generator and the subprocess transport. The patch catches the error and returns a `SystemMessage` placeholder. Both `_sdk_parser.parse_message` and `_sdk_client.parse_message` must be patched since the SDK copies the function reference.

## API runner tools

`api/tools.py` provides four tools with local execution:

- **Read** — reads files with line numbers, supports offset/limit.
- **Glob** — finds files by pattern, capped at 200 results.
- **Grep** — runs `grep -rn` as a subprocess, 30s timeout.
- **Bash** — runs shell commands. When `allowed_tools` contains patterns like `Bash(git clone*)`, commands are validated against those patterns via `fnmatch`. Unrestricted when no patterns are set.

Tool definitions use canonical `ToolDefinition` (TypedDict with `name`, `description`, `input_schema`) — provider-agnostic.

## File tree

```
agent/
├── router.py        # run() dispatches to api/ or cli/ runner; handle_event() dispatches with conversation persistence
├── result.py        # AgentResult dataclass (output, is_error, num_turns, duration_ms, conversation_id, cost)
├── prompts.py       # Guideline loading (.nominal/ overrides), language detection, system prompt composition
├── errors.py        # handle_agent_errors(): async context manager that catches and posts error replies
├── api/
│   ├── runner.py    # Provider-agnostic agentic loop: run() + handle_event()
│   └── tools.py     # Tool definitions and local execution (Read, Glob, Grep, Bash)
└── cli/
    └── runner.py    # Claude Code CLI wrapper: run() + handle_event() (claude_agent_sdk.query + SDK monkey-patch)
```

## Non-obvious details

- `AgentResult.conversation_id` carries a CLI conversation ID or a provider response ID. Either can be `None` when the runner/provider does not support continuity.
- The CLI runner captures the conversation ID from both the `init` system message and the `ResultMessage` — whichever is available. The `ResultMessage` takes precedence if it has one.
- `resolve_system_prompt()` in `prompts.py` is the single composition entry point: resolves repo guidelines, detects languages, builds the full prompt.
- Language detection currently supports Python only (`.py`, `.pyi`); extend via `EXTENSION_TO_LANGUAGE` in `prompts.py`.
- Guideline priority: repo `.nominal/guidelines.md` replaces (not appends to) built-in defaults.
- The API runner's `_extract_last_text()` walks the canonical message history in reverse to find the last assistant text — used as fallback output when `max_turns` is reached mid-tool-use.
- `anthropic`, `openai`, and `google-genai` are optional dependencies — install the one matching your chosen provider (or all via the `all` extra).
