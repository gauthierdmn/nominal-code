# Sub-Agents Reference

The `agent.sub_agents` package provides parallel sub-agent execution for codebase exploration and planning. Sub-agents are isolated agent instances that compose on top of `run_api_agent()` with per-type tool restrictions.

## Agent Types

```python
from nominal_code.agent.sub_agents import AgentType, AGENT_TYPE_TOOLS
```

| Type | Value | Allowed Tools |
|---|---|---|
| `AgentType.EXPLORE` | `"explore"` | Read, Glob, Grep, Bash |
| `AgentType.PLAN` | `"plan"` | Read, Glob, Grep, Bash |

No agent type includes `submit_review` or `Agent` — sub-agents cannot produce reviews or spawn child agents.

## Entry Points

### `run_explore_with_planner()`

High-level API that handles planning and parallel execution automatically.

```python
from nominal_code.agent.sub_agents import run_explore_with_planner

result = await run_explore_with_planner(
    changed_files=["src/auth.py", "src/models.py", ...],
    diffs={"src/auth.py": "+new line\n-old line", ...},
    cwd=Path("/path/to/repo"),
    provider=provider,           # LLMProvider instance (shared, not closed)
    model="gemini-2.5-flash",
    provider_name=ProviderName.GOOGLE,
    system_prompt="",            # Empty = use bundled explore.md
    planner_model="",            # Empty = use same as model
    max_turns=12,                # Total budget, divided across groups
    file_threshold=8,            # Min files to trigger parallel mode
    compaction_config=CompactionConfig(),
)
```

**Behavior:**
- If `len(changed_files) < file_threshold` → single agent explores all files.
- If `>= file_threshold` → planner partitions files into groups, parallel agents explore each group.
- If planner fails → falls back to single agent.

### `run_explore()`

Lower-level API that takes pre-planned groups.

```python
from nominal_code.agent.sub_agents import run_explore, ExploreGroup

groups = [
    ExploreGroup(label="auth", files=["src/auth.py"], prompt="Check callers of authenticate()"),
    ExploreGroup(label="api", files=["src/api.py"], prompt="Verify route handlers"),
]

result = await run_explore(
    groups=groups,
    cwd=Path("/path/to/repo"),
    provider=provider,
    model="gemini-2.5-flash",
    max_turns=12,  # Total, divided across groups (min 4 each)
)
```

### `plan_exploration_groups()`

Run only the planner step (single LLM call, no tools).

```python
from nominal_code.agent.sub_agents import plan_exploration_groups

groups = await plan_exploration_groups(
    changed_files=["a.py", "b.py", ...],
    diffs={"a.py": "...", "b.py": "..."},
    provider=provider,
    model="gemini-2.5-flash",
)
# Returns list[ExploreGroup] | None
```

## Result Types

### `ParallelExploreResult`

Returned by `run_explore()` and `run_explore_with_planner()`.

| Field | Type | Description |
|---|---|---|
| `sub_results` | `tuple[SubAgentResult, ...]` | Per-agent results |
| `metrics` | `AggregatedMetrics` | Aggregated metrics |

### `SubAgentResult`

| Field | Type | Description |
|---|---|---|
| `group` | `ExploreGroup` | The group this agent explored |
| `output` | `str` | Agent's text output |
| `is_error` | `bool` | Whether execution errored |
| `num_turns` | `int` | Agentic turns taken |
| `duration_ms` | `int` | Wall-clock duration |
| `messages` | `tuple[Message, ...]` | Full message history |
| `cost` | `CostSummary \| None` | Token/cost info |

### `AggregatedMetrics`

Token counts and API calls are summed across sub-agents. Duration is wall-clock (not sum).

| Field | Type | Default |
|---|---|---|
| `total_turns` | `int` | `0` |
| `total_api_calls` | `int` | `0` |
| `total_input_tokens` | `int` | `0` |
| `total_output_tokens` | `int` | `0` |
| `total_cache_creation_tokens` | `int` | `0` |
| `total_cache_read_tokens` | `int` | `0` |
| `total_cost_usd` | `float \| None` | `None` |
| `duration_ms` | `int` | `0` |
| `num_groups` | `int` | `0` |
| `group_labels` | `tuple[str, ...]` | `()` |

## Configuration

| Parameter | Default | Description |
|---|---|---|
| `file_threshold` | `8` | Min changed files to trigger the planner |
| `max_turns` | `0` (unlimited) | Total turn budget across all sub-agents |
| `planner_model` | Same as `model` | Model for the planner call |
| `compaction_config` | `None` | Message compaction for long conversations |

### Turn Allocation

When `max_turns > 0`, each sub-agent gets `max(4, max_turns // num_groups)` turns. When `max_turns == 0`, each gets `DEFAULT_MAX_TURNS_PER_SUB_AGENT` (32).

## Bundled Prompts

- `prompts/sub_agents/explore.md` — exploration agent system prompt (read-only context gathering).
- `prompts/sub_agents/planner.md` — planner system prompt (file grouping into JSON).

Load programmatically:

```python
from nominal_code.agent.sub_agents import load_explore_system_prompt, load_planner_system_prompt

explore_prompt = load_explore_system_prompt()
planner_prompt = load_planner_system_prompt()
```

## Utility Functions

- `allocate_turns(total_turns, num_groups)` — compute per-group turn budget.
- `aggregate_metrics(sub_results, duration_ms)` — sum metrics across sub-agents.
- `build_planner_user_message(changed_files, diffs)` — format the planner input.
- `parse_planner_response(response_text, changed_files)` — parse planner JSON output.
