# feat/security Branch Analysis

Branch: `feat/security` (2 commits, 65 files, +2669/−1390 lines)

Commits:
- `c6fb67c` feat: security features
- `8c36ac0` feat: improve separation of concerns in codebase

---

## 1. Security — Agent Sandbox & Output Redaction

Prevents secret exfiltration from the LLM agent subprocess environment and redacts
known token patterns from tool output before returning it to the model.

### Files

| File | Status | What changed |
|------|--------|--------------|
| `agent/sandbox.py` | **New** | `SAFE_ENV_VARS` allowlist, `SECRET_PATTERNS` regexes, `build_sanitized_env()`, `sanitize_output()` |
| `agent/api/tools.py` | Modified | `SHELL_INJECTION_PATTERN`, `DEFAULT_ALLOWED_CLONE_HOSTS`, `GIT_CLONE_PATTERN` constants; `_validate_bash_command()`, `_validate_clone_host()` validators; `execute_tool()`, `_execute_bash()`, `_execute_grep()` now accept `sanitized_env` and `allowed_clone_hosts`; all tool output passes through `sanitize_output()` |
| `agent/api/runner.py` | Modified | `run_api_agent()` accepts and forwards `sanitized_env` + `allowed_clone_hosts` to `execute_tool()` |
| `agent/invoke.py` | Modified | `invoke_agent()` accepts `sanitized_env` + `allowed_clone_hosts`, routes them to `run_api_agent()` (API mode only) |
| `handlers/review.py` | Modified | Calls `build_sanitized_env()` and passes result to `invoke_agent()`; `post_review_result()` applies `sanitize_output()` to findings and summaries before posting |

### Tests

| File | Status | Coverage |
|------|--------|----------|
| `tests/agent/test_sandbox.py` | **New** | 23 tests: env allowlist filtering, extra safe vars, secret pattern redaction (GitLab PAT, GitHub PAT, OpenAI key, Google API key, RSA private key, bearer token) |
| `tests/agent/api/test_tools.py` | Expanded | Shell injection blocking, clone host validation, tool output sanitization |

### How it works

```
review()
  └─ build_sanitized_env()           → allowlist-filtered os.environ
  └─ invoke_agent(sanitized_env=...)
       └─ run_api_agent()
            └─ execute_tool()
                 ├─ _validate_bash_command()  → reject shell metacharacters
                 ├─ _validate_clone_host()    → hostname allowlist
                 ├─ subprocess(env=sanitized_env)
                 └─ sanitize_output()         → redact tokens in output
```

---

## 2. Security — Prompt Injection Defense

Wraps all untrusted content (diffs, comments, user prompts) in XML boundary tags so
the LLM can distinguish system instructions from user-controlled input.

### Files

| File | Status | What changed |
|------|--------|--------------|
| `agent/prompts.py` | Modified | New constants: `TAG_UNTRUSTED_DIFF`, `TAG_UNTRUSTED_COMMENT`, `TAG_UNTRUSTED_REQUEST`, `TAG_UNTRUSTED_HUNK`, `TAG_FILE_PATH`, `TAG_BRANCH_NAME`, `TAG_REPO_GUIDELINES`; new `wrap_tag()` function; `resolve_system_prompt()` wraps guidelines in `<repo-guidelines>` tags |
| `handlers/review.py` | Modified | `_build_reviewer_prompt()` wraps diffs in `<untrusted-diff>`, comments in `<untrusted-comment>`, user prompts in `<untrusted-request>`, file paths in `<file-path>`, branch names in `<branch-name>` |
| `prompts/reviewer_prompt.md` | Modified | Instructions telling the model to respect boundary tags and treat tagged content as untrusted |
| `prompts/system_prompt.md` | Modified | Same boundary tag awareness instructions for worker bot |

### Tests

| File | Status | Coverage |
|------|--------|----------|
| `tests/agent/test_prompts.py` | Expanded | `wrap_tag()` behavior, `resolve_system_prompt()` with guideline tagging |

---

## 3. Security — Git Repository Hardening

Prevents malicious repositories from executing code during clone/fetch via git hooks,
symlink escapes, or `.gitmodules` file protocol tricks.

### Files

| File | Status | What changed |
|------|--------|--------------|
| `workspace/git.py` | Modified | `_clone()`: passes `--config core.hooksPath=/dev/null`, `--config core.symlinks=false`, `--config protocol.file.allow=never` to `git clone`; `_update()`: sets the same three configs via `git config` before fetch/reset; `ensure_ready()`: detects pre-cloned workspaces (`.git` exists but no `clone_url`) and skips git operations |

### Tests

| File | Status | Coverage |
|------|--------|----------|
| `tests/workspace/test_git_hardening.py` | **New** | 8 tests: hooks disabled on clone, symlinks disabled, file protocol blocked, config set before fetch on update |
| `tests/workspace/test_git_pre_cloned.py` | **New** | 4 tests: externally managed workspace detection, skip git ops, raise when neither clone_url nor .git exist |

---

## 4. Security — Kubernetes Pod Hardening

Runs agent pods with minimal privileges: non-root user, read-only root filesystem,
dropped capabilities, no service account token.

### Files

| File | Status | What changed |
|------|--------|--------------|
| `config/kubernetes.py` | Modified | New fields: `pod_security_enabled` (default True), `run_as_user` (default 1000), `read_only_root_filesystem` (default True), `automount_service_account_token` (default False) |
| `jobs/runner/kubernetes.py` | Modified | Pod spec includes `securityContext` (readOnlyRootFilesystem, runAsNonRoot, runAsUser, allowPrivilegeEscalation=false, capabilities drop ALL), volume mounts for `/workspace` and `/tmp`, pod-level `automountServiceAccountToken` |
| `ci/Dockerfile` | Modified | Creates `nominal` user (UID 1000), creates `/workspace` + `/tmp/nominal` with correct ownership, switches to `USER nominal` |
| `.dockerignore` | **New** | Excludes `.git`, `__pycache__`, `.venv`, docs, tests from image |

### Tests

| File | Status | Coverage |
|------|--------|----------|
| `tests/jobs/runner/test_kubernetes.py` | Expanded | 25+ tests: security context fields, volume mounts, non-root user, capability drops, service account toggle |

---

## 5. Config Refactoring — Policy Separation

Decomposes the monolithic `Config` class into focused, frozen Pydantic models that
separate filtering (who/what to accept) from routing (how to dispatch).

### Files

| File | Status | What changed |
|------|--------|--------------|
| `config/policies.py` | **New** | `FilteringPolicy` (allowed_users, allowed_repos, pr_title_include_tags, pr_title_exclude_tags); `RoutingPolicy` (reviewer_triggers, worker_bot_username, reviewer_bot_username). Both frozen. |
| `config/settings.py` | Modified | New classes: `WorkerConfig`, `ReviewerConfig`, `PromptsConfig`, `WorkspaceConfig`, `RedisConfig`, `WebhookConfig` (bundles host, port, filtering, routing, kubernetes, redis). `Config` is now mode-aware with `from_env()`, `for_cli()`, `for_ci()` class methods. |
| `config/loader.py` | Modified | Assembles `FilteringPolicy` and `RoutingPolicy` from env vars, composes `WebhookConfig`, passes to `Config` |
| `config/__init__.py` | Modified | Updated exports to include new types |
| `config/models.py` | Modified | Removed fields that moved to `policies.py` |

### Tests

| File | Status | Coverage |
|------|--------|----------|
| `tests/config/test_policies.py` | **New** | 15 tests: frozen immutability, field defaults, equality, policy composition, override scenarios |
| `tests/test_config.py` | Updated | Config construction updated to use nested `webhook.filtering` and `webhook.routing` paths |

### Data flow

```
Old: config.allowed_users, config.reviewer_triggers, config.allowed_repos
New: config.webhook.filtering.allowed_users, config.webhook.routing.reviewer_triggers
```

---

## 6. Webhook Server Decomposition

Breaks the monolithic webhook handler into small, testable functions with explicit
policy parameters instead of a full `Config` dependency.

### Files

| File | Status | What changed |
|------|--------|--------------|
| `commands/webhook/server.py` | Modified | Extracted: `filter_event()`, `should_process_event()`, `dispatch_lifecycle_event()`, `dispatch_comment_event()`; handler receives `FilteringPolicy` + `RoutingPolicy` from `WebhookConfig`; runner constructed via `build_runner()` |
| `commands/webhook/helpers.py` | Modified | `acknowledge_event()` signature: `Config` → `FilteringPolicy` |
| `commands/webhook/job.py` | Modified | Config access: `config.redis_url` → `webhook.redis.url` |
| `jobs/runner/__init__.py` | Modified | Re-exports `JobRunner` + `build_runner()` |
| `jobs/runner/base.py` | Modified | `build_runner(config, platforms)` factory extracted from server.py; constructs `KubernetesRunner` or `ProcessRunner` based on config |

### Tests

| File | Status | Coverage |
|------|--------|----------|
| `tests/commands/webhook/test_server.py` | Updated | Fixtures refactored to construct `FilteringPolicy`/`RoutingPolicy` |
| `tests/commands/webhook/test_auth.py` | Updated | Config structure updates |
| `tests/integration/github/test_webhook*.py` | Updated | Config initialization updates |
| `tests/integration/gitlab/test_webhook*.py` | Updated | Config initialization updates |

---

## 7. Platform Cleanup

Removes unused protocol methods and simplifies platform implementations.

### Files

| File | Status | What changed |
|------|--------|--------------|
| `platforms/base.py` | Modified | Removed `is_pr_open()` from `Platform` protocol |
| `platforms/github/platform.py` | Modified | Removed `is_pr_open()` implementation; GitHub App auth adjustments |
| `platforms/github/ci.py` | Modified | Minor updates to match platform changes |
| `platforms/gitlab/platform.py` | Modified | Removed `is_pr_open()` implementation; simplified |

### Tests

| File | Status | Coverage |
|------|--------|----------|
| `tests/platforms/github/test_github_api_base.py` | **New** | Tests for GitHub API base functionality |
| `tests/platforms/github/test_platform.py` | Updated | Removed `is_pr_open()` tests |
| `tests/platforms/gitlab/test_platform.py` | Updated | Removed `is_pr_open()` tests, structure updates |

---

## 8. Workspace Cleanup Removal

Removes the `WorkspaceCleaner` background task that periodically deleted workspaces
for closed PRs. In Kubernetes deployments, pods are ephemeral and don't need cleanup.

### Files

| File | Status | What changed |
|------|--------|--------------|
| `workspace/cleanup.py` | **Deleted** | `WorkspaceCleaner` class and all cleanup logic removed |
| `tests/workspace/test_cleanup.py` | **Deleted** | 443 lines of cleanup tests removed |
| `config/settings.py` | Modified | Removed `cleanup_interval_hours` field |
| `commands/webhook/server.py` | Modified | Removed `WorkspaceCleaner` initialization and lifecycle |
| `docs/architecture.md` | Modified | Removed "Workspace Cleaner" component and "Cleanup Loop" sections |
| `docs/deployment/index.md` | Modified | Removed cleanup configuration; notes K8s pods are ephemeral |
| `docs/reference/configuration.md` | Modified | Removed `workspace.cleanup_interval_hours` |

---

## 9. Documentation

### Files

| File | Status | What changed |
|------|--------|--------------|
| `docs/reference/policies.md` | **New** | Full reference for `FilteringPolicy` and `RoutingPolicy` with YAML mappings, Python examples, programmatic usage patterns |
| `docs/security.md` | Modified | Significantly expanded: agent sandbox, prompt injection defense, git hardening, pod security |
| `docs/architecture.md` | Modified | New "Policies" section, removed cleanup references, updated file tree |
| `docs/deployment/index.md` | Modified | K8s ephemeral pod note, manual cleanup for non-K8s |
| `docs/index.md` | Modified | Link to new policies reference |
| `docs/reference/configuration.md` | Modified | Removed cleanup field, link to policies reference |
| `docs/reference/env-vars.md` | Modified | Minor updates |

---

## Proposed Improvements

### 1. Eliminate `# type: ignore[assignment]` for `config.webhook`

**Problem**: `server.py:65` and `server.py:418` use `config.webhook  # type: ignore[assignment]`
because `Config.webhook` is typed as `WebhookConfig | None`, but the webhook server knows
it's always set.

**Fix**: Add a method to `Config`:

```python
def require_webhook(self) -> WebhookConfig:
    if self.webhook is None:
        raise ValueError("WebhookConfig is required but not configured")
    return self.webhook
```

Replace `config.webhook  # type: ignore[assignment]` with `config.require_webhook()`.

**Files**: `config/settings.py`, `commands/webhook/server.py`

---

### 2. Remove redundant `build_sanitized_env()` call from review handler

**Problem**: `handlers/review.py:296` calls `build_sanitized_env()` and passes it to
`invoke_agent()`. But `run_api_agent()` in `agent/api/runner.py` already builds a sanitized
env when none is provided. The call in the review handler is redundant boilerplate that every
handler would need to repeat.

**Fix**: Remove the explicit `build_sanitized_env()` call from `review.py`. Let
`invoke_agent()` / `run_api_agent()` handle it internally as a secure default.

**Files**: `handlers/review.py`

---

### 3. Add `file://` protocol rejection to `_validate_clone_host()`

**Problem**: Git config blocks `file://` at the git level (`protocol.file.allow=never`),
but `_validate_clone_host()` in `tools.py` doesn't check for it. If git config is
bypassed, `file://` clones would pass tool-level validation.

**Fix**: Add an explicit check at the top of `_validate_clone_host()`:

```python
if url.startswith("file://"):
    raise ToolError("file:// protocol is not allowed")
```

**Files**: `agent/api/tools.py`

---

### 4. Harden `wrap_tag()` against tag injection

**Problem**: If untrusted content contains `</tag-name>`, it prematurely closes the
boundary tag. For example, a diff containing `</untrusted-diff>` would break the
boundary, potentially enabling prompt injection.

**Fix**: Escape closing tags in content before wrapping:

```python
def wrap_tag(tag: str, content: str) -> str:
    safe_content: str = content.replace(f"</{tag}>", f"<\\/{tag}>")
    return f"<{tag}>\n{safe_content}\n</{tag}>"
```

**Files**: `agent/prompts.py`

---

### 5. Replace `SystemExit(1)` with proper exception in `build_runner()`

**Problem**: `jobs/runner/base.py:65` raises `SystemExit(1)` when Redis is missing for
K8s mode. `SystemExit` bypasses normal exception handling and is difficult to test.

**Fix**: Raise `ValueError` (or a custom `ConfigurationError`) instead:

```python
raise ValueError("REDIS_URL is required when kubernetes config is set")
```

Let the caller (`run_webhook_server()`) catch it alongside other config errors.

**Files**: `jobs/runner/base.py`

---

### 6. Add Pydantic validators to policy models

**Problem**: No validation prevents `worker_bot_username == reviewer_bot_username`
(username collision), or catches an empty `allowed_users` set when webhook mode requires
authorization.

**Fix**: Add a `@model_validator` to `RoutingPolicy`:

```python
@model_validator(mode="after")
def check_no_username_collision(self) -> RoutingPolicy:
    if (
        self.worker_bot_username
        and self.reviewer_bot_username
        and self.worker_bot_username == self.reviewer_bot_username
    ):
        raise ValueError("worker and reviewer bot usernames must differ")
    return self
```

Verify that `load_config()` already validates non-empty `allowed_users` for webhook mode
(it likely does — if not, add it there).

**Files**: `config/policies.py`, possibly `config/loader.py`

---

### 7. Consolidate secret pattern detection

**Problem**: Token redaction exists in two places with different patterns:
- `agent/sandbox.py`: `SECRET_PATTERNS` — regex list for API tokens, private keys, bearer tokens
- `workspace/git.py`: `TOKEN_PATTERN` — regex for HTTP basic auth URLs

**Fix**: Move `TOKEN_PATTERN` into `sandbox.py` alongside `SECRET_PATTERNS` and expose
a `redact_url()` helper. Have `git.py` import from `sandbox.py` instead of defining
its own pattern.

**Files**: `agent/sandbox.py`, `workspace/git.py`

---

### 8. Use `spec=` on mocks in webhook server tests

**Problem**: Some mocks in `test_server.py` use bare `MagicMock()` without `spec`,
which silently allows calls to methods that don't exist on the real object. This can
hide interface drift when protocols change.

**Fix**: Add `spec=Platform`, `spec=JobRunner`, etc. to mock constructors:

```python
mock_platform = AsyncMock(spec=Platform)
mock_runner = AsyncMock(spec=JobRunner)
```

**Files**: `tests/commands/webhook/test_server.py`

---

### 9. Warn about disk accumulation in non-K8s mode

**Problem**: Workspace cleanup was removed silently. Non-K8s deployments accumulate PR
workspace directories on disk with no automated cleanup.

**Fix**: Add an explicit warning in `docs/deployment/index.md` about disk accumulation
in non-K8s mode and suggest a cron job (e.g. `find /tmp/nominal-code -maxdepth 3 -mtime +7 -type d -exec rm -rf {} +`).

**Files**: `docs/deployment/index.md`

---

### 10. Split prompt building from security tagging in review handler

**Problem**: `_build_reviewer_prompt()` in `handlers/review.py` mixes business logic
(assembling PR context) with security concerns (wrapping content in boundary tags).
The function is ~70 lines with interleaved `wrap_tag()` calls that add visual noise.

**Fix**: The boundary tagging is already well-integrated and the function reads clearly.
However, if the tagging grows more complex (e.g. escaping, nesting), consider extracting
a `_tag_content(tag, content)` method that handles both escaping (improvement #4) and
wrapping in one call. For now this is low priority — the current implementation is
readable and correct.

**Files**: `handlers/review.py` (future consideration)

---

## Module Splits & Structural Improvements

### Module size overview

| Module | Lines | Verdict |
|--------|------:|---------|
| `platforms/github/platform.py` | 977 | Split into submodules |
| `platforms/gitlab/platform.py` | 773 | Split into submodules |
| `agent/api/tools.py` | 731 | OK (cohesive) |
| `handlers/review.py` | 608 | Split into subpackage |
| `llm/openai.py` | 543 | Deduplicate with siblings |
| `commands/webhook/server.py` | 468 | OK (already decomposed) |
| `config/models.py` | 400 | Split models from env loading |
| `platforms/base.py` | 395 | OK (clean protocol layer) |
| `config/loader.py` | 353 | OK (minor refactor) |
| `config/settings.py` | 323 | OK |

### Split 1: `platforms/github/platform.py` (977 lines) → submodules

The platform file bundles 5 unrelated responsibilities into one class. Each group has
zero coupling with the others beyond sharing the HTTP client.

**Proposed structure:**

```
platforms/github/
├── __init__.py          # re-exports GitHubPlatform, _create_github_platform
├── platform.py          # (~250 lines) Core class, __init__, _request(), authenticate(),
│                        #   build_clone_url(), verify_webhook(), properties
├── events.py            # (~150 lines) parse_event(), _parse_issue_comment(),
│                        #   _parse_review_comment(), _parse_review(), _parse_pull_request()
├── comments.py          # (~150 lines) post_reply(), post_reaction(), post_pr_reaction(),
│                        #   _format_suggestion_body()
├── fetch.py             # (~250 lines) fetch_pr_branch(), fetch_pr_diff(),
│                        #   fetch_pr_comments(), _fetch_issue_comments(),
│                        #   _fetch_review_comments()
├── review.py            # (~100 lines) submit_review()
├── auth.py              # (existing) GitHub App auth
└── ci.py                # (existing) CI-mode platform
```

**Why:** Event parsing, comment posting, diff fetching, and review submission are
independent operations that share only the HTTP client. Splitting makes each file
scannable and testable in isolation.

### Split 2: `platforms/gitlab/platform.py` (773 lines) → mirror GitHub structure

Same pattern as GitHub for consistency:

```
platforms/gitlab/
├── __init__.py
├── platform.py          # Core class, _request(), authenticate(), verify_webhook()
├── events.py            # parse_event(), _parse_note(), _parse_merge_request()
├── comments.py          # post_reply(), post_reaction(), post_pr_reaction()
├── fetch.py             # fetch_pr_branch(), fetch_pr_diff(), fetch_pr_comments()
└── review.py            # submit_review() (~103 lines, most complex function)
```

### Split 3: `config/models.py` (400 lines) → separate env loading

The file mixes Pydantic model definitions (data shapes) with environment variable
parsing logic (loading strategy). These are distinct concerns.

**Proposed structure:**

```
config/
├── models.py            # (~200 lines) All *Settings Pydantic models (pure data shape)
├── env.py               # (~200 lines) _yaml_settings_source(), _collect_env_overrides(),
│                        #   _set_nested(), _deep_merge(), AppSettings.from_env()
├── loader.py            # (existing) Assembles Config from AppSettings
├── settings.py          # (existing) Runtime Config + sub-configs
├── policies.py          # (existing) FilteringPolicy, RoutingPolicy
└── ...
```

**Why:** Separating data shapes from acquisition strategies makes it easier to test
env mapping independently and to add new config sources (TOML, secrets manager) later.

### Split 4: `handlers/review.py` (608 lines) → subpackage

The review handler orchestrates 12 sequential steps spanning workspace setup, prompt
building, agent invocation, output parsing, and result posting. These are distinct
phases that can be separated.

**Proposed structure:**

```
handlers/
├── review/
│   ├── __init__.py          # re-exports review(), run_and_post_review(), ReviewResult
│   ├── context.py           # (~100 lines) ReviewContext, _prepare_review_context()
│   ├── handler.py           # (~150 lines) review(), run_and_post_review()
│   ├── posting.py           # (~60 lines) post_review_result()
│   └── prompt.py            # (~100 lines) _build_reviewer_prompt(),
│                            #   _format_existing_comments()
├── output.py                # (existing, cohesive — keep as-is)
├── diff.py                  # (existing)
└── worker.py                # (existing)
```

**Why:** The 608-line file coordinates workspace setup, prompt composition, agent
invocation, output repair, finding filtering, and result posting. Splitting by phase
makes each file focused and the orchestration in `handler.py` becomes a clean 3-step
pipeline: prepare → invoke → postprocess.

---

## Code Quality Improvements

### 11. Extract pagination helper for platform implementations

**Problem:** The pagination loop pattern is duplicated 3+ times across GitHub and GitLab
platforms (~170 lines total). Each implementation follows the identical structure:

```python
while True:
    response = await self._request("GET", url)
    data = response.json()
    if not data: break
    for entry in data: results.append(...)
    if len(data) < per_page: break
    page += 1
```

Locations:
- `platforms/github/platform.py`: `_fetch_issue_comments()`, `_fetch_review_comments()`,
  `fetch_pr_diff()` (partial)
- `platforms/gitlab/platform.py`: `fetch_pr_comments()`

**Fix:** Extract a shared async generator:

```python
async def paginate_api(
    request_fn: Callable[[str], Awaitable[httpx.Response]],
    url_template: str,
    per_page: int = 100,
) -> AsyncIterator[dict[str, Any]]:
    page: int = 1
    while True:
        response = await request_fn(url_template.format(page=page, per_page=per_page))
        items: list[dict[str, Any]] = response.json()
        if not items:
            break
        for item in items:
            yield item
        if len(items) < per_page:
            break
        page += 1
```

**Files:** New `platforms/pagination.py`, then simplify `github/platform.py` and
`gitlab/platform.py`

---

### 12. Deduplicate LLM provider conversion logic

**Problem:** All three LLM providers (`openai.py`, `anthropic.py`, `google.py`) implement
nearly identical `_to_api_messages()`, `_to_api_tools()`, and `_to_llm_response()`
functions (~80 lines each, ~240 lines total duplication).

Each follows the same pattern: iterate canonical messages, convert `TextBlock` /
`ToolUseBlock` / `ToolResultBlock` to provider-specific format, and map stop reasons.

**Fix:** Create a shared conversion module parameterized by provider-specific field mappings:

```python
# llm/convert.py
def convert_messages(
    messages: list[LLMMessage],
    text_mapper: Callable[[TextBlock], dict],
    tool_use_mapper: Callable[[ToolUseBlock], dict],
    tool_result_mapper: Callable[[ToolResultBlock], dict],
) -> list[dict[str, Any]]:
    ...
```

Each provider then supplies its mappers as small, focused functions.

**Files:** New `llm/convert.py`, simplify `llm/openai.py`, `llm/anthropic.py`,
`llm/google.py`

---

### 13. Replace `isinstance()` dispatch with polymorphic agent config

**Problem:** Runtime `isinstance(agent_config, ApiAgentConfig)` checks appear in 4+
locations:
- `agent/invoke.py:53, 64, 119, 188`
- `handlers/review.py:285`

Each caller needs to know about both config types and their specific behaviors.

**Fix:** Add a dispatch method to the agent config base:

```python
class AgentConfig(BaseModel):
    async def run_agent(self, ...) -> AgentResult:
        raise NotImplementedError

class ApiAgentConfig(AgentConfig):
    async def run_agent(self, ...) -> AgentResult:
        return await run_api_agent(...)

class CliAgentConfig(AgentConfig):
    async def run_agent(self, ...) -> AgentResult:
        return await run_cli_agent(...)
```

Then `invoke_agent()` becomes: `return await agent_config.run_agent(...)`.

**Files:** `config/agent.py`, `agent/invoke.py`, `handlers/review.py`

---

### 14. Use dict-based tool dispatch in `execute_tool()`

**Problem:** `agent/api/tools.py:execute_tool()` (79 lines) uses a chain of `if name ==`
checks. Adding a new tool requires modifying the dispatcher.

**Fix:** Replace with a registry:

```python
TOOL_EXECUTORS: dict[str, Callable[..., Awaitable[str] | str]] = {
    "Read": _execute_read,
    "Glob": _execute_glob,
    "Grep": _execute_grep,
    "Bash": _execute_bash,
}
```

Then dispatch becomes: `executor = TOOL_EXECUTORS.get(name)`.

**Files:** `agent/api/tools.py`

---

### 15. Extract `_build_position_payload()` from GitLab `submit_review()`

**Problem:** `platforms/gitlab/platform.py:submit_review()` is 103 lines — the most
complex function in the codebase. It fetches MR versions, builds position payloads
per-finding, and handles per-finding errors in a nested loop.

**Fix:** Extract:
- `_build_position_payload(finding, version_data)` — builds the position dict
- `_post_finding_discussion(finding, position, url)` — posts a single discussion thread

This reduces `submit_review()` to a clean loop over findings.

**Files:** `platforms/gitlab/platform.py` (or `platforms/gitlab/review.py` after split)

---

### 16. Add `Config.require_webhook()` accessor (from improvement #1, expanded)

Extend this pattern to other optional config fields:

```python
def require_reviewer(self) -> ReviewerConfig:
    if self.reviewer is None:
        raise ValueError("ReviewerConfig is required but not configured")
    return self.reviewer
```

This eliminates the scattered `if config.reviewer is None: raise RuntimeError(...)`
checks in `handlers/review.py:255` and `handlers/review.py:481`.

**Files:** `config/settings.py`, `handlers/review.py`, `commands/webhook/server.py`

---

## Verification Results

- **Ruff lint**: 3 fixable errors found and auto-fixed (unsorted imports in `server.py`, unused imports `Path` and `call` in `test_git_hardening.py`)
- **Ruff format**: 3 files reformatted (`server.py`, `test_git_hardening.py`, `test_git_pre_cloned.py`)
- **Pytest**: 971 passed, 28 deselected in 4.78s — all tests pass
