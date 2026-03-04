# Security

This page covers the security model, threat surface, and hardening recommendations for Nominal Code — with particular focus on LLM-related risks.

## Trust Model

| Category | Input | Trust Level |
|----------|-------|-------------|
| Configuration | Environment variables, system prompts, webhook secrets, `ALLOWED_USERS` | **Trusted** — set by the operator |
| User content | PR diffs, PR comments, repository file content, webhook payloads | **Untrusted** — controlled by external contributors |
| Per-repo overrides | `.nominal/guidelines.md`, `.nominal/languages/{lang}.md` | **Semi-trusted** — committed by repo maintainers, injected into the system prompt |

!!! warning "Primary attack surface"

    PR diffs and repository content are the primary attack surface. Any contributor who can open a PR or push code can influence what the LLM sees and how it reasons.

## LLM Security

### Prompt Injection Risks

Prompt injection occurs when untrusted input manipulates the LLM into ignoring its instructions or performing unintended actions. In the context of AI code review, the main attack vectors are:

1. **Via PR diff** — malicious instructions embedded in code comments, docstrings, or string literals that the reviewer agent reads during analysis.

2. **Via PR comments** — adversarial text in the existing discussion context. When the agent processes conversation history, injected instructions in earlier comments can influence its behavior.

3. **Via repository content** — files read by the agent during review (e.g. configuration files, documentation, or other source files) may contain adversarial content designed to steer the agent.

4. **Via `.nominal/guidelines.md`** — per-repo guideline overrides are injected directly into the system prompt. A compromised or malicious guideline file can alter the agent's review behavior.

### Built-in Mitigations

The following mechanisms limit the impact of a successful prompt injection:

- **Read-only tool restrictions** — the reviewer bot can only use `Read`, `Glob`, `Grep`, and `Bash(git clone*)`. Even if prompt-injected, it cannot modify files, push code, or call arbitrary commands.

- **Bash command allowlisting** — bash commands are checked against `fnmatch` patterns before execution. Commands that don't match an allowed pattern are rejected with an error.

- **`ALLOWED_USERS` gating** — only users listed in `ALLOWED_USERS` can trigger the agent via comments. Unauthorized users are silently ignored, preventing external actors from directly prompting the agent.

- **Turn and token caps** — `AGENT_MAX_TURNS` limits the number of agent loop iterations, and `MAX_RESPONSE_TOKENS` (16,384) caps each LLM response. These prevent runaway agent loops.

- **Diff line validation** — review findings are validated against the actual diff. Findings that reference lines outside the diff are filtered out and appended to the summary instead.

### Recommendations

- **Prefer reviewer-only mode** — the reviewer bot's read-only tool set drastically limits the blast radius of prompt injection. Use the worker bot at your own risk.

- **Keep `ALLOWED_USERS` tight** — only grant access to trusted team members. In open-source repos, this prevents external contributors from prompting the agent directly.

- **Use read-only reviewer tokens** — set `GITHUB_REVIEWER_TOKEN` (or `GITLAB_REVIEWER_TOKEN`) to a token with only read and comment permissions. This adds a second layer of defense beyond tool restrictions.

- **Set `AGENT_MAX_TURNS`** — configure a reasonable cap (e.g. 10–20) to limit how many iterations the agent can run, reducing the window for exploitation.

- **Review `.nominal/guidelines.md` changes carefully** — these files are injected into the system prompt. Treat changes to them with the same scrutiny as CI configuration changes.

- **For open-source repos, prefer CI mode** — CI mode runs automatically on PR events without accepting user-supplied prompts, eliminating comment-based injection vectors entirely.

!!! danger "Worker bot considerations"

    The worker bot runs with **full tool access** (`bypassPermissions`) and can modify files, run arbitrary commands, and push commits. A successful prompt injection against the worker bot could result in arbitrary code execution and unauthorized repository changes. Only enable it in trusted, private repositories with a restricted set of allowed users.

## Webhook Verification

### GitHub

Webhook payloads are verified using HMAC-SHA256. The `X-Hub-Signature-256` header is compared against the expected signature using `hmac.compare_digest()` for constant-time comparison:

```python
expected = "sha256=" + hmac.new(
    webhook_secret.encode(), body, hashlib.sha256,
).hexdigest()

return hmac.compare_digest(signature, expected)
```

### GitLab

GitLab webhooks are verified by comparing the `X-Gitlab-Token` header against the configured shared secret, also using `hmac.compare_digest()`.

!!! warning "Verification skipped when no secret is configured"

    If `GITHUB_WEBHOOK_SECRET` or `GITLAB_WEBHOOK_SECRET` is not set, signature verification is skipped entirely and all payloads are accepted. Always configure a webhook secret in production.

### Request Size Limit

The webhook server enforces a **5 MB** maximum request body size. Payloads exceeding this limit are rejected before processing.

## Authentication

### GitHub PAT Mode

Set `GITHUB_TOKEN` to a personal access token. Optionally set `GITHUB_REVIEWER_TOKEN` for the reviewer bot to use a separate, more restricted token.

### GitHub App Mode (Recommended)

GitHub App authentication uses RS256 JWTs to request short-lived installation tokens:

- JWTs expire after **600 seconds**
- Installation tokens are cached for **1 hour** and refreshed with a **5-minute** margin
- Tokens are automatically rotated — no long-lived secrets beyond the private key
- Scoped to the specific permissions granted to the App

Configure via `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (inline) or `GITHUB_APP_PRIVATE_KEY_PATH` (file path), and optionally `GITHUB_INSTALLATION_ID`.

### GitLab

Set `GITLAB_TOKEN` for full access. Optionally set `GITLAB_REVIEWER_TOKEN` for reviewer-specific operations.

## Authorization

### Comment Events

When a user mentions the bot in a PR comment, the author's username is checked against the `ALLOWED_USERS` frozenset. Comments from unauthorized users are silently ignored:

```python
if event.author_username not in config.allowed_users:
    logger.warning("Ignoring comment from unauthorized user: %s", event.author_username)
    return
```

`ALLOWED_USERS` must contain at least one username — the server refuses to start without it.

### Auto-trigger Events

PR lifecycle events (open, push, reopen, ready-for-review) configured in `REVIEWER_TRIGGERS` bypass the `ALLOWED_USERS` check. These events have no user-supplied prompt, and draft/WIP PRs are skipped.

## Tool Restrictions

| Capability | Reviewer Bot | Worker Bot |
|------------|-------------|------------|
| **Available tools** | `Read`, `Glob`, `Grep`, `Bash(git clone*)` | All tools |
| **Bash commands** | Only `git clone*` (fnmatch) | Unrestricted |
| **Permission mode** | `bypassPermissions` (with tool allowlist) | `bypassPermissions` (no allowlist) |
| **Can modify files** | No | Yes |
| **Can push code** | No | Yes |

## Secret Management

- **All secrets are passed via environment variables** — `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITLAB_TOKEN`, etc.

- **Token redaction in logs** — embedded tokens in clone URLs are redacted using `_redact_url()`, which replaces credentials with `***` before logging:

    ```python
    TOKEN_PATTERN = re.compile(r"(https?://[^:]+:)[^@]+(@)")
    ```

- **Private key options** — GitHub App private keys can be provided inline (`GITHUB_APP_PRIVATE_KEY`) or via file path (`GITHUB_APP_PRIVATE_KEY_PATH`). Inline takes precedence.

- **Never commit `.env` files** — secrets should be injected via your deployment platform's secret management (e.g. Docker secrets, Kubernetes secrets, CI variables).

## Resource Limits

| Resource | Limit | Purpose |
|----------|-------|---------|
| Bash command timeout | 120 seconds | Prevents long-running commands |
| Grep timeout | 30 seconds | Prevents expensive searches |
| HTTP client timeout | 30 seconds | Prevents hanging API calls |
| Max response tokens | 16,384 | Caps LLM output per response |
| Max agent turns | Configurable (`AGENT_MAX_TURNS`, default: unlimited) | Limits agent loop iterations |
| Max glob results | 200 | Prevents oversized file listings |
| Max grep output | 30,000 characters | Truncates large search results |
| Max read lines | 2,000 | Truncates large file reads |
| Max line length | 2,000 characters | Truncates long lines |
| Webhook body size | 5 MB | Rejects oversized payloads |
| Shallow clone depth | 1 commit | Minimizes cloned data |
| Tool log truncation | 500 characters | Limits tool output in logs |

## Network Exposure

- **Default bind address** — the server binds to `0.0.0.0:8080`. Restrict network access using firewall rules or deploy behind a reverse proxy.

- **TLS termination** — the server does not terminate TLS. Use a reverse proxy (e.g. nginx, Caddy, cloud load balancer) for HTTPS.

- **Health endpoint** — `GET /health` returns `{"status": "ok"}` with no sensitive data.

- **Webhook routes** — `POST /webhooks/github` and `POST /webhooks/gitlab` return `401` for requests with invalid signatures when a webhook secret is configured.

- **Per-PR serialization** — concurrent requests for the same PR are queued and processed one at a time, keyed by `(platform, repo, pr_number, bot_type)`. This prevents race conditions but does not limit concurrency across different PRs.
