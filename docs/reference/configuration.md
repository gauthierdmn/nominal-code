# Configuration

Nominal Code is configured via environment variables, `.env` files, CLI flags, and CI inputs. This page covers how configuration works. For the full variable reference, see [Environment Variables](env-vars.md).

## How Configuration Is Loaded

| Source | Applies To | Notes |
|---|---|---|
| Environment variables / `.env` file | All modes | Primary configuration method |
| CLI flags (`--prompt`, `--model`, etc.) | CLI mode | Override env vars |
| Action/template inputs | CI mode | Mapped to `INPUT_*` env vars |
| Per-repo `.nominal/` files | All modes | Override global prompt/guidelines |

## Mode Comparison

| | CI Mode | CLI Mode | Webhook Mode |
|---|---|---|---|
| **Trigger** | PR event in CI pipeline | Manual command | PR comment via webhook |
| **Agent runner** | LLM provider API (direct) | Claude Code CLI | Claude Code CLI |
| **Requires Claude Code CLI** | No | Yes | Yes |
| **Billing** | Provider API key (per-token) | Claude Code CLI login (Pro/Max or API key) | Claude Code CLI login (Pro/Max or API key) |
| **Server** | Not needed | Not needed | aiohttp server required |
| **Auth check** | None (CI handles it) | None (you are the user) | `ALLOWED_USERS` allowlist |
| **Conversation continuity** | No (one-shot) | No (one-shot) | Yes (multi-turn per PR) |
| **Workspace** | CI runner checkout | Cloned to temp dir | Cloned to `WORKSPACE_BASE_DIR` |
| **Workspace cleanup** | N/A (CI runner) | Manual | Automatic periodic cleanup |
| **Bot username** | Not needed | Not needed | Required for `@mention` |

## Prompt File Configuration

The bot loads system prompts and coding guidelines from files at startup.

### `WORKER_SYSTEM_PROMPT`

Path to the system prompt used when the worker bot runs the agent. Defaults to `system_prompt.md` in the project root.

### `REVIEWER_SYSTEM_PROMPT`

Path to the system prompt used when the reviewer bot runs the agent. Defaults to `reviewer_prompt.md` in the project root.

### `CODING_GUIDELINES`

Path to a coding guidelines file that gets appended to both the worker and reviewer system prompts. Defaults to `coding_guidelines.md`. This file is read once at startup and included in every agent invocation.

### `LANGUAGE_GUIDELINES_DIR`

Path to a directory containing language-specific guideline files. Each file should be named `{language}.md` (e.g. `python.md`). When the PR diff contains files matching a known language, the corresponding guideline file is appended to the system prompt. Defaults to `prompts/languages`.

Languages are detected from file extensions in the PR diff. Currently supported: `.py` and `.pyi` (Python).

## Per-Repo Overrides

Repositories can override the global guidelines by placing files in a `.nominal/` directory at the repository root. Per-repo overrides take priority over the built-in defaults.

### General guidelines

A `.nominal/guidelines.md` file replaces the global `CODING_GUIDELINES` for that repository. When present, the built-in guidelines are **not** appended — the repo file is used exclusively.

### Language-specific guidelines

A `.nominal/languages/{language}.md` file (e.g. `.nominal/languages/python.md`) replaces the built-in language guideline for that language. This allows teams to specify project-specific coding conventions per language without changing the global configuration.

## Repository Filtering

When a GitHub App or webhook endpoint receives events from multiple repositories, you can restrict which repositories the bot processes using `ALLOWED_REPOS`:

```bash
ALLOWED_REPOS=owner/repo-a,owner/repo-b
```

**Rules:**

- When set, only events from the listed repositories are processed. Events from unlisted repositories are silently filtered out.
- When unset or empty, all repositories are accepted (backward compatible).
- Repository names must match exactly (e.g. `owner/repo`), and are case-sensitive.

## Auto-Trigger

The reviewer bot can be configured to automatically run on PR lifecycle events, without requiring an `@mention` in a comment. Set `REVIEWER_TRIGGERS` to a comma-separated list of event types:

```bash
REVIEWER_TRIGGERS=pr_opened,pr_push
```

| Event Type | GitHub Source | GitLab Source |
|---|---|---|
| `pr_opened` | PR opened | MR opened |
| `pr_push` | New commits pushed to PR | MR updated with new commits |
| `pr_reopened` | PR reopened | MR reopened |
| `pr_ready_for_review` | PR marked ready (was draft) | _(not available)_ |

When unset or empty, auto-triggering is disabled and the reviewer only responds to `@mentions` (backward compatible).

Auto-triggered reviews skip the `ALLOWED_USERS` check since there is no comment author. Draft PRs on GitHub and WIP merge requests on GitLab are automatically skipped.

## PR Title Tag Filtering

The webhook server can filter events based on tags in the PR/MR title. Tags are substrings enclosed in square brackets (e.g. `[nominalbot]`). This is useful when multiple test suites or bot instances share a single repository and you want each to process only its own events.

```bash
PR_TITLE_INCLUDE_TAGS=nominalbot
PR_TITLE_EXCLUDE_TAGS=skip,wip
```

**Rules:**

- **`PR_TITLE_INCLUDE_TAGS`** (allowlist) — when set, only events whose PR title contains at least one `[tag]` are processed. Events without any matching include tag are skipped.
- **`PR_TITLE_EXCLUDE_TAGS`** (blocklist) — events whose PR title contains any `[tag]` from this list are skipped.
- Exclude takes priority over include. If both match, the event is skipped.
- Both empty = no filtering (backward compatible).
- Matching is case-insensitive.

**Examples:**

| PR Title | Include Tags | Exclude Tags | Result |
|---|---|---|---|
| `feat: add login [nominalbot]` | `nominalbot` | — | Processed |
| `feat: add login` | `nominalbot` | — | Skipped |
| `feat: add login [skip]` | — | `skip` | Skipped |
| `feat: add login [nominalbot] [skip]` | `nominalbot` | `skip` | Skipped (exclude wins) |
| `feat: add login` | — | `skip` | Processed |

## Private Dependencies

Both bots can `git clone` private repositories into a shared `.deps/` directory inside the workspace. This is useful when a PR depends on internal libraries not available on PyPI — the agent can clone them to inspect source code for context.

- Dependencies are cloned with `--depth=1` to minimize download time.
- The `.deps/` directory is shared across PRs for the same repository, so a dependency only needs to be cloned once.
- The reviewer bot is restricted to read-only tools plus `git clone` — it cannot modify files in cloned dependencies.

## Workspace Cleanup

The bot clones repositories into `WORKSPACE_BASE_DIR`. Over time, workspaces for closed or merged PRs accumulate. A built-in cleaner handles this automatically:

- Runs once immediately on startup to remove stale workspaces left from a previous run.
- Then runs periodically in the background at the interval set by `CLEANUP_INTERVAL_HOURS`.

A workspace is deleted only when no configured platform reports the PR as open. If an API check fails, the workspace is kept as a safety measure. Empty parent directories are cleaned up as well.

Set `CLEANUP_INTERVAL_HOURS=0` to disable cleanup entirely.
