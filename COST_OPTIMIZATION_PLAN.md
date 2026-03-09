# Cost Optimization Plan for Nominal Code Reviewer Bot

## Executive Summary

Nominal Code currently uses **Claude Sonnet 4** for all code reviews via the Anthropic API, with ephemeral prompt caching as the only cost optimization. This plan proposes a phased approach to reduce API costs by **60-80%** while maintaining or improving review accuracy. The key strategies are: **multi-model routing** (match model power to task complexity), **deep caching and batching** (maximize Anthropic's discount mechanisms), **multi-vendor diversification** (leverage cheaper providers for commodity tasks), and **open-source model integration** (near-zero cost for high-volume triage).

---

## Table of Contents

1. [Current State Analysis](#1-current-state-analysis)
2. [Strategy 1: Multi-Model Routing & Cascading](#2-strategy-1-multi-model-routing--cascading)
3. [Strategy 2: Maximize Anthropic Cost Features](#3-strategy-2-maximize-anthropic-cost-features)
4. [Strategy 3: Multi-Vendor Diversification](#4-strategy-3-multi-vendor-diversification)
5. [Strategy 4: Open-Source Model Integration](#5-strategy-4-open-source-model-integration)
6. [Strategy 5: Application-Level Optimizations](#6-strategy-5-application-level-optimizations)
7. [Recommended Architecture](#7-recommended-architecture)
8. [Implementation Phases](#8-implementation-phases)
9. [Cost Projections](#9-cost-projections)
10. [Risks and Mitigations](#10-risks-and-mitigations)
11. [Sources](#11-sources)

---

## 1. Current State Analysis

### What We Have

| Aspect | Current State |
|--------|---------------|
| **Model** | `claude-sonnet-4-20250514` for all reviews |
| **Mode** | API (CI), Claude Code CLI (webhook/CLI) |
| **Caching** | Ephemeral prompt caching on system prompt only |
| **Batching** | None |
| **Multi-model** | Single model for everything |
| **Vendor** | Anthropic only |
| **Token budget** | Max 16,384 output tokens per turn |

### Where the Money Goes

1. **Input tokens (largest driver):** System prompt (~250 tok) + guidelines (~100-500 tok) + PR diff (highly variable, hundreds to tens of thousands) + existing comments context (50 comments = ~250-1000 tok)
2. **Output tokens:** Full JSON review output with summary + inline findings + code suggestions
3. **Agentic tool-use loops:** Multiple turns when the reviewer calls Read/Glob/Grep tools to inspect full files beyond the diff, each turn incurring input+output costs
4. **Redundant reviews:** Re-reviews on force pushes, CI retriggers, and rapid commit sequences

### Current Pricing (Claude Sonnet 4.6)

| | Standard | With Cache Hit | Batch |
|-|----------|---------------|-------|
| **Input** | $3.00/MTok | $0.30/MTok | $1.50/MTok |
| **Output** | $15.00/MTok | $15.00/MTok | $7.50/MTok |

---

## 2. Strategy 1: Multi-Model Routing & Cascading

### The Core Insight

Not every code review needs the same model. A style nit ("unused import") does not require the same reasoning power as detecting a subtle race condition. Research shows that **routing 85% of queries to a cheaper model** can maintain 95% of the quality of always using the expensive model ([RouteLLM, LMSYS](https://lmsys.org/blog/2024-07-01-routellm/)).

### Task Classification for Code Review

We can decompose what the reviewer bot does into distinct difficulty tiers:

#### Tier 1: Surface-Level Checks (Cheap Model)
- Unused imports/variables
- Naming convention violations
- Missing docstrings or type hints
- Formatting issues (beyond what linters catch)
- Simple copy-paste errors
- TODO/FIXME comments in production code

**Recommended model:** Haiku 4.5 ($1.00/$5.00 per MTok) or Gemini 2.5 Flash-Lite ($0.10/$0.40)

#### Tier 2: Standard Code Review (Mid-Range Model)
- Logic correctness of straightforward changes
- Error handling gaps
- API misuse (wrong method, missing parameters)
- Test coverage gaps
- Code duplication
- Performance anti-patterns (N+1 queries, unnecessary allocations)

**Recommended model:** Claude Sonnet 4.6 ($3.00/$15.00) or Gemini 2.5 Pro ($1.25/$10.00)

#### Tier 3: Deep Analysis (Premium Model)
- Security vulnerabilities (injection, auth bypass, data leaks)
- Concurrency bugs (race conditions, deadlocks)
- Architectural impact assessment
- Complex algorithm correctness
- Cross-service interaction issues

**Recommended model:** Claude Opus 4.6 ($5.00/$25.00) -- but only for the 10-15% of reviews that truly need it

### Routing Approaches

#### Option A: Diff-Based Heuristic Router (Simplest)

Route based on measurable properties of the PR:

```python
def select_model(diff_stats: DiffStats, pr_metadata: PRMetadata) -> str:
    # Security-sensitive paths always get premium review
    if any(is_security_sensitive(f.path) for f in diff_stats.files):
        return "claude-opus-4-20250514"

    # Large architectural changes get premium review
    if diff_stats.files_changed > 20 or diff_stats.lines_changed > 1000:
        return "claude-opus-4-20250514"

    # Small, single-file changes get cheap review
    if diff_stats.files_changed <= 3 and diff_stats.lines_changed < 100:
        return "claude-haiku-4-20250514"

    # Everything else gets mid-range
    return "claude-sonnet-4-20250514"
```

Security-sensitive paths could be configured in `.nominal/guidelines.md` or a new `.nominal/config.yaml`:
```yaml
security_sensitive_paths:
  - "auth/"
  - "crypto/"
  - "middleware/auth*"
  - "*.env*"
  - "migrations/"
  - "Dockerfile"
  - "docker-compose*"
```

**Estimated savings: 40-50%** (assuming ~30% of PRs are small, ~55% medium, ~15% large/security)

#### Option B: Two-Pass Cascade with Judge (Best Quality/Cost Ratio)

Inspired by [HubSpot's production architecture](https://product.hubspot.com/blog/automated-code-review-the-6-month-evolution):

```
Pass 1: Haiku reviews the entire diff (cheap, fast)
           |
           v
Pass 2: Judge Agent (Haiku) filters findings for:
           - Accuracy: Is this actually a bug/issue?
           - Actionability: Can the developer act on this?
           - Severity: Is this worth commenting on?
           |
           v
Pass 3: Only LOW-CONFIDENCE findings from Pass 1
        get re-reviewed by Sonnet/Opus
```

The Judge Agent pattern is critical. HubSpot found that **the most common failure mode wasn't incorrect feedback -- it was unhelpful feedback**. A judge pass that filters noisy suggestions dramatically improves developer experience AND saves money by preventing unnecessary escalations.

**Estimated savings: 60-75%** (Haiku handles most reviews; Sonnet/Opus only for escalated findings)

#### Option C: RouteLLM Open-Source Router (Most Sophisticated)

Use the [RouteLLM](https://github.com/lm-sys/RouteLLM) framework for ML-based routing:

- Trains on preference data to predict which model will give the best answer
- Matrix factorization router achieved **95% of GPT-4 quality routing only 14% to GPT-4**
- Can be fine-tuned on your own review feedback data (thumbs up/down on comments)
- Adds ~50ms latency per routing decision (negligible for code review)

This is the most sophisticated option but requires maintaining a routing model. Best suited for high-volume deployments (>1000 PRs/month).

### Implementation in Nominal Code

The current architecture has a clean separation point in `handlers/review.py` where the agent runner is invoked. The model is already configurable via `AGENT_MODEL` env var. Changes needed:

1. Add a `ModelRouter` class that takes diff stats + PR metadata and returns a model identifier
2. Modify `handlers/review.py` to call the router before invoking the agent
3. Extend the model identifier to support non-Anthropic providers (see Strategy 3)
4. Add a `--routing-strategy` config option (heuristic | cascade | routellm)

---

## 3. Strategy 2: Maximize Anthropic Cost Features

### 3a. Enhanced Prompt Caching

The current implementation uses ephemeral caching on the system prompt only. We can do much more.

**What to cache (in order of reuse frequency):**

1. **System prompt + global guidelines** (already cached) -- reused across ALL reviews
2. **Language-specific guidelines** (`prompts/languages/*.md`) -- reused across all reviews of that language
3. **Repository-specific guidelines** (`.nominal/guidelines.md`) -- reused across all PRs in that repo
4. **Repository structure context** -- file tree, key interfaces -- reused within a repo

**Cache tiers and pricing:**

| Cache Type | Write Cost | Read Cost (Hit) | TTL | Best For |
|------------|-----------|----------------|-----|----------|
| 5-minute ephemeral | 1.25x input | 0.1x input | 5 min | Burst reviews (multiple PRs in quick succession) |
| 1-hour extended | 2.0x input | 0.1x input | 1 hour | Sustained review sessions |

**Implementation:** Use Anthropic's `cache_control` markers strategically. Place breakpoints at each layer:

```python
messages = [
    {
        "role": "system",
        "content": [
            {"type": "text", "text": base_system_prompt, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": language_guidelines, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": repo_guidelines, "cache_control": {"type": "ephemeral"}},
        ]
    }
]
```

**Estimated savings: 40-60% on input tokens** (system prompts and guidelines often dominate input for small-medium PRs)

### 3b. Batch API for Non-Urgent Reviews

Anthropic's Batch API provides a flat **50% discount** on all tokens. Not every review needs to be real-time.

**Use cases for batch processing:**

| Trigger | Urgency | Use Batch? |
|---------|---------|------------|
| PR opened | Medium | No -- developer expects timely feedback |
| `@bot review` mention | High | No -- explicit human request |
| Push to existing PR | Low | **Yes** -- developer is still working |
| Nightly codebase scan | None | **Yes** -- scheduled job |
| Pre-merge final review | Medium | Maybe -- configurable per repo |

**Implementation:** Add a `ReviewMode` enum (`REALTIME`, `BATCH`) and route accordingly. The Batch API returns results within minutes, which is acceptable for non-interactive reviews.

**Estimated savings: 50% on batch-eligible reviews** (likely 20-40% of total volume)

### 3c. Combined: Batch + Caching

These stack. For a batch review with cached system prompt:

- Input: cached portion at 0.1x + uncached at 0.5x = **~70-80% off input**
- Output: 0.5x = **50% off output**

---

## 4. Strategy 3: Multi-Vendor Diversification

### Why Multi-Vendor?

1. **Price competition:** Google and others significantly undercut Anthropic on lower tiers
2. **Resilience:** No single-vendor outage takes down reviews
3. **Best-of-breed:** Different vendors excel at different tasks
4. **Rate limit headroom:** Spread load across providers

### Vendor Comparison for Code Review Tasks (March 2026)

| Provider | Model | Input/MTok | Output/MTok | Strengths |
|----------|-------|-----------|------------|-----------|
| **Google** | Gemini 2.5 Flash-Lite | $0.10 | $0.40 | Ultra-cheap, 1M context, good for triage |
| **Google** | Gemini 2.5 Flash | $0.30 | $2.50 | Great quality/price ratio, thinking mode |
| **Google** | Gemini 2.5 Pro | $1.25 | $10.00 | Competitive with Sonnet, cheaper |
| **DeepSeek** | V3.2 | $0.28 | $0.42 | Ridiculously cheap, strong at code |
| **Anthropic** | Haiku 4.5 | $1.00 | $5.00 | Fast, good at structured output |
| **Anthropic** | Sonnet 4.6 | $3.00 | $15.00 | Excellent code reasoning |
| **Anthropic** | Opus 4.6 | $5.00 | $25.00 | Best-in-class for complex analysis |
| **OpenAI** | GPT-5 Nano | $0.05 | $0.40 | Cheapest major-vendor option |
| **OpenAI** | GPT-4.1 | $2.00 | $8.00 | Strong at code, cheaper than Sonnet |

### Recommended Multi-Vendor Assignment

| Review Task | Primary Model | Fallback | Why |
|-------------|--------------|----------|-----|
| **Triage/classification** | Gemini 2.5 Flash-Lite | GPT-5 Nano | $0.10/MTok input is 30x cheaper than Sonnet |
| **Style & lint** | Gemini 2.5 Flash | Haiku 4.5 | Pattern matching doesn't need frontier reasoning |
| **Standard review** | Gemini 2.5 Pro or GPT-4.1 | Sonnet 4.6 | 40-60% cheaper than Sonnet, comparable quality |
| **Security review** | Sonnet 4.6 | Opus 4.6 | Security demands high accuracy, Anthropic excels |
| **Architecture review** | Opus 4.6 | GPT-5.2 Pro | Only frontier models for high-stakes decisions |
| **Judge/filter pass** | Gemini 2.5 Flash-Lite | Haiku 4.5 | Evaluating review quality is simpler than generating it |

### Implementation: Provider Abstraction Layer

The current codebase is tightly coupled to Anthropic's API in `agent/api/runner.py`. We need a provider abstraction:

```python
# New: llm/provider.py
class LLMProvider(Protocol):
    async def create_message(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 16384,
    ) -> Message: ...

# New: llm/anthropic.py
class AnthropicProvider(LLMProvider): ...

# New: llm/google.py
class GoogleProvider(LLMProvider): ...

# New: llm/openai.py
class OpenAIProvider(LLMProvider): ...

# New: llm/openai.py (DeepSeek)
class DeepSeekProvider(LLMProvider): ...
```

The router (Strategy 1) would return both a model name AND a provider, and the handler would dispatch accordingly.

### Data Sensitivity Consideration

> **Important:** DeepSeek routes through Chinese infrastructure. For repositories with sensitive IP, compliance requirements, or government contracts, route ONLY to Anthropic, Google (US/EU regions), or OpenAI. Make this configurable per repository.

---

## 5. Strategy 4: Open-Source Model Integration

### Best Open-Source Models for Code Review (2026)

| Model | Parameters | Active Params | SWE-Bench | License | Best For |
|-------|-----------|---------------|-----------|---------|----------|
| **Qwen3-Coder** | 480B MoE | 35B | 69.6% | Apache 2.0 | Full code review, near-frontier quality |
| **Qwen3-Coder-Next** | MoE | 3B | 70%+ | Apache 2.0 | Incredibly efficient, rivals frontier models |
| **DeepSeek V3.2** | 671B MoE | ~37B | N/A | Open | Strong reasoning, very cheap hosted |
| **LLaMA 4 Maverick** | 400B MoE | ~17B | N/A | Meta License | Bug detection, runtime errors |
| **Qwen 2.5 Coder** | 7B-32B | All | N/A | Apache 2.0 | Fine-tuning base for specialized tasks |

### Hosted Open-Source (Zero Infrastructure)

Several providers host open-source models at near-cost pricing:

| Provider | Model | Input/MTok | Output/MTok |
|----------|-------|-----------|------------|
| **Fireworks AI** | Qwen3-Coder | ~$0.22 | ~$1.00 |
| **Together AI** | Qwen3-Coder | ~$0.20 | ~$0.90 |
| **Groq** | LLaMA 4 | Free tier available | Very fast inference |
| **DeepSeek API** | V3.2 | $0.28 | $0.42 |
| **NVIDIA NIM** | Various | Pay-per-use | Optimized inference |

These are **10-50x cheaper** than Claude Sonnet 4.6 for input tokens.

### Self-Hosting Economics

Self-hosting makes sense at scale. NVIDIA's research demonstrated that a **fine-tuned 8B parameter model outperformed models 40x its size** on code review severity classification, suggesting that a small, specialized model can be very effective.

**When to self-host:**

| Monthly Volume | Recommendation |
|----------------|----------------|
| < 500 PRs | Use hosted APIs only |
| 500-5,000 PRs | Hosted open-source (Fireworks/Together) + Anthropic for premium |
| 5,000+ PRs | Consider self-hosting Qwen3-Coder-Next on a single A100/H100 |

**Self-hosting stack:**
- **vLLM** or **TensorRT-LLM** for inference server
- **Qwen3-Coder-Next** (3B active parameters) runs on a single GPU
- Single A100 80GB: ~$2/hour on cloud = ~$1,440/month
- At 5,000 PRs/month, that's ~$0.29/review vs $1-5/review on APIs

### Fine-Tuning for Specialized Tasks

NVIDIA's research on fine-tuning small models for code review ([source](https://developer.nvidia.com/blog/fine-tuning-small-language-models-to-optimize-code-review-accuracy/)) showed:

1. Generate training data: Use Claude Opus to label 5,000-10,000 code review examples
2. Fine-tune Qwen 2.5 Coder 7B with LoRA on that data
3. Result: **18% better accuracy** than base model, **competitive with models 40x larger**
4. Inference cost: ~$0.01-0.05 per review (self-hosted) or ~$0.05-0.10 (hosted)

This creates a **data flywheel**: as developers give thumbs-up/down on review comments, that feedback refines the training set, making the fine-tuned model better over time.

**Recommended fine-tuning pipeline for Nominal Code:**

```
Phase 1: Collect review data
  - Log all Sonnet/Opus reviews + developer reactions (resolved, dismissed, thumbs up/down)
  - Target: 5,000+ labeled examples

Phase 2: Generate training labels
  - Use Opus to score each review comment on accuracy, actionability, helpfulness
  - Format as (diff, review_comment, quality_score) triples

Phase 3: Fine-tune
  - Base model: Qwen 2.5 Coder 7B or 14B
  - Method: LoRA (4-bit quantization for efficiency)
  - Objective: Given a diff, produce review comments with quality score > threshold

Phase 4: Deploy
  - Hosted on Fireworks/Together initially
  - Self-hosted when volume justifies
  - Use as Tier 1 model in the routing cascade
```

---

## 6. Strategy 5: Application-Level Optimizations

### 6a. Structured Output & Token Efficiency

The current reviewer returns verbose JSON with summaries and detailed suggestions. We can reduce output tokens by:

1. **Tighter JSON schema:** Remove optional verbose fields for Tier 1 reviews
2. **Severity filtering in prompt:** "Only report issues of severity HIGH or CRITICAL" for initial pass
3. **Constrained decoding:** Use `response_format` / JSON mode to prevent preamble text
4. **Diff-only context for simple reviews:** For Tier 1 (style/lint), don't include full file context -- just the diff

**Estimated savings: 20-30% on output tokens**

### 6b. Incremental Reviews

Currently noted in `IDEAS.md` as planned. When a developer pushes new commits to an existing PR:

- **Current:** Re-review the entire diff from scratch
- **Proposed:** Only review the incremental diff since the last review

Implementation:
1. Store the last-reviewed commit SHA per PR (already have session tracking)
2. On new push, fetch diff between last-reviewed SHA and new HEAD
3. Only send the incremental diff to the model
4. Merge new findings with existing review (avoid duplicates)

**Estimated savings: 30-50% on rapid-iteration PRs** (which are the most expensive due to repeated reviews)

### 6c. Application-Level Response Caching

Cache full review responses keyed by content hash:

```python
cache_key = hash(
    system_prompt_version,
    model,
    diff_content_hash,     # Hash of the actual diff
    guidelines_version,     # Hash of guidelines
)
```

If the same diff is reviewed again (CI retrigger, re-run), return the cached response instantly at zero cost.

**Estimated savings: Near-100% on duplicate reviews** (estimated 5-15% of volume from CI retriggers)

### 6d. Smart Context Window Management

Reduce input tokens by being smarter about what context to include:

1. **Comment deduplication:** Don't send resolved comment threads to the model
2. **Diff chunking for large PRs:** Split large diffs into file-group chunks, review independently, merge results
3. **Selective file inclusion:** For PRs touching 50+ files, use a cheap model to classify which files need review vs. which are auto-generated/trivial
4. **Compressed file references:** Instead of full file paths repeated in every finding, use short IDs

### 6e. Feedback Loop for Continuous Improvement

Track these metrics to continuously optimize:

| Metric | Purpose |
|--------|---------|
| **Cost per review** | Track by model, routing tier, PR size |
| **Finding acceptance rate** | % of comments not dismissed -- measures quality |
| **Escalation rate** | % routed from cheap to expensive model -- tune threshold |
| **False positive rate** | Comments that get "resolved without action" -- reduce noise |
| **Time to review** | Latency by model/provider -- ensure SLA |

Add to `config.py` and export as Prometheus metrics or structured logs.

---

## 7. Recommended Architecture

### Full Architecture Diagram

```
                            PR Event (opened / pushed / @mention)
                                          |
                                   [Event Handler]
                                          |
                              +-----------+-----------+
                              |                       |
                         Is duplicate?           Is incremental?
                         (content hash)          (commits since last review)
                              |                       |
                         Yes: Return              Yes: Fetch only
                         cached response          incremental diff
                              |                       |
                              +----------+------------+
                                         |
                                    [Diff Analyzer]
                                    Compute: size, files changed,
                                    file paths, language mix
                                         |
                                  [Model Router]
                              /        |          \
                         Tier 1      Tier 2       Tier 3
                        (Simple)   (Standard)   (Complex/Security)
                           |          |              |
                     Gemini Flash  Gemini Pro     Claude Sonnet 4.6
                     or Haiku 4.5  or GPT-4.1     w/ prompt caching
                     ($0.10-1.00)  ($1.25-2.00)   ($3.00/MTok)
                           |          |              |
                           +-----+----+----+---------+
                                 |         |
                           [Judge Agent]   |
                           (Gemini Flash-Lite)
                           Filters noisy    |
                           suggestions      |
                                 |         |
                           Low-confidence findings
                           escalated to Sonnet/Opus
                                 |
                           [Merge & Deduplicate]
                                 |
                           [Post Review Comments]
                                 |
                           [Log Metrics: cost, latency,
                            model used, finding count]
                                 |
                           [Cache Response by content hash]
```

### Configuration Example

```yaml
# .nominal/config.yaml (new per-repo config)

routing:
  strategy: "heuristic"  # heuristic | cascade | routellm
  security_sensitive_paths:
    - "auth/"
    - "crypto/"
    - "*.env*"
    - "Dockerfile"

  tiers:
    simple:
      max_files: 3
      max_lines: 100
      provider: "google"
      model: "gemini-2.5-flash-lite"
    standard:
      provider: "google"
      model: "gemini-2.5-pro"
    complex:
      provider: "anthropic"
      model: "claude-sonnet-4-20250514"
    security:
      provider: "anthropic"
      model: "claude-opus-4-20250514"

  judge:
    enabled: true
    provider: "google"
    model: "gemini-2.5-flash-lite"

caching:
  prompt_cache: true
  response_cache: true
  response_cache_ttl: 86400  # 24 hours

batching:
  enabled: true
  triggers:
    - "push_to_existing_pr"
    - "scheduled_scan"

data_sensitivity: "standard"  # standard | high (excludes DeepSeek, non-US providers)
```

---

## 8. Implementation Phases

### Phase 1: Quick Wins (1-2 weeks) -- Estimated 40-50% savings

**Zero or minimal code changes, immediate cost reduction:**

1. **Enhanced prompt caching**
   - Add cache breakpoints to language-specific and repo-specific guidelines
   - Use 1-hour cache TTL for webhook mode (sustained sessions)
   - Files: `agent/api/runner.py`

2. **Application-level response caching**
   - Cache review results by diff content hash
   - Skip API call entirely on CI retriggers with identical diff
   - Files: `handlers/review.py` (add cache layer)

3. **Incremental reviews**
   - Store last-reviewed commit SHA per PR
   - On new push, only review the incremental diff
   - Files: `handlers/review.py`, `platforms/*/platform.py`

4. **Batch API for push events**
   - Route non-interactive push events to Anthropic Batch API (50% off)
   - Keep `@mention` reviews as real-time
   - Files: `agent/api/runner.py` (add batch mode)

### Phase 2: Multi-Model Routing (2-4 weeks) -- Estimated 60-70% savings

**Add intelligence to model selection:**

5. **Heuristic router**
   - Implement diff-based classifier (size, paths, language)
   - Route simple PRs to Haiku 4.5 (5x cheaper than Sonnet)
   - Route security-sensitive paths to Opus
   - Files: New `routing/router.py`, modify `handlers/review.py`

6. **Judge agent pass**
   - Add second-pass evaluation of review findings
   - Filter out unhelpful/noisy suggestions before posting
   - Use Haiku 4.5 for the judge (cheap, fast)
   - Files: New `review/judge.py`, modify `handlers/review.py`

7. **Structured output optimization**
   - Tighten JSON schema for Tier 1 reviews
   - Add severity-based filtering to prompts
   - Files: `prompts/reviewer_prompt.md`, `handlers/review.py`

### Phase 3: Multi-Vendor (3-6 weeks) -- Estimated 70-80% savings

**Break vendor lock-in, access cheaper models:**

8. **Provider abstraction layer**
   - Create `LLMProvider` protocol with implementations for Anthropic, Google, OpenAI
   - Unified message format with provider-specific adapters
   - Files: `llm/` directory

9. **Google Gemini integration**
   - Gemini 2.5 Flash-Lite for triage and judge ($0.10/MTok -- 30x cheaper than Sonnet)
   - Gemini 2.5 Pro for standard reviews ($1.25/MTok -- 2.4x cheaper than Sonnet)
   - Files: New `llm/google.py`

10. **DeepSeek / OpenAI fallback**
    - DeepSeek V3.2 as ultra-cheap option for non-sensitive repos ($0.28/$0.42 per MTok)
    - GPT-4.1 as Sonnet alternative ($2.00/$8.00 per MTok)
    - Files: New `llm/openai.py (DeepSeek)`, `llm/openai.py`

11. **Per-repo configuration**
    - `.nominal/config.yaml` for routing rules, provider preferences, data sensitivity
    - Files: Extend `config.py`, new `routing/config.py`

### Phase 4: Open-Source & Fine-Tuning (6-12 weeks) -- Estimated 80-90% savings at scale

**For high-volume deployments:**

12. **Hosted open-source integration**
    - Route Tier 1 reviews to Qwen3-Coder via Fireworks/Together ($0.20/MTok)
    - Route triage to Qwen3-Coder-Next (3B active params, near-frontier quality)
    - Files: New `llm/openai.py (Fireworks)`

13. **Review data collection pipeline**
    - Log all reviews + developer feedback (resolved, dismissed, reactions)
    - Build training dataset for fine-tuning
    - Files: New `metrics/collector.py`, extend webhook handlers

14. **Fine-tuned model deployment**
    - Fine-tune Qwen 2.5 Coder 7B on collected review data
    - Deploy via Fireworks or self-hosted vLLM
    - Use as primary Tier 1 model

15. **Observability dashboard**
    - Cost per review, model distribution, quality metrics
    - Automatic threshold tuning for routing decisions
    - Files: New `metrics/dashboard.py` or Grafana/Prometheus integration

---

## 9. Cost Projections

### Assumptions
- 1,000 PRs/month
- Average diff: 300 lines changed, 5 files
- Average input: ~8,000 tokens (system prompt + guidelines + diff + comments)
- Average output: ~2,000 tokens (review JSON)
- 20% are simple (Tier 1), 65% standard (Tier 2), 15% complex (Tier 3)
- 10% of reviews are CI retriggers (cacheable)
- 30% are rapid pushes to existing PRs (batch-eligible or incremental)

### Monthly Cost Estimates

| Strategy | Input Cost | Output Cost | Total/Month | Savings vs Current |
|----------|-----------|-------------|-------------|-------------------|
| **Current** (Sonnet 4.6 for all) | $24.00 | $30.00 | **$54.00** | Baseline |
| **Phase 1** (caching + batch + incremental) | $10.00 | $18.00 | **$28.00** | **48%** |
| **Phase 2** (+ multi-model routing) | $5.50 | $10.00 | **$15.50** | **71%** |
| **Phase 3** (+ multi-vendor) | $3.00 | $6.00 | **$9.00** | **83%** |
| **Phase 4** (+ open-source triage) | $1.50 | $4.00 | **$5.50** | **90%** |

### At Higher Volume (10,000 PRs/month)

| Strategy | Total/Month | Per-Review Cost |
|----------|-------------|----------------|
| **Current** | ~$540 | $0.054 |
| **Phase 3** | ~$90 | $0.009 |
| **Phase 4 + self-hosting** | ~$30-50 | $0.003-0.005 |

---

## 10. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Quality degradation from cheaper models** | Missed bugs, unhelpful feedback | Judge agent filters bad suggestions; A/B test with shadow mode before switching; track finding acceptance rate |
| **Multi-vendor API inconsistency** | Different response formats, tool support | Provider abstraction layer normalizes responses; comprehensive test suite per provider |
| **DeepSeek data sensitivity** | Code sent to Chinese infrastructure | Configurable `data_sensitivity` flag per repo; exclude DeepSeek for sensitive repos |
| **Rate limits across providers** | Review delays during high volume | Automatic fallback chain: primary -> secondary -> tertiary provider |
| **Open-source model quality variance** | Inconsistent review depth | Always available escalation to Claude Sonnet/Opus for low-confidence findings |
| **Increased system complexity** | More components to maintain | Phased implementation; each phase is independently valuable; feature flags for rollback |
| **Cache staleness** | Stale reviews served for changed guidelines | Include guidelines hash in cache key; invalidate on `.nominal/` file changes |
| **Fine-tuned model drift** | Model quality degrades as codebase evolves | Continuous retraining pipeline; monitor quality metrics; automatic fallback to commercial API |
| **Vendor pricing changes** | Cost assumptions invalidated | Multi-vendor strategy inherently hedges; routing rules easily updated |

### Quality Assurance: Shadow Mode

Before switching any production traffic to a new model/provider, run in **shadow mode**:

1. Continue using Sonnet 4.6 as primary (post its review)
2. Simultaneously send the same diff to the new model
3. Compare findings: precision (correct findings / total findings) and recall (caught issues / actual issues)
4. Only promote the new model when it meets quality thresholds (e.g., >90% precision, >80% recall vs Sonnet baseline)

---

## 11. Sources

### Pricing & Provider Comparisons
- [Anthropic API Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [TLDL: LLM API Pricing February 2026](https://www.tldl.io/resources/llm-api-pricing-2026)
- [IntuitionLabs: AI API Pricing Comparison 2026](https://intuitionlabs.ai/articles/ai-api-pricing-comparison-grok-gemini-openai-claude)

### Routing & Cascading
- [RouteLLM: Cost-Effective LLM Routing (LMSYS)](https://lmsys.org/blog/2024-07-01-routellm/)
- [RouteLLM GitHub Repository](https://github.com/lm-sys/RouteLLM)
- [Swfte: Multi-Model AI Cuts Costs 85%](https://www.swfte.com/blog/intelligent-llm-routing-multi-model-ai)
- [AWS: Multi-LLM Routing Strategies](https://aws.amazon.com/blogs/machine-learning/multi-llm-routing-strategies-for-generative-ai-applications-on-aws/)
- [ETH Zurich: Unified Routing and Cascading](https://arxiv.org/html/2410.10347v1)

### Caching & Cost Reduction
- [Anthropic: Prompt Caching Documentation](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Thomson Reuters Labs: 60% Cost Reduction with Prompt Caching](https://medium.com/tr-labs-ml-engineering-blog/prompt-caching-the-secret-to-60-cost-reduction-in-llm-applications-6c792a0ac29b)
- [Medium: Prompt Caching -- $720 to $72/month](https://medium.com/@labeveryday/prompt-caching-is-a-must-how-i-went-from-spending-720-to-72-monthly-on-api-costs-3086f3635d63)
- [ngrok: 10x Cheaper Tokens with Prompt Caching](https://ngrok.com/blog/prompt-caching)
- [AWS: LLM Response Caching Strategies](https://aws.amazon.com/blogs/database/optimize-llm-response-costs-and-latency-with-effective-caching/)

### Open-Source Models for Code
- [Index.dev: Open Source Coding LLMs Ranked](https://www.index.dev/blog/open-source-coding-llms-ranked)
- [Qwen3-Coder GitHub](https://github.com/QwenLM/Qwen3-Coder)
- [NVIDIA: Fine-Tuning Small Language Models for Code Review](https://developer.nvidia.com/blog/fine-tuning-small-language-models-to-optimize-code-review-accuracy/)
- [Graphite: Open Source AI Code Review Tools](https://graphite.com/guides/best-open-source-ai-code-review-tools-2025)

### Case Studies
- [HubSpot: Automated Code Review -- The 6-Month Evolution](https://product.hubspot.com/blog/automated-code-review-the-6-month-evolution)
- [Koombea: Reduce AI Expenses by 80%](https://ai.koombea.com/blog/llm-cost-optimization)
- [Hacker News: 92% Cost Savings with Open-Source Cascading](https://news.ycombinator.com/item?id=46288111)

### General Cost Optimization
- [FutureAGI: LLM Cost Optimization Guide](https://futureagi.com/blogs/llm-cost-optimization-2025)
- [DataCamp: Top 10 Methods to Reduce LLM Costs](https://www.datacamp.com/blog/ai-cost-optimization)
- [Analytics Vidhya: 10 Ways to Slash Inference Costs](https://www.analyticsvidhya.com/blog/2025/12/llm-cost-optimization/)
- [MindStudio: Best AI Model Routers](https://www.mindstudio.ai/blog/best-ai-model-routers-multi-provider-llm-cost-011e6/)
- [NVIDIA NIM for Developers](https://developer.nvidia.com/nim)
