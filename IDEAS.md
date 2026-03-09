# Feature Ideas

## Review Quality

- **Incremental reviews** — on re-review, only look at commits added since the last review instead of the full diff, so the bot focuses on what actually changed
- **Severity levels** — tag findings as error/warning/nit so reviewers can prioritize; optionally auto-approve PRs with only nits
- **Configurable review focus** — per-repo config (e.g. a `.nominal-code.yml`) to ignore certain paths, set language-specific rules, or adjust strictness

## Automation & Triggers

- **Auto-review on PR open** — trigger a review automatically when a PR is opened or new commits are pushed, without needing an `@mention`
- **Scheduled re-review** — re-review stale open PRs periodically (e.g. after dependency updates or base branch changes)
- **Slash commands** — support commands like `@bot /focus security` or `@bot /approve` for more structured interactions beyond free-text

## Developer Experience

- **Review summary as PR status check** — post a GitHub check run or GitLab pipeline status (pass/fail/neutral) so review results show up in the merge-readiness UI
- **Diff-aware comment threading** — reply inline on the exact diff line instead of posting a separate top-level comment, and update/resolve its own threads on re-review
- **Rate limiting / cooldown** — prevent the bot from being triggered repeatedly on the same PR within a short window (debounce rapid mentions)

## Operations

- **Metrics & observability** — expose a `/metrics` endpoint (Prometheus-style) with counters for reviews performed, agent duration, error rates, token usage
- **Webhook event queue** — persist incoming webhooks to a durable queue (Redis, SQLite) so events aren't lost if the agent is busy or the process restarts
- **Multi-model support** — let repos or users pick different models per review (e.g. a fast model for nit-level checks, a stronger model for security-focused reviews)

## Security & Access

- **Per-repo allowed users** — scope permissions so user X can trigger the bot on repo A but not repo B
- **Token rotation / GitHub App auth** — support GitHub App installation tokens (auto-rotating) instead of long-lived PATs

## Installation

- **Dumb easy installation** — the easier the setup for the user, the more adoption we can get

---

## Per-Repository Memory for Review Agents

### Problem

Every review starts from zero. The agent has no knowledge of the repository's architecture, team conventions, recurring patterns, or its own past mistakes. This leads to:

- **Repeated false positives** — the agent flags the same non-issue across multiple PRs, wasting reviewer attention after it's been dismissed once.
- **Missed context** — the agent doesn't know that "this repo uses the repository pattern" or "services never import from each other directly", so it can't enforce architectural rules that aren't written down.
- **Noise over time** — without calibration, the agent's signal-to-noise ratio stays flat. Teams that use it heavily don't get a better experience than teams that just started.

### Concept

Introduce a per-repository memory file that the agent reads as part of its system prompt and writes to after each review cycle. The memory captures patterns learned from reviewing the repository: architecture decisions, team preferences, common false positives, and conventions that don't appear in the codebase's formal style guide.

Memory lives in `app/prompts/memory/` on the server, keyed by a stable repository identifier (e.g. a hash of the platform + full repo name). It is **not** committed to the reviewed repository — it belongs to the bot instance.

### Memory File Structure

Each repository gets a single markdown file:

```
app/prompts/memory/
└── {repo-hash}.md
```

The file is structured as a flat list of observations, grouped by category:

```markdown
# Memory: owner/repo

## Architecture
- Uses repository pattern — data access is always behind a repository class
- Services communicate through an event bus, never import each other directly
- All API endpoints return a standard envelope: {"data": ..., "error": ...}

## Team Preferences
- Team prefers explicit error handling over broad try/except blocks
- PR authors frequently dismiss suggestions about import ordering — stop flagging
- Type annotations are enforced strictly; Any is never acceptable

## False Positives
- `utils.retry()` looks like it swallows exceptions but it re-raises after logging — do not flag
- `config.get()` returns Optional but callers assert non-None because config validation runs at startup

## Patterns
- Test files use a `_make_*` factory pattern for test fixtures
- All async handlers follow: validate → acknowledge → process → respond
```

### How Memory Gets Written

After each review cycle (reviewer bot only, at least initially), the agent is given its memory file as context and asked to update it based on what it learned. This happens as a lightweight post-processing step:

1. The review completes and the result is posted to the PR.
2. The agent receives a follow-up prompt: "Based on this review of {repo}, update the memory file. Add new observations, remove outdated ones, and keep the file under {token_budget} tokens."
3. The updated memory is written back to disk.

The memory update is **not** part of the review itself — it runs after, so it doesn't consume review context window tokens or slow down the response.

### How Memory Gets Read

During prompt composition (in `_resolve_guidelines`), the memory file is loaded alongside general and language guidelines:

```
system_prompt.md
  + general guidelines (config default or .nominal/guidelines.md)
  + language guidelines (built-in or .nominal/languages/{lang}.md)
  + memory (prompts/memory/{repo-hash}.md)
```

Memory is appended last because it's the most dynamic and lowest-priority context. If the context window is tight, memory is the first thing to truncate.

### Token Budget and Summarization

Memory files must stay small. A 2000-token memory file is useful context; a 20000-token memory file is a liability that crowds out the actual diff.

Controls:

- **Hard cap**: Memory files are truncated to a configurable token budget (default ~2000 tokens / ~8KB of text) before being included in the prompt.
- **Periodic summarization**: Every N reviews (configurable), the memory file is condensed. Old observations that haven't been reinforced are dropped. Redundant entries are merged. The goal is to keep the file at roughly 60-70% of the budget, leaving room for new observations.
- **Staleness decay**: Each observation could carry an implicit "last confirmed" timestamp. Observations that haven't been relevant in the last M reviews are candidates for removal during summarization.

### Feedback Loop: Learning from Responses

The most valuable signal comes from what happens after a review is posted:

- **Comment resolved without code change** — the finding was likely a false positive or a known pattern. Add to false positives list.
- **Comment resolved with code change** — the finding was valid. Reinforce similar patterns in memory.
- **Author replies "this is intentional"** — explicit signal to stop flagging this pattern.
- **Author replies with context** — architectural knowledge the agent should remember.

This feedback loop requires the bot to observe PR activity after posting a review, not just at the moment of invocation. This could be implemented as:

- A periodic poll of recently-reviewed PRs to check comment resolution status.
- A webhook listener for comment reply events on PRs the bot has reviewed.
- A deferred job that runs N hours after a review to check outcomes.

### Scope Boundaries

What memory should contain:

- Repository architecture patterns confirmed across multiple reviews
- Team preferences surfaced through repeated author responses
- Verified false positives with explanation of why they're not issues
- Naming/structural conventions not captured in formal guidelines

What memory should NOT contain:

- Anything already in `.nominal/guidelines.md` or language guidelines (avoid duplication)
- PR-specific context (branch names, specific bugs, in-progress work)
- Speculative observations from a single review (require confirmation before persisting)
- Sensitive information (credentials, internal URLs, PII from comments)

### Repo-Level vs Instance-Level Memory

Memory files live on the bot's server, not in the reviewed repository. This means:

- Different bot instances reviewing the same repo build independent memories.
- Memory is lost if the server is wiped without backup.
- Teams cannot inspect or edit the bot's memory directly.

A future enhancement could support exporting memory to `.nominal/memory.md` in the repository, giving teams visibility and edit control. The bot would merge its server-side memory with the repo-committed version, preferring the repo version for conflicts.

### Implementation Phases

**Phase 1 — Read-only memory (manual)**
- Add memory file loading to prompt composition.
- Operators manually write memory files for repositories they know well.
- No automatic updates. Validates the concept with zero risk.

**Phase 2 — Automatic memory updates**
- After each review, run a lightweight agent pass to update the memory file.
- Implement token budget enforcement and summarization.
- Add `MEMORY_ENABLED` and `MEMORY_TOKEN_BUDGET` config options.

**Phase 3 — Feedback loop**
- Track review outcomes (resolved comments, author responses).
- Feed outcomes back into memory updates.
- Implement staleness decay and confidence scoring.

### Open Questions

- Should the worker bot also read/write memory, or only the reviewer?
- How to handle memory for monorepos where different directories have different conventions?
- Should memory be shared across bot instances (e.g. stored in a shared volume or database)?
- What's the right cadence for summarization — every N reviews, or time-based?

## Use feedback from user

Can have a thumb up / thumb down mechanism to create a dataset of positive and negative reviews, and use it to improve the product (TBD how: fine tune model, improve prompts etc.)

## Use human comments as models

Comments from a selected set of reviewer can be analyzed by the model to show good review practices, and improve the qulity of the code review.
