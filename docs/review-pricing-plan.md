# Plan: Compute the Price of a Code Review Job

## Context

Nominal Code uses LLM APIs (Anthropic, OpenAI, Google, etc.) to power automated code reviews. Today, every API call's token usage data is **silently discarded** — providers return input/output token counts, but the `_to_llm_response()` functions in each provider throw them away. There is no way to know how much a review costs.

Being able to measure cost per review is the prerequisite for all cost optimization work (model routing, caching strategies, batching) described in `COST_OPTIMIZATION_PLAN.md`. Without it, there's no baseline and no way to measure impact.

The goal is to capture token usage from every LLM API call, accumulate it across the multi-turn agentic loop, compute a dollar cost using a pricing table, and surface the result in logs and the `AgentResult` / `ReviewResult` data models.

---

## Design

### Data flow

```
Provider SDK response (has token counts)
        │
        ▼
_to_llm_response() ── extracts ──▶ TokenUsage ──▶ LLMResponse.usage
        │
        ▼
run_agent_api() loop ── accumulates per turn ──▶ total TokenUsage
        │
        ▼
build_cost_summary() ── applies pricing table ──▶ CostSummary
        │
        ▼
AgentResult.cost ──▶ ReviewResult.cost ──▶ logs / CI output
```

For the CLI runner, the SDK already provides `ResultMessage.total_cost_usd` and `ResultMessage.usage`, so we extract those directly instead of accumulating per-turn.

---

## Step 1 — Add `TokenUsage` dataclass and extend `LLMResponse`

**File:** `app/nominal_code/llm/messages.py`

Add a frozen dataclass for per-call token counts:

```python
@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0   # Anthropic-specific, 0 for others
    cache_read_input_tokens: int = 0       # Anthropic-specific, 0 for others
```

Add a `usage` field to `LLMResponse`:

```python
@dataclass(frozen=True)
class LLMResponse:
    content: list[TextBlock | ToolUseBlock]
    stop_reason: StopReason
    response_id: str | None = None
    usage: TokenUsage | None = None        # NEW — backwards-compatible default
```

---

## Step 2 — Create cost module with pricing table

**File (new):** `app/nominal_code/llm/cost.py`

Contains:

- **`ModelPricing`** — `NamedTuple` with `input_per_token`, `output_per_token`, `cache_write_per_token`, `cache_read_per_token` (all in $/token, not $/MTok)
- **`PRICING`** — `dict[str, ModelPricing]` keyed by model identifier string, covering every default model in `registry.PROVIDERS`:

  | Model | Input $/MTok | Output $/MTok | Cache Write | Cache Read |
  |-------|-------------|--------------|-------------|------------|
  | `claude-sonnet-4-20250514` | 3.00 | 15.00 | 3.75 | 0.30 |
  | `claude-opus-4-20250514` | 15.00 | 75.00 | 18.75 | 1.50 |
  | `claude-haiku-4-20250514` | 1.00 | 5.00 | 1.25 | 0.10 |
  | `gpt-4.1` | 2.00 | 8.00 | — | — |
  | `gemini-2.5-flash` | 0.30 | 2.50 | — | — |
  | `deepseek-chat` | 0.28 | 0.42 | — | — |
  | `llama-3.3-70b-versatile` | 0.59 | 0.79 | — | — |
  | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | 0.88 | 0.88 | — | — |
  | `accounts/fireworks/models/llama-v3p3-70b-instruct` | 0.90 | 0.90 | — | — |

- **`CostSummary`** — Frozen dataclass with `total_input_tokens`, `total_output_tokens`, `total_cache_creation_tokens`, `total_cache_read_tokens`, `total_cost_usd: float | None`, `provider: str`, `model: str`, `num_api_calls: int`
- **`accumulate_usage(existing, new) -> TokenUsage`** — Sums two `TokenUsage` instances
- **`compute_cost(usage, model) -> float | None`** — Looks up `_get_pricing()[model]` and multiplies; returns `None` for unknown models
- **`build_cost_summary(usage, model, provider, num_api_calls) -> CostSummary`** — Combines the above

### Why a self-managed pricing table (not LiteLLM / tokencost)?

| Option | Verdict | Reason |
|--------|---------|--------|
| **LiteLLM** `completion_cost()` | Skip | Heavy dependency (~15 MB, many transitive deps), designed as a full proxy/router layer. We only need a price lookup. |
| **tokencost** | Skip | Infrequently updated, may lag new models. Adds a dependency for what is ~50 lines of data. |
| **LangFuse / Helicone** | Defer | Full observability platforms — overkill for cost tracking alone. Worth revisiting when building `/metrics`. |
| **Vendor LiteLLM's `model_prices_and_context_window.json`** | Optional future fallback | MIT-licensed, ~250KB JSON file with 100+ models. Could be vendored as a fallback for models not in our `PRICING` dict. No runtime dependency. |
| **Self-managed dict** | **Use this** | Zero dependencies, trivial to maintain (one dict), covers exactly the 7+ models we support, co-located with the code that uses it. |

---

## Step 3 — Extend `AgentResult` and `ReviewResult`

**File:** `app/nominal_code/agent/result.py`

```python
@dataclass(frozen=True)
class AgentResult:
    # ... existing fields ...
    cost: CostSummary | None = None    # NEW
```

**File:** `app/nominal_code/handlers/review.py`

```python
@dataclass(frozen=True)
class ReviewResult:
    # ... existing fields ...
    cost: CostSummary | None = None    # NEW
```

Both are backwards-compatible (default `None`).

---

## Step 4 — Extract token usage from each provider

### Anthropic (`app/nominal_code/llm/anthropic.py`)

Modify `_to_llm_response()` to read `response.usage`:
- `response.usage.input_tokens` → uncached input tokens
- `response.usage.output_tokens`
- `response.usage.cache_creation_input_tokens` (use `getattr` with default 0 for backwards compat)
- `response.usage.cache_read_input_tokens`

**Anthropic cache billing note:** `input_tokens` is the *uncached* portion. Cache creation and read tokens are reported separately. The cost formula is:
```
cost = input_tokens × standard_rate
     + cache_creation_tokens × 1.25 × standard_rate
     + cache_read_tokens × 0.1 × standard_rate
     + output_tokens × output_rate
```

### OpenAI (`app/nominal_code/llm/openai.py`)

Two paths to update:
1. **`_to_llm_response()`** (Chat Completions) — read `response.usage.prompt_tokens`, `response.usage.completion_tokens`
2. **`_responses_to_llm_response()`** (Responses API) — read `response.usage.input_tokens`, `response.usage.output_tokens`
3. **`_send_responses_api()`** — carry `usage` through when reconstructing `LLMResponse` with `response_id`

### Google (`app/nominal_code/llm/google.py`)

Modify `_to_llm_response()` to read `response.usage_metadata.prompt_token_count`, `response.usage_metadata.candidates_token_count`.

### DeepSeek, Groq, Together, Fireworks

All use `OpenAIProvider` → Chat Completions path. Covered by the OpenAI change automatically.

---

## Step 5 — Accumulate usage in the API runner

**File:** `app/nominal_code/agent/api/runner.py`

In `run_agent_api()`:

1. Add `provider_name: str = ""` parameter (threaded from `run_agent()`)
2. Initialize `accumulated_usage: TokenUsage | None = None` and `api_call_count: int = 0`
3. After each `provider.send()`:
   - Increment `api_call_count`
   - If `response.usage is not None`, call `accumulate_usage(accumulated_usage, response.usage)`
4. At every `return AgentResult(...)` site (there are 4: normal end, submit_review, max_turns, error), include `cost=build_cost_summary(accumulated_usage, model, provider_name, api_call_count)`

**File:** `app/nominal_code/agent/router.py`

Pass `provider_name=agent_config.provider.name` to `run_agent_api()`.

---

## Step 6 — Extract cost from CLI runner

**File:** `app/nominal_code/agent/cli/runner.py`

The `claude_agent_sdk.ResultMessage` already has:
- `total_cost_usd: float | None`
- `usage: dict[str, Any] | None`
- `duration_api_ms: int`

When processing `ResultMessage`, build a `CostSummary` from these fields:

```python
cli_cost = CostSummary(
    total_input_tokens=usage_dict.get("input_tokens", 0),
    total_output_tokens=usage_dict.get("output_tokens", 0),
    total_cache_creation_tokens=usage_dict.get("cache_creation_input_tokens", 0),
    total_cache_read_tokens=usage_dict.get("cache_read_input_tokens", 0),
    total_cost_usd=message.total_cost_usd,
    provider="claude-cli",
    model="",  # SDK doesn't expose this
)
```

The `usage` dict shape should be logged at DEBUG level on first use to confirm its exact keys.

---

## Step 7 — Surface cost in logs and output

### Structured logging (`app/nominal_code/handlers/review.py`)

Extend the existing log line:

```
Before: Reviewer finished for owner/repo#42 (findings=3, turns=5, duration=12345ms)
After:  Reviewer finished for owner/repo#42 (findings=3, turns=5, duration=12345ms, cost=$0.0412, tokens_in=8234, tokens_out=1856)
```

### CI output (`app/nominal_code/commands/ci.py`)

Print a cost summary block visible in GitHub Actions / GitLab CI logs:

```
Review completed for owner/repo#42
  Findings: 3 valid, 1 rejected
  Model: claude-sonnet-4-20250514 (anthropic)
  Tokens: 8,234 in / 1,856 out (cache read: 2,100)
  Cost: $0.0412
  Duration: 12,345ms (5 turns, 6 API calls)
```

### ReviewResult propagation

Pass `result.cost` into `ReviewResult(cost=result.cost)` so callers have access.

### JSON repair cost (deferred)

`_repair_review_output()` makes additional LLM calls. For now, log those costs separately rather than aggregating into the main review cost. Repair calls are rare.

---

## Step 8 — Tests

**New file:** `app/tests/llm/test_cost.py`

| Test case | What it validates |
|-----------|-------------------|
| `test_accumulate_usage_from_none` | `accumulate_usage(None, usage)` returns the usage unchanged |
| `test_accumulate_usage_sums_fields` | Two usages are correctly summed field-by-field |
| `test_compute_cost_known_model` | Returns correct dollar amount for each model in `PRICING` |
| `test_compute_cost_unknown_model` | Returns `None` for a model not in `PRICING` |
| `test_compute_cost_with_cache` | Anthropic cache tokens priced at different rates |
| `test_build_cost_summary_no_usage` | Returns zero-valued summary when usage is `None` |
| `test_all_registry_models_have_pricing` | Every default model in `registry.PROVIDERS` has an entry in `PRICING` |

**Extend existing provider test files:**

- `tests/llm/test_anthropic.py` — verify `_to_llm_response()` extracts usage (with and without cache fields)
- `tests/llm/test_openai.py` — both Chat Completions and Responses API paths
- `tests/llm/test_google.py` — verify `usage_metadata` extraction

**Extend runner tests:**

- `tests/agent/api/test_runner.py` — mock provider returning `LLMResponse` with usage across 2+ turns, verify `AgentResult.cost` has correct accumulated totals

---

## File change summary

| File | Change | Description |
|------|--------|-------------|
| `llm/messages.py` | Modify | Add `TokenUsage` dataclass, add `usage` field to `LLMResponse` |
| `llm/cost.py` | **New** | `CostSummary`, `ModelPricing`, `PRICING` dict, utility functions |
| `agent/result.py` | Modify | Add `cost: CostSummary \| None = None` field |
| `llm/anthropic.py` | Modify | Extract usage in `_to_llm_response()` |
| `llm/openai.py` | Modify | Extract usage in both response conversion functions |
| `llm/google.py` | Modify | Extract usage in `_to_llm_response()` |
| `agent/api/runner.py` | Modify | Accumulate usage across turns, add `provider_name` param |
| `agent/router.py` | Modify | Thread `provider_name` to `run_agent_api()` |
| `agent/cli/runner.py` | Modify | Extract cost from `ResultMessage` |
| `handlers/review.py` | Modify | Add `cost` to `ReviewResult`, extend log message |
| `commands/ci.py` | Modify | Print cost summary in CI output |
| `tests/llm/test_cost.py` | **New** | Unit tests for cost module |
| Existing LLM provider test files | Modify | Add usage extraction tests |
| `tests/agent/api/test_runner.py` | Modify | Add cost accumulation tests |

## Verification

1. **Unit tests:** `cd app && uv run pytest tests/llm/test_cost.py tests/llm/ tests/agent/api/test_runner.py -v`
2. **Type check:** `cd app && uv run mypy nominal_code/llm/cost.py nominal_code/llm/messages.py nominal_code/agent/result.py`
3. **Manual smoke test (CI mode):** Run `nominal-code ci github` on a test PR with `ANTHROPIC_API_KEY` set and `LOG_LEVEL=INFO`. Verify cost appears in log output.
4. **Manual smoke test (CLI mode):** Run `nominal-code review owner/repo#N` and verify cost appears in log output.
5. **Full test suite:** `cd app && uv run pytest` — all existing tests must still pass (changes are backwards-compatible via defaults).
