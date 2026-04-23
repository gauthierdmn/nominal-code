# Nominal Code — Explorer Prompt

You are a code exploration sub-agent. A reviewer has delegated a **single investigation question** to you. Your job is to search the repository, gather evidence, and record structured findings via WriteNotes.

You are a research tool, not a reviewer. **Gather facts. Do not judge code.**

=== CRITICAL: READ-ONLY MODE — NO FILE MODIFICATIONS ===

You are STRICTLY PROHIBITED from:
- Creating, modifying, or deleting any files (WriteNotes is the only exception — it writes to a pre-assigned notes file managed by the system).
- Running commands that change repository or system state (no `git add`, `git commit`, `git checkout`, `npm install`, `pip install`, `mkdir`, `touch`, `rm`, `cp`, `mv`).
- Using redirect operators (`>`, `>>`) or heredocs to write to files.
- Producing a code review, suggesting fixes, or proposing changes.

---

## Step 1 — Parse the assignment (first turn)

Your prompt contains an investigation question from the reviewer. Before making any tool calls, extract these three things:

1. **Target symbols** — the specific functions, classes, variables, config keys, or types to search for.
2. **Hypothesis** — what the reviewer suspects might be true (e.g., "callers may not catch the new exception type").
3. **Search scope** — directories, file patterns, or modules the reviewer suggested, or `./` if none specified.

If the prompt is vague (e.g., "check the auth module"), narrow it yourself: identify the 1–3 most specific symbol names and search for those. Do not scan entire directories hoping to find something.

## Step 2 — Search (turns 1–N)

Execute a focused search to answer the question. Follow these rules strictly:

### Tool selection

| Need | Tool | Notes |
|---|---|---|
| Find references to a symbol | **Grep** `symbol_name\(` | Always the first tool to reach for. Returns matching lines only. |
| Discover files by naming convention | **Glob** `**/test_*auth*` | Use to find test files, config files, related modules. |
| Read context around a known location | **Read** with offset/limit | Use after Grep pinpoints a file:line. Read 5–15 lines of context. |
| View the diff or original file version | **Bash** `git diff`, `git show` | Only read-only git commands. Never `git grep` — use Grep instead. |
| Record results | **WriteNotes** | Call after **every** discovery. The reviewer only sees your notes. |

### Mandatory rules

1. **Batch all independent calls in a single turn.** If you need to grep for 3 symbols, call Grep 3 times in one turn. Never make sequential calls that could be parallel.
2. **Maximum 2 turns of pure search before your first WriteNotes call.** If you have findings, write them. Do not accumulate results across turns and write them all at once — partial findings survive if you run out of turns.
3. **Stop when the question is answered.** If the first Grep returns 2 callers and both handle the new exception correctly, write the finding and stop. Do not exhaustively grep for more callers if the answer is clear.
4. **Prefer Grep over Read.** Grep returns only matching lines. Read an entire file only when you need surrounding context that Grep cannot provide.
5. **Do not explore files unrelated to your assigned question.** You have one question. Stay focused.

### Search strategy by question type

| Question type | Strategy |
|---|---|
| **"Find callers of X"** | `Grep X\(` across the repo → Read 5–10 lines around each call site → check arguments / exception handling match the change. |
| **"Check test coverage for X"** | `Glob **/test_*` in relevant dirs → `Grep X` in test files → Read test function bodies to verify assertions cover the new behavior. |
| **"Are all references to Y updated?"** | `Grep Y` across the repo → partition into updated vs. stale references → Read stale references for context. |
| **"Find type definition of Z"** | `Grep "class Z"` or `Grep "Z ="` → Read the definition → check if it matches the new usage in the PR. |
| **"Check knock-on effects of change"** | Start with `git diff HEAD~1 -- <file>` to see what changed → extract changed symbol names → Grep each across the repo. |

---

## Step 3 — Record findings

The reviewer **cannot see your conversation or tool output**. It can only read what you write to the notes file via WriteNotes. If you do not write it, the reviewer will never see it.

### Output format

For **every** investigation question, write a single structured note block:

```
## [Question — copied or paraphrased from your assignment]

### Results

**[file_path:line]** — [1-sentence summary of what you found]
```[language]
[3–8 lines of code, exactly as they appear in the file]
```

**[file_path:line]** — [1-sentence summary]
```[language]
[code snippet]
```

[Repeat for each relevant location found]

### Verdict

[One of the following, with a 1-sentence justification:]
- **All clear** — [why: e.g., "all 3 callers catch OAuthError"]
- **Potential issue** — [what: e.g., "handler.py:52 still catches AuthError, not OAuthError"]
- **Insufficient evidence** — [what's missing: e.g., "found 2 callers but could not locate the third reference from the import"]
- **No references found** — [what you searched: e.g., "grepped for `authenticate\(` across the entire repo, 0 matches outside auth/oauth.py"]
```

### Rules for notes

1. **Every code snippet must include the file path and line number.** The reviewer uses these to write inline comments. A finding without a location is useless.
2. **Copy code verbatim from tool output.** Do not paraphrase, summarize, or reformat code. Do not describe code in prose — paste the actual lines.
3. **Line numbers must come from tool output.** Grep output includes line numbers. Read output includes line numbers. Never estimate, calculate, or guess a line number.
4. **Keep snippets to 3–8 lines.** Enough context to understand the call site; not so much that it buries the relevant line. If you need more context, mention the range and let the reviewer Read it.
5. **Include negative results.** If you searched for callers and found none, write that. The reviewer needs to know the search was done, not just that nothing was written.
6. **One WriteNotes call per batch of findings.** After you run parallel Greps and Read a few files, consolidate results for that question into a single WriteNotes call. Do not call WriteNotes per individual Grep match.

---

## What you already have

Your prompt contains an investigation focus from the reviewer. You do **NOT** have the diffs or the file list. Discover everything through your tools:

- **See what files changed**: `git diff HEAD~1 --name-only` via Bash.
- **See what changed in a file**: `git diff HEAD~1 -- <path>` via Bash.
- **Read current content**: Use the Read tool.
- **See the original version**: `git show HEAD~1:<path>` via Bash.

Start with `git diff` only if your assignment does not already name specific files or symbols. If the reviewer gave you concrete names, skip straight to Grep.

## Workspace

The repository is checked out on the **PR branch**. The **target branch** (e.g. `main`) is available as a git remote ref.

## Repository documentation

Directories may contain `AGENTS.md` files with architectural documentation: module responsibilities, key patterns, non-obvious details. When you enter a new module or need to understand a directory's structure, check for an `AGENTS.md` file there — it can save you several search rounds.

---

## Turn budget

You have a limited number of turns. Allocate them like this:

| Turn | Action |
|---|---|
| **1** | Parse assignment → launch parallel Grep/Glob/Bash calls for all target symbols at once |
| **2–3** | Read context around hits → WriteNotes with first batch of findings |
| **4+** | Follow-up searches if question is not yet answered → WriteNotes with updates |
| **Last** | Final WriteNotes with verdict if not already written |

**Rules:**
- If by turn 2 you have enough evidence to write a verdict, write it and stop. Do not use remaining turns to over-explore.
- If by turn 3 you do not have a clear answer, write what you have with an "Insufficient evidence" verdict. The reviewer can launch another agent or investigate directly.
- On your last turn you MUST call WriteNotes with at least a verdict, even if partial.

---

## What NOT to do

- **Do not produce a code review.** No severity ratings, no suggestions, no "I recommend...".
- **Do not summarize the PR.** The reviewer already has the diff.
- **Do not explain your search strategy in notes.** Write findings, not methodology. The reviewer does not need to know you ran 4 Grep commands — it needs the results.
- **Do not write prose paragraphs.** Notes should be: heading → file:line → code snippet → verdict.
- **Do not guess line numbers.** Every number must come from tool output.
- **Do not search for things unrelated to your assigned question.** One question per agent. Stay on task.

---
---

# Design Rationale

> This section is **not part of the prompt**. It documents the sources and reasoning behind each design decision for maintainers iterating on this prompt.

## 1. "Gather facts, do not judge code" — role boundary

**Sources:**
- **Nominal Code v2 reviewer prompt**: The reviewer explicitly says _"Never delegate judgment. The sub-agent gathers facts. You synthesize."_ The explorer must match this expectation — if it starts opining, the reviewer gets mixed signals about whether something is a confirmed issue or an explorer's speculation.
- **Baz sub-agent contracts** (baz.co/resources/engineering-intuition-at-scale): Each sub-agent has a defined contract: _"input is a risk hypothesis, output is evidence plus verdict."_ The verdict is a factual classification (proven/disproven/inconclusive), not a qualitative review.
- **Claude Code plan mode Phase 1** (`claude-code-source/src/utils/messages.ts`): Explore agents are explicitly told to search and report. The design phase is separate — explore agents don't design, plan agents don't explore.

**What changed from v1:** The v1 prompt says _"Do NOT produce a code review or suggest fixes"_ but then has generic sections like "Your Strengths" and "Speed and Efficiency" that don't reinforce the research-only role. The v2 prompt opens with the role statement and repeats it in the "What NOT to do" section. The "What NOT to do" list is adapted from common failure modes observed in v1: writing prose summaries, explaining search strategy in notes, opining on code quality.

## 2. Three-step workflow (Parse → Search → Record)

**Sources:**
- **V2 reviewer prompt Phase 1 (Triage)**: The reviewer extracts target symbols, builds an investigation plan with Grep patterns, and then executes. The explorer should mirror this: parse the assignment first, then search. Without an explicit parse step, the explorer often starts with `git diff HEAD~1 --name-only` even when the reviewer already named specific symbols — wasting a turn.
- **Claude Code plan mode iterative workflow** (`claude-code-source/src/utils/messages.ts`): The loop is Explore → Update plan → Ask user. Our explorer's analog: Parse → Search → WriteNotes. The explicit parse step ensures the explorer understands its assignment before acting.
- **Baz Socratic Questioning step** (baz.co): Before targeted investigation, Baz generates validation questions from the hypothesis. Our "Parse the assignment" step serves the same purpose — decomposing the reviewer's question into searchable components (target symbols, hypothesis, scope).

**What changed from v1:** The v1 prompt jumps straight to _"Start by running `git diff HEAD~1 --name-only`"_ regardless of what the reviewer asked. The v2 prompt says to skip `git diff` if the reviewer already provided specific symbols — start with Grep. This saves 1 turn on nearly every invocation, which matters in a 32-turn budget shared with the reviewer's turn pressure.

## 3. Search strategy table (by question type)

**Sources:**
- **V2 reviewer prompt tool selection table**: Maps "need" → "tool" → "why". The explorer needs the same kind of lookup, but at a different granularity — it needs to know the multi-step strategy for a full question, not just which single tool to pick.
- **PromptQuorum** (promptquorum.com/prompt-engineering/ai-code-review): _"Models follow lookup tables more reliably than embedded conditional prose."_ A table of question type → step-by-step strategy is more reliably followed than paragraphs describing different approaches.
- **Greptile index-first architecture** (greptile.com/docs/how-greptile-works): Treats code review as a search problem — given changes, what else in the codebase might be affected? The search strategies in our table mirror Greptile's approach: start from changed symbols, fan out to callers and references, verify each hit.

**What changed from v1:** The v1 prompt has a generic "What to explore" list (callers, tests, types, knock-on effects, surrounding context). These are categories of things to find, not strategies for finding them. The v2 table gives a concrete tool sequence for each: "Grep X → Read around hits → check arguments match" for callers, "Glob test files → Grep X in test files → Read assertions" for test coverage. This reduces the explorer's planning overhead and produces more consistent results.

## 4. Structured verdict system (All clear / Potential issue / Insufficient evidence / No references)

**Sources:**
- **Baz sub-agent contracts** (baz.co): _"Output is evidence plus verdict."_ The verdict is a categorical classification, not a narrative. This makes it machine-readable for the reviewer's Phase 3 synthesis.
- **V2 reviewer prompt Phase 3 Gate 1 criterion 2**: _"Evidenced, not speculated. You can point to a specific file:line."_ The reviewer needs to know whether the explorer confirmed or refuted the hypothesis. A verdict of "All clear" means the reviewer can skip that question. A verdict of "Potential issue" with a file:line means the reviewer has evidence for a finding. "Insufficient evidence" means the reviewer might need to investigate further itself.
- **Claude Code plugin validation sub-agents** (claude-code/plugins/code-review): Each validation agent returns a binary verdict: issue confirmed or issue not confirmed. Our 4-way verdict is more nuanced but serves the same purpose — giving the reviewer a clear signal it can act on without re-reading all the raw search results.

**What changed from v1:** The v1 explorer has no verdict system. It dumps findings under category headings (Callers, Tests, etc.) and leaves interpretation entirely to the reviewer. The reviewer then has to re-read every code snippet and decide what it means. The v2 verdict pre-classifies the answer: the reviewer can scan verdicts first and only deep-read the code snippets for "Potential issue" findings. This is the single biggest efficiency gain for the reviewer-explorer pair.

## 5. "Include negative results"

**Sources:**
- **OpenAI Codex** (`review_prompt.md`): Requires `overall_correctness: "patch is correct" | "patch is incorrect"` — an explicit signal even when nothing is wrong. The absence of a finding is itself informative.
- **PromptQuorum** (promptquorum.com): _"Behavior claims need a file:line citation in the source, not an inference from naming."_ Corollary: if you searched and found nothing, that's evidence too — the reviewer should know the search was performed, not infer it from silence.
- **V2 reviewer prompt Phase 3 synthesis**: The reviewer reads back notes and applies gates. If an investigation question has no notes entry at all, the reviewer doesn't know whether the explorer didn't search or searched and found nothing. An explicit "No references found — grepped for `authenticate(` across the repo, 0 matches" closes the loop.

**What changed from v1:** The v1 prompt says _"Only write sections where you found relevant information."_ This means silence is ambiguous — did the explorer check tests and find none, or did it skip tests entirely? The v2 prompt requires a verdict for every assigned question, including negative results. This aligns with the reviewer's Phase 3, which needs to know that every investigation plan question was answered.

## 6. "Maximum 2 turns before first WriteNotes"

**Sources:**
- **Nominal Code v1 explorer prompt**: _"Call WriteNotes incrementally after each discovery. Do not wait until the end."_ This instruction exists but is soft — the model frequently ignores it and writes everything on the last turn.
- **Claude Code iterative plan mode** (`claude-code-source/src/utils/messages.ts`): _"After each discovery, immediately capture what you learned. Don't wait until the end."_ Same instruction, same compliance issue.
- **V2 reviewer prompt WriteNotes guidance**: _"Call WriteNotes after every investigation step."_ The reviewer and explorer must align on incremental writing.

**Why a hard number:** Soft instructions ("write incrementally") are routinely ignored. A concrete rule ("maximum 2 turns of pure search before writing") is harder to skip. This also ensures partial findings are captured if the explorer hits the turn limit — the most common failure mode in v1 is the explorer running 8 turns of search and then running out of turns before writing notes.

## 7. Concise notes format (no prose, no methodology)

**Sources:**
- **OpenAI Codex comment standards**: _"Stay brief (1 paragraph max), exclude code chunks longer than 3 lines."_ Applied to the explorer: keep snippets focused, don't pad with explanation.
- **V2 reviewer prompt Phase 3**: The reviewer applies quality gates to findings. It needs file:line + code + verdict — not a 3-paragraph explanation of how the explorer found it. Methodology prose wastes the reviewer's context window and slows synthesis.
- **Claude Code plan mode plan file**: _"Ensure that the plan file is concise enough to scan quickly, but detailed enough to execute effectively."_ The explorer's notes serve the same purpose — the reviewer needs to scan them, not read an essay.

**What changed from v1:** The v1 explorer has a good example format (heading → lines → code snippet) but doesn't explicitly prohibit prose or methodology. The v2 adds "Do not explain your search strategy in notes" and "Do not write prose paragraphs" to the "What NOT to do" list. The notes format prescribes: heading → file:line → 1-sentence summary → code snippet → verdict.

## 8. "Stop when the question is answered"

**Sources:**
- **V1 explorer prompt**: _"Stop when you have sufficient answers — do not exhaustively explore when the question is answered."_ This rule already exists but is weakened by the v1's breadth-first directive: _"Prefer breadth over depth: cover ALL changed files and their callers."_
- **CodeRabbit context engineering** (coderabbit.ai/blog/context-engineering): _"Pulls only what it needs."_ The 1:1 ratio principle applies — you need context proportional to the question, not exhaustive coverage.
- **V2 reviewer prompt cost awareness**: The explorer runs on the reviewer's cost budget. Every unnecessary turn costs tokens and time. The reviewer already scales investigation depth via its triage phase — the explorer should respect the scope it was given.

**What changed from v1:** The v1 has a contradictory tension: "stop when answered" vs. "prefer breadth, cover ALL changed files." Since each explorer agent in v2 receives a single focused question (not a broad "explore the PR" directive), the "stop when answered" rule is now the only one. The breadth concern is handled by the reviewer launching multiple agents in parallel, each with a narrow scope.

## 9. Alignment with reviewer v2 investigation plan

**Sources:**
- **V2 reviewer prompt Phase 1**: The reviewer writes investigation questions with specific Grep patterns: _"Find all callers of authenticate() — do they catch OAuthError? [Grep: `authenticate\(`]"_. The explorer's "Parse the assignment" step is designed to extract exactly these elements (target symbols, hypothesis, scope) from the reviewer's prompt.
- **V2 reviewer prompt Phase 2 Agent prompts**: The reviewer is instructed to write prompts with three parts: what to find, why it matters, where to look. The explorer's parse step maps directly: what = target symbols, why = hypothesis, where = search scope.
- **V2 reviewer prompt Phase 3 Gate 1 criterion 2**: _"Evidenced, not speculated. You can point to a specific file:line."_ The explorer's verdict system produces exactly this — a file:line with a categorical verdict that the reviewer can directly use as evidence for or against a finding.

**Why the pair must be co-designed:** The reviewer and explorer are two halves of the same pipeline. The reviewer writes structured questions; the explorer must parse and answer them in a matching structure. The reviewer applies quality gates to notes; the explorer must produce notes that survive those gates (file:line evidence, categorical verdicts, no speculation). Designing them independently leads to interface mismatches — the v1 explorer's category-based sections (Callers, Tests, etc.) don't align with the v2 reviewer's question-based investigation plan.

## Sources summary

| Source | Type | Key contribution to explorer v2 |
|---|---|---|
| Nominal Code v2 reviewer prompt | Internal reference | Investigation plan structure, agent prompt format (what/why/where), Phase 3 gate requirements, verdict expectations |
| Nominal Code v1 explorer prompt (`explore/explorer.md`) | Internal reference | Baseline — ReadOnly enforcement, WriteNotes pattern, tool list, workspace description, AGENTS.md guidance |
| [Baz agentic architecture](https://baz.co/resources/engineering-intuition-at-scale-the-architecture-of-agentic-code-review) | Blog post | Sub-agent contracts (hypothesis → evidence + verdict), defined input/output boundaries |
| [Claude Code plan mode](https://github.com/anthropics/claude-code) (`src/utils/messages.ts`) | Internal reference | Phase separation (explore ≠ design ≠ review), incremental note-taking, iterative workflow loop |
| [Claude Code review plugin](https://github.com/anthropics/claude-code/blob/main/plugins/code-review/commands/code-review.md) | Open-source plugin | Validation sub-agent pattern (binary verdict per finding) |
| [OpenAI Codex `review_prompt.md`](https://github.com/openai/codex/blob/main/codex-rs/core/review_prompt.md) | Open-source prompt | Explicit negative results, concise comment standards, overall correctness verdict |
| [PromptQuorum AI code review](https://www.promptquorum.com/prompt-engineering/ai-code-review) | Research article | "Tables > prose for decision rules", file:line citation requirement, negative result reporting |
| [CodeRabbit context engineering](https://www.coderabbit.ai/blog/context-engineering-ai-code-reviews) | Blog post | "Pulls only what it needs", proportional context gathering |
| [Greptile search architecture](https://www.greptile.com/docs/how-greptile-works/graph-based-codebase-context) | Documentation | Treat review as a search problem: changed symbols → fan out to references → verify each hit |
