# Review Process

The bot fetches the PR diff, runs an AI agent with read-only tools, and posts a structured inline code review.

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
3. Builds a prompt that includes the diff, existing discussion context, and any user instructions.
4. Runs the agent in the cloned workspace with restricted tools.
5. Parses the agent's JSON output into a structured review.
6. Posts the review as native inline comments on the PR/MR.

## Restricted Tool Set

The bot runs with `bypassPermissions` mode but is limited to these tools:

| Tool | Purpose |
|---|---|
| `Read` | Read file contents |
| `Glob` | Find files by pattern |
| `Grep` | Search file contents |
| `Bash(git clone*)` | Clone private dependencies into `.deps/` |

The bot cannot modify files, run arbitrary commands, or push commits. See [Security — Tool Restrictions](security.md#tool-restrictions).

## JSON Output and Retry Logic

The agent must output strict JSON matching this format:

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

The reviewer is not limited to the lines changed in the PR. It actively checks for knock-on effects elsewhere in the repository — for example, callers that were not updated after a function signature change, or stale references to a renamed config key.

Findings that target lines within the diff are posted as native inline comments. Findings that reference files or lines outside the diff are automatically folded into the general review body under an **Additional notes (not in diff)** section, since the GitHub/GitLab API only supports inline comments on diff lines.

This means no review feedback is lost, regardless of where in the codebase the issue is found.

## Pre-Review Context

The `review()` function accepts an optional `context` parameter for injecting additional information into the reviewer's prompt. This is typically the output from codebase exploration sub-agents (see [Sub-Agents](reference/sub-agents.md)), but can be any text the caller wants the reviewer to consider.

The context is inserted verbatim into the user message before the review instruction. The caller is responsible for formatting — nominal-code does not add headers or framing.

Example usage:

```python
from nominal_code.agent.sub_agents import run_explore_with_planner

# Run exploration
explore_result = await run_explore_with_planner(
    changed_files=files, diffs=diffs, cwd=repo_path,
    provider=provider, model="gemini-2.5-flash",
)

# Format context from sub-agent results
context = "\n\n".join(
    sub.output for sub in explore_result.sub_results if not sub.is_error
)

# Pass to review
result = await review(event=event, prompt="", config=config,
                      platform=platform, context=context)
```

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
