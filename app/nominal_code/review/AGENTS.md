# review/

Reviewer bot handler — runs a Claude agent to produce structured code reviews with inline comments.

## Key concepts

- **Structured output** — the agent returns JSON (`{"summary": "...", "comments": [...]}`). The handler parses it, validates findings against the actual diff, and posts a native platform review.
- **Diff filtering** — findings are validated against real diff line numbers. Comments targeting lines outside the changed hunks are rejected and appended to the summary as "additional notes".
- **Retry on parse failure** — if the agent produces malformed JSON, the handler retries up to `MAX_REVIEW_RETRIES` (2) times with a retry prompt that includes the previous output.
- **Read-only clone** — the reviewer uses `build_reviewer_clone_url()` (optional read-only token) to avoid granting write access.

## File tree

```
review/
└── handler.py     # review(), review_and_post(), prompt building, JSON parsing, diff filtering
```

## Important details

- **Tool restrictions** — the reviewer agent is limited to `Read`, `Glob`, `Grep`, `Bash(git clone*)`.
- **Existing comment context** — up to 50 most recent non-bot comments are included in the prompt so the agent is aware of prior discussion.
- **Parallel fetching** — `review()` uses `asyncio.gather()` to fetch the PR diff and existing comments concurrently.
- **Hunk line parsing** — `_parse_diff_lines()` extracts valid new-side line numbers from unified diff `@@` headers, counting only non-deletion lines.
- **Diff index** — `_build_diff_index()` maps `{file_path: set[valid_lines]}` for O(1) finding validation.
- `ReviewResult` bundles the parsed review, valid/rejected findings, effective summary, and raw agent output.
- `_filter_findings()` returns a `(valid, rejected)` tuple — rejected findings are those on files or lines not in the diff.
- `_build_effective_summary()` appends rejected findings under an "Additional notes" heading.
- The prompt includes the full diff, changed file list, deps path, and (optionally) existing comments formatted as markdown.
