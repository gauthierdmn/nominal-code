# Plan: Improve Planner & Explore Sub-Agent System

## Context

The planner+explore sub-agent system in nominal-code has several gaps between what the prompts promise and what the code delivers. The explore system prompt (`explore.md:19`) claims diffs are in the prompt, but `_run_single_sub_agent()` never sends them. The planner sees only file paths and `+N -N` counts, so it groups by path proximity rather than logical cohesion. Group constraints are hardcoded and break on large PRs. We use Claude Code's implementation as the reference for how a production agent orchestration handles these same problems.

## Changes

### 1. Add diff summarization utilities to `app/nominal_code/review/diff.py`

Add two functions after the existing `HUNK_HEADER_PATTERN`:

- **New regex** `HUNK_SYMBOL_PATTERN` — captures the optional trailing text from hunk headers (`@@ -N,M +N,M @@ def some_function` → `"def some_function"`). The existing `HUNK_HEADER_PATTERN` at line 7 only captures line numbers.

- **`extract_hunk_symbols(patch: str) -> list[str]`** — Parse a unified diff and return deduplicated function/class names from hunk headers. Strip common prefixes like `def `, `class `, `async def `. Return empty list when headers have no context text (gracefully handles languages without git diff function detection).

- **`summarize_diff(patch: str) -> str`** — Return a compact one-liner: `"+12 -3 (validate_token, UserModel.__init__)"`. Uses line counting logic similar to `build_planner_user_message` + `extract_hunk_symbols`. When no symbols found, return just `"+12 -3"`.

### 2. Enrich planner input — `app/nominal_code/agent/sub_agents/planner.py`

Modify `build_planner_user_message()` (line 88) to use `summarize_diff()` instead of inline line counting. Change the per-file format from:

```
  src/auth.py  |  +12 -3
```

to:

```
  src/auth.py  |  +12 -3  (validate_token, refresh_session)
```

Append `\nTotal: {N} files` at the end so the planner knows scale.

### 3. Dynamic group constraints — `app/nominal_code/agent/sub_agents/planner.py`

Add `compute_group_bounds(file_count: int) -> tuple[int, int, int]` returning `(min_groups, max_groups, max_files_per_group)`:

| Files    | min_groups | max_groups | max_files_per_group |
|----------|-----------|------------|---------------------|
| 8–12     | 2         | 3          | 6                   |
| 13–25    | 2         | 5          | 8                   |
| 26–50    | 3         | 7          | 10                  |
| 51+      | 4         | 10         | 12                  |

In `plan_exploration_groups()`, after loading the system prompt, call `compute_group_bounds(len(changed_files))` and inject the bounds into the prompt. Use `str.replace()` on sentinel tokens (`__MIN_GROUPS__`, `__MAX_GROUPS__`, `__MAX_FILES_PER_GROUP__`) rather than `str.format()` — the prompt contains JSON with curly braces that would clash with format syntax.

### 4. Rewrite planner prompt — `app/nominal_code/prompts/sub_agents/planner.md`

Full rewrite addressing four issues at once:

**a) Add downstream consumer context (new section after the task description):**
Explain that each group goes to a read-only explore sub-agent with Read/Glob/Grep/Bash tools, whose output feeds a reviewer agent that has NO tools.

**b) Replace hardcoded group bounds with sentinel tokens:**
`"Create __MIN_GROUPS__ to __MAX_GROUPS__ groups"` and `"Each group should have at most __MAX_FILES_PER_GROUP__ files"`.

**c) Replace the vague example** with a concrete one showing specific function names, callers to search for, and tests to check.

**d) Revise the overlap rule:**
Keep strict partitioning for changed files, but add: *"If a shared file affects multiple groups, assign it to the group where it is most central. In other groups' prompts, instruct the sub-agent to also read that shared file for context."* This preserves clean validation while letting the planner express cross-cutting awareness through prompt text.

### 5. Thread diffs to explore agents — `app/nominal_code/agent/sub_agents/runner.py`

This is the highest-impact change.

**a) Add `build_explore_user_message()` helper:**

```python
def build_explore_user_message(
    group: ExploreGroup,
    diffs: dict[str, str],
    all_groups: list[ExploreGroup],
) -> str:
```

Builds a user message with three sections:
1. **"Your assigned files"** — full unified diffs for each file in `group.files`
2. **"Changes in other groups"** — one line per other group: label + file paths with `summarize_diff()` output. This gives cross-group awareness cheaply (~200-400 tokens).
3. **"Exploration task"** — the planner-authored `group.prompt`

**Decision on full vs. group diffs:** Give **group's own diffs in full** + **one-line summaries of other groups**. Full diffs for all groups would be 20k+ tokens on large PRs and flood the explore agent's context. The summary is enough for the agent to know "group B changed `validate_token` in `auth.py`" and decide to grep for callers.

**b) Modify `run_explore()` signature** — add `diffs: dict[str, str] = {}` parameter (default empty for backward compatibility). Thread it to `_run_single_sub_agent()`.

**c) Modify `_run_single_sub_agent()` signature** — add `diffs: dict[str, str]` and `all_groups: list[ExploreGroup]`. Replace `prompt=group.prompt` at line 352 with `prompt=build_explore_user_message(group, diffs, all_groups)`.

**d) Modify `run_explore_with_planner()`** — pass `diffs` to `run_explore()`.

### 6. Update explore prompt — `app/nominal_code/prompts/sub_agents/explore.md`

Lines 19-23 already claim diffs are present — now they actually will be. Adjust wording to also mention the "Changes in other groups" summary section:

```
You also have a brief summary of changes in other exploration groups. Use this to
decide whether to grep for cross-cutting callers or references.
```

### 7. Fix fallback prompt — `app/nominal_code/agent/sub_agents/runner.py`

Replace the weak fallback at line 213 (`prompt="Explore all changed files."`) with a new helper:

```python
def build_fallback_prompt(changed_files: list[str], diffs: dict[str, str]) -> str:
```

Generates a prompt listing each file with its `summarize_diff()` output and generic exploration instructions (search for callers, check tests, read type definitions). Reuses `summarize_diff` from step 1.

### 8. Update tests

- **`app/tests/review/test_diff.py`** — Add `test_extract_hunk_symbols_*` and `test_summarize_diff_*` covering: Python hunks (`def`, `class`, `async def`), empty context hunks, multi-hunk diffs, diffs with no changes.
- **`app/tests/agent/sub_agents/test_planner.py`** — Update `TestBuildPlannerUserMessage` to assert hunk symbols appear. Add `test_compute_group_bounds_*` for boundary values (8, 12, 13, 25, 26, 50, 51). Update `TestPlanExplorationGroups` for sentinel replacement.
- **`app/tests/agent/sub_agents/test_runner.py`** — Update `run_explore()` calls to pass `diffs={}`. Add tests for `build_explore_user_message()` (verifies three sections present, group diffs included, other groups summarized). Add test for `build_fallback_prompt()`.

## Critical files

- `app/nominal_code/review/diff.py` — new `extract_hunk_symbols()`, `summarize_diff()`
- `app/nominal_code/agent/sub_agents/planner.py` — enriched `build_planner_user_message()`, new `compute_group_bounds()`
- `app/nominal_code/agent/sub_agents/runner.py` — new `build_explore_user_message()`, `build_fallback_prompt()`, diffs threading
- `app/nominal_code/prompts/sub_agents/planner.md` — full rewrite
- `app/nominal_code/prompts/sub_agents/explore.md` — update "What you already have" section

## Verification

1. `cd ~/Projects/nominal-code/app && uv run ruff check . && uv run ruff format --check .`
2. `cd ~/Projects/nominal-code/app && uv run mypy nominal_code/`
3. `cd ~/Projects/nominal-code/app && uv run pytest tests/review/test_diff.py tests/agent/sub_agents/test_planner.py tests/agent/sub_agents/test_runner.py -v`
4. Manually inspect `build_explore_user_message()` output with a sample multi-group PR to verify the three-section structure reads well.
