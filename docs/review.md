# Review Process

The bot runs a three-stage review pipeline: a **planner** partitions the work into investigation concerns, parallel **explorer** agents gather codebase context for each concern, then a single-turn **reviewer** agent produces a structured inline code review.

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
3. **Plan:** A planner agent reads the changed file list and the project's coding guidelines, then partitions the review into concern-based exploration groups (e.g., callers and dependencies, test coverage, type safety). This is a single LLM call with structured output via the `submit_plan` tool. When the PR has fewer than 8 changed files, the planner is skipped and a single explorer handles all concerns.
4. **Explore:** Parallel explorer agents run concurrently — one per concern. Each agent uses read-only tools (Read, Grep, Glob, Bash) to discover diffs, search for callers, check test coverage, and verify type contracts. Findings are recorded incrementally via the `WriteNotes` tool. See [Exploration Pipeline](reference/explore.md).
5. **Review:** A single-turn reviewer agent receives the annotated diffs, assembled exploration notes, coding guidelines, and existing comments. It calls the `submit_review` tool with a structured JSON review — one API call, no file-reading tools.
6. Parses the agent's JSON output into a structured review.
7. Posts the review as native inline comments on the PR/MR.

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

This allows the review agent to reference exact line numbers and indentation without needing to read files — enabling the one-turn, tool-free design.

## Exploration Notes

Explore sub-agents write structured findings to markdown notes files organized under headings:

- `## Callers` — functions in other files that call or reference the changed code
- `## Tests` — test coverage for the changed modules
- `## Type Definitions` — types, base classes, protocols referenced by the changes
- `## Knock-on Effects` — callers not updated, config references not renamed
- `## Additional Context` — surrounding function bodies, related code

The notes from all sub-agents are assembled and injected into the review agent's prompt. See [Exploration Pipeline](reference/explore.md) and [Compaction](reference/compaction.md).

## Review Agent

The review agent runs in **single-turn mode** — one API call, no file-reading tools. Its only tool is `submit_review`, which enforces the structured output format. The agent receives everything it needs in the prompt:

- Annotated diffs with exact line numbers
- Exploration notes from sub-agents
- Repository coding guidelines
- Existing PR comments (to avoid duplicates)
- User instructions (if any)

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
