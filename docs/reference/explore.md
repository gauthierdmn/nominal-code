# Exploration Pipeline

The `review.explore` package implements the plan → explore stages of the review pipeline. A planner agent reads the project's coding guidelines and partitions the work into investigation concerns. Each concern is assigned to a parallel explorer agent that gathers codebase context for the reviewer.

## Three-Stage Pipeline

```
changed files + diffs + guidelines
        │
        ├─ Planner (1 turn, submit_plan tool)
        │   → list[ExploreGroup]
        │
        ├─ Explorer agents (parallel, up to 32 turns each)
        │   → notes files via WriteNotes
        │
        └─ assemble_notes()
            → context string for the reviewer
```

When the PR has fewer than 8 changed files, the planner is skipped and a single explorer handles all concerns with a fallback prompt.

## Planner

Single LLM call with `tool_choice=REQUIRED` and the `submit_plan` tool. The planner receives:

- Changed file paths with `+N -M` line counts
- The project's coding guidelines (when available)

It returns 2–5 concern-based `ExploreGroup` objects. When no guidelines are available, it falls back to default concerns: callers and dependencies, test coverage, type safety and contracts, knock-on effects.

The planner does NOT assign files to groups — it assigns investigation concerns. Each group has a `label` and a `prompt` with specific exploration instructions.

## Explorer

Each explorer agent runs via `run_api_agent()` with these tools:

| Tool | Purpose |
|---|---|
| Read | Read file contents |
| Glob | Find files by pattern |
| Grep | Search file contents |
| Bash | Read-only shell commands (`git diff`, `git log`, etc.) |
| WriteNotes | Record structured findings to a notes file |

Explorer agents receive only the planner's concern-focused prompt — no diffs, no file lists. They discover everything through their tools (`git diff HEAD~1`, Read, Grep).

### WriteNotes

Explorers record findings to a markdown notes file organized under headings:

- `## Callers` — functions calling the changed code
- `## Tests` — test coverage for changed modules
- `## Type Definitions` — types, protocols, base classes
- `## Knock-on Effects` — callers not updated, broken references
- `## Additional Context` — surrounding code for understanding

The notes file serves two purposes:

1. **Primary deliverable** — the reviewer receives the notes content, not the raw conversation.
2. **Compaction summary** — when the context window fills up, the notes file replaces older messages at zero cost. See [Compaction](compaction.md).

Each explorer gets its own notes file (no write conflicts during parallel execution).

## Configuration

Each stage can use a different LLM provider and model:

```yaml
agent:
  reviewer:
    provider: "anthropic"
    model: "claude-sonnet-4-20250514"
  planner:
    provider: "google"
    model: "gemini-2.5-flash"
  explorer:
    provider: "google"
    model: "gemini-2.5-flash"
```

When `planner` or `explorer` are omitted, they inherit from `reviewer`. See [Configuration](configuration.md) and [Environment Variables](env-vars.md).

| Parameter | Default | Description |
|---|---|---|
| `file_threshold` | `8` | Min changed files to trigger the planner |
| Turn budget per explorer | `32` | `DEFAULT_MAX_TURNS_PER_SUB_AGENT` |

## Result Types

### `ExploreGroup`

Returned by the planner, consumed by the explorer.

| Field | Type | Description |
|---|---|---|
| `label` | `str` | Short concern label (e.g., "callers", "test-coverage") |
| `prompt` | `str` | Specific exploration instructions for the explorer |

### `SubAgentResult`

| Field | Type | Description |
|---|---|---|
| `group` | `ExploreGroup` | The concern this agent explored |
| `output` | `str` | Agent's text output |
| `is_error` | `bool` | Whether execution errored |
| `num_turns` | `int` | Agentic turns taken |
| `duration_ms` | `int` | Wall-clock duration |
| `cost` | `CostSummary \| None` | Token/cost info |
| `notes` | `str` | Structured findings from the notes file |

### `ParallelExploreResult`

| Field | Type | Description |
|---|---|---|
| `sub_results` | `tuple[SubAgentResult, ...]` | Per-agent results |
| `metrics` | `AggregatedMetrics` | Aggregated metrics (tokens, costs, turns) |

## Bundled Prompts

- `prompts/explore/explorer.md` — explorer system prompt (read-only context gathering via tools).
- `prompts/explore/planner.md` — planner system prompt (concern-based grouping via `submit_plan` tool).
