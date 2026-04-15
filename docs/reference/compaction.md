# Compaction

When an agent runs for many turns, the accumulated message history (prompts, tool calls, tool results) can approach the LLM's context window limit. Compaction replaces older messages with a concise summary so the agent can continue working without hitting the limit.

## Strategy: Notes-Based Compaction

Nominal Code uses a **notes-based compaction strategy** for agents with notes files (the reviewer and explore sub-agents). Instead of generating a summary at compaction time (which would require an extra LLM call), the agent writes structured findings to a markdown notes file throughout execution via the `WriteNotes` tool. When compaction triggers, the notes file content is used directly as the summary.

### How It Works

```
Turn 1:  agent reads files, writes findings to notes    → context grows
Turn 2:  agent greps for callers, writes to notes       → context grows
  ...
Turn N:  context exceeds threshold
         │
         ├─ Read notes file
         ├─ Notes non-empty?
         │     YES → replace older messages with:
         │           [compaction summary from notes] + [last 4 messages]
         │     NO  → skip compaction, retry next turn
         │
Turn N+1: agent continues with compacted context
```

On each turn after tool execution, the runner checks whether compaction is enabled and a notes file exists:

1. **Read the notes file** from disk.
2. **If empty** (the agent hasn't written anything yet), skip compaction entirely. The runner retries on the next turn — eventually the agent will write findings and compaction becomes possible.
3. **If non-empty**, call `compact_with_notes()` which replaces all messages except the most recent 4 with a continuation message containing the notes content.

### The Continuation Message

After compaction, the message history looks like:

```
[system] [context compacted]
         This session is being continued from a previous conversation
         that ran out of context. The summary below covers the earlier
         portion of the conversation.

         {notes file content}

         Recent messages are preserved verbatim.
         Continue the conversation from where it left off...

[user]   (tool result from turn N-1)
[assistant] (response from turn N-1)
[user]   (tool result from turn N)
[assistant] (response from turn N)
```

The agent picks up exactly where it left off, with its own structured findings as context instead of hundreds of raw tool outputs.

## Trade-Offs

### Prompt Cache Invalidation

When compaction triggers, the LLM's prompt cache is invalidated. The message prefix changes from the original sequence to the continuation summary, so the provider must re-process everything from scratch on the next API call.

This is an inherent cost of any compaction strategy. The mitigation:

- **Compaction should be rare.** Most explore sessions (32 turns) won't hit the token limit. When they do, one cache miss is the price for continuing instead of stopping.
- **Cache rebuilds quickly.** After compaction, the new shorter message sequence starts building a fresh cache. Subsequent turns benefit from caching again.

### No Information Loss in Notes

The raw tool output (grep results, full file contents) is discarded during compaction. But the agent's structured findings — the important parts — survive intact in the notes file. The continuation summary contains them verbatim. The agent loses the noise, not the signal.

### Empty Notes Edge Case

If the agent hasn't written any notes when compaction triggers (e.g., it spent many turns reading files before writing), compaction is skipped entirely. The runner retries every turn until notes appear or the agent finishes. This avoids producing an empty summary that would leave the agent without context.

## Configuration

Compaction is controlled by two parameters on `run_api_agent()`:

| Parameter | Type | Description |
|---|---|---|
| `notes_file_path` | `Path \| None` | Path to the notes file. When provided, enables both note-writing and notes-based compaction |

Compaction triggers when `notes_file_path` is provided and the context window exceeds `COMPACTION_TOKEN_THRESHOLD`. The API runner (`run_api_agent`) handles this automatically for both the reviewer and explore sub-agents.

## Implementation

The compaction module (`agent/compaction.py`) provides:

- `compact_with_notes(messages, notes_content)` — the single entry point. Returns `CompactionResult(messages, summary_text)`.
- `CompactionResult` — frozen dataclass with the compacted message list and the summary text (empty if no compaction occurred).
- `PRESERVE_RECENT_MESSAGES = 4` — number of recent messages to keep verbatim after compaction.

The module is intentionally minimal (~100 lines). The complexity lives in the explore prompt that instructs the agent to write good notes, not in the compaction logic itself.
