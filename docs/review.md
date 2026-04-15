# Review Process

The bot runs a **reviewer agent** that reads annotated diffs, investigates the codebase using tools, and produces a structured inline code review. In **API mode** (CI, or webhook/CLI with `--provider`), the reviewer is a multi-turn agent that can also spawn **explore sub-agents** on demand for deep investigation. In **CLI mode** (default for CLI and webhook), the reviewer runs via the Claude Code CLI with read-only tools.

## How to Trigger

Mention the bot by its configured username in a PR/MR comment:

```
@my-reviewer please review this
```

You can optionally include specific instructions:

```
@my-reviewer focus on error handling
```

The bot can also run automatically on PR lifecycle events — see [Auto-Trigger](reference/configuration.md#auto-trigger).

## What It Does

1. Clones the repository (or updates an existing workspace) to the PR's head branch.
2. Concurrently fetches the full PR diff and existing comments.
3. Builds the reviewer prompt with annotated diffs, existing comments, coding guidelines, and any user instructions.
4. Runs the **reviewer agent**:
    - **API mode**: a multi-turn agentic loop (up to `reviewer_max_turns`, default 8) with access to Read, Glob, Grep, Bash, WriteNotes, and the **Agent tool** for spawning explore sub-agents.
    - **CLI mode**: runs via the Claude Code CLI with read-only tools (Read, Glob, Grep, `Bash(git clone*)`).
5. **API mode only**: for deep investigation, the reviewer spawns **explore sub-agents** via the Agent tool. Each sub-agent runs with its own turn budget (up to `explorer_max_turns`, default 32) and writes findings via WriteNotes. Multiple sub-agents run concurrently. See [Sub-Agents](reference/explore.md).
6. **API mode only**: the reviewer calls `submit_review` with a structured JSON review. If the turn limit is reached without calling `submit_review`, a fallback single-turn call is made with accumulated notes.
7. Parses the agent's JSON output into a structured review.
8. Posts the review as native inline comments on the PR/MR.

## Annotated Diffs

Diffs are line-annotated before being sent to the review agent. Each line is prefixed with its actual line number in the file, removing the need for the agent to count through hunk headers:

```
@@ -10,4 +10,5 @@ def foo():
 10:    existing_line
-11:    old_code
+11:    new_code
+12:    added_line
 13:    context_line
```

This allows the review agent to reference exact line numbers and indentation without needing to read files for line-counting.

## Exploration Notes

When the reviewer spawns explore sub-agents, each sub-agent writes structured findings to a markdown notes file organized under headings:

- `## Callers` — functions in other files that call or reference the changed code
- `## Tests` — test coverage for the changed modules
- `## Type Definitions` — types, base classes, protocols referenced by the changes
- `## Knock-on Effects` — callers not updated, config references not renamed
- `## Additional Context` — surrounding function bodies, related code

Notes are returned to the reviewer as the Agent tool result, giving it full context for its review. See [Sub-Agents](reference/explore.md) and [Compaction](reference/compaction.md).

## Review Agent

The reviewer receives annotated diffs, coding guidelines, existing PR comments, and user instructions in its prompt. The available tools depend on the agent runner:

### API Mode Tools

In API mode (CI, or webhook/CLI with `--provider`), the reviewer is a **multi-turn agent** with these tools:

| Tool | Purpose |
|---|---|
| Read | Read file contents with line numbers |
| Glob | Find files by pattern |
| Grep | Search file contents |
| Bash | Shell commands (with allowlist validation) |
| WriteNotes | Record findings to a notes file |
| Agent | Spawn explore sub-agents for deep investigation |
| submit_review | Submit the final structured review |

The reviewer decides how to investigate — using tools directly for simple lookups or delegating to explore sub-agents for complex analysis.

### CLI Mode Tools

In CLI mode (default for CLI and webhook), the reviewer runs via the Claude Code CLI with restricted read-only tools:

| Tool | Purpose |
|---|---|
| Read | Read file contents |
| Glob | Find files by pattern |
| Grep | Search file contents |
| Bash(git clone*) | Clone private dependencies only |

CLI mode does not support sub-agents, WriteNotes, or the `submit_review` tool. The Claude Code CLI manages the agent's tool execution and output directly.

## JSON Output and Retry Logic

The agent calls `submit_review` with JSON matching this format:

```json
{
  "summary": "Overall review summary",
  "comments": [
    {
      "path": "src/main.py",
      "line": 42,
      "body": "This variable is unused."
    }
  ]
}
```

If the agent's output is not valid JSON, the bot retries up to 2 times with a corrective prompt. If all retries fail, the raw output is posted as a plain comment.

When findings exist, they are posted as a native code review with inline comments on the specific lines. If there are no findings, only the summary is posted as a plain comment.

## Cross-File Review

The reviewer is not limited to the lines changed in the PR. Exploration notes include callers, tests, and knock-on effects found across the repository. The review agent uses these to flag issues outside the diff.

Findings that target lines within the diff are posted as native inline comments. Findings that reference files or lines outside the diff are automatically folded into the general review body under an **Additional notes (not in diff)** section, since the GitHub/GitLab API only supports inline comments on diff lines.

## Existing Discussion Context

Before running the agent, the bot fetches all existing comments on the PR to avoid duplicating previously raised issues:

- Comments authored by the bot itself are filtered out.
- Only the 50 most recent non-bot comments are included.
- Resolved threads are tagged as such — the agent is instructed to skip them.
- On GitHub, both conversation comments and inline review comments are fetched.
- On GitLab, all discussion notes are fetched (excluding system notes).

## Running in Different Modes

The bot is available in all three modes:

- **[Webhook](modes/webhook.md)** — real-time via `@mention` with conversation continuity
- **[CLI](modes/cli.md)** — one-shot from the command line
- **[CI](modes/ci.md)** — automated in GitHub Actions or GitLab CI
