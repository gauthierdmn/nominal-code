# Sub-Agents

The reviewer agent can spawn **explore sub-agents** on demand via the `Agent` tool. Sub-agents run their own agentic loop with isolated tools and a separate turn budget, then return their findings as notes content to the reviewer.

## How It Works

```
Reviewer agent (multi-turn, up to 8 turns)
    │
    ├─ [simple lookup] ──> Read / Grep / Glob directly
    │
    ├─ [deep investigation] ──> Agent tool
    │                              │
    │                   +──────────+──────────+
    │                   │  Explore Sub-Agent  │
    │                   │  (up to 32 turns)   │
    │                   │  Read, Glob, Grep,  │
    │                   │  Bash, WriteNotes   │
    │                   +──────────+──────────+
    │                              │
    │                        notes.md (via WriteNotes)
    │                              │
    │<──── notes content ──────────+
    │
    └─ submit_review ──> structured JSON review
```

The reviewer decides when exploration is needed based on what it sees in the diffs. It provides a task prompt describing what to investigate (e.g. "find all callers of `process_event` and check if they handle the new return type"). The sub-agent discovers everything through its tools.

Multiple Agent calls in the same turn are dispatched concurrently via `asyncio.create_task`.

## Explore Sub-Agent

Each explore sub-agent runs via `run_api_agent()` with these tools:

| Tool | Purpose |
|---|---|
| Read | Read file contents |
| Glob | Find files by pattern |
| Grep | Search file contents |
| Bash | Read-only shell commands (`git diff`, `git log`, etc.) |
| WriteNotes | Record structured findings to a notes file |

Sub-agents do **not** have `submit_review` or `Agent` — they cannot produce reviews or spawn other sub-agents.

### WriteNotes

Sub-agents record findings to a markdown notes file organized under headings:

- `## Callers` — functions calling the changed code
- `## Tests` — test coverage for changed modules
- `## Type Definitions` — types, protocols, base classes
- `## Knock-on Effects` — callers not updated, broken references
- `## Additional Context` — surrounding code for understanding

The notes file serves two purposes:

1. **Primary deliverable** — the reviewer receives the notes content as the Agent tool result, not the raw conversation.
2. **Compaction summary** — when the sub-agent's context window fills up, the notes file replaces older messages at zero cost. See [Compaction](compaction.md).

Each sub-agent gets its own notes file in a temporary directory (no write conflicts during concurrent execution). The directory is cleaned up after notes are read back.

## Configuration

The reviewer and explorer can use different LLM providers and models:

```yaml
agent:
  reviewer:
    provider: "anthropic"
    model: "claude-sonnet-4-20250514"
  explorer:                   # optional, falls back to reviewer
    provider: "google"
    model: "gemini-2.5-flash"
```

When `explorer` is omitted, it inherits from `reviewer`. See [Configuration](configuration.md) and [Environment Variables](env-vars.md).

| Parameter | Default | Description |
|---|---|---|
| `agent.reviewer.max_turns` | `8` | Turn budget for the reviewer agent |
| `agent.explorer.max_turns` | `32` | Turn budget per explore sub-agent |

## Cost Tracking

Sub-agent costs are collected as `tuple[CostSummary, ...]` on `AgentResult.sub_agent_costs` and propagated to `ReviewResult.sub_agent_costs`. Each entry carries token counts, API call count, and estimated dollar cost for one sub-agent invocation.

The reviewer's `_log_review_costs()` function sums the reviewer's own cost with all sub-agent costs to produce the total pipeline cost.

## Fallback Behavior

If the reviewer reaches its turn limit (`agent.reviewer.max_turns`) without calling `submit_review`, a fallback single-turn call is made. This call receives the original prompt plus any notes the reviewer accumulated, and is forced to call `submit_review` immediately via `tool_choice=REQUIRED`.

## Bundled Prompts

- `prompts/explore/explorer.md` — system prompt for explore sub-agents (read-only codebase investigation via tools).
- `prompts/explore/suffix.md` — sub-agent suffix template appended to system prompts ("You are a background sub-agent...").
