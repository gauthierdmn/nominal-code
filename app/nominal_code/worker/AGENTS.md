# worker/

Worker bot handler — runs a Claude agent to apply code fixes and push changes.

## Key concepts

- **Unrestricted agent** — unlike the reviewer, the worker has no tool restrictions. It can read, write, run commands, and push commits.
- **Plain text output** — the worker's agent output is posted as-is (no JSON parsing or structured validation).
- **Context-aware prompts** — the prompt includes the file path, diff hunk, branch name, and user request for precise context.

## File tree

```
worker/
└── handler.py     # review_and_fix(), _build_prompt()
```

## Important details

- `review_and_fix()` resolves the branch, sets up the workspace (clone/reset + deps dir), runs the agent, and posts the reply.
- `_build_prompt()` includes `diff_hunk` and `file_path` context when the comment is on a specific line in the diff.
- The reply includes `commit_sha` from `CommentReply` if the agent pushed changes, so the platform can link to the commit.
- Errors are caught by `handle_agent_errors()` from `agent/errors.py`, which posts user-facing error messages.
- The worker uses `setup_workspace()` (immediate clone) rather than `create_workspace()` (deferred), since it doesn't need parallel fetching.
