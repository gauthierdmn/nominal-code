# agent/

Handles LLM agent invocation via two backends, conversation persistence, prompt composition, and error handling.

## Dual runner architecture

`runner.py` dispatches to one of two backends based on the agent config type (`CliAgentConfig` or `ApiAgentConfig`):

- **`api/runner.py`** — provider-agnostic agentic loop. Uses the `LLMProvider` protocol from `providers/base.py` to call any LLM API. Implements the loop locally: send prompt → process tool_use blocks → execute tools via `api/tools.py` → send results back → repeat. Used in CI mode. Stateless (no conversation continuity).
- **`cli/runner.py`** — spawns the Claude Code CLI via `claude_agent_sdk.query()`. Streams messages, captures conversation IDs. Used in webhook and CLI modes. Supports conversation resumption.

The API runner's default model is resolved from `providers.DEFAULT_MODELS` based on the provider name. The dispatcher in `runner.py` creates the provider via `create_provider()` and resolves the default model. The CLI runner defers to the Claude Code CLI's configured model unless overridden.

## Multi-provider support

`providers/` contains a provider abstraction layer:

- **`types.py`** — canonical types (`Message`, `TextBlock`, `ToolUseBlock`, `ToolResultBlock`, `ToolDefinition`, `LLMResponse`, `StopReason`). All loop logic uses these; providers convert to/from their native SDK.
- **`base.py`** — `LLMProvider` Protocol with a single `send()` method, plus error hierarchy (`ProviderError`, `RateLimitError`, `ContextLengthError`).
- **`anthropic.py`** — `AnthropicProvider` wrapping the `anthropic` SDK. Preserves `cache_control={"type": "ephemeral"}`.
- **`openai.py`** — `OpenAIProvider` for any OpenAI-compatible API (OpenAI, DeepSeek, Groq, Together, Fireworks) via `base_url`.
- **`registry.py`** — `create_provider()` factory, `DEFAULT_MODELS` registry, provider-to-base-URL/API-key-env-var mappings.

Provider selection: `ApiAgentConfig.provider` field (env var `AGENT_PROVIDER`). Defaults to `"anthropic"`.

## SDK monkey-patching

`cli/runner.py` patches `claude_agent_sdk._internal.message_parser.parse_message` at import time. The upstream implementation raises `MessageParseError` for unknown message types (e.g. `rate_limit_event`), which kills the async generator and the subprocess transport. The patch catches the error and returns a `SystemMessage` placeholder. Both `_sdk_parser.parse_message` and `_sdk_client.parse_message` must be patched since the SDK copies the function reference.

## API runner tools

`api/tools.py` provides four tools with local execution:

- **Read** — reads files with line numbers, supports offset/limit.
- **Glob** — finds files by pattern, capped at 200 results.
- **Grep** — runs `grep -rn` as a subprocess, 30s timeout.
- **Bash** — runs shell commands. When `allowed_tools` contains patterns like `Bash(git clone*)`, commands are validated against those patterns via `fnmatch`. Unrestricted when no patterns are set.

Tool definitions use canonical `ToolDefinition` (TypedDict with `name`, `description`, `input_schema`) — provider-agnostic.

## Conversation management

- **`ConversationStore`** — unified in-memory store with two parallel dicts keyed by `(platform, repo, PR, bot_type)`: lightweight conversation IDs and full message histories (API mode only).
- **`JobQueue`** — per-PR async job queue. Auto-spawns a consumer task per key, self-cleans when drained.
- **`tracking.py`** — `run_and_track_conversation()` looks up/stores conversation IDs and messages around `run_agent()` calls.

## File tree

```
agent/
├── runner.py        # Dispatcher: routes to api/ or cli/ runner based on agent config type
├── result.py        # AgentResult dataclass (output, is_error, num_turns, duration_ms, conversation_id)
├── memory.py        # ConversationStore (unified per-PR conversation ID + message history store)
├── prompts.py       # Guideline loading (.nominal/ overrides), language detection, system prompt composition
├── errors.py        # handle_agent_errors(): async context manager that catches and posts error replies
├── providers/
│   ├── registry.py      # create_provider() factory, DEFAULT_MODELS registry
│   ├── base.py          # LLMProvider Protocol, ProviderError, RateLimitError, ContextLengthError
│   ├── types.py         # Canonical types: Message, ContentBlock, ToolDefinition, LLMResponse, StopReason
│   ├── anthropic.py     # AnthropicProvider (wraps anthropic SDK)
│   └── openai.py        # OpenAIProvider (OpenAI, DeepSeek, Groq, Together, Fireworks)
├── api/
│   ├── runner.py    # Provider-agnostic agentic loop
│   └── tools.py     # Tool definitions and local execution (Read, Glob, Grep, Bash)
└── cli/
    ├── runner.py    # Claude Code CLI wrapper (claude_agent_sdk.query + SDK monkey-patch)
    ├── job.py   # JobQueue (per-PR async queue)
    └── tracking.py  # run_and_track_conversation(): conversation lookup/store around agent runs
```

## Non-obvious details

- `AgentResult.conversation_id` carries a CLI conversation ID or a provider response ID. Either can be `None` when the runner/provider does not support continuity.
- The CLI runner captures the conversation ID from both the `init` system message and the `ResultMessage` — whichever is available. The `ResultMessage` takes precedence if it has one.
- `resolve_system_prompt()` in `prompts.py` is the single composition entry point: resolves repo guidelines, detects languages, builds the full prompt.
- Language detection currently supports Python only (`.py`, `.pyi`); extend via `EXTENSION_TO_LANGUAGE` in `prompts.py`.
- Guideline priority: repo `.nominal/guidelines.md` replaces (not appends to) built-in defaults.
- The API runner's `_extract_last_text()` walks the canonical message history in reverse to find the last assistant text — used as fallback output when `max_turns` is reached mid-tool-use.
- Both `anthropic` and `openai` are optional dependencies — install the one matching your chosen provider (or both via the `all` extra).
