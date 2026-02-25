# Reviewer Bot

The reviewer bot fetches the PR diff, runs an AI agent with read-only tools, and posts a structured inline code review.

## How to Trigger

Mention the reviewer bot by its configured username in a PR/MR comment:

```
@my-reviewer please review this
```

You can optionally include specific instructions:

```
@my-reviewer focus on error handling
```

## What It Does

1. Clones the repository (or updates an existing workspace) to the PR's head branch.
2. Concurrently fetches the full PR diff and existing comments.
3. Builds a prompt that includes the diff, existing discussion context, and any user instructions.
4. Runs the agent in the cloned workspace with restricted tools.
5. Parses the agent's JSON output into a structured review.
6. Posts the review as native inline comments on the PR/MR.

## Restricted Tool Set

The reviewer runs with `bypassPermissions` mode but is limited to these tools:

| Tool | Purpose |
|---|---|
| `Read` | Read file contents |
| `Glob` | Find files by pattern |
| `Grep` | Search file contents |
| `Bash(git clone*)` | Clone private dependencies into `.deps/` |

The reviewer cannot modify files, run arbitrary commands, or push commits.

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

## Existing Discussion Context

Before running the agent, the reviewer fetches all existing comments on the PR to avoid duplicating previously raised issues:

- Comments authored by the reviewer bot itself are filtered out.
- Only the 50 most recent non-bot comments are included.
- Resolved threads are tagged as such — the agent is instructed to skip them.
- On GitHub, both conversation comments and inline review comments are fetched.
- On GitLab, all discussion notes are fetched (excluding system notes).

## Read-Only Reviewer Token

By default, the reviewer clones using the main platform token (`GITHUB_TOKEN` / `GITLAB_TOKEN`). For tighter security, set `GITHUB_REVIEWER_TOKEN` or `GITLAB_REVIEWER_TOKEN` to provide a read-only token used exclusively for the reviewer's `git clone` operations.

## Session Continuity

The reviewer maintains session continuity within the same PR. Subsequent review requests resume from the previous session, preserving context. Sessions are keyed by `(platform, repo, pr_number, "reviewer")`.
